"""Shape contracts for the nine hyper-lint-<binding>.yml workflows and hyper-lint-rust.yml.

Each is one job on ubuntu-26.04-arm with no `needs` and no artifact: the binding's doc linter
and formatter, moved from hyper-check-core.yml, so a consumer's board runs it the moment the
`changes` job says that binding moved, ahead of the core legs. Go's fast gate keeps the
regeneration check (a core change without a regenerated translation turns it red); R's
keeps no floor check, that being a release's; Swift's takes the image's toolchain.
"""
from pathlib import Path
import unittest

import yaml_lite

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'
BINDINGS = ['csharp', 'java', 'python', 'ruby', 'php', 'swift', 'typescript', 'r', 'go']
# A lint whose tool has no arm64 build moves to ubuntu-26.04 with the reason at runs-on.
X64_ONLY = {}


def load(binding):
    text = (WORKFLOWS / f'hyper-lint-{binding}.yml').read_text()
    return text, yaml_lite.load(text)


class LintWorkflowTests(unittest.TestCase):
    def test_each_lint_is_one_job_on_linux_arm64_with_no_needs(self):
        for binding in BINDINGS:
            with self.subTest(binding=binding):
                text, workflow = load(binding)
                self.assertEqual(list(workflow['jobs']), ['lint'])
                self.assertEqual(workflow['jobs']['lint']['runs-on'], X64_ONLY.get(binding, 'ubuntu-26.04-arm'))
                self.assertNotIn('needs', workflow['jobs']['lint'])
                self.assertNotIn('download-artifact', text)
                self.assertNotIn('upload-artifact', text)
                inputs = workflow['on']['workflow_call']['inputs']
                self.assertLessEqual({'crate', 'project', 'mode'}, set(inputs))
                for line in text.splitlines():
                    if 'runs-on:' in line:
                        self.assertNotIn('latest', line)

    def test_r_lint_carries_fmt_clang_format_and_no_floor_check(self):
        text, workflow = load('r')
        steps = workflow['jobs']['lint']['steps']
        names = [s.get('name', '') for s in steps]
        self.assertIn('lintr', names)
        self.assertIn('cargo fmt --check --manifest-path r/src/rust/Cargo.toml', text)
        self.assertIn('clang-format --dry-run -Werror r/src/*.c r/src/*.h', text)
        inputs = workflow['on']['workflow_call']['inputs']
        self.assertEqual(inputs['r_version']['default'], 'release')
        self.assertNotIn('r_floor_version', inputs)
        self.assertFalse([name for name in names if name.startswith('R CMD check')])

    def test_rust_lint_is_fmt_and_clippy_on_linux_arm64(self):
        text, workflow = load('rust')
        self.assertEqual(list(workflow['jobs']), ['lint'])
        self.assertEqual(workflow['jobs']['lint']['runs-on'], 'ubuntu-26.04-arm')
        self.assertNotIn('needs', workflow['jobs']['lint'])
        self.assertIn('cargo fmt --check', text)
        self.assertIn('cargo clippy --all-targets -- -D warnings', text)

    def test_go_core_tier_is_the_regeneration_check_alone(self):
        # A core change that doesn't touch go/ skips Go's linters, not the proof that the committed
        # core is this commit's: test-go runs that committed core and would pass on a stale one.
        _, workflow = load('go')
        steps = workflow['jobs']['lint']['steps']
        for name in ('gofmt', 'go vet', 'Install revive', 'revive'):
            step = next(s for s in steps if s.get('name') == name)
            self.assertIn("inputs.linters != 'core'", step['if'], name)
        current = next(s for s in steps if s.get('name') == 'The committed core is current')
        self.assertNotIn('inputs.linters', current['if'])

    def test_go_fast_gate_keeps_the_regeneration_check(self):
        text, workflow = load('go')
        inputs = workflow['on']['workflow_call']['inputs']
        self.assertEqual(inputs['linters']['default'], 'fast')
        steps = workflow['jobs']['lint']['steps']
        for step in steps:
            self.assertNotIn('golangci', (step.get('run') or '') + step.get('uses', ''))

        def find(needle):
            return next(s for s in steps if needle in s.get('name', '') or needle in (s.get('run') or ''))
        for needle in ('gofmt', 'go vet ./...', 'revive -config revive.toml'):
            self.assertNotIn("inputs.linters == 'full'", find(needle).get('if', ''), needle)
        current = find('TestCoreIsCurrent')
        self.assertIn('_CHECK_CORE=1', current['run'])
        self.assertIn('TestRegenerationCarriesTheCrateVersion', current['run'])
        self.assertIn('rust-toolchain.toml', current['run'])
        self.assertNotIn('inputs.linters', current.get('if', ''))
        for needle in ('Cross-compile sweep', 'Race'):
            self.assertIn("inputs.linters == 'full'", find(needle)['if'], needle)

    def test_swift_lint_asserts_the_image_toolchain(self):
        text, workflow = load('swift')
        steps = workflow['jobs']['lint']['steps']
        for step in steps:
            self.assertNotIn('setup-swift', step.get('uses', ''))
        self.assertNotIn('container', workflow['jobs']['lint'])
        assertion = next(s for s in steps if 'swift --version' in (s.get('run') or ''))
        self.assertIn('Swift version', assertion['run'])
        self.assertIn('SWIFT_MINOR', assertion['run'])
        self.assertEqual(workflow['on']['workflow_call']['inputs']['swift_version']['default'], '6.4')
        self.assertIn('swiftlint_linux_arm64.zip', text)
        self.assertIn('swift format lint', text)


if __name__ == '__main__':
    unittest.main()
