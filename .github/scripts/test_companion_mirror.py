"""companion_mirror.py: what hyper-companions.yml downloads from a pinned run, and the
SHA256SUMS and pin.json it seals the mirror with."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import companion_map
import companion_mirror
import hyper_config

COMMIT = 'c' * 40
PIN = {'repo': 'SkunkWerkx/HyperCast', 'commit': COMMIT, 'run': 41346771778}
RERUN = f're-run `SkunkWerkx/HyperCast` CI at `{COMMIT}` with `purpose=board` and move the pin'


def consumer(root, companions=None):
    data = {'crate': 'hypertabular', 'project': 'HyperTabular',
            'exports': {'sources': ['rust/src/ffi.rs'], 'patterns': ['x'], 'prefixes': ['x']},
            'companions': {'hypercast': PIN} if companions is None else companions}
    path = Path(root) / '.github' / 'hyper.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding='utf-8')
    return hyper_config.HyperConfig(data, root)


class PlanTests(unittest.TestCase):
    def test_plan_names_the_one_companion_and_every_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = consumer(root)
            plan = companion_mirror.plan(root)
            names = sorted(companion_map.all_artifacts(cfg))
        self.assertEqual({k: plan[k] for k in ('name', 'repo', 'commit', 'run', 'repositories')},
                         {'name': 'hypercast', 'repo': 'SkunkWerkx/HyperCast', 'commit': COMMIT,
                          'run': 41346771778, 'repositories': 'HyperCast'})
        self.assertEqual(plan['pattern'], '{' + ','.join(names) + '}')
        self.assertGreater(len(names), 1)

    def test_a_single_name_is_no_brace_set(self):
        # minimatch takes `{a}` literally: one name is the pattern itself.
        self.assertEqual(companion_mirror.pattern(['a']), 'a')
        self.assertEqual(companion_mirror.pattern(['b', 'a']), '{a,b}')

    def test_no_block_or_two_companions_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root, companions={})
            with self.assertRaisesRegex(ValueError, "no companions block: a repository without companions doesn't call hyper-companions.yml"):
                companion_mirror.plan(root)
        with tempfile.TemporaryDirectory() as root:
            consumer(root, companions={'hypercast': PIN, 'hyperuuid': {**PIN, 'repo': 'SkunkWerkx/HyperUuid'}})
            with self.assertRaisesRegex(ValueError, 'mirrors one companion'):
                companion_mirror.plan(root)

    def test_plan_writes_step_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            output = Path(root) / 'output'
            with mock.patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}), mock.patch('sys.stdout', io.StringIO()):
                companion_mirror.main(['plan', '--root', root])
            lines = dict(line.split('=', 1) for line in output.read_text().splitlines())
        self.assertEqual(lines['name'], 'hypercast')
        self.assertEqual(lines['run'], '41346771778')
        self.assertEqual(lines['repositories'], 'HyperCast')
        self.assertTrue(lines['pattern'].startswith('{'))


class SealTests(unittest.TestCase):
    def mirror(self, root, names):
        directory = Path(root) / 'mirror'
        for name in names:
            (directory / name).mkdir(parents=True)
            (directory / name / f'{name}.bin').write_bytes(name.encode())
        return directory

    def test_seal_writes_sums_and_pin(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = consumer(root)
            names = sorted(companion_map.all_artifacts(cfg))
            directory = self.mirror(root, names)
            companion_mirror.seal(root, directory, 'success')
            sums = (directory / 'SHA256SUMS').read_text().splitlines()
            pin = json.loads((directory / 'pin.json').read_text())
        self.assertEqual(pin, PIN)
        self.assertEqual(len(sums), len(names))
        first = names[0]
        self.assertIn(f'{hashlib.sha256(first.encode()).hexdigest()}  {first}/{first}.bin', sums)
        self.assertEqual(sums, sorted(sums, key=lambda line: line.split('  ', 1)[1]))

    def test_a_failed_download_names_the_rerun(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = consumer(root)
            directory = self.mirror(root, sorted(companion_map.all_artifacts(cfg)))
            with self.assertRaises(ValueError) as caught:
                companion_mirror.seal(root, directory, 'failure')
            self.assertIn(RERUN, str(caught.exception))
            self.assertFalse((directory / 'SHA256SUMS').exists())

    def test_an_absent_artifact_names_it_and_the_rerun(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = consumer(root)
            names = sorted(companion_map.all_artifacts(cfg))
            directory = self.mirror(root, names[1:])
            with self.assertRaises(ValueError) as caught:
                companion_mirror.seal(root, directory, 'success')
            self.assertIn(names[0], str(caught.exception))
            self.assertIn(RERUN, str(caught.exception))
            self.assertIn('run 41346771778', str(caught.exception))

    def test_an_empty_artifact_is_absent(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = consumer(root)
            names = sorted(companion_map.all_artifacts(cfg))
            directory = self.mirror(root, names)
            (directory / names[0] / f'{names[0]}.bin').unlink()
            with self.assertRaisesRegex(ValueError, names[0]):
                companion_mirror.seal(root, directory, 'success')


if __name__ == '__main__':
    unittest.main()
