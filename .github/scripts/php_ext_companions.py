#!/usr/bin/env python3
"""Build the PHP extensions of a crate's companions, for its suite to load beside its own.

A companion is a dependency whose own PHP extension this crate's extension needs at run time
(HyperTabular judges every cell with ext-hypercast's classes). hyper-build-php-extension.yml's
`php_companions` input names them; every leg runs this from the consumer's `rust/`:

    php_ext_companions.py --companions "<crate> ..." --out <dir> --target-root <dir> [--env-file <file>]

Each companion is built from the registry source of the one version the consumer's Cargo.lock
pins (`cargo metadata --locked`), never from a path or git source, so the suite runs against the
release a consumer's PIE installs beside this one. The build is the companion's own `cargo
php-ext` alias spelled out (the alias lives in its repository's .cargo/config.toml, which the
published crate does not carry), under the companion's published Cargo.lock, into a target dir
of its own. The library is staged in <dir> as <companion>.so (<companion>.dll on Windows), and
`<COMPANION>_PHP_EXTENSION=<path>` is appended to <file> (the job's GITHUB_ENV) for the suite.

Companions are only ever loaded by the suite: they are not load-checked as this crate, not
packed and not attested. On Windows this runs inside the crate's own build step, which holds the
MSVC toolset environment and the bootstrap variable the extension build needs (abi_vectorcall),
so the workflow sets that variable on one step only.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# crates.io and any other Cargo registry, git-index or sparse; never `path` or `git+`.
REGISTRY_SOURCES = ('registry+', 'sparse+')
# A lib target's kinds: what its manifest declares (`rlib` for crates.io's hypercast), or `lib`.
LIB_KINDS = {'lib', 'rlib', 'dylib', 'cdylib', 'staticlib'}


def env_name(companion):
    """The suite's variable for a companion: the name upper-cased, `-` as `_`, as the workflow
    and php_ext_musl.sh derive <CRATE>_PHP_EXTENSION (`tr '[:lower:]-' '[:upper:]_'`)."""
    return companion.upper().replace('-', '_') + '_PHP_EXTENSION'


def locked_package(metadata, companion):
    """The one registry package named `companion` in the lock's resolve."""
    named = [p for p in metadata['packages'] if p['name'] == companion]
    if not named:
        raise SystemExit(f'::error::Cargo.lock pins no package {companion}')
    registry = [p for p in named if (p.get('source') or '').startswith(REGISTRY_SOURCES)]
    if len(registry) != len(named):
        others = ', '.join(f"{p['version']} from {p.get('source') or 'a path'}" for p in named if p not in registry)
        raise SystemExit(f'::error::companion {companion} must come from a registry, not {others}')
    if len(registry) != 1:
        versions = ', '.join(p['version'] for p in registry)
        raise SystemExit(f'::error::Cargo.lock pins {companion} at more than one version ({versions})')
    return registry[0]


def library_name(package, platform=sys.platform):
    """The cdylib file cargo writes for the package's lib target on this platform."""
    lib = next(t['name'] for t in package['targets'] if LIB_KINDS & set(t['kind']))
    if platform == 'win32':
        return f'{lib}.dll'
    if platform == 'darwin':
        return f'lib{lib}.dylib'
    return f'lib{lib}.so'


def build_command(package, target_dir):
    """The companion's `cargo php-ext` (rustc --release --crate-type cdylib --features php),
    against its published manifest and lock."""
    return ['cargo', 'rustc', '--locked', '--manifest-path', package['manifest_path'], '--release',
            '--lib', '--crate-type', 'cdylib', '--features', 'php', '--target-dir', str(target_dir)]


def staged_name(companion, platform=sys.platform):
    return f'{companion}.dll' if platform == 'win32' else f'{companion}.so'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--companions', required=True, help='space-separated crate names')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--target-root', required=True, type=Path,
                        help='each companion builds in <target-root>/companion-<name>')
    args = parser.parse_args(argv)
    companions = args.companions.split()
    if not companions:
        return 0
    metadata = json.loads(subprocess.run(['cargo', 'metadata', '--locked', '--format-version', '1'],
                                         check=True, stdout=subprocess.PIPE, text=True).stdout)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = args.target_root.resolve()
    lines = []
    for companion in companions:
        package = locked_package(metadata, companion)
        print(f"companion {companion} {package['version']} from {package['source']}", flush=True)
        target = root / f'companion-{companion}'
        subprocess.run(build_command(package, target), check=True)
        staged = out / staged_name(companion)
        shutil.copyfile(target / 'release' / library_name(package), staged)
        lines.append(f'{env_name(companion)}={staged.as_posix()}')
        print(lines[-1], flush=True)
    if args.env_file:
        with args.env_file.open('a', encoding='utf-8', newline='\n') as env:
            env.writelines(line + '\n' for line in lines)
    return 0


if __name__ == '__main__':
    sys.exit(main())
