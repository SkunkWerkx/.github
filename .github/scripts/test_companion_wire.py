"""companion_wire.py: each ecosystem's override, written into the leg's own tree (the
companion-pins spec, "The mapping"), against HyperTabular's own manifests (fixtures/wire/)."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

import companion_fetch
import companion_wire

SCRIPTS = Path(__file__).resolve().parent
FIXTURES = SCRIPTS / 'fixtures' / 'wire'
MODULE = 'github.com/SkunkWerkx/HyperCast/go'
ENV = {'HYPERCAST_SOURCE': '.companions/hypercast', 'HYPERCAST_ARTIFACTS': '.companions/hypercast-artifacts',
       'HYPERCAST_COMMIT': 'c' * 40, 'HYPERCAST_VERSION': '1.0.0',
       'HYPERCAST_RUST_REQ': '.companions/hypercast/rust', 'HYPERCAST_GO_REQ': '.companions/hypercast/go'}
# The pinned Python files as fetch-companions stages them for an osx-arm64 leg (the wheels) and
# the sdist legs (the sdist), and the Pyodide wheel; the names are HyperCast 14df722's, as
# fixtures/origin/python/ recorded them.
WHEELS = '.companions/hypercast-artifacts/pypi-wheels-osx-arm64'
ABI3 = f'{WHEELS}/hypercast-1.0.0-cp311-abi3-macosx_11_0_arm64.whl'
ABI3T = f'{WHEELS}/hypercast-1.0.0-cp315-abi3.abi3t-macosx_11_0_arm64.whl'
SDIST = '.companions/hypercast-artifacts/pypi-wheels-sdist/hypercast-1.0.0.tar.gz'
PYODIDE = '.companions/hypercast-artifacts/pypi-wheels-pyodide/hypercast-1.0.0-cp314-abi3-pyemscripten_2026_0_wasm32.whl'
PY_ENV = {**ENV, 'HYPERCAST_PYTHON_REQ': ','.join((ABI3, ABI3T, SDIST)), 'HYPERCAST_PYODIDE_REQ': PYODIDE}


def quiet(test):
    """The wiring's and the probes' progress lines, kept out of the test run's output."""
    for stream in ('sys.stdout', 'sys.stderr'):
        patcher = mock.patch(stream, io.StringIO())
        patcher.start()
        test.addCleanup(patcher.stop)


def checkout(root, *ecosystems):
    """A consumer checkout holding the fixtures of `ecosystems`, with the pinned companion's
    source laid out as fetch-companions leaves it (its own lock included, which no wiring may
    touch)."""
    root = Path(root)
    for eco in ecosystems:
        shutil.copytree(FIXTURES / eco, root, dirs_exist_ok=True)
    source = root / '.companions' / 'hypercast'
    (source / 'rust').mkdir(parents=True)
    (source / 'rust' / 'Cargo.toml').write_text('[package]\nname = "hypercast"\nversion = "1.0.0"\n')
    (source / 'rust' / 'Cargo.lock').write_text('version = 4\n\n[[package]]\nname = "hypercast"\nversion = "1.0.0"\n')
    (source / 'go').mkdir(parents=True)
    (source / 'go' / 'go.mod').write_text(f'module {MODULE}\n\ngo 1.26\n')
    # The leg's staged artifacts and the SHA256SUMS the stage step writes beside them.
    artifacts = root / '.companions' / 'hypercast-artifacts'
    sums = []
    for relative in (ABI3, ABI3T, SDIST, PYODIDE):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f'pinned {path.name}'.encode())
        sums.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(artifacts).as_posix()}\n')
    (artifacts / 'SHA256SUMS').write_text(''.join(sums))
    return root.resolve()


def take_patch(lock):
    """What `cargo update -p hypercast` does to a lock when the patch applies: the hypercast
    package loses its registry source and checksum."""
    text = lock.read_text()
    block = re.compile(r'(\[\[package\]\]\nname = "hypercast"\n(?:(?!\n\n).)*)', re.S)
    lock.write_text(block.sub(lambda m: re.sub(r'\n(source|checksum) = [^\n]*', '', m.group(1)), text))


class FakeTool:
    """A runner recording each command. `effect(argv, cwd)` stands for what the tool does."""
    def __init__(self, effect=None):
        self.calls = []
        self.effect = effect

    def __call__(self, argv, cwd=None, check=False, **_):
        self.calls.append((list(map(str, argv)), cwd))
        if self.effect:
            self.effect(list(map(str, argv)), cwd)
        return subprocess.CompletedProcess(argv, 0, '', '')


def cargo_takes_patch(argv, cwd):
    take_patch(Path(argv[argv.index('--manifest-path') + 1]).with_name('Cargo.lock'))


def go_mod_edit(argv, cwd):
    """`go mod edit -replace=<module>=<dir>`, as go writes it."""
    module, _, target = argv[3].removeprefix('-replace=').partition('=')
    path = Path(cwd) / 'go.mod'
    path.write_text(path.read_text() + f'\nreplace {module} => {target}\n')


class WireRustTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = checkout(Path(self.tmp.name) / 'consumer', 'rust')
        self.home = Path(self.tmp.name) / 'cargo-home'
        self.environ = {'CARGO_HOME': str(self.home)}
        self.pin = self.root / '.companions' / 'hypercast' / 'rust'

    def wire(self, tool, environ=None, home=None):
        companion_wire.wire_rust(self.root, ENV, run=tool, environ=self.environ if environ is None else environ,
                                 home=home)

    def patch(self, home=None):
        return tomllib.loads(((home or self.home) / 'config.toml').read_text())['patch']['crates-io']

    def test_wire_rust_pinned_passes(self):
        self.home.mkdir()
        (self.home / 'config.toml').write_text('[net]\ngit-fetch-with-cli = true\n')
        tool = FakeTool(cargo_takes_patch)
        self.wire(tool)
        # The runner's own Cargo settings stay; the patch is added beside them.
        config = tomllib.loads((self.home / 'config.toml').read_text())
        self.assertEqual(config['net'], {'git-fetch-with-cli': True})
        self.assertEqual(self.patch(), {'hypercast': {'path': self.pin.as_posix()}})
        # One update per workspace that locks the companion, so later --locked calls agree.
        self.assertEqual(tool.calls, [(['cargo', 'update', '-p', 'hypercast', '--manifest-path',
                                        str(self.root / workspace / 'Cargo.toml')], None)
                                      for workspace in ('rust', 'typescript')])
        for workspace in ('rust', 'typescript'):
            lock = tomllib.loads((self.root / workspace / 'Cargo.lock').read_text())
            [entry] = [p for p in lock['package'] if p['name'] == 'hypercast']
            self.assertNotIn('source', entry)

    def test_wire_rust_rewrites_requirement_to_pin(self):
        # HyperTabular's manifests name the release its code needs (typescript/ says 1.1.0) while
        # the pin is HyperCast's master, still 1.0.0: in the job's tree only, every requirement
        # becomes the pin's exact version, which the patch meets. Nothing else in the file moves.
        before = {w: (self.root / w / 'Cargo.toml').read_text() for w in ('rust', 'typescript')}
        self.assertIn('hypercast = { version = "1.1.0", default-features = false }', before['typescript'])
        self.wire(FakeTool(cargo_takes_patch))
        for workspace, old in (('rust', '"1.0.0"'), ('typescript', '"1.1.0"')):
            after = (self.root / workspace / 'Cargo.toml').read_text()
            changed = [(a, b) for a, b in zip(before[workspace].splitlines(), after.splitlines()) if a != b]
            self.assertEqual(changed, [(f'hypercast = {{ version = {old}, default-features = false }}',
                                        'hypercast = { version = "=1.0.0", default-features = false }')])
            deps = tomllib.loads(after)['dependencies']['hypercast']
            self.assertEqual(deps, {'version': '=1.0.0', 'default-features': False})

    def test_wire_rust_rewrites_every_form(self):
        manifest = self.root / 'other' / 'Cargo.toml'
        manifest.parent.mkdir()
        manifest.write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n\n'
            '[dependencies]\nhypercast = "1.1"  # the judge\nother = "1"\n'
            'judge = { package = "hypercast", version = "^1.1.0", features = ["std"] }\n\n'
            '[dev-dependencies]\nhypercast.version = "1.1.0"\nhypercast.default-features = false\n\n'
            '[build-dependencies.hypercast]\nversion = "~1.1"\noptional = true\n\n'
            "[target.'cfg(unix)'.dependencies]\nhypercast = { version = \"1.1.0\" }\n\n"
            '[target."cfg(windows)".dev-dependencies.hypercast]\nversion = "1.1.0"\n\n'
            '[workspace.dependencies]\nhypercast = { version = "1.1.0", default-features = false }\n')
        self.assertTrue(companion_wire._pin_requirement(manifest, 'hypercast', '1.0.0'))
        data = tomllib.loads(manifest.read_text())
        self.assertEqual(data['dependencies']['hypercast'], '=1.0.0')
        self.assertEqual(data['dependencies']['other'], '1')
        self.assertEqual(data['dependencies']['judge'], {'package': 'hypercast', 'version': '=1.0.0', 'features': ['std']})
        self.assertEqual(data['dev-dependencies']['hypercast'], {'version': '=1.0.0', 'default-features': False})
        self.assertEqual(data['build-dependencies']['hypercast'], {'version': '=1.0.0', 'optional': True})
        self.assertEqual(data['target']['cfg(unix)']['dependencies']['hypercast'], {'version': '=1.0.0'})
        self.assertEqual(data['target']['cfg(windows)']['dev-dependencies']['hypercast'], {'version': '=1.0.0'})
        self.assertEqual(data['workspace']['dependencies']['hypercast'], {'version': '=1.0.0', 'default-features': False})
        self.assertIn('hypercast = "=1.0.0"  # the judge', manifest.read_text())
        # Inheriting from the workspace needs no rewrite of its own; a second run changes nothing.
        member = self.root / 'other' / 'member' / 'Cargo.toml'
        member.parent.mkdir()
        member.write_text('[package]\nname = "m"\n\n[dependencies]\nhypercast = { workspace = true }\n')
        self.assertFalse(companion_wire._pin_requirement(member, 'hypercast', '1.0.0'))
        self.assertFalse(companion_wire._pin_requirement(manifest, 'hypercast', '1.0.0'))

    def test_wire_rust_rewrites_only_the_companion_under_a_shared_key(self):
        # The same key renames the companion in one table and another crate in the next: only the
        # companion's requirement moves.
        manifest = self.root / 'other' / 'Cargo.toml'
        manifest.parent.mkdir()
        manifest.write_text('[package]\nname = "x"\n\n'
                            '[dependencies]\nhc = { package = "hypercast", version = "1.1.0" }\n\n'
                            '[dev-dependencies]\nhc = { package = "serde", version = "1" }\n\n'
                            '[build-dependencies.hc]\npackage = "serde"\nversion = "1"\n')
        self.assertTrue(companion_wire._pin_requirement(manifest, 'hypercast', '1.0.0'))
        data = tomllib.loads(manifest.read_text())
        self.assertEqual(data['dependencies']['hc'], {'package': 'hypercast', 'version': '=1.0.0'})
        self.assertEqual(data['dev-dependencies']['hc'], {'package': 'serde', 'version': '1'})
        self.assertEqual(data['build-dependencies']['hc'], {'package': 'serde', 'version': '1'})

    def test_wire_rust_leaves_multiline_strings_alone(self):
        manifest = self.root / 'other' / 'Cargo.toml'
        manifest.parent.mkdir()
        inside = '\n[dependencies]\nhypercast = "9"\n'
        manifest.write_text('[package]\nname = "x"\n'
                            'description = """' + inside + '"""\n'
                            "notes = '''" + inside + "'''\n\n"
                            '[dependencies]\nhypercast = "1.1.0"\n')
        self.assertTrue(companion_wire._pin_requirement(manifest, 'hypercast', '1.0.0'))
        data = tomllib.loads(manifest.read_text())
        self.assertEqual(data['package']['description'], inside[1:])
        self.assertEqual(data['package']['notes'], inside[1:])
        self.assertEqual(data['dependencies']['hypercast'], '=1.0.0')

    def test_wire_rust_refuses_a_rewrite_that_reaches_beyond_the_requirement(self):
        # A string the line scanner misreads (an escaped quote before two more puts `"""` twice
        # on the opening line) gets a requirement-shaped line inside it rewritten; comparing the
        # whole parsed manifest refuses that rather than writing it (every requirement itself says
        # =1.0.0 afterwards, so only that comparison sees it).
        manifest = self.root / 'other' / 'Cargo.toml'
        manifest.parent.mkdir()
        before = ('[dependencies]\nhypercast = "1.1.0"\n\n'
                  '[package]\nname = "x"\ndescription = """he said \\"""\n[dependencies]\nhypercast = "9"\n"""\n')
        manifest.write_text(before)
        self.assertEqual(tomllib.loads(before)['package']['description'],
                         'he said """\n[dependencies]\nhypercast = "9"\n')
        with self.assertRaises(ValueError) as caught:
            companion_wire._pin_requirement(manifest, 'hypercast', '1.0.0')
        self.assertIn('changes more than', str(caught.exception))
        self.assertEqual(manifest.read_text(), before)

    def test_wire_rust_keeps_line_endings(self):
        manifest = self.root / 'other' / 'Cargo.toml'
        manifest.parent.mkdir()
        before = b'[package]\r\nname = "x"\r\n\r\n[dependencies]\r\nhypercast = "1.1.0"\r\nother = "1"\n'
        manifest.write_bytes(before)
        self.assertTrue(companion_wire._pin_requirement(manifest, 'hypercast', '1.0.0'))
        self.assertEqual(manifest.read_bytes(), before.replace(b'"1.1.0"', b'"=1.0.0"'))

    def test_wire_rust_refuses_a_path_or_git_with_a_version(self):
        # Cargo builds a path or git dependency from there and never patches it, version or not.
        for value in ('{ path = "../../HyperCast/rust", version = "1.1.0" }',
                      '{ git = "https://github.com/SkunkWerkx/HyperCast", version = "1.1.0" }'):
            with self.subTest(value=value):
                manifest = self.root / 'other' / 'Cargo.toml'
                manifest.parent.mkdir(exist_ok=True)
                before = f'[package]\nname = "x"\n\n[dependencies]\nhypercast = {value}\n'
                manifest.write_text(before)
                with self.assertRaises(ValueError) as caught:
                    companion_wire._pin_requirement(manifest, 'hypercast', '1.0.0')
                self.assertIn('takes hypercast from a', str(caught.exception))
                self.assertEqual(manifest.read_text(), before)

    def test_wire_rust_refuses_a_path_requirement_on_the_companion(self):
        manifest = self.root / 'rust' / 'Cargo.toml'
        manifest.write_text(manifest.read_text().replace(
            'hypercast = { version = "1.0.0", default-features = false }',
            'hypercast = { path = "../../HyperCast/rust", default-features = false }'))
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeTool(cargo_takes_patch))
        self.assertIn('takes hypercast from a path source', str(caught.exception))

    def test_wire_rust_unknown_form_is_refused(self):
        # A form the text rewrite misses (here a quoted `version` key) is caught by re-parsing,
        # never left at 1.1.0.
        manifest = self.root / 'typescript' / 'Cargo.toml'
        manifest.write_text(manifest.read_text().replace(
            'hypercast = { version = "1.1.0", default-features = false }',
            'hypercast = { "version" = "1.1.0", default-features = false }'))
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeTool(cargo_takes_patch))
        self.assertIn("still requires '1.1.0', not '=1.0.0'", str(caught.exception))

    def test_wire_rust_then_origin_passes(self):
        import companion_origin
        from test_companion_origin import Recorded
        self.wire(FakeTool(cargo_takes_patch))
        runner = Recorded(self.root, 'pinned.stdout')
        companion_origin.origin_rust(self.root, ENV, {}, run=runner)
        self.assertEqual(len(runner.calls), 2)

    def test_wire_rust_registry_copy_is_rejected(self):
        # An update that leaves crates.io's copy in the lock (the patch's version doesn't meet the
        # requirement, so Cargo ignores it) stops the leg here, naming the lock.
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeTool())
        message = str(caught.exception)
        self.assertIn('rust/Cargo.lock', message)
        self.assertIn('registry+https://github.com/rust-lang/crates.io-index', message)
        self.assertIn(self.pin.as_posix(), message)

    def test_wire_rust_refuses_another_patch(self):
        self.home.mkdir()
        before = '[patch.crates-io]\nhypercast = { path = "../HyperCast/rust" }\n'
        (self.home / 'config.toml').write_text(before)
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeTool(cargo_takes_patch))
        self.assertIn('[patch.crates-io]', str(caught.exception))
        self.assertEqual((self.home / 'config.toml').read_text(), before)

    def test_wire_rust_twice_writes_one_patch(self):
        self.wire(FakeTool(cargo_takes_patch))
        self.wire(FakeTool(cargo_takes_patch))
        self.assertEqual((self.home / 'config.toml').read_text().count('[patch.crates-io]'), 1)

    def test_wire_rust_without_cargo_home_writes_the_home_config(self):
        home = Path(self.tmp.name) / 'home'
        self.wire(FakeTool(cargo_takes_patch), environ={}, home=home)
        self.assertEqual(self.patch(home / '.cargo'), {'hypercast': {'path': self.pin.as_posix()}})

    def test_wire_rust_updates_every_workspace_but_the_companions_own(self):
        shutil.copytree(self.root / 'rust', self.root / 'java' / 'jni')
        # A build directory and the pinned source's own lock are never workspaces to update.
        shutil.copytree(self.root / 'rust', self.root / 'rust' / 'target' / 'package' / 'x')
        tool = FakeTool(cargo_takes_patch)
        self.wire(tool)
        updated = [Path(argv[-1]).parent.relative_to(self.root).as_posix() for argv, _ in tool.calls]
        self.assertEqual(updated, ['java/jni', 'rust', 'typescript'])

    def test_wire_rust_needs_a_workspace_locking_the_companion(self):
        for workspace in ('rust', 'typescript'):
            (self.root / workspace / 'Cargo.lock').write_text('version = 4\n')
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeTool(cargo_takes_patch))
        self.assertIn('no Cargo.lock', str(caught.exception))

    def test_wire_rust_needs_the_pinned_crate(self):
        shutil.rmtree(self.pin)
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeTool(cargo_takes_patch))
        self.assertIn('.companions/hypercast/rust/Cargo.toml', str(caught.exception))
        self.assertFalse((self.home / 'config.toml').exists())


class WireGoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = checkout(Path(self.tmp.name) / 'consumer', 'go')
        self.pin = self.root / '.companions' / 'hypercast' / 'go'

    def test_wire_go_pinned_passes(self):
        tool = FakeTool(go_mod_edit)
        companion_wire.wire_go(self.root, ENV, run=tool)
        self.assertEqual(tool.calls, [(['go', 'mod', 'edit', f'-replace={MODULE}={self.pin}'], self.root / 'go')])
        self.assertIn(f'replace {MODULE} => {self.pin}', (self.root / 'go' / 'go.mod').read_text())
        # A local replace needs no go.sum line: the file is left as committed.
        self.assertEqual((self.root / 'go' / 'go.sum').read_text(), (FIXTURES / 'go' / 'go' / 'go.sum').read_text())

    def test_wire_go_registry_copy_is_rejected(self):
        # A replace that didn't land leaves the module proxy to decide: stopped here.
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_go(self.root, ENV, run=FakeTool())
        self.assertIn('go/go.mod', str(caught.exception))
        self.assertIn(MODULE, str(caught.exception))

    def test_wire_go_needs_a_module_requiring_the_companion(self):
        (self.root / 'go' / 'go.mod').write_text('module example.com/x\n\ngo 1.26\n')
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_go(self.root, ENV, run=FakeTool(go_mod_edit))
        self.assertIn(f'no go.mod requires {MODULE}', str(caught.exception))

    def test_wire_go_takes_the_module_path_from_the_pinned_source(self):
        (self.pin / 'go.mod').write_text('module example.com/renamed\n')
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_go(self.root, ENV, run=FakeTool(go_mod_edit))
        self.assertIn('example.com/renamed', str(caught.exception))

    @unittest.skipUnless(shutil.which('go'), 'no go on PATH')
    def test_wire_go_with_the_real_go(self):
        companion_wire.wire_go(self.root, ENV)
        text = (self.root / 'go' / 'go.mod').read_text()
        self.assertRegex(text, rf'(?m)^replace {re.escape(MODULE)} => {re.escape(str(self.pin))}$')


ORIGIN_FIXTURES = SCRIPTS / 'fixtures' / 'origin' / 'python'
REQUIREMENT = 'dependencies = ["hypercast>=1.1.0,<2"]'


class FakePip:
    """`<python> -m pip` answering from recorded output (fixtures/origin/python/): the dry run's
    report (`report`, a fixture name, or `nomatch.stderr` for pip's exit 1), and an install,
    which it records. `python` is the interpreter command the wiring was given."""
    def __init__(self, root, report='report-abi3.json', python=('/x/bin/python',)):
        self.root, self.report, self.python = Path(root), report, list(python)
        self.calls, self.installed, self.kwargs = [], [], []
        self.edit = lambda text: text

    def __call__(self, argv, cwd=None, check=False, capture_output=False, text=False, **kwargs):
        argv = list(map(str, argv))
        self.calls.append(argv)
        self.kwargs.append(kwargs)
        assert argv[:len(self.python) + 2] == self.python + ['-m', 'pip'], argv
        if '--dry-run' in argv:
            if self.report == 'nomatch.stderr':
                return subprocess.CompletedProcess(argv, 1, '', (ORIGIN_FIXTURES / self.report).read_text())
            return subprocess.CompletedProcess(argv, 0, self.edit((ORIGIN_FIXTURES / self.report).read_text()
                                                                  .replace('@ROOT@', str(self.root))), '')
        self.installed.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, '', '')


class WirePythonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = checkout(Path(self.tmp.name) / 'consumer', 'python')
        self.pyproject = self.root / 'python' / 'pyproject.toml'

    def wire(self, pip, env=PY_ENV, python='/x/bin/python', environ=None):
        companion_wire.wire_python(self.root, env, run=pip, python=python, environ=environ or {})

    def test_wire_python_pinned_passes(self):
        pip = FakePip(self.root)
        self.wire(pip)
        # pip chooses among the pinned wheels alone (no index), then installs that file by its path,
        # so PEP 610's direct_url.json records it and its hash.
        dry_run = pip.calls[0]
        self.assertEqual(dry_run[:4], ['/x/bin/python', '-m', 'pip', 'install'])
        for flag in ('--dry-run', '--no-index', '--no-deps', '--only-binary=:all:', '--ignore-installed'):
            self.assertIn(flag, dry_run)
        self.assertEqual(dry_run[dry_run.index('--find-links') + 1], str(self.root / WHEELS))
        self.assertEqual(dry_run[-1], 'hypercast==1.0.0')
        self.assertEqual(pip.calls[1], ['/x/bin/python', '-m', 'pip', 'install', '--disable-pip-version-check',
                                        '--no-deps', '--force-reinstall', str(self.root / ABI3)])
        self.assertEqual(len(pip.calls), 2)
        # The requirement names the pin's exact version, which the installed copy meets: that one
        # line is the only change (the consumer's manifest still names the release it needs).
        before = (FIXTURES / 'python' / 'python' / 'pyproject.toml').read_text()
        self.assertIn(REQUIREMENT, before)
        self.assertEqual(self.pyproject.read_text(),
                         before.replace(REQUIREMENT, 'dependencies = ["hypercast==1.0.0"]'))

    def test_the_dry_run_report_is_read_as_utf8_whatever_the_code_page(self):
        # The report carries the wheel's README; on Windows a pipe is cp1252, where pip failed on
        # HyperCast's check marks. pip is told to write UTF-8, and the report is decoded so.
        pip = FakePip(self.root)
        self.wire(pip)
        self.assertEqual(pip.kwargs[0].get('encoding'), 'utf-8')
        self.assertEqual((pip.kwargs[0].get('env') or {}).get('PYTHONIOENCODING'), 'utf-8')

    def test_wire_python_free_threaded_takes_the_abi3t_wheel(self):
        pip = FakePip(self.root, 'report-abi3t.json')
        self.wire(pip)
        self.assertEqual(pip.installed, [str(self.root / ABI3T)])

    def test_wire_python_no_wheel_fits_takes_the_sdist(self):
        # 3.14t and PyPy: no pinned wheel fits, so the pinned sdist is built, never the index's.
        pip = FakePip(self.root, 'nomatch.stderr')
        self.wire(pip)
        self.assertEqual(pip.installed, [str(self.root / SDIST)])

    def test_wire_python_no_wheel_and_no_sdist_fails(self):
        env = {**PY_ENV, 'HYPERCAST_PYTHON_REQ': f'{ABI3},{ABI3T}'}
        pip = FakePip(self.root, 'nomatch.stderr')
        with self.assertRaises(ValueError) as caught:
            self.wire(pip, env)
        self.assertIn('/x/bin/python', str(caught.exception))
        self.assertIn('no pinned wheel fits', str(caught.exception))
        self.assertEqual(pip.installed, [])

    def test_wire_python_registry_copy_is_rejected(self):
        # A choice that is not one of the pinned files (an index's, say) is never installed.
        pip = FakePip(self.root)
        pip.edit = lambda text: text.replace(
            f'file://{self.root}/{ABI3}', 'https://files.pythonhosted.org/packages/hypercast-1.0.0-cp311-abi3.whl')
        with self.assertRaises(ValueError) as caught:
            self.wire(pip)
        self.assertIn('files.pythonhosted.org', str(caught.exception))
        self.assertIn('not a pinned file', str(caught.exception))
        self.assertEqual(pip.installed, [])

    def test_wire_python_tampered_hash_is_rejected(self):
        (self.root / ABI3).write_bytes(b'not what the companions job checked')
        pip = FakePip(self.root)
        with self.assertRaises(ValueError) as caught:
            self.wire(pip)
        self.assertIn('SHA256SUMS', str(caught.exception))
        self.assertIn(Path(ABI3).name, str(caught.exception))
        self.assertEqual(pip.installed, [])

    def test_wire_python_without_an_interpreter_rewrites_only(self):
        # HYPER_COMPANION_PYTHON=none: no interpreter on this host runs the consumer (its
        # containers and venvs install the pin themselves, companion_container.sh).
        pip = FakePip(self.root)
        companion_wire.wire_python(self.root, PY_ENV, run=pip, environ={'HYPER_COMPANION_PYTHON': 'none'})
        self.assertEqual(pip.calls, [])
        self.assertIn('"hypercast==1.0.0"', self.pyproject.read_text())

    def test_wire_python_takes_an_interpreter_command(self):
        # osx-x64's interpreter runs under Rosetta: the command is split, never quoted whole.
        pip = FakePip(self.root, python=('arch', '-x86_64', '/x/bin/python'))
        companion_wire.wire_python(self.root, PY_ENV, run=pip,
                                   environ={'HYPER_COMPANION_PYTHON': 'arch -x86_64 /x/bin/python'})
        self.assertEqual(pip.installed, [str(self.root / ABI3)])
        # --python wins over the variable.
        pip = FakePip(self.root)
        companion_wire.wire_python(self.root, PY_ENV, run=pip, python='/x/bin/python',
                                   environ={'HYPER_COMPANION_PYTHON': 'none'})
        self.assertEqual(len(pip.calls), 2)

    def test_wire_python_takes_an_interpreter_path_whole(self):
        # setup-python's path on Windows (backslashes) or one with a space is one argument.
        odd = Path(self.tmp.name) / 'Python 3.14' / 'python\\exe'
        odd.parent.mkdir()
        odd.write_text('')
        self.assertEqual(companion_wire.python_command(str(odd)), [str(odd)])
        self.assertEqual(companion_wire.python_command('arch -x86_64 /x/bin/python'), ['arch', '-x86_64', '/x/bin/python'])

    def test_wire_python_defaults_to_this_interpreter(self):
        pip = FakePip(self.root, python=(sys.executable,))
        companion_wire.wire_python(self.root, PY_ENV, run=pip, environ={})
        self.assertEqual(pip.installed, [str(self.root / ABI3)])

    def test_wire_python_rewrites_every_form(self):
        self.pyproject.write_text(
            '[project]\nname = "x"\ndependencies = [\n'
            '    "numpy>=2",\n'
            "    'HyperCast[fast] >= 1.1.0, <2 ; python_version >= \"3.11\"',\n"
            '    "hypercastle>=1",\n'
            ']\n'
            '[project.optional-dependencies]\ntest = ["pytest", "HYPERCAST~=1.1", "hyper-cast>=1"]\n'
            '[dependency-groups]\ndev = ["hypercast"]\n')
        self.wire(FakePip(self.root))
        data = tomllib.loads(self.pyproject.read_text())
        self.assertEqual(data['project']['dependencies'],
                         ['numpy>=2', 'HyperCast[fast]==1.0.0 ; python_version >= "3.11"', 'hypercastle>=1'])
        self.assertEqual(data['project']['optional-dependencies']['test'], ['pytest', 'HYPERCAST==1.0.0', 'hyper-cast>=1'])
        self.assertEqual(data['dependency-groups']['dev'], ['hypercast==1.0.0'])
        # Twice is once.
        text = self.pyproject.read_text()
        self.wire(FakePip(self.root))
        self.assertEqual(self.pyproject.read_text(), text)

    def test_wire_python_refuses_a_direct_reference(self):
        self.pyproject.write_text('[project]\nname = "x"\ndependencies = ["hypercast @ file:///x/hypercast.whl"]\n')
        pip = FakePip(self.root)
        with self.assertRaises(ValueError) as caught:
            self.wire(pip)
        self.assertIn('direct reference', str(caught.exception))
        self.assertEqual(pip.calls, [])

    def test_wire_python_refuses_a_rewrite_that_reaches_beyond_the_requirement(self):
        # The same text outside a dependency list: rewriting it would change more than the
        # requirement, so nothing is written.
        text = ('[project]\nname = "x"\nkeywords = ["hypercast>=1.1.0,<2"]\n'
                'dependencies = ["hypercast>=1.1.0,<2"]\n')
        self.pyproject.write_text(text)
        with self.assertRaises(ValueError) as caught:
            self.wire(FakePip(self.root))
        self.assertIn('changes more than', str(caught.exception))
        self.assertEqual(self.pyproject.read_text(), text)

    def test_wire_python_needs_a_manifest_requiring_the_companion(self):
        self.pyproject.write_text('[project]\nname = "x"\ndependencies = ["numpy"]\n')
        with self.assertRaises(ValueError) as caught:
            self.wire(FakePip(self.root))
        self.assertIn('no pyproject.toml', str(caught.exception))

    def test_wire_python_reads_a_read_only_tree_already_rewritten(self):
        # A container mounting the checkout read-only (/src) wires after the host rewrote it:
        # the rewrite has nothing left to do and writes nothing.
        self.wire(FakePip(self.root))
        self.pyproject.chmod(0o444)
        self.addCleanup(self.pyproject.chmod, 0o644)
        pip = FakePip(self.root)
        self.wire(pip)
        self.assertEqual(pip.installed, [str(self.root / ABI3)])


class WirePyodideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = checkout(Path(self.tmp.name) / 'consumer', 'python')
        self.deps = Path(self.tmp.name) / 'deps'
        self.deps.mkdir()

    def test_wire_pyodide_pinned_passes(self):
        companion_wire.wire_pyodide(self.root, PY_ENV, deps=self.deps)
        # The pinned wheel goes where pyodide-suite installs the dependencies from, before the
        # consumer's wheel, and the requirement is the pin's exact version.
        self.assertEqual(sorted(p.name for p in self.deps.iterdir()), [Path(PYODIDE).name])
        self.assertEqual((self.deps / Path(PYODIDE).name).read_bytes(), (self.root / PYODIDE).read_bytes())
        self.assertIn('"hypercast==1.0.0"', (self.root / 'python' / 'pyproject.toml').read_text())

    def test_wire_pyodide_without_deps_rewrites_only(self):
        companion_wire.wire_pyodide(self.root, PY_ENV)
        self.assertIn('"hypercast==1.0.0"', (self.root / 'python' / 'pyproject.toml').read_text())
        self.assertEqual(list(self.deps.iterdir()), [])

    def test_wire_pyodide_registry_copy_is_rejected(self):
        # Another copy of the companion among the dependencies (pip download's, from PyPI) would
        # be installed beside or instead of the pin.
        (self.deps / 'hypercast-1.0.0-cp314-abi3-pyemscripten_2026_0_wasm32.whl').write_bytes(b'from PyPI')
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_pyodide(self.root, PY_ENV, deps=self.deps)
        self.assertIn('already holds', str(caught.exception))
        (self.deps / 'hypercast-1.0.0-cp314-abi3-pyemscripten_2026_0_wasm32.whl').unlink()
        (self.deps / 'HyperCast-0.9.0-py3-none-any.whl').write_bytes(b'from PyPI')
        with self.assertRaises(ValueError):
            companion_wire.wire_pyodide(self.root, PY_ENV, deps=self.deps)

    def test_wire_pyodide_tampered_hash_is_rejected(self):
        (self.root / PYODIDE).write_bytes(b'tampered')
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_pyodide(self.root, PY_ENV, deps=self.deps)
        self.assertIn('SHA256SUMS', str(caught.exception))
        self.assertEqual(list(self.deps.iterdir()), [])

    def test_wire_pyodide_needs_a_pinned_wheel(self):
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_pyodide(self.root, {**PY_ENV, 'HYPERCAST_PYODIDE_REQ': ''}, deps=self.deps)
        self.assertIn('no pinned Pyodide wheel', str(caught.exception))


def npm_integrity(path):
    """npm's integrity string for a file: `sha512-` and the base64 digest."""
    import base64
    return 'sha512-' + base64.b64encode(hashlib.sha512(Path(path).read_bytes()).digest()).decode()


def npm_stage(root, *staged):
    """The pinned TypeScript package as fetch-companions leaves it (HyperCast 14df722's
    package.json and hyper.json, fixtures/wire/npm-pinned/), and the leg's node-* artifacts
    (`staged`, paths under the checkout) beside the Python ones, SHA256SUMS listing them all."""
    root = Path(root)
    shutil.copytree(FIXTURES / 'npm-pinned', root / '.companions' / 'hypercast', dirs_exist_ok=True)
    artifacts = root / '.companions' / 'hypercast-artifacts'
    lines = [(artifacts / 'SHA256SUMS').read_text()]
    for relative in staged:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f'pinned {path.name}'.encode())
        lines.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(artifacts).as_posix()}\n')
    (artifacts / 'SHA256SUMS').write_text(''.join(lines))
    return root.resolve()


# The leg's artifacts as an osx-arm64 addon leg stages them, and as the WASI leg does: the module
# and the loaders hyper-build-wasm.yml's build-node-wasi collects (`<crate>.wasm32-wasip1.wasm`,
# `<crate>.wasip1*`), the names HyperCast 14df722's `npm run build:wasi` wrote.
NODE = '.companions/hypercast-artifacts/node-osx-arm64'
ADDON = f'{NODE}/hypercast.darwin-arm64.node'
NODE_WASI = '.companions/hypercast-artifacts/node-wasm32-wasip1'
WASI = tuple(f'{NODE_WASI}/{name}' for name in (
    'hypercast.wasm32-wasip1.wasm', 'hypercast.wasip1.cjs', 'hypercast.wasip1.d.cts', 'hypercast.wasip1-browser.js',
    'hypercast.wasip1-deferred.js', 'hypercast.wasip1-deferred.d.ts'))
NPM_ENV = {**ENV, 'HYPERCAST_NPM_REQ': '.companions/hypercast/typescript'}
PACKAGE = '@skunkwerkx/hypercast'
WASI_PACKAGE = '@skunkwerkx/hypercast-wasm32-wasip1'
TARBALL = '.companions/hypercast-npm/skunkwerkx-hypercast-1.0.0.tgz'
WASI_TARBALL = '.companions/hypercast-npm/skunkwerkx-hypercast-wasm32-wasip1-1.0.0.tgz'


def npm_pack(directory, out):
    """What `npm pack --json --pack-destination <out>` does in `directory`: package.json, and the
    `files` it lists that exist (npm also always adds README and LICENSE), under `package/` in a
    gzipped tarball; its report."""
    import tarfile
    manifest = json.loads((Path(directory) / 'package.json').read_text())
    names = ['package.json'] + [n for n in manifest.get('files', []) if (Path(directory) / n).is_file()
                                and n != 'package.json']
    filename = manifest['name'].lstrip('@').replace('/', '-') + f'-{manifest["version"]}.tgz'
    tarball = Path(out) / filename
    with tarfile.open(tarball, 'w:gz') as archive:
        for name in names:
            archive.add(Path(directory) / name, arcname=f'package/{name}')
    return [{'name': manifest['name'], 'version': manifest['version'], 'filename': filename,
             'integrity': npm_integrity(tarball), 'files': [{'path': n} for n in names]}]


class FakeNpm:
    """npm as the wiring runs it. `pack` packs as npm does (npm_pack), noting the package.json it
    packed from; `install --package-lock-only` records each `file:` dependency of the manifest in
    its lock as npm writes one (resolved relative to the lock, the tarball's integrity). With
    `takes=False` it leaves the lock as it was (a rewrite npm never read); with `registry=True`
    it writes, for each such dependency, the entry npm wrote for the registry's copy
    (fixtures/origin/npm/registry.json: npmjs's 0.0.1 placeholder). `report` shapes pack's list of
    reports as another npm prints it."""
    def __init__(self, takes=True, fail_pack=False, registry=False, report=lambda reports: reports):
        self.calls, self.packed_from, self.takes, self.fail_pack = [], [], takes, fail_pack
        self.registry, self.report = registry, report

    def __call__(self, argv, cwd=None, check=False, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append((argv, str(cwd)))
        assert argv[0] == 'npm', argv
        if argv[1] == 'pack':
            self.packed_from.append(json.loads((Path(cwd) / 'package.json').read_text()))
            if self.fail_pack:
                raise subprocess.CalledProcessError(1, argv, '', 'npm error pack failed')
            out = Path(argv[argv.index('--pack-destination') + 1])
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.report(npm_pack(cwd, out))), '')
        assert argv[1:3] == ['install', '--package-lock-only'], argv
        if self.takes:
            manifest = json.loads((Path(cwd) / 'package.json').read_text())
            lock_path = Path(cwd) / 'package-lock.json'
            lock = json.loads(lock_path.read_text())
            top = lock['packages']['']
            for section in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'):
                if manifest.get(section):
                    top[section] = manifest[section]
            for section in ('dependencies', 'devDependencies', 'optionalDependencies'):
                for name, spec in (manifest.get(section) or {}).items():
                    if spec.startswith('file:'):
                        tarball = Path(spec[5:])
                        if self.registry:
                            lock['packages'][f'node_modules/{name}'] = json.loads(
                                (SCRIPTS / 'fixtures' / 'origin' / 'npm' / 'registry.json').read_text())
                            continue
                        lock['packages'][f'node_modules/{name}'] = {
                            'version': '1.0.0', 'resolved': 'file:' + os.path.relpath(tarball, cwd).replace(os.sep, '/'),
                            'integrity': npm_integrity(tarball), 'dev': section == 'devDependencies', 'license': 'MIT'}
            lock_path.write_text(json.dumps(lock, indent=2) + '\n')
        return subprocess.CompletedProcess(argv, 0, '', '')


class RealPackNpm(FakeNpm):
    """npm's own `pack`, with the lock still FakeNpm's (a real lock-only install of the fixture
    would reach the registry for the rest of its tree)."""
    def __call__(self, argv, cwd=None, **kwargs):
        if argv[1] != 'pack':
            return super().__call__(argv, cwd, **kwargs)
        self.calls.append((list(map(str, argv)), str(cwd)))
        return subprocess.run([shutil.which('npm'), *map(str, argv[1:])], cwd=cwd, capture_output=True, text=True,
                              check=True)


def fake_pack_wasi(package_dir, artifact_dir, out, cfg, work=None, runner=None):
    """npm_package.pack_wasi, standing in for the release's own packing (napi create-npm-dirs and
    `npm pack` of npm/wasm32-wasip1/): the artifact's files under `package/`, as it packs them."""
    import tarfile
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tarball = out / f'skunkwerkx-{cfg.crate}-{cfg.node_wasi}-1.0.0.tgz'
    manifest = Path(out) / 'package.json'
    manifest.write_text(json.dumps({'name': f'@skunkwerkx/{cfg.crate}-{cfg.node_wasi}', 'version': '1.0.0'}))
    with tarfile.open(tarball, 'w:gz') as archive:
        archive.add(manifest, arcname='package/package.json')
        manifest.unlink()
        for path in sorted(Path(artifact_dir).iterdir()):
            archive.add(path, arcname=f'package/{path.name}')
    return tarball


def tar_members(tarball):
    import tarfile
    with tarfile.open(tarball) as archive:
        return {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}


class FakeBun:
    """Bun as the wiring runs it: `pm pack --quiet` packs as npm_pack does and prints the tarball;
    `install --lockfile-only` writes each `file:` dependency as Bun does (the absolute path, the
    tarball's integrity), or leaves the lock alone with `takes=False`."""
    def __init__(self, takes=True):
        self.calls, self.takes = [], takes

    def __call__(self, argv, cwd=None, check=False, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append(argv)
        assert argv[0] == 'bun', argv
        if argv[1:3] == ['pm', 'pack']:
            out = Path(argv[argv.index('--destination') + 1])
            report = npm_pack(cwd, out)
            return subprocess.CompletedProcess(argv, 0, str(out / report[0]['filename']) + '\n', '')
        assert argv[1:3] == ['install', '--lockfile-only'], argv
        if self.takes:
            manifest = json.loads((Path(cwd) / 'package.json').read_text())
            packages = {}
            for section in ('dependencies', 'devDependencies', 'optionalDependencies'):
                for name, spec in (manifest.get(section) or {}).items():
                    if spec.startswith('file:'):
                        packages[name] = [f'{name}@{spec[5:]}', {}, npm_integrity(spec[5:])]
            (Path(cwd) / 'bun.lock').write_text(json.dumps({'packages': packages}, indent=2) + '\n')
        return subprocess.CompletedProcess(argv, 0, '', '')


class WireBunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = npm_stage(checkout(Path(self.tmp.name) / 'consumer', 'npm'), ADDON)
        for directory in (self.root / 'typescript', self.root / 'typescript' / 'browser-smoke'):
            (directory / 'package-lock.json').unlink()
            (directory / 'bun.lock').write_text('{"packages": {},}\n')

    def wire(self, bun):
        with mock.patch('npm_package.pack_wasi', fake_pack_wasi):
            companion_wire.wire_npm(self.root, NPM_ENV, run=bun, environ={})

    def test_a_bun_lock_packs_and_relocks_with_bun(self):
        bun = FakeBun()
        self.wire(bun)
        self.assertEqual(bun.calls[0][:3], ['bun', 'pm', 'pack'])
        self.assertIn(['bun', 'install', '--lockfile-only', '--ignore-scripts'], bun.calls)
        entry = companion_wire._npm_lock_entry(self.root / 'typescript', PACKAGE)
        self.assertEqual(entry['resolved'], f'file:{(self.root / TARBALL).as_posix()}')
        self.assertEqual(entry['integrity'], npm_integrity(self.root / TARBALL))

    def test_a_lock_that_ignores_the_rewrite_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'typescript/bun.lock resolves .* from nothing'):
            self.wire(FakeBun(takes=False))

    def test_a_pack_without_the_payload_is_rejected(self):
        class Whitelist(FakeBun):
            def __call__(self, argv, cwd=None, **kwargs):
                if list(map(str, argv))[1:3] == ['pm', 'pack']:
                    data = json.loads((Path(cwd) / 'package.json').read_text())
                    data['files'] = [f for f in data['files'] if not f.endswith('.node')]
                    (Path(cwd) / 'package.json').write_text(json.dumps(data))
                return super().__call__(argv, cwd=cwd, **kwargs)
        with self.assertRaisesRegex(ValueError, 'bun pm pack left .*\\.node out of'):
            self.wire(Whitelist())


class WireNpmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = npm_stage(checkout(Path(self.tmp.name) / 'consumer', 'npm'), ADDON)
        self.source = self.root / '.companions' / 'hypercast' / 'typescript'
        self.manifest = self.root / 'typescript' / 'package.json'
        self.smoke = self.root / 'typescript' / 'browser-smoke' / 'package.json'

    def wire(self, npm, environ=None):
        with mock.patch('npm_package.pack_wasi', fake_pack_wasi):
            companion_wire.wire_npm(self.root, NPM_ENV, run=npm, environ=environ or {})

    def test_wire_npm_pinned_passes(self):
        source_before = (self.source / 'package.json').read_bytes()
        smoke_before = self.smoke.read_bytes()
        npm = FakeNpm()
        self.wire(npm)
        tarball = self.root / TARBALL
        # The pinned package packed once, from its own directory, with a job-local files list that
        # adds this leg's addon (the committed whitelist leaves every addon out)...
        self.assertEqual([c for c, _ in npm.calls][0],
                         ['npm', 'pack', '--json', '--ignore-scripts', '--pack-destination', str(tarball.parent)])
        self.assertEqual(npm.calls[0][1], str(self.source))
        committed = json.loads(source_before)['files']
        self.assertEqual(npm.packed_from[0]['files'], committed + ['hypercast.darwin-arm64.node'])
        self.assertEqual(tar_members(tarball)['package/hypercast.darwin-arm64.node'],
                         (self.root / ADDON).read_bytes())
        # ...and the pinned checkout left as it was checked out: the manifest restored byte for
        # byte, the addon's copy gone.
        self.assertEqual((self.source / 'package.json').read_bytes(), source_before)
        self.assertFalse((self.source / 'hypercast.darwin-arm64.node').exists())
        # The consumer's direct devDependency on that tarball, and its peer on the pin's exact
        # version (dev supersedes peer for the package's own install; the peer is what a packed
        # consumer names to the registry it is installed from). Nothing else in the file moves.
        before = json.loads((FIXTURES / 'npm' / 'typescript' / 'package.json').read_text())
        after = json.loads(self.manifest.read_text())
        self.assertEqual(after['devDependencies'][PACKAGE], f'file:{tarball.as_posix()}')
        self.assertEqual(after['peerDependencies'][PACKAGE], '1.0.0')
        before['devDependencies'][PACKAGE] = after['devDependencies'][PACKAGE]
        before['peerDependencies'][PACKAGE] = '1.0.0'
        self.assertEqual(after, before)
        self.assertEqual(list(after), list(before))
        # The lock regenerated with --package-lock-only, which now resolves the tarball.
        self.assertEqual(npm.calls[1], (['npm', 'install', '--package-lock-only', '--ignore-scripts', '--no-audit',
                                         '--no-fund'], str(self.root / 'typescript')))
        entry = json.loads((self.root / 'typescript' / 'package-lock.json').read_text())['packages'][
            f'node_modules/{PACKAGE}']
        self.assertEqual(entry['resolved'], 'file:../.companions/hypercast-npm/skunkwerkx-hypercast-1.0.0.tgz')
        self.assertEqual(entry['integrity'], npm_integrity(tarball))
        # browser-smoke takes the WASI package, which this addon leg stages nothing for: left as
        # committed, and never installed by this leg.
        self.assertEqual(self.smoke.read_bytes(), smoke_before)
        self.assertEqual(len(npm.calls), 2)
        # The record the origin check and the containers read.
        record = json.loads((self.root / '.companions' / 'hypercast-npm' / 'wired.json').read_text())
        self.assertEqual(record['packages'], {PACKAGE: {
            'tarball': TARBALL, 'integrity': npm_integrity(tarball),
            'payload': ['node-osx-arm64/hypercast.darwin-arm64.node']}})

    def test_wire_npm_wasi_leg_takes_the_payload_and_the_wasi_package(self):
        shutil.rmtree(self.root / NODE)
        npm_stage(self.root, *WASI)
        npm = FakeNpm()
        self.wire(npm)
        names = [Path(p).name for p in WASI]
        self.assertEqual(npm.packed_from[0]['files'][-len(names):], sorted(names))
        members = tar_members(self.root / TARBALL)
        for path in WASI:
            self.assertEqual(members[f'package/{Path(path).name}'], (self.root / path).read_bytes())
        self.assertNotIn('package/hypercast.darwin-arm64.node', members)
        # browser-smoke's devDependency on the WASI package, packed as the release packs it.
        smoke = json.loads(self.smoke.read_text())
        self.assertEqual(smoke['devDependencies'][WASI_PACKAGE], f'file:{(self.root / WASI_TARBALL).as_posix()}')
        lock = json.loads((self.smoke.parent / 'package-lock.json').read_text())['packages']
        self.assertEqual(lock[f'node_modules/{WASI_PACKAGE}']['integrity'], npm_integrity(self.root / WASI_TARBALL))
        self.assertEqual([c[1] for c in npm.calls[1:]], [str(self.root / 'typescript'), str(self.smoke.parent)])

    def test_wire_npm_registry_copy_is_rejected(self):
        # npm resolved the registry's copy (npmjs's placeholder, as a real lock names it): the
        # wiring sees the URL and stops.
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeNpm(registry=True))
        self.assertIn(f'typescript/package-lock.json resolves {PACKAGE} from '
                      'https://registry.npmjs.org/@skunkwerkx/hypercast/-/hypercast-0.0.1.tgz (sha512-pSPU',
                      str(caught.exception))
        self.assertIn(f'not the pinned {TARBALL}', str(caught.exception))

    def test_wire_npm_tampered_hash_is_rejected(self):
        (self.root / ADDON).write_bytes(b'not the pinned addon')
        npm = FakeNpm()
        with self.assertRaises(ValueError) as caught:
            self.wire(npm)
        self.assertIn('node-osx-arm64/hypercast.darwin-arm64.node', str(caught.exception))
        self.assertIn('SHA256SUMS', str(caught.exception))
        self.assertEqual(npm.calls, [])

    def test_wire_npm_lock_that_ignores_the_rewrite_is_rejected(self):
        # npm left the lock as committed (a registry resolution, or a rewrite it never read): the
        # wiring fails there, not at the install that would follow.
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeNpm(takes=False))
        self.assertIn(f'typescript/package-lock.json resolves {PACKAGE} from nothing', str(caught.exception))
        self.assertIn(f'not the pinned {TARBALL}: the rewrite was not used', str(caught.exception))

    def test_wire_npm_refuses_an_addon_for_another_platform(self):
        # A mis-mapped artifact: the osx-arm64 leg's directory holds the linux-x64 addon, which
        # napi's loader on that leg never asks for. Refused by name, before anything is packed.
        root = Path(self.tmp.name) / 'misnamed'
        npm_stage(checkout(root, 'npm'), f'{NODE}/hypercast.linux-x64-gnu.node')
        npm = FakeNpm()
        with self.assertRaises(ValueError) as caught:
            with mock.patch('npm_package.pack_wasi', fake_pack_wasi):
                companion_wire.wire_npm(root, NPM_ENV, run=npm, environ={})
        self.assertIn('node-osx-arm64 has no hypercast.darwin-arm64.node', str(caught.exception))
        self.assertIn('hypercast.linux-x64-gnu.node', str(caught.exception))
        self.assertEqual(npm.calls, [])
        # The WASI leg needs the module and its loader; a directory for no napi target is refused,
        # and so is a second addon beside the leg's own.
        for staged, message in (((f'{NODE_WASI}/hypercast.wasip1.cjs',), 'has no hypercast.wasm32-wasip1.wasm'),
                                ((f'{NODE_WASI}/hypercast.wasm32-wasip1.wasm',), 'has no hypercast.wasip1.cjs'),
                                (('.companions/hypercast-artifacts/node-solaris-x64/hypercast.solaris.node',),
                                 'node-solaris-x64 is no napi target'),
                                ((ADDON, f'{NODE}/hypercast.linux-x64-gnu.node'),
                                 'holds hypercast.linux-x64-gnu.node beside hypercast.darwin-arm64.node')):
            root = Path(self.tmp.name) / staged[0].split('/')[-1]
            npm_stage(checkout(root, 'npm'), *staged)
            with self.assertRaises(ValueError) as caught:
                with mock.patch('npm_package.pack_wasi', fake_pack_wasi):
                    companion_wire.wire_npm(root, NPM_ENV, run=FakeNpm(), environ={})
            self.assertIn(message, str(caught.exception))

    def test_wire_npm_no_overrides_for_direct_dependency(self):
        # An `overrides` entry for a dependency the manifest names directly is npm's EOVERRIDE:
        # the wiring rewrites the dependency itself and adds no override anywhere.
        self.wire(FakeNpm())
        for path in (self.manifest, self.root / 'typescript' / 'package-lock.json'):
            self.assertNotIn('overrides', path.read_text(), path)
        # A committed override of the companion is refused, the manifest left as it was.
        data = json.loads((FIXTURES / 'npm' / 'typescript' / 'package.json').read_text())
        data['overrides'] = {PACKAGE: '1.1.0'}
        self.manifest.write_text(json.dumps(data, indent=2) + '\n')
        before = self.manifest.read_bytes()
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeNpm())
        self.assertIn('overrides', str(caught.exception))
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_wire_npm_reads_the_pack_report_npm_12_prints(self):
        # npm 12 keys the report by package name where npm 11 prints a list; the package's own
        # name is the key, so a report for anything else is no report.
        self.wire(FakeNpm(report=lambda reports: {PACKAGE: reports[0]}))
        self.assertIn('package/hypercast.darwin-arm64.node', tar_members(self.root / TARBALL))

    def test_wire_npm_refuses_a_pack_report_for_another_package(self):
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeNpm(report=lambda reports: {'@other/package': reports[0]}))
        self.assertIn('npm pack printed no report', str(caught.exception))

    def test_wire_npm_restores_the_pinned_manifest_when_the_pack_fails(self):
        before = (self.source / 'package.json').read_bytes()
        with self.assertRaises(subprocess.CalledProcessError):
            self.wire(FakeNpm(fail_pack=True))
        self.assertEqual((self.source / 'package.json').read_bytes(), before)
        self.assertFalse((self.source / 'hypercast.darwin-arm64.node').exists())

    def test_wire_npm_refuses_a_path_requirement_already_there(self):
        data = json.loads(self.manifest.read_text())
        data['devDependencies'][PACKAGE] = 'file:../../HyperCast/typescript'
        self.manifest.write_text(json.dumps(data, indent=2) + '\n')
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeNpm())
        self.assertIn('file:../../HyperCast/typescript', str(caught.exception))

    def test_wire_npm_full_set_is_packed_for_the_registry(self):
        # The consumer tests' leg stages every napi addon and the WASI build: the whole set the
        # release publishes is packed by npm_package.py from them, for the job's own Verdaccio,
        # and the job's later steps learn where (<NAME>_TARBALLS through $GITHUB_ENV).
        import hyper_config
        cfg = hyper_config.load(self.root / '.companions' / 'hypercast')
        addons = [f'.companions/hypercast-artifacts/node-{rid}/hypercast.{suffix}.node'
                  for rid, suffix in cfg.node_platforms.items()]
        npm_stage(self.root, *addons, *WASI)
        github_env = Path(self.tmp.name) / 'github-env'
        github_env.write_text('EARLIER=1\n')
        packed = []

        def fake_pack(package_dir, artifacts_dir, out, cfg, **_):
            packed.append((Path(package_dir), Path(artifacts_dir), Path(out)))
            Path(out).mkdir(parents=True, exist_ok=True)
            return [fake_pack_wasi(package_dir, Path(artifacts_dir) / f'node-{cfg.node_wasi}', out, cfg)]
        npm = FakeNpm()
        with mock.patch('npm_package.pack', fake_pack):
            self.wire(npm, environ={'GITHUB_ENV': str(github_env)})
        registry = self.root / '.companions' / 'hypercast-npm' / 'registry'
        self.assertEqual(packed, [(self.source, self.root / '.companions' / 'hypercast-artifacts', registry)])
        self.assertEqual(github_env.read_text(), f'EARLIER=1\nHYPERCAST_TARBALLS={registry}\n')
        # The package's own devDependency takes every addon in one job-local tarball, and
        # browser-smoke the set's WASI package.
        members = tar_members(self.root / TARBALL)
        self.assertEqual(len([m for m in members if m.endswith('.node')]), len(addons))
        self.assertEqual(json.loads(self.smoke.read_text())['devDependencies'][WASI_PACKAGE],
                         f'file:{(registry / "skunkwerkx-hypercast-wasm32-wasip1-1.0.0.tgz").as_posix()}')

    def test_wire_npm_is_idempotent(self):
        self.wire(FakeNpm())
        manifest = self.manifest.read_bytes()
        self.wire(FakeNpm())
        self.assertEqual(self.manifest.read_bytes(), manifest)

    @unittest.skipUnless(shutil.which('npm'), 'needs npm')
    def test_pack_without_files_list_has_no_addon(self):
        # Real npm: the committed whitelist packs no addon, so the tarball a plain `npm pack` makes
        # is one the origin check rejects; the wiring's job-local list is what puts it in.
        import companion_origin
        for name in json.loads((self.source / 'package.json').read_text())['files']:
            if name != 'package.json':
                (self.source / name).write_text(f'// {name}\n')
        shutil.copyfile(self.root / ADDON, self.source / 'hypercast.darwin-arm64.node')
        out = Path(self.tmp.name) / 'plain'
        out.mkdir()
        result = subprocess.run([shutil.which('npm'), 'pack', '--json', '--ignore-scripts', '--pack-destination',
                                 str(out)], cwd=self.source, capture_output=True, text=True, check=True)
        report = companion_wire._npm_pack_report(json.loads(result.stdout), PACKAGE)
        plain = out / report['filename']
        self.assertNotIn('package/hypercast.darwin-arm64.node', tar_members(plain))
        (self.source / 'hypercast.darwin-arm64.node').unlink()
        # The wiring, with real npm packing: the addon is in.
        self.wire(RealPackNpm())
        tarball = self.root / TARBALL
        self.assertIn('package/hypercast.darwin-arm64.node', tar_members(tarball))
        companion_origin.origin_npm(self.root, NPM_ENV, companion_origin.read_sums(self.root, NPM_ENV))
        # The plain tarball in its place, with a lock that agrees with it: rejected for the addon.
        shutil.copyfile(plain, tarball)
        lock_path = self.root / 'typescript' / 'package-lock.json'
        lock = json.loads(lock_path.read_text())
        lock['packages'][f'node_modules/{PACKAGE}']['integrity'] = npm_integrity(tarball)
        lock_path.write_text(json.dumps(lock))
        record_path = self.root / '.companions' / 'hypercast-npm' / 'wired.json'
        record = json.loads(record_path.read_text())
        record['packages'][PACKAGE]['integrity'] = npm_integrity(tarball)
        record_path.write_text(json.dumps(record))
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_npm(self.root, NPM_ENV, companion_origin.read_sums(self.root, NPM_ENV))
        self.assertIn('without hypercast.darwin-arm64.node', caught.exception.found)


# The Ruby leg's artifacts as fetch-companions stages them for osx-arm64 (both ABIs and the Fiddle
# library), and the pinned gem's own files.
RUBY_ARTIFACTS = {'ruby-native-osx-arm64-4.0/hypercast_native.bundle': b'magnus 4.0',
                  'ruby-native-osx-arm64-3.4/hypercast_native.bundle': b'magnus 3.4',
                  'native-osx-arm64/libhypercast.dylib': b'fiddle library'}
RUBY_ENV = {**ENV, 'HYPERCAST_RUBY_REQ': '.companions/hypercast/ruby'}
# build-ruby-wasm's leg (abi=4.0): HyperCast's own wasm32-wasip1 archive for that minor, and
# where suite-ruby-wasm.sh unpacks the hypercast-wasm gem the pin packs around it.
WASM_ARTIFACTS = {'ruby-wasm-4.0/hypercast_native.a': b'wasm 4.0 archive'}
WASM_GEM = '.companions/hypercast-wasm/hypercast-wasm-1.0.0'
GEMSPEC = 'ruby/hypertabular.gemspec'
# The PHP leg's: one PIE zip, holding the extension as its one entry.
PHP_ZIP = '.companions/hypercast-artifacts/php-ext-osx-arm64-8.5/php_hypercast-v1.0.0_php8.5-arm64-darwin-bsdlibc-nts.zip'
PHP_ENV = {**ENV, 'HYPERCAST_PHP_REQ': PHP_ZIP}
PHP_EXTENSION = '.companions/hypercast-php/hypercast.so'


def stage(root, files):
    """Artifacts staged under .companions/hypercast-artifacts/, their lines added to SHA256SUMS."""
    artifacts = Path(root) / '.companions' / 'hypercast-artifacts'
    with open(artifacts / 'SHA256SUMS', 'a') as sums:
        for relative, data in files.items():
            path = artifacts / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            sums.write(f'{hashlib.sha256(data).hexdigest()}  {relative}\n')


def ruby_checkout(root, artifacts=RUBY_ARTIFACTS):
    """HyperTabular's Ruby manifests, the pinned gem's source, and the leg's staged binaries."""
    root = checkout(root, 'ruby')
    gem = root / '.companions' / 'hypercast' / 'ruby'
    (gem / 'lib').mkdir(parents=True)
    (gem / 'hypercast.gemspec').write_text('Gem::Specification.new do |spec|\n  spec.name = "hypercast"\n'
                                            '  spec.version = "1.0.0"\nend\n')
    (gem / 'lib' / 'hypercast.rb').write_text('module HyperCast\n  VERSION = "1.0.0"\nend\n')
    stage(root, artifacts)
    return root


def php_zip(entries):
    buffer = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def php_checkout(root, entries=None):
    root = checkout(root, 'php')
    stage(root, {PHP_ZIP.split('hypercast-artifacts/', 1)[1]: php_zip(entries or {'hypercast.so': b'ext-hypercast'})})
    return root


class WireRubyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = ruby_checkout(Path(self.tmp.name) / 'consumer')
        self.gem = self.root / '.companions' / 'hypercast' / 'ruby'

    def wire(self, env=RUBY_ENV):
        companion_wire.wire_ruby(self.root, env)

    def changed(self, relative, before):
        after = (self.root / relative).read_text()
        return [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b], after

    def test_wire_ruby_pinned_passes(self):
        gemfile = (self.root / 'ruby' / 'Gemfile').read_text()
        gemspec = (self.root / GEMSPEC).read_text()
        smoke = (self.root / 'ruby' / 'wasm-smoke' / 'Gemfile').read_text()
        self.wire()
        # The Gemfile gains the path gem, and nothing else in it moves.
        after = (self.root / 'ruby' / 'Gemfile').read_text()
        self.assertTrue(after.startswith(gemfile))
        self.assertEqual(after[len(gemfile):].strip().splitlines()[-1], f'gem "hypercast", path: "{self.gem.as_posix()}"')
        # The gemspec's requirement (the release its code needs) becomes the pin's exact version.
        changed, _ = self.changed(GEMSPEC, gemspec)
        self.assertEqual(changed, [('  spec.add_dependency "hypercast", "~> 1.0"', '  spec.add_dependency "hypercast", "= 1.0.0"')])
        # The Gemfile with no requirement on the companion is left as committed.
        self.assertEqual((self.root / 'ruby' / 'wasm-smoke' / 'Gemfile').read_text(), smoke)
        # Each extension where lib/hypercast.rb requires it for its ABI, and the Fiddle library
        # in the gem's native/<rid>/.
        lib = self.gem / 'lib' / 'hypercast'
        self.assertEqual((lib / '4.0' / 'hypercast_native.bundle').read_bytes(), b'magnus 4.0')
        self.assertEqual((lib / '3.4' / 'hypercast_native.bundle').read_bytes(), b'magnus 3.4')
        self.assertEqual((lib / 'native' / 'osx-arm64' / 'libhypercast.dylib').read_bytes(), b'fiddle library')

    def test_ruby_literals_are_double_quoted_and_read_back(self):
        # The wired Gemfile is in the job's tree, which a consumer's RuboCop lints: the default
        # Style/StringLiterals refuses a single-quoted literal (HyperTabular's first pinned
        # board). Double quotes then escape `#` as well, or a path holding `#{` interpolates.
        import companion_origin
        for text in ('/home/runner/work/x/.companions/hypercast/ruby', 'a"b', "a'b", 'a#{x}b', 'a\\b'):
            with self.subTest(text=text):
                literal = companion_wire._ruby_quote(text)
                self.assertTrue(literal.startswith('"') and literal.endswith('"'), literal)
                self.assertEqual(companion_origin._ruby_string(literal[0], literal[1:-1]), text)
        # A single-quoted literal a consumer wrote still reads as Ruby reads it.
        self.assertEqual(companion_origin._ruby_string("'", "a\\'b\\\\c\\#"), "a'b\\c\\#")

    def test_wire_ruby_registry_copy_is_rejected(self):
        # A Gemfile that already takes the companion from rubygems.org (or from another path) is
        # one the wiring would have to override silently: refused, and left as it was.
        for line in ('gem "hypercast", "~> 1.0"\n', 'gem "hypercast", path: "../../HyperCast/ruby"\n'):
            with self.subTest(line=line):
                gemfile = self.root / 'ruby' / 'Gemfile'
                gemfile.write_text('source "https://rubygems.org"\ngemspec\n' + line)
                with self.assertRaises(ValueError) as caught:
                    self.wire()
                self.assertIn('ruby/Gemfile', str(caught.exception))
                self.assertEqual(gemfile.read_text(), 'source "https://rubygems.org"\ngemspec\n' + line)

    def test_wire_ruby_tampered_hash_is_rejected(self):
        (self.root / '.companions' / 'hypercast-artifacts' / 'ruby-native-osx-arm64-4.0' / 'hypercast_native.bundle').write_bytes(b'x')
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('ruby-native-osx-arm64-4.0/hypercast_native.bundle', str(caught.exception))
        self.assertFalse((self.gem / 'lib' / 'hypercast' / '4.0').exists())

    def test_wire_ruby_is_idempotent(self):
        self.wire()
        files = {p: p.read_bytes() for p in (self.root / 'ruby').rglob('*') if p.is_file()}
        self.wire()
        self.assertEqual({p: p.read_bytes() for p in (self.root / 'ruby').rglob('*') if p.is_file()}, files)

    def test_wire_ruby_refuses_a_binary_the_pin_already_holds(self):
        other = self.gem / 'lib' / 'hypercast' / '4.0' / 'hypercast_native.bundle'
        other.parent.mkdir(parents=True)
        other.write_bytes(b'built elsewhere')
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('4.0/hypercast_native.bundle', str(caught.exception))
        self.assertEqual(other.read_bytes(), b'built elsewhere')

    def test_wire_ruby_refuses_an_artifact_it_cannot_place(self):
        for relative in ('ruby-native-osx-arm64-4.0/other.bundle', 'ruby-native-osx-arm64/hypercast_native.bundle',
                         'native-osx-arm64/hypercast.h'):
            with self.subTest(relative=relative):
                root = ruby_checkout(Path(self.tmp.name) / relative.replace('/', '_'), {relative: b'x'})
                with self.assertRaises(ValueError):
                    companion_wire.wire_ruby(root, RUBY_ENV)

    def test_wire_ruby_without_binaries_rewrites_only(self):
        # The lint leg stages nothing: the gem's source alone is what Bundler resolves there.
        root = ruby_checkout(Path(self.tmp.name) / 'lint', {})
        companion_wire.wire_ruby(root, RUBY_ENV)
        self.assertIn('gem "hypercast", path:', (root / 'ruby' / 'Gemfile').read_text())
        self.assertFalse((root / '.companions' / 'hypercast' / 'ruby' / 'lib' / 'hypercast').exists())

    def test_wire_ruby_rewrites_every_form(self):
        spec = self.root / GEMSPEC
        spec.write_text('Gem::Specification.new do |spec|\n'
                        '  spec.add_dependency "hypercast", "~> 1.1"\n'
                        "  spec.add_runtime_dependency('hypercast', '>= 1.1', '< 2') # the judge\n"
                        '  spec.add_development_dependency "hypercast"\n'
                        '  spec.add_dependency "hypercast-wasm", "~> 1.1"\n'
                        '  spec.add_dependency "fiddle"\nend\n')
        self.wire()
        self.assertEqual(spec.read_text(), 'Gem::Specification.new do |spec|\n'
                                           '  spec.add_dependency "hypercast", "= 1.0.0"\n'
                                           "  spec.add_runtime_dependency('hypercast', \"= 1.0.0\") # the judge\n"
                                           '  spec.add_development_dependency "hypercast", "= 1.0.0"\n'
                                           '  spec.add_dependency "hypercast-wasm", "~> 1.1"\n'
                                           '  spec.add_dependency "fiddle"\nend\n')

    def test_wire_ruby_unknown_form_is_refused(self):
        spec = self.root / GEMSPEC
        for line in ('  spec.add_dependency "hypercast", REQUIREMENT\n', '  spec.add_dependency("hypercast",\n'):
            with self.subTest(line=line):
                spec.write_text('Gem::Specification.new do |spec|\n' + line + 'end\n')
                with self.assertRaises(ValueError) as caught:
                    self.wire()
                self.assertIn(GEMSPEC, str(caught.exception))
                self.assertEqual(spec.read_text(), 'Gem::Specification.new do |spec|\n' + line + 'end\n')

    def test_wire_ruby_needs_a_manifest_requiring_the_companion(self):
        (self.root / GEMSPEC).write_text('Gem::Specification.new do |spec|\nend\n')
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('requires hypercast', str(caught.exception))


class WireRubyWasmTests(unittest.TestCase):
    """build-ruby-wasm's leg: the minor's ruby-wasm-<abi> archive placed where the pin's
    `rake wasm:gem[<abi>]` packs it from, and the smoke Gemfile (the one reading WASM_GEM_DIR)
    taking <name>-wasm from where suite-ruby-wasm.sh unpacks that gem."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = ruby_checkout(Path(self.tmp.name) / 'consumer', WASM_ARTIFACTS)
        self.gem = self.root / '.companions' / 'hypercast' / 'ruby'
        self.smoke = self.root / 'ruby' / 'wasm-smoke' / 'Gemfile'

    def test_wire_ruby_wasm_places_the_archive_and_takes_the_wasm_gem_by_path(self):
        smoke = self.smoke.read_text()
        companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.assertEqual((self.gem / 'ext' / 'hypercast_native' / '4.0' / 'hypercast_native.a').read_bytes(),
                         b'wasm 4.0 archive')
        wasm = self.root / WASM_GEM
        self.assertEqual(companion_wire.ruby_wasm_gem(self.root, 'hypercast', '1.0.0'), wasm)
        after = self.smoke.read_text()
        self.assertTrue(after.startswith(smoke))
        self.assertEqual(after[len(smoke):].strip().splitlines()[-1], f'gem "hypercast-wasm", path: "{wasm.as_posix()}"')
        # The host's Gemfile still takes the pin, as on every Ruby leg.
        self.assertIn(f'gem "hypercast", path: "{self.gem.as_posix()}"', (self.root / 'ruby' / 'Gemfile').read_text())
        files = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, files)

    def test_wire_ruby_wasm_refuses_a_smoke_gemfile_naming_the_wasm_gem(self):
        line = 'gem "hypercast-wasm", "1.0.0"\n'
        self.smoke.write_text(self.smoke.read_text() + line)
        before = self.smoke.read_text()
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.assertIn('ruby/wasm-smoke/Gemfile already names hypercast-wasm', str(caught.exception))
        self.assertEqual(self.smoke.read_text(), before)
        self.assertFalse((self.gem / 'ext').exists())

    def test_wire_ruby_wasm_needs_exactly_one_smoke_gemfile(self):
        self.smoke.unlink()
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.assertIn('WASM_GEM_DIR', str(caught.exception))
        other = self.root / 'ruby' / 'other-smoke' / 'Gemfile'
        other.parent.mkdir()
        shutil.copyfile(FIXTURES / 'ruby' / 'ruby' / 'wasm-smoke' / 'Gemfile', other)
        shutil.copyfile(other, self.smoke)
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.assertIn('2 Gemfiles', str(caught.exception))

    def test_wire_ruby_wasm_tampered_hash_is_rejected(self):
        (self.root / '.companions' / 'hypercast-artifacts' / 'ruby-wasm-4.0' / 'hypercast_native.a').write_bytes(b'x')
        smoke = self.smoke.read_text()
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.assertIn('ruby-wasm-4.0/hypercast_native.a', str(caught.exception))
        self.assertFalse((self.gem / 'ext').exists())
        self.assertEqual(self.smoke.read_text(), smoke)

    def test_wire_ruby_wasm_refuses_an_artifact_it_cannot_place(self):
        for relative in ('ruby-wasm-4.0/other.a', 'ruby-wasm/hypercast_native.a', 'ruby-wasm-4.0/x/hypercast_native.a'):
            with self.subTest(relative=relative):
                root = ruby_checkout(Path(self.tmp.name) / relative.replace('/', '_'), {relative: b'x'})
                with self.assertRaises(ValueError):
                    companion_wire.wire_ruby(root, RUBY_ENV)

    def test_wire_ruby_without_the_wasm_archive_leaves_the_smoke_gemfile(self):
        root = ruby_checkout(Path(self.tmp.name) / 'native', RUBY_ARTIFACTS)
        smoke = (root / 'ruby' / 'wasm-smoke' / 'Gemfile').read_text()
        companion_wire.wire_ruby(root, RUBY_ENV)
        self.assertEqual((root / 'ruby' / 'wasm-smoke' / 'Gemfile').read_text(), smoke)


class WirePhpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = php_checkout(Path(self.tmp.name) / 'consumer')
        self.github_env = Path(self.tmp.name) / 'github_env'

    def wire(self, environ=None):
        companion_wire.wire_php(self.root, PHP_ENV,
                                environ={'GITHUB_ENV': str(self.github_env)} if environ is None else environ)

    def test_wire_php_pinned_passes(self):
        manifests = {p: p.read_bytes() for p in self.root.rglob('composer.json')}
        self.wire()
        extension = self.root / PHP_EXTENSION
        self.assertEqual(extension.read_bytes(), b'ext-hypercast')
        # What the suite's tools/extension.php loads, and Composer told to ignore ext-hypercast's
        # version, for every later step; the committed manifests unchanged.
        self.assertEqual(self.github_env.read_text(), f'HYPERCAST_PHP_EXTENSION={extension.as_posix()}\n'
                                                      'COMPOSER_IGNORE_PLATFORM_REQ=ext-hypercast\n')
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('composer.json')}, manifests)

    def test_wire_php_registry_copy_is_rejected(self):
        # The legacy registry build names its own extension in the same variable: one or the other.
        with self.assertRaises(ValueError) as caught:
            self.wire({'GITHUB_ENV': str(self.github_env), 'HYPERCAST_PHP_EXTENSION': '/runner/php-ext/hypercast.so'})
        self.assertIn('/runner/php-ext/hypercast.so', str(caught.exception))
        self.assertFalse(self.github_env.exists())

    def test_wire_php_tampered_hash_is_rejected(self):
        (self.root / PHP_ZIP).write_bytes(php_zip({'hypercast.so': b'another build'}))
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('php_hypercast-v1.0.0_php8.5-arm64-darwin-bsdlibc-nts.zip', str(caught.exception))
        self.assertFalse((self.root / PHP_EXTENSION).exists())

    def test_wire_php_takes_one_zip_with_one_extension(self):
        for entries in ({'hypercast.so': b'a', 'README': b'b'}, {'other.so': b'a'}):
            with self.subTest(entries=sorted(entries)):
                root = php_checkout(Path(self.tmp.name) / '_'.join(sorted(entries)), entries)
                with self.assertRaises(ValueError):
                    companion_wire.wire_php(root, PHP_ENV, environ={})
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_php(self.root, {**PHP_ENV, 'HYPERCAST_PHP_REQ': ''}, environ={})
        self.assertIn('no pinned PHP extension', str(caught.exception))

    def test_wire_php_windows_entry(self):
        root = php_checkout(Path(self.tmp.name) / 'win', {'php_hypercast-v1.0.0-8.5-nts-vs17-x86_64.dll': b'dll'})
        companion_wire.wire_php(root, PHP_ENV, environ={'GITHUB_ENV': str(self.github_env)})
        self.assertIn('hypercast-php/php_hypercast-v1.0.0-8.5-nts-vs17-x86_64.dll\n', self.github_env.read_text())

    def test_wire_php_without_github_env_prints_the_variables(self):
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.wire({})
        self.assertIn(f'HYPERCAST_PHP_EXTENSION={(self.root / PHP_EXTENSION).as_posix()}', out.getvalue())


# The Java leg's artifacts as fetch-companions stages them for osx-arm64: the JNI shim and the
# core, which HyperCast's Gradle packs from src/main/resources/native/<rid>/.
JAVA_ARTIFACTS = {'jni-osx-arm64/libhypercast_jni.dylib': b'jni shim', 'native-osx-arm64/libhypercast.dylib': b'core'}
JAVA_ENV = {**ENV, 'HYPERCAST_JAVA_REQ': '.companions/hypercast/java'}
JAR = '.companions/m2/io/github/skunkwerkx/hypercast/1.0.0/hypercast-1.0.0.jar'
JAVA_RECORD = '.companions/hypercast-java.record'
# The C# legs': the desktop one (osx-arm64: the core and the static-libs artifact, laid out by
# HyperCast's repository paths and carrying Swift's archives too), the Blazor one (the
# browser-wasm archive) and the Android one (both Android RIDs' cores).
CSHARP_ARTIFACTS = {'native-osx-arm64/libhypercast.dylib': b'core',
                    'static-libs/csharp/HyperCast/staticlibs/osx-arm64/libhypercast.a': b'static osx-arm64',
                    'static-libs/csharp/HyperCast/staticlibs/linux-x64/libhypercast.a': b'static linux-x64',
                    'static-libs/swift/HyperCastCore.artifactbundle/arm64-apple-macosx/libhypercast.a': b'swift'}
BLAZOR_ARTIFACTS = {'wasm-emscripten-staticlib/libhypercast.a': b'browser-wasm archive'}
ANDROID_ARTIFACTS = {'native-android-arm64/libhypercast.so': b'android arm64',
                     'native-android-x64/libhypercast.so': b'android x64',
                     'static-libs/csharp/HyperCast/staticlibs/android-arm64/libhypercast.a': b'static android'}
# The Apple mobile leg's: the static-libs artifact alone (Native AOT links the archive).
APPLE_ARTIFACTS = {'static-libs/csharp/HyperCast/staticlibs/ios-arm64/libhypercast.a': b'static ios-arm64',
                   'static-libs/swift/HyperCastCore.artifactbundle/arm64-apple-ios/libhypercast.a': b'swift'}
CSHARP_ENV = {**ENV, 'HYPERCAST_CSHARP_REQ': '.companions/hypercast/csharp'}
NUPKG = '.companions/nuget/HyperCast.1.0.0.nupkg'
CSHARP_RECORD = '.companions/hypercast-csharp.record'
# Where each staged artifact lands in the package (HyperCast's pack job's layout).
CSHARP_PATHS = {'native-osx-arm64/libhypercast.dylib': 'runtimes/osx-arm64/native/libhypercast.dylib',
                'static-libs/csharp/HyperCast/staticlibs/osx-arm64/libhypercast.a': 'staticlibs/osx-arm64/libhypercast.a',
                'static-libs/csharp/HyperCast/staticlibs/linux-x64/libhypercast.a': 'staticlibs/linux-x64/libhypercast.a',
                'wasm-emscripten-staticlib/libhypercast.a': 'runtimes/browser-wasm/nativeassets/net11.0/libhypercast.a',
                'native-android-arm64/libhypercast.so': 'runtimes/android-arm64/native/libhypercast.so',
                'native-android-x64/libhypercast.so': 'runtimes/android-x64/native/libhypercast.so',
                'static-libs/csharp/HyperCast/staticlibs/android-arm64/libhypercast.a': 'staticlibs/android-arm64/libhypercast.a',
                'static-libs/csharp/HyperCast/staticlibs/ios-arm64/libhypercast.a': 'staticlibs/ios-arm64/libhypercast.a'}


def java_checkout(root, artifacts=JAVA_ARTIFACTS):
    """HyperTabular's Gradle build, HyperCast's own at the pin, and the leg's staged natives."""
    root = checkout(root, 'java')
    shutil.copytree(FIXTURES / 'java-pinned', root / '.companions' / 'hypercast', dirs_exist_ok=True)
    stage(root, artifacts)
    return root


def csharp_checkout(root, artifacts=CSHARP_ARTIFACTS):
    """HyperTabular's C# manifests, HyperCast's package project and hyper.json at the pin, and the
    leg's staged artifacts."""
    root = checkout(root, 'csharp')
    shutil.copytree(FIXTURES / 'csharp-pinned', root / '.companions' / 'hypercast', dirs_exist_ok=True)
    stage(root, artifacts)
    return root


def write_zip(path, entries):
    """A zip of `entries`, each stamped with one fixed time, so packing the same tree twice gives
    the same bytes (a name alone takes the clock's time, and two packs either side of a
    two-second tick would differ)."""
    import zipfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data)


def zip_entries(path):
    import zipfile
    with zipfile.ZipFile(path) as archive:
        return {i.filename: archive.read(i) for i in archive.infolist() if not i.is_dir()}


class FakeGradle(FakeTool):
    """`./gradlew ... -Dmaven.repo.local=<m2> publishToMavenLocal`: the jar, with what the pinned
    tree holds under src/main/resources/ (the staged natives), where Maven's layout puts it."""
    def __init__(self, extra=None, drop=()):
        super().__init__(self.publish)
        self.extra, self.drop = extra or {}, drop

    def publish(self, argv, cwd):
        m2 = Path(next(a for a in argv if a.startswith('-Dmaven.repo.local=')).split('=', 1)[1])
        resources = Path(cwd) / 'src' / 'main' / 'resources'
        entries = {'io/github/skunkwerkx/hypercast/Cast.class': b'class'}
        entries.update({p.relative_to(resources).as_posix(): p.read_bytes()
                        for p in sorted(resources.rglob('*')) if p.is_file()})
        entries.update(self.extra)
        write_zip(m2 / 'io/github/skunkwerkx/hypercast/1.0.0/hypercast-1.0.0.jar',
                  {k: v for k, v in entries.items() if k not in self.drop})


class FakeDotnetPack(FakeTool):
    """`dotnet pack <csproj> -c Release -o <feed>`: a nupkg of what the package project holds under
    runtimes/ and staticlibs/."""
    def __init__(self, extra=None, version='1.0.0'):
        super().__init__(self.pack)
        self.extra, self.version = extra or {}, version

    def pack(self, argv, cwd):
        project = Path(argv[2]).parent
        entries = {'HyperCast.nuspec': b'<package/>', 'lib/net10.0/HyperCast.Managed.dll': b'managed'}
        entries.update({p.relative_to(project).as_posix(): p.read_bytes()
                        for d in ('runtimes', 'staticlibs') for p in sorted((project / d).rglob('*')) if p.is_file()})
        entries.update(self.extra)
        write_zip(Path(argv[argv.index('-o') + 1]) / f'HyperCast.{self.version}.nupkg', entries)


class WireJavaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = java_checkout(Path(self.tmp.name) / 'consumer')
        self.gradle_home = Path(self.tmp.name) / 'gradle-home'
        self.pin = self.root / '.companions' / 'hypercast' / 'java'

    def wire(self, tool=None, root=None):
        tool = tool or FakeGradle()
        companion_wire.wire_java(root or self.root, JAVA_ENV, run=tool,
                                 environ={'GRADLE_USER_HOME': str(self.gradle_home)})
        return tool

    def init_script(self):
        return (self.gradle_home / 'init.d' / 'companions.gradle').read_text()

    def properties(self):
        return (self.gradle_home / 'init.d' / 'companions.properties').read_text(encoding='latin-1')

    def test_wire_java_pinned_passes(self):
        manifests = {p: p.read_bytes() for p in (self.root / 'java').rglob('*') if p.is_file()}
        tool = self.wire()
        # The leg's natives where HyperCast's Gradle packs them, then its publishToMavenLocal into
        # the job-local repository (signing not required: a job-local repository has no key).
        native = self.pin / 'src' / 'main' / 'resources' / 'native' / 'osx-arm64'
        self.assertEqual((native / 'libhypercast_jni.dylib').read_bytes(), b'jni shim')
        self.assertEqual((native / 'libhypercast.dylib').read_bytes(), b'core')
        [(argv, cwd)] = tool.calls
        self.assertEqual(Path(cwd), self.pin)
        self.assertEqual(Path(argv[0]).name, 'gradlew.bat' if os.name == 'nt' else 'gradlew')
        self.assertIn(f'-Dmaven.repo.local={(self.root / ".companions" / "m2").as_posix()}', argv)
        self.assertEqual(argv[argv.index('--init-script') + 1], str(SCRIPTS / 'companion_java_publish.gradle'))
        self.assertEqual(argv[-1], 'publishToMavenLocal')
        # The record of what was published: the coordinate (HyperCast's group and project name),
        # the pin's version and the jar's SHA-256.
        jar = self.root / JAR
        record = dict(line.split('=', 1) for line in (self.root / JAVA_RECORD).read_text().splitlines())
        self.assertEqual(record, {'group': 'io.github.skunkwerkx', 'module': 'hypercast', 'version': '1.0.0',
                                  'jar': JAR, 'sha256': hashlib.sha256(jar.read_bytes()).hexdigest()})
        # The init script is the Forge's, unchanged: no data becomes Groovy source. Its data, that
        # repository, module, version and the jar's hash, is the properties file beside it,
        # which the script loads (java.util.Properties).
        self.assertEqual(self.init_script(), (SCRIPTS / 'companion_java.init.gradle').read_text())
        self.assertEqual(companion_wire.read_java_properties(self.properties()),
                         {'repository': (self.root / '.companions' / 'm2').as_posix(), 'group': 'io.github.skunkwerkx',
                          'module': 'hypercast', 'version': '1.0.0', 'sha256': record['sha256']})
        # The consumer's own build files are left as committed: the force is the init script's.
        self.assertEqual({p: p.read_bytes() for p in (self.root / 'java').rglob('*') if p.is_file()}, manifests)

    def test_wire_java_registry_copy_is_rejected(self):
        # A publish that leaves no jar at the pin's coordinate (another version, another name)
        # would leave the build resolving whatever its repositories have: refused, no init script.
        def elsewhere(argv, cwd):
            FakeGradle().publish(argv, cwd)
            jar = self.root / JAR
            jar.rename(jar.with_name('hypercast-1.0.0-other.jar'))
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeTool(elsewhere))
        self.assertIn('hypercast-1.0.0.jar', str(caught.exception))
        self.assertFalse((self.gradle_home / 'init.d' / 'companions.gradle').exists())

    def test_wire_java_tampered_hash_is_rejected(self):
        artifacts = self.root / '.companions' / 'hypercast-artifacts'
        (artifacts / 'jni-osx-arm64' / 'libhypercast_jni.dylib').write_bytes(b'another build')
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('jni-osx-arm64/libhypercast_jni.dylib', str(caught.exception))
        self.assertFalse((self.root / '.companions' / 'm2').exists())

    def test_wire_java_jar_without_the_staged_natives_is_rejected(self):
        # The jar is read back: one missing a staged library, or carrying another RID's, is not
        # the leg's.
        for tool, needle in ((FakeGradle(drop=('native/osx-arm64/libhypercast_jni.dylib',)),
                              'native/osx-arm64/libhypercast_jni.dylib'),
                             (FakeGradle({'native/linux-x64/libhypercast.so': b'x'}), 'native/linux-x64/libhypercast.so'),
                             (FakeGradle({'native/osx-arm64/libhypercast.dylib': b'other'}),
                              'native/osx-arm64/libhypercast.dylib')):
            with self.subTest(needle=needle):
                with self.assertRaises(ValueError) as caught:
                    self.wire(tool)
                self.assertIn(needle, str(caught.exception))
                self.assertFalse((self.gradle_home / 'init.d' / 'companions.gradle').exists())

    def test_wire_java_needs_the_natives(self):
        root = java_checkout(Path(self.tmp.name) / 'bare', {})
        with self.assertRaises(ValueError) as caught:
            self.wire(root=root)
        self.assertIn('native-', str(caught.exception))
        for relative in ('jni-osx-arm64/hypercast.h', 'native-osx-arm64/sub/libhypercast.dylib'):
            with self.subTest(relative=relative):
                root = java_checkout(Path(self.tmp.name) / relative.replace('/', '_'), {**JAVA_ARTIFACTS, relative: b'x'})
                with self.assertRaises(ValueError):
                    self.wire(root=root)

    def test_wire_java_is_idempotent(self):
        self.wire()
        first = (self.init_script(), self.properties(), (self.root / JAVA_RECORD).read_text())
        self.wire()
        self.assertEqual((self.init_script(), self.properties(), (self.root / JAVA_RECORD).read_text()), first)

    def test_wire_java_holds_any_path_as_data(self):
        # A quote, a dollar, a backslash, a field name of the old template and a non-ASCII letter:
        # data in a properties file, never Groovy source, and read back exactly.
        root = java_checkout(Path(self.tmp.name) / "it's $HOME @VERSION@ \\ é=:#!")
        self.wire(root=root)
        self.assertEqual(self.init_script(), (SCRIPTS / 'companion_java.init.gradle').read_text())
        values = companion_wire.read_java_properties(self.properties())
        self.assertEqual(values['repository'], (root / '.companions' / 'm2').resolve().as_posix())
        self.assertEqual(values['version'], '1.0.0')
        self.assertTrue(self.properties().isascii())
        # The escaping java.util.Properties reads (a JDK's Properties.load read these back
        # exactly): every special character escaped, everything outside ASCII as \\uXXXX.
        for value, escaped in (("a=b", "a\\=b"), ("a:b", "a\\:b"), ("#a", "\\#a"), ("!a", "\\!a"),
                               (" lead", "\\ lead"), ("back\\slash", "back\\\\slash"),
                               ("line\nbreak", "line\\nbreak"), ("é", "\\u00e9"), ("\u2028", "\\u2028"),
                               ("\U0001F600", "\\ud83d\\ude00")):
            with self.subTest(value=value):
                text = companion_wire.java_properties(value, 'g', 'm', 'v', 's')
                self.assertIn(f'\nrepository={escaped}\n', text)
                self.assertEqual(companion_wire.read_java_properties(text)['repository'], value)


class WireCsharpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = csharp_checkout(Path(self.tmp.name) / 'consumer')
        self.package = self.root / '.companions' / 'hypercast' / 'csharp' / 'HyperCast'

    def wire(self, tool=None, root=None):
        tool = tool or FakeDotnetPack()
        companion_wire.wire_csharp(root or self.root, CSHARP_ENV, run=tool, environ={})
        return tool

    def test_wire_csharp_pinned_passes(self):
        csproj = (self.root / 'csharp' / 'HyperTabular' / 'HyperTabular.csproj').read_text()
        props = (self.root / 'csharp' / 'Directory.Build.props').read_bytes()
        tool = self.wire()
        # HyperCast packed as its pack job packs it, into the job-local feed, with the leg's
        # natives where that job lays them; Swift's archives in static-libs are not C#'s.
        [(argv, cwd)] = tool.calls
        self.assertEqual(argv, ['dotnet', 'pack', str(self.package / 'HyperCast.csproj'), '-c', 'Release', '-o',
                                str(self.root / '.companions' / 'nuget')])
        self.assertEqual(Path(cwd), self.package.parent)
        entries = zip_entries(self.root / NUPKG)
        for staged, data in CSHARP_ARTIFACTS.items():
            if staged in CSHARP_PATHS:
                self.assertEqual(entries[CSHARP_PATHS[staged]], data)
        self.assertNotIn('swift', ' '.join(entries))
        # The requirement (1.1.0, the release HyperTabular's code needs) becomes the pin's exact
        # version, and that one line is the only change.
        after = (self.root / 'csharp' / 'HyperTabular' / 'HyperTabular.csproj').read_text()
        changed = [(a, b) for a, b in zip(csproj.splitlines(), after.splitlines()) if a != b]
        self.assertEqual(changed, [('    <PackageReference Include="HyperCast" Version="1.1.0" />',
                                    '    <PackageReference Include="HyperCast" Version="[1.0.0]" />')])
        self.assertEqual((self.root / 'csharp' / 'Directory.Build.props').read_bytes(), props)
        # The record: the package, its version, the nupkg's SHA-512 and NuGet's contentHash of it.
        nupkg = (self.root / NUPKG).read_bytes()
        record = dict(line.split('=', 1) for line in (self.root / CSHARP_RECORD).read_text().splitlines())
        import base64
        self.assertEqual(record, {'package': 'HyperCast', 'version': '1.0.0', 'nupkg': NUPKG,
                                  'sha512': hashlib.sha512(nupkg).hexdigest(),
                                  'content_hash': base64.b64encode(hashlib.sha512(nupkg).digest()).decode()})
        # nuget.config at the checkout's root: the feed the only source for HyperCast, nuget.org
        # every other package's.
        import xml.etree.ElementTree as ET
        config = ET.parse(self.root / 'nuget.config').getroot()
        sources = {e.get('key'): e.get('value') for e in config.find('packageSources') if e.tag == 'add'}
        self.assertEqual(sources, {'companions': (self.root / '.companions' / 'nuget').as_posix(),
                                   'nuget.org': 'https://api.nuget.org/v3/index.json'})
        self.assertEqual(config.find('packageSources')[0].tag, 'clear')
        mapping = {s.get('key'): [p.get('pattern') for p in s] for s in config.find('packageSourceMapping')}
        self.assertEqual(mapping, {'companions': ['HyperCast'], 'nuget.org': ['*']})

    def test_wire_csharp_keeps_the_pin_out_of_the_shared_global_packages_folder(self):
        # NuGet takes a package already in the global packages folder whatever its source: one
        # holding nuget.org's HyperCast at the pinned version (a cache, a runner's) would win over
        # the feed. Later steps restore into a job-local folder instead, over any NUGET_PACKAGES.
        from contextlib import redirect_stdout
        github_env = Path(self.tmp.name) / 'github_env'
        github_env.write_text('EARLIER=1\n')
        companion_wire.wire_csharp(self.root, CSHARP_ENV, run=FakeDotnetPack(),
                                   environ={'GITHUB_ENV': str(github_env), 'NUGET_PACKAGES': '/shared/nuget'})
        folder = self.root.resolve() / '.companions' / 'nuget-packages'
        self.assertEqual(github_env.read_text(), f'EARLIER=1\nNUGET_PACKAGES={folder.as_posix()}\n')
        out = io.StringIO()
        with redirect_stdout(out):
            companion_wire.wire_csharp(self.root, CSHARP_ENV, run=FakeDotnetPack(), environ={})
        self.assertIn(f'NUGET_PACKAGES={folder.as_posix()}\n', out.getvalue())

    def test_wire_csharp_blazor_android_and_apple_legs(self):
        for artifacts in (BLAZOR_ARTIFACTS, ANDROID_ARTIFACTS, APPLE_ARTIFACTS):
            with self.subTest(artifacts=sorted(artifacts)):
                root = csharp_checkout(Path(self.tmp.name) / sorted(artifacts)[0].replace('/', '_'), artifacts)
                self.wire(root=root)
                entries = zip_entries(root / NUPKG)
                self.assertEqual({k: v for k, v in entries.items() if k.startswith(('runtimes/', 'staticlibs/'))},
                                 {CSHARP_PATHS[k]: v for k, v in artifacts.items() if k in CSHARP_PATHS})

    def test_wire_csharp_registry_copy_is_rejected(self):
        # A nuget.config of the consumer's own would merge with the generated one (a <clear/> in
        # it would drop the feed): refused, and nothing is written.
        own = self.root / 'csharp' / 'NuGet.Config'
        own.write_text('<configuration><packageSources><clear /></packageSources></configuration>\n')
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('csharp/NuGet.Config', str(caught.exception))
        self.assertFalse((self.root / 'nuget.config').exists())
        self.assertIn('Version="1.1.0"', (self.root / 'csharp' / 'HyperTabular' / 'HyperTabular.csproj').read_text())
        # A pack that leaves no package at the pin's version leaves nothing for the feed to serve.
        own.unlink()
        with self.assertRaises(ValueError) as caught:
            self.wire(FakeDotnetPack(version='1.1.0'))
        self.assertIn('HyperCast.1.0.0.nupkg', str(caught.exception))

    def test_wire_csharp_tampered_hash_is_rejected(self):
        artifacts = self.root / '.companions' / 'hypercast-artifacts'
        (artifacts / 'native-osx-arm64' / 'libhypercast.dylib').write_bytes(b'another build')
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('native-osx-arm64/libhypercast.dylib', str(caught.exception))
        self.assertFalse((self.root / '.companions' / 'nuget').exists())

    def test_wire_csharp_package_without_the_staged_natives_is_rejected(self):
        for extra, needle in (({'runtimes/linux-x64/native/libhypercast.so': b'x'},
                               'runtimes/linux-x64/native/libhypercast.so'),
                              ({'staticlibs/osx-arm64/libhypercast.a': b'other'}, 'staticlibs/osx-arm64/libhypercast.a')):
            with self.subTest(needle=needle):
                with self.assertRaises(ValueError) as caught:
                    self.wire(FakeDotnetPack(extra))
                self.assertIn(needle, str(caught.exception))
                self.assertFalse((self.root / 'nuget.config').exists())

    def test_wire_csharp_rewrites_every_form(self):
        root = self.root
        (root / 'csharp' / 'Directory.Packages.props').write_text(
            '<Project>\n  <ItemGroup>\n    <PackageVersion Version="1.1.0" Include="HyperCast" />\n'
            '    <PackageVersion Include="Shouldly" Version="4.0.0" />\n  </ItemGroup>\n</Project>\n')
        (root / 'csharp' / 'Other.props').write_text(
            '<Project>\n  <PropertyGroup>\n    <HyperCastVersion>1.1.0</HyperCastVersion>\n  </PropertyGroup>\n'
            '  <ItemGroup>\n    <PackageReference Include="hypercast" Version="$(HyperCastVersion)" />\n'
            '    <PackageReference Include="HyperCast.Extra" Version="1.1.0" />\n'
            # Central management's per-project override, and an item updated rather than included.
            '    <PackageReference Include="HyperCast" VersionOverride="1.1.0" />\n'
            '    <PackageReference Update="HYPERCAST" Version="1.1.0" PrivateAssets="all" />\n'
            '    <PackageReference Update="HyperCast" PrivateAssets="all" />\n'
            '    <PackageVersion Update="HyperCast" VersionOverride="$(HyperCastVersion)" />\n'
            '  </ItemGroup>\n</Project>\n')
        self.wire()
        packages = (root / 'csharp' / 'Directory.Packages.props').read_text()
        self.assertIn('<PackageVersion Version="[1.0.0]" Include="HyperCast" />', packages)
        self.assertIn('<PackageVersion Include="Shouldly" Version="4.0.0" />', packages)
        other = (root / 'csharp' / 'Other.props').read_text()
        self.assertIn('<HyperCastVersion>[1.0.0]</HyperCastVersion>', other)
        self.assertIn('Version="$(HyperCastVersion)"', other)
        self.assertIn('<PackageReference Include="HyperCast.Extra" Version="1.1.0" />', other)
        self.assertIn('<PackageReference Include="HyperCast" VersionOverride="[1.0.0]" />', other)
        self.assertIn('<PackageReference Update="HYPERCAST" Version="[1.0.0]" PrivateAssets="all" />', other)
        self.assertIn('<PackageReference Update="HyperCast" PrivateAssets="all" />', other)
        self.assertIn('<PackageVersion Update="HyperCast" VersionOverride="$(HyperCastVersion)" />', other)

    def test_wire_csharp_unknown_form_is_refused(self):
        project = self.root / 'csharp' / 'HyperTabular' / 'HyperTabular.csproj'
        for text in ('<PackageReference Include="HyperCast"><Version>1.1.0</Version></PackageReference>',
                     '<PackageReference Include="HyperCast" Version="$(Elsewhere)" />',
                     '<PackageReference Include="HyperCast" VersionOverride="$(Elsewhere)" />',
                     '<PackageReference Update="HyperCast"><VersionOverride>1.1.0</VersionOverride></PackageReference>'):
            with self.subTest(text=text):
                project.write_text(f'<Project>\n  <ItemGroup>\n    {text}\n  </ItemGroup>\n</Project>\n')
                with self.assertRaises(ValueError) as caught:
                    self.wire()
                self.assertIn('HyperTabular.csproj', str(caught.exception))
                self.assertIn(text, project.read_text())

    def test_wire_csharp_needs_an_artifact_and_a_requirement(self):
        root = csharp_checkout(Path(self.tmp.name) / 'bare', {'c-packages-ci/hypercast-1.0.0-ios-arm64.tar.gz': b'c'})
        with self.assertRaises(ValueError) as caught:
            self.wire(root=root)
        self.assertIn('native-', str(caught.exception))
        (self.root / 'csharp' / 'HyperTabular' / 'HyperTabular.csproj').write_text('<Project />\n')
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('requires HyperCast', str(caught.exception))

    def test_wire_csharp_is_idempotent(self):
        self.wire()
        paths = [p for p in (self.root / 'csharp').rglob('*') if p.is_file()]
        paths += [self.root / 'nuget.config', self.root / CSHARP_RECORD]
        files = {p: p.read_bytes() for p in paths}
        self.wire()
        self.assertEqual({p: p.read_bytes() for p in paths}, files)


# The Swift leg's: the two zips swift-release-assets holds, each a tree under its own name (the
# Forge's swift_release.py zip), and the pin's own hyper.json, which names them.
SWIFT_ENV = {**ENV, 'HYPERCAST_SWIFT_REQ': '.companions/hypercast'}
SWIFT_ASSETS = '.companions/hypercast-artifacts/swift-release-assets'
BUNDLE = {'HyperCastCore.artifactbundle/info.json': b'{"artifacts": "pinned"}',
          'HyperCastCore.artifactbundle/arm64-apple-macosx/libhypercast.a': b'pinned macOS archive',
          'HyperCastCore.artifactbundle/x86_64-unknown-linux-gnu/libhypercast.a': b'pinned glibc archive',
          'HyperCastCore.artifactbundle/include/hypercast.h': b'/* header */',
          'HyperCastCore.artifactbundle/include/module.modulemap': b'module HyperCastCore {}'}
XCFRAMEWORK = {'HyperCastCoreApple.xcframework/Info.plist': b'<plist/>',
               'HyperCastCoreApple.xcframework/ios-arm64/libhypercast.a': b'pinned iOS archive',
               'HyperCastCoreApple.xcframework/ios-arm64/Headers/HyperCastCore/hypercast.h': b'/* header */'}
SWIFT_RECORD = '.companions/hypercast-swift.sums'
HYPERCAST_CONFIG = {'crate': 'hypercast', 'project': 'HyperCast', 'swift_remote': True, 'c_assets': True,
                    'exports': {'sources': ['rust/src/ffi.rs'], 'patterns': ['x'], 'prefixes': ['cast_']}}


def zip_of(files, links=(), modes=None):
    """A zip as swift_release.zip_tree writes one: the tree's directories, then its files; `links`
    names entries stored as symlinks, and `modes` gives an entry's permission bits (else 0644)."""
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        dirs = sorted({'/'.join(name.split('/')[:i]) + '/' for name in files for i in range(1, name.count('/') + 1)})
        for name in dirs:
            archive.writestr(zipfile.ZipInfo(name), b'')
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o120777 if name in links else 0o100000 | (modes or {}).get(name, 0o644)) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def swift_checkout(root, bundle=None, xcframework=None):
    """HyperTabular's three Swift manifests, the pin's source as its checkout holds it (a stale,
    committed bundle beside the include/ that is source), and the leg's two staged zips."""
    root = checkout(root, 'swift')
    pin = root / '.companions' / 'hypercast'
    (pin / '.github').mkdir()
    (pin / '.github' / 'hyper.json').write_text(json.dumps(HYPERCAST_CONFIG))
    stale = pin / 'swift' / 'HyperCastCore.artifactbundle'
    (stale / 'arm64-apple-macosx').mkdir(parents=True)
    (stale / 'arm64-apple-macosx' / 'libhypercast.a').write_bytes(b'a stale committed archive')
    (stale / 'aarch64-unknown-linux-gnu').mkdir()
    (stale / 'aarch64-unknown-linux-gnu' / 'libhypercast.a').write_bytes(b'another stale archive')
    (stale / 'info.json').write_text('stale')
    (pin / 'Package.swift').write_text('// swift-tools-version:6.2\n')
    stage(root, {'swift-release-assets/HyperCastCore.artifactbundle.zip': zip_of(BUNDLE if bundle is None else bundle),
                 'swift-release-assets/HyperCastCoreApple.xcframework.zip':
                     zip_of(XCFRAMEWORK if xcframework is None else xcframework)})
    return root


HYPERCAST_URL = '.package(url: "https://github.com/SkunkWerkx/HyperCast", from: "1.1.0")'


class WireSwiftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = swift_checkout(Path(self.tmp.name) / 'consumer')
        self.pin = self.root / '.companions' / 'hypercast'

    def wire(self, root=None):
        companion_wire.wire_swift(root or self.root, SWIFT_ENV)

    def manifests(self, root=None):
        return {p.relative_to(root or self.root).as_posix(): p.read_text()
                for p in sorted((root or self.root).rglob('Package.swift')) if '.companions' not in p.parts}

    def test_wire_swift_pinned_passes(self):
        before = self.manifests()
        self.wire()
        after = self.manifests()
        # Each manifest naming the companion takes the pin's root package by a path relative to
        # itself (a container that copies the tree resolves the same one); that call is the only
        # change. The smoke test, which takes the consumer's own root by path, is left.
        self.assertEqual(after['Package.swift'],
                         before['Package.swift'].replace(HYPERCAST_URL, '.package(path: ".companions/hypercast")'))
        self.assertEqual(after['swift/Package.swift'],
                         before['swift/Package.swift'].replace(HYPERCAST_URL, '.package(path: "../.companions/hypercast")'))
        self.assertEqual(after['swift/StaticSmokeTest/Package.swift'], before['swift/StaticSmokeTest/Package.swift'])
        # The comment naming a `.package(url:` is not a requirement and stays.
        self.assertIn('`.package(url: "https://github.com/SkunkWerkx/HyperTabular", ...)`', after['Package.swift'])
        # Both trees are the zips', whole: the stale committed archives are gone.
        for files in (BUNDLE, XCFRAMEWORK):
            for name, data in files.items():
                self.assertEqual((self.pin / 'swift' / name).read_bytes(), data)
        held = sorted(p.relative_to(self.pin / 'swift').as_posix() for p in (self.pin / 'swift').rglob('*') if p.is_file())
        self.assertEqual(held, sorted([*BUNDLE, *XCFRAMEWORK]))
        # The extracted files' hashes, for the containers' `sha256sum -c` (relative to the pin).
        record = (self.root / SWIFT_RECORD).read_text().splitlines()
        self.assertEqual(sorted(record), sorted(f'{hashlib.sha256(d).hexdigest()}  swift/{n}'
                                                for n, d in {**BUNDLE, **XCFRAMEWORK}.items()))

    def test_wire_swift_is_idempotent(self):
        self.wire()
        first = self.manifests()
        self.wire()
        self.assertEqual(self.manifests(), first)

    def test_wire_swift_registry_copy_is_rejected(self):
        # A requirement the rewrite can't make the pin's: the companion from the package registry,
        # or from another path. Refused by name, and nothing is extracted or rewritten.
        for line in ('.package(id: "skunkwerkx.hypercast", from: "1.0.0")', '.package(path: "../../HyperCast")',
                     '.package(name: "HyperCast", path: "../vendor/hypercast")'):
            with self.subTest(line=line):
                root = swift_checkout(Path(self.tmp.name) / str(abs(hash(line))))
                manifest = root / 'swift' / 'Package.swift'
                manifest.write_text(manifest.read_text().replace(HYPERCAST_URL, line))
                before = self.manifests(root)
                with self.assertRaises(ValueError) as caught:
                    self.wire(root)
                self.assertIn('swift/Package.swift', str(caught.exception))
                self.assertEqual(self.manifests(root), before)
                self.assertEqual((root / '.companions/hypercast/swift/HyperCastCore.artifactbundle/info.json').read_text(),
                                 'stale')

    def test_wire_swift_tampered_hash_is_rejected(self):
        (self.root / SWIFT_ASSETS / 'HyperCastCore.artifactbundle.zip').write_bytes(zip_of({**BUNDLE, 'x': b'x'}))
        before = self.manifests()
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('swift-release-assets/HyperCastCore.artifactbundle.zip', str(caught.exception))
        self.assertEqual(self.manifests(), before)
        self.assertEqual((self.pin / 'swift' / 'HyperCastCore.artifactbundle' / 'info.json').read_text(), 'stale')

    def test_wire_swift_refuses_a_zip_it_cannot_extract_safely(self):
        for name, files, links in (
                ('escape', {**BUNDLE, 'HyperCastCore.artifactbundle/../../evil': b'x'}, ()),
                ('other tree', {**BUNDLE, 'Elsewhere/libhypercast.a': b'x'}, ()),
                ('absolute', {**BUNDLE, '/HyperCastCore.artifactbundle/x': b'x'}, ()),
                ('symlink', {**BUNDLE, 'HyperCastCore.artifactbundle/link': b'/etc/passwd'},
                 ('HyperCastCore.artifactbundle/link',))):
            with self.subTest(name=name):
                root = swift_checkout(Path(self.tmp.name) / name.replace(' ', '_'))
                artifacts = root / '.companions' / 'hypercast-artifacts'
                data = zip_of(files, links)
                (artifacts / 'swift-release-assets' / 'HyperCastCore.artifactbundle.zip').write_bytes(data)
                sums = artifacts / 'SHA256SUMS'
                sums.write_text(re.sub(r'^\S+(  swift-release-assets/HyperCastCore\.artifactbundle\.zip)$',
                                       lambda m: hashlib.sha256(data).hexdigest() + m.group(1), sums.read_text(), flags=re.M))
                with self.assertRaises(ValueError):
                    self.wire(root)
                self.assertFalse((root / 'evil').exists())

    def test_wire_swift_needs_both_zips(self):
        (self.root / SWIFT_ASSETS / 'HyperCastCoreApple.xcframework.zip').unlink()
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('HyperCastCoreApple.xcframework.zip', str(caught.exception))

    def test_wire_swift_rewrites_every_form(self):
        forms = ('.package(url: "https://github.com/SkunkWerkx/HyperCast", exact: "1.0.0")',
                 '.package(url: "https://github.com/SkunkWerkx/HyperCast.git", branch: "master")',
                 '.package(\n            url: "https://github.com/SkunkWerkx/HyperCast",\n            '
                 '"1.1.0"..<"2.0.0"\n        )',
                 '.package(name: "HyperCast", url: "git@github.com:SkunkWerkx/HyperCast.git", .upToNextMinor(from: "1.1.0"))',
                 '.package(url: "https://github.com/skunkwerkx/hypercast", revision: "abc")')
        for form in forms:
            with self.subTest(form=form):
                root = swift_checkout(Path(self.tmp.name) / hashlib.sha256(form.encode()).hexdigest()[:12])
                manifest = root / 'swift' / 'Package.swift'
                text = manifest.read_text().replace(HYPERCAST_URL, form)
                manifest.write_text(text)
                self.wire(root)
                self.assertEqual(manifest.read_text(), text.replace(form, '.package(path: "../.companions/hypercast")'))

    def test_wire_swift_carries_traits(self):
        # SwiftPM 6.1's traits choose what the package builds: the path form keeps them verbatim.
        form = ('.package(\n            url: "https://github.com/SkunkWerkx/HyperCast", from: "1.1.0",\n'
                '            traits: ["Fast", .trait(name: "Extra", enabledTraits: ["A", "B"])]\n        )')
        manifest = self.root / 'swift' / 'Package.swift'
        text = manifest.read_text().replace(HYPERCAST_URL, form)
        manifest.write_text(text)
        self.wire()
        self.assertEqual(manifest.read_text(), text.replace(
            form, '.package(path: "../.companions/hypercast", traits: ["Fast", .trait(name: "Extra", '
                  'enabledTraits: ["A", "B"])])'))
        # A trait's own `name:` is not the package's: another package with a trait so named stays.
        other = '.package(url: "https://example.com/Other", from: "1.0.0", traits: [.trait(name: "HyperCast")])'
        self.assertEqual(companion_wire.swift_requirements(other, 'hypercast'), [])

    def test_wire_swift_refuses_an_argument_it_cannot_carry(self):
        # A label the rewrite does not know would be dropped, building something else: refused by
        # name before anything is extracted or rewritten.
        manifest = self.root / 'swift' / 'Package.swift'
        manifest.write_text(manifest.read_text().replace(
            HYPERCAST_URL, '.package(url: "https://github.com/SkunkWerkx/HyperCast", from: "1.1.0", newThing: true)'))
        before = self.manifests()
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn("swift/Package.swift: hypercast's requirement passes newThing:", str(caught.exception))
        self.assertEqual(self.manifests(), before)
        self.assertEqual((self.pin / 'swift' / 'HyperCastCore.artifactbundle' / 'info.json').read_text(), 'stale')

    def test_wire_swift_keeps_file_modes(self):
        tool = 'HyperCastCore.artifactbundle/arm64-apple-macosx/tool'
        root = swift_checkout(Path(self.tmp.name) / 'modes', bundle={**BUNDLE, tool: b'#!/bin/sh\n'})
        artifacts = root / '.companions' / 'hypercast-artifacts'
        data = zip_of({**BUNDLE, tool: b'#!/bin/sh\n'}, modes={tool: 0o755})
        (artifacts / 'swift-release-assets' / 'HyperCastCore.artifactbundle.zip').write_bytes(data)
        sums = artifacts / 'SHA256SUMS'
        sums.write_text(re.sub(r'^\S+(  swift-release-assets/HyperCastCore\.artifactbundle\.zip)$',
                               lambda m: hashlib.sha256(data).hexdigest() + m.group(1), sums.read_text(), flags=re.M))
        self.wire(root)
        pin = root / '.companions' / 'hypercast' / 'swift'
        self.assertEqual((pin / tool).stat().st_mode & 0o777, 0o755)
        self.assertEqual((pin / 'HyperCastCore.artifactbundle/info.json').stat().st_mode & 0o777, 0o644)

    def test_wire_swift_refuses_a_symlinked_directory_in_the_pin(self):
        # The pin's swift/ a link out of .companions/: removing or writing through it would act
        # outside the pin.
        outside = Path(self.tmp.name) / 'outside'
        (outside / 'HyperCastCore.artifactbundle').mkdir(parents=True)
        (outside / 'HyperCastCore.artifactbundle' / 'info.json').write_text('outside')
        shutil.rmtree(self.pin / 'swift')
        (self.pin / 'swift').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('.companions/hypercast/swift is a symlink', str(caught.exception))
        self.assertEqual(sorted(p.name for p in outside.rglob('*')), ['HyperCastCore.artifactbundle', 'info.json'])
        self.assertEqual((outside / 'HyperCastCore.artifactbundle' / 'info.json').read_text(), 'outside')

    def test_wire_swift_without_a_requirement_fails(self):
        for manifest in self.root.rglob('Package.swift'):
            if '.companions' not in manifest.parts:
                manifest.write_text(manifest.read_text().replace(HYPERCAST_URL, ''))
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('no Package.swift', str(caught.exception))


# The C legs': c-packages-ci, every platform's archive (a .tar.gz, Windows' a .zip), each unpacking
# to hypercast-<version>-<rid>/, as c_package.py packs them.
C_ENV = {**ENV, 'HYPERCAST_C_REQ': '.companions/hypercast-artifacts/c-packages-ci'}
C_TREE = {'include/hypercast.h': b'/* c header */', 'include/hypercast.hpp': b'// c++ header',
          'lib/libhypercast.a': b'pinned static', 'lib/cmake/hypercast/hypercastConfig.cmake': b'# config',
          'lib/pkgconfig/hypercast.pc': b'Version: 1.0.0'}
C_PACKAGES = '.companions/hypercast-c'
C_RECORD = '.companions/hypercast-c.sums'


def c_archive(name, files, zip_format=False, modes=None):
    """`<name>.tar.gz` (or .zip) bytes holding `files` under `<name>/`, as c_package.py packs one
    (`modes`: an entry's permission bits, else 0644)."""
    modes = modes or {}
    if zip_format:
        return zip_of({f'{name}/{k}': v for k, v in files.items()}, modes={f'{name}/{k}': v for k, v in modes.items()})
    import tarfile
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for relative, data in sorted(files.items()):
            info = tarfile.TarInfo(f'{name}/{relative}')
            info.size = len(data)
            info.mode = modes.get(relative, 0o644)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def c_checkout(root, archives=None):
    root = checkout(root, 'c')
    archives = archives or {'hypercast-1.0.0-osx-arm64.tar.gz': c_archive('hypercast-1.0.0-osx-arm64', C_TREE),
                            'hypercast-1.0.0-win-x64.zip': c_archive('hypercast-1.0.0-win-x64', C_TREE, True)}
    stage(root, {f'c-packages-ci/{k}': v for k, v in archives.items()})
    return root


class WireCTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = c_checkout(Path(self.tmp.name) / 'consumer')
        self.github_env = Path(self.tmp.name) / 'github_env'

    def wire(self, root=None, environ=None):
        companion_wire.wire_c(root or self.root, C_ENV,
                              environ={'GITHUB_ENV': str(self.github_env)} if environ is None else environ)

    def test_wire_c_pinned_passes(self):
        cargo = (self.root / 'rust' / 'Cargo.toml').read_text()
        self.wire()
        packages = self.root / C_PACKAGES
        for rid in ('osx-arm64', 'win-x64'):
            for relative, data in C_TREE.items():
                self.assertEqual((packages / f'hypercast-1.0.0-{rid}' / relative).read_bytes(), data)
        # What c/smoke/hypercast.sh reads in place of the release download, for every later step.
        self.assertEqual(self.github_env.read_text(), f'HYPERCAST_PACKAGES={packages.as_posix()}\n')
        # hypercast.sh takes the version from rust/Cargo.toml's requirement: the pin's, exactly.
        self.assertEqual((self.root / 'rust' / 'Cargo.toml').read_text(),
                         cargo.replace('hypercast = { version = "1.0.0"', 'hypercast = { version = "=1.0.0"'))
        record = (self.root / C_RECORD).read_text().splitlines()
        self.assertEqual(sorted(record), sorted(f'{hashlib.sha256(d).hexdigest()}  hypercast-1.0.0-{rid}/{n}'
                                                for rid in ('osx-arm64', 'win-x64') for n, d in C_TREE.items()))

    def test_wire_c_rewrites_a_next_release_requirement(self):
        manifest = self.root / 'rust' / 'Cargo.toml'
        manifest.write_text(manifest.read_text().replace('version = "1.0.0", default', 'version = "1.1.0", default'))
        self.wire()
        self.assertIn('hypercast = { version = "=1.0.0", default-features = false }', manifest.read_text())

    def test_wire_c_registry_copy_is_rejected(self):
        # HYPERCAST_PACKAGES already naming another set (a sibling checkout's local build): one or
        # the other is what the smoke links.
        with self.assertRaises(ValueError) as caught:
            self.wire(environ={'GITHUB_ENV': str(self.github_env), 'HYPERCAST_PACKAGES': '/elsewhere/packages'})
        self.assertIn('/elsewhere/packages', str(caught.exception))
        self.assertFalse(self.github_env.exists())

    def test_wire_c_tampered_hash_is_rejected(self):
        artifacts = self.root / '.companions' / 'hypercast-artifacts' / 'c-packages-ci'
        (artifacts / 'hypercast-1.0.0-osx-arm64.tar.gz').write_bytes(
            c_archive('hypercast-1.0.0-osx-arm64', {**C_TREE, 'lib/libhypercast.a': b'another build'}))
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('c-packages-ci/hypercast-1.0.0-osx-arm64.tar.gz', str(caught.exception))
        self.assertFalse((self.root / C_PACKAGES).exists())
        self.assertFalse(self.github_env.exists())

    def test_wire_c_refuses_an_archive_it_cannot_place(self):
        for name, archives in (
                ('other version', {'hypercast-1.1.0-osx-arm64.tar.gz': c_archive('hypercast-1.1.0-osx-arm64', C_TREE)}),
                ('other tree', {'hypercast-1.0.0-osx-arm64.tar.gz': c_archive('hypercast-1.0.0-linux-x64', C_TREE)}),
                ('escape', {'hypercast-1.0.0-osx-arm64.tar.gz':
                            c_archive('hypercast-1.0.0-osx-arm64', {**C_TREE, '../../evil': b'x'})}),
                ('none', {})):
            with self.subTest(name=name):
                root = c_checkout(Path(self.tmp.name) / name.replace(' ', '_'), archives or {'README': b'x'})
                with self.assertRaises(ValueError):
                    companion_wire.wire_c(root, C_ENV, environ={})
                self.assertFalse((root / 'evil').exists())
                self.assertFalse((root / '.companions' / 'evil').exists())

    def test_wire_c_without_github_env_prints_the_variable(self):
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.wire(environ={})
        self.assertIn(f'HYPERCAST_PACKAGES={(self.root / C_PACKAGES).as_posix()}', out.getvalue())

    def test_wire_c_keeps_file_modes(self):
        # c_package.py packs a shared library 0755; the release's `tar -xzf` keeps it so.
        tree = {**C_TREE, 'lib/libhypercast.dylib': b'pinned shared', 'bin/hypercast.dll': b'pinned dll'}
        root = c_checkout(Path(self.tmp.name) / 'modes', {
            'hypercast-1.0.0-osx-arm64.tar.gz': c_archive('hypercast-1.0.0-osx-arm64', tree,
                                                          modes={'lib/libhypercast.dylib': 0o755}),
            'hypercast-1.0.0-win-x64.zip': c_archive('hypercast-1.0.0-win-x64', tree, True,
                                                     modes={'bin/hypercast.dll': 0o755})})
        self.wire(root)
        packages = root / C_PACKAGES
        self.assertEqual((packages / 'hypercast-1.0.0-osx-arm64/lib/libhypercast.dylib').stat().st_mode & 0o777, 0o755)
        self.assertEqual((packages / 'hypercast-1.0.0-win-x64/bin/hypercast.dll').stat().st_mode & 0o777, 0o755)
        self.assertEqual((packages / 'hypercast-1.0.0-osx-arm64/lib/libhypercast.a').stat().st_mode & 0o777, 0o644)

    def test_wire_c_refuses_a_symlinked_packages_directory(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('outside')
        (self.root / C_PACKAGES).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError) as caught:
            self.wire()
        self.assertIn('.companions/hypercast-c is a symlink', str(caught.exception))
        self.assertEqual([p.name for p in outside.iterdir()], ['keep'])


# The R leg: HyperCast 14df722 as the pin (its commit is what the probe recorded,
# fixtures/origin/r/), HyperTabular's r/DESCRIPTION and rust/Cargo.toml as the consumer.
R_COMMIT = '14df7225ddf983bb5ca04bf35b9ecc7aeebabc96'
R_ENV = {**ENV, 'HYPERCAST_COMMIT': R_COMMIT, 'HYPERCAST_R_REQ': '.companions/hypercast/r'}


def r_checkout(root):
    """HyperTabular's R package and core manifest, with HyperCast's R package (its DESCRIPTION
    and a vendor.sh) in the pinned source."""
    root = checkout(root, 'r')
    shutil.copytree(FIXTURES / 'r-pinned', root / '.companions' / 'hypercast', dirs_exist_ok=True)
    tools = root / '.companions' / 'hypercast' / 'r' / 'tools'
    tools.mkdir(parents=True)
    (tools / 'vendor.sh').write_text('#!/usr/bin/env bash\nexit 0\n')
    return root


class FakeR:
    """vendor.sh, `R CMD INSTALL` and `Rscript -e`, each standing for its effect: the pin's
    vendor tarball written, the package's DESCRIPTION installed into the named library as R
    installs it, the library R would choose printed."""
    def __init__(self, library=None, fail=None):
        self.calls, self.library, self.fail = [], library, fail

    def __call__(self, argv, cwd=None, check=False, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append((argv, cwd))
        if self.fail and self.fail in ' '.join(argv):
            raise subprocess.CalledProcessError(1, argv)
        if argv[1:2] and argv[1].endswith('/tools/vendor.sh'):
            tarball = Path(argv[1]).parent.parent / 'src' / 'rust' / 'vendor.tar.xz'
            tarball.parent.mkdir(parents=True, exist_ok=True)
            tarball.write_bytes(b'vendored')
        elif argv[:3] == ['R', 'CMD', 'INSTALL']:
            library = Path(next(a for a in argv if a.startswith('--library=')).partition('=')[2])
            source = Path(argv[-1])
            package = re.search(r'^Package:\s*(\S+)', (source / 'DESCRIPTION').read_text(), re.M).group(1)
            (library / package).mkdir(parents=True, exist_ok=True)
            shutil.copy(source / 'DESCRIPTION', library / package / 'DESCRIPTION')
        elif argv[0] == 'Rscript':
            return subprocess.CompletedProcess(argv, 0, str(self.library), '')
        return subprocess.CompletedProcess(argv, 0, '', '')


class WireRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = r_checkout(Path(self.tmp.name) / 'consumer')
        self.pin = self.root / '.companions' / 'hypercast'
        self.library = Path(self.tmp.name) / 'library'
        self.github_env = Path(self.tmp.name) / 'github_env'
        self.environ = {'GITHUB_ENV': str(self.github_env), 'R_LIBS_USER': str(self.library)}

    def wire(self, tool=None, environ=None, env=R_ENV):
        tool = tool or FakeR()
        companion_wire.wire_r(self.root, env, run=tool, environ=self.environ if environ is None else environ)
        return tool

    def test_wire_r_pinned_passes(self):
        description = (self.root / 'r' / 'DESCRIPTION').read_text()
        cargo = (self.root / 'rust' / 'Cargo.toml').read_text()
        pinned = (self.pin / 'r' / 'DESCRIPTION').read_text()
        tool = self.wire()
        # HyperCast's vendor.sh stages the pin's own core into its tarball, then R installs the
        # stamped tree into the job's library (R_LIBS_USER, which setup-r-dependencies resolves
        # against), where nothing has to download it.
        [(vendor, vendor_cwd), (install, install_cwd)] = tool.calls
        self.assertEqual(Path(vendor[0]).name, 'bash')
        self.assertEqual(vendor[1:], [(self.pin / 'r' / 'tools' / 'vendor.sh').as_posix(), '--stage'])
        self.assertEqual(vendor_cwd, self.pin / 'r')
        self.assertEqual(install, ['R', 'CMD', 'INSTALL', '--preclean', f'--library={self.library}',
                                   (self.pin / 'r').as_posix()])
        self.assertIsNone(install_cwd)
        self.assertTrue(self.library.is_dir())
        # The pin's DESCRIPTION carries the commit, the field the proof reads back from what R loads.
        self.assertEqual((self.pin / 'r' / 'DESCRIPTION').read_text(), pinned + f'Hyper-Pin: {R_COMMIT}\n')
        self.assertIn(f'Hyper-Pin: {R_COMMIT}\n', (self.library / 'hypercast' / 'DESCRIPTION').read_text())
        # The consumer's floor relaxed to the pin's version, in the job's tree only; that one line.
        self.assertEqual((self.root / 'r' / 'DESCRIPTION').read_text(),
                         description.replace('hypercast (>= 1.1.0)', 'hypercast (>= 1.0.0)'))
        # The consumer's core, which its R package vendors, takes the pin's crate: its requirement
        # set to the pin's exact version, and <NAME>_PATH (what HyperTabular's vendor.sh reads)
        # at the pin's rust/ for every later step.
        self.assertEqual((self.root / 'rust' / 'Cargo.toml').read_text(),
                         cargo.replace('hypercast = { version = "1.0.0"', 'hypercast = { version = "=1.0.0"'))
        self.assertEqual(self.github_env.read_text(), f'HYPERCAST_PATH={(self.pin / "rust").as_posix()}\n')

    def test_wire_r_registry_copy_is_rejected(self):
        # HYPERCAST_PATH already naming another crate (a sibling checkout's): one or the other is
        # what the consumer's R package vendors.
        tool = FakeR()
        with self.assertRaises(ValueError) as caught:
            self.wire(tool, environ={**self.environ, 'HYPERCAST_PATH': '/elsewhere/HyperCast/rust'})
        self.assertIn('/elsewhere/HyperCast/rust', str(caught.exception))
        self.assertEqual(tool.calls, [])
        self.assertFalse(self.github_env.exists())
        self.assertNotIn('Hyper-Pin', (self.pin / 'r' / 'DESCRIPTION').read_text())

    def test_wire_r_is_idempotent(self):
        # The floor R installs the same tree again, into its own library.
        self.wire()
        floor = Path(self.tmp.name) / 'floor'
        tool = self.wire(environ={**self.environ, 'R_LIBS_USER': str(floor)})
        self.assertEqual((self.pin / 'r' / 'DESCRIPTION').read_text().count('Hyper-Pin:'), 1)
        self.assertIn(f'--library={floor}', tool.calls[-1][0])
        self.assertIn('hypercast (>= 1.0.0)', (self.root / 'r' / 'DESCRIPTION').read_text())
        # A stamp from another pin is replaced, never kept beside this one.
        description = self.pin / 'r' / 'DESCRIPTION'
        description.write_text(description.read_text().replace(R_COMMIT, 'a' * 40))
        self.wire()
        self.assertEqual(re.findall(r'^Hyper-Pin: (.*)$', description.read_text(), re.M), [R_COMMIT])

    def test_wire_r_rewrites_every_dependency_field(self):
        description = self.root / 'r' / 'DESCRIPTION'
        description.write_text(
            'Package: hypertabular\nVersion: 1.0.0\n'
            'Depends: R (>= 4.2), hypercast(>=1.1)\n'
            'Imports:\n    jsonlite,\n    hypercast (== 1.1.0),\n    hypercastr (>= 9.9),\n    my.hypercast (>= 9.9)\n'
            'LinkingTo: hypercast\n'
            'Suggests: testthat (>= 3.0.0), hypercast\n    (>= 1.1.0)\n'
            'Description: Mentions hypercast (>= 1.1.0) in prose, which is no requirement.\n')
        self.wire()
        self.assertEqual(description.read_text(),
                         'Package: hypertabular\nVersion: 1.0.0\n'
                         'Depends: R (>= 4.2), hypercast (>= 1.0.0)\n'
                         'Imports:\n    jsonlite,\n    hypercast (>= 1.0.0),\n    hypercastr (>= 9.9),\n'
                         '    my.hypercast (>= 9.9)\n'
                         'LinkingTo: hypercast\n'
                         'Suggests: testthat (>= 3.0.0), hypercast (>= 1.0.0)\n'
                         'Description: Mentions hypercast (>= 1.1.0) in prose, which is no requirement.\n')

    def test_wire_r_needs_a_requirement_and_the_pins_package(self):
        (self.root / 'r' / 'DESCRIPTION').write_text('Package: hypertabular\nImports: jsonlite\n')
        with self.assertRaisesRegex(ValueError, 'no DESCRIPTION under .* requires hypercast'):
            self.wire()
        root = r_checkout(Path(self.tmp.name) / 'other')
        pinned = root / '.companions' / 'hypercast' / 'r' / 'DESCRIPTION'
        pinned.write_text(pinned.read_text().replace('Package: hypercast', 'Package: other'))
        with self.assertRaisesRegex(ValueError, r'\.companions/hypercast/r/DESCRIPTION is the package other'):
            companion_wire.wire_r(root, R_ENV, run=FakeR(), environ=self.environ)
        with self.assertRaisesRegex(ValueError, 'HYPERCAST_COMMIT'):
            self.wire(env={k: v for k, v in R_ENV.items() if k != 'HYPERCAST_COMMIT'})

    def test_wire_r_without_r_libs_user_asks_r(self):
        tool = self.wire(FakeR(library=self.library), environ={'GITHUB_ENV': str(self.github_env)})
        self.assertEqual(tool.calls[1][0], ['Rscript', '-e', 'cat(.libPaths()[1L])'])
        self.assertEqual(tool.calls[2][0][4], f'--library={self.library}')

    def test_wire_r_failed_install_is_raised_and_sets_nothing(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.wire(FakeR(fail='INSTALL'))
        self.assertFalse(self.github_env.exists())

    def test_wire_r_without_github_env_prints_the_variable(self):
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.wire(environ={'R_LIBS_USER': str(self.library)})
        self.assertIn(f'HYPERCAST_PATH={(self.pin / "rust").as_posix()}\n', out.getvalue())


class WireCliTests(unittest.TestCase):
    def test_every_wired_ecosystem_is_available(self):
        import companion_map
        for eco in companion_map.ECOSYSTEMS:
            with self.subTest(eco=eco):
                self.assertTrue(companion_fetch.available(eco))

    def test_cli_reads_the_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = checkout(tmp, 'go')
            (root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in ENV.items()))
            self.assertEqual(companion_wire.read_env(root), ENV)
            # Without go on PATH the CLI still reaches the tool, and fails there, not on the env.
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_wire.py'), 'go', '--root', str(root)],
                                    capture_output=True, text=True,
                                    env={**os.environ, 'PATH': os.environ['PATH'] if shutil.which('go') else ''})
            if shutil.which('go'):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'replace {MODULE} =>', (root / 'go' / 'go.mod').read_text())
            else:
                self.assertNotEqual(result.returncode, 0)

    def test_cli_without_the_env_file_fails_naming_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_wire.py'), 'rust', '--root', tmp],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('::error::', result.stderr)
            self.assertIn('.companions/env', result.stderr)

    def test_env_without_the_ecosystem_fails_naming_it(self):
        env = {k: v for k, v in ENV.items() if k != 'HYPERCAST_GO_REQ'}
        with self.assertRaises(ValueError) as caught:
            companion_wire.wire_go(Path('.'), env, run=FakeTool())
        self.assertIn('_GO_REQ', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
