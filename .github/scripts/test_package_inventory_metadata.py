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

SCRIPT = Path(__file__).with_name('release_package_metadata.py')
# npm platform package names (napi-rs), and the corpus members every R source tarball carries.
NODE_SUFFIXES = tuple(NODE_PLATFORMS.values())
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


def write_npm(root, crate, version, platform_version=None, dependency_version=None):
    """The nine `npm pack` tarballs: the root package, then one per napi platform."""
    names = [f'@skunkwerkx/{crate}-{suffix}' for suffix in NODE_SUFFIXES]
    dependencies = {name: dependency_version or version for name in names}
    write_tar(root / f'npm/{crate}-{version}.tgz', [
        ('package/package.json', npm_manifest(crate, version, optionalDependencies=dependencies)),
        ('package/index.js', b'module.exports = {}\n')])
    for suffix, name in zip(NODE_SUFFIXES, names):
        write_tar(root / f'npm/skunkwerkx-{crate}-{suffix}-{version}.tgz', [
            ('package/package.json', npm_manifest(name, platform_version or version)),
            (f'package/{crate}.{suffix}.node', b'native')])


def r_entries(crate, version, description=None, drop=()):
    entries = [(f'{crate}/DESCRIPTION', (description or f'Package: {crate}\nVersion: {version}\n').encode()),
               (f'{crate}/src/rust/vendor.tar.xz', b'vendored crates'),
               (f'{crate}/src/rust/Cargo.lock', b'version = 4\n'),
               (f'{crate}/src/rust/core/Cargo.toml', f'[package]\nname="{crate}"\n'.encode())]
    entries += [(f'{crate}/tests/testthat/corpus/{name}', b'[]') for name in R_CORPUS]
    return [(name, data) for name, data in entries if name not in drop]


def write_cran(root, crate, version, **changes):
    write_tar(root / f'cran/{crate}_{version}.tar.gz', r_entries(crate, version, **changes))


def create_packages(root, crate='hypercast', version='0.10.0'):
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
                 'optionalDependency at another version': dict(dependency_version='0.8.0')}
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
        names = [f'@skunkwerkx/hypercast-{s}' for s in NODE_SUFFIXES]
        broken = {
            'wrong root name': (root_tgz, npm_manifest('wrongproject', '0.10.0',
                                optionalDependencies={n: '0.10.0' for n in names})),
            'stale root version': (root_tgz, npm_manifest('hypercast', '0.8.0',
                                   optionalDependencies={n: '0.10.0' for n in names})),
            'missing optionalDependency': (root_tgz, npm_manifest('hypercast', '0.10.0',
                                           optionalDependencies={n: '0.10.0' for n in names[1:]})),
            'extra optionalDependency': (root_tgz, npm_manifest('hypercast', '0.10.0',
                                         optionalDependencies={**{n: '0.10.0' for n in names}, 'left-pad': '0.10.0'})),
            'wrong platform name': (platform, npm_manifest('@skunkwerkx/wrongproject-linux-x64-gnu', '0.10.0')),
            'platform named as another platform': (platform, npm_manifest(names[1], '0.10.0')),
            'platform named as the root': (platform, npm_manifest('hypercast', '0.10.0')),
        }
        for label, (path, manifest) in broken.items():
            with self.subTest(label):
                original = path.read_bytes()
                write_tar(path, [('package/package.json', manifest)])
                with self.assertRaisesRegex(ValueError, 'npm'):
                    self.validate()
                path.write_bytes(original)
        self.validate()
        original = root_tgz.read_bytes()
        write_tar(root_tgz, [('package/index.js', b'')])
        with self.assertRaises(ValueError):
            self.validate()
        root_tgz.write_bytes(original)
        (self.root / 'npm/extra-0.10.0.tgz').write_bytes(original)
        with self.assertRaises(ValueError):
            self.validate()

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

    def test_counts_follow_the_consumer_config(self):
        cfg = SimpleNamespace(wheels=24, ruby_platforms=dict(list(RUBY_PLATFORMS.items())[:6]),
                              node_platforms=NODE_PLATFORMS)
        with self.assertRaisesRegex(ValueError, '24 wheels'):
            self.m.validate_package_metadata(self.root, 'hypercast', '0.10.0', cfg)
        (self.root / 'pypi/hypercast-0.10.0-abi0-none-platform.whl').unlink()
        with self.assertRaisesRegex(ValueError, '8 RubyGems'):
            self.m.validate_package_metadata(self.root, 'hypercast', '0.10.0', cfg)


if __name__ == '__main__':
    unittest.main()
