"""The emscripten archive guard: no exception handling, no feature beyond the two both .NET runtimes accept.

Every fixture is built here from a few lines of C or wasm assembly, so each check is shown to
fail on its own. These tests need clang, llvm-ar, llvm-objdump and llvm-objcopy with the
WebAssembly target: $LLVM_BIN, or the directory of llvm-objdump on PATH (validate.yml installs
them). Without them the tests error rather than skip: a guard nobody watched fail proves nothing.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wasm_archive_guard', SCRIPTS / 'wasm_archive_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def llvm_bin():
    if os.environ.get('LLVM_BIN'):
        return Path(os.environ['LLVM_BIN'])
    objdump = shutil.which('llvm-objdump')
    if not objdump:
        raise RuntimeError('set LLVM_BIN, or put llvm-objdump (with clang beside it) on PATH')
    return Path(objdump).parent


CLEAN_C = 'int f(int x) { return x + 1; }\n'
THROW_S = """.tagtype __cpp_exception i32
.globl f
f:
  .functype f () -> ()
  i32.const 0
  throw __cpp_exception
  end_function
"""
LEGACY_S = """.globl f
f:
  .functype f () -> ()
  try
  catch_all
  end_try
  end_function
"""
# The other legacy and new mnemonics, so a typo in EH_INSTRUCTIONS cannot pass unseen.
REST_S = """.tagtype __cpp_exception i32
.globl f
f:
  .functype f () -> ()
  try
  catch __cpp_exception
  drop
  rethrow 0
  end_try
  try
  delegate 0
  block exnref
  try_table (catch_all_ref 0)
  end_try_table
  unreachable
  end_block
  throw_ref
  end_function
"""
DATA_C = 'const int t[4] = {1, 2, 3, 4};\n'
TRY_TABLE_S = """.globl f
f:
  .functype f () -> ()
  block
  try_table (catch_all 0)
  end_try_table
  end_block
  end_function
"""


class WasmArchiveGuardTests(unittest.TestCase):
    def setUp(self):
        self.bin = llvm_bin()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def tool(self, name, *args):
        subprocess.run([str(self.bin / name), *map(str, args)], check=True, capture_output=True)

    def obj(self, name, source, suffix, *flags):
        """A wasm object from C or assembly; the clean flags give it a target_features section."""
        src = self.dir / f'{name}{suffix}'
        src.write_text(source)
        out = self.dir / name / 'same.o' if name.startswith('dup') else self.dir / f'{name}.o'
        out.parent.mkdir(parents=True, exist_ok=True)
        self.tool('clang', '--target=wasm32-unknown-unknown', '-mcpu=mvp', '-msign-ext', *flags, '-c', src, '-o', out)
        return out

    def archive(self, *members, fmt='gnu', ops='qc'):
        lib = self.dir / 'lib.a'
        lib.unlink(missing_ok=True)
        self.tool('llvm-ar', f'--format={fmt}', ops, lib, *members)
        return lib

    def run_guard(self, lib):
        return subprocess.run([sys.executable, str(SCRIPTS / 'wasm_archive_guard.py'), str(lib), '--llvm-bin', str(self.bin)],
                              capture_output=True, text=True)

    def assert_fails_naming(self, lib, *needles):
        result = self.run_guard(lib)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stdout + result.stderr)

    def patched(self, obj, old, new):
        data = obj.read_bytes()
        self.assertEqual(data.count(old), 1, f'{old!r} once in {obj.name}')
        obj.write_bytes(data.replace(old, new))
        return obj

    def test_clean_object_passes_in_both_archive_formats(self):
        for fmt in ('gnu', 'bsd'):
            with self.subTest(fmt=fmt):
                result = self.run_guard(self.archive(self.obj('clean', CLEAN_C, '.c'), fmt=fmt))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('clean: 1 members, 1 functions', result.stdout)

    def test_bsd_archive_findings_name_the_member(self):
        lib = self.archive(self.obj('a-long-member-name-past-sixteen', CLEAN_C, '.c', '-mbulk-memory'), fmt='bsd')
        self.assert_fails_naming(lib, 'a-long-member-name-past-sixteen.o#1', 'bulk-memory-opt')

    def test_every_other_eh_mnemonic_fails(self):
        lib = self.archive(self.obj('rest', REST_S, '.s', '-mexception-handling'))
        self.assertEqual(dict(guard.eh_instructions(lib, self.bin)['rest.o#1']),
                         {'try': 2, 'catch': 1, 'rethrow': 1, 'delegate': 1, 'try_table': 1, 'throw_ref': 1})
        self.assert_fails_naming(lib, 'catch x1', 'rethrow', 'delegate', 'throw_ref')

    def test_undecodable_instruction_fails(self):
        # local.get 0; i32.const 1; i32.add; end, with i32.add made an opcode nothing defines.
        obj = self.patched(self.obj('unknown', CLEAN_C, '.c', '-O2'), b'\x20\x00\x41\x01\x6a\x0b', b'\x20\x00\x41\x01\x27\x0b')
        self.assert_fails_naming(self.archive(obj), 'unknown.o#1', 'cannot decode')

    def test_data_only_member_passes(self):
        result = self.run_guard(self.archive(self.obj('data', DATA_C, '.c'), self.obj('clean', CLEAN_C, '.c')))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('clean: 2 members, 1 functions', result.stdout)

    def test_disassembly_short_of_the_code_section_fails(self):
        # An llvm-objdump that prints nothing: the code section declares a body it never showed.
        fake = self.dir / 'fakebin'
        fake.mkdir()
        (fake / 'llvm-objdump').write_text('#!/bin/sh\nexit 0\n')
        (fake / 'llvm-objdump').chmod(0o755)
        lib = self.archive(self.obj('clean', CLEAN_C, '.c'))
        result = subprocess.run([sys.executable, str(SCRIPTS / 'wasm_archive_guard.py'), str(lib), '--llvm-bin', str(fake)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('clean.o#1: llvm-objdump showed 0 of 1 functions', result.stdout)

    def test_a_feature_not_declared_minus_counts_as_declared(self):
        obj = self.patched(self.obj('required', CLEAN_C, '.c', '-mbulk-memory'), b'+\x0fbulk-memory-opt', b'=\x0fbulk-memory-opt')
        # S: llvm-ar's symbol table reader refuses the prefix the guard must still read.
        self.assert_fails_naming(self.archive(obj, ops='qcS'), 'required.o#1', 'bulk-memory-opt')

    def test_truncated_archive_fails_with_an_annotation(self):
        lib = self.archive(self.obj('clean', CLEAN_C, '.c'))
        lib.write_bytes(lib.read_bytes()[:-40])
        result = self.run_guard(lib)
        self.assertEqual(result.returncode, 1)
        self.assertIn('runs past the end of the archive', result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_garbage_member_header_fails_with_an_annotation(self):
        lib = self.dir / 'garbage.a'
        lib.write_bytes(b'!<arch>\n' + b'x.o/'.ljust(48) + b'notanumber`\n')
        result = self.run_guard(lib)
        self.assertEqual(result.returncode, 1)
        self.assertIn('::error::', result.stdout)
        self.assertNotIn('malformed member header', result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_throw_fails(self):
        lib = self.archive(self.obj('throw', THROW_S, '.s', '-mexception-handling'))
        self.assertEqual(dict(guard.eh_instructions(lib, self.bin)['throw.o#1']), {'throw': 1})
        self.assert_fails_naming(lib, 'throw')

    def test_legacy_try_catch_all_fails(self):
        lib = self.archive(self.obj('legacy', LEGACY_S, '.s', '-mexception-handling'))
        self.assertEqual(dict(guard.eh_instructions(lib, self.bin)['legacy.o#1']), {'try': 1, 'catch_all': 1})
        self.assert_fails_naming(lib, 'try', 'catch_all')

    def test_try_table_fails(self):
        lib = self.archive(self.obj('table', TRY_TABLE_S, '.s', '-mexception-handling'))
        self.assertEqual(dict(guard.eh_instructions(lib, self.bin)['table.o#1']), {'try_table': 1})
        self.assert_fails_naming(lib, 'try_table')

    def test_the_two_allowed_features_pass(self):
        lib = self.archive(self.obj('allowed', CLEAN_C, '.c', '-mmutable-globals'))
        self.assertEqual(guard.rejected_features(lib), {})
        result = self.run_guard(lib)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('clean: 1 members, 1 functions, no exception handling, no rejected feature', result.stdout)

    def test_a_feature_marked_minus_is_not_declared(self):
        # `-` says the object was built without the feature, so it can hold none of its
        # instructions; the same object with `+` fails (test_each_other_feature_fails).
        obj = self.patched(self.obj('disallowed', CLEAN_C, '.c', '-mbulk-memory'), b'+\x0fbulk-memory-opt', b'-\x0fbulk-memory-opt')
        obj = self.patched(obj, b'+\x0bbulk-memory', b'-\x0bbulk-memory')
        lib = self.archive(obj, ops='qcS')
        self.assertEqual(guard.rejected_features(lib), {})
        self.assertEqual(self.run_guard(lib).returncode, 0)

    def test_each_other_feature_fails(self):
        # The first three are the ones .NET 10's Binaryen does not know; the last three it knows,
        # but its post-link pass (the SDK's own wasm-opt run, after Emscripten's has stripped the
        # target_features section) does not enable them: an i64.trunc_sat_f64_s in a linked
        # module fails a .NET 10 Release publish.
        for flag, feature in (('-mbulk-memory', 'bulk-memory-opt'), ('-mreference-types', 'call-indirect-overlong'),
                              ('-mexception-handling', 'exception-handling'),
                              ('-mnontrapping-fptoint', 'nontrapping-fptoint'), ('-mmultivalue', 'multivalue'),
                              ('-msimd128', 'simd128')):
            with self.subTest(flag=flag):
                lib = self.archive(self.obj(f'feature{flag}', CLEAN_C, '.c', flag))
                self.assertEqual(guard.eh_instructions(lib, self.bin), {}, 'a feature fixture holds no EH instruction')
                self.assertIn(feature, guard.rejected_features(lib)[f'feature{flag}.o#1'])
                self.assert_fails_naming(lib, feature)

    def test_duplicate_member_names_are_each_inspected(self):
        # The bad member goes first: `llvm-ar x` keeps the last same-named member, so this is
        # the one an extracting guard would lose.
        first = self.obj('dup1', CLEAN_C, '.c', '-mbulk-memory')
        second = self.obj('dup2', CLEAN_C, '.c')
        lib = self.archive(first, second)
        self.assertEqual(list(guard.rejected_features(lib)), ['same.o#1'])
        self.assertIn('bulk-memory-opt', guard.rejected_features(lib)['same.o#1'])
        self.assert_fails_naming(lib, 'same.o#1', 'bulk-memory-opt')

    def test_empty_archive_fails(self):
        lib = self.dir / 'empty.a'
        lib.write_bytes(b'!<arch>\n')
        self.assert_fails_naming(lib, 'no members')

    def test_non_wasm_member_fails(self):
        note = self.dir / 'note.txt'
        note.write_text('not a wasm object\n')
        self.assert_fails_naming(self.archive(note), 'note.txt#1', 'not a wasm object')

    def test_member_without_target_features_fails(self):
        stripped = self.obj('stripped', CLEAN_C, '.c')
        self.tool('llvm-objcopy', '--remove-section=target_features', stripped)
        self.assert_fails_naming(self.archive(stripped), 'stripped.o#1', 'no target_features')


if __name__ == '__main__':
    unittest.main()
