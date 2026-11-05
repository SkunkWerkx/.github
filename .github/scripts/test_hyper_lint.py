"""Shape contracts for the lint a binding's job runs ahead of its suite, and hyper-lint-rust.yml.

A binding is one job on a pull request: its toolchain once, then its linters, then its suite. The
linters run only when the caller says so (`lint`, from the classifier's lint_<binding>, and only on a
pull request: what reached master has been linted), on the one leg that lints. Go's tiers take the
place of that switch, because its regeneration proof runs on any core change and off a pull request.
"""
from pathlib import Path
import unittest

import yaml_lite

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'
LINUX = "matrix.rid == 'linux-x64'"
# binding: (workflow, job, the leg's condition, the linters, the first step of the suite they precede)
LINTS = {
    'csharp': ('hyper-test-csharp.yml', 'test', LINUX, ['dotnet format whitespace --verify-no-changes'],
               "Test C# bindings against this platform's native lib"),
    'java': ('hyper-test-java.yml', 'test', LINUX, ['spotlessCheck', 'cargo fmt --check (java/jni)', 'cargo clippy (java/jni)'],
             'Test Java (JNI) on the primary JDK and the floor'),
    'python': ('hyper-test-python.yml', 'test', LINUX, ['Install ruff', 'ruff check', 'ruff format --check'], 'Test Python bindings'),
    'ruby': ('hyper-test-ruby.yml', 'test', LINUX, ['rake docs:check', 'rubocop'], 'Ruby (Magnus) — compat ABI'),
    'php': ('hyper-build-php-extension.yml', 'build', "(inputs.mode == 'pr' || matrix.stubs)", ['phpcs'],
            'Test the extension (composer test)'),
    'swift': ('hyper-test-swift.yml', 'test', "matrix.rid == 'osx-arm64'", ['Install SwiftLint', 'Lint', 'swift format lint'],
              'Test Swift bindings, the core linked in'),
    'typescript': ('hyper-test-typescript.yml', 'test', LINUX, ['Install the dev dependencies', 'bun run lint', 'cargo fmt --check', 'cargo clippy'],
                   'Test TypeScript bindings on Node'),
    'r': ('hyper-test-r.yml', 'test', LINUX, ['Install the package for lintr', 'lintr', 'cargo fmt --check', 'clang-format --dry-run'],
          'R CMD check the R package'),
}


def steps(workflow, job):
    return yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][job]['steps']


def index(job_steps, name):
    return next(i for i, step in enumerate(job_steps) if step.get('name') == name)


class FoldedLintTests(unittest.TestCase):
    def test_no_binding_keeps_a_lint_workflow(self):
        self.assertEqual([p.name for p in WORKFLOWS.glob('hyper-lint-*.yml')], ['hyper-lint-rust.yml'])

    def test_each_binding_lints_on_one_leg_before_its_suite(self):
        for binding, (workflow, job, leg, linters, suite) in LINTS.items():
            with self.subTest(binding=binding):
                job_steps = steps(workflow, job)
                for name in linters:
                    step = job_steps[index(job_steps, name)]
                    self.assertTrue(step['if'].startswith('inputs.lint && '), step['if'])
                    self.assertIn(leg, step['if'])
                    self.assertLess(index(job_steps, name), index(job_steps, suite))

    def test_lint_is_off_unless_the_caller_asks(self):
        for binding, (workflow, *_) in LINTS.items():
            with self.subTest(binding=binding):
                lint = yaml_lite.load((WORKFLOWS / workflow).read_text())['on']['workflow_call']['inputs']['lint']
                self.assertEqual((lint['type'], lint['default']), ('boolean', False))

    def test_only_the_lint_runs_only_on_request_in_java(self):
        # The JNI crate's tests, its link, its exports and its no-panic proof are not lints: a core
        # change reaches them without touching java/, and so does a release.
        job_steps = steps('hyper-test-java.yml', 'test')
        for name in ('cargo test (java/jni)', 'The release build links libc and nothing else',
                     "JNI_OnLoad plus the core's exports, and nothing else",
                     'No panic path survives in a release build of every native'):
            step = job_steps[index(job_steps, name)]
            self.assertEqual(step['if'], "inputs.java_jni && matrix.rid == 'linux-x64'", name)

    def test_r_lints_a_copy_so_the_check_builds_a_clean_tree(self):
        job_steps = steps('hyper-test-r.yml', 'test')
        run = job_steps[index(job_steps, 'Install the package for lintr')]['run']
        self.assertIn('cp -R r "$RUNNER_TEMP/rlint/"', run)
        self.assertIn('R CMD INSTALL --library="$RUNNER_TEMP/rlib" "$RUNNER_TEMP/rlint/r"', run)
        self.assertLess(index(job_steps, "Vendor the R package's core, crates and corpus"),
                        index(job_steps, 'Install the package for lintr'))

    def test_rust_lint_is_fmt_and_clippy_on_linux_arm64(self):
        text = (WORKFLOWS / 'hyper-lint-rust.yml').read_text()
        workflow = yaml_lite.load(text)
        self.assertEqual(list(workflow['jobs']), ['lint'])
        self.assertEqual(workflow['jobs']['lint']['runs-on'], 'ubuntu-26.04-arm')
        self.assertNotIn('needs', workflow['jobs']['lint'])
        self.assertIn('cargo fmt --check', text)
        self.assertIn('cargo clippy --all-targets -- -D warnings', text)


class GoTierTests(unittest.TestCase):
    def setUp(self):
        self.steps = steps('hyper-test-go.yml', 'test')

    def step(self, name):
        return self.steps[index(self.steps, name)]

    def test_the_regeneration_proof_runs_on_every_tier_but_none(self):
        # A core change that doesn't touch go/ skips Go's linters, not the proof that the committed
        # core is this commit's: the suite runs that committed core and would pass on a stale one.
        current = self.step('The committed core is current')
        self.assertIn("inputs.linters != 'none'", current['if'])
        self.assertIn('_CHECK_CORE=1', current['run'])
        self.assertIn('TestRegenerationCarriesTheCrateVersion', current['run'])

    def test_the_linters_are_the_fast_tier_alone(self):
        for name in ('gofmt', 'go vet', 'Install revive', 'revive'):
            self.assertEqual(self.step(name)['if'], f"{LINUX} && inputs.linters == 'fast'", name)

    def test_the_sweep_and_the_race_run_off_a_pull_request(self):
        for name in ('Cross-compile sweep', 'Race'):
            self.assertEqual(self.step(name)['if'], f"{LINUX} && inputs.linters == 'sweep'", name)

    def test_the_tiers_come_before_the_suite_and_the_leg_that_lints_wires_rust(self):
        suite = index(self.steps, 'Test Go bindings, the translated core')
        for name in ('gofmt', 'The committed core is current', 'Race'):
            self.assertLess(index(self.steps, name), suite)
        fetch = self.step('Fetch the companions')
        self.assertIn("'rust go'", fetch['with']['ecosystems'])


class SwiftLintTests(unittest.TestCase):
    def test_swift_lints_on_macos_with_the_portable_build(self):
        text = (WORKFLOWS / 'hyper-test-swift.yml').read_text()
        self.assertIn('portable_swiftlint.zip', text)
        self.assertIn('../swiftlint_bin/swiftlint lint --strict Sources', text)
        self.assertNotIn('swiftlint-static', text)


if __name__ == '__main__':
    unittest.main()
