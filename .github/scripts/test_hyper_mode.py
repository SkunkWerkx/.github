"""A run's companion mode, from the event that triggered it."""
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
EVENTS = SCRIPTS / 'fixtures' / 'events'
spec = importlib.util.spec_from_file_location('hyper_mode', SCRIPTS / 'hyper_mode.py')
hyper_mode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hyper_mode)


def mode(name, event_name):
    return hyper_mode.companion_mode(event_name, json.loads((EVENTS / f'{name}.json').read_text()))


class CompanionModeTests(unittest.TestCase):
    def test_board_dispatch_is_companion(self):
        self.assertEqual(mode('dispatch_board', 'workflow_dispatch'), 'companion')

    def test_release_dispatch_is_release(self):
        self.assertEqual(mode('dispatch_release', 'workflow_dispatch'), 'release')

    def test_unknown_purpose_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'purpose'):
            mode('dispatch_bogus', 'workflow_dispatch')

    def test_missing_purpose_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'purpose'):
            mode('dispatch_none', 'workflow_dispatch')

    def test_other_events_are_companion(self):
        for name in ('pull_request', 'schedule', 'push'):
            with self.subTest(name):
                self.assertEqual(mode(name, name), 'companion')

    def test_expression_agrees_with_companion_mode(self):
        # Evaluate MODE_RELEASE's two clauses, parsed out of the constant, against each payload.
        match = re.fullmatch(r"github\.event_name == '(\w+)' && github\.event\.inputs\.purpose == '(\w+)'",
                             hyper_mode.MODE_RELEASE)
        self.assertIsNotNone(match, hyper_mode.MODE_RELEASE)
        want_event, want_purpose = match.groups()
        for name, event_name in (('dispatch_board', 'workflow_dispatch'), ('dispatch_release', 'workflow_dispatch'),
                                 ('pull_request', 'pull_request'), ('schedule', 'schedule'), ('push', 'push')):
            event = json.loads((EVENTS / f'{name}.json').read_text())
            purpose = (event.get('inputs') or {}).get('purpose')
            expr = event_name == want_event and purpose == want_purpose
            with self.subTest(name):
                self.assertEqual(expr, hyper_mode.companion_mode(event_name, event) == 'release')

    def test_cli_refuses_a_bogus_purpose(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            env = {**os.environ, 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_OUTPUT': str(out),
                   'GITHUB_EVENT_PATH': str(EVENTS / 'dispatch_bogus.json')}
            run = subprocess.run([sys.executable, str(SCRIPTS / 'hyper_mode.py')], env=env,
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertRegex(run.stderr, r'::error::.*purpose')
            self.assertFalse(out.exists() and out.read_text())

    def test_cli_writes_the_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            env = {**os.environ, 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_OUTPUT': str(out),
                   'GITHUB_EVENT_PATH': str(EVENTS / 'dispatch_release.json')}
            subprocess.run([sys.executable, str(SCRIPTS / 'hyper_mode.py')], env=env, check=True)
            self.assertEqual(out.read_text(), 'companion_mode=release\n')


if __name__ == '__main__':
    unittest.main()
