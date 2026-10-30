"""The C release archives, for a consumer whose hyper.json sets `c_assets`.

One archive per platform, attached to the GitHub Release: the generated header, the shared and
static libraries CI built and attested, and the files CMake and pkg-config find them by.

    {crate}-{version}-{rid}/
      include/{crate}.h, include/{crate}.hpp (the C++ header, when the consumer has one)
      lib/lib{crate}.a | lib/lib{crate}.so | lib/lib{crate}.dylib
      bin/{crate}.dll, lib/{crate}.dll.lib, lib/{crate}.lib          (Windows)
      lib/pkgconfig/{crate}.pc
      lib/cmake/{crate}/{crate}Config.cmake, {crate}ConfigVersion.cmake
      LICENSE, README.md

The consumer's `c/` holds what is particular to it: `{crate}.pc.in`,
`cmake/{crate}Config.cmake`, `cmake/{crate}ConfigVersion.cmake.in` and `README.md`. Three
placeholders are filled here: @VERSION@ (rust/Cargo.toml's), @MAJOR@ and @POINTER_SIZE@.
Every path in the package files is relative to the file, so an archive works wherever it is
unpacked.

Windows takes one file no CI job builds: the DLL's import library, which a C program needs to
link against the DLL and which `native-win-*` does not carry. It holds only the DLL's name and
its exports, so it is derived here from the attested DLL's own export table (`llvm-readobj
--coff-exports`, then `llvm-dlltool`), and nothing in it can differ from what the DLL exports.

The WebAssembly archive (wasm32-wasip1, for wasi-sdk) and the four Apple mobile ones (iOS, the
iOS simulator on Apple silicon, Mac Catalyst on both architectures) have the static library
only: neither WASI nor an iOS app loads a shared library beside the program.

Archives are deterministic (sorted entries, fixed times, modes and owners), so a pack rebuilt
from the same inputs is byte-identical.

  inputs    --artifacts DIR             every library `pack` reads there, one per line, for the
                                        attestation check before it
  pack      --artifacts DIR --out DIR   every archive, from the CI run's downloaded artifacts
  assemble  --rid RID --static F [--shared F] --out DIR
                                        one unpacked archive, from local files (a smoke test)
"""
import argparse
import gzip
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hyper_config  # noqa: E402
import swift_release  # noqa: E402

APPLE_MOBILE_RIDS = ('ios-arm64', 'iossimulator-arm64', 'maccatalyst-arm64', 'maccatalyst-x64')
C_RIDS = hyper_config.RIDS + ('android-arm64', 'android-x64') + APPLE_MOBILE_RIDS + ('wasi-wasm32',)
STATIC_ONLY = APPLE_MOBILE_RIDS + ('wasi-wasm32',)
# Where the static-libs artifact keeps each platform's archive: C#'s Native AOT tree has one per
# RID (glibc and musl apart, unlike Go's; the Apple mobile ones with apple_mobile), and only
# Swift's bundle carries WebAssembly.
WASI_TRIPLE = 'wasm32-unknown-wasip1'
DLLTOOL_MACHINE = {'win-x64': 'i386:x86-64', 'win-arm64': 'arm64'}
EPOCH = 315532800  # 1980-01-01, as the Swift zips


def is_windows(rid):
    return rid.startswith('win-')


def archive_name(cfg, version, rid):
    return f'{cfg.crate}-{version}-{rid}' + ('.zip' if is_windows(rid) else '.tar.gz')


def asset_names(cfg, version):
    return tuple(archive_name(cfg, version, rid) for rid in C_RIDS)


def shared_name(cfg, rid):
    if rid in STATIC_ONLY:
        return None
    if rid.startswith('android-'):
        return f'lib{cfg.crate}.so'
    return cfg.lib_name(rid)


def static_name(cfg, rid):
    return f'{cfg.crate}.lib' if is_windows(rid) else f'lib{cfg.crate}.a'


def artifact_inputs(cfg, artifacts, rid):
    """The shared and static library for `rid` in the CI run's downloaded artifacts."""
    artifacts = Path(artifacts)
    shared = shared_name(cfg, rid)
    shared = artifacts / f'native-{rid}' / shared if shared else None
    if rid == 'wasi-wasm32':
        static = artifacts / 'static-libs' / cfg.bundle / WASI_TRIPLE / f'lib{cfg.crate}.a'
    else:
        static = artifacts / 'static-libs' / f'csharp/{cfg.project}/staticlibs/{rid}' / static_name(cfg, rid)
    return shared, static


def fill(text, values, source):
    for key, value in values.items():
        text = text.replace(f'@{key}@', value)
    left = sorted(set(re.findall(r'@[A-Z_]+@', text)))
    if left:
        raise ValueError(f'{source} has placeholders nothing fills: {left}')
    return text


def coff_exports(dll, readobj):
    out = subprocess.run([readobj, '--coff-exports', str(dll)], check=True, capture_output=True, text=True).stdout
    names = re.findall(r'^\s*Name: (\S+)$', out, re.M)
    if not names:
        raise ValueError(f'{dll} exports nothing')
    return names


def import_library(cfg, rid, dll, destination, readobj, dlltool):
    """The import library for `dll`, from its own export table."""
    exports = coff_exports(dll, readobj)
    definition = destination.with_suffix('.def')
    definition.write_text(f'LIBRARY {dll.name}\nEXPORTS\n' + ''.join(f'  {name}\n' for name in exports))
    try:
        subprocess.run([dlltool, '-m', DLLTOOL_MACHINE[rid], '-d', str(definition), '-D', dll.name,
                        '-l', str(destination)], check=True)
    finally:
        definition.unlink()
    return exports


def find_tool(name):
    """An LLVM tool from PATH, or from the newest /usr/lib/llvm-*/bin (Ubuntu's layout)."""
    found = os.environ.get(name.upper().replace('-', '_')) or shutil.which(name)
    if found:
        return found
    candidates = sorted(Path('/usr/lib').glob(f'llvm-*/bin/{name}'),
                        key=lambda p: int(re.search(r'llvm-(\d+)', str(p))[1]))
    if not candidates:
        raise ValueError(f'{name} not found: install LLVM or set {name.upper().replace("-", "_")}')
    return str(candidates[-1])


def assemble(cfg, root, rid, version, static, shared, out):
    """The unpacked archive for `rid` under `out`, from these library files."""
    if not cfg.c_assets:
        raise ValueError('hyper.json does not set c_assets')
    if rid not in C_RIDS:
        raise ValueError(f'unknown C platform {rid}; expected one of {C_RIDS}')
    root, c = Path(root), Path(root) / 'c'
    for path in (static, shared):
        if path is not None and (Path(path).is_symlink() or not Path(path).is_file()):
            raise ValueError(f'missing library: {path}')
    if (shared is None) != (shared_name(cfg, rid) is None):
        raise ValueError(f'{rid} {"needs" if shared is None else "has no"} shared library')
    name = archive_name(cfg, version, rid).removesuffix('.zip').removesuffix('.tar.gz')
    tree = Path(out) / name
    if tree.exists():
        shutil.rmtree(tree)
    (tree / 'include').mkdir(parents=True)
    (tree / 'lib/pkgconfig').mkdir(parents=True)
    (tree / f'lib/cmake/{cfg.crate}').mkdir(parents=True)
    shutil.copyfile(root / 'include' / cfg.header, tree / 'include' / cfg.header)
    cpp = root / 'include' / f'{cfg.crate}.hpp'
    if cpp.is_file():
        shutil.copyfile(cpp, tree / 'include' / cpp.name)
    shutil.copyfile(static, tree / 'lib' / static_name(cfg, rid))
    if shared is not None:
        if is_windows(rid):
            (tree / 'bin').mkdir()
            shutil.copyfile(shared, tree / 'bin' / shared_name(cfg, rid))
            import_library(cfg, rid, tree / 'bin' / shared_name(cfg, rid), tree / 'lib' / f'{cfg.crate}.dll.lib',
                           find_tool('llvm-readobj'), find_tool('llvm-dlltool'))
        else:
            shutil.copyfile(shared, tree / 'lib' / shared_name(cfg, rid))
    values = dict(VERSION=version, MAJOR=version.split('.')[0],
                  POINTER_SIZE='4' if rid == 'wasi-wasm32' else '8')
    templates = {f'{cfg.crate}.pc.in': f'lib/pkgconfig/{cfg.crate}.pc',
                 f'cmake/{cfg.crate}Config.cmake': f'lib/cmake/{cfg.crate}/{cfg.crate}Config.cmake',
                 f'cmake/{cfg.crate}ConfigVersion.cmake.in': f'lib/cmake/{cfg.crate}/{cfg.crate}ConfigVersion.cmake',
                 'README.md': 'README.md'}
    for source, destination in templates.items():
        (tree / destination).write_text(fill((c / source).read_text(), values, f'c/{source}'), newline='\n')
    shutil.copyfile(root / 'LICENSE', tree / 'LICENSE')
    return tree


def tar_tree(source, destination):
    """`source` as a .tar.gz under its own name, deterministically."""
    source = Path(source)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for path in [source, *sorted(source.rglob('*'))]:
            if path.is_symlink():
                raise ValueError(f'{path} is a symlink; the archive holds regular files only')
            info = tarfile.TarInfo(path.relative_to(source.parent).as_posix())
            info.mtime, info.uid, info.gid, info.uname, info.gname = EPOCH, 0, 0, '', ''
            if path.is_dir():
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            else:
                data = path.read_bytes()
                info.size = len(data)
                info.mode = 0o755 if path.suffix in ('.so', '.dylib', '.dll') else 0o644
                archive.addfile(info, io.BytesIO(data))
    with open(destination, 'wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, compresslevel=9,
                                                       filename='') as compressed:
        compressed.write(buffer.getvalue())


def pack_tree(tree, rid, out):
    destination = Path(out) / (tree.name + ('.zip' if is_windows(rid) else '.tar.gz'))
    if is_windows(rid):
        swift_release.zip_tree(tree, destination)
    else:
        tar_tree(tree, destination)
    return destination


def inputs(cfg, artifacts):
    return [path for rid in C_RIDS for path in artifact_inputs(cfg, artifacts, rid) if path is not None]


def pack(cfg, root, artifacts, out):
    version = swift_release.crate_version(root)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    staging = out / '.trees'
    built = []
    for rid in C_RIDS:
        shared, static = artifact_inputs(cfg, artifacts, rid)
        built.append(pack_tree(assemble(cfg, root, rid, version, static, shared, staging), rid, out))
    shutil.rmtree(staging)
    return built


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('inputs', 'pack', 'assemble'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--out', type=Path, help='pack, assemble: where the output is written')
    parser.add_argument('--artifacts', type=Path, help='pack: the downloaded CI artifacts')
    parser.add_argument('--rid', help='assemble: the platform')
    parser.add_argument('--static', type=Path, help='assemble: the static library')
    parser.add_argument('--shared', type=Path, help='assemble: the shared library (none for wasi-wasm32)')
    args = parser.parse_args()
    cfg = hyper_config.load(args.root)
    if args.mode == 'inputs':
        for path in inputs(cfg, args.artifacts):
            print(path)
    elif args.mode == 'pack':
        for path in pack(cfg, args.root, args.artifacts, args.out):
            print(f'{swift_release.sha256(path)}  {path.name}')
    else:
        print(assemble(cfg, args.root, args.rid, swift_release.crate_version(args.root), args.static, args.shared,
                       args.out))


if __name__ == '__main__':
    main()
