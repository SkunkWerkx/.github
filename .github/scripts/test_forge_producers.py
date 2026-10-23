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
        self.assertRegex(native, r'      php_extension_schedule:\n(?:        .*\n)*?        default: inline')
        self.assertIn("inputs.php_extension_schedule == 'parallel'", native)
        # Every inline extension step must be disabled when its parallel producer owns it.
        section = native.split('- name: Build PHP (ext-php-rs) extension', 1)[1].split('\n      # RUBY', 1)[0]
        self.assertEqual(section.count("inputs.php_extension_schedule == 'inline'"), 5)

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

    def test_native_defaults_keep_legacy_producers_and_local_link_coverage(self):
        text = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        for gate in ('static_libs_artifact', 'swift_static_tests', 'android_shared_artifact'):
            self.assertRegex(text, rf'      {gate}:\n(?:        .*\n)*?        default: true')
        self.assertIn('if: inputs.static_libs && inputs.static_libs_artifact', text)
        self.assertIn('if: inputs.android && inputs.android_shared_artifact', text)
        self.assertIn("uses: ./.github/workflows/hyper-build-static.yml", text)
        self.assertIn("uses: ./.github/workflows/hyper-build-android.yml", text)
        self.assertIn("uses: ./.github/workflows/hyper-test-swift-static.yml", text)
        # Local suites must still build their own fresh archives when producers are external.
        local = text.split('- name: Build this leg\'s static libraries', 1)[1].split('\n      #', 1)[0]
        self.assertIn('inputs.static_libs', local)
        self.assertNotIn('inputs.static_libs_artifact', local)

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
