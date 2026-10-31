"""npm_package.py: the one body that packs the npm tarballs, for the pack-npm action and the
consumer's own CI alike."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

from hyper_config import NODE_PLATFORMS
import npm_package
from release_package_metadata import WASI_RUNTIME, npm_all_names, npm_filename
from test_package_inventory_metadata import NODE_SUFFIXES, NODE_WASI, WASI_RUNTIME_PINS, write_npm, write_tar

ENGINES = '^22.13.0 || >=23.5.0'
WASI_FILES = (f'hypercast.{NODE_WASI}.wasm', 'hypercast.wasip1.cjs', 'hypercast.wasip1.d.cts',
              'hypercast.wasip1-browser.js', 'hypercast.wasip1-deferred.js', 'hypercast.wasip1-deferred.d.ts')
CFG = SimpleNamespace(crate='hypercast', node_platforms=NODE_PLATFORMS, node_wasi=NODE_WASI)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


def read_json(path):
    return json.loads(path.read_text())


class FakeNpm:
    """Records every command and plays the parts of `npm ci`, `napi create-npm-dirs`, `npm pack`
    and `npm pkg set` on the staged copy, so the pack body runs without Node."""

    def __init__(self, loaders_required=True):
        self.commands = []
        self.loaders_required = loaders_required

    def __call__(self, cmd, cwd):
        cwd = Path(cwd)
        self.commands.append((tuple(cmd), cwd))
        tool = Path(cmd[0]).name
        if tool.startswith('napi') and cmd[1] == 'create-npm-dirs':
            self.create_npm_dirs(cwd)
        elif tool.startswith('npm') and cmd[1:3] == ['pkg', 'set']:
            manifest = read_json(cwd / 'package.json')
            for assignment in cmd[3:]:
                key, value = assignment.split('=', 1)
                section, name = key.split('.', 1)
                manifest.setdefault(section, {})[name] = value
            write_json(cwd / 'package.json', manifest)
        elif tool.startswith('npm') and cmd[1] == 'pack':
            out = Path(cmd[cmd.index('--pack-destination') + 1])
            manifest = read_json(cwd / 'package.json')
            entries = [('package/package.json', (cwd / 'package.json').read_bytes())]
            for name in manifest.get('files', []):
                if (cwd / name).is_file():
                    entries.append((f'package/{name}', (cwd / name).read_bytes()))
            write_tar(out / npm_filename(manifest['name'], manifest['version']), entries)
        elif tool.startswith('npm') and cmd[1] == 'ci':
            pass
        else:
            raise AssertionError(f'unexpected command {cmd}')

    def create_npm_dirs(self, cwd):
        root = read_json(cwd / 'package.json')
        scope = root['napi']['packageName']
        for suffix in NODE_SUFFIXES:
            write_json(cwd / 'npm' / suffix / 'package.json', dict(
                name=f'{scope}-{suffix}', version=root['version'], main=f'hypercast.{suffix}.node',
                files=[f'hypercast.{suffix}.node'], os=['linux'], cpu=['x64'], engines=root['engines']))
        if self.loaders_required and not (cwd / 'hypercast.wasip1.cjs').is_file():
            raise AssertionError('create-npm-dirs found no WASI loader in the package root')
        write_json(cwd / 'npm' / NODE_WASI / 'package.json', dict(
            name=f'{scope}-{NODE_WASI}', version=root['version'], main='hypercast.wasip1.cjs',
            files=[*WASI_FILES, f'hypercast.{NODE_WASI}.wasm.d.ts'], engines=root['engines'],
            dependencies={'@napi-rs/wasm-runtime': '~1.0.0', '@emnapi/core': '1.5.0', '@emnapi/runtime': '1.5.0'}))
        (cwd / 'npm' / NODE_WASI / f'hypercast.{NODE_WASI}.wasm.d.ts').write_text('export {}\n')


class NpmPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.package = self.tmp / 'typescript'
        write_json(self.package / 'package.json', dict(
            name='hypercast', version='0.10.0', files=['index.js'], engines={'node': ENGINES},
            napi=dict(binaryName='hypercast', packageName='@skunkwerkx/hypercast'),
            devDependencies={'@napi-rs/cli': '3.10.9'}))
        (self.package / 'index.js').write_text('module.exports = {}\n')
        write_json(self.package / 'package-lock.json', dict(packages={
            'node_modules/@napi-rs/wasm-runtime': {'version': '1.0.0'},
            'node_modules/@emnapi/core': {'version': '1.5.0'},
            'node_modules/@emnapi/runtime': {'version': '1.5.0'}}))
        self.artifacts = self.tmp / 'artifacts'
        for rid, suffix in NODE_PLATFORMS.items():
            path = self.artifacts / f'node-{rid}' / f'hypercast.{suffix}.node'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'native ' + suffix.encode())
        self.wasi_artifact = self.artifacts / f'node-{NODE_WASI}'
        self.wasi_artifact.mkdir()
        for name in WASI_FILES:
            (self.wasi_artifact / name).write_bytes(b'\0asm' if name.endswith('.wasm') else b'// loader\n')
        self.out = self.tmp / 'out'

    def test_lockfile_versions_reads_the_pins_and_misses_loudly(self):
        self.assertEqual(npm_package.lockfile_versions(self.package, WASI_RUNTIME), WASI_RUNTIME_PINS)
        with self.assertRaisesRegex(ValueError, 'left-pad'):
            npm_package.lockfile_versions(self.package, ('left-pad',))

    def test_pin_wasi_dependencies_makes_every_runtime_exact_and_refuses_strangers(self):
        wasi_dir = self.tmp / 'wasi'
        write_json(wasi_dir / 'package.json', dict(name='x', version='0.10.0', dependencies={
            '@napi-rs/wasm-runtime': '~1.2.3', '@emnapi/core': '^1.5.0', '@emnapi/runtime': 'latest'}))
        npm_package.pin_wasi_dependencies(wasi_dir, {'@napi-rs/wasm-runtime': '1.2.3', '@emnapi/core': '1.5.0',
                                                     '@emnapi/runtime': '1.5.0'})
        self.assertEqual(read_json(wasi_dir / 'package.json')['dependencies'],
                         {'@napi-rs/wasm-runtime': '1.2.3', '@emnapi/core': '1.5.0', '@emnapi/runtime': '1.5.0'})
        write_json(wasi_dir / 'package.json', dict(name='x', version='0.10.0', dependencies={'tslib': '2.0.0'}))
        with self.assertRaisesRegex(ValueError, 'tslib'):
            npm_package.pin_wasi_dependencies(wasi_dir, WASI_RUNTIME_PINS)

    def test_check_wasi_manifest_refuses_platform_binding_and_another_floor(self):
        wasi_dir = self.tmp / 'wasi'
        good = dict(name='x', version='0.10.0', engines={'node': ENGINES})
        write_json(wasi_dir / 'package.json', good)
        npm_package.check_wasi_manifest(wasi_dir)
        for label, bad in (('cpu', {**good, 'cpu': ['wasm32']}), ('os', {**good, 'os': ['linux']}),
                           ('libc', {**good, 'libc': ['glibc']}), ('engines', {**good, 'engines': {'node': '>=20'}}),
                           ('no engines', {k: v for k, v in good.items() if k != 'engines'})):
            with self.subTest(label):
                write_json(wasi_dir / 'package.json', bad)
                with self.assertRaises(ValueError):
                    npm_package.check_wasi_manifest(wasi_dir)

    def test_fill_wasi_dir_copies_the_artifact_and_wants_every_listed_file(self):
        work = self.tmp / 'work'
        wasi_dir = work / 'npm' / NODE_WASI
        write_json(wasi_dir / 'package.json', dict(name='x', version='0.10.0', files=list(WASI_FILES)))
        (self.wasi_artifact / f'hypercast.{NODE_WASI}.debug.wasm').write_bytes(b'\0asm debug')
        self.assertEqual(npm_package.fill_wasi_dir(work, self.wasi_artifact, 'hypercast', NODE_WASI), wasi_dir)
        self.assertEqual(sorted(p.name for p in wasi_dir.iterdir()), sorted([*WASI_FILES, 'package.json']),
                         'the debug module stays out')
        (self.wasi_artifact / 'hypercast.wasip1.cjs').unlink()
        shutil.rmtree(wasi_dir)
        write_json(wasi_dir / 'package.json', dict(name='x', version='0.10.0', files=list(WASI_FILES)))
        with self.assertRaisesRegex(ValueError, 'hypercast.wasip1.cjs'):
            npm_package.fill_wasi_dir(work, self.wasi_artifact, 'hypercast', NODE_WASI)

    def test_stage_copies_and_refuses_a_manifest_already_carrying_dependencies(self):
        fake = FakeNpm()
        work = self.tmp / 'work'
        npm_package.stage(self.package, work, runner=fake)
        self.assertTrue((work / 'index.js').is_file())
        self.assertEqual(fake.commands[0][0][1:], ('ci', '--ignore-scripts'))
        self.assertEqual(fake.commands[0][1], work)
        manifest = read_json(self.package / 'package.json')
        for label, bad in (('dependencies', {**manifest, 'dependencies': {'left-pad': '1.0.0'}}),
                           ('optionalDependencies', {**manifest, 'optionalDependencies': {'x': '1'}}),
                           ('packageName', {**manifest, 'napi': {**manifest['napi'], 'packageName': 'hypercast'}})):
            with self.subTest(label):
                write_json(self.package / 'package.json', bad)
                shutil.rmtree(work, ignore_errors=True)
                with self.assertRaisesRegex(ValueError, label):
                    npm_package.stage(self.package, work, runner=FakeNpm())

    def test_check_tarballs_wants_exactly_the_ten(self):
        write_npm(self.tmp, 'hypercast', '0.10.0')
        paths = sorted((self.tmp / 'npm').iterdir())
        self.assertEqual(len(paths), 10)
        npm_package.check_tarballs(paths, 'hypercast', '0.10.0', CFG, WASI_RUNTIME_PINS)
        wasi = next(p for p in paths if NODE_WASI in p.name)
        native = next(p for p in paths if NODE_SUFFIXES[0] in p.name)
        with self.assertRaisesRegex(ValueError, 'wasi'):
            npm_package.check_tarballs([p for p in paths if p != wasi], 'hypercast', '0.10.0', CFG, WASI_RUNTIME_PINS)
        with self.assertRaisesRegex(ValueError, NODE_SUFFIXES[0]):
            npm_package.check_tarballs([p for p in paths if p != native], 'hypercast', '0.10.0', CFG, WASI_RUNTIME_PINS)
        npm_package.check_tarballs([p for p in paths if p != native], 'hypercast', '0.10.0', CFG, WASI_RUNTIME_PINS,
                                   allow_missing_natives=True)
        with self.assertRaisesRegex(ValueError, 'wasi'):
            npm_package.check_tarballs([p for p in paths if p != wasi], 'hypercast', '0.10.0', CFG, WASI_RUNTIME_PINS,
                                       allow_missing_natives=True)
        extra = self.tmp / 'npm' / 'skunkwerkx-hypercast-linux-x64-gnu-0.10.0.copy.tgz'
        shutil.copy(native, extra)
        with self.assertRaises(ValueError):
            npm_package.check_tarballs([*paths, extra], 'hypercast', '0.10.0', CFG, WASI_RUNTIME_PINS)
        with self.assertRaisesRegex(ValueError, 'differ'):
            npm_package.check_tarballs(paths, 'hypercast', '0.10.0', CFG, {**WASI_RUNTIME_PINS, '@emnapi/core': '1.5.1'})

    def test_pack_wasi_cli_stages_creates_dirs_and_packs_the_wasi_dir_only(self):
        fake = FakeNpm()
        code = npm_package.main(['pack-wasi', '--package', str(self.package), '--artifact', str(self.wasi_artifact),
                                 '--out', str(self.out), '--root', str(self.tmp), '--work', str(self.tmp / 'work')],
                                runner=fake, cfg=CFG)
        self.assertEqual(code, 0)
        verbs = [(Path(cmd[0]).name.split('.')[0], *cmd[1:2]) for cmd, _ in fake.commands]
        self.assertEqual(verbs, [('npm', 'ci'), ('napi', 'create-npm-dirs'), ('npm', 'pack')])
        pack_cmd, pack_cwd = fake.commands[2]
        self.assertEqual(pack_cwd.name, NODE_WASI)
        self.assertIn('--ignore-scripts', pack_cmd)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [f'skunkwerkx-hypercast-{NODE_WASI}-0.10.0.tgz'])
        manifest = read_json(pack_cwd / 'package.json')
        self.assertEqual(manifest['dependencies'], WASI_RUNTIME_PINS, 'pinned from the lockfile, not the registry')
        self.assertEqual(read_json(self.package / 'package.json').get('dependencies'), None, 'the checkout is untouched')

    def test_pack_cli_packs_natives_then_wasi_then_the_root_with_both_pinned(self):
        fake = FakeNpm()
        code = npm_package.main(['pack', '--package', str(self.package), '--artifacts', str(self.artifacts),
                                 '--out', str(self.out), '--root', str(self.tmp), '--work', str(self.tmp / 'work')],
                                runner=fake, cfg=CFG)
        self.assertEqual(code, 0)
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, sorted(npm_filename(n, '0.10.0') for n in npm_all_names('hypercast', NODE_SUFFIXES, NODE_WASI)))
        packs = [cwd for cmd, cwd in fake.commands if cmd[1] == 'pack']
        self.assertEqual(len(packs), 10)
        self.assertEqual(packs[-1].name, 'work', 'the root is packed last')
        self.assertEqual(packs[-2].name, NODE_WASI)
        sets = [cmd for cmd, _ in fake.commands if cmd[1:3] == ('pkg', 'set')]
        self.assertEqual(len(sets), 1)
        root = read_json(packs[-1] / 'package.json')
        self.assertEqual(root['optionalDependencies'], {f'@skunkwerkx/hypercast-{s}': '0.10.0' for s in NODE_SUFFIXES})
        self.assertEqual(root['dependencies'], {f'@skunkwerkx/hypercast-{NODE_WASI}': '0.10.0'})
        self.assertNotIn('dependencies', read_json(self.package / 'package.json'))

    def test_pack_cli_refuses_a_missing_native_unless_allowed(self):
        shutil.rmtree(self.artifacts / 'node-linux-arm64')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = npm_package.main(['pack', '--package', str(self.package), '--artifacts', str(self.artifacts),
                                     '--out', str(self.out), '--root', str(self.tmp)], runner=FakeNpm(), cfg=CFG)
        self.assertEqual(code, 1)
        self.assertIn('linux-arm64', err.getvalue())
        self.assertFalse(self.out.exists() and any(self.out.iterdir()), 'nothing half-packed is left')
        code = npm_package.main(['pack', '--package', str(self.package), '--artifacts', str(self.artifacts),
                                 '--out', str(self.out), '--root', str(self.tmp), '--allow-missing-natives'],
                                runner=FakeNpm(), cfg=CFG)
        self.assertEqual(code, 0)
        self.assertEqual(len(list(self.out.iterdir())), 9)
        root_tgz = self.out / 'hypercast-0.10.0.tgz'
        with tarfile.open(root_tgz) as archive:
            manifest = json.loads(archive.extractfile('package/package.json').read())
        self.assertEqual(len(manifest['optionalDependencies']), 8, 'the root still pins all eight')

    def test_pack_cli_skips_named_platforms_in_pr_mode_and_refuses_unknown_or_unlisted_gaps(self):
        # PR-mode CI has no osx-x64 build (hyper-build-native skips it), so the pack takes the
        # rids to leave out by name, as c_package.py does; anything else missing still fails.
        shutil.rmtree(self.artifacts / 'node-osx-x64')
        code = npm_package.main(['pack', '--package', str(self.package), '--artifacts', str(self.artifacts),
                                 '--out', str(self.out), '--root', str(self.tmp), '--skip', 'osx-x64'],
                                runner=FakeNpm(), cfg=CFG)
        self.assertEqual(code, 0)
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(len(names), 9)
        self.assertNotIn(npm_filename('@skunkwerkx/hypercast-darwin-x64', '0.10.0'), names)
        with tarfile.open(self.out / 'hypercast-0.10.0.tgz') as archive:
            manifest = json.loads(archive.extractfile('package/package.json').read())
        self.assertEqual(len(manifest['optionalDependencies']), 8, 'the root still pins all eight')
        shutil.rmtree(self.out)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = npm_package.main(['pack', '--package', str(self.package), '--artifacts', str(self.artifacts),
                                     '--out', str(self.out), '--root', str(self.tmp), '--skip', 'bogus-rid'],
                                    runner=FakeNpm(), cfg=CFG)
        self.assertEqual(code, 1)
        self.assertIn('bogus-rid', err.getvalue())
        shutil.rmtree(self.artifacts / 'node-linux-arm64')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = npm_package.main(['pack', '--package', str(self.package), '--artifacts', str(self.artifacts),
                                     '--out', str(self.out), '--root', str(self.tmp), '--skip', 'osx-x64'],
                                    runner=FakeNpm(), cfg=CFG)
        self.assertEqual(code, 1, 'a gap outside the skip list still fails')
        self.assertIn('linux-arm64', err.getvalue())


if __name__ == '__main__':
    unittest.main()


class PackDestinationTest(unittest.TestCase):
    """`npm pack` runs inside the staged package directory under a temporary work tree, so a
    relative `--out` has to reach it absolute, or the tarball lands in the scratch and the
    caller's `npm-wasi/*.tgz` is empty (HyperUuid run 41225202643, build-node-wasi)."""

    def test_pack_dir_hands_npm_an_absolute_destination_from_a_relative_out(self):
        import os
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        package = tmp / 'staged'
        write_json(package / 'package.json', {'name': '@skunkwerkx/hypercast-wasm32-wasip1', 'version': '0.10.0', 'files': []})
        fake = FakeNpm()
        before = os.getcwd()
        os.chdir(tmp)
        try:
            path = npm_package.pack_dir(package, 'npm-wasi', runner=fake)
        finally:
            os.chdir(before)
        (pack_cmd, pack_cwd), = fake.commands
        destination = Path(pack_cmd[pack_cmd.index('--pack-destination') + 1])
        self.assertEqual(pack_cwd, package, 'npm pack runs in the staged package directory')
        self.assertTrue(destination.is_absolute(), f'npm would resolve {destination} against {pack_cwd}')
        self.assertEqual(destination, (tmp / 'npm-wasi').resolve())
        self.assertEqual(path, (tmp / 'npm-wasi').resolve() / 'skunkwerkx-hypercast-wasm32-wasip1-0.10.0.tgz')
