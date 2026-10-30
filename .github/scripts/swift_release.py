"""The Swift core as release assets, for a consumer whose hyper.json sets `swift_remote`.

SwiftPM takes a binary target from a URL when the manifest gives the zip's SHA-256 beside it,
so the core need not live in git: the static-libs job zips the artifact bundle and the
XCFramework it built and attests the zips (`zip`), staging writes the release's version and
their checksums into the root Package.swift (`stage`), the release guard compares the zips
with what that commit names (`check`), and the release attaches the same bytes to the GitHub
Release. Package.swift holds them on three lines, each written only here:

    let coreRelease = "X.Y.Z"
    let coreChecksum = "<sha256 of {Project}Core.artifactbundle.zip>"
    let appleCoreChecksum = "<sha256 of {Project}CoreApple.xcframework.zip>"

The zips are deterministic (sorted entries, a fixed timestamp and mode), so a zip rebuilt from
the same tree is byte-identical; nothing depends on that, but it makes a local check exact.

  zip    --out DIR      zip the bundle and XCFramework in the checkout (every target present)
  stage  --assets DIR   write the version from rust/Cargo.toml and the zips' checksums
  check  --assets DIR   the zips' checksums and the version are what Package.swift names
"""
import argparse
import hashlib
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hyper_config  # noqa: E402

MANIFEST = 'Package.swift'
LINES = (('version', 'coreRelease'), ('core', 'coreChecksum'), ('apple', 'appleCoreChecksum'))
# The earliest timestamp a zip can hold; every entry gets it, so the bytes depend on content only.
EPOCH = (1980, 1, 1, 0, 0, 0)


def line_pattern(name):
    return re.compile(rf'^let {name} = "([^"]*)"$', re.M)


def read(text):
    """The release version and the two checksums Package.swift names."""
    values = {}
    for key, name in LINES:
        matches = line_pattern(name).findall(text)
        if len(matches) != 1:
            raise ValueError(f'{MANIFEST} needs exactly one `let {name} = "..."` line, found {len(matches)}')
        values[key] = matches[0]
    return values


def write(text, version, core, apple):
    read(text)
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError(f'release version must be X.Y.Z, got {version!r}')
    for checksum in (core, apple):
        if not re.fullmatch('[0-9a-f]{64}', checksum):
            raise ValueError(f'checksum must be 64 lowercase hex digits, got {checksum!r}')
    for (_, name), value in zip(LINES, (version, core, apple)):
        text = line_pattern(name).sub(f'let {name} = "{value}"', text)
    return text


def only_release_lines_differ(before, after):
    """True when `after` is `before` with nothing but the three release lines rewritten."""
    values = read(after)
    return write(before, values['version'], values['core'], values['apple']) == after


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def zip_tree(source, destination):
    """Zip `source` (a directory) under its own name, deterministically."""
    source = Path(source)
    entries = sorted(path for path in source.rglob('*'))
    with zipfile.ZipFile(destination, 'w') as archive:
        for path in [source, *entries]:
            if path.is_symlink():
                raise ValueError(f'{path} is a symlink; the zip holds regular files only')
            name = path.relative_to(source.parent).as_posix()
            info = zipfile.ZipInfo(name + '/' if path.is_dir() else name, EPOCH)
            info.create_system = 3  # Unix, so the mode below is read
            if path.is_dir():
                info.external_attr = (0o40755 << 16) | 0x10
                archive.writestr(info, b'')
            else:
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes(), compresslevel=9)


def expected_files(cfg):
    """Every file each zip must hold, relative to the checkout."""
    bundle = [f'{cfg.bundle}/{target}/{cfg.archive_name(target)}' for target in cfg.swift_targets]
    bundle += [f'{cfg.bundle}/info.json', f'{cfg.bundle}/include/{cfg.header}',
               f'{cfg.bundle}/include/module.modulemap']
    xcframework = [f'{cfg.xcframework}/Info.plist']
    for slice_ in cfg.xcframework_slices:
        xcframework.append(f'{cfg.xcframework}/{slice_}/lib{cfg.crate}.a')
        xcframework += [f'{cfg.xcframework}/{slice_}/Headers/{cfg.project}Core/{name}'
                        for name in (cfg.header, 'module.modulemap')]
    return {cfg.swift_assets[0]: (cfg.bundle, bundle), cfg.swift_assets[1]: (cfg.xcframework, xcframework)}


def build_zips(cfg, root, out):
    if not cfg.swift_remote:
        raise ValueError('hyper.json does not set swift_remote')
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    built = []
    for asset, (tree, files) in expected_files(cfg).items():
        present = {path.relative_to(root).as_posix() for path in (root / tree).rglob('*') if path.is_file()}
        missing, extra = sorted(set(files) - present), sorted(present - set(files))
        if missing or extra:
            raise ValueError(f'{tree} is not the complete release tree: missing {missing}, unexpected {extra}')
        zip_tree(root / tree, out / asset)
        built.append(out / asset)
    return built


def crate_version(root):
    return re.search(r'^version = "([^"]+)"', (Path(root) / 'rust/Cargo.toml').read_text(), re.M)[1]


def checksums(cfg, assets):
    assets = Path(assets)
    for name in cfg.swift_assets:
        path = assets / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'missing Swift release asset: {path}')
    core, apple = (sha256(assets / name) for name in cfg.swift_assets)
    return core, apple


def stage(cfg, root, assets):
    manifest = Path(root) / MANIFEST
    core, apple = checksums(cfg, assets)
    manifest.write_text(write(manifest.read_text(), crate_version(root), core, apple))


def check(cfg, text, version, assets):
    """The zips are the ones `text` (Package.swift) names, for `version`."""
    named = read(text)
    if named['version'] != version:
        raise ValueError(f'{MANIFEST} names release {named["version"]!r}, not {version}')
    core, apple = checksums(cfg, assets)
    for asset, (key, actual) in zip(cfg.swift_assets, (('core', core), ('apple', apple))):
        if named[key] != actual:
            raise ValueError(f'{asset}: {MANIFEST} names {named[key] or "no checksum"}, the zip is {actual}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('zip', 'stage', 'check'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--out', type=Path, help='zip: where the two zips are written')
    parser.add_argument('--assets', type=Path, help='stage, check: the directory holding the two zips')
    args = parser.parse_args()
    cfg = hyper_config.load(args.root)
    if args.mode == 'zip':
        for path in build_zips(cfg, args.root, args.out):
            print(f'{sha256(path)}  {path.name}')
    elif args.mode == 'stage':
        stage(cfg, args.root, args.assets)
    else:
        check(cfg, (args.root / MANIFEST).read_text(), crate_version(args.root), args.assets)


if __name__ == '__main__':
    main()
