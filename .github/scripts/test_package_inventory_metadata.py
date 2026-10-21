"""Real archive fixtures for embedded release identity checks; no registry access."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
import warnings
import zipfile

from package_preparation import crate_metadata

SCRIPT = Path(__file__).with_name('release_package_metadata.py')
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


def create_packages(root, crate='hypercast', version='0.10.0'):
    """Populate the exact package layout used by release_inventory tests."""
    for suffix in ('nupkg', 'snupkg'):
        write_zip(root / f'nuget/{crate}.{version}.{suffix}', [(f'{crate}.nuspec',
            f'<package xmlns="http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd"><metadata>'
            f'<id>{crate.title()}</id><version>{version}</version></metadata></package>')])
    for index in range(25):
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


if __name__ == '__main__':
    unittest.main()
