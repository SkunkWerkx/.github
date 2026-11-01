"""Shape contracts for hyper-core-checks.yml: the core's static checks on three hosts, one job.

Every check hyper-check-core.yml ran as a job of its own runs here as a step, on the host its
old job named (`matrix.arch`: x64, arm64 or win), soonest-failing first; the binding lints
live in hyper-lint-<binding>.yml and the cdylib legs in hyper-core.yml.
"""
from pathlib import Path
import re
import unittest

import yaml_lite

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'
BINDING_LINTS = {'cdylib', 'lint_go', 'lint_python', 'lint_node', 'lint_java_jni', 'lint_r', 'lint_php',
                 'lint_ruby', 'lint_swift', 'format_csharp', 'format_java', 'format_swift'}
BINDING_WORDS = ('Go', 'Java', 'JNI', 'TypeScript', 'Node', 'Bun', 'R CMD', 'Python', 'C#', 'Swift', 'Ruby', 'PHP')
ORDER = ['cargo fmt --check', 'cargo clippy', "declares exactly the core's C ABI", 'corpus', 'semver',
         'rust-version', 'no_std', 'no panic path', 'big-endian', 'wasm32-wasip1 under wasmtime',
         'Validate workflow syntax', 'clang-format and shellcheck']


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
        no_panic = self.step('no panic path')
        self.assertIn('inputs.no_panic_runners', no_panic['if'])
        self.assertIn('matrix.os', no_panic['if'])
        default = self.workflow['on']['workflow_call']['inputs']['no_panic_runners']['default']
        self.assertEqual(default, '["ubuntu-26.04", "windows-2025-vs2026"]')
        for needle in ("The repository's own core checks", 'cargo test with the extra feature set'):
            self.assertIn("matrix.arch != 'win'", self.step(needle)['if'])
        for needle in ORDER:
            if needle in ('no panic path',) or needle in ("The repository's own core checks",):
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

    def test_inputs_are_check_cores_minus_binding_lints_plus_c_lint(self):
        old = yaml_lite.load((WORKFLOWS / 'hyper-check-core.yml').read_text())['on']['workflow_call']['inputs']
        new = self.workflow['on']['workflow_call']['inputs']
        self.assertEqual(set(new), (set(old) - BINDING_LINTS) | {'c_lint'})
        self.assertEqual(new['c_lint']['type'], 'boolean')
        self.assertIs(new['c_lint']['default'], True)
        for name in set(old) - BINDING_LINTS:
            with self.subTest(input=name):
                self.assertEqual(new[name]['type'], old[name]['type'])
                if name != 'no_panic_runners':
                    self.assertEqual(new[name]['default'], old[name]['default'])
        self.assertIn('inputs.c_lint', self.step('clang-format and shellcheck')['if'])


if __name__ == '__main__':
    unittest.main()
