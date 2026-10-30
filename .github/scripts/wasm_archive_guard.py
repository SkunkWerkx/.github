"""Refuse an emscripten static archive that a .NET browser-wasm app cannot load.

`python3 wasm_archive_guard.py ARCHIVE --llvm-bin DIR` exits 0 only when every member is a wasm
object with a target_features section, llvm-objdump decodes every function the member's code
section declares, no function holds an exception-handling instruction, and no member declares
a feature other than `sign-ext` and `mutable-globals`:

* any exception-handling instruction, legacy or new: .NET 11 links the new (exnref) encoding,
  and a browser refuses a module that mixes the two; Rust's prebuilt sysroot carries the
  legacy one, which a build-std archive leaves out.
* any other feature: a .NET 10 publish runs wasm-opt twice. Emscripten's run turns each
  declared feature into an `--enable-...` flag, and its Binaryen 117 does not know
  `bulk-memory-opt` or `call-indirect-overlong` (dotnet/runtime#132858); it then strips the
  target_features section, and the SDK's own post-link run enables only simd, bulk memory and
  exception handling beyond Binaryen's defaults (sign-ext, mutable-globals), so an instruction
  from any other feature, an `i64.trunc_sat_f64_s` say, fails a Release publish. The two
  allowed features are Binaryen's defaults, which both runs accept with no flag, and all the
  archive's rustflags declare; everything else is refused, simd included, which the archive
  does not need.

Members are read by occurrence with the ar reader below, never `llvm-ar x`, which writes
same-named members over each other and would let one escape. Anything the guard cannot
inspect fails it: an empty result, a byte llvm-objdump cannot decode, or a function it never
printed is never read as clean.
"""
import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys
import tempfile

EH_INSTRUCTIONS = frozenset({'try', 'catch', 'catch_all', 'rethrow', 'delegate', 'throw', 'try_table', 'throw_ref'})
ALLOWED_FEATURES = frozenset({'sign-ext', 'mutable-globals'})
WASM_MAGIC = b'\0asm'
# llvm-objdump -d: `<offset>: <encoded bytes>\t<mnemonic>\t<operands>`.
INSTRUCTION_LINE = re.compile(r'\s*[0-9a-f]+:(\s[0-9a-f]{2})+\s*$')


class GuardError(Exception):
    """The archive could not be inspected."""


def members(archive):
    """(`name#occurrence`, bytes) for every object member, in archive order (GNU and BSD ar)."""
    data = Path(archive).read_bytes()
    if not data.startswith(b'!<arch>\n'):
        raise GuardError(f'{archive}: not an ar archive')
    pos, long_names, seen, out = 8, b'', Counter(), []
    while pos < len(data):
        header = data[pos:pos + 60]
        if len(header) < 60 or header[58:60] != b'`\n':
            raise GuardError(f'{archive}: malformed member header at byte {pos}')
        raw = header[:16].decode(errors='replace').rstrip()
        try:
            size = int(header[48:58])
        except ValueError:
            raise GuardError(f'{archive}: member {raw!r} has no size') from None
        body = data[pos + 60:pos + 60 + size]
        if len(body) != size:
            raise GuardError(f'{archive}: member {raw!r} runs past the end of the archive')
        pos += 60 + size + (size & 1)
        if raw.startswith('#1/'):
            length = int(raw[3:])
            name, body = body[:length].rstrip(b'\0').decode(), body[length:]
        elif raw == '//':
            long_names = body
            continue
        elif raw in ('/', '/SYM64/') or raw.startswith('__.SYMDEF'):
            continue
        elif raw.startswith('/') and raw[1:].isdigit():
            start = int(raw[1:])
            name = long_names[start:long_names.index(b'/\n', start)].decode()
        else:
            name = raw.rstrip('/')
        if name.startswith('__.SYMDEF'):
            continue
        seen[name] += 1
        out.append((f'{name}#{seen[name]}', body))
    if not out:
        raise GuardError(f'{archive}: no members')
    return out


def _leb(data, pos):
    value = shift = 0
    while True:
        if pos >= len(data):
            raise GuardError('truncated wasm object')
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7f) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def sections(label, body):
    """(id, payload start) for each section of a wasm object."""
    if not body.startswith(WASM_MAGIC):
        raise GuardError(f'{label}: not a wasm object')
    pos, out = 8, []
    try:
        while pos < len(body):
            size, start = _leb(body, pos + 1)
            out.append((body[pos], start))
            pos = start + size
    except GuardError as error:
        raise GuardError(f'{label}: {error}') from None
    return out


def code_bodies(label, body):
    """How many function bodies the code section declares; 0 for a data-only object."""
    for kind, start in sections(label, body):
        if kind == 10:
            return _leb(body, start)[0]
    return 0


def target_features(label, body):
    """The names a wasm object's target_features section declares: every prefix but `-`."""
    for kind, start in sections(label, body):
        if kind != 0:
            continue
        try:
            name_len, at = _leb(body, start)
            if body[at:at + name_len] != b'target_features':
                continue
            count, at = _leb(body, at + name_len)
            declared = set()
            for _ in range(count):
                prefix = body[at:at + 1]
                length, at = _leb(body, at + 1)
                if prefix != b'-':
                    declared.add(body[at:at + length].decode(errors='replace'))
                at += length
        except GuardError as error:
            raise GuardError(f'{label}: {error}') from None
        return declared
    raise GuardError(f'{label}: no target_features section')


def rejected_features(archive):
    """{member: the features it declares beyond ALLOWED_FEATURES}, for members that declare any."""
    found = {}
    for label, body in members(archive):
        bad = target_features(label, body) - ALLOWED_FEATURES
        if bad:
            found[label] = bad
    return found


def member_instructions(label, body, llvm_bin):
    """(Counter of exception-handling mnemonics, function count) for one member."""
    declared = code_bodies(label, body)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'member.o'
        path.write_bytes(body)
        result = subprocess.run([str(Path(llvm_bin) / 'llvm-objdump'), '-d', str(path)],
                                capture_output=True, text=True)
    if result.returncode != 0 or result.stderr.strip():
        raise GuardError(f'{label}: llvm-objdump failed: {result.stderr.strip()}')
    counts, functions = Counter(), 0
    for line in result.stdout.splitlines():
        if line.rstrip().endswith('>:'):
            # The code section's own `<CODE>:` label is not a function.
            functions += not line.rstrip().endswith('<CODE>:')
            continue
        fields = line.split('\t')
        if len(fields) > 1 and INSTRUCTION_LINE.match(fields[0]) and fields[1].split():
            mnemonic = fields[1].split()[0]
            if mnemonic == '<unknown>':
                raise GuardError(f'{label}: llvm-objdump cannot decode {fields[0].strip()}')
            if mnemonic in EH_INSTRUCTIONS:
                counts[mnemonic] += 1
    if functions != declared:
        raise GuardError(f'{label}: llvm-objdump showed {functions} of {declared} functions')
    return counts, functions


def eh_instructions(archive, llvm_bin):
    """{member: Counter of exception-handling mnemonics}, for members that hold any."""
    found = {}
    for label, body in members(archive):
        counts, _ = member_instructions(label, body, llvm_bin)
        if counts:
            found[label] = counts
    return found


def problems(archive, llvm_bin):
    """(every reason the archive fails, functions inspected): one failure never hides another."""
    out, functions = [], 0
    for label, body in members(archive):
        try:
            counts, found = member_instructions(label, body, llvm_bin)
            functions += found
            out += [f'{label}: {name} x{n}' for name, n in sorted(counts.items())]
        except GuardError as error:
            out.append(str(error))
        try:
            out += [f'{label}: declares {name}' for name in sorted(target_features(label, body) - ALLOWED_FEATURES)]
        except GuardError as error:
            if str(error) not in out:
                out.append(str(error))
    return out, functions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--llvm-bin', type=Path, required=True, help='directory holding llvm-objdump')
    args = parser.parse_args(argv)
    try:
        count = len(members(args.archive))
        found, functions = problems(args.archive, args.llvm_bin)
    except GuardError as error:
        print(f'::error::{error}')
        return 1
    for problem in found:
        print(f'::error::{args.archive.name}: {problem}')
    if found:
        return 1
    print(f'clean: {count} members, {functions} functions, no exception handling, no rejected feature')
    return 0


if __name__ == '__main__':
    sys.exit(main())
