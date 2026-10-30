"""The C release archives (`c_assets`): the flag, the layout per platform, the templates, the
Windows import library and the archives' bytes."""
import importlib.util
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_hyper_config import MINIMAL, write_config

SCRIPTS = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c_package = load('c_package')
hyper_config = load('hyper_config')
C = {**MINIMAL, 'c_assets': True}
TEMPLATES = {
    'c/hyperuuid.pc.in': 'Version: @VERSION@\n',
    'c/cmake/hyperuuidConfig.cmake': '# config\n',
    'c/cmake/hyperuuidConfigVersion.cmake.in': 'set(PACKAGE_VERSION "@VERSION@") # @MAJOR@ @POINTER_SIZE@\n',
    'c/README.md': '# hyperuuid for C\n',
    'include/hyperuuid.h': '/* header */\n',
    'include/hyperuuid.hpp': '// C++ header\n',
    'LICENSE': 'MIT\n',
    'rust/Cargo.toml': '[package]\nname = "hyperuuid"\nversion = "1.1.0"\n',
}
READOBJ = '''File: hyperuuid.dll
Export {
  Ordinal: 1
  Name: hyperuuid_version
  RVA: 0x1000
}
Export {
  Ordinal: 2
  Name: uuid_new_v4
  RVA: 0x1010
}
'''


def fake_import_library(cfg, rid, dll, destination, readobj, dlltool):
    destination.write_text(f'import library for {dll.name} ({rid})')
    return ['hyperuuid_version']


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        for relative, text in TEMPLATES.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        write_config(self.root, C)
        self.cfg = hyper_config.load(self.root)
        self.libs = Path(self.tmp.name) / 'libs'
        self.libs.mkdir()
        patcher = patch.object(c_package, 'import_library', fake_import_library)
        patcher.start()
        self.addCleanup(patcher.stop)
        tools = patch.object(c_package, 'find_tool', lambda name: name)
        tools.start()
        self.addCleanup(tools.stop)

    def lib(self, name):
        path = self.libs / name
        path.write_bytes(name.encode())
        return path

    def assemble(self, rid, static, shared):
        return c_package.assemble(self.cfg, self.root, rid, '1.1.0', static, shared, Path(self.tmp.name) / 'out')

    def artifacts(self):
        """Every input `pack` reads, laid out as the CI run's artifacts download."""
        artifacts = Path(self.tmp.name) / 'artifacts'
        for rid in c_package.C_RIDS:
            shared, static = c_package.artifact_inputs(self.cfg, artifacts, rid)
            for path in (shared, static):
                if path is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(f'{rid} {path.name}'.encode())
        return artifacts


def files(tree):
    return sorted(p.relative_to(tree).as_posix() for p in tree.rglob('*') if p.is_file())


class ConfigTests(unittest.TestCase):
    def test_c_assets_is_off_by_default_and_must_be_a_bool(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_config(tmp)
            self.assertFalse(hyper_config.load(tmp).c_assets)
            write_config(tmp, {**MINIMAL, 'c_assets': 'yes'})
            with self.assertRaisesRegex(ValueError, 'c_assets must be true or false'):
                hyper_config.load(tmp)

    def test_shell_exports_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_config(tmp, C)
            out = subprocess.run(['python3', str(SCRIPTS / 'hyper_config.py'), 'shell'], cwd=tmp,
                                 check=True, capture_output=True, text=True).stdout
            self.assertIn('C_ASSETS=true', out.splitlines())

    def test_one_archive_per_platform_zip_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_config(tmp, C)
            names = c_package.asset_names(hyper_config.load(tmp), '1.1.0')
        self.assertEqual(len(names), 15)
        self.assertIn('hyperuuid-1.1.0-iossimulator-arm64.tar.gz', names)
        self.assertIn('hyperuuid-1.1.0-linux-musl-x64.tar.gz', names)
        self.assertIn('hyperuuid-1.1.0-win-arm64.zip', names)
        self.assertIn('hyperuuid-1.1.0-wasi-wasm32.tar.gz', names)


class AssembleTests(Fixture):
    def test_linux_carries_both_libraries_and_the_package_files(self):
        tree = self.assemble('linux-x64', self.lib('libhyperuuid.a'), self.lib('libhyperuuid.so'))
        self.assertEqual(tree.name, 'hyperuuid-1.1.0-linux-x64')
        self.assertEqual(files(tree), [
            'LICENSE', 'README.md', 'include/hyperuuid.h', 'include/hyperuuid.hpp',
            'lib/cmake/hyperuuid/hyperuuidConfig.cmake',
            'lib/cmake/hyperuuid/hyperuuidConfigVersion.cmake', 'lib/libhyperuuid.a', 'lib/libhyperuuid.so',
            'lib/pkgconfig/hyperuuid.pc'])
        self.assertEqual((tree / 'lib/pkgconfig/hyperuuid.pc').read_text(), 'Version: 1.1.0\n')
        self.assertEqual((tree / 'lib/cmake/hyperuuid/hyperuuidConfigVersion.cmake').read_text(),
                         'set(PACKAGE_VERSION "1.1.0") # 1 8\n')

    def test_a_consumer_without_a_cpp_header_ships_the_c_one_alone(self):
        (self.root / 'include/hyperuuid.hpp').unlink()
        tree = self.assemble('linux-x64', self.lib('libhyperuuid.a'), self.lib('libhyperuuid.so'))
        self.assertEqual([f for f in files(tree) if f.startswith('include/')], ['include/hyperuuid.h'])

    def test_macos_ships_the_dylib(self):
        tree = self.assemble('osx-arm64', self.lib('libhyperuuid.a'), self.lib('libhyperuuid.dylib'))
        self.assertIn('lib/libhyperuuid.dylib', files(tree))

    def test_windows_puts_the_dll_in_bin_beside_its_import_library(self):
        tree = self.assemble('win-arm64', self.lib('hyperuuid.lib'), self.lib('hyperuuid.dll'))
        self.assertTrue({'bin/hyperuuid.dll', 'lib/hyperuuid.dll.lib', 'lib/hyperuuid.lib'} <= set(files(tree)))
        self.assertEqual((tree / 'lib/hyperuuid.dll.lib').read_text(), 'import library for hyperuuid.dll (win-arm64)')

    def test_wasi_is_static_only_and_32_bit(self):
        tree = self.assemble('wasi-wasm32', self.lib('libhyperuuid.a'), None)
        self.assertEqual([f for f in files(tree) if f.startswith(('lib/lib', 'bin/'))], ['lib/libhyperuuid.a'])
        self.assertTrue((tree / 'lib/cmake/hyperuuid/hyperuuidConfigVersion.cmake').read_text().endswith('# 1 4\n'))

    def test_apple_mobile_is_static_only_and_64_bit(self):
        for rid in c_package.APPLE_MOBILE_RIDS:
            with self.subTest(rid=rid):
                tree = self.assemble(rid, self.lib('libhyperuuid.a'), None)
                self.assertEqual([f for f in files(tree) if f.startswith(('lib/lib', 'bin/'))], ['lib/libhyperuuid.a'])
                self.assertTrue((tree / 'lib/cmake/hyperuuid/hyperuuidConfigVersion.cmake').read_text().endswith('# 1 8\n'))
        with self.assertRaisesRegex(ValueError, 'ios-arm64 has no shared library'):
            self.assemble('ios-arm64', self.lib('libhyperuuid.a'), self.lib('libhyperuuid.dylib'))

    def test_a_platform_must_have_exactly_the_libraries_it_ships(self):
        with self.assertRaisesRegex(ValueError, 'linux-x64 needs shared library'):
            self.assemble('linux-x64', self.lib('libhyperuuid.a'), None)
        with self.assertRaisesRegex(ValueError, 'wasi-wasm32 has no shared library'):
            self.assemble('wasi-wasm32', self.lib('libhyperuuid.a'), self.lib('libhyperuuid.so'))
        with self.assertRaisesRegex(ValueError, 'missing library'):
            self.assemble('linux-x64', self.libs / 'absent.a', self.lib('libhyperuuid.so'))
        with self.assertRaisesRegex(ValueError, 'unknown C platform'):
            self.assemble('freebsd-x64', self.lib('libhyperuuid.a'), self.lib('libhyperuuid.so'))

    def test_an_unfilled_placeholder_stops_the_pack(self):
        (self.root / 'c/README.md').write_text('version @VERSON@\n')
        with self.assertRaisesRegex(ValueError, r"c/README.md has placeholders nothing fills: \['@VERSON@'\]"):
            self.assemble('linux-x64', self.lib('libhyperuuid.a'), self.lib('libhyperuuid.so'))

    def test_without_the_flag_nothing_is_assembled(self):
        write_config(self.root, MINIMAL)
        self.cfg = hyper_config.load(self.root)
        with self.assertRaisesRegex(ValueError, 'does not set c_assets'):
            self.assemble('linux-x64', self.lib('libhyperuuid.a'), self.lib('libhyperuuid.so'))


class ImportLibraryTests(unittest.TestCase):
    def test_the_definition_lists_exactly_the_dll_exports(self):
        seen = {}

        def run(command, **kwargs):
            if command[1] == '--coff-exports':
                return subprocess.CompletedProcess(command, 0, stdout=READOBJ)
            seen['command'] = command
            seen['definition'] = Path(command[command.index('-d') + 1]).read_text()
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as tmp, patch.object(c_package.subprocess, 'run', run):
            dll = Path(tmp) / 'hyperuuid.dll'
            exports = c_package.import_library(None, 'win-x64', dll, Path(tmp) / 'hyperuuid.dll.lib', 'readobj', 'dlltool')
            self.assertFalse((Path(tmp) / 'hyperuuid.dll.def').exists())
        self.assertEqual(exports, ['hyperuuid_version', 'uuid_new_v4'])
        self.assertEqual(seen['definition'], 'LIBRARY hyperuuid.dll\nEXPORTS\n  hyperuuid_version\n  uuid_new_v4\n')
        self.assertEqual(seen['command'][:3], ['dlltool', '-m', 'i386:x86-64'])

    def test_a_dll_that_exports_nothing_is_refused(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout='File: hyperuuid.dll\n')

        with patch.object(c_package.subprocess, 'run', run), self.assertRaisesRegex(ValueError, 'exports nothing'):
            c_package.coff_exports(Path('hyperuuid.dll'), 'readobj')


class PackTests(Fixture):
    def test_pack_builds_every_archive_deterministically(self):
        artifacts = self.artifacts()
        first = c_package.pack(self.cfg, self.root, artifacts, Path(self.tmp.name) / 'first')
        second = c_package.pack(self.cfg, self.root, artifacts, Path(self.tmp.name) / 'second')
        self.assertEqual(sorted(p.name for p in first), sorted(c_package.asset_names(self.cfg, '1.1.0')))
        self.assertEqual(sorted(p.name for p in (Path(self.tmp.name) / 'first').iterdir()),
                         sorted(p.name for p in first))
        for a, b in zip(first, second):
            self.assertEqual(a.read_bytes(), b.read_bytes(), a.name)

    def test_the_tarball_has_fixed_owners_times_and_modes(self):
        out = Path(self.tmp.name) / 'out'
        c_package.pack(self.cfg, self.root, self.artifacts(), out)
        path = out / 'hyperuuid-1.1.0-linux-arm64.tar.gz'
        self.assertEqual(path.read_bytes()[4:8], bytes(4), 'the gzip header carries a timestamp')
        with tarfile.open(path) as archive:
            members = {m.name: m for m in archive.getmembers()}
            self.assertEqual((archive.extractfile('hyperuuid-1.1.0-linux-arm64/lib/libhyperuuid.so').read()),
                             b'linux-arm64 libhyperuuid.so')
        self.assertEqual(next(iter(members)), 'hyperuuid-1.1.0-linux-arm64')
        self.assertTrue(all(m.uid == 0 and m.gid == 0 and m.mtime == c_package.EPOCH for m in members.values()))
        self.assertEqual(members['hyperuuid-1.1.0-linux-arm64/lib/libhyperuuid.so'].mode, 0o755)
        self.assertEqual(members['hyperuuid-1.1.0-linux-arm64/include/hyperuuid.h'].mode, 0o644)

    def test_windows_is_a_zip_with_the_dll_and_import_library(self):
        out = Path(self.tmp.name) / 'out'
        c_package.pack(self.cfg, self.root, self.artifacts(), out)
        with zipfile.ZipFile(out / 'hyperuuid-1.1.0-win-x64.zip') as archive:
            names = archive.namelist()
        self.assertIn('hyperuuid-1.1.0-win-x64/bin/hyperuuid.dll', names)
        self.assertIn('hyperuuid-1.1.0-win-x64/lib/hyperuuid.dll.lib', names)

    def test_inputs_are_what_pack_reads(self):
        artifacts = self.artifacts()
        listed = c_package.inputs(self.cfg, artifacts)
        self.assertEqual(len(listed), 25)  # 15 static, 10 shared: wasi and Apple mobile have none
        self.assertEqual(sorted(listed), sorted(p for p in artifacts.rglob('*') if p.is_file()))

    def test_a_missing_input_stops_the_pack(self):
        artifacts = self.artifacts()
        (artifacts / 'native-android-x64/libhyperuuid.so').unlink()
        with self.assertRaisesRegex(ValueError, 'missing library: .*native-android-x64'):
            c_package.pack(self.cfg, self.root, artifacts, Path(self.tmp.name) / 'out')


class InventoryAndNotesTests(unittest.TestCase):
    def test_the_c_group_holds_exactly_the_archives_signed_at_b(self):
        inventory = load('release_inventory')
        cfg = hyper_config.HyperConfig(C)
        paths = ['nuget/hyperuuid.0.9.0.nupkg', 'nuget/hyperuuid.0.9.0.snupkg', 'crate/hyperuuid-0.9.0.crate',
                 'crate/r.json', 'crate/m.bin', 'maven/bundle.zip', 'ruby/hyperuuid-0.9.0.gem',
                 'ruby/hyperuuid-wasm-0.9.0.gem',
                 *(f'ruby/hyperuuid-0.9.0-{p}.gem' for p in cfg.ruby_platforms.values()),
                 *(f'pypi/hyperuuid-0.9.0-cp311-{i}.whl' for i in range(cfg.wheels))]
        archives = [f'c/{name}' for name in c_package.asset_names(cfg, '0.9.0')]
        inventory.validate_layout(paths + archives, 'hyperuuid', '0.9.0', cfg)
        for wrong in (paths, paths + archives[1:], paths + archives + ['c/extra.tar.gz'],
                      paths + [a.replace('0.9.0', '0.8.0') for a in archives]):
            with self.subTest(wrong=wrong[len(paths):][:2]), self.assertRaisesRegex(ValueError, 'C archives'):
                inventory.validate_layout(wrong, 'hyperuuid', '0.9.0', cfg)
        with self.assertRaisesRegex(ValueError, 'C archives'):
            inventory.validate_layout(paths + archives, 'hyperuuid', '0.9.0', hyper_config.HyperConfig(MINIMAL))
        self.assertTrue('c/x.tar.gz'.startswith(inventory.CONSUMER_SIGNED))
        self.assertFalse('c/x.tar.gz'.startswith(inventory.BUILT_AT_SOURCE))

    def test_the_notes_count_the_archives(self):
        notes = load('release_notes')
        files = [{'path': 'nuget/hyperuuid.0.9.0.nupkg', 'location': 'packages'},
                 {'path': 'go/staticlib/linux_amd64/libhyperuuid.a', 'location': 'source', 'signer_repo': 'x'},
                 {'path': 'c/hyperuuid-0.9.0-linux-x64.tar.gz', 'location': 'packages'},
                 {'path': 'c/hyperuuid-0.9.0-win-x64.zip', 'location': 'packages'}]
        inventory = dict(repository='SkunkWerkx/HyperUuid', version='0.9.0', source_sha='a' * 40, ci_run_id='42',
                         files=files)
        text = ' '.join(notes.verifying(inventory).split())
        self.assertIn('committed for Go, Swift and PHP, and 2 C archives, one per platform, attached below.', text)


if __name__ == '__main__':
    unittest.main()
