"""The consumer description every Forge script and action reads."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hyper_config', SCRIPTS / 'hyper_config.py')
hyper_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hyper_config)

MINIMAL = {'crate': 'hyperuuid', 'project': 'HyperUuid',
           'exports': {'sources': ['rust/src/ffi.rs'], 'patterns': [r'pub extern "C" fn (\w+)'],
                       'prefixes': ['uuid_', 'hyperuuid_']}}


def write_config(root, data=MINIMAL):
    path = Path(root) / hyper_config.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


class HyperConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_defaults_derive_every_layout_name_from_crate_and_project(self):
        cfg = hyper_config.HyperConfig(MINIMAL)
        self.assertEqual(cfg.lib_name('linux-x64'), 'libhyperuuid.so')
        self.assertEqual(cfg.lib_name('osx-arm64'), 'libhyperuuid.dylib')
        self.assertEqual(cfg.lib_name('win-arm64'), 'hyperuuid.dll')
        self.assertNotIn('win-arm64', cfg.php_libs)
        self.assertEqual(len(cfg.php_libs), 7)
        self.assertEqual(cfg.bundle, 'swift/HyperUuidCore.artifactbundle')
        self.assertEqual(cfg.xcframework, 'swift/HyperUuidCoreApple.xcframework')
        self.assertEqual(cfg.version_env, 'HYPERUUID_VERSION')
        self.assertEqual(cfg.wheels, 25)
        self.assertEqual(cfg.ruby_abis, ('3.4', '4.0'))
        self.assertEqual(len(cfg.ruby_platforms), 7)
        paths = cfg.payload_paths(Path('artifacts'))
        # 7 PHP libraries, 13 Go and 11 Swift archives, info.json, Info.plist and three
        # slices of archive + header + module map.
        self.assertEqual(len(paths), 7 + 13 + 11 + 2 + 3 * 3)
        self.assertEqual(paths['go/staticlib/wasm/libhyperuuid.a'],
                         Path('artifacts/static-libs/go/staticlib/wasm/libhyperuuid.a'))
        self.assertEqual(paths['swift/HyperUuidCore.artifactbundle/x86_64-unknown-windows-msvc/hyperuuid.lib'],
                         Path('artifacts/static-libs/swift/HyperUuidCore.artifactbundle/x86_64-unknown-windows-msvc/hyperuuid.lib'))
        self.assertEqual(paths['php/src/native/win-x64/hyperuuid.dll'],
                         Path('artifacts/native/native-win-x64/hyperuuid.dll'))
        self.assertIn('swift/HyperUuidCore.artifactbundle/include/hyperuuid.h', cfg.source_paths)
        self.assertEqual(set(cfg.version_files), {
            'rust/Cargo.toml', 'csharp/HyperUuid/HyperUuid.csproj', 'java/build.gradle.kts',
            'ruby/hyperuuid.gemspec', 'ruby/lib/hyperuuid.rb'})

    def test_overrides_and_validation(self):
        data = {**MINIMAL, 'wheels': 9, 'ruby': {'abis': ['4.0']}, 'cdylib_smoke': '.github/scripts/cdylib_smoke.py'}
        cfg = hyper_config.HyperConfig(data)
        self.assertEqual((cfg.wheels, cfg.ruby_abis, cfg.cdylib_smoke), (9, ('4.0',), '.github/scripts/cdylib_smoke.py'))
        for broken in [{k: v for k, v in MINIMAL.items() if k != 'exports'},
                       {**MINIMAL, 'crate': 'HyperUuid'}, {**MINIMAL, 'project': 'hyperuuid'},
                       {**MINIMAL, 'exports': {**MINIMAL['exports'], 'prefixes': []}}]:
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                hyper_config.HyperConfig(broken)
        with self.assertRaisesRegex(ValueError, 'missing'):
            hyper_config.load(self.root)

    def test_core_exports_reads_plain_and_table_patterns_and_rejects_nonsense(self):
        write_config(self.root, {**MINIMAL, 'exports': {
            'sources': ['rust/src/ffi.rs', 'rust/src/abi.rs'],
            'patterns': [r'pub extern "C" fn (\w+)',
                         {'block': r'numeric_exports! \{(.*?)\n\}', 'name': r'(\w+) => \('}],
            'exclude': ['$export'], 'prefixes': ['uuid_', 'hyperuuid_']}})
        (self.root / 'rust/src').mkdir(parents=True)
        (self.root / 'rust/src/ffi.rs').write_text(
            'pub extern "C" fn uuid_new_v7() {}\nnumeric_exports! {\n    uuid_a => (1),\n    uuid_b => (2),\n}\n')
        (self.root / 'rust/src/abi.rs').write_text('pub extern "C" fn hyperuuid_version() -> u32 { 0 }\n')
        cfg = hyper_config.load(self.root)
        self.assertEqual(cfg.core_exports(), {'uuid_new_v7', 'uuid_a', 'uuid_b', 'hyperuuid_version'})
        self.assertTrue(cfg.is_export_symbol('uuid_x'))
        self.assertFalse(cfg.is_export_symbol('rust_eh_personality'))
        (self.root / 'rust/src/abi.rs').write_text('')
        with self.assertRaisesRegex(ValueError, 'export set'):
            cfg.core_exports()

    def test_shell_output_is_bash_that_bash_accepts(self):
        write_config(self.root)
        script = subprocess.check_output([sys.executable, str(SCRIPTS / 'hyper_config.py'), 'shell',
                                          '--root', str(self.root)], text=True)
        probe = script + 'echo "$CRATE ${LIB_NAMES[osx-x64]} ${PHP_LIBS[win-x64]} ${RUBY_PLATFORMS[linux-x64]} ${#RUBY_ABIS[@]} ${STATIC_TREES[go/staticlib]} $WHEELS ${PHP_LIBS[win-arm64]:-none}"\n'
        out = subprocess.check_output(['bash', '-euo', 'pipefail', '-c', probe], text=True).strip()
        self.assertEqual(out, 'hyperuuid libhyperuuid.dylib hyperuuid.dll x86_64-linux-gnu 2 13 25 none')
        lines = subprocess.check_output([sys.executable, str(SCRIPTS / 'hyper_config.py'), 'version-files',
                                         '--root', str(self.root)], text=True).splitlines()
        self.assertEqual(lines[0].split('\t')[0], 'rust/Cargo.toml')
        self.assertEqual(len(lines), 5)


if __name__ == '__main__':
    unittest.main()
