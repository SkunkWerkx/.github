"""Shape contracts for hyper-core-checks.yml: the core's static checks on three hosts, one job.

Every check hyper-check-core.yml ran as a job of its own runs here as a step, on the host its
old job named (`matrix.arch`: x64, arm64 or win), soonest-failing first; the binding lints
run in hyper-test-<binding>.yml ahead of its suite and the cdylib legs in hyper-core.yml.
"""
from pathlib import Path
import re
import unittest

import yaml_lite

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'
BINDING_LINTS = {'cdylib', 'lint_go', 'lint_python', 'lint_node', 'lint_java_jni', 'lint_r', 'lint_php',
                 'lint_ruby', 'lint_swift', 'format_csharp', 'format_java', 'format_swift'}
CORE_LINTS = {'lint_rust', 'clippy_targets', 'actionlint'}  # the PR leg's, in hyper-core.yml
BINDING_WORDS = ('Go', 'Java', 'JNI', 'TypeScript', 'Node', 'Bun', 'R CMD', 'Python', 'C#', 'Swift', 'Ruby', 'PHP')
ORDER = ["declares exactly the core's C ABI", 'corpus', 'semver',
         'rust-version', 'no_std', 'no panic path', 'the canary panics', 'big-endian', 'wasm32-wasip1 under wasmtime']


class CoreChecksWorkflowTests(unittest.TestCase):
    PATH = WORKFLOWS / 'hyper-core-checks.yml'

    def setUp(self):
        self.text = self.PATH.read_text()
        self.workflow = yaml_lite.load(self.text)
        self.job = self.workflow['jobs']['checks']
        self.steps = self.job['steps']

    def step(self, needle):
        return next(s for s in self.steps if needle in s.get('name', ''))

    def test_semver_reads_crates_io_without_colour(self):
        # dtolnay/rust-toolchain sets CARGO_TERM_COLOR=always; coloured `cargo search` output
        # never matches the sed that reads the published version, which turned HyperCast's real
        # semver break into "cannot read the published version". Both workflows that read it
        # pass --color never.
        for path in (self.PATH, WORKFLOWS / 'hyper-check-core.yml'):
            with self.subTest(workflow=path.name):
                searches = re.findall(r'\$\(cargo search[^|]*\|', path.read_text())
                self.assertEqual(len(searches), 1)
                self.assertIn('--color never', searches[0])

    def test_big_endian_control_reads_the_consumers_vector(self):
        # HyperUuid's sql_order vector was hard-coded here, so every other crate's big-endian
        # leg failed with "can't read ../corpus/sql_order.json" (HyperCast's second board).
        control = self.step('Negative control, a corrupted corpus vector must fail conformance')
        run = control['run']
        self.assertIn('hyper_config.py corrupt-corpus --root ..', run)
        self.assertIn('--test "$CONTROL_TEST"', run)
        self.assertIn('grep -qxF "test $CONTROL_FAILS ... FAILED" "$out"', run)
        self.assertNotIn('sql_order', self.text)
        names = [s.get('name') for s in self.steps]
        checkout = self.step('Check out the executing Forge revision')
        self.assertEqual(checkout['with']['path'], '.hyperforge')
        self.assertEqual(checkout['with']['ref'], '${{ job.workflow_sha }}')
        # Every host checks the Forge out, unconditionally: fetch-companions needs it on each.
        self.assertNotIn('if', checkout)
        self.assertLess(names.index('Check out the executing Forge revision'), names.index('Fetch the companions'))
        self.assertLess(names.index('Check out the executing Forge revision'), names.index(control['name']))

    def test_three_hosts_fail_fast_off(self):
        self.assertEqual(list(self.workflow['jobs']), ['checks'])
        self.assertIs(self.job['strategy']['fail-fast'], False)
        rows = self.job['strategy']['matrix']['include']
        self.assertEqual([(r['os'], r['arch']) for r in rows],
                         [('ubuntu-26.04', 'x64'), ('ubuntu-26.04-arm', 'arm64'), ('windows-2025-vs2026', 'win')])
        self.assertEqual(self.job['runs-on'], '${{ matrix.os }}')
        for line in self.text.splitlines():
            if 'runs-on:' in line or line.strip().startswith('- { os:'):
                self.assertNotIn('latest', line)

    def test_host_specific_checks_keep_their_hosts(self):
        for needle in ('no panic path', 'the canary panics'):
            no_panic = self.step(needle)
            self.assertIn('inputs.no_panic_runners', no_panic['if'], needle)
            self.assertIn('matrix.os', no_panic['if'], needle)
        default = self.workflow['on']['workflow_call']['inputs']['no_panic_runners']['default']
        self.assertEqual(default, '["ubuntu-26.04", "windows-2025-vs2026"]')
        for needle in ("The repository's own core checks", 'cargo test with the extra feature set'):
            self.assertIn("matrix.arch != 'win'", self.step(needle)['if'])
        for needle in ORDER:
            if needle in ('no panic path', 'the canary panics') or needle in ("The repository's own core checks",):
                continue
            step = self.step(needle)
            self.assertIn("matrix.arch == 'x64'", step['if'], needle)

    def test_order_fails_soonest(self):
        names = [s.get('name', '') for s in self.steps]
        positions = [next(i for i, n in enumerate(names) if needle in n) for needle in ORDER]
        self.assertEqual(positions, sorted(positions), list(zip(ORDER, positions)))

    def test_no_binding_lints(self):
        for name in BINDING_LINTS:
            self.assertIsNone(re.search(rf'inputs\.{name}\b', self.text), name)
        for step in self.steps:
            for word in BINDING_WORDS:
                self.assertNotIn(word, step.get('name', ''), step.get('name'))

    def test_inputs_are_check_cores_minus_every_lint(self):
        old = yaml_lite.load((WORKFLOWS / 'hyper-check-core.yml').read_text())['on']['workflow_call']['inputs']
        new = self.workflow['on']['workflow_call']['inputs']
        self.assertEqual(set(new), (set(old) - BINDING_LINTS - CORE_LINTS) | {'no_panic_canary'})
        for name in set(old) - BINDING_LINTS - CORE_LINTS:
            with self.subTest(input=name):
                self.assertEqual(new[name]['type'], old[name]['type'])
                if name != 'no_panic_runners':
                    self.assertEqual(new[name]['default'], old[name]['default'])

    def test_no_panic_canary_is_opt_in(self):
        # Off unless a crate names its canary: HyperUuid runs as before, HyperCast proves its
        # no-panic harness is linked and checked by making the canary build fail, by name.
        canary = self.workflow['on']['workflow_call']['inputs']['no_panic_canary']
        self.assertEqual(canary['type'], 'string')
        self.assertEqual(canary['default'], '')
        self.assertIn('RUSTFLAGS', canary['description'])
        step = self.step('the canary panics')
        for needle in ('inputs.no_panic', "inputs.no_panic_canary != ''", 'fromJSON(inputs.no_panic_runners)'):
            self.assertIn(needle, step['if'])
        self.assertEqual(step['working-directory'], 'rust')
        self.assertEqual(step['env']['CANARY'], '${{ inputs.no_panic_canary }}')
        self.assertEqual(step['env']['NO_PANIC_COMMAND'], '${{ inputs.no_panic_command }}')
        self.assertIn('../.hyperforge/.github/scripts/no_panic_canary.py --name "$CANARY" -- bash -c "$NO_PANIC_COMMAND"',
                      step['run'])


if __name__ == '__main__':
    unittest.main()
