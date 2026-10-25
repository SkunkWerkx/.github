"""One description of a Hyper* repository, read from its `.github/hyper.json`.

Every Forge action, reusable workflow and script that needs to know a consumer's names
reads them here, so the three repositories cannot drift apart one hard-coded table at a
time. The file holds only what differs between repositories; everything the layout
conventions fix (RIDs, library names, the Swift bundle, the Go archive set, the Ruby
platform gems) is derived from `crate` and `project` and can be overridden by the same key.

Required keys: `crate` (lowercase core name), `project` (PascalCase binding name) and
`exports` (how to read the C export list from the Rust source: `sources`, `patterns`,
optional `exclude`, and `prefixes`, the symbol prefixes a shipped library may define).
A pattern is a regex whose first group is an export name, or an object `{"block": ...,
"name": ...}` that first finds blocks with one regex and then names inside each block
with another. Optional keys: `abi_probe` (a C program compiled against the bundle's header
and linked with each payload; default `.github/scripts/abi_probe.c`), `cdylib_smoke` (a
Python script given a shared library and `rust/Cargo.toml`: each RID's in hyper-check-core.yml's
check-cdylib, the committed linux-x64 one in release_guard.py), `wheels` (count,
default 25), `ruby.abis` and `ruby.platforms`, `go_targets`, `swift_targets`,
`xcframework_slices`, `version_files` (path -> regex with one group around the version).
"""
import json
from pathlib import Path
import re

RIDS = ('linux-x64', 'linux-arm64', 'linux-musl-x64', 'linux-musl-arm64',
        'osx-x64', 'osx-arm64', 'win-x64', 'win-arm64')
# PHP on Windows is an x64 process even on ARM hardware, so no win-arm64 library ships there.
PHP_RIDS = tuple(rid for rid in RIDS if rid != 'win-arm64')
GO_TARGETS = ('android_amd64', 'android_arm64', 'darwin_amd64', 'darwin_arm64', 'ios_arm64',
              'iossimulator_arm64', 'linux_amd64', 'linux_arm64', 'maccatalyst_amd64',
              'maccatalyst_arm64', 'wasm', 'windows_amd64', 'windows_arm64')
SWIFT_TARGETS = ('aarch64-swift-linux-musl', 'aarch64-unknown-linux-android',
                 'aarch64-unknown-linux-gnu', 'aarch64-unknown-windows-msvc', 'arm64-apple-macosx',
                 'wasm32-unknown-wasip1', 'x86_64-apple-macosx', 'x86_64-swift-linux-musl',
                 'x86_64-unknown-linux-android', 'x86_64-unknown-linux-gnu',
                 'x86_64-unknown-windows-msvc')
XCFRAMEWORK_SLICES = ('ios-arm64', 'ios-arm64-simulator', 'ios-arm64-maccatalyst')
RUBY_ABIS = ('3.4', '4.0')
# Seven platform gems: every RID but osx-x64, which installs the universal gem and runs on
# Fiddle. The glibc gems name their libc (`-linux-gnu`) so RubyGems before 4.0 never picks
# one on Alpine; the Forge README's ledger has the measurement.
RUBY_PLATFORMS = {'linux-x64': 'x86_64-linux-gnu', 'linux-arm64': 'aarch64-linux-gnu',
                  'linux-musl-x64': 'x86_64-linux-musl', 'linux-musl-arm64': 'aarch64-linux-musl',
                  'osx-arm64': 'arm64-darwin', 'win-x64': 'x64-mingw-ucrt',
                  'win-arm64': 'aarch64-mingw-ucrt'}
WHEELS = 25
CONFIG_PATH = '.github/hyper.json'


class HyperConfig:
    def __init__(self, data, root=None):
        for key in ('crate', 'project', 'exports'):
            if key not in data:
                raise ValueError(f'{CONFIG_PATH} needs "{key}"')
        if not re.fullmatch('[a-z][a-z0-9_]*', data['crate']):
            raise ValueError('crate must be a lowercase Rust crate name')
        if not re.fullmatch('[A-Z][A-Za-z0-9]*', data['project']):
            raise ValueError('project must be a PascalCase name')
        exports = data['exports']
        for key in ('sources', 'patterns', 'prefixes'):
            if not isinstance(exports.get(key), list) or not exports[key]:
                raise ValueError(f'exports.{key} must be a non-empty list')
        self.data = data
        self.root = Path(root) if root else None
        self.crate = data['crate']
        self.project = data['project']
        self.exports = exports
        self.abi_probe = data.get('abi_probe', '.github/scripts/abi_probe.c')
        self.cdylib_smoke = data.get('cdylib_smoke')
        self.wheels = int(data.get('wheels', WHEELS))
        ruby = data.get('ruby', {})
        self.ruby_abis = tuple(ruby.get('abis', RUBY_ABIS))
        self.ruby_platforms = dict(ruby.get('platforms', RUBY_PLATFORMS))
        self.go_targets = tuple(data.get('go_targets', GO_TARGETS))
        self.swift_targets = tuple(data.get('swift_targets', SWIFT_TARGETS))
        self.xcframework_slices = tuple(data.get('xcframework_slices', XCFRAMEWORK_SLICES))
        self.version_files = data.get('version_files') or self.default_version_files()

    @classmethod
    def load(cls, root):
        root = Path(root)
        path = root / CONFIG_PATH
        if not path.is_file():
            raise ValueError(f'{path} is missing: every Hyper* repository describes itself there')
        return cls(json.loads(path.read_text(encoding='utf-8')), root)

    # Names the layout conventions fix.
    def lib_name(self, rid):
        if rid.startswith('win'):
            return f'{self.crate}.dll'
        return f'lib{self.crate}.dylib' if rid.startswith('osx') else f'lib{self.crate}.so'

    @property
    def lib_names(self):
        return {rid: self.lib_name(rid) for rid in RIDS}

    def archive_name(self, target):
        return f'{self.crate}.lib' if 'windows' in target else f'lib{self.crate}.a'

    @property
    def php_libs(self):
        return {rid: self.lib_name(rid) for rid in PHP_RIDS}

    @property
    def bundle(self):
        return f'swift/{self.project}Core.artifactbundle'

    @property
    def xcframework(self):
        return f'swift/{self.project}CoreApple.xcframework'

    @property
    def header(self):
        return f'{self.crate}.h'

    @property
    def version_env(self):
        return f'{self.crate.upper()}_VERSION'

    @property
    def native_extension(self):
        return f'{self.crate}_native'

    def default_version_files(self):
        """Path -> regex with one group around the version, for prepare-release."""
        return {
            'rust/Cargo.toml': r'^version = "([^"]+)"',
            f'csharp/{self.project}/{self.project}.csproj': r'<Version>([^<]+)</Version>',
            'java/build.gradle.kts': rf'^version = System\.getenv\("{self.version_env}"\) \?: "([^"]+)"',
            f'ruby/{self.crate}.gemspec': r'spec\.version = "([^"]+)"',
            f'ruby/lib/{self.crate}.rb': r'VERSION = "([^"]+)"',
        }

    def payload_paths(self, artifacts):
        """Every committed native payload -> where the CI artifacts hold it."""
        artifacts = Path(artifacts)
        paths = {f'php/src/native/{rid}/{name}': artifacts / 'native' / f'native-{rid}' / name
                 for rid, name in self.php_libs.items()}
        static = [f'go/staticlib/{target}/lib{self.crate}.a' for target in self.go_targets]
        static += [f'{self.bundle}/{target}/{self.archive_name(target)}' for target in self.swift_targets]
        static += [f'{self.bundle}/info.json', f'{self.xcframework}/Info.plist']
        for slice_ in self.xcframework_slices:
            static.append(f'{self.xcframework}/{slice_}/lib{self.crate}.a')
            static += [f'{self.xcframework}/{slice_}/Headers/{self.project}Core/{name}'
                       for name in (self.header, 'module.modulemap')]
        paths.update({path: artifacts / 'static-libs' / path for path in static})
        return paths

    @property
    def source_paths(self):
        """Files under the payload trees that are source, not staged payload."""
        return {'php/src/native/README.md', 'go/staticlib/README.md',
                f'{self.bundle}/include/{self.header}', f'{self.bundle}/include/module.modulemap'}

    @property
    def payload_trees(self):
        return ('php/src/native', self.bundle, self.xcframework, 'go/staticlib')

    @property
    def probe_payloads(self):
        """The three payload kinds the ABI probe links against: a PHP shared library, a
        Swift archive and a Go archive, each the linux-x64 one."""
        return (f'php/src/native/linux-x64/{self.lib_name("linux-x64")}',
                f'{self.bundle}/x86_64-unknown-linux-gnu/lib{self.crate}.a',
                f'go/staticlib/linux_amd64/lib{self.crate}.a')

    def core_exports(self, root=None):
        """The C export names the Rust source declares, by the repository's own rule."""
        root = Path(root) if root else self.root
        names = set()
        for relative in self.exports['sources']:
            text = (root / relative).read_text(encoding='utf-8')
            for pattern in self.exports['patterns']:
                if isinstance(pattern, str):
                    names.update(re.findall(pattern, text))
                else:
                    for block in re.findall(pattern['block'], text, re.S):
                        names.update(re.findall(pattern['name'], block))
        names.difference_update(self.exports.get('exclude', []))
        if f'{self.crate}_version' not in names or len(names) < 2:
            raise ValueError('could not read the current C export set')
        return names

    def is_export_symbol(self, name):
        return name.startswith(tuple(self.exports['prefixes']))


def load(root='.'):
    return HyperConfig.load(root)


def shell(cfg):
    """Bash assignments for the actions' scripts: `eval "$(python3 hyper_config.py shell)"`."""
    def assoc(name, mapping):
        pairs = ' '.join(f'[{key}]={value}' for key, value in sorted(mapping.items()))
        return f'declare -A {name}=({pairs})'
    lines = [f'CRATE={cfg.crate}', f'PROJECT={cfg.project}', f'WHEELS={cfg.wheels}',
             f'BUNDLE={cfg.bundle}', f'XCFRAMEWORK={cfg.xcframework}', f'HEADER={cfg.header}',
             f'NATIVE_EXTENSION={cfg.native_extension}',
             'RUBY_ABIS=(' + ' '.join(cfg.ruby_abis) + ')',
             assoc('LIB_NAMES', cfg.lib_names), assoc('PHP_LIBS', cfg.php_libs),
             assoc('RUBY_PLATFORMS', cfg.ruby_platforms),
             assoc('STATIC_TREES', {cfg.bundle: len(cfg.swift_targets),
                                    cfg.xcframework: len(cfg.xcframework_slices),
                                    'go/staticlib': len(cfg.go_targets)})]
    return '\n'.join(lines) + '\n'


def bump(cfg, root, version):
    """Set every version file's one captured group to `version`; each must match once."""
    changed = []
    for relative, pattern in cfg.version_files.items():
        path = Path(root) / relative
        text = path.read_text(encoding='utf-8')
        matches = list(re.finditer(pattern, text, re.M))
        if len(matches) != 1:
            raise ValueError(f'{relative}: expected one version match for {pattern!r}, found {len(matches)}')
        match = matches[0]
        updated = text[:match.start(1)] + version + text[match.end(1):]
        if updated != text:
            path.write_text(updated, encoding='utf-8')
            changed.append(relative)
    return changed


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('shell', 'json', 'version-files', 'bump'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--version', help='bump: the bare X.Y.Z every version file is set to')
    args = parser.parse_args()
    cfg = HyperConfig.load(args.root)
    if args.mode == 'shell':
        print(shell(cfg), end='')
    elif args.mode == 'version-files':
        for path, pattern in cfg.version_files.items():
            print(f'{path}\t{pattern}')
    elif args.mode == 'bump':
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', args.version or ''):
            raise ValueError('bump needs a bare X.Y.Z version')
        for path in bump(cfg, args.root, args.version):
            print(path)
    else:
        print(json.dumps(dict(crate=cfg.crate, project=cfg.project, wheels=cfg.wheels,
                              ruby_abis=cfg.ruby_abis, ruby_platforms=cfg.ruby_platforms,
                              go_targets=cfg.go_targets, swift_targets=cfg.swift_targets,
                              xcframework_slices=cfg.xcframework_slices,
                              version_files=cfg.version_files), indent=2))


if __name__ == '__main__':
    main()
