"""no_panic_canary.py: the no-panic proof's negative control, judged against recorded build
output (fixtures/no_panic_canary/) and against real child processes for what main runs."""
from pathlib import Path
import sys
import unittest

import no_panic_canary

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'no_panic_canary'
NAME = 'hypercast_canary'


def fixture(name):
    return (FIXTURES / f'{name}.log').read_text()


def child(code):
    """A command running `code` in a fresh interpreter, as main receives it after `--`."""
    return [sys.executable, '-c', code]


class JudgeTests(unittest.TestCase):
    def test_a_build_that_passes_fails_the_check(self):
        ok, message = no_panic_canary.judge(0, '', NAME)
        self.assertFalse(ok)
        self.assertIn('passed the proof', message)

    def test_the_canary_alone_passes(self):
        ok, message = no_panic_canary.judge(101, fixture('canary_only'), NAME)
        self.assertTrue(ok, message)

    def test_another_function_named_fails_naming_it(self):
        ok, message = no_panic_canary.judge(101, fixture('canary_and_other'), NAME)
        self.assertFalse(ok)
        self.assertIn('cast_text', message)
        self.assertNotIn(NAME, message)

    def test_a_failure_without_the_canary_is_another_reason(self):
        log = fixture('unrelated_error')
        ok, message = no_panic_canary.judge(101, log, NAME)
        self.assertFalse(ok)
        self.assertIn('for some other reason', message)
        self.assertIn(log.strip().splitlines()[-1], message)

    def test_a_canary_whose_name_only_starts_the_same_is_not_the_canary(self):
        log = 'ERROR[no-panic]: detected panic in function `hypercast_canary_two`\n'
        ok, _ = no_panic_canary.judge(101, log, NAME)
        self.assertFalse(ok)


class MainTests(unittest.TestCase):
    def test_main_runs_the_command_and_judges_its_result(self):
        self.assertNotEqual(no_panic_canary.main(['--name', 'x', '--', *child('raise SystemExit(0)')]), 0)

    def test_main_passes_the_cfg_and_accepts_the_canary_alone(self):
        code = ("import os, sys\n"
                "assert os.environ['RUSTFLAGS'] == '--cfg x', os.environ.get('RUSTFLAGS')\n"
                "print('ERROR[no-panic]: detected panic in function `x`')\n"
                "sys.exit(101)\n")
        self.assertEqual(no_panic_canary.main(['--name', 'x', '--', *child(code)]), 0)

    def test_main_runs_in_root(self):
        code = ("import os, sys\n"
                f"if os.path.realpath(os.getcwd()) != os.path.realpath({str(FIXTURES)!r}): sys.exit(0)\n"
                "print('ERROR[no-panic]: detected panic in function `x`')\n"
                "sys.exit(101)\n")
        argv = ['--name', 'x', '--root', str(FIXTURES), '--', *child(code)]
        self.assertEqual(no_panic_canary.main(argv), 0)


if __name__ == '__main__':
    unittest.main()
