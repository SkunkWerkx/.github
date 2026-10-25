"""Regression contracts for isolated Forge producers and immutable action sources."""
from pathlib import Path
import importlib.util
import os
import re
import subprocess
import unittest

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'


class ProducerContracts(unittest.TestCase):
    def test_timing_model_waits_for_both_android_architectures(self):
        path = Path(__file__).with_name('ci_timing.py')
        self.assertTrue(path.exists())
        spec = importlib.util.spec_from_file_location('ci_timing', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def entry(name, start, end):
            return {'name': name, 'startedAt': f'2026-10-21T00:{start}:00Z',
                    'completedAt': f'2026-10-21T00:{end}:00Z', 'conclusion': 'success'}
        run = {'conclusion': 'success', 'startedAt': '2026-10-21T00:00:00Z', 'jobs': [
            entry('build-native / build-static-libs', '00', '02'),
            entry('build-native / build-native-android (android-arm64, aarch64)', '00', '01'),
            entry('build-native / build-native-android (android-x64, x86_64)', '03', '04'),
            entry('test-android', '10', '16'),
            entry('test-apple-mobile', '10', '30')]}
        result = {row['job']: row for row in module.summarize(run)}
        self.assertEqual(result['test-android']['artifact_ready_seconds'], 240)
        self.assertEqual(result['test-android']['observed_wait_seconds'], 360)
        self.assertEqual(result['test-android']['modeled_finish_seconds'], 600)
        self.assertEqual(result['test-apple-mobile']['artifact_ready_seconds'], 120)
        self.assertEqual(result['test-apple-mobile']['modeled_finish_seconds'], 1320)

    def test_parallel_php_keeps_existing_platforms_load_probe_and_artifacts(self):
        path = WORKFLOWS / 'hyper-build-php-extension.yml'
        self.assertTrue(path.exists())
        text = path.read_text()
        self.assertEqual(re.findall(r'            rid: ([\w-]+)', text),
                         ['linux-x64', 'linux-arm64', 'osx-arm64'])
        for value in ('cargo rustc --release --crate-type cdylib --features php',
                      'php -d extension=', 'extension_loaded(',
                      'name: php-native-${{ matrix.rid }}',
                      'actions/attest-build-provenance@'):
            self.assertIn(value, text)
        native = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        # The extension is built only by its own job: the matrix builds none, so it can never
        # be built twice or by two recipes that drift apart, and there is no schedule to pick.
        self.assertNotIn('php_extension_schedule', native)
        self.assertNotIn('- name: Validate PHP extension schedule', native)
        self.assertIn("  build-php-extension:\n    if: inputs.php && inputs.mode != 'pr'\n", native)
        for gone in ('--features php', 'php-native-${{ matrix.rid }}'):
            self.assertNotIn(gone, native)

    def test_wasi_suite_runs_under_a_pinned_checksummed_wasmtime_with_the_repository_mounted(self):
        import yaml_lite
        workflow = yaml_lite.load((WORKFLOWS / 'hyper-check-core.yml').read_text())
        self.assertIs(workflow['on']['workflow_call']['inputs']['wasi_tests']['default'], True)
        job = workflow['jobs']['test-wasi']
        self.assertEqual(job['if'], 'inputs.wasi_tests')
        steps = {step.get('name'): step for step in job['steps']}
        install = next(step for name, step in steps.items() if name and name.startswith('Install wasmtime '))
        version = install['env']['WASMTIME_VERSION']
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        self.assertEqual(install['name'], f'Install wasmtime {version}')
        self.assertRegex(install['env']['WASMTIME_SHA256'], r'^[0-9a-f]{64}$')
        self.assertIn('sha256sum -c -', install['run'])
        self.assertIn('releases/download/v$WASMTIME_VERSION/', install['run'])
        run = steps['cargo test for wasm32-wasip1 under wasmtime']['run']
        self.assertIn('--dir $GITHUB_WORKSPACE --dir $target', run)
        self.assertIn('cargo test --target wasm32-wasip1', run)

    def test_every_composite_job_checks_out_the_executing_forge_revision(self):
        for name in ('hyper-build-native.yml', 'hyper-build-wasm.yml',
                     'hyper-build-wheels.yml', 'hyper-build-static.yml',
                     'hyper-test-swift-static.yml'):
            path = WORKFLOWS / name
            self.assertTrue(path.exists(), name)
            text = path.read_text()
            self.assertNotRegex(text, r'uses: SkunkWerkx/\.github/\.github/actions/')
            jobs = re.split(r'\n  [\w-]+:\n', text.split('\njobs:\n', 1)[1])
            for body in jobs:
                if 'uses: ./.hyperforge/.github/actions/' not in body:
                    continue
                self.assertIn('ref: ${{ job.workflow_sha }}', body)
                self.assertIn('path: .hyperforge', body)
                self.assertLess(body.index('ref: ${{ job.workflow_sha }}'),
                                body.index('uses: ./.hyperforge/.github/actions/'))

    def test_every_third_party_action_is_pinned_to_a_commit(self):
        # A tag can be moved after the fact; a commit cannot. The trailing comment is the
        # version Dependabot reads and rewrites when it bumps the pin.
        uses = re.compile(r'^\s*(?:- )?uses:\s*(\S+)(.*)$', re.M)
        for path in sorted(WORKFLOWS.parent.glob('**/*.yml')):
            for ref, comment in uses.findall(path.read_text()):
                if ref.startswith('./'):
                    continue
                with self.subTest(path=path.name, ref=ref):
                    self.assertRegex(ref, r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}$')
                    self.assertRegex(comment, r'^ # \S+$')

    def test_missing_or_mutable_forge_revision_fails_before_checkout(self):
        text = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        self.assertIn('- name: Validate Forge workflow revision', text)
        tail = text.split('- name: Validate Forge workflow revision\n', 1)[1]
        block = tail.split('        run: |\n', 1)[1]
        lines = []
        for line in block.splitlines():
            if line and not line.startswith('          '):
                break
            lines.append(line[10:])
        script = '\n'.join(lines)
        for sha, wanted in (('a' * 40, 0), ('', 1), ('master', 1), ('a' * 39, 1)):
            result = subprocess.run(['bash', '-c', script],
                                    env={**os.environ, 'FORGE_WORKFLOW_SHA': sha},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, wanted, (sha, result.stderr))

    def test_native_leaves_the_static_android_and_swift_static_producers_to_the_caller(self):
        import yaml_lite
        text = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        workflow = yaml_lite.load(text)
        inputs = workflow['on']['workflow_call']['inputs']
        # Every caller runs these producers as top-level jobs of its own, so that Apple and
        # Android tests start when their artifacts exist; a nested copy here has no user.
        for gone in ('static_libs_artifact', 'swift_static_tests', 'android_shared_artifact',
                     'apple_mobile', 'android', 'swift_floor_version'):
            self.assertNotIn(gone, inputs)
            self.assertNotIn(f'inputs.{gone}', text)
        self.assertEqual(set(workflow['jobs']),
                         {'build-native', 'build-native-osx-x64', 'build-native-musl',
                          'build-php-extension'})
        for producer in ('hyper-build-static.yml', 'hyper-build-android.yml',
                         'hyper-test-swift-static.yml'):
            self.assertNotIn(f'uses: ./.github/workflows/{producer}', text)
        # Local suites still build their own fresh archives.
        local = text.split('- name: Build this leg\'s static libraries', 1)[1].split('\n      #', 1)[0]
        self.assertIn('inputs.static_libs', local)

    def test_extracted_producers_keep_packaging_artifact_contracts(self):
        static = WORKFLOWS / 'hyper-build-static.yml'
        android = WORKFLOWS / 'hyper-build-android.yml'
        self.assertTrue(static.exists())
        self.assertTrue(android.exists())
        self.assertIn('upload: "true"', static.read_text())
        text = android.read_text()
        for value in ('android-arm64', 'android-x64', 'max-page-size=16384',
                      'name: native-${{ matrix.rid }}', 'actions/attest-build-provenance@'):
            self.assertIn(value, text)


if __name__ == '__main__':
    unittest.main()
