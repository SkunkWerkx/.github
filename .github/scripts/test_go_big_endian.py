"""go_big_endian.sh's verdict, with `go` and `docker` faked: a gate that ran nothing must not pass."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('go_big_endian.sh')

FAKE_GO = r'''#!/bin/sh
case "$1" in
  list)
    case "$2" in
      -f) echo "$PWD" ;;
      *)
        printf '%s' "$FAKE_LIST"
        exit "${FAKE_LIST_STATUS:-0}"
        ;;
    esac
    ;;
  test)
    # go test -c -o <binary> <package>: a package with no test files leaves no binary.
    [ -n "${FAKE_NO_TESTS:-}" ] || : >"$4"
    exit "${FAKE_BUILD_STATUS:-0}"
    ;;
esac
'''

FAKE_DOCKER = r'''#!/bin/sh
echo run >>"$FAKE_DOCKER_LOG"
echo '--- PASS: TestSomething (0.00s)'
exit "${FAKE_RUN_STATUS:-0}"
'''


class Verdict(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        fake = self.root / 'fake'
        fake.mkdir()
        for name, body in (('go', FAKE_GO), ('docker', FAKE_DOCKER)):
            path = fake / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        (self.root / 'repo' / 'go').mkdir(parents=True)
        (self.root / 'repo' / 'go' / 'go.mod').write_text('module x\n\ngo 1.26\n')
        self.log = self.root / 'docker.log'
        self.env = {**os.environ, 'PATH': f'{fake}{os.pathsep}{os.environ["PATH"]}', 'FAKE_DOCKER_LOG': str(self.log)}
        for name in ('ARCH', 'IMAGE', 'PACKAGES'):
            self.env.pop(name, None)

    def run_gate(self, **env):
        return subprocess.run(['bash', str(SCRIPT), str(self.root / 'repo' / 'go')], env={**self.env, **env},
                              capture_output=True, text=True, check=False)

    def containers(self):
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def test_a_suite_that_passes_passes(self):
        done = self.run_gate(FAKE_LIST='x/a\nx/b\n')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.containers(), 2)

    def test_a_suite_that_fails_fails(self):
        done = self.run_gate(FAKE_LIST='x/a\n', FAKE_RUN_STATUS='1')
        self.assertNotEqual(done.returncode, 0)

    def test_a_failed_build_fails(self):
        done = self.run_gate(FAKE_LIST='x/a\n', FAKE_BUILD_STATUS='1')
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.containers(), 0)

    def test_a_failed_package_list_fails(self):
        for listed in ('', 'x/a\n'):  # Go can print part of the list and still fail.
            with self.subTest(listed=listed):
                done = self.run_gate(FAKE_LIST=listed, FAKE_LIST_STATUS='1')
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(self.containers(), 0)

    def test_an_empty_package_list_fails(self):
        done = self.run_gate(FAKE_LIST='')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('no packages', done.stderr)
        self.assertEqual(self.containers(), 0)

    def test_a_blank_packages_value_fails(self):
        done = self.run_gate(PACKAGES='   ')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('no packages', done.stderr)

    def test_no_test_files_anywhere_fails(self):
        done = self.run_gate(FAKE_LIST='x/a\nx/b\n', FAKE_NO_TESTS='1')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('no suite ran', done.stderr)
        self.assertEqual(self.containers(), 0)

    def test_a_named_package_with_no_test_files_fails(self):
        done = self.run_gate(PACKAGES='x/a', FAKE_NO_TESTS='1')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('no suite ran', done.stderr)


if __name__ == '__main__':
    unittest.main()
