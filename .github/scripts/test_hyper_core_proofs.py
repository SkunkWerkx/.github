"""Shape contracts for hyper-core-proofs.yml: the thesis proofs, on every pull request's core.

It copies hyper-core-checks.yml's proof steps rather than sharing them, so these tests are what
keeps the two from drifting: a proof changed in one and not the other is red here.
"""
from pathlib import Path
import unittest

import yaml_lite

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'
PROOFS = ['cargo check (no_std, bare metal)',
          'A default-features = false consumer builds on the host and for wasm32-unknown-unknown',
          'assert the core links no allocator',
          "The repository's own core checks",
          'the public API and the C exports link with no panic path',
          'the canary panics, and is the only function the no-panic proof names']


def steps(name, job):
    return yaml_lite.load((WORKFLOWS / name).read_text())['jobs'][job]['steps']


class CoreProofsWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.proofs = {s.get('name'): s for s in steps('hyper-core-proofs.yml', 'proofs')}
        self.checks = {s.get('name'): s for s in steps('hyper-core-checks.yml', 'checks')}

    def test_every_proof_runs_what_core_checks_runs(self):
        for name in PROOFS:
            with self.subTest(step=name):
                for key in ('run', 'env', 'working-directory'):
                    self.assertEqual(self.proofs[name].get(key), self.checks[name].get(key))

    def test_proofs_keep_their_input_gates_and_lose_the_host_ones(self):
        for name in PROOFS:
            with self.subTest(step=name):
                gate = self.proofs[name]['if']
                self.assertIn('inputs.', gate)
                self.assertNotIn('matrix.', gate)

    def test_the_inputs_match_core_checks(self):
        inputs = yaml_lite.load((WORKFLOWS / 'hyper-core-proofs.yml').read_text())['on']['workflow_call']['inputs']
        others = yaml_lite.load((WORKFLOWS / 'hyper-core-checks.yml').read_text())['on']['workflow_call']['inputs']
        for name in ('no_std', 'no_std_rustflags', 'no_panic', 'no_panic_command', 'no_panic_canary'):
            with self.subTest(input=name):
                self.assertEqual(inputs[name], others[name])

    def test_the_companion_is_fetched_before_any_proof(self):
        names = [s.get('name') for s in steps('hyper-core-proofs.yml', 'proofs')]
        self.assertLess(names.index('Fetch the companions'), min(names.index(name) for name in PROOFS))
        self.assertEqual(self.proofs['Fetch the companions']['with']['leg'], 'hyper-core-proofs.yml:proofs')


if __name__ == '__main__':
    unittest.main()
