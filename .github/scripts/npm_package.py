"""Pack the TypeScript binding's npm tarballs: the one body the Forge's pack-npm action and a
consumer's own CI run (`ci.yml`'s npm consumer tests pack the same way the release does).

Two operations, both on a copy of the package directory, never on the checkout:

  pack       --package typescript --artifacts <dir> --out <dir> [--root .] [--allow-missing-natives]
             Every package the release publishes: one platform package per napi name in
             hyper.json's `node.platforms`, each carrying its one addon from
             `<artifacts>/node-<rid>/`; the WASI fallback package from
             `<artifacts>/node-<node.wasi>/`; then the root, whose optionalDependencies pin
             every platform package and whose dependencies pin the WASI package at this
             version, injected at pack time and never committed.
  pack-wasi  --package typescript --artifact <dir> --out <dir> [--root .]
             The WASI package alone, for the consumer tests that run before the natives exist.

The WASI package's manifest comes from `napi create-npm-dirs`, which still reaches the registry:
the CLI resolves `@napi-rs/wasm-runtime` to a `~` range on the newest release it finds there,
so a pack on a later day would name a runtime the suite never ran against. The three runtime
dependencies are therefore rewritten to the exact versions in the committed lock (`bun.lock`, or
`package-lock.json` for a package not yet on Bun, a pinned companion's commit among them), the ones
the forced-WASI suite was proven with, and the tarball is refused unless they match.

Beside a `bun.lock`, Bun does all of it: it installs, runs the napi CLI on its own runtime and packs,
so no Node is involved. Node and npm come in at `npm publish` alone, in the publish-npm action, since
Bun has no OIDC trusted publishing or provenance (oven-sh/bun#15601, oven-sh/bun#22423). A package
still on `package-lock.json` installs and packs with npm, as it did.

`--allow-missing-natives` is for a developer machine with one native built: absent natives are
skipped, the root still pins all eight, and the output is never for release (the action never
passes it) and cannot stand in for the published natives in the lockfile scenario.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import bun_lock
import hyper_config
from release_package_metadata import NPM_SCOPE, WASI_RUNTIME, npm_all_names, npm_filename, npm_tarball, npm_wasi_name

# The WASI package's own Node floor: Node-API 10, which the addons build against, ships in
# 22.14 and 23.6 (the runtime's own floor, `node:wasi`'s reactor `initialize` and the WASI
# preview1 surface the loader uses, is lower: 22.13 and 23.5). The root package carries the same
# range, and `create-npm-dirs` copies it; this check holds the WASI package to it even if the
# root moves.
WASI_NODE_ENGINES = '^22.14.0 || >=23.6.0'


def default_runner(cmd, cwd):
    """Run `cmd` in `cwd`, failing loudly. `bun`, `npm` and `napi` resolve through PATH and the
    staged copy's node_modules/.bin (so `.exe` and `.cmd` shims resolve on Windows too)."""
    search = str(Path(cwd) / 'node_modules' / '.bin') + os.pathsep + os.environ.get('PATH', '')
    executable = shutil.which(cmd[0], path=search) or cmd[0]
    subprocess.run([executable, *cmd[1:]], cwd=cwd, check=True)


def read_manifest(directory):
    return json.loads((Path(directory) / 'package.json').read_text(encoding='utf-8'))


def write_manifest(directory, manifest):
    (Path(directory) / 'package.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


def lockfile_versions(package_dir, names):
    """The exact version the package's lock resolved each of `names` to."""
    if bun_lock.present(package_dir):
        versions = {name: bun_lock.version(package_dir, name) for name in names}
        for name, version in versions.items():
            if not version:
                raise ValueError(f'{bun_lock.LOCK} does not resolve {name}: install it as a devDependency')
        return versions
    lock = json.loads((Path(package_dir) / 'package-lock.json').read_text(encoding='utf-8'))
    packages = lock.get('packages', {})
    versions = {}
    for name in names:
        entry = packages.get(f'node_modules/{name}')
        if not isinstance(entry, dict) or not isinstance(entry.get('version'), str):
            raise ValueError(f'package-lock.json does not resolve {name}: install it as a devDependency')
        versions[name] = entry['version']
    return versions


def stage(package_dir, work, runner=default_runner):
    """Copy the package directory to `work` (without node_modules and target, which the lock's
    install and nothing else restore) and install its dev dependencies. The committed manifest must
    carry no dependencies of either kind: they are injected here, into the copy."""
    package_dir, work = Path(package_dir), Path(work)
    manifest = read_manifest(package_dir)
    for key in ('dependencies', 'optionalDependencies'):
        if key in manifest:
            raise ValueError(f'{package_dir / "package.json"} carries {key}: they are injected at pack time, never committed')
    scope_name = manifest.get('napi', {}).get('packageName')
    if scope_name != manifest['name']:
        raise ValueError(f'napi.packageName is {scope_name!r}; it must equal the package name, '
                         f'{manifest["name"]}, so the platform packages are {manifest["name"]}-<platform>')
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(package_dir, work, symlinks=True,
                    ignore=shutil.ignore_patterns('node_modules', 'target', 'npm'))
    if bun_lock.present(work):
        runner(['bun', 'install', '--frozen-lockfile', '--ignore-scripts'], work)
    else:
        runner(['npm', 'ci', '--ignore-scripts'], work)


def artifact_files(artifact_dir):
    """The files a WASI build artifact contributes: everything but the debug module, which the
    loader would fall back to if it travelled and which no tarball may carry."""
    return sorted(p for p in Path(artifact_dir).iterdir() if p.is_file() and not p.name.endswith('.debug.wasm'))


def stage_wasi_sources(work, artifact_dir):
    """`napi create-npm-dirs` lists the WASI package's files from the loaders it finds beside
    package.json, so the artifact's files go into the copy's root first."""
    for path in artifact_files(artifact_dir):
        shutil.copy2(path, Path(work) / path.name)


def create_npm_dirs(work, runner=default_runner):
    """Beside a `bun.lock`, on Bun's runtime: `--bun` overrides the CLI's `node` shebang, which
    would otherwise take whatever Node the runner has."""
    command = ['bun', 'run', '--bun', 'napi'] if bun_lock.present(work) else ['napi']
    runner([*command, 'create-npm-dirs'], Path(work))


def carry_licenses(work, package):
    """Every package holds the compiled core, so each carries the license texts the root does. A
    generated manifest's `files` lists only the build's output, and npm's automatic LICENSE
    inclusion stops at that exact name, so each file is listed too."""
    names = [name for name in ('LICENSE', 'LICENSE-UNICODE') if (Path(work) / name).is_file()]
    for name in names:
        shutil.copy2(Path(work) / name, Path(package) / name)
    manifest = read_manifest(package)
    manifest['files'] = [*manifest.get('files', []), *names]
    write_manifest(package, manifest)


def fill_wasi_dir(work, artifact_dir, crate, wasi):
    """Copy the artifact into `npm/<wasi>/`; every file the generated manifest lists must then exist."""
    wasi_dir = Path(work) / 'npm' / wasi
    for path in artifact_files(artifact_dir):
        shutil.copy2(path, wasi_dir / path.name)
    missing = [name for name in read_manifest(wasi_dir).get('files', []) if not (wasi_dir / name).is_file()]
    if missing:
        raise ValueError(f'the WASI package manifest lists files the build did not produce: {missing}')
    wasm = f'{crate}.{wasi}.wasm'
    if not (wasi_dir / wasm).is_file():
        raise ValueError(f'the WASI artifact has no {wasm}')
    carry_licenses(work, wasi_dir)
    return wasi_dir


def pin_wasi_dependencies(wasi_dir, versions):
    """Rewrite the WASI package's dependencies to the exact versions given (the lockfile's)."""
    manifest = read_manifest(wasi_dir)
    dependencies = manifest.get('dependencies', {})
    strangers = sorted(set(dependencies) - set(WASI_RUNTIME))
    if strangers:
        raise ValueError(f'the WASI package depends on {strangers}; only {list(WASI_RUNTIME)} are expected')
    manifest['dependencies'] = {name: versions[name] for name in dependencies}
    write_manifest(wasi_dir, manifest)


def check_wasi_manifest(wasi_dir):
    """The WASI package installs everywhere (no os/cpu/libc) and states its Node floor."""
    manifest = read_manifest(wasi_dir)
    bound = [key for key in ('os', 'cpu', 'libc') if key in manifest]
    if bound:
        raise ValueError(f'the WASI package manifest declares {bound}; it must install on every platform')
    engines = manifest.get('engines', {}).get('node')
    if engines != WASI_NODE_ENGINES:
        raise ValueError(f'the WASI package engines.node is {engines!r}, expected {WASI_NODE_ENGINES!r}')


def fill_native_dir(work, artifact_dir, crate, suffix):
    addon = Path(artifact_dir) / f'{crate}.{suffix}.node'
    if not addon.is_file():
        raise ValueError(f'missing addon {addon}')
    native_dir = Path(work) / 'npm' / suffix
    shutil.copy2(addon, native_dir / addon.name)
    carry_licenses(work, native_dir)
    return native_dir


def pack_dir(directory, out, on_bun, runner=default_runner):
    """Pack one package directory into `out` with the lock's package manager; the tarball's
    path. `out` is resolved against the caller's directory first: the packer runs inside the
    staged package under the work tree, and would otherwise write a relative `--out` there."""
    directory, out = Path(directory), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    command = ['bun', 'pm', 'pack', '--ignore-scripts', '--destination'] if on_bun \
        else ['npm', 'pack', '--ignore-scripts', '--pack-destination']
    runner([*command, str(out)], directory)
    manifest = read_manifest(directory)
    path = out / npm_filename(manifest['name'], manifest['version'])
    if not path.is_file():
        raise ValueError(f'{command[0]} did not write {path}')
    return path


def set_root_dependencies(work, scope_name, version, natives, wasi):
    """Pin every platform package (optionalDependencies) and the WASI package (dependencies)
    at this version in the copy's root manifest."""
    manifest = read_manifest(work)
    manifest['optionalDependencies'] = {f'{scope_name}-{suffix}': version for suffix in sorted(natives)}
    if wasi:
        manifest['dependencies'] = {f'{scope_name}-{wasi}': version}
    write_manifest(work, manifest)


def check_tarballs(paths, crate, version, cfg, versions, allow_missing_natives=False, skip=()):
    """Every tarball is one of the release's packages and well formed (the release gate's
    validator, with the lockfile's runtime versions), and together they are exactly the set:
    the root, every platform package and the WASI package, once each. The platform packages of
    the rids in `skip` are expected to be absent; with `allow_missing_natives`, any platform
    package may be; the root and the WASI package may never be."""
    platforms = list(cfg.node_platforms.values())
    wasi = getattr(cfg, 'node_wasi', '')
    names = [npm_tarball(Path(path), crate, version, platforms, wasi, wasi_runtime=versions if wasi else None)
             for path in paths]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f'npm packages packed more than once: {duplicates}')
    expected = npm_all_names(crate, platforms, wasi)
    skipped = {f'{NPM_SCOPE}/{crate}-{cfg.node_platforms[rid]}' for rid in skip}
    missing = [name for name in expected if name not in names and name not in skipped]
    natives = set(expected) - {f'{NPM_SCOPE}/{crate}', npm_wasi_name(crate, wasi)}
    if missing and not (allow_missing_natives and set(missing) <= natives):
        raise ValueError(f'npm packages missing: {missing}')
    packed_anyway = sorted(skipped & set(names))
    if packed_anyway:
        raise ValueError(f'npm packages packed though skipped: {packed_anyway}')


def pack_wasi(package_dir, artifact_dir, out, cfg, work=None, runner=default_runner):
    """The WASI package alone."""
    package_dir, artifact_dir, out = Path(package_dir), Path(artifact_dir), Path(out)
    wasi = cfg.node_wasi
    if not wasi:
        raise ValueError('hyper.json says node.wasi is empty: this project ships no WASI package')
    versions = lockfile_versions(package_dir, WASI_RUNTIME)
    manifest = read_manifest(package_dir)
    if manifest['name'] != f'{NPM_SCOPE}/{cfg.crate}':
        raise ValueError(f'the package is {manifest["name"]}, hyper.json names {NPM_SCOPE}/{cfg.crate}')
    with tempfile.TemporaryDirectory(prefix='npm-package-') as scratch:
        work = Path(work) if work else Path(scratch) / 'work'
        stage(package_dir, work, runner)
        stage_wasi_sources(work, artifact_dir)
        create_npm_dirs(work, runner)
        wasi_dir = fill_wasi_dir(work, artifact_dir, cfg.crate, wasi)
        pin_wasi_dependencies(wasi_dir, versions)
        check_wasi_manifest(wasi_dir)
        path = pack_dir(wasi_dir, out, bun_lock.present(work), runner)
    npm_tarball(path, cfg.crate, manifest['version'], list(cfg.node_platforms.values()), wasi, wasi_runtime=versions)
    return path


def pack(package_dir, artifacts_dir, out, cfg, work=None, allow_missing_natives=False, runner=default_runner,
         skip=()):
    """Every package but the platform packages of the rids in `skip`: the platform packages,
    the WASI package, then the root. A release skips none; a pr-mode CI run skips osx-x64, the
    one native hyper-build-native does not build there (as c_package.py's pack does)."""
    package_dir, artifacts_dir, out = Path(package_dir), Path(artifacts_dir), Path(out)
    wasi = getattr(cfg, 'node_wasi', '')
    unknown = sorted(set(skip) - set(cfg.node_platforms))
    if unknown:
        raise ValueError(f'--skip names rids this project has no npm platform package for: {unknown}')
    manifest = read_manifest(package_dir)
    if manifest['name'] != f'{NPM_SCOPE}/{cfg.crate}':
        raise ValueError(f'the package is {manifest["name"]}, hyper.json names {NPM_SCOPE}/{cfg.crate}')
    version, scope_name = manifest['version'], f'{NPM_SCOPE}/{cfg.crate}'
    natives = {}
    for rid in sorted(cfg.node_platforms):
        suffix = cfg.node_platforms[rid]
        addon = artifacts_dir / f'node-{rid}' / f'{cfg.crate}.{suffix}.node'
        if rid in skip:
            continue
        if addon.is_file():
            natives[rid] = suffix
        elif not allow_missing_natives:
            raise ValueError(f'missing addon for {rid}: {addon} (a release never ships a root package naming a '
                             f'platform package that has no addon)')
    wasi_artifact = artifacts_dir / f'node-{wasi}' if wasi else None
    if wasi and not wasi_artifact.is_dir():
        raise ValueError(f'missing WASI build: {wasi_artifact}')
    versions = lockfile_versions(package_dir, WASI_RUNTIME) if wasi else {}
    with tempfile.TemporaryDirectory(prefix='npm-package-') as scratch:
        work = Path(work) if work else Path(scratch) / 'work'
        stage(package_dir, work, runner)
        on_bun = bun_lock.present(work)
        if wasi:
            stage_wasi_sources(work, wasi_artifact)
        create_npm_dirs(work, runner)
        paths = []
        for rid, suffix in natives.items():
            paths.append(pack_dir(fill_native_dir(work, artifacts_dir / f'node-{rid}', cfg.crate, suffix), out, on_bun, runner))
        if wasi:
            wasi_dir = fill_wasi_dir(work, wasi_artifact, cfg.crate, wasi)
            pin_wasi_dependencies(wasi_dir, versions)
            check_wasi_manifest(wasi_dir)
            paths.append(pack_dir(wasi_dir, out, on_bun, runner))
        # The root pins all eight platform packages whether or not each was packed here: a
        # developer's partial pack still describes the published shape.
        set_root_dependencies(work, scope_name, version, list(cfg.node_platforms.values()), wasi)
        paths.append(pack_dir(work, out, on_bun, runner))
    check_tarballs(paths, cfg.crate, version, cfg, versions, allow_missing_natives, skip)
    return paths


def main(argv=None, runner=default_runner, cfg=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('pack', 'pack-wasi'):
        sub = commands.add_parser(name)
        sub.add_argument('--package', type=Path, required=True, help='the TypeScript binding directory')
        sub.add_argument('--out', type=Path, required=True, help='where the tarballs go')
        sub.add_argument('--root', type=Path, default=Path('.'), help='the consumer checkout holding .github/hyper.json')
        sub.add_argument('--work', type=Path, help='the staging copy (a temporary directory by default)')
        if name == 'pack':
            sub.add_argument('--artifacts', type=Path, required=True, help='holds node-<rid>/ for every platform and node-<wasi>/')
            sub.add_argument('--allow-missing-natives', action='store_true',
                             help='developer machines only: skip platforms whose addon is absent')
            sub.add_argument('--skip', action='append', default=[], metavar='RID',
                             help='leave out this rid\'s platform package (pr-mode CI: osx-x64, which '
                                  'hyper-build-native does not build there); the root still pins it')
        else:
            sub.add_argument('--artifact', type=Path, required=True, help='the WASI build output directory')
    args = parser.parse_args(argv)
    try:
        cfg = cfg or hyper_config.load(args.root)
        if args.command == 'pack':
            paths = pack(args.package, args.artifacts, args.out, cfg, args.work, args.allow_missing_natives, runner,
                         skip=args.skip)
        else:
            paths = [pack_wasi(args.package, args.artifact, args.out, cfg, args.work, runner)]
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f'npm_package: {error}', file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
