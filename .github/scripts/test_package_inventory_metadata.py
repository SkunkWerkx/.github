"""Real archive fixtures for embedded release identity checks; no registry access."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
import warnings
import zipfile

from package_preparation import crate_metadata
from hyper_config import NODE_PLATFORMS, RUBY_PLATFORMS
import php_ext_package

SCRIPT = Path(__file__).with_name('release_package_metadata.py')
# npm platform package names (napi-rs), and the corpus members every R source tarball carries.
NODE_SUFFIXES = tuple(NODE_PLATFORMS.values())
NODE_WASI = 'wasm32-wasip1'
WASI_RUNTIME_PINS = {'@napi-rs/wasm-runtime': '1.0.0', '@emnapi/core': '1.5.0', '@emnapi/runtime': '1.5.0'}
R_CORPUS = ('v5.json', 'sql_order.json', 'sql_order_refused.json', 'timestamp.json', 'inspect.json')
PLATFORMS = ('', '-x86_64-linux-gnu', '-aarch64-linux-gnu', '-x86_64-linux-musl',
             '-aarch64-linux-musl', '-arm64-darwin', '-x64-mingw-ucrt', '-aarch64-mingw-ucrt')


def write_zip(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in entries:
                archive.writestr(name, data)


def write_tar(path, entries, mode='w:gz'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode) as archive:
        for name, data in entries:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))


def npm_manifest(name, version, **fields):
    return json.dumps(dict(name=name, version=version, **fields)).encode()


def write_npm(root, crate, version, platform_version=None, dependency_version=None, wasi=True,
              wasi_dependencies=None):
    """The ten `npm pack` tarballs: the root package, one per napi platform, and the WASI
    fallback package the root depends on (nine without it, `wasi=False`)."""
    names = [f'@skunkwerkx/{crate}-{suffix}' for suffix in NODE_SUFFIXES]
    optional = {name: dependency_version or version for name in names}
    wasi_name = f'@skunkwerkx/{crate}-{NODE_WASI}'
    root_fields = dict(optionalDependencies=optional)
    if wasi:
        root_fields['dependencies'] = {wasi_name: dependency_version or version}
    write_tar(root / f'npm/{crate}-{version}.tgz', [
        ('package/package.json', npm_manifest(crate, version, **root_fields)),
        ('package/index.js', b'module.exports = {}\n')])
    for suffix, name in zip(NODE_SUFFIXES, names):
        write_tar(root / f'npm/skunkwerkx-{crate}-{suffix}-{version}.tgz', [
            ('package/package.json', npm_manifest(name, platform_version or version)),
            (f'package/{crate}.{suffix}.node', b'native')])
    if wasi:
        write_tar(root / f'npm/skunkwerkx-{crate}-{NODE_WASI}-{version}.tgz',
                  wasi_entries(npm_manifest(wasi_name, platform_version or version,
                                            dependencies=wasi_dependencies or WASI_RUNTIME_PINS)))


def wasi_entries(manifest, crate='hypercast', extra=()):
    """A WASI package's members: the manifest, the one .wasm and the Node loader, plus `extra`."""
    return [('package/package.json', manifest), (f'package/{crate}.{NODE_WASI}.wasm', b'\0asm'),
            (f'package/{crate}.wasip1.cjs', b'module.exports = {}\n'), *extra]


def vendor_tarball(entries):
    """A `vendor.tar.xz` as vendor.sh writes it, with the given (name, bytes) members."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:xz') as archive:
        for name, data in entries:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return out.getvalue()


def r_entries(crate, version, description=None, drop=(), pin=None, vendored=None, vendor=None):
    """The members `R CMD build` packs: DESCRIPTION, the wrapper manifest pinning the core at
    `pin` (the version by default), the vendor tarball carrying the core at `vendored` (the
    version by default; None leaves the core out; `vendor` replaces the whole tarball), the
    derived lock and the corpus."""
    pin = version if pin is None else pin
    vendored = version if vendored is None else vendored
    if vendor is None:
        crates = [('vendor/libc/Cargo.toml', b'[package]\nname = "libc"\nversion = "0.2.0"\n')]
        if vendored:
            crates.append((f'vendor/{crate}/Cargo.toml',
                           f'[package]\nname = "{crate}"\nversion = "{vendored}"\n'.encode()))
        vendor = vendor_tarball(crates)
    manifest = (f'[package]\nname = "rcore"\n\n[dependencies]\n'
                f'{crate} = {{ version = "={pin}", default-features = false, features = ["staticlib"] }}\n')
    entries = [(f'{crate}/DESCRIPTION', (description or f'Package: {crate}\nVersion: {version}\n').encode()),
               (f'{crate}/src/rust/Cargo.toml', manifest.encode()),
               (f'{crate}/src/rust/vendor.tar.xz', vendor),
               (f'{crate}/src/rust/Cargo.lock', b'version = 4\n')]
    entries += [(f'{crate}/tests/testthat/corpus/{name}', b'[]') for name in R_CORPUS]
    return [(name, data) for name, data in entries if name not in drop]


def write_cran(root, crate, version, **changes):
    write_tar(root / f'cran/{crate}_{version}.tar.gz', r_entries(crate, version, **changes))


def write_php(root, crate, version):
    """The release's PIE zips, one per platform and minor, named with the release tag."""
    for name in php_ext_package.release_asset_names(f'v{version}', crate):
        stem = name.removesuffix('.zip')
        write_zip(root / f'php/{name}', [(f'{stem}.dll' if '-nts-vs' in name else f'{crate}.so', b'extension')])


def sdist_entries(crate, version, lock=True, cargo_version=None, cargo=True):
    top = f'{crate}-{version}'
    entries = [(f'{top}/PKG-INFO', f'Metadata-Version: 2.3\nName: {crate}\nVersion: {version}\n'.encode())]
    if cargo:
        entries.append((f'{top}/rust/Cargo.toml', f'[package]\nname = "{crate}"\nversion = "{cargo_version or version}"\n'.encode()))
    if lock:
        entries.append((f'{top}/rust/Cargo.lock', b'version = 4\n'))
    return entries


def create_packages(root, crate='hypercast', version='0.10.0', sdist=False):
    """Populate the exact package layout used by release_inventory tests."""
    for suffix in ('nupkg', 'snupkg'):
        write_zip(root / f'nuget/{crate}.{version}.{suffix}', [(f'{crate}.nuspec',
            f'<package xmlns="http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd"><metadata>'
            f'<id>{crate.title()}</id><version>{version}</version></metadata></package>')])
    for index in range(17):
        write_zip(root / f'pypi/{crate}-{version}-abi{index}-none-platform.whl', [
            (f'{crate}-{version}.dist-info/METADATA',
             f'Metadata-Version: 2.3\nName: {crate}\nVersion: {version}\n\n')])
    for platform in (*PLATFORMS, '-wasm'):
        name = crate + ('-wasm' if platform == '-wasm' else '')
        filename = f'{name}-{version}' + (platform if platform != '-wasm' else '') + '.gem'
        metadata = f'--- !ruby/object:Gem::Specification\nname: {name}\nversion: !ruby/object:Gem::Version\n  version: {version}\nplatform: ruby\n'
        write_tar(root / f'ruby/{filename}', [('metadata.gz', gzip.compress(metadata.encode())),
                                            ('data.tar.gz', gzip.compress(b''))], 'w')
    if sdist:
        write_tar(root / f'pypi/{crate}-{version}.tar.gz', sdist_entries(crate, version))
    crate_file = root / f'crate/{crate}-{version}.crate'
    write_tar(crate_file, [(f'{crate}-{version}/Cargo.toml',
                           f'[package]\nname="{crate}"\nversion="{version}"\n'.encode())])
    metadata = json.dumps(crate_metadata(crate_file)).encode()
    (crate_file.parent / 'metadata.json').write_bytes(metadata)
    body = crate_file.read_bytes()
    (crate_file.parent / 'upload.bin').write_bytes(struct.pack('<I', len(metadata)) + metadata
                                                + struct.pack('<I', len(body)) + body)
    prefix = f'io/github/skunkwerkx/{crate}/{version}/{crate}-{version}'
    entries = []
    for suffix in ('.pom', '.jar', '-sources.jar', '-javadoc.jar', '.module'):
        data = (f'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
                f'<groupId>io.github.skunkwerkx</groupId><artifactId>{crate}</artifactId>'
                f'<version>{version}</version><name>{crate}</name></project>').encode() if suffix == '.pom' else b'jar'
        entries += [(prefix + suffix, data), (prefix + suffix + '.asc', b'signature')]
    write_zip(root / 'maven/central-bundle.zip', entries)
    for name, data in entries:
        path = root / 'maven/repository' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    write_npm(root, crate, version)
    write_cran(root, crate, version)
    write_php(root, crate, version)


class PackageMetadataTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'package metadata validator is missing')
        spec = importlib.util.spec_from_file_location('metadata', SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        create_packages(self.root)

    def validate(self):
        self.m.validate_package_metadata(self.root, 'hypercast', '0.10.0')

    def rewrite_zip(self, path, change):
        with zipfile.ZipFile(path) as archive:
            entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
        write_zip(path, change(entries))

    def test_accepts_all_real_package_formats_and_pascalcase_nuget_id(self):
        self.validate()

    def test_rejects_framed_request_with_incorrect_dependencies_before_publication(self):
        directory = self.root/'crate'
        metadata = json.loads((directory/'metadata.json').read_text())
        metadata['deps'] = [{'name':'wrong','version_req':'1'}]
        data = json.dumps(metadata).encode()
        crate = (directory/'hypercast-0.10.0.crate').read_bytes()
        (directory/'metadata.json').write_bytes(data)
        (directory/'upload.bin').write_bytes(struct.pack('<I',len(data))+data+struct.pack('<I',len(crate))+crate)
        with self.assertRaisesRegex(ValueError,'metadata'):
            self.validate()

    def test_rejects_stale_versions_hidden_behind_current_filenames(self):
        targets = [self.root / 'nuget/hypercast.0.10.0.nupkg',
                   self.root / 'nuget/hypercast.0.10.0.snupkg',
                   self.root / 'pypi/hypercast-0.10.0-abi0-none-platform.whl',
                   self.root / 'maven/central-bundle.zip']
        for path in targets:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                self.rewrite_zip(path, lambda entries: [(name, data.replace(b'0.10.0', b'0.8.0')) for name, data in entries])
                with self.assertRaises(ValueError):
                    self.validate()
                path.write_bytes(original)

    def test_rejects_wrong_project_inside_each_zip_format(self):
        for path in (self.root / 'nuget/hypercast.0.10.0.nupkg',
                     self.root / 'pypi/hypercast-0.10.0-abi0-none-platform.whl',
                     self.root / 'maven/central-bundle.zip'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                self.rewrite_zip(path, lambda entries: [(name, data.replace(b'hypercast', b'wrongproject').replace(b'Hypercast', b'WrongProject')) for name, data in entries])
                with self.assertRaises(ValueError):
                    self.validate()
                path.write_bytes(original)

    def test_rejects_duplicate_zip_entries_in_each_format(self):
        for path in (self.root / 'nuget/hypercast.0.10.0.nupkg',
                     self.root / 'pypi/hypercast-0.10.0-abi0-none-platform.whl',
                     self.root / 'maven/central-bundle.zip'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                self.rewrite_zip(path, lambda entries: entries + [entries[0]])
                with self.assertRaises(ValueError):
                    self.validate()
                path.write_bytes(original)

    def test_rejects_stale_or_wrong_gem_metadata_and_duplicate_tar_entries(self):
        path = self.root / 'ruby/hypercast-0.10.0.gem'
        original = path.read_bytes()
        for name, version, duplicate in [('hypercast', '0.8.0', False),
                                         ('wrongproject', '0.10.0', False),
                                         ('hypercast', '0.10.0', True)]:
            with self.subTest(name=name, version=version, duplicate=duplicate):
                metadata = f'--- !ruby/object:Gem::Specification\nname: {name}\nversion: !ruby/object:Gem::Version\n  version: {version}\n'
                entries = [('metadata.gz', gzip.compress(metadata.encode()))]
                write_tar(path, entries * (2 if duplicate else 1), 'w')
                with self.assertRaises(ValueError):
                    self.validate()
                path.write_bytes(original)

    def test_rejects_crate_manifest_identity_and_duplicate_members(self):
        path = self.root / 'crate/hypercast-0.10.0.crate'
        original = path.read_bytes()
        for name, version, duplicate in [('hypercast', '0.8.0', False),
                                         ('wrongproject', '0.10.0', False),
                                         ('hypercast', '0.10.0', True)]:
            with self.subTest(name=name, version=version, duplicate=duplicate):
                manifest = f'[package]\nname="{name}"\nversion="{version}"\n'.encode()
                entries = [('hypercast-0.10.0/Cargo.toml', manifest)]
                write_tar(path, entries * (2 if duplicate else 1))
                with self.assertRaises(ValueError):
                    self.validate()
                path.write_bytes(original)

    def test_rejects_wrong_saved_crate_metadata_or_upload_request(self):
        path = self.root / 'crate/metadata.json'
        for metadata in ({'name': 'hypercast', 'vers': '0.8.0'},
                         {'name': 'wrongproject', 'vers': '0.10.0'}):
            path.write_text(json.dumps(metadata))
            with self.assertRaises(ValueError):
                self.validate()
        path.write_text('{"name":"hypercast","vers":"0.10.0"}')
        (self.root / 'crate/upload.bin').write_bytes(b'stale request')
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_missing_maven_signature(self):
        path = self.root / 'maven/central-bundle.zip'
        self.rewrite_zip(path, lambda entries: [(name, data) for name, data in entries if not name.endswith('-sources.jar.asc')])
        with self.assertRaises(ValueError):
            self.validate()

    def test_rejects_duplicate_metadata_identity_fields(self):
        path = self.root / 'pypi/hypercast-0.10.0-abi0-none-platform.whl'
        self.rewrite_zip(path, lambda entries: [(name, data.replace(b'Version: 0.10.0', b'Version: 0.10.0\nVersion: 0.8.0')) for name, data in entries])
        with self.assertRaises(ValueError):
            self.validate()

    def test_npm_and_cran_groups_are_optional(self):
        shutil.rmtree(self.root / 'npm')
        shutil.rmtree(self.root / 'cran')
        self.validate()

    def test_rejects_npm_tarballs_with_stale_or_wrong_identity(self):
        cases = {'platform at another version': dict(platform_version='0.8.0'),
                 'optionalDependency at another version': dict(dependency_version='0.8.0'),
                 'wasi package missing': dict(wasi=False)}
        for label, change in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.root / 'npm')
                write_npm(self.root, 'hypercast', '0.10.0', **change)
                with self.assertRaisesRegex(ValueError, 'npm'):
                    self.validate()
        shutil.rmtree(self.root / 'npm')
        write_npm(self.root, 'hypercast', '0.10.0')
        root_tgz = self.root / 'npm/hypercast-0.10.0.tgz'
        platform = self.root / f'npm/skunkwerkx-hypercast-{NODE_SUFFIXES[0]}-0.10.0.tgz'
        wasi = self.root / f'npm/skunkwerkx-hypercast-{NODE_WASI}-0.10.0.tgz'
        names = [f'@skunkwerkx/hypercast-{s}' for s in NODE_SUFFIXES]
        wasi_name = f'@skunkwerkx/hypercast-{NODE_WASI}'
        optional = {n: '0.10.0' for n in names}
        wasi_dependency = {wasi_name: '0.10.0'}

        def root_manifest(name='hypercast', version='0.10.0', optional=optional, **fields):
            return npm_manifest(name, version, optionalDependencies=optional, **fields)

        def wasi_manifest(dependencies=WASI_RUNTIME_PINS, **fields):
            return npm_manifest(wasi_name, '0.10.0', dependencies=dependencies, **fields)

        broken = {
            'wrong root name': (root_tgz, [('package/package.json', root_manifest('wrongproject', dependencies=wasi_dependency))]),
            'stale root version': (root_tgz, [('package/package.json', root_manifest(version='0.8.0', dependencies=wasi_dependency))]),
            'missing optionalDependency': (root_tgz, [('package/package.json', root_manifest(
                optional={n: '0.10.0' for n in names[1:]}, dependencies=wasi_dependency))]),
            'extra optionalDependency': (root_tgz, [('package/package.json', root_manifest(
                optional={**optional, 'left-pad': '0.10.0'}, dependencies=wasi_dependency))]),
            'root without dependencies': (root_tgz, [('package/package.json', root_manifest())]),
            'root dependency at another version': (root_tgz, [('package/package.json', root_manifest(
                dependencies={wasi_name: '0.8.0'}))]),
            'root carrying an addon': (root_tgz, [('package/package.json', root_manifest(dependencies=wasi_dependency)),
                                                  ('package/hypercast.linux-x64-gnu.node', b'native')]),
            'wrong platform name': (platform, [('package/package.json', npm_manifest('@skunkwerkx/wrongproject-linux-x64-gnu', '0.10.0'))]),
            'platform named as another platform': (platform, [('package/package.json', npm_manifest(names[1], '0.10.0'))]),
            'platform named as the root': (platform, [('package/package.json', npm_manifest('hypercast', '0.10.0'))]),
            'platform carrying a wasm': (platform, [('package/package.json', npm_manifest(names[0], '0.10.0')),
                                                    (f'package/hypercast.{NODE_SUFFIXES[0]}.node', b'native'),
                                                    (f'package/hypercast.{NODE_WASI}.wasm', b'\0asm')]),
            'wasi declaring a cpu': (wasi, wasi_entries(wasi_manifest(cpu=['wasm32']))),
            'wasi declaring an os': (wasi, wasi_entries(wasi_manifest(os=['linux']))),
            'wasi declaring a libc': (wasi, wasi_entries(wasi_manifest(libc=['glibc']))),
            'wasi runtime as a tilde range': (wasi, wasi_entries(wasi_manifest({**WASI_RUNTIME_PINS, '@napi-rs/wasm-runtime': '~1.0.0'}))),
            'wasi runtime as a caret range': (wasi, wasi_entries(wasi_manifest({**WASI_RUNTIME_PINS, '@emnapi/core': '^1.5.0'}))),
            'wasi runtime as a tag': (wasi, wasi_entries(wasi_manifest({**WASI_RUNTIME_PINS, '@emnapi/runtime': 'latest'}))),
            'wasi runtime as a url': (wasi, wasi_entries(wasi_manifest(
                {**WASI_RUNTIME_PINS, '@emnapi/runtime': 'git+https://github.com/toyobayashi/emnapi.git'}))),
            'wasi with a fourth dependency': (wasi, wasi_entries(wasi_manifest({**WASI_RUNTIME_PINS, 'tslib': '2.8.1'}))),
            'wasi missing a runtime dependency': (wasi, wasi_entries(wasi_manifest(
                {k: v for k, v in WASI_RUNTIME_PINS.items() if k != '@emnapi/core'}))),
            'wasi without dependencies': (wasi, wasi_entries(npm_manifest(wasi_name, '0.10.0'))),
            'wasi carrying an addon': (wasi, wasi_entries(wasi_manifest(), extra=[('package/hypercast.linux-x64-gnu.node', b'native')])),
            'wasi carrying two wasm modules': (wasi, wasi_entries(wasi_manifest(),
                                                                  extra=[(f'package/hypercast.{NODE_WASI}.debug.wasm', b'\0asm')])),
            'wasi without its wasm': (wasi, [('package/package.json', wasi_manifest()), ('package/hypercast.wasip1.cjs', b'')]),
            'wasi without its loader': (wasi, [('package/package.json', wasi_manifest()), (f'package/hypercast.{NODE_WASI}.wasm', b'\0asm')]),
            'wasi named as a platform': (wasi, wasi_entries(npm_manifest(names[0], '0.10.0', dependencies=WASI_RUNTIME_PINS))),
        }
        for label, (path, entries) in broken.items():
            with self.subTest(label):
                original = path.read_bytes()
                write_tar(path, entries)
                with self.assertRaisesRegex(ValueError, 'npm'):
                    self.validate()
                path.write_bytes(original)
        self.validate()
        original = wasi.read_bytes()
        write_tar(wasi, wasi_entries(wasi_manifest({**WASI_RUNTIME_PINS, '@emnapi/core': '2.0.0-alpha.4', '@emnapi/runtime': '2.0.0-alpha.4'})))
        self.validate()
        wasi.write_bytes(original)
        original = root_tgz.read_bytes()
        write_tar(root_tgz, [('package/index.js', b'')])
        with self.assertRaises(ValueError):
            self.validate()
        root_tgz.write_bytes(original)
        (self.root / 'npm/extra-0.10.0.tgz').write_bytes(original)
        with self.assertRaises(ValueError):
            self.validate()

    def test_npm_tarball_compares_wasi_runtime_when_given(self):
        wasi = self.root / f'npm/skunkwerkx-hypercast-{NODE_WASI}-0.10.0.tgz'
        self.assertEqual(self.m.npm_tarball(wasi, 'hypercast', '0.10.0', NODE_SUFFIXES, NODE_WASI, WASI_RUNTIME_PINS),
                         f'@skunkwerkx/hypercast-{NODE_WASI}')
        with self.assertRaisesRegex(ValueError, 'npm'):
            self.m.npm_tarball(wasi, 'hypercast', '0.10.0', NODE_SUFFIXES, NODE_WASI,
                               {**WASI_RUNTIME_PINS, '@emnapi/core': '1.5.1'})
        with self.assertRaisesRegex(ValueError, 'npm'):
            self.m.npm_tarball(wasi, 'hypercast', '0.10.0', NODE_SUFFIXES, '')
        self.assertEqual(self.m.npm_all_names('hypercast', NODE_SUFFIXES, NODE_WASI)[-1], f'@skunkwerkx/hypercast-{NODE_WASI}')
        self.assertEqual(len(self.m.npm_all_names('hypercast', NODE_SUFFIXES, '')), 9)
        self.assertIsNone(self.m.npm_wasi_name('hypercast', ''))
        self.assertEqual(self.m.WASI_RUNTIME, ('@napi-rs/wasm-runtime', '@emnapi/core', '@emnapi/runtime'))
        self.assertEqual(self.m.DEFAULTS.node_wasi, NODE_WASI)

    def test_rejects_r_source_tarball_with_stale_identity_or_missing_members(self):
        path = self.root / 'cran/hypercast_0.10.0.tar.gz'
        members = [name for name, _ in r_entries('hypercast', '0.10.0')
                   if name != 'hypercast/DESCRIPTION']
        self.assertEqual(len(members), 8)
        for member in members:
            with self.subTest(missing=member):
                write_cran(self.root, 'hypercast', '0.10.0', drop=(member,))
                with self.assertRaisesRegex(ValueError, 'R source tarball'):
                    self.validate()
        for label, description in {'stale version': 'Package: hypercast\nVersion: 0.8.0\n',
                                   'wrong package': 'Package: wrongproject\nVersion: 0.10.0\n',
                                   'two versions': 'Package: hypercast\nVersion: 0.10.0\nVersion: 0.8.0\n'}.items():
            with self.subTest(label):
                write_cran(self.root, 'hypercast', '0.10.0', description=description)
                with self.assertRaisesRegex(ValueError, 'R source tarball'):
                    self.validate()
        write_tar(path, [(name.replace('hypercast/', 'wrongproject/', 1), data)
                         for name, data in r_entries('hypercast', '0.10.0')])
        with self.assertRaisesRegex(ValueError, 'R source tarball'):
            self.validate()
        write_cran(self.root, 'hypercast', '0.10.0')
        self.validate()

    def test_rejects_r_source_tarball_whose_core_pin_or_vendored_core_is_off(self):
        # The wrapper crate depends on the core from crates.io at exactly the release
        # version, and vendor.sh puts that version's source in the vendor tarball; either
        # at another version means CRAN would build (or fail to fetch) a different core.
        for label, changes in {'pin at another version': dict(pin='0.9.0'),
                               'pin not exact': dict(pin='0.10.0', vendored='0.10.0'),
                               'vendored core at another version': dict(vendored='0.9.0'),
                               'core not vendored': dict(vendored=''),
                               'vendor tarball not xz': dict(vendor=b'vendored crates')}.items():
            with self.subTest(label):
                if label == 'pin not exact':
                    entries = [(name, data.replace(b'"=0.10.0"', b'"0.10.0"') if name.endswith('src/rust/Cargo.toml') else data)
                               for name, data in r_entries('hypercast', '0.10.0')]
                    write_tar(self.root / 'cran/hypercast_0.10.0.tar.gz', entries)
                else:
                    write_cran(self.root, 'hypercast', '0.10.0', **changes)
                with self.assertRaisesRegex(ValueError, 'R (source tarball|wrapper crate)'):
                    self.validate()
        write_cran(self.root, 'hypercast', '0.10.0')
        self.validate()

    def sdist_cfg(self, sdist=True):
        return SimpleNamespace(**vars(self.m.DEFAULTS) | {'sdist': sdist})

    def validate_sdist(self):
        self.m.validate_package_metadata(self.root, 'hypercast', '0.10.0', self.sdist_cfg())

    def test_accepts_an_sdist_when_the_config_says_so(self):
        create_packages(self.root, sdist=True)
        self.validate_sdist()

    def test_sdist_count_is_exact(self):
        with self.assertRaisesRegex(ValueError, 'expected 17 wheels and 1 sdist'):
            self.validate_sdist()
        create_packages(self.root, sdist=True)
        with self.assertRaisesRegex(ValueError, 'expected 17 wheels and 0 sdist'):
            self.m.validate_package_metadata(self.root, 'hypercast', '0.10.0', self.sdist_cfg(False))
        write_tar(self.root / 'pypi/hypercast-0.10.0-1.tar.gz', sdist_entries('hypercast', '0.10.0'))
        with self.assertRaisesRegex(ValueError, 'expected 17 wheels and 1 sdist'):
            self.validate_sdist()

    def test_sdist_rejects_wrong_identity(self):
        path = self.root / 'pypi/hypercast-0.10.0.tar.gz'
        top = 'hypercast-0.10.0'
        stale = [(n, d.replace(b'Version: 0.10.0', b'Version: 0.9.0')) for n, d in sdist_entries('hypercast', '0.10.0')]
        wrong_name = [(n, d.replace(b'Name: hypercast', b'Name: other')) for n, d in sdist_entries('hypercast', '0.10.0')]
        two_tops = sdist_entries('hypercast', '0.10.0') + [('other-0.10.0/README', b'x')]
        wrong_top = [(n.replace(top, 'hypercast-0.9.0', 1), d) for n, d in sdist_entries('hypercast', '0.10.0')]
        for label, entries in (('stale PKG-INFO', stale), ('wrong Name', wrong_name),
                               ('two top directories', two_tops), ('wrong top directory', wrong_top)):
            with self.subTest(label):
                create_packages(self.root, sdist=False)
                write_tar(path, entries)
                with self.assertRaises(ValueError):
                    self.validate_sdist()

    def test_sdist_must_carry_the_crate_and_its_lock(self):
        path = self.root / 'pypi/hypercast-0.10.0.tar.gz'
        for label, entries in (('no Cargo.toml', sdist_entries('hypercast', '0.10.0', cargo=False)),
                               ('stale Cargo.toml', sdist_entries('hypercast', '0.10.0', cargo_version='0.9.0')),
                               ('no Cargo.lock', sdist_entries('hypercast', '0.10.0', lock=False))):
            with self.subTest(label):
                write_tar(path, entries)
                with self.assertRaises(ValueError):
                    self.validate_sdist()

    def test_counts_follow_the_consumer_config(self):
        cfg = SimpleNamespace(wheels=24, sdist=False, ruby_platforms=dict(list(RUBY_PLATFORMS.items())[:6]),
                              node_platforms=NODE_PLATFORMS, node_wasi=NODE_WASI)
        with self.assertRaisesRegex(ValueError, '24 wheels'):
            self.m.validate_package_metadata(self.root, 'hypercast', '0.10.0', cfg)
        (self.root / 'pypi/hypercast-0.10.0-abi0-none-platform.whl').unlink()
        with self.assertRaisesRegex(ValueError, '8 RubyGems'):
            self.m.validate_package_metadata(self.root, 'hypercast', '0.10.0', cfg)


if __name__ == '__main__':
    unittest.main()
