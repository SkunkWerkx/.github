"""companion_origin.py: each ecosystem's proof that the leg took the pinned companion and not a
registry's copy (the companion-pins spec, "Failure modes"), against the probes' recorded
output (fixtures/origin/)."""
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
import unittest
from unittest import mock
import zipfile

import companion_origin
import companion_wire
from test_companion_wire import (ABI3, ABI3T, ADDON, CSHARP_ENV, ENV, JAR, JAVA_ENV, MODULE, NPM_ENV, NUPKG, PACKAGE,
                                 PHP_ENV, PHP_EXTENSION, PHP_ZIP, PY_ENV, PYODIDE, RUBY_ARTIFACTS, RUBY_ENV, SDIST, TARBALL,
                                 WASM_ARTIFACTS, WASM_GEM, BUNDLE, XCFRAMEWORK, C_ENV, C_TREE, HYPERCAST_URL, SWIFT_ENV, FakeDotnetPack,
                                 FakeGradle, FakeNpm, c_archive, c_checkout, checkout, csharp_checkout, fake_pack_wasi, java_checkout,
                                 npm_integrity, npm_stage, php_checkout, php_zip, quiet, ruby_checkout, swift_checkout,
                                 R_COMMIT, R_ENV, FakeR, r_checkout)

SCRIPTS = Path(__file__).resolve().parent
FIXTURES = SCRIPTS / 'fixtures' / 'origin'
HOME = '/home/runner'


class Recorded:
    """A runner answering every command with one recorded output, keeping the commands."""
    def __init__(self, root, stdout='', stderr='', returncode=0, by_dir=None):
        self.root, self.calls = Path(root), []
        self.answer = (stdout, stderr, returncode)
        self.by_dir = by_dir or {}

    def text(self, name):
        if not name:
            return ''
        eco = 'go' if name.endswith(('.json', 'missing.stderr')) else 'rust'
        return (FIXTURES / eco / name).read_text().replace('@ROOT@', str(self.root)).replace('@HOME@', HOME)

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append((argv, cwd))
        where = Path(argv[argv.index('--manifest-path') + 1]).parent if '--manifest-path' in argv else Path(cwd)
        stdout, stderr, code = self.by_dir.get(where.relative_to(self.root).as_posix(), self.answer)
        return subprocess.CompletedProcess(argv, code, self.text(stdout), self.text(stderr))


class OriginRustTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = checkout(Path(self.tmp.name) / 'consumer', 'rust')
        self.pin = self.root / '.companions' / 'hypercast' / 'rust'

    def origin(self, runner):
        companion_origin.origin_rust(self.root, ENV, {}, run=runner)

    def rejected(self, runner):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(runner)
        error = caught.exception
        self.assertEqual(error.expected, f'hypercast from {self.pin.as_posix()} (the pin, a path source)')
        self.assertIn(error.binding, str(error))
        self.assertIn(error.found, str(error))
        self.assertIn(error.expected, str(error))
        return error

    def test_origin_rust_pinned_passes(self):
        runner = Recorded(self.root, 'pinned.stdout')
        self.origin(runner)
        self.assertEqual(runner.calls, [(['cargo', 'tree', '-i', 'hypercast', '--locked', '--all-features', '--depth', '0',
                                          '--prefix', 'none', '--color', 'never',
                                          '--manifest-path', str(self.root / w / 'Cargo.toml')],
                                         None) for w in ('rust', 'typescript')])

    def test_origin_rust_registry_copy_is_rejected(self):
        error = self.rejected(Recorded(self.root, 'registry.stdout'))
        self.assertEqual(error.binding, 'Rust (rust/)')
        self.assertEqual(error.found, 'hypercast v1.0.0 from a registry (crates.io)')

    def test_origin_rust_unused_patch_is_rejected(self):
        # Cargo's warning, not an error: promoted here, whatever the tree then shows. Every shape
        # recorded: today's Cargo, 1.85-1.90's ("Patch ... graph."), and both coloured, which is
        # what a leg prints when `--color never` is lost under dtolnay/rust-toolchain's
        # CARGO_TERM_COLOR=always.
        for stderr in ('unused.stderr', 'unused-color.stderr', 'unused-old.stderr', 'unused-old-color.stderr'):
            for stdout in ('unused.stdout', 'pinned.stdout'):
                with self.subTest(stderr=stderr, stdout=stdout):
                    error = self.rejected(Recorded(self.root, stdout, stderr))
                    self.assertIn('was not used', error.found)
                    self.assertNotIn('\x1b', error.found)

    def test_origin_rust_stale_lock_is_rejected(self):
        error = self.rejected(Recorded(self.root, '', 'stale.stderr', 101))
        self.assertIn('cargo tree failed (exit 101)', error.found)
        self.assertIn('--locked', error.found)

    def test_origin_rust_another_path_is_rejected(self):
        runner = Recorded(self.root, 'pinned.stdout')
        runner.text = lambda name: f'hypercast v1.0.0 ({self.root.parent}/HyperCast/rust)\n'
        error = self.rejected(runner)
        self.assertEqual(error.found, f'hypercast v1.0.0 from {self.root.parent}/HyperCast/rust')

    def test_origin_rust_checks_every_workspace(self):
        shutil.copytree(self.root / 'rust', self.root / 'java' / 'jni')
        runner = Recorded(self.root, 'pinned.stdout', by_dir={'typescript': ('registry.stdout', '', 0)})
        error = self.rejected(runner)
        self.assertEqual(error.binding, 'Rust (typescript/)')
        self.assertEqual(len(runner.calls), 3)


class OriginGoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = checkout(Path(self.tmp.name) / 'consumer', 'go')
        self.pin = self.root / '.companions' / 'hypercast' / 'go'

    def rejected(self, runner):
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_go(self.root, ENV, {}, run=runner)
        error = caught.exception
        self.assertEqual(error.binding, 'Go (go/)')
        self.assertEqual(error.expected, f'{MODULE} replaced by {self.pin} (the pin)')
        return error

    def test_origin_go_pinned_passes(self):
        runner = Recorded(self.root, 'pinned.json')
        companion_origin.origin_go(self.root, ENV, {}, run=runner)
        self.assertEqual(runner.calls, [(['go', 'list', '-m', '-json', MODULE], self.root / 'go')])

    def test_origin_go_registry_copy_is_rejected(self):
        error = self.rejected(Recorded(self.root, 'registry.json'))
        self.assertEqual(error.found, f'{MODULE} v1.0.0 from the module proxy, no replace '
                                      f'({HOME}/go/pkg/mod/github.com/!skunk!werkx/!hyper!cast/go@v1.0.0)')

    def test_origin_go_missing_is_rejected(self):
        # The proxy has no v1.1.0: no copy at all, and the check says so.
        error = self.rejected(Recorded(self.root, '', 'missing.stderr', 1))
        self.assertEqual(error.found, 'no copy: go list failed (exit 1): go: github.com/SkunkWerkx/HyperCast/go@v1.1.0: '
                                      'invalid version: unknown revision go/v1.1.0')

    def test_origin_go_another_replace_is_rejected(self):
        runner = Recorded(self.root, 'pinned.json')
        other = self.root.parent / 'HyperCast' / 'go'
        runner.text = lambda name: json.dumps({'Path': MODULE, 'Version': 'v1.1.0',
                                               'Replace': {'Path': str(other), 'Dir': str(other)}})
        error = self.rejected(runner)
        self.assertEqual(error.found, f'{MODULE} replaced by {other}')


# The hashes pip recorded (fixtures/origin/python/), as the leg's SHA256SUMS lists them.
RECORDED_SUMS = {
    'pypi-wheels-osx-arm64/hypercast-1.0.0-cp311-abi3-macosx_11_0_arm64.whl':
        '6760396926cb6ca03058153982f124b420d7876a8215cc54031be403d0418b33',
    'pypi-wheels-osx-arm64/hypercast-1.0.0-cp315-abi3.abi3t-macosx_11_0_arm64.whl':
        'eacb32d3e8ca1682323b655512a6fc85a6e2dfb89864631fd5ccb4245ba4f06e',
    'pypi-wheels-sdist/hypercast-1.0.0.tar.gz': 'bf56fdfaa0cf28a4c52b8c21d3767c14d19448db630f688759c2c913ee9b1b84',
}


class Inspected:
    """`<python> -m pip inspect` answering with one recorded fixtures/origin/python/ file."""
    def __init__(self, root, name='pinned.json', returncode=0, stderr=''):
        self.root, self.name, self.returncode, self.stderr, self.calls = Path(root), name, returncode, stderr, []
        self.edit = lambda data: data

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        self.calls.append(list(map(str, argv)))
        out = ''
        if self.name:
            text = (FIXTURES / 'python' / self.name).read_text().replace('@ROOT@', str(self.root))
            out = json.dumps(self.edit(json.loads(text.replace('@HOME@', HOME))))
        return subprocess.CompletedProcess(argv, self.returncode, out, self.stderr)


class OriginPythonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = checkout(Path(self.tmp.name) / 'consumer', 'python')
        pyproject = self.root / 'python' / 'pyproject.toml'
        pyproject.write_text(pyproject.read_text().replace('"hypercast>=1.1.0,<2"', '"hypercast==1.0.0"'))

    def origin(self, runner, sums=None, python='/x/bin/python'):
        companion_origin.origin_python(self.root, PY_ENV, RECORDED_SUMS if sums is None else sums, run=runner,
                                       python=python, environ={})

    def rejected(self, runner, sums=None, binding='Python 3.14.10 (/x/bin/python)'):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(runner, sums)
        error = caught.exception
        self.assertEqual(error.binding, binding)
        self.assertEqual(error.expected, f'hypercast 1.0.0 installed from a pinned file ({ABI3}, {ABI3T}, {SDIST}), '
                                         'its sha256 as SHA256SUMS lists it')
        return error

    def test_origin_python_pinned_passes(self):
        runner = Inspected(self.root)
        self.origin(runner)
        self.assertEqual(runner.calls, [['/x/bin/python', '-m', 'pip', '--disable-pip-version-check', 'inspect']])

    def test_origin_python_pinned_sdist_passes(self):
        self.origin(Inspected(self.root, 'sdist.json'))

    def test_origin_python_registry_copy_is_rejected(self):
        # `pip install hypercast==1.0.0` from PyPI: the same version, and no direct_url.json.
        error = self.rejected(Inspected(self.root, 'registry.json'))
        self.assertEqual(error.found, 'hypercast 1.0.0 from an index (PyPI), with no direct_url.json')

    def test_origin_python_tampered_hash_is_rejected(self):
        sums = {**RECORDED_SUMS, 'pypi-wheels-osx-arm64/hypercast-1.0.0-cp311-abi3-macosx_11_0_arm64.whl': '0' * 64}
        error = self.rejected(Inspected(self.root), sums)
        self.assertEqual(error.found, f'hypercast 1.0.0 from {ABI3} with sha256 '
                                      '6760396926cb6ca03058153982f124b420d7876a8215cc54031be403d0418b33, '
                                      f'not SHA256SUMS\' {"0" * 64}')

    def test_origin_python_unlisted_file_is_rejected(self):
        sums = {k: v for k, v in RECORDED_SUMS.items() if 'cp311' not in k}
        error = self.rejected(Inspected(self.root), sums)
        self.assertIn('not in SHA256SUMS', error.found)

    def test_origin_python_another_file_is_rejected(self):
        runner = Inspected(self.root)
        other = self.root.parent / 'HyperCast' / 'python' / 'dist' / Path(ABI3).name

        def elsewhere(data):
            for entry in data['installed']:
                if entry.get('direct_url'):
                    entry['direct_url']['url'] = other.as_uri()
            return data
        runner.edit = elsewhere
        error = self.rejected(runner)
        self.assertEqual(error.found, f'hypercast 1.0.0 from {other}')

    def test_origin_python_missing_is_rejected(self):
        runner = Inspected(self.root)
        runner.edit = lambda data: {**data, 'installed': [e for e in data['installed']
                                                          if e['metadata']['name'] != 'hypercast']}
        self.assertEqual(self.rejected(runner).found, 'no hypercast installed')

    def test_origin_python_two_copies_are_rejected(self):
        # A stale egg-info on sys.path beside the pinned dist-info: pip inspect lists both, and
        # proving one says nothing about which one imports.
        stale = f'{HOME}/venvs/p314/lib/python3.14/site-packages/hypercast.egg-info'
        pinned_at = f'{HOME}/venvs/p314/lib/python3.14/site-packages/hypercast-1.0.0.dist-info'
        for first in (True, False):
            with self.subTest(pinned_listed_first=first):
                runner = Inspected(self.root)

                def twice(data):
                    pinned = next(e for e in data['installed'] if e['metadata']['name'] == 'hypercast')
                    other = {**pinned, 'direct_url': None, 'metadata_location': stale}
                    rest = [e for e in data['installed'] if e is not pinned]
                    return {**data, 'installed': [pinned, other, *rest] if first else [other, pinned, *rest]}
                runner.edit = twice
                error = self.rejected(runner)
                listed = f'{pinned_at}; {stale}' if first else f'{stale}; {pinned_at}'
                self.assertEqual(error.found, f'2 copies of hypercast installed ({listed})')

    def test_origin_python_inspect_failure_is_rejected(self):
        error = self.rejected(Inspected(self.root, '', 1, 'No module named pip\n'), binding='Python (/x/bin/python)')
        self.assertEqual(error.found, 'pip inspect failed (exit 1): No module named pip')

    def test_origin_python_unrewritten_requirement_is_rejected(self):
        # pip would read the consumer's own range, which only the index can meet.
        pyproject = self.root / 'python' / 'pyproject.toml'
        pyproject.write_text(pyproject.read_text().replace('"hypercast==1.0.0"', '"hypercast>=1.1.0,<2"'))
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(Inspected(self.root))
        self.assertEqual(caught.exception.binding, 'Python (python/pyproject.toml)')
        self.assertEqual(caught.exception.found, "a requirement 'hypercast>=1.1.0,<2'")
        self.assertEqual(caught.exception.expected, "every requirement on hypercast at '==1.0.0', the pin's version")

    def test_origin_python_without_an_interpreter_checks_the_requirement_only(self):
        runner = Inspected(self.root)
        companion_origin.origin_python(self.root, PY_ENV, RECORDED_SUMS, run=runner,
                                       environ={'HYPER_COMPANION_PYTHON': 'none'})
        self.assertEqual(runner.calls, [])

    @unittest.skipUnless(sys.version_info >= (3, 11), 'tomllib')
    def test_origin_python_with_the_real_pip(self):
        # A real venv and pip, offline: the pin installed by the wiring passes; an index-style
        # install of the same version (--find-links, so no direct_url.json) is rejected.
        venv = Path(self.tmp.name) / 'venv'
        made = subprocess.run([sys.executable, '-m', 'venv', str(venv)], capture_output=True, text=True)
        if made.returncode != 0:
            self.skipTest(f'no venv here: {made.stderr.strip()[-200:]}')
        python = str(venv / ('Scripts' if os.name == 'nt' else 'bin') / 'python')
        wheel_dir = self.root / '.companions' / 'hypercast-artifacts' / 'pypi-wheels-any'
        wheel = wheel_dir / 'hypercast-1.0.0-py3-none-any.whl'
        make_wheel(wheel, 'pinned')
        sums = {'pypi-wheels-any/hypercast-1.0.0-py3-none-any.whl': hashlib.sha256(wheel.read_bytes()).hexdigest()}
        with (wheel_dir.parent / 'SHA256SUMS').open('a') as listed:
            listed.writelines(f'{v}  {k}\n' for k, v in sums.items())
        env = {**PY_ENV, 'HYPERCAST_PYTHON_REQ': wheel.relative_to(self.root).as_posix()}
        import companion_wire
        companion_wire.wire_python(self.root, env, python=python, environ={})
        companion_origin.origin_python(self.root, env, sums, python=python, environ={})
        index = Path(self.tmp.name) / 'index'
        make_wheel(index / wheel.name, 'from an index')
        subprocess.run([python, '-m', 'pip', 'install', '-q', '--no-index', '--force-reinstall', '--find-links',
                        str(index), 'hypercast==1.0.0'], check=True, capture_output=True)
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_python(self.root, env, sums, python=python, environ={})
        self.assertEqual(caught.exception.found, 'hypercast 1.0.0 from an index (PyPI), with no direct_url.json')

    @unittest.skipUnless(sys.version_info >= (3, 11), 'tomllib')
    def test_origin_python_reads_a_non_ascii_readme_on_any_code_page(self):
        # HyperCast's README has a ✓, and `pip inspect` prints it; a Windows pipe is cp1252,
        # forced here as PYTHONIOENCODING, where pip could not write it.
        venv = Path(self.tmp.name) / 'venv'
        made = subprocess.run([sys.executable, '-m', 'venv', str(venv)], capture_output=True, text=True)
        if made.returncode != 0:
            self.skipTest(f'no venv here: {made.stderr.strip()[-200:]}')
        python = str(venv / ('Scripts' if os.name == 'nt' else 'bin') / 'python')
        wheel_dir = self.root / '.companions' / 'hypercast-artifacts' / 'pypi-wheels-any'
        wheel = wheel_dir / 'hypercast-1.0.0-py3-none-any.whl'
        make_wheel(wheel, 'pinned', description='| macOS | \u2713 |\n')
        sums = {'pypi-wheels-any/hypercast-1.0.0-py3-none-any.whl': hashlib.sha256(wheel.read_bytes()).hexdigest()}
        with (wheel_dir.parent / 'SHA256SUMS').open('a') as listed:
            listed.writelines(f'{v}  {k}\n' for k, v in sums.items())
        env = {**PY_ENV, 'HYPERCAST_PYTHON_REQ': wheel.relative_to(self.root).as_posix()}
        import companion_wire
        companion_wire.wire_python(self.root, env, python=python, environ={})
        with mock.patch.dict(os.environ, {'PYTHONIOENCODING': 'cp1252'}):
            companion_origin.origin_python(self.root, env, sums, python=python, environ={})


def make_wheel(path, marker, description=''):
    """A minimal pure wheel of hypercast 1.0.0, which pip installs offline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    files = {'hypercast/__init__.py': f'ORIGIN = {marker!r}\n',
             'hypercast-1.0.0.dist-info/METADATA': 'Metadata-Version: 2.1\nName: hypercast\nVersion: 1.0.0\n'
                                                   + (f'\n{description}' if description else ''),
             'hypercast-1.0.0.dist-info/WHEEL': 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n'
                                                'Tag: py3-none-any\n'}
    record = ''.join(f'{name},,\n' for name in files) + 'hypercast-1.0.0.dist-info/RECORD,,\n'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, body in {**files, 'hypercast-1.0.0.dist-info/RECORD': record}.items():
            archive.writestr(name, body)


class OriginPyodideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = checkout(Path(self.tmp.name) / 'consumer', 'python')
        pyproject = self.root / 'python' / 'pyproject.toml'
        pyproject.write_text(pyproject.read_text().replace('"hypercast>=1.1.0,<2"', '"hypercast==1.0.0"'))
        self.sums = companion_origin.read_sums(self.root, PY_ENV)

    def test_origin_pyodide_pinned_passes(self):
        companion_origin.origin_pyodide(self.root, PY_ENV, self.sums)

    def test_origin_pyodide_registry_copy_is_rejected(self):
        # The requirement left at the consumer's range: the dependency download would take PyPI's.
        pyproject = self.root / 'python' / 'pyproject.toml'
        pyproject.write_text(pyproject.read_text().replace('"hypercast==1.0.0"', '"hypercast>=1.1.0,<2"'))
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_pyodide(self.root, PY_ENV, self.sums)
        self.assertEqual(caught.exception.binding, 'Pyodide (python/pyproject.toml)')

    def test_origin_pyodide_tampered_hash_is_rejected(self):
        (self.root / PYODIDE).write_bytes(b'tampered')
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_pyodide(self.root, PY_ENV, self.sums)
        self.assertEqual(caught.exception.binding, f'Pyodide ({PYODIDE})')
        self.assertIn('sha256 ' + hashlib.sha256(b'tampered').hexdigest(), caught.exception.found)


class Resolved:
    """`node -p "require.resolve(process.argv[1] + '/package.json')" <package>` answering with where a consumer
    directory's Node finds the package: `where(cwd)`, or its own node_modules by default."""
    def __init__(self, where=None):
        self.calls, self.where = [], where

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append((argv, str(cwd)))
        assert argv[:3] == ['node', '-p', companion_origin.NODE_RESOLVE] and len(argv) == 4, argv
        package = argv[3]
        found = self.where(Path(cwd)) if self.where else Path(cwd) / 'node_modules' / package / 'package.json'
        if found is None:
            return subprocess.CompletedProcess(argv, 1, '', f"Error: Cannot find module '{package}/package.json'")
        return subprocess.CompletedProcess(argv, 0, f'{found}\n', '')


def npm_install(consumer, tarball, package=PACKAGE):
    """What `npm ci` leaves in node_modules from a tarball: its `package/` contents."""
    import tarfile
    target = Path(consumer) / 'node_modules' / package
    with tarfile.open(tarball) as archive:
        for member in archive.getmembers():
            if member.isfile():
                path = target / member.name.removeprefix('package/')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.extractfile(member).read())
    return target


class OriginNpmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = npm_stage(checkout(Path(self.tmp.name) / 'consumer', 'npm'), ADDON)
        # The committed whitelist's files (the loader and wrappers), as the pinned checkout has them.
        source = self.root / '.companions' / 'hypercast' / 'typescript'
        for listed in json.loads((source / 'package.json').read_text())['files']:
            if listed != 'package.json':
                (source / listed).write_text(f'// pinned {listed}\n')
        with mock.patch('npm_package.pack_wasi', fake_pack_wasi):
            companion_wire.wire_npm(self.root, NPM_ENV, run=FakeNpm(), environ={})
        self.tarball = self.root / TARBALL
        self.integrity = npm_integrity(self.tarball)
        self.lock = self.root / 'typescript' / 'package-lock.json'
        self.sums = companion_origin.read_sums(self.root, NPM_ENV)

    def origin(self, run=None, installed=False, **kwargs):
        companion_origin.origin_npm(self.root, NPM_ENV, self.sums, run=run or Resolved(), installed=installed, **kwargs)

    def rejected(self, **kwargs):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(**kwargs)
        error = caught.exception
        self.assertEqual(error.expected, f'{PACKAGE} 1.0.0 from file:{TARBALL} (the pinned package packed with this '
                                         f'leg\'s artifacts), integrity {self.integrity}')
        self.assertIn(error.found, str(error))
        return error

    def entry(self, **changes):
        lock = json.loads(self.lock.read_text())
        lock['packages'][f'node_modules/{PACKAGE}'].update(changes)
        self.lock.write_text(json.dumps(lock, indent=2))

    def test_origin_npm_pinned_passes(self):
        self.origin()
        # After the install, and in the directory the suite runs from: Node resolves the package
        # to this consumer's node_modules, whose addon is the pinned one.
        npm_install(self.root / 'typescript', self.tarball)
        run = Resolved()
        self.origin(run=run, installed=True)
        self.assertEqual(run.calls, [(['node', '-p', "require.resolve(process.argv[1] + '/package.json')", PACKAGE],
                                      str(self.root / 'typescript'))])

    def test_origin_npm_fake_lock_entry_is_the_shape_npm_writes(self):
        # The entry FakeNpm writes, beside the one real npm 11.20 wrote for the wiring of HyperCast
        # 14df722 into HyperTabular (fixtures/origin/npm/pinned.json): the same `file:` form,
        # relative to the lock, and the fields origin reads.
        real = json.loads((FIXTURES / 'npm' / 'pinned.json').read_text())
        fake = json.loads(self.lock.read_text())['packages'][f'node_modules/{PACKAGE}']
        self.assertEqual(fake['resolved'], real['resolved'])
        self.assertEqual((fake['version'], fake['dev']), (real['version'], real['dev']))
        self.assertTrue(real['integrity'].startswith('sha512-') and fake['integrity'].startswith('sha512-'))

    def test_origin_npm_registry_copy_is_rejected(self):
        # The lock entry npm writes for a registry copy (fixtures/origin/npm/registry.json).
        entry = json.loads((FIXTURES / 'npm' / 'registry.json').read_text())
        self.entry(**entry)
        error = self.rejected()
        self.assertEqual(error.binding, 'npm (typescript/)')
        self.assertEqual(error.found, f'{PACKAGE} {entry["version"]} from {entry["resolved"]} (a registry)')

    def test_origin_npm_tampered_hash_is_rejected(self):
        # The lock's integrity is not the packed tarball's.
        self.entry(integrity='sha512-' + 'A' * 86 + '==')
        self.assertIn('integrity sha512-AAAA', self.rejected().found)
        # The tarball itself changed after packing: neither the lock nor the record matches it.
        self.setUp()
        self.tarball.write_bytes(self.tarball.read_bytes() + b'\0')
        self.assertIn('integrity', self.rejected().found)
        # The installed addon is not the pinned one.
        self.setUp()
        installed = npm_install(self.root / 'typescript', self.tarball)
        (installed / 'hypercast.darwin-arm64.node').write_bytes(b'a registry addon')
        error = self.rejected(installed=True)
        self.assertIn('node_modules/@skunkwerkx/hypercast/hypercast.darwin-arm64.node', error.found)
        self.assertIn('not SHA256SUMS', error.found)

    def test_origin_npm_tarball_without_the_listed_payload_is_rejected(self):
        # The tarball, lock and record agree, but the addon packed is not the one SHA256SUMS lists.
        self.sums['node-osx-arm64/hypercast.darwin-arm64.node'] = '0' * 64
        error = self.rejected()
        self.assertIn(f"{PACKAGE}'s hypercast.darwin-arm64.node with sha256 ", error.found)
        self.assertIn("not SHA256SUMS' " + '0' * 64, error.found)

    def test_origin_npm_installed_package_is_the_tarball_whole(self):
        # The addon alone is not the package: its manifest names the pin's package and version,
        # and every file napi's loader and the wrappers read is the tarball's.
        def installed_with(change):
            self.setUp()
            installed = npm_install(self.root / 'typescript', self.tarball)
            change(installed)
            return self.rejected(installed=True).found

        def manifest(**fields):
            def change(installed):
                data = json.loads((installed / 'package.json').read_text())
                data.update(fields)
                (installed / 'package.json').write_text(json.dumps(data))
            return change
        self.assertIn(f'{PACKAGE} 0.0.1 installed (node_modules/{PACKAGE}/package.json)',
                      installed_with(manifest(version='0.0.1')))
        self.assertIn(f'@skunkwerkx/other 1.0.0 installed (node_modules/{PACKAGE}/package.json)',
                      installed_with(manifest(name='@skunkwerkx/other')))
        self.assertIn(f'node_modules/{PACKAGE}/index.js unlike the tarball\'s package/index.js',
                      installed_with(lambda d: (d / 'index.js').write_text('// a registry loader\n')))
        self.assertIn(f'node_modules/{PACKAGE}/extra.js, which the tarball does not hold',
                      installed_with(lambda d: (d / 'extra.js').write_text('// extra\n')))
        self.assertIn(f'node_modules/{PACKAGE} without the tarball\'s package/index.js',
                      installed_with(lambda d: (d / 'index.js').unlink()))

    def as_bun(self, resolved=None, integrity=None):
        """The wired consumer as Bun locks it: the tarball's absolute path and its integrity."""
        self.lock.unlink(missing_ok=True)
        path = str(self.tarball) if resolved is None else resolved
        (self.root / 'typescript' / 'bun.lock').write_text(
            '{"packages": {' + json.dumps(PACKAGE) + ': ' +
            json.dumps([f'{PACKAGE}@{path}', {}, integrity or self.integrity]) + ',},}\n')

    def test_origin_bun_lock_pinned_passes(self):
        self.as_bun()
        self.origin(only='typescript')

    def test_origin_bun_lock_registry_copy_is_rejected(self):
        self.lock.unlink()
        (self.root / 'typescript' / 'bun.lock').write_text(
            '{"packages": {' + json.dumps(PACKAGE) + ': ' +
            json.dumps([f'{PACKAGE}@0.0.1', '', {}, 'sha512-placeholder']) + ',},}\n')
        self.assertIn('0.0.1 from nowhere (a registry)', self.rejected(only='typescript').found)

    def test_origin_bun_lock_another_tarball_or_integrity_is_rejected(self):
        self.as_bun(resolved='/elsewhere/x.tgz')
        self.assertIn('from file:/elsewhere/x.tgz', self.rejected(only='typescript').found)
        self.as_bun(integrity='sha512-other')
        self.assertIn('with integrity sha512-other in the lock', self.rejected(only='typescript').found)

    def test_origin_no_bun_lock_entry_is_rejected(self):
        self.lock.unlink()
        (self.root / 'typescript' / 'bun.lock').write_text('{"packages": {},}\n')
        self.assertIn(f'no node_modules/{PACKAGE} entry in bun.lock', self.rejected(only='typescript').found)

    def test_bun_tag_passes_only_beside_a_bun_lock_and_only_at_the_top(self):
        installed = npm_install(self.root / 'typescript', self.tarball)
        (installed / '.bun-tag').write_text('tag')
        # Beside an npm lock nothing wrote it.
        self.assertIn('.bun-tag, which the tarball does not hold', self.rejected(installed=True, only='typescript').found)
        self.as_bun()
        self.origin(installed=True, only='typescript')
        (installed / 'sub').mkdir()
        (installed / 'sub' / '.bun-tag').write_text('tag')
        self.assertIn('sub/.bun-tag, which the tarball does not hold', self.rejected(installed=True, only='typescript').found)

    def test_origin_npm_registry_installs_read_a_bun_lock(self):
        registry = {PACKAGE: {'tarball': 'x', 'integrity': 'sha512-good'}}
        record = companion_wire.npm_dir(self.root, 'hypercast') / companion_wire.NPM_RECORD
        data = json.loads(record.read_text())
        data['registry'] = registry
        record.write_text(json.dumps(data))
        work = self.root / 'work'
        (work / 'bun-install').mkdir(parents=True)
        lock = work / 'bun-install' / 'bun.lock'
        lock.write_text('{"packages": {' + json.dumps(PACKAGE) + ': ' + json.dumps([f'{PACKAGE}@1.0.0', '', {}, 'sha512-good']) + ',},}')
        companion_origin.origin_npm_installs(self.root, NPM_ENV, work, expect=['bun-install'])
        lock.write_text('{"packages": {' + json.dumps(PACKAGE) + ': ' + json.dumps([f'{PACKAGE}@1.0.0', '', {}, 'sha512-bad']) + ',},}')
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_npm_installs(self.root, NPM_ENV, work, expect=['bun-install'])
        self.assertIn('with integrity sha512-bad', caught.exception.found)

    def test_origin_npm_another_tarball_is_rejected(self):
        shutil.copyfile(self.tarball, self.root / 'elsewhere.tgz')
        self.entry(resolved='file:../elsewhere.tgz')
        self.assertEqual(self.rejected().found, f'{PACKAGE} 1.0.0 from file:../elsewhere.tgz')

    def test_origin_npm_no_lock_entry_is_rejected(self):
        lock = json.loads(self.lock.read_text())
        del lock['packages'][f'node_modules/{PACKAGE}']
        self.lock.write_text(json.dumps(lock))
        self.assertIn(f'no node_modules/{PACKAGE} entry', self.rejected().found)

    def test_origin_npm_one_consumer(self):
        # A step proves the consumer it installed: browser-smoke, which this addon leg leaves
        # alone, is no consumer at all, and typescript/ is.
        self.origin(only='typescript', installed=False)
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(only='typescript/browser-smoke')
        self.assertIn('browser-smoke requiring a wired package', caught.exception.found)

    def test_origin_npm_installed_needs_the_install(self):
        # Asked after the install: nothing installed, or Node finding another copy, fails.
        self.assertIn('Cannot find module', self.rejected(installed=True, run=Resolved(lambda cwd: None)).found)
        npm_install(self.root / 'typescript', self.tarball)
        hoisted = npm_install(self.root, self.tarball)
        error = self.rejected(installed=True, run=Resolved(lambda cwd: hoisted / 'package.json'))
        self.assertIn(f'Node resolves {PACKAGE} to {hoisted.as_posix()}', error.found)

    def test_origin_npm_registry_installs_are_the_pinned_set(self):
        # The consumer tests' scenarios install from the job's Verdaccio: each lock's integrity for
        # a companion package must be the one the pinned set packed.
        registry = self.root / '.companions' / 'hypercast-npm' / 'registry'
        registry.mkdir()
        shutil.copyfile(self.tarball, registry / self.tarball.name)
        record_path = self.root / '.companions' / 'hypercast-npm' / 'wired.json'
        record = json.loads(record_path.read_text())
        record['registry'] = {PACKAGE: {'tarball': f'.companions/hypercast-npm/registry/{self.tarball.name}',
                                        'integrity': npm_integrity(self.tarball)}}
        record_path.write_text(json.dumps(record))
        work = self.root / 'typescript' / 'consumer-tests' / 'work'
        url = 'http://127.0.0.1:4873/@skunkwerkx/hypercast/-/hypercast-1.0.0.tgz'
        for scenario in ('plain', 'bundlers'):
            (work / scenario).mkdir(parents=True)
            (work / scenario / 'package-lock.json').write_text(json.dumps({'packages': {
                '': {}, f'node_modules/{PACKAGE}': {'version': '1.0.0', 'resolved': url,
                                                    'integrity': npm_integrity(self.tarball)}}}))
        companion_origin.origin_npm_installs(self.root, NPM_ENV, work)
        (work / 'bundlers' / 'package-lock.json').write_text(json.dumps({'packages': {
            f'node_modules/{PACKAGE}': {'version': '1.0.0', 'resolved': url, 'integrity': 'sha512-npmjs'}}}))
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_npm_installs(self.root, NPM_ENV, work)
        self.assertEqual(caught.exception.binding, 'npm (typescript/consumer-tests/work/bundlers/)')
        self.assertIn('sha512-npmjs', caught.exception.found)
        # Each scenario the caller names must have left a lock resolving a companion package: one
        # that installed without a lock (or with another package manager) is not passed over.
        (work / 'bundlers' / 'package-lock.json').write_text(json.dumps({'packages': {
            f'node_modules/{PACKAGE}': {'version': '1.0.0', 'resolved': url, 'integrity': npm_integrity(self.tarball)}}}))
        companion_origin.origin_npm_installs(self.root, NPM_ENV, work, expect=['plain', 'bundlers'])
        (work / 'consumer two').mkdir()
        (work / 'consumer two' / 'yarn.lock').write_text('')
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_npm_installs(self.root, NPM_ENV, work, expect=['plain', 'consumer two'])
        self.assertEqual(caught.exception.binding, 'npm (typescript/consumer-tests/work/consumer two/)')
        self.assertIn('no bun.lock or package-lock.json resolving a companion package', caught.exception.found)
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_npm_installs(self.root, NPM_ENV, work, expect=['floor'])
        self.assertIn('no bun.lock or package-lock.json resolving a companion package', caught.exception.found)
        # The CLI takes the names comma-separated.
        (self.root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in NPM_ENV.items()))
        cli = ['npm', '--root', str(self.root), '--installs', str(work), '--expect']
        self.assertEqual(companion_origin.main([*cli, 'plain,bundlers']), 0)
        self.assertEqual(companion_origin.main([*cli, 'plain,consumer two']), 1)
        # Scenarios that installed no companion package at all prove nothing.
        shutil.rmtree(work)
        work.mkdir()
        with self.assertRaises(companion_origin.OriginError):
            companion_origin.origin_npm_installs(self.root, NPM_ENV, work)


class Bundled:
    """`bundle info <name> --path` and `bundle exec ruby companion_ruby.rb <name>` answered from
    fixtures/origin/ruby/, the recorded hashes swapped for the test's staged bytes'."""
    # The hashes HyperCast 14df722's real binaries had when the facts were recorded.
    RECORDED = {'9cd4ac42c21c930f94defd4951172d9d1a3ca3b3b06e300bfe03275e38d15936': b'magnus 4.0',
                '0e96b9e05b68783ebbb01e5b011b2dc066c64387cb733b6f92cab2b152a1ac02': b'fiddle library'}

    def __init__(self, root, path='pinned.path', facts='pinned-4.0.facts', edit=None, info_code=0):
        self.root, self.path, self.facts, self.edit, self.info_code = Path(root), path, facts, edit, info_code
        self.calls = []

    def text(self, name):
        text = (FIXTURES / 'ruby' / name).read_text().replace('@ROOT@', str(self.root)).replace('@HOME@', HOME)
        for recorded, data in self.RECORDED.items():
            text = text.replace(recorded, hashlib.sha256(data).hexdigest())
        return self.edit(text) if self.edit else text

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append((argv, cwd))
        if argv[1:3] == ['info', 'hypercast']:
            if self.info_code:
                return subprocess.CompletedProcess(argv, self.info_code, '', 'Could not find gem \'hypercast\'.\n')
            return subprocess.CompletedProcess(argv, 0, self.text(self.path), '')
        return subprocess.CompletedProcess(argv, 0, self.text(self.facts), '')


class OriginRubyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = ruby_checkout(Path(self.tmp.name) / 'consumer')
        self.gem = self.root / '.companions' / 'hypercast' / 'ruby'
        companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.sums = companion_origin.read_sums(self.root, RUBY_ENV)

    def origin(self, runner=None, **kwargs):
        companion_origin.origin_ruby(self.root, RUBY_ENV, self.sums, run=runner or Bundled(self.root), **kwargs)

    def rejected(self, runner=None, **kwargs):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(runner, **kwargs)
        self.assertIn('hypercast 1.0.0 from ', caught.exception.expected)
        self.assertIn(self.gem.as_posix(), caught.exception.expected)
        return caught.exception

    def test_origin_ruby_pinned_passes(self):
        # Right after the wiring: the manifests and the staged binaries, no tool run.
        runner = Bundled(self.root)
        self.origin(runner)
        self.assertEqual(runner.calls, [])
        # After the install, in the process that runs the suite: Bundler's path, then what
        # Ruby loaded, on the Magnus backend (4.0) and on Fiddle (3.3, no extension staged).
        for facts in ('pinned-4.0.facts', 'pinned-3.3.facts'):
            with self.subTest(facts=facts):
                runner = Bundled(self.root, facts=facts)
                self.origin(runner, installed=True)
                ruby = str(SCRIPTS / 'companion_ruby.rb')
                self.assertEqual([argv[1:] for argv, _ in runner.calls],
                                 [['info', 'hypercast', '--path'], ['exec', 'ruby', ruby, 'hypercast']])
                self.assertEqual({cwd for _, cwd in runner.calls}, {self.root / 'ruby'})

    def test_origin_ruby_registry_copy_is_rejected(self):
        # Bundler resolved the gem from RubyGems (HyperCast 1.0.0 is there): the path names it.
        error = self.rejected(Bundled(self.root, path='registry.path', facts='registry.facts'), installed=True)
        self.assertEqual(error.binding, 'Ruby (ruby/)')
        self.assertEqual(error.found, f'hypercast from {HOME}/vendor/bundle/ruby/4.0.0/gems/hypercast-1.0.0-arm64-darwin '
                                      '(RubyGems, not the pin)')
        # Bundler's path the pin's, but Ruby required another copy (a gem on RUBYLIB, say).
        error = self.rejected(Bundled(self.root, facts='registry.facts'), installed=True)
        self.assertIn(f'{HOME}/vendor/bundle/ruby/4.0.0/gems/hypercast-1.0.0-arm64-darwin/lib/hypercast.rb', error.found)
        # Not in the bundle at all.
        error = self.rejected(Bundled(self.root, info_code=7), installed=True)
        self.assertIn('bundle info failed (exit 7)', error.found)
        # Unwired manifests (the leg installed before, or without, the wiring): Bundler's answer
        # is what names the copy, before the manifests are looked at.
        unwired = ruby_checkout(Path(self.tmp.name) / 'unwired')
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_ruby(unwired, RUBY_ENV, self.sums, installed=True,
                                         run=Bundled(unwired, path='registry.path', facts='registry.facts'))
        self.assertIn('(RubyGems, not the pin)', caught.exception.found)

    def test_origin_ruby_tampered_hash_is_rejected(self):
        # A staged binary swapped after the wiring placed it: the files, then what Ruby loaded.
        extension = self.gem / 'lib' / 'hypercast' / '4.0' / 'hypercast_native.bundle'
        extension.write_bytes(b'another build')
        error = self.rejected()
        self.assertIn('lib/hypercast/4.0/hypercast_native.bundle', error.found)
        self.assertIn(hashlib.sha256(b'another build').hexdigest(), error.found)
        extension.write_bytes(b'magnus 4.0')
        for facts, field in (('pinned-4.0.facts', 'extension_sha256'), ('pinned-3.3.facts', 'library_sha256')):
            with self.subTest(facts=facts):
                swapped = Bundled(self.root, facts=facts,
                                  edit=lambda t, f=field: re.sub(rf'^{f}=.*$', f'{f}={"0" * 64}', t, flags=re.M))
                error = self.rejected(swapped, installed=True)
                self.assertIn('0' * 64, error.found)

    def test_origin_ruby_backend_must_be_the_staged_one(self):
        # Magnus staged for 4.0 but Fiddle loaded (no HYPERCAST_PURE): the extension never ran.
        fiddle = Bundled(self.root, edit=lambda t: re.sub(r'^backend=.*$', 'backend=fiddle', t, flags=re.M))
        error = self.rejected(fiddle, installed=True)
        self.assertIn('the Fiddle backend', error.found)
        # With HYPERCAST_PURE set, Fiddle is what the step asked for.
        pure = Bundled(self.root, facts='pinned-3.3.facts',
                       edit=lambda t: re.sub(r'^abi=.*$', 'abi=4.0', re.sub(r'^pure=.*$', 'pure=1', t, flags=re.M),
                                             flags=re.M))
        self.origin(pure, installed=True)
        # An extension loaded from outside the pin's staged files.
        stray = Bundled(self.root, edit=lambda t: t.replace('/4.0/hypercast_native.bundle', '/hypercast_native.bundle'))
        error = self.rejected(stray, installed=True)
        self.assertIn('lib/hypercast/hypercast_native.bundle', error.found)
        # Another version reported by the pinned source (a stale lib/ on RUBYLIB shadowing it).
        version = Bundled(self.root, edit=lambda t: t.replace('version=1.0.0', 'version=1.1.0'))
        self.assertIn('1.1.0', self.rejected(version, installed=True).found)

    def test_origin_ruby_fiddle_library_is_the_one_mapped(self):
        # The probe forces the Fiddle load and reports what this process mapped (Linux's
        # /proc/self/maps, macOS's dyld images): the gem's search answering the pin while another
        # copy is what the dlopen took is refused, as is a load that never happened.
        pinned = str(self.gem / 'lib' / 'hypercast' / 'native' / 'osx-arm64' / 'libhypercast.dylib')
        other = f'{HOME}/vendor/bundle/ruby/3.3.0/gems/hypercast-1.0.0/lib/hypercast/native/osx-arm64/libhypercast.dylib'
        for mapped, needle in ((other, other), (f'{pinned},{other}', other), ('', 'mapping no')):
            with self.subTest(mapped=mapped):
                swapped = Bundled(self.root, facts='pinned-3.3.facts',
                                  edit=lambda t, m=mapped: re.sub(r'^mapped=.*$', f'mapped={m}', t, flags=re.M))
                error = self.rejected(swapped, installed=True)
                self.assertIn(needle, error.found)
        # Where the process's mappings can't be read (Windows), the gem's own search is the proof.
        unobserved = Bundled(self.root, facts='pinned-3.3.facts',
                             edit=lambda t: re.sub(r'^mapped=.*$', 'mapped=', re.sub(r'^observed=.*$', 'observed=', t,
                                                                                     flags=re.M), flags=re.M))
        self.origin(unobserved, installed=True)

    def test_origin_ruby_unwired_manifests_are_rejected(self):
        gemfile = self.root / 'ruby' / 'Gemfile'
        gemfile.write_text(gemfile.read_text().split('\n# The pinned hypercast')[0] + '\n')
        error = self.rejected()
        self.assertEqual(error.binding, 'Ruby (ruby/Gemfile)')
        self.assertIn('no gem', error.found)
        companion_wire.wire_ruby(self.root, RUBY_ENV)
        spec = self.root / 'ruby' / 'hypertabular.gemspec'
        spec.write_text(spec.read_text().replace('"= 1.0.0"', '"~> 1.0"'))
        error = self.rejected()
        self.assertIn('ruby/hypertabular.gemspec', error.binding)
        self.assertIn('~> 1.0', error.found)

    def test_origin_ruby_without_binaries_asks_bundler_only(self):
        # The lint leg stages nothing, so nothing there loads the gem: Bundler's path is the proof.
        root = ruby_checkout(Path(self.tmp.name) / 'lint', {})
        companion_wire.wire_ruby(root, RUBY_ENV)
        runner = Bundled(root)
        companion_origin.origin_ruby(root, RUBY_ENV, companion_origin.read_sums(root, RUBY_ENV), run=runner,
                                     installed=True)
        self.assertEqual([argv[1:] for argv, _ in runner.calls], [['info', 'hypercast', '--path']])
        with self.assertRaises(companion_origin.OriginError):
            companion_origin.origin_ruby(root, RUBY_ENV, {}, run=Bundled(root, path='registry.path'), installed=True)

    def test_origin_ruby_one_consumer(self):
        runner = Bundled(self.root)
        self.origin(runner, installed=True, only=Path('ruby'))
        self.assertEqual({cwd for _, cwd in runner.calls}, {self.root / 'ruby'})
        with self.assertRaises(companion_origin.OriginError):
            self.origin(runner, installed=True, only=Path('ruby/wasm-smoke'))

    def test_probe_takes_the_name_as_an_argument(self):
        text = (SCRIPTS / 'companion_ruby.rb').read_text()
        self.assertIn('ARGV.fetch(0)', text)
        self.assertNotIn('hypercast', text.lower().replace('companion_ruby', ''))

    @unittest.skipUnless(shutil.which('ruby') and shutil.which('cc') and sys.platform in ('darwin', 'linux'),
                         'no ruby or cc, or a platform whose mappings the probe cannot read')
    def test_probe_observes_the_library_fiddle_opened(self):
        # A gem of HyperCast's Fiddle shape: native_version dlopens what Runtime.library_path
        # answers. The probe forces that load and reports the mapping; a gem that opens another
        # copy than its search names is caught by the mapping, not by the search.
        ext = 'dylib' if sys.platform == 'darwin' else 'so'
        tmp = Path(self.tmp.name)
        lib = tmp / 'lib'
        native = lib / 'probed' / 'native'
        native.mkdir(parents=True)
        (tmp / 'probed.c').write_text('unsigned probed_version(void) { return 0x020304; }\n')
        for target in (native / f'libprobed.{ext}', tmp / 'decoy' / f'libprobed.{ext}'):
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['cc', '-shared', '-fPIC', '-o', str(target), str(tmp / 'probed.c')], check=True)
        gem = ('require "fiddle"\nmodule Probed\n  VERSION = "2.3.4"\n  BACKEND = :fiddle\n'
               '  module Runtime\n    class << self\n      private\n'
               f'      def library_path = File.join(__dir__, "probed", "native", "libprobed.{ext}")\n'
               '    end\n  end\n'
               '  def self.native_version\n'
               '    Fiddle.dlopen(ENV["PROBED_OPEN"] || Runtime.send(:library_path))\n    "2.3.4"\n  end\nend\n')
        (lib / 'probed.rb').write_text(gem)
        probe = [shutil.which('ruby'), str(SCRIPTS / 'companion_ruby.rb'), 'probed']
        environ = {**os.environ, 'RUBYLIB': str(lib)}
        environ.pop('PROBED_OPEN', None)
        result = subprocess.run(probe, capture_output=True, text=True, env=environ)
        self.assertEqual(result.returncode, 0, result.stderr)
        facts = dict(line.split('=', 1) for line in result.stdout.splitlines())
        pinned = str((native / f'libprobed.{ext}').resolve())
        self.assertEqual((facts['backend'], facts['library'], facts['observed'], facts['mapped']),
                         ('fiddle', pinned, '1', pinned))
        self.assertEqual(facts['library_sha256'], hashlib.sha256(Path(pinned).read_bytes()).hexdigest())
        decoy = str((tmp / 'decoy' / f'libprobed.{ext}').resolve())
        result = subprocess.run(probe, capture_output=True, text=True, env={**environ, 'PROBED_OPEN': decoy})
        facts = dict(line.split('=', 1) for line in result.stdout.splitlines())
        self.assertEqual((facts['library'], facts['mapped']), (pinned, decoy))

    @unittest.skipUnless(shutil.which('ruby'), 'no ruby on PATH')
    def test_probe_on_a_real_ruby(self):
        # A gem of the probe's shape, required from RUBYLIB: the facts are its files', the
        # extension and library absent, so their hashes are empty.
        lib = Path(self.tmp.name) / 'lib'
        (lib / 'probed').mkdir(parents=True)
        (lib / 'probed.rb').write_text('module Probed\n  VERSION = "2.3.4"\n  BACKEND = :fiddle\nend\n')
        result = subprocess.run(['ruby', str(SCRIPTS / 'companion_ruby.rb'), 'probed'], capture_output=True, text=True,
                                env={**os.environ, 'RUBYLIB': str(lib)})
        self.assertEqual(result.returncode, 0, result.stderr)
        facts = dict(line.split('=', 1) for line in result.stdout.splitlines())
        self.assertEqual((facts['version'], facts['backend'], facts['extension'], facts['source']),
                         ('2.3.4', 'fiddle', '', str((lib / 'probed.rb').resolve())))
        result = subprocess.run(['ruby', str(SCRIPTS / 'companion_ruby.rb'), 'x; system("id")'], capture_output=True,
                                text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('is not a gem name', result.stderr)


class WasmBundled:
    """`bundle info hypercast-wasm --path` in the smoke directory, answering `path`."""
    def __init__(self, path, code=0):
        self.path, self.code, self.calls = str(path), code, []

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append((argv, cwd))
        if self.code:
            return subprocess.CompletedProcess(argv, self.code, '', "Could not find gem 'hypercast-wasm'.\n")
        return subprocess.CompletedProcess(argv, 0, self.path + '\n', '')


def fake_wasm_gem(root, archives, gem=None):
    """What suite-ruby-wasm.sh leaves at WASM_GEM: the pin's `rake wasm:gem[<abi>]` unpacked,
    its lib/ the pin's and each archive gzipped under ext/hypercast_native/<abi>/."""
    import gzip
    wasm = Path(gem or Path(root) / WASM_GEM)
    shutil.copytree(Path(root) / '.companions' / 'hypercast' / 'ruby' / 'lib', wasm / 'lib')
    (wasm / 'hypercast-wasm.gemspec').write_text('Gem::Specification.new { |s| s.name = "hypercast-wasm" }\n')
    for abi, data in archives.items():
        target = wasm / 'ext' / 'hypercast_native' / abi / 'hypercast_native.a.gz'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(data, mtime=0))
    return wasm


class OriginRubyWasmTests(unittest.TestCase):
    """build-ruby-wasm, between the smoke Gemfile's `bundle install` and `rbwasm build`: Bundler
    takes hypercast-wasm from the gem the pin packed, and that gem carries the leg's
    ruby-wasm-<abi> archive with SHA256SUMS's hash and the pin's lib/."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = ruby_checkout(Path(self.tmp.name) / 'consumer', WASM_ARTIFACTS)
        companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.sums = companion_origin.read_sums(self.root, RUBY_ENV)
        self.wasm = fake_wasm_gem(self.root, {'4.0': b'wasm 4.0 archive'})
        self.smoke = Path('ruby/wasm-smoke')

    def origin(self, runner=None):
        companion_origin.origin_ruby_wasm(self.root, RUBY_ENV, self.sums, run=runner or WasmBundled(self.wasm),
                                          only=self.smoke)

    def rejected(self, runner=None):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(runner)
        self.assertIn(f'hypercast-wasm 1.0.0 from {self.wasm.as_posix()}', caught.exception.expected)
        return caught.exception

    def test_origin_ruby_wasm_pinned_passes(self):
        runner = WasmBundled(self.wasm)
        self.origin(runner)
        self.assertEqual([argv[1:] for argv, _ in runner.calls], [['info', 'hypercast-wasm', '--path']])
        self.assertEqual({cwd for _, cwd in runner.calls}, {self.root / 'ruby' / 'wasm-smoke'})
        # Right after the wiring (no gem packed yet), the static proof: the placed archive and the
        # smoke Gemfile's path.
        shutil.rmtree(self.wasm)
        companion_origin.origin_ruby(self.root, RUBY_ENV, self.sums, run=WasmBundled(self.wasm))

    def test_origin_ruby_wasm_registry_copy_is_rejected(self):
        # The smoke Gemfile without the wiring's line: Bundler took RubyGems' hypercast-wasm 1.0.0.
        registry = f'{HOME}/.gem/ruby/4.0.0/gems/hypercast-wasm-1.0.0'
        error = self.rejected(WasmBundled(registry))
        self.assertEqual(error.binding, 'Ruby wasm (ruby/wasm-smoke/)')
        self.assertEqual(error.found, f'hypercast-wasm from {registry} (RubyGems, not the pin)')
        self.assertIn('bundle info failed (exit 7)', self.rejected(WasmBundled(self.wasm, code=7)).found)
        # And statically, right after a wiring whose smoke Gemfile line went missing.
        smoke = self.root / 'ruby' / 'wasm-smoke' / 'Gemfile'
        smoke.write_text(smoke.read_text().split('\n# The pinned hypercast-wasm')[0] + '\n')
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_ruby(self.root, RUBY_ENV, self.sums)
        self.assertIn('ruby/wasm-smoke/Gemfile', caught.exception.binding)
        self.assertIn('rubygems.org resolves it', caught.exception.found)

    def test_origin_ruby_wasm_tampered_hash_is_rejected(self):
        gz = self.wasm / 'ext' / 'hypercast_native' / '4.0' / 'hypercast_native.a.gz'
        import gzip
        gz.write_bytes(gzip.compress(b'another build', mtime=0))
        error = self.rejected()
        self.assertIn('ext/hypercast_native/4.0/hypercast_native.a.gz', error.found)
        self.assertIn(hashlib.sha256(b'another build').hexdigest(), error.found)
        gz.unlink()
        self.assertIn('ext/hypercast_native/4.0/hypercast_native.a.gz', self.rejected().found)

    def test_origin_ruby_wasm_carries_only_the_pins_files(self):
        # Another minor's archive beside the leg's (a gem packed from elsewhere), and a lib/ file
        # that is not the pin's.
        fake_wasm_gem(self.root, {'3.4': b'registry 3.4'}, gem=Path(self.tmp.name) / 'other')
        shutil.copytree(Path(self.tmp.name) / 'other' / 'ext' / 'hypercast_native' / '3.4',
                        self.wasm / 'ext' / 'hypercast_native' / '3.4')
        self.assertIn('ext/hypercast_native/3.4/hypercast_native.a.gz', self.rejected().found)
        shutil.rmtree(self.wasm / 'ext' / 'hypercast_native' / '3.4')
        (self.wasm / 'lib' / 'hypercast.rb').write_text('module HyperCast\n  VERSION = "1.0.0" # registry\nend\n')
        self.assertIn('lib/hypercast.rb', self.rejected().found)

    def test_origin_ruby_wasm_needs_the_legs_archive(self):
        root = ruby_checkout(Path(self.tmp.name) / 'native', RUBY_ARTIFACTS)
        companion_wire.wire_ruby(root, RUBY_ENV)
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_ruby_wasm(root, RUBY_ENV, companion_origin.read_sums(root, RUBY_ENV),
                                              run=WasmBundled(self.wasm), only=self.smoke)
        self.assertIn('no ruby-wasm-<abi> archive', caught.exception.found)


class Probed:
    """`php [-d extension=<file>] companion_php.php <name>` answered from fixtures/origin/php/:
    the bare PHP's answer without the flag, the flagged one's with it."""
    def __init__(self, bare='bare.json', flagged='pinned.json', stderr='', edit=None):
        self.bare, self.flagged, self.stderr, self.edit = bare, flagged, stderr, edit
        self.calls = []

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append(argv)
        flagged = any(a.startswith('extension=') for a in argv)
        stdout = (FIXTURES / 'php' / (self.flagged if flagged else self.bare)).read_text()
        stderr = (FIXTURES / 'php' / self.stderr).read_text() if flagged and self.stderr else ''
        return subprocess.CompletedProcess(argv, 0, self.edit(stdout) if self.edit and flagged else stdout, stderr)


class OriginPhpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = php_checkout(Path(self.tmp.name) / 'consumer')
        companion_wire.wire_php(self.root, PHP_ENV, environ={})
        self.extension = (self.root / PHP_EXTENSION).as_posix()
        self.sums = companion_origin.read_sums(self.root, PHP_ENV)
        self.environ = {'HYPERCAST_PHP_EXTENSION': self.extension}

    def origin(self, runner=None, environ=None, **kwargs):
        companion_origin.origin_php(self.root, PHP_ENV, self.sums, run=runner or Probed(),
                                    environ=self.environ if environ is None else environ, **kwargs)

    def rejected(self, runner=None, environ=None, **kwargs):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(runner, environ, **kwargs)
        self.assertEqual(caught.exception.expected, f'hypercast 1.0.0 loaded from {self.extension} (the pin), '
                                                    'and from nowhere else')
        return caught.exception

    def test_origin_php_pinned_passes(self):
        runner = Probed()
        self.origin(runner)
        self.assertEqual(runner.calls, [])
        self.origin(runner, installed=True)
        probe = str(SCRIPTS / 'companion_php.php')
        flags = ['-d', 'display_startup_errors=1', '-d', 'display_errors=stderr']
        self.assertEqual(runner.calls, [['php', *flags, probe, 'hypercast'],
                                        ['php', '-d', f'extension={self.extension}', *flags, probe, 'hypercast']])

    def test_origin_php_registry_copy_is_rejected(self):
        # PIE installed an extension this PHP loads from its ini: the suite's -d flag then only
        # warns, and the ini's copy is the one that runs.
        error = self.rejected(Probed(bare='ini.json', stderr='ini.stderr'), installed=True)
        self.assertEqual(error.found, 'hypercast 1.0.0 loaded by this PHP without the pin (its ini, or PIE\'s)')
        self.assertEqual(error.binding, 'PHP 8.5.12 (php)')
        # The startup warning alone (a bare probe that missed it) is caught too.
        error = self.rejected(Probed(stderr='ini.stderr'), installed=True)
        self.assertIn('Module "hypercast" is already loaded', error.found)

    def test_origin_php_tampered_hash_is_rejected(self):
        (self.root / PHP_EXTENSION).write_bytes(b'another build')
        error = self.rejected()
        self.assertIn(hashlib.sha256(b'another build').hexdigest(), error.found)
        self.assertIn('.companions/hypercast-php/hypercast.so', error.found)
        (self.root / PHP_EXTENSION).write_bytes(b'ext-hypercast')
        (self.root / PHP_ZIP).write_bytes(php_zip({'hypercast.so': b'ext-hypercast built again'}))
        self.assertIn('SHA256SUMS', self.rejected().found)

    def test_origin_php_missing_origin_is_named(self):
        # No extension wired: the variable the suite loads is unset, so it loads whatever this PHP
        # or a sibling checkout has.
        error = self.rejected(environ={}, installed=True)
        self.assertEqual(error.found, 'HYPERCAST_PHP_EXTENSION unset: the suite would load whatever this PHP or a '
                                      'sibling checkout has, not the pin')
        error = self.rejected(environ={'HYPERCAST_PHP_EXTENSION': '/runner/php-ext/hypercast.so'}, installed=True)
        self.assertIn('/runner/php-ext/hypercast.so', error.found)
        # The flag given and nothing loaded (the file is not a PHP extension, or not for this PHP).
        error = self.rejected(Probed(flagged='bare.json'), installed=True)
        self.assertIn('not loaded', error.found)
        # Another version reported.
        error = self.rejected(Probed(edit=lambda t: t.replace('"1.0.0"', '"1.1.0"')), installed=True)
        self.assertIn('1.1.0', error.found)

    def test_origin_php_mapped_files_are_the_pin(self):
        # On Linux the probe names the files mapped into the process: only the pin's may be.
        pinned = json.dumps({'php': '8.5.0', 'loaded': True, 'version': '1.0.0', 'mapped': [self.extension]})
        self.origin(Probed(edit=lambda t: pinned), installed=True)
        other = json.dumps({'php': '8.5.0', 'loaded': True, 'version': '1.0.0',
                            'mapped': [self.extension, '/usr/local/lib/php/extensions/hypercast.so']})
        self.assertIn('/usr/local/lib/php/extensions/hypercast.so',
                      self.rejected(Probed(edit=lambda t: other), installed=True).found)

    def test_probe_takes_the_name_as_an_argument(self):
        text = (SCRIPTS / 'companion_php.php').read_text()
        self.assertIn('$argv[1]', text)
        self.assertNotIn('hypercast', text.lower())

    @unittest.skipUnless(shutil.which('php'), 'no php on PATH')
    def test_probe_on_a_real_php(self):
        result = subprocess.run(['php', str(SCRIPTS / 'companion_php.php'), 'json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        facts = json.loads(result.stdout)
        self.assertTrue(facts['loaded'])
        result = subprocess.run(['php', str(SCRIPTS / 'companion_php.php'), 'x");system("id'], capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('is not an extension name', result.stderr)


class Gradled:
    """`gradlew -q --console=plain companionOrigin` answered from fixtures/origin/java/ (recorded
    with HyperTabular's build and the real init script): the pinned jar's lines, Maven Central's
    copy, a swapped jar, and a build with no init script."""
    def __init__(self, root, home, sha256, name='pinned', code=0):
        self.root, self.home, self.sha256, self.name, self.code = root, home, sha256, name, code
        self.calls = []

    def text(self, suffix):
        path = FIXTURES / 'java' / f'{self.name}.{suffix}'
        if not path.is_file():
            return ''
        return (path.read_text().replace('@ROOT@', self.root.as_posix()).replace('@HOME@', str(self.home))
                .replace('@SHA256@', self.sha256))

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append((argv, cwd))
        return subprocess.CompletedProcess(argv, self.code, self.text('stdout'), self.text('stderr'))


class OriginJavaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = java_checkout(Path(self.tmp.name) / 'consumer').resolve()
        self.home = Path(self.tmp.name).resolve() / 'home'
        self.environ = {'GRADLE_USER_HOME': str(self.home / '.gradle')}
        companion_wire.wire_java(self.root, JAVA_ENV, run=FakeGradle(), environ=self.environ)
        self.sha256 = hashlib.sha256((self.root / JAR).read_bytes()).hexdigest()
        self.sums = companion_origin.read_sums(self.root, JAVA_ENV)
        self.m2 = (self.root / '.companions' / 'm2').as_posix()

    def origin(self, runner=None, **kwargs):
        runner = runner or Gradled(self.root, self.home, self.sha256)
        companion_origin.origin_java(self.root, JAVA_ENV, self.sums, run=runner, environ=self.environ, **kwargs)
        return runner

    def rejected(self, runner=None, **kwargs):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(runner, **kwargs)
        self.assertEqual(caught.exception.expected,
                         f'io.github.skunkwerkx:hypercast:1.0.0 from {self.m2} (the pin, sha256 {self.sha256})')
        self.assertTrue(caught.exception.binding.startswith('Java'), caught.exception.binding)
        return caught.exception

    def test_origin_java_pinned_passes(self):
        runner = self.origin()
        self.assertEqual(runner.calls, [])
        runner = self.origin(installed=True)
        gradlew = 'gradlew.bat' if os.name == 'nt' else 'gradlew'
        self.assertEqual(runner.calls, [([str(self.root / 'java' / gradlew), '-q', '--console=plain', 'companionOrigin'],
                                         self.root / 'java')])

    def test_origin_java_registry_copy_is_rejected(self):
        # The init script without its repository: the force still pins 1.0.0, and Maven Central's
        # copy is what Gradle's module cache hands the build (recorded with a JDK 25 consumer;
        # HyperTabular's floor of 21 can't even select that jar).
        error = self.rejected(Gradled(self.root, self.home, self.sha256, 'registry', 1), installed=True)
        self.assertEqual(error.binding, 'Java (java/, :)')
        self.assertIn(f'{self.home}/.gradle/caches/modules-2/files-2.1/io.github.skunkwerkx/hypercast/1.0.0/', error.found)
        self.assertIn("a remote repository's copy (Maven Central, from Gradle's module cache)", error.found)
        # No init script at all: the build resolves the companion from its own repositories.
        error = self.rejected(Gradled(self.root, self.home, self.sha256, 'unwired', 1), installed=True)
        self.assertIn("no companionOrigin task in the build: the init script is not installed", error.found)
        (self.home / '.gradle' / 'init.d' / 'companions.gradle').unlink()
        error = self.rejected()
        self.assertIn('no Gradle init script', error.found)
        self.assertIn("the build's own repositories (Maven Central)", error.found)

    def test_origin_java_a_copy_only_a_suite_task_loads_is_rejected(self):
        # Recorded with `dependencies { testRuntimeOnly(files("<Central's jar>")) }` added to
        # HyperTabular's build: no module classpath selects it, but :test and :testFloor hand it
        # to their JVMs beside the pin's jar.
        error = self.rejected(Gradled(self.root, self.home, self.sha256, 'files', 1), installed=True)
        self.assertEqual(error.binding, 'Java (java/, :test)')
        self.assertIn(f'{self.home}/central/hypercast-1.0.0.jar', error.found)
        self.assertIn('a copy from outside the job-local repository', error.found)

    def test_origin_java_compares_canonical_paths(self):
        # Gradle names the jar through a symlinked root (macOS's /tmp, a linked workspace): the
        # same file, so the proof holds; a string prefix check would call it foreign.
        link = Path(self.tmp.name).resolve() / 'linked'
        link.symlink_to(self.root, target_is_directory=True)
        runner = self.origin(Gradled(link, self.home, self.sha256), installed=True)
        self.assertEqual(len(runner.calls), 1)

    def test_origin_java_tampered_hash_is_rejected(self):
        # The jar in the job-local repository swapped after the publish (recorded: Maven Central's
        # bytes under the pin's path).
        error = self.rejected(Gradled(self.root, self.home, self.sha256, 'swapped', 1), installed=True)
        self.assertIn(f'{self.m2}/io/github/skunkwerkx/hypercast/1.0.0/hypercast-1.0.0.jar', error.found)
        self.assertIn('2acd78c17c1f2793b89c2c80c2502b3fcb916f790d7b01633de7e96bef869805', error.found)
        # The same on disk, found by the static check.
        jar = self.root / JAR
        good = jar.read_bytes()
        jar.write_bytes(b'another jar')
        self.assertIn(hashlib.sha256(b'another jar').hexdigest(), self.rejected().found)
        jar.write_bytes(good)
        # A staged native that differs from SHA256SUMS: the jar carries bytes the pinned run never built.
        sums = dict(self.sums)
        sums['jni-osx-arm64/libhypercast_jni.dylib'] = '0' * 64
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_java(self.root, JAVA_ENV, sums, run=Gradled(self.root, self.home, self.sha256),
                                         environ=self.environ)
        self.assertIn('native/osx-arm64/libhypercast_jni.dylib', caught.exception.found)
        # Its properties edited after the wiring (another repository), then the script itself.
        properties = self.home / '.gradle' / 'init.d' / 'companions.properties'
        good = properties.read_bytes()
        properties.write_text(properties.read_text(encoding='latin-1').replace('/.companions/m2', '/elsewhere'),
                              encoding='latin-1')
        self.assertIn('differs from the one the wiring wrote', self.rejected().found)
        properties.write_bytes(good)
        script = self.home / '.gradle' / 'init.d' / 'companions.gradle'
        script.write_text(script.read_text().replace('exclusiveContent', 'content'))
        self.assertIn('differs from the one the wiring wrote', self.rejected().found)

    def test_origin_java_a_line_without_the_companion_proves_nothing(self):
        # Gradle passing with no companion line (a build that never resolved it) is no proof.
        runner = Gradled(self.root, self.home, self.sha256, 'unwired', 0)
        error = self.rejected(runner, installed=True)
        self.assertIn('no companion-origin line', error.found)

    def test_init_script_reads_its_data_and_proves_what_the_suites_load(self):
        text = (SCRIPTS / 'companion_java.init.gradle').read_text()
        # No field for a renderer to fill: the data is the properties file beside the script.
        self.assertEqual(re.findall(r'@[A-Z0-9]+@', text), [])
        self.assertIn("new File(initscript.sourceFile.parentFile, 'companions.properties')", text)
        # Both module classpaths, and the classpath of every Test and JavaExec task (what the
        # suites load), each of which waits on the proof.
        # The scan realizes every such task when the proof is in the graph: a lazily registered
        # task (`gradlew companionOrigin` alone, or a suite task's own run) is otherwise never seen.
        for needle in ("['runtimeClasspath', 'testRuntimeClasspath']",
                       '    p.tasks.withType(Test).configureEach { t -> t.dependsOn(origin) }\n',
                       '    p.tasks.withType(JavaExec).configureEach { t -> t.dependsOn(origin) }\n',
                       '        (p.tasks.withType(Test).toList() + p.tasks.withType(JavaExec).toList()).each { t ->\n'
                       '            loaded << [t.path, { t.classpath }]\n',
                       'loaded.each { path, classpath ->', 'found << [path, '):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


class OriginCsharpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = csharp_checkout(Path(self.tmp.name) / 'consumer').resolve()
        companion_wire.wire_csharp(self.root, CSHARP_ENV, run=FakeDotnetPack(), environ={})
        self.sums = companion_origin.read_sums(self.root, CSHARP_ENV)
        self.packages = Path(self.tmp.name).resolve() / 'nuget-packages'
        self.feed = (self.root / '.companions' / 'nuget').as_posix()
        import base64
        self.content_hash = base64.b64encode(hashlib.sha512((self.root / NUPKG).read_bytes()).digest()).decode()

    def restore(self, metadata='pinned', nupkg=None, assets=None):
        """What `dotnet restore` leaves: the assets file naming the global packages folder, and in
        that folder the package's .nupkg.metadata (recorded) and its copy of the nupkg."""
        folder = self.packages / 'hypercast' / '1.0.0'
        folder.mkdir(parents=True, exist_ok=True)
        text = (FIXTURES / 'csharp' / f'{metadata}.nupkg.metadata').read_text()
        (folder / '.nupkg.metadata').write_text(text.replace('@ROOT@', self.root.as_posix())
                                                .replace('@CONTENT_HASH@', self.content_hash))
        (folder / 'hypercast.1.0.0.nupkg').write_bytes(nupkg or (self.root / NUPKG).read_bytes())
        obj = self.root / 'csharp' / 'HyperTabular' / 'obj'
        obj.mkdir(parents=True, exist_ok=True)
        text = assets or (FIXTURES / 'csharp' / 'project.assets.json').read_text()
        (obj / 'project.assets.json').write_text(text.replace('@NUGET_PACKAGES@', self.packages.as_posix())
                                                 .replace('@CONTENT_HASH@', self.content_hash))

    def origin(self, **kwargs):
        companion_origin.origin_csharp(self.root, CSHARP_ENV, self.sums, **kwargs)

    def rejected(self, **kwargs):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(**kwargs)
        self.assertEqual(caught.exception.expected,
                         f'HyperCast 1.0.0 from {self.feed} (the pin, contentHash {self.content_hash})')
        self.assertTrue(caught.exception.binding.startswith('C#'), caught.exception.binding)
        return caught.exception

    def test_origin_csharp_pinned_passes(self):
        self.origin()
        self.restore()
        self.origin(installed=True)

    def test_origin_csharp_registry_copy_is_rejected(self):
        # The global packages folder already held nuget.org's 1.0.0 (an earlier restore, a cache):
        # NuGet never extracts an id and version twice, so that copy is what the build takes.
        self.restore('registry', nupkg=b'nuget.org bytes')
        error = self.rejected(installed=True)
        self.assertEqual(error.binding, 'C# (csharp/HyperTabular/)')
        self.assertIn("nuget.org's copy (source https://api.nuget.org/v3/index.json)", error.found)
        # Nothing restored: no proof at all.
        shutil.rmtree(self.root / 'csharp' / 'HyperTabular' / 'obj')
        self.assertIn('no project.assets.json', self.rejected(installed=True).found)
        # No nuget.config: every source the machine knows serves HyperCast.
        (self.root / 'nuget.config').unlink()
        self.assertIn('nuget.config', self.rejected().found)

    def test_origin_csharp_tampered_hash_is_rejected(self):
        # The global folder's copy, or its metadata, not the packed bytes.
        self.restore(nupkg=b'another package')
        self.assertIn(hashlib.sha512(b'another package').hexdigest()[:16], self.rejected(installed=True).found)
        self.restore(metadata='registry')
        self.restore()
        folder = self.packages / 'hypercast' / '1.0.0'
        metadata = json.loads((folder / '.nupkg.metadata').read_text())
        metadata['contentHash'] = 'AAAA'
        (folder / '.nupkg.metadata').write_text(json.dumps(metadata))
        self.assertIn('contentHash AAAA', self.rejected(installed=True).found)
        # The feed's nupkg swapped after the pack.
        nupkg = self.root / NUPKG
        good = nupkg.read_bytes()
        nupkg.write_bytes(b'swapped')
        self.assertIn(hashlib.sha512(b'swapped').hexdigest()[:16], self.rejected().found)
        nupkg.write_bytes(good)
        # A staged native the package carries that SHA256SUMS lists otherwise.
        sums = dict(self.sums)
        sums['native-osx-arm64/libhypercast.dylib'] = '0' * 64
        with self.assertRaises(companion_origin.OriginError) as caught:
            companion_origin.origin_csharp(self.root, CSHARP_ENV, sums)
        self.assertIn('runtimes/osx-arm64/native/libhypercast.dylib', caught.exception.found)

    def test_origin_csharp_another_version_or_requirement_is_rejected(self):
        text = (FIXTURES / 'csharp' / 'project.assets.json').read_text().replace('HyperCast/1.0.0', 'HyperCast/1.1.0')
        self.restore(assets=text)
        self.assertIn('HyperCast 1.1.0', self.rejected(installed=True).found)
        project = self.root / 'csharp' / 'HyperTabular' / 'HyperTabular.csproj'
        wired = project.read_text()
        project.write_text(wired.replace('[1.0.0]', '1.1.0'))
        self.assertIn('1.1.0', self.rejected().found)
        # An Update or a VersionOverride added after the wiring moves the requirement too.
        for item in ('<PackageReference Update="HyperCast" Version="1.1.0" />',
                     '<PackageReference Include="HyperCast" VersionOverride="1.1.0" />'):
            with self.subTest(item=item):
                project.write_text(wired.replace('</Project>', f'  <ItemGroup>{item}</ItemGroup>\n</Project>'))
                self.assertIn('requiring HyperCast 1.1.0', self.rejected().found)

    def test_origin_csharp_cli(self):
        (self.root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in CSHARP_ENV.items()))
        self.restore()
        argv = [sys.executable, str(SCRIPTS / 'companion_origin.py'), 'csharp', '--root', str(self.root), '--installed',
                '--dir', 'csharp']
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('C# (csharp/HyperTabular/): HyperCast 1.0.0 from', result.stdout)
        self.restore('registry')
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::C# (csharp/HyperTabular/): found HyperCast 1.0.0 at", result.stderr)

    def test_origin_csharp_only_reads_the_named_directory(self):
        self.restore('registry', nupkg=b'nuget.org bytes')
        other = self.root / 'csharp' / 'Other' / 'obj'
        other.mkdir(parents=True)
        self.assertIn('no project.assets.json', self.rejected(installed=True, only=Path('csharp/Other')).found)


class SwiftRecorded:
    """`swift package show-dependencies` and `swift build -v` answered from fixtures/origin/swift/,
    per directory, keeping the commands."""
    def __init__(self, root, graph='pinned.json', build='pinned-build.log', code=0):
        self.root, self.calls = Path(root), []
        self.graph, self.build, self.code = graph, build, code

    def text(self, name):
        return (FIXTURES / 'swift' / name).read_text().replace('@ROOT@', str(self.root))

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        argv = list(map(str, argv))
        self.calls.append((argv, cwd))
        if argv[1:3] == ['package', 'show-dependencies']:
            return subprocess.CompletedProcess(argv, self.code, self.text(self.graph) if self.graph else '', '')
        return subprocess.CompletedProcess(argv, self.code, self.text(self.build) if self.build else '', '')


class OriginSwiftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = swift_checkout(Path(self.tmp.name) / 'consumer')
        companion_wire.wire_swift(self.root, SWIFT_ENV)
        self.pin = self.root / '.companions' / 'hypercast'
        self.sums = companion_origin.read_sums(self.root, SWIFT_ENV)

    def origin(self, runner, **kwargs):
        companion_origin.origin_swift(self.root, SWIFT_ENV, self.sums, run=runner, **kwargs)

    def rejected(self, runner, **kwargs):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(runner, **kwargs)
        error = caught.exception
        self.assertEqual(error.expected, f'hypercast from {self.pin.as_posix()} (the pin, with the bundle and '
                                         'XCFramework from swift-release-assets)')
        return error

    def test_origin_swift_pinned_passes(self):
        runner = SwiftRecorded(self.root)
        self.origin(runner)
        # Every SwiftPM root naming the companion: the root manifest and swift/'s.
        self.assertEqual(runner.calls, [(['swift', 'package', 'show-dependencies', '--format', 'json'], self.root / d)
                                        for d in ('.', 'swift')])

    def test_origin_swift_registry_copy_is_rejected(self):
        # SwiftPM resolved HyperCast from its URL (the requirement rewritten to a version, no path):
        # the remote's 1.0.0 checkout, whose committed bundle is not the pin's.
        error = self.rejected(SwiftRecorded(self.root, graph='remote.json'))
        self.assertEqual(error.binding, 'Swift (./)')
        self.assertEqual(error.found, f'hypercast 1.0.0 from https://github.com/SkunkWerkx/HyperCast (remote source '
                                      f'control, checked out at {self.root}/swift/.build/checkouts/HyperCast)')
        # The manifest itself still naming the URL is caught before any tool runs.
        manifest = self.root / 'swift' / 'Package.swift'
        manifest.write_text(manifest.read_text().replace('.package(path: "../.companions/hypercast")', HYPERCAST_URL))
        runner = SwiftRecorded(self.root)
        error = self.rejected(runner)
        self.assertEqual(error.binding, 'Swift (swift/Package.swift)')
        self.assertIn('https://github.com/SkunkWerkx/HyperCast', error.found)
        self.assertEqual(runner.calls, [])

    def test_origin_swift_tampered_hash_is_rejected(self):
        archive = self.pin / 'swift' / 'HyperCastCore.artifactbundle' / 'arm64-apple-macosx' / 'libhypercast.a'
        archive.write_bytes(b'another build')
        error = self.rejected(SwiftRecorded(self.root))
        self.assertIn('swift/HyperCastCore.artifactbundle/arm64-apple-macosx/libhypercast.a', error.found)
        # The zip itself changed after staging.
        archive.write_bytes(BUNDLE['HyperCastCore.artifactbundle/arm64-apple-macosx/libhypercast.a'])
        self.sums['swift-release-assets/HyperCastCore.artifactbundle.zip'] = '0' * 64
        error = self.rejected(SwiftRecorded(self.root))
        self.assertIn('swift-release-assets/HyperCastCore.artifactbundle.zip', error.found)

    def test_origin_swift_extra_file_in_the_tree_is_rejected(self):
        (self.pin / 'swift' / 'HyperCastCoreApple.xcframework' / 'ios-arm64' / 'stray.a').write_bytes(b'x')
        error = self.rejected(SwiftRecorded(self.root))
        self.assertIn('stray.a', error.found)

    def test_origin_swift_installed_proves_the_linked_archive(self):
        runner = SwiftRecorded(self.root)
        self.origin(runner, installed=True, only=Path('swift'))
        self.assertEqual(runner.calls, [(['swift', 'package', 'show-dependencies', '--format', 'json'], self.root / 'swift'),
                                        (['swift', 'build', '-v', '--build-tests'], self.root / 'swift')])

    def test_origin_swift_installed_remote_link_is_rejected(self):
        # The graph says the pin, but the link line names the remote checkout's archive.
        error = self.rejected(SwiftRecorded(self.root, build='remote-build.log'), installed=True, only=Path('swift'))
        self.assertEqual(error.binding, 'Swift (swift/)')
        self.assertIn(f'{self.root}/swift/.build/checkouts/HyperCast/swift/HyperCastCore.artifactbundle/'
                      'arm64-apple-macosx/libhypercast.a', error.found)
        self.assertIn('linked', error.found)

    def test_origin_swift_installed_nothing_linked_is_rejected(self):
        error = self.rejected(SwiftRecorded(self.root, build=''), installed=True, only=Path('swift'))
        self.assertIn('linked no libhypercast.a', error.found)

    def test_origin_swift_installed_native_build_system_reads_the_copy(self):
        # The pre-6.4 build system copies the bundle's archive into .build and links it by -l name:
        # the copy found through the link line's -L directories must hold the pin's bytes.
        copy = self.root / 'swift' / '.build' / 'arm64-apple-macosx' / 'debug' / 'libhypercast.a'
        copy.parent.mkdir(parents=True)
        copy.write_bytes(BUNDLE['HyperCastCore.artifactbundle/arm64-apple-macosx/libhypercast.a'])
        runner = SwiftRecorded(self.root, build='native-build.log')
        self.origin(runner, installed=True, only=Path('swift'), build_system='native')
        self.assertEqual(runner.calls[-1][0], ['swift', 'build', '-v', '--build-tests', '--build-system', 'native'])
        copy.write_bytes(b'the remote checkout\'s archive')
        error = self.rejected(SwiftRecorded(self.root, build='native-build.log'), installed=True, only=Path('swift'),
                              build_system='native')
        self.assertIn(copy.as_posix(), error.found)

    def xcodebuild_log(self, name, slice_bytes=None):
        """fixtures/origin/swift/<name> with the products directory under the tmp tree, holding the
        copy Xcode makes of the processed XCFramework slice (the pin's bytes unless given)."""
        products = Path(self.tmp.name) / 'DerivedData' / 'Build' / 'Products'
        (products / 'Debug-iphonesimulator').mkdir(parents=True, exist_ok=True)
        data = XCFRAMEWORK['HyperCastCoreApple.xcframework/ios-arm64/libhypercast.a']
        (products / 'Debug-iphonesimulator' / 'libhypercast.a').write_bytes(data if slice_bytes is None else slice_bytes)
        (products / 'Debug-iphonesimulator' / 'libhypertabular.a').write_bytes(b'the consumer\'s own')
        log = Path(self.tmp.name) / name
        log.write_text((FIXTURES / 'swift' / name).read_text().replace('@ROOT@', str(self.root))
                       .replace('@PRODUCTS@', str(products)))
        return log, products

    def test_origin_swift_xcframework_pinned_passes(self):
        # The Apple mobile legs build through xcodebuild, whose log the leg hands over: the pin's
        # XCFramework processed into the products directory, and that copy linked by -l.
        log, products = self.xcodebuild_log('xcodebuild-pinned.log')
        runner = SwiftRecorded(self.root)
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.origin(runner, installed=True, only=Path('swift'), xcodebuild_log=log)
        self.assertEqual(runner.calls, [(['swift', 'package', 'show-dependencies', '--format', 'json'],
                                         self.root / 'swift')])
        self.assertIn(f'processing {self.pin.as_posix()}/swift/HyperCastCoreApple.xcframework, linking '
                      f'{products}/Debug-iphonesimulator/libhypercast.a', out.getvalue())

    def test_origin_swift_xcframework_from_the_remote_is_rejected(self):
        log, _ = self.xcodebuild_log('xcodebuild-remote.log')
        error = self.rejected(SwiftRecorded(self.root), installed=True, only=Path('swift'), xcodebuild_log=log)
        self.assertEqual(error.found, f'{self.root}/DerivedData/SourcePackages/artifacts/hypercast/HyperCastCoreApple/'
                                      'HyperCastCoreApple.xcframework processed, not the pin\'s')

    def test_origin_swift_xcframework_copy_with_other_bytes_is_rejected(self):
        log, products = self.xcodebuild_log('xcodebuild-pinned.log', slice_bytes=b'the release\'s iOS archive')
        error = self.rejected(SwiftRecorded(self.root), installed=True, only=Path('swift'), xcodebuild_log=log)
        self.assertEqual(error.found, f'{products}/Debug-iphonesimulator/libhypercast.a, the copy of {self.root}/.companions/'
                                      'hypercast/swift/HyperCastCoreApple.xcframework, holding other bytes than the pin\'s '
                                      'slice')

    def test_origin_swift_xcframework_unprocessed_is_rejected(self):
        # A log without the XCFramework (the bundle's build, or an up-to-date one) proves nothing.
        log = Path(self.tmp.name) / 'swift-build.log'
        log.write_text((FIXTURES / 'swift' / 'pinned-build.log').read_text().replace('@ROOT@', str(self.root)))
        error = self.rejected(SwiftRecorded(self.root), installed=True, only=Path('swift'), xcodebuild_log=log)
        self.assertIn('no ProcessXCFramework of HyperCastCoreApple.xcframework', error.found)

    def test_origin_swift_tool_failure_is_named(self):
        error = self.rejected(SwiftRecorded(self.root, graph='', code=1))
        self.assertIn('show-dependencies failed', error.found)


@unittest.skipUnless(shutil.which('bash'), 'no bash')
class OriginCTests(unittest.TestCase):
    """The probe is HyperTabular's own c/smoke/hypercast.sh (fixtures/wire/c/), run by bash: with
    HYPERCAST_PACKAGES set it reads nothing from the network."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = c_checkout(Path(self.tmp.name) / 'consumer')
        companion_wire.wire_c(self.root, C_ENV, environ={})
        self.packages = self.root / '.companions' / 'hypercast-c'
        self.sums = companion_origin.read_sums(self.root, C_ENV)
        self.environ = {**os.environ, 'HYPERCAST_PACKAGES': self.packages.as_posix()}

    def origin(self, **kwargs):
        companion_origin.origin_c(self.root, C_ENV, self.sums, **{'environ': self.environ, **kwargs})

    def rejected(self, **kwargs):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(**kwargs)
        error = caught.exception
        self.assertEqual(error.expected, f'hypercast 1.0.0 from {self.packages.as_posix()} (the pin\'s c-packages-ci, '
                                         'unpacked), as c/smoke/hypercast.sh reports it')
        return error

    def test_origin_c_pinned_passes(self):
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.origin()
        self.assertIn(f'C (c/smoke/hypercast.sh, osx-arm64): hypercast 1.0.0 from '
                      f'{(self.packages / "hypercast-1.0.0-osx-arm64").as_posix()}', out.getvalue())

    def test_origin_c_registry_copy_is_rejected(self):
        # Unset, hypercast.sh downloads HyperCast's GitHub Release archive: the proof says so
        # before anything runs.
        error = self.rejected(installed=True, environ={k: v for k, v in os.environ.items() if k != 'HYPERCAST_PACKAGES'})
        self.assertIn('HYPERCAST_PACKAGES unset', error.found)
        self.assertIn('GitHub Release', error.found)
        # Set to another set holding a same-named archive (a local build).
        other = Path(self.tmp.name) / 'other' / 'hypercast-1.0.0-osx-arm64'
        other.mkdir(parents=True)
        error = self.rejected(installed=True, environ={**os.environ, 'HYPERCAST_PACKAGES': str(other.parent)})
        self.assertIn(str(other.parent), error.found)
        # The requirement not rewritten: hypercast.sh asks for the next release, which the pin is not.
        manifest = self.root / 'rust' / 'Cargo.toml'
        manifest.write_text(manifest.read_text().replace('version = "=1.0.0"', 'version = "1.1.0"'))
        error = self.rejected()
        self.assertIn('a request for hypercast 1.1.0 (rust/Cargo.toml)', error.found)

    def test_origin_c_tampered_hash_is_rejected(self):
        library = self.packages / 'hypercast-1.0.0-osx-arm64' / 'lib' / 'libhypercast.a'
        library.write_bytes(b'another build')
        error = self.rejected()
        self.assertIn('hypercast-1.0.0-osx-arm64/lib/libhypercast.a', error.found)
        library.write_bytes(C_TREE['lib/libhypercast.a'])
        self.sums['c-packages-ci/hypercast-1.0.0-win-x64.zip'] = '0' * 64
        error = self.rejected()
        self.assertIn('c-packages-ci/hypercast-1.0.0-win-x64.zip', error.found)

    def test_origin_c_without_the_consumers_script_is_named(self):
        (self.root / 'c' / 'smoke' / 'hypercast.sh').unlink()
        error = self.rejected()
        self.assertIn('c/smoke/hypercast.sh', error.found)


class OriginCWindowsTests(unittest.TestCase):
    """What Git's bash prints on a Windows runner, against what Python holds there."""
    PACKAGES = r'D:\a\HyperTabular\HyperTabular\.companions\hypercast-c\hypercast-1.0.0-win-x64'

    def test_origin_c_reads_git_bash_paths_on_windows(self):
        same = companion_origin._same_dir
        # `pwd` in Git's bash, and cygpath -m's form of the same directory.
        for reported in ('/d/a/HyperTabular/HyperTabular/.companions/hypercast-c/hypercast-1.0.0-win-x64',
                         'D:/a/HyperTabular/HyperTabular/.companions/hypercast-c/hypercast-1.0.0-win-x64',
                         'd:/a/hypertabular/HyperTabular/.companions/hypercast-c/hypercast-1.0.0-win-x64/'):
            with self.subTest(reported=reported):
                self.assertTrue(same(reported, self.PACKAGES, nt=True))
        for reported in ('/e/a/HyperTabular/HyperTabular/.companions/hypercast-c/hypercast-1.0.0-win-x64',
                         '/d/a/HyperTabular/HyperTabular/other/hypercast-1.0.0-win-x64',
                         'C:/Users/runneradmin/hypercast/hypercast-1.0.0-win-x64'):
            with self.subTest(reported=reported):
                self.assertFalse(same(reported, self.PACKAGES, nt=True))
        # Elsewhere a path is a path: /d/ is no drive.
        self.assertFalse(same('/d/a/x', 'D:/a/x', nt=False))

    def test_origin_c_finds_gits_bash_on_windows(self):
        find = companion_origin._find_bash
        git = r'C:\Program Files\Git\bin\bash.exe'
        wsl = r'C:\Windows\System32\bash.exe'
        environ = {'ProgramFiles': r'C:\Program Files', 'SystemRoot': r'C:\Windows', 'PATH': ''}
        # Git's own, whatever the PATH says (a pwsh step's PATH may reach WSL's first).
        self.assertEqual(find(environ, nt=True, isfile=lambda p: p == git, which=lambda *_, **__: wsl), git)
        # No Git bash: WSL's is refused by name, Git's named.
        with self.assertRaises(companion_origin.OriginError) as caught:
            find(environ, nt=True, isfile=lambda p: False, which=lambda *_, **__: wsl)
        self.assertIn(wsl, caught.exception.found)
        self.assertIn('Git', caught.exception.found)
        # Another bash on PATH, outside %SystemRoot%, is taken.
        msys = r'C:\msys64\usr\bin\bash.exe'
        self.assertEqual(find(environ, nt=True, isfile=lambda p: False, which=lambda *_, **__: msys), msys)
        # Elsewhere, PATH's.
        self.assertEqual(find({'PATH': '/x'}, nt=False, isfile=lambda p: False, which=lambda *_, **__: '/x/bash'), '/x/bash')


# The archive's CMake package as HyperCast ships it (fixtures/origin/c/, from HyperCast 14df722's
# c/cmake/), and a library for every platform's name, so the host's branch finds its own.
CMAKE_TREE = {k: v for k, v in C_TREE.items() if not k.startswith('lib/cmake/')}
CMAKE_TREE.update({
    'lib/cmake/hypercast/hypercastConfig.cmake': (FIXTURES / 'c' / 'hypercastConfig.cmake').read_bytes(),
    'lib/cmake/hypercast/hypercastConfigVersion.cmake': (FIXTURES / 'c' / 'hypercastConfigVersion.cmake.in').read_text()
    .replace('@VERSION@', '1.0.0').replace('@MAJOR@', '1').replace('@POINTER_SIZE@', '8').encode(),
    'lib/libhypercast.dylib': b'pinned dylib', 'lib/libhypercast.so': b'pinned so', 'lib/hypercast.lib': b'pinned lib',
    'lib/hypercast.dll.lib': b'pinned implib', 'bin/hypercast.dll': b'pinned dll'})


@unittest.skipUnless(shutil.which('bash') and shutil.which('cmake'), 'no bash or cmake')
class OriginCCMakeTests(unittest.TestCase):
    """--installed asks CMake, with the directory c/smoke/hypercast.sh reports on its prefix path
    (as run.sh passes it), which package find_package chooses and what it imports."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = c_checkout(Path(self.tmp.name) / 'consumer', {
            'hypercast-1.0.0-osx-arm64.tar.gz': c_archive('hypercast-1.0.0-osx-arm64', CMAKE_TREE),
            'hypercast-1.0.0-linux-x64.tar.gz': c_archive('hypercast-1.0.0-linux-x64', CMAKE_TREE)})
        companion_wire.wire_c(self.root, C_ENV, environ={})
        self.packages = self.root / '.companions' / 'hypercast-c'
        self.sums = companion_origin.read_sums(self.root, C_ENV)
        self.environ = {k: v for k, v in os.environ.items() if not k.lower().startswith('hypercast')}
        self.environ['HYPERCAST_PACKAGES'] = self.packages.as_posix()

    def origin(self, **kwargs):
        companion_origin.origin_c(self.root, C_ENV, self.sums, **{'environ': self.environ, 'installed': True, **kwargs})

    def test_origin_c_cmake_pinned_passes(self):
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.origin(rids=['osx-arm64'])
        archive = (self.packages / 'hypercast-1.0.0-osx-arm64').resolve().as_posix()
        self.assertIn(f'CMake (osx-arm64): find_package(hypercast 1.0.0) chose {archive}/lib/cmake/hypercast, '
                      f'importing {archive}/lib/', out.getvalue())

    def test_origin_c_cmake_another_package_is_rejected(self):
        # hypercast_ROOT is searched before CMAKE_PREFIX_PATH: the smoke would take that package.
        other = Path(self.tmp.name) / 'installed'
        for relative, data in CMAKE_TREE.items():
            (other / relative).parent.mkdir(parents=True, exist_ok=True)
            (other / relative).write_bytes(data.replace(b'pinned', b'system') if relative.startswith(('lib/lib', 'bin'))
                                           else data)
        self.environ['hypercast_ROOT'] = str(other)
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(rids=['osx-arm64'])
        self.assertEqual(caught.exception.binding, 'C (CMake, osx-arm64)')
        self.assertIn(f'find_package(hypercast 1.0.0) choosing {other.resolve().as_posix()}/lib/cmake/hypercast',
                      caught.exception.found)

    def test_origin_c_cmake_needs_the_rid(self):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin()
        self.assertIn('no --rid', caught.exception.found)
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(rids=['win-arm64'])
        self.assertIn('no pinned win-arm64 archive', caught.exception.found)

    def test_origin_c_probe_prints_cygpaths(self):
        # Where cygpath is (Git's bash), the probe prints the reported directory through it.
        tools = Path(self.tmp.name) / 'tools'
        tools.mkdir()
        (tools / 'cygpath').write_text('#!/bin/sh\necho "$*" >> "$CYGPATH_CALLS"\necho M:/converted\n')
        (tools / 'cygpath').chmod(0o755)
        calls = Path(self.tmp.name) / 'calls'
        self.environ.update(PATH=f'{tools}{os.pathsep}{self.environ["PATH"]}', CYGPATH_CALLS=str(calls))
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(installed=False)
        self.assertEqual(caught.exception.found, 'hypercast 1.0.0 from M:/converted')
        self.assertRegex(calls.read_text(), r'^-m \S+/hypercast-1\.0\.0-linux-x64\n')


class RRecorded:
    """Rscript answering the probe with one recorded output (fixtures/origin/r/), keeping the
    commands; @SCRATCH@ is the directory it was recorded in."""
    def __init__(self, name, returncode=0, scratch='/scratch', edit=None):
        self.calls = []
        text = (FIXTURES / 'r' / name).read_text().replace('@SCRATCH@', scratch) if name else ''
        self.answer = (edit(text) if edit else text), returncode

    def __call__(self, argv, cwd=None, capture_output=False, text=False, **_):
        self.calls.append((list(map(str, argv)), cwd))
        stdout, code = self.answer
        return subprocess.CompletedProcess(argv, code, stdout, '')


def vendor_tarball(path, name, files):
    """A vendor.tar.xz as vendor.sh writes it, holding `files` under vendor/<name>/."""
    import tarfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w:xz') as archive:
        for relative, data in files.items():
            info = tarfile.TarInfo(f'vendor/{name}/{relative}')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


class OriginRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        quiet(self)
        self.root = r_checkout(Path(self.tmp.name) / 'consumer')
        self.library = Path(self.tmp.name) / 'library'
        companion_wire.wire_r(self.root, R_ENV, run=FakeR(), environ={'R_LIBS_USER': str(self.library)})
        self.pin = self.root / '.companions' / 'hypercast'
        # The pinned crate as HyperCast's rust/ holds it, and as `cargo package` vendors it.
        (self.pin / 'rust' / 'src').mkdir(parents=True)
        (self.pin / 'rust' / 'src' / 'lib.rs').write_bytes(b'// the pin\n')
        self.packaged = {'Cargo.toml': b'# normalized by cargo package\n',
                         'Cargo.toml.orig': (self.pin / 'rust' / 'Cargo.toml').read_bytes(),
                         # Re-resolved as packaged: what HyperTabular's vendor.sh leaves (its patch unused).
                         'Cargo.lock': (self.pin / 'rust' / 'Cargo.lock').read_bytes()
                         + b'\n[[patch.unused]]\nname = "hypertabular"\nversion = "1.0.0"\n',
                         'src/lib.rs': b'// the pin\n', '.cargo_vcs_info.json': b'{}',
                         '.cargo-checksum.json': b'{"files":{},"package":null}'}
        self.tarball = self.root / 'r' / 'src' / 'rust' / 'vendor.tar.xz'

    def origin(self, runner, **kwargs):
        companion_origin.origin_r(self.root, R_ENV, {}, run=runner, **kwargs)

    def rejected(self, runner, **kwargs):
        with self.assertRaises(companion_origin.OriginError) as caught:
            self.origin(runner, **kwargs)
        error = caught.exception
        self.assertEqual(error.expected, f'hypercast 1.0.0 built from {(self.pin / "r").as_posix()} (the pin, '
                                         f'Hyper-Pin: {R_COMMIT}), from no repository')
        self.assertIn(error.found, str(error))
        return error

    def test_origin_r_pinned_passes(self):
        runner = RRecorded('pinned.stdout')
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.origin(runner)
        # The probe loads the package as the suite does, its name passed as an argument.
        self.assertEqual(runner.calls, [(['Rscript', str(SCRIPTS / 'companion_r.R'), 'hypercast'], None)])
        self.assertIn(f'R (Rscript): hypercast 1.0.0 from /scratch/rlib/hypercast, Hyper-Pin {R_COMMIT}',
                      out.getvalue())

    def test_origin_r_registry_copy_is_rejected(self):
        # A copy from a repository (the probe recorded CRAN's jsonlite): its Repository field.
        error = self.rejected(RRecorded('cran.stdout'))
        self.assertIn('from CRAN (/opt/homebrew/lib/R/4.6/site-library/jsonlite)', error.found)
        # The release's GitHub tarball, as pak installs a url:: reference: no Hyper-Pin.
        error = self.rejected(RRecorded('url.stdout'))
        self.assertIn('hypercast 1.0.0 with no Hyper-Pin field (/scratch/rellib/hypercast, RemoteType url)',
                      error.found)
        # Nothing installed: the namespace does not load.
        error = self.rejected(RRecorded('missing.stdout', returncode=3))
        self.assertIn('hypercast does not load: there is no package called', error.found)
        self.assertIn('/scratch/emptylib', error.found)

    def test_origin_r_another_pin_is_rejected(self):
        error = self.rejected(RRecorded('pinned.stdout', edit=lambda t: t.replace(R_COMMIT, 'a' * 40)))
        self.assertIn(f'hypercast 1.0.0 built from another pin ({"a" * 40}) at /scratch/rlib/hypercast', error.found)
        error = self.rejected(RRecorded('pinned.stdout', edit=lambda t: t.replace('Version\t1.0.0', 'Version\t0.9.0')))
        self.assertIn('hypercast 0.9.0', error.found)

    def test_origin_r_unwired_tree_is_rejected(self):
        # The pin unstamped (the wiring never ran), or the consumer's floor left above the pin.
        description = self.pin / 'r' / 'DESCRIPTION'
        stamped = description.read_text()
        description.write_text(stamped.replace(f'Hyper-Pin: {R_COMMIT}\n', ''))
        error = self.rejected(RRecorded('pinned.stdout'))
        self.assertIn('.companions/hypercast/r/DESCRIPTION with no Hyper-Pin', error.found)
        description.write_text(stamped)
        consumer = self.root / 'r' / 'DESCRIPTION'
        consumer.write_text(consumer.read_text().replace('hypercast (>= 1.0.0)', 'hypercast (>= 1.1.0)'))
        error = self.rejected(RRecorded('pinned.stdout'))
        self.assertEqual(error.binding, 'R (r/DESCRIPTION)')
        self.assertIn('a requirement hypercast (>= 1.1.0)', error.found)

    def test_origin_r_probe_failure_is_named(self):
        error = self.rejected(RRecorded('', returncode=127))
        self.assertIn('the probe failed (exit 127)', error.found)

    def test_origin_r_installed_proves_the_vendored_crate(self):
        # The consumer's own vendor tarball (HyperTabular's R package compiles its core, and
        # HyperCast's crate with it, offline from it) holds the pin's crate, as package_into_vendor
        # writes it from <NAME>_PATH.
        vendor_tarball(self.tarball, 'hypercast', self.packaged)
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.origin(RRecorded('pinned.stdout'), installed=True)
        self.assertIn('R (r/src/rust/vendor.tar.xz): hypercast from', out.getvalue())

    def test_origin_r_installed_rejects_the_registry_crate(self):
        # Without <NAME>_PATH, cargo vendor takes crates.io's crate, whose checksum file names the
        # package's hash.
        vendor_tarball(self.tarball, 'hypercast', {**self.packaged,
                                                   '.cargo-checksum.json': b'{"files":{},"package":"' + b'f' * 64 + b'"}'})
        error = self.rejected(RRecorded('pinned.stdout'), installed=True)
        self.assertEqual(error.binding, 'R (r/src/rust/vendor.tar.xz)')
        self.assertIn('a registry\'s hypercast at vendor/hypercast (its .cargo-checksum.json names the package ' + 'f' * 64, error.found)
        # Another crate of the same version: a file the pin's differs from, or does not have.
        for files, needle in (({**self.packaged, 'src/lib.rs': b'// crates.io 1.0.0\n'}, 'src/lib.rs differs'),
                              ({**self.packaged, 'src/extra.rs': b'x'}, 'src/extra.rs is not the pin\'s'),
                              ({**self.packaged, 'Cargo.toml.orig': b'[package]\n'}, 'Cargo.toml.orig differs')):
            with self.subTest(needle=needle):
                vendor_tarball(self.tarball, 'hypercast', files)
                error = self.rejected(RRecorded('pinned.stdout'), installed=True)
                self.assertIn(needle, error.found)

    def test_origin_r_installed_without_a_vendored_crate_is_rejected(self):
        # rust/Cargo.toml requires hypercast, so the R package's Rust half needs it: a proof that
        # finds no vendored copy has proven nothing, and fails. No tarball at all, a tarball of
        # other crates only, and a renamed tarball the walk does not take are all that case.
        needle = 'no vendor.tar.xz holds hypercast (rust/Cargo.toml requires it): the R package\'s Rust half is unproven'
        for setup in (lambda: None,
                      lambda: vendor_tarball(self.tarball, 'other', self.packaged),
                      lambda: vendor_tarball(self.tarball.with_name('crates.tar.xz'), 'hypercast', self.packaged)):
            with self.subTest(setup=setup):
                self.tarball.unlink(missing_ok=True)
                setup()
                error = self.rejected(RRecorded('pinned.stdout'), installed=True)
                self.assertEqual(error.binding, 'R (vendor.tar.xz)')
                self.assertIn(needle, error.found)
        # A consumer whose Rust needs no companion crate has nothing for the walk to find.
        for manifest in self.root.rglob('Cargo.toml'):
            if '.companions' not in manifest.parts:
                manifest.write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
        self.tarball.unlink(missing_ok=True)
        self.origin(RRecorded('pinned.stdout'), installed=True)

    def test_origin_r_installed_proves_a_versioned_vendor_directory(self):
        # cargo vendor's vendor/<name>-<version>/, which it writes when two versions coexist, is
        # the companion's crate too: proven, and a registry copy there is rejected.
        vendor_tarball(self.tarball, 'hypercast-1.0.0', self.packaged)
        with mock.patch('sys.stdout', io.StringIO()) as out:
            self.origin(RRecorded('pinned.stdout'), installed=True)
        self.assertIn('R (r/src/rust/vendor.tar.xz): hypercast from', out.getvalue())
        vendor_tarball(self.tarball, 'hypercast-0.9.0', {**self.packaged,
                                                         '.cargo-checksum.json': b'{"files":{},"package":"' + b'e' * 64 + b'"}'})
        error = self.rejected(RRecorded('pinned.stdout'), installed=True)
        self.assertIn('vendor/hypercast-0.9.0', error.found)
        # A crate whose name only starts with the companion's is another crate.
        vendor_tarball(self.tarball, 'hypercast-macros', {**self.packaged, '.cargo-checksum.json': b'{"package":"x"}'})
        error = self.rejected(RRecorded('pinned.stdout'), installed=True)
        self.assertIn('no vendor.tar.xz holds hypercast', error.found)


class OriginCliTests(unittest.TestCase):
    def test_cli_fails_with_the_origin_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = checkout(tmp, 'go')
            (root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in ENV.items()))
            # Not wired: go.mod still requires v1.1.0, which the proxy doesn't have (or, offline,
            # can't be reached): either way no copy is the pin's.
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'go', '--root', str(root)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('::error::Go (go/): found ', result.stderr)
            self.assertIn(f'expected {MODULE} replaced by {root / ".companions" / "hypercast" / "go"} (the pin)',
                          result.stderr)

    @unittest.skipUnless(shutil.which('go'), 'no go on PATH')
    def test_cli_passes_once_wired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = checkout(tmp, 'go')
            (root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in ENV.items()))
            for script in ('companion_wire.py', 'companion_origin.py'):
                result = subprocess.run([sys.executable, str(SCRIPTS / script), 'go', '--root', str(root)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Go (go/): ', result.stdout)

    def test_cli_php_names_the_missing_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = php_checkout(Path(tmp) / 'consumer')
            (root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in PHP_ENV.items()))
            environ = {k: v for k, v in os.environ.items() if k != 'HYPERCAST_PHP_EXTENSION'}
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_wire.py'), 'php', '--root', str(root)],
                                    capture_output=True, text=True, env=environ)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'php', '--root', str(root),
                                     '--installed'], capture_output=True, text=True, env=environ)
            self.assertEqual(result.returncode, 1)
            self.assertIn('::error::PHP (php): found HYPERCAST_PHP_EXTENSION unset', result.stderr)

    def test_cli_ruby_wasm_names_the_registry_copy(self):
        # The CLI the suite runs between `bundle install` and `rbwasm build`, with a bundle stub
        # answering RubyGems' path.
        with tempfile.TemporaryDirectory() as tmp:
            root = ruby_checkout(Path(tmp) / 'consumer', WASM_ARTIFACTS)
            (root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in RUBY_ENV.items()))
            with mock.patch('sys.stdout'):
                companion_wire.wire_ruby(root, RUBY_ENV)
            bin_dir = Path(tmp) / 'bin'
            bin_dir.mkdir()
            (bin_dir / 'bundle').write_text('#!/bin/sh\necho "$FAKE_PATH"\n')
            (bin_dir / 'bundle').chmod(0o755)
            for path, code, needle in (('/gems/hypercast-wasm-1.0.0', 1, 'found hypercast-wasm from /gems/hypercast-wasm-1.0.0'),
                                       (str(fake_wasm_gem(root, {'4.0': b'wasm 4.0 archive'})), 0, 'Ruby wasm (ruby/wasm-smoke/)')):
                result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'ruby-wasm', '--root',
                                         str(root), '--dir', 'ruby/wasm-smoke'], capture_output=True, text=True,
                                        env={**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'FAKE_PATH': path})
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(needle, result.stderr + result.stdout)
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'ruby-wasm', '--installed'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)

    @unittest.skipUnless(shutil.which('bash'), 'no bash')
    def test_cli_c_names_the_missing_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = c_checkout(Path(tmp) / 'consumer')
            (root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in C_ENV.items()))
            environ = {k: v for k, v in os.environ.items() if k not in ('HYPERCAST_PACKAGES', 'GITHUB_ENV')}
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_wire.py'), 'c', '--root', str(root)],
                                    capture_output=True, text=True, env=environ)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'c', '--root', str(root)],
                                    capture_output=True, text=True, env=environ)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'c', '--root', str(root),
                                     '--installed', '--rid', 'osx-arm64'], capture_output=True, text=True, env=environ)
            self.assertEqual(result.returncode, 1)
            self.assertIn('::error::C (HYPERCAST_PACKAGES): found HYPERCAST_PACKAGES unset', result.stderr)
            packages = (root / '.companions' / 'hypercast-c').as_posix()
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'c', '--root', str(root),
                                     '--installed'], capture_output=True, text=True,
                                    env={**environ, 'HYPERCAST_PACKAGES': packages})
            self.assertEqual(result.returncode, 2)
            self.assertIn('c --installed needs --rid', result.stderr)
            # --rid is the archive the smoke builds against; CMake (when there) is asked about it.
            with mock.patch.object(companion_origin, 'origin_c') as origin:
                companion_origin.main(['c', '--root', str(root), '--installed', '--rid', 'osx-arm64', '--rid', 'ios-arm64'])
            self.assertEqual(origin.call_args.kwargs, {'installed': True, 'rids': ['osx-arm64', 'ios-arm64']})

    def test_cli_swift_passes_dir_and_build_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = swift_checkout(Path(tmp) / 'consumer')
            (root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in SWIFT_ENV.items()))
            with mock.patch('sys.stdout'):
                companion_wire.wire_swift(root, SWIFT_ENV)
            with mock.patch.object(companion_origin, 'origin_swift') as origin:
                companion_origin.main(['swift', '--root', str(root), '--dir', 'swift', '--installed',
                                       '--build-system', 'native'])
            self.assertEqual(origin.call_args.kwargs, {'installed': True, 'only': Path('swift'), 'build_system': 'native'})
            with mock.patch.object(companion_origin, 'origin_swift') as origin:
                companion_origin.main(['swift', '--root', str(root), '--dir', 'swift', '--installed',
                                       '--xcodebuild-log', 'build.log'])
            self.assertEqual(origin.call_args.kwargs, {'installed': True, 'only': Path('swift'),
                                                       'xcodebuild_log': Path('build.log')})
        result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'swift', '--xcodebuild-log', 'x'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--xcodebuild-log is for swift, with --installed', result.stderr)

    def test_cli_installed_and_dir_are_for_the_installing_ecosystems(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'go', '--installed'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--installed is for npm, ruby, php, java, csharp, swift, c and r', result.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            root = r_checkout(Path(tmp) / 'consumer')
            (root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in R_ENV.items()))
            with mock.patch.object(companion_origin, 'origin_r') as origin:
                self.assertEqual(companion_origin.main(['r', '--root', str(root), '--installed']), 0)
            self.assertEqual(origin.call_args.kwargs, {'installed': True})
        result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'php', '--dir', 'php'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--dir is for npm, ruby, ruby-wasm, java, csharp and swift', result.stderr)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'c', '--build-system', 'native'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--build-system is for swift, with --installed', result.stderr)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'ruby', '--installs', 'x'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--installs and --expect are for npm', result.stderr)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_origin.py'), 'ruby', '--php', 'php8.5'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--php is for php', result.stderr)


if __name__ == '__main__':
    unittest.main()
