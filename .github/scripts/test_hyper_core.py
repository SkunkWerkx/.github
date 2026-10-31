"""Shape contracts for hyper-core.yml: the seven core legs a consumer's PR board forks first.

The legs build the core, run its suite and corpus, check the cdylib's exports, smoke the C
archive, and upload `native-<rid>`; no binding runs here, and nothing attests in `pr` mode.
"""
from pathlib import Path
import re
import unittest

import yaml_lite

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'
LABELS = {'ubuntu-26.04', 'ubuntu-26.04-arm', 'xcode-27', 'windows-2025-vs2026', 'windows-11-vs2026-arm'}
BINDING_WORDS = ('Go', 'Java', 'JNI', 'TypeScript', 'Node', 'Bun', 'R CMD', 'Python', 'C#', 'Swift', 'Ruby', 'PHP')


class CoreWorkflowTests(unittest.TestCase):
    PATH = WORKFLOWS / 'hyper-core.yml'

    def setUp(self):
        self.text = self.PATH.read_text()
        self.workflow = yaml_lite.load(self.text)
        self.jobs = self.workflow['jobs']

    @staticmethod
    def names(job):
        return [step.get('name', step.get('uses', '')) for step in job['steps']]

    @staticmethod
    def matrix(job):
        return job['strategy']['matrix']['include']

    def test_seven_legs_on_explicit_labels(self):
        self.assertEqual(set(self.jobs), {'core', 'core-musl', 'core-osx-x64'})
        self.assertEqual({row['rid'] for row in self.matrix(self.jobs['core'])},
                         {'linux-x64', 'linux-arm64', 'osx-arm64', 'win-x64', 'win-arm64'})
        self.assertEqual({row['rid'] for row in self.matrix(self.jobs['core-musl'])},
                         {'linux-musl-x64', 'linux-musl-arm64'})
        labels = {row['os'] for name in ('core', 'core-musl') for row in self.matrix(self.jobs[name])}
        labels.add(self.jobs['core-osx-x64']['runs-on'])
        self.assertLessEqual(labels, LABELS, labels - LABELS)
        for line in self.text.splitlines():
            if 'runs-on:' in line:
                self.assertNotIn('latest', line)

    def test_core_matrix_carries_the_leg_columns(self):
        for row in self.matrix(self.jobs['core']):
            with self.subTest(rid=row['rid']):
                self.assertLessEqual({'os', 'rid', 'lib_name', 'target', 'manylinux', 'static_targets'}, set(row))
                self.assertNotIn('shard', row)

    def test_uploads_only_native_rid(self):
        names = re.findall(r'upload-artifact@[0-9a-f]{40}[^\n]*\n(?:[^\n]*\n)*?\s+name: ([^\n]+)', self.text)
        self.assertEqual(set(names), {'native-${{ matrix.rid }}', 'native-osx-x64'}, names)
        self.assertIn('uses: ./.hyperforge/.github/actions/musl-native', self.text)

    def test_attestation_is_release_only(self):
        for job in self.jobs.values():
            for step in job['steps']:
                if step.get('name', '').startswith('Attest'):
                    self.assertIn("inputs.mode != 'pr'", step['if'])

    def test_no_binding_steps(self):
        for job_name, job in self.jobs.items():
            for name in self.names(job):
                for word in BINDING_WORDS:
                    self.assertNotIn(word, name, f'{job_name}: {name!r} looks like a binding step')

    def test_every_leg_builds_its_static_archive_then_checks_exports_and_smokes_c(self):
        order = ['Validate Forge workflow revision', 'Check out the executing Forge revision',
                 './.hyperforge/.github/actions/static-libs', 'Check the cdylib exports', 'Smoke-test the C archive']
        for job_name, job in self.jobs.items():
            with self.subTest(job=job_name):
                keys = [step.get('name', '') + ' ' + step.get('uses', '') for step in job['steps']]
                positions = [next(i for i, key in enumerate(keys) if needle in key) for needle in order]
                self.assertEqual(positions, sorted(positions), f'{job_name}: {keys}')
                forge = next(step for step in job['steps'] if step.get('name') == 'Check out the executing Forge revision')
                self.assertEqual(forge['with']['path'], '.hyperforge')
                static = next(step for step in job['steps'] if step.get('uses') == './.hyperforge/.github/actions/static-libs')
                self.assertEqual(static['with'].get('upload', 'false'), 'false')
        osx = next(step for step in self.jobs['core-osx-x64']['steps'] if step.get('uses') == './.hyperforge/.github/actions/static-libs')
        self.assertEqual(osx['with']['targets'], 'x86_64-apple-darwin')

    def test_c_smoke_needs_static_libs(self):
        for job_name, job in self.jobs.items():
            with self.subTest(job=job_name):
                smoke = next(step for step in job['steps'] if step.get('name') == 'Smoke-test the C archive')
                self.assertIn('inputs.c_smoke', smoke['if'])
                self.assertIn('inputs.static_libs', smoke['if'])

    def test_osx_x64_is_release_only(self):
        self.assertIn("inputs.mode != 'pr'", self.jobs['core-osx-x64']['if'])
        self.assertIn('inputs.osx_x64', self.jobs['core-osx-x64']['if'])

    def test_inputs(self):
        inputs = self.workflow['on']['workflow_call']['inputs']
        self.assertEqual(set(inputs), {'mode', 'crate', 'project', 'osx_x64', 'musl', 'static_libs', 'attest_native', 'c_smoke'})
        for name in ('osx_x64', 'musl', 'static_libs', 'attest_native', 'c_smoke'):
            self.assertIs(inputs[name]['default'], True, name)
        self.assertEqual(inputs['mode']['default'], 'release')


if __name__ == '__main__':
    unittest.main()
