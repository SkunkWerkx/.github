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

    def test_sdist_is_off_by_default_and_reaches_shell_and_json(self):
        self.assertIs(hyper_config.HyperConfig(MINIMAL).sdist, False)
        self.assertIs(hyper_config.HyperConfig({**MINIMAL, 'sdist': True}).sdist, True)
        write_config(self.root, {**MINIMAL, 'sdist': True})
        shell = subprocess.run(['bash', '-c', f'eval "$(python3 {SCRIPTS / "hyper_config.py"} shell --root {self.root})"; echo $SDIST'],
                               capture_output=True, text=True, check=True)
        self.assertEqual(shell.stdout.strip(), 'true')
        data = json.loads(subprocess.run([sys.executable, str(SCRIPTS / 'hyper_config.py'), 'json', '--root', str(self.root)],
                                         capture_output=True, text=True, check=True).stdout)
        self.assertIs(data['sdist'], True)
        write_config(self.root)
        off = subprocess.run(['bash', '-c', f'eval "$(python3 {SCRIPTS / "hyper_config.py"} shell --root {self.root})"; echo $SDIST'],
                             capture_output=True, text=True, check=True)
        self.assertEqual(off.stdout.strip(), 'false')

    def test_wasm_toolchain_defaults_to_stable_and_reaches_shell_and_json(self):
        self.assertEqual(hyper_config.HyperConfig(MINIMAL).wasm_toolchain, 'stable')
        pinned = {**MINIMAL, 'wasm': {'toolchain': 'nightly-2026-10-27'}}
        self.assertEqual(hyper_config.HyperConfig(pinned).wasm_toolchain, 'nightly-2026-10-27')
        write_config(self.root, pinned)
        shell = subprocess.run(['bash', '-c', f'eval "$(python3 {SCRIPTS / "hyper_config.py"} shell --root {self.root})"; echo $WASM_TOOLCHAIN'],
                               capture_output=True, text=True, check=True)
        self.assertEqual(shell.stdout.strip(), 'nightly-2026-10-27')
        data = json.loads(subprocess.run([sys.executable, str(SCRIPTS / 'hyper_config.py'), 'json', '--root', str(self.root)],
                                         capture_output=True, text=True, check=True).stdout)
        self.assertEqual(data['wasm_toolchain'], 'nightly-2026-10-27')

    def test_wasm_toolchain_must_be_stable_or_a_dated_nightly(self):
        for bad in ('nightly', 'beta', 'nightly-2026-1-2', 1, None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'wasm.toolchain must be stable or nightly-YYYY-MM-DD'):
                hyper_config.HyperConfig({**MINIMAL, 'wasm': {'toolchain': bad}})

    def test_wasm_must_be_an_object_holding_only_toolchain(self):
        for bad in (None, 'nightly-2026-10-27', ['toolchain'], {'toolchian': 'nightly-2026-10-27'}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'wasm must be an object with only a toolchain key'):
                hyper_config.HyperConfig({**MINIMAL, 'wasm': bad})

    def test_wasm_toolchain_date_digits_are_ascii(self):
        with self.assertRaisesRegex(ValueError, 'wasm.toolchain must be stable or nightly-YYYY-MM-DD'):
            hyper_config.HyperConfig({**MINIMAL, 'wasm': {'toolchain': 'nightly-\u0662026-10-27'}})

    def test_sdist_must_be_a_boolean(self):
        for bad in ('false', 1, None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'sdist must be true or false'):
                hyper_config.HyperConfig({**MINIMAL, 'sdist': bad})

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
        self.assertEqual(cfg.wheels, 17)
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
        self.assertEqual(out, 'hyperuuid libhyperuuid.dylib hyperuuid.dll x86_64-linux-gnu 2 13 17 none')
        lines = subprocess.check_output([sys.executable, str(SCRIPTS / 'hyper_config.py'), 'version-files',
                                         '--root', str(self.root)], text=True).splitlines()
        self.assertEqual(lines[0].split('\t')[0], 'rust/Cargo.toml')
        self.assertEqual(len(lines), 5)

    CLASSIC = ['rust/Cargo.toml', 'csharp/HyperUuid/HyperUuid.csproj', 'java/build.gradle.kts',
               'ruby/hyperuuid.gemspec', 'ruby/lib/hyperuuid.rb']

    def write_classic(self):
        contents = {'rust/Cargo.toml': 'version = "1.0.0"\n',
                    'csharp/HyperUuid/HyperUuid.csproj': '<Version>1.0.0</Version>\n',
                    'java/build.gradle.kts': 'version = System.getenv("HYPERUUID_VERSION") ?: "1.0.0"\n',
                    'ruby/hyperuuid.gemspec': 'spec.version = "1.0.0"\n',
                    'ruby/lib/hyperuuid.rb': 'VERSION = "1.0.0"\n'}
        for relative, text in contents.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def test_version_files_follow_the_binding_directories(self):
        write_config(self.root)
        self.assertEqual(list(hyper_config.load(self.root).version_files), self.CLASSIC)
        (self.root / 'typescript').mkdir()
        (self.root / 'r').mkdir()
        files = hyper_config.load(self.root).version_files
        self.assertEqual(list(files), self.CLASSIC + ['typescript/package.json', 'r/DESCRIPTION'])
        self.assertEqual(files['typescript/package.json'], r'^  "version": "([^"]+)",$')
        self.assertEqual(files['r/DESCRIPTION'], r'^Version: (.+)$')
        # Each directory is independent.
        (self.root / 'r').rmdir()
        self.assertEqual(list(hyper_config.load(self.root).version_files),
                         self.CLASSIC + ['typescript/package.json'])

    def test_bump_ignores_bindings_a_consumer_lacks(self):
        write_config(self.root)
        self.write_classic()
        cfg = hyper_config.load(self.root)
        changed = hyper_config.bump(cfg, self.root, '1.1.0')
        self.assertEqual(sorted(changed), sorted(self.CLASSIC))

    def test_bump_fails_loudly_when_a_binding_lost_its_manifest(self):
        write_config(self.root)
        self.write_classic()
        (self.root / 'r').mkdir()
        cfg = hyper_config.load(self.root)
        with self.assertRaises(FileNotFoundError):
            hyper_config.bump(cfg, self.root, '1.1.0')

    def test_node_platforms_in_shell_and_json(self):
        cfg = hyper_config.HyperConfig(MINIMAL)
        self.assertEqual(cfg.node_platforms, hyper_config.NODE_PLATFORMS)
        self.assertEqual(len(cfg.node_platforms), 8)
        self.assertEqual(cfg.node_platforms['linux-musl-arm64'], 'linux-arm64-musl')
        override = hyper_config.HyperConfig({**MINIMAL, 'node': {'platforms': {'linux-x64': 'linux-x64-gnu'}}})
        self.assertEqual(override.node_platforms, {'linux-x64': 'linux-x64-gnu'})
        write_config(self.root)
        script = subprocess.check_output([sys.executable, str(SCRIPTS / 'hyper_config.py'), 'shell',
                                          '--root', str(self.root)], text=True)
        self.assertIn('declare -A NODE_PLATFORMS=([linux-arm64]=linux-arm64-gnu ', script)
        out = subprocess.check_output(
            ['bash', '-euo', 'pipefail', '-c', script + 'echo "${NODE_PLATFORMS[win-x64]} ${#NODE_PLATFORMS[@]}"'],
            text=True).strip()
        self.assertEqual(out, 'win32-x64-msvc 8')
        data = json.loads(subprocess.check_output([sys.executable, str(SCRIPTS / 'hyper_config.py'), 'json',
                                                   '--root', str(self.root)], text=True))
        self.assertEqual(data['node_platforms'], hyper_config.NODE_PLATFORMS)

    def test_jni_lib_names(self):
        cfg = hyper_config.HyperConfig(MINIMAL)
        names = cfg.jni_lib_names
        self.assertEqual(sorted(names), sorted(hyper_config.RIDS))
        self.assertEqual(len(names), 8)
        crate = cfg.crate
        self.assertEqual(cfg.jni_lib_name('win-arm64'), f'{crate}_jni.dll')
        self.assertEqual(cfg.jni_lib_name('osx-x64'), f'lib{crate}_jni.dylib')
        self.assertEqual(cfg.jni_lib_name('linux-musl-arm64'), f'lib{crate}_jni.so')
        write_config(self.root)
        script = subprocess.check_output([sys.executable, str(SCRIPTS / 'hyper_config.py'), 'shell',
                                          '--root', str(self.root)], text=True)
        self.assertIn('declare -A JNI_LIB_NAMES=(', script)
        out = subprocess.check_output(
            ['bash', '-euo', 'pipefail', '-c',
             script + 'echo "${JNI_LIB_NAMES[win-x64]} ${JNI_LIB_NAMES[osx-arm64]} ${#JNI_LIB_NAMES[@]}"'],
            text=True).strip()
        self.assertEqual(out, f'{crate}_jni.dll lib{crate}_jni.dylib 8')


if __name__ == '__main__':
    unittest.main()
