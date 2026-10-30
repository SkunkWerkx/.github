"""The Swift core as release assets (`swift_remote`): the manifest lines, the zips, the guard,
the inventory and the release notes."""
import importlib.util
import json
from pathlib import Path
import subprocess
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


swift_release = load('swift_release')
guard = load('release_guard')
hyper_config = load('hyper_config')
REMOTE = {**MINIMAL, 'swift_remote': True}
REPO = 'SkunkWerkx/HyperUuid'
MANIFEST = '''// swift-tools-version:6.2
import PackageDescription

let coreRelease = "0.8.0"
let coreChecksum = ""
let appleCoreChecksum = ""

let package = Package(name: "HyperUuid")
'''
CORE, APPLE = 'a' * 64, 'b' * 64


def complete_tree(root, cfg):
    """Every file the two zips must hold, each with its own path as content."""
    for files in (files for _, files in swift_release.expected_files(cfg).values()):
        for relative in files:
            path = Path(root) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)


class ManifestTests(unittest.TestCase):
    def test_write_sets_the_three_lines_and_read_returns_them(self):
        text = swift_release.write(MANIFEST, '0.9.0', CORE, APPLE)
        self.assertEqual(swift_release.read(text), {'version': '0.9.0', 'core': CORE, 'apple': APPLE})
        self.assertEqual(text.replace('0.9.0', '0.8.0').replace(CORE, '').replace(APPLE, ''), MANIFEST)

    def test_write_refuses_a_bad_version_or_checksum(self):
        for version, core in [('0.9', CORE), ('0.9.0', 'A' * 64), ('0.9.0', 'a' * 63), ('0.9.0', '')]:
            with self.subTest(version=version, core=core), self.assertRaises(ValueError):
                swift_release.write(MANIFEST, version, core, APPLE)

    def test_each_line_must_appear_exactly_once(self):
        for text in (MANIFEST.replace('let coreChecksum = ""\n', ''),
                     MANIFEST + 'let coreRelease = "0.8.0"\n'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'exactly one'):
                swift_release.read(text)

    def test_only_the_release_lines_may_differ(self):
        staged = swift_release.write(MANIFEST, '0.9.0', CORE, APPLE)
        self.assertTrue(swift_release.only_release_lines_differ(MANIFEST, staged))
        self.assertFalse(swift_release.only_release_lines_differ(MANIFEST, staged + '// more\n'))
        self.assertFalse(swift_release.only_release_lines_differ(
            MANIFEST, staged.replace('name: "HyperUuid"', 'name: "Other"')))


class ZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        write_config(self.root, REMOTE)
        self.cfg = hyper_config.load(self.root)
        complete_tree(self.root, self.cfg)

    def test_the_zips_hold_each_tree_under_its_own_name_and_are_deterministic(self):
        first = swift_release.build_zips(self.cfg, self.root, self.root / 'one')
        second = swift_release.build_zips(self.cfg, self.root, self.root / 'two')
        self.assertEqual([p.name for p in first], list(self.cfg.swift_assets))
        for a, b in zip(first, second):
            self.assertEqual(a.read_bytes(), b.read_bytes())
        with zipfile.ZipFile(first[0]) as archive:
            files = {name for name in archive.namelist() if not name.endswith('/')}
            _, expected = swift_release.expected_files(self.cfg)[self.cfg.swift_assets[0]]
            self.assertEqual(files, {path.removeprefix('swift/') for path in expected})
            self.assertIn('HyperUuidCore.artifactbundle/', archive.namelist())

    def test_a_missing_or_extra_file_refuses_the_zip(self):
        (self.root / self.cfg.bundle / 'wasm32-unknown-wasip1/libhyperuuid.a').unlink()
        with self.assertRaisesRegex(ValueError, 'missing .*wasm32'):
            swift_release.build_zips(self.cfg, self.root, self.root / 'out')
        complete_tree(self.root, self.cfg)
        (self.root / self.cfg.xcframework / 'stray.txt').write_text('stray')
        with self.assertRaisesRegex(ValueError, 'unexpected .*stray'):
            swift_release.build_zips(self.cfg, self.root, self.root / 'out')

    def test_stage_writes_the_crate_version_and_check_compares(self):
        assets = self.root / 'assets'
        swift_release.build_zips(self.cfg, self.root, assets)
        (self.root / 'rust').mkdir()
        (self.root / 'rust/Cargo.toml').write_text('version = "0.9.0"\n')
        (self.root / 'Package.swift').write_text(MANIFEST)
        swift_release.stage(self.cfg, self.root, assets)
        text = (self.root / 'Package.swift').read_text()
        named = swift_release.read(text)
        self.assertEqual(named['version'], '0.9.0')
        self.assertEqual(named['core'], swift_release.sha256(assets / self.cfg.swift_assets[0]))
        swift_release.check(self.cfg, text, '0.9.0', assets)
        with self.assertRaisesRegex(ValueError, 'names release'):
            swift_release.check(self.cfg, text, '0.9.1', assets)
        (assets / self.cfg.swift_assets[1]).write_bytes(b'other bytes')
        with self.assertRaisesRegex(ValueError, 'CoreApple.xcframework.zip'):
            swift_release.check(self.cfg, text, '0.9.0', assets)


class ConfigTests(unittest.TestCase):
    def test_swift_leaves_the_committed_payloads_and_package_swift_may_be_staged(self):
        cfg = hyper_config.HyperConfig(REMOTE)
        paths = cfg.payload_paths(Path('artifacts'))
        self.assertEqual(len(paths), 13)
        self.assertFalse(any(path.startswith('swift/') for path in paths))
        self.assertEqual(cfg.payload_trees, ('go/staticlib',))
        self.assertEqual(cfg.staged_edits, ('Package.swift',))
        self.assertEqual(cfg.swift_assets, ('HyperUuidCore.artifactbundle.zip', 'HyperUuidCoreApple.xcframework.zip'))
        self.assertFalse(any(path.startswith('swift/') for path in cfg.probe_payloads))
        shell = hyper_config.shell(cfg)
        self.assertIn('SWIFT_REMOTE=true', shell)
        self.assertIn('declare -A STATIC_TREES=([go/staticlib]=13)', shell)

    def test_off_by_default_and_must_be_a_boolean(self):
        cfg = hyper_config.HyperConfig(MINIMAL)
        self.assertIs(cfg.swift_remote, False)
        self.assertEqual((cfg.staged_edits, cfg.swift_assets), ((), ()))
        self.assertIn('SWIFT_REMOTE=false', hyper_config.shell(cfg))
        with self.assertRaisesRegex(ValueError, 'swift_remote'):
            hyper_config.HyperConfig({**MINIMAL, 'swift_remote': 'yes'})


class GuardTests(unittest.TestCase):
    """A -> B for a swift_remote consumer: B rewrites Package.swift's release lines."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        for key, value in [('commit.gpgsign', 'false'), ('user.name', 'Test'), ('user.email', 'test@example.com')]:
            self.git('config', key, value)
        write_config(self.root, REMOTE)
        self.cfg = guard.config(self.root)
        self.write('rust/Cargo.toml', 'version = "0.9.0"\n')
        self.write('CHANGELOG.md', '## [0.9.0]\n\n- A thing.\n')
        self.write('Package.swift', MANIFEST)
        self.commit('preparation')
        self.a = self.git('rev-parse', 'HEAD')
        self.b = self.stage(swift_release.write(MANIFEST, '0.9.0', CORE, APPLE))
        self.run = dict(id=42, head_sha=self.a, head_branch='master', path='.github/workflows/ci.yml',
                        event='workflow_dispatch', status='completed', conclusion='success',
                        repository={'full_name': REPO})

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True).strip()

    def write(self, path, value):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(value)

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)

    def stage(self, manifest):
        self.git('reset', '-q', '--hard', self.a)
        for path in guard.payload_paths(self.cfg, Path('artifacts')):
            self.write(path, 'verified bytes')
        self.write('Package.swift', manifest)
        self.commit(f'stage\n\nRelease-Source-SHA: {self.a}\nRelease-CI-Run-ID: 42')
        return self.git('rev-parse', 'HEAD')

    def gate(self, commit, tag='v0.9.0'):
        with patch.object(guard, 'api', return_value=self.run):
            return guard.release_record(self.root, self.cfg, commit, tag, '', REPO)

    def test_the_staged_release_lines_pass(self):
        self.assertEqual(self.gate(self.b), (self.a, '42'))

    def test_any_other_package_swift_change_is_refused(self):
        b = self.stage(swift_release.write(MANIFEST, '0.9.0', CORE, APPLE).replace('HyperUuid")', 'Other")'))
        with self.assertRaisesRegex(ValueError, 'beyond its release lines'):
            self.gate(b)

    def test_the_tag_needs_the_release_lines_to_name_its_version(self):
        b = self.stage(swift_release.write(MANIFEST, '0.8.9', CORE, APPLE))
        with self.assertRaisesRegex(ValueError, "names Swift release '0.8.9'"):
            self.gate(b)
        self.assertEqual(self.gate(b, tag=''), (self.a, '42'))

    def test_the_ci_zips_must_be_the_ones_the_manifest_names(self):
        assets = guard.swift_assets_dir(self.root)
        assets.mkdir(parents=True)
        for name in self.cfg.swift_assets:
            (assets / name).write_bytes(name.encode())
        core, apple = (swift_release.sha256(assets / name) for name in self.cfg.swift_assets)
        guard.check_swift_assets(self.root, self.cfg, swift_release.write(MANIFEST, '0.9.0', core, apple))
        with self.assertRaisesRegex(ValueError, 'CoreApple'):
            guard.check_swift_assets(self.root, self.cfg, swift_release.write(MANIFEST, '0.9.0', core, CORE))
        with patch.object(guard, 'verify_attestation') as verify:
            guard.check_swift_assets(self.root, self.cfg, swift_release.write(MANIFEST, '0.9.0', core, apple),
                                     sha=self.a, repo=REPO)
        self.assertEqual([call.args[0].name for call in verify.call_args_list], list(self.cfg.swift_assets))


class InventoryAndNotesTests(unittest.TestCase):
    def setUp(self):
        self.inventory = load('release_inventory')
        self.notes = load('release_notes')

    def test_the_github_group_holds_exactly_the_swift_zips(self):
        cfg = hyper_config.HyperConfig(REMOTE)
        groups = self.inventory.INVENTORY_GROUPS
        self.assertIn('github', groups)
        paths = ['nuget/hyperuuid.0.9.0.nupkg', 'nuget/hyperuuid.0.9.0.snupkg', 'crate/hyperuuid-0.9.0.crate',
                 'crate/r.json', 'crate/m.bin', 'maven/bundle.zip', 'ruby/hyperuuid-0.9.0.gem',
                 'ruby/hyperuuid-wasm-0.9.0.gem',
                 *(f'ruby/hyperuuid-0.9.0-{p}.gem' for p in cfg.ruby_platforms.values()),
                 *(f'pypi/hyperuuid-0.9.0-cp311-{i}.whl' for i in range(cfg.wheels)),
                 *(f'php/{name}' for name in self.inventory.php_asset_names('0.9.0', 'hyperuuid'))]
        assets = [f'github/{name}' for name in cfg.swift_assets]
        self.inventory.validate_layout(paths + assets, 'hyperuuid', '0.9.0', cfg)
        for wrong in (paths, paths + assets[:1], paths + assets + ['github/extra.zip']):
            with self.subTest(wrong=wrong[len(paths):]), self.assertRaisesRegex(ValueError, 'GitHub Release assets'):
                self.inventory.validate_layout(wrong, 'hyperuuid', '0.9.0', cfg)
        with self.assertRaisesRegex(ValueError, 'GitHub Release assets'):
            self.inventory.validate_layout(paths + assets, 'hyperuuid', '0.9.0', hyper_config.HyperConfig(MINIMAL))
        self.assertTrue('github/x.zip'.startswith(self.inventory.BUILT_AT_SOURCE))

    def test_the_notes_name_the_zips_and_commit_swift_nothing(self):
        files = [{'path': 'nuget/hyperuuid.0.9.0.nupkg', 'location': 'packages'},
                 {'path': 'go/staticlib/linux_amd64/libhyperuuid.a', 'location': 'source', 'signer_repo': 'x'},
                 {'path': 'github/HyperUuidCore.artifactbundle.zip', 'location': 'packages'},
                 {'path': 'github/HyperUuidCoreApple.xcframework.zip', 'location': 'packages'}]
        inventory = dict(repository=REPO, version='0.9.0', source_sha='a' * 40, ci_run_id='42', files=files)
        text = ' '.join(self.notes.verifying(inventory).split())
        self.assertIn('the 1 native libraries committed for Go, and the Swift core\'s '
                      '`HyperUuidCore.artifactbundle.zip` and `HyperUuidCoreApple.xcframework.zip`, attached below. '
                      'Its predicate', text)
        self.assertIn('The native libraries, the Swift zips, the Python wheels', text)
        self.assertNotIn('metadata files', text)


if __name__ == '__main__':
    unittest.main()
