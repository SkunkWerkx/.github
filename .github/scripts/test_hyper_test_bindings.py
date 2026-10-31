"""Shape contracts for the seven hyper-test-<binding>.yml workflows (the artifact-consuming
bindings; Go and PHP have their own).

Each runs one binding's suite on the five desktop legs, on the two musl RIDs where
musl-suite supports the binding, and on osx-x64 (release mode) where the binding ships it,
against the core hyper-core.yml uploaded; it never builds a second core. Only the bindings
that load the shared library download it (C#, Java and Ruby place it; Swift links the
static-libs archives; TypeScript, Python and R compile their own extension from rust/); every
musl leg downloads it, since suite-musl.sh requires it beside the suite.
"""
from pathlib import Path
import re
import unittest

import yaml_lite

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'
BINDINGS = ['csharp', 'java', 'typescript', 'ruby', 'python', 'r', 'swift']
LABELS = {'ubuntu-26.04', 'ubuntu-26.04-arm', 'xcode-27', 'windows-2025-vs2026', 'windows-11-vs2026-arm'}
NATIVE = 'native-${{ matrix.rid }}'
DESKTOP_DOWNLOADS = {'csharp': {NATIVE, 'static-libs'}, 'java': {NATIVE}, 'ruby': {NATIVE}, 'swift': {'static-libs'},
                     'typescript': set(), 'python': set(), 'r': set()}
MUSL = {'csharp', 'java', 'typescript', 'ruby', 'python'}
OSX_X64 = {'java', 'typescript'}
UPLOADS = {'csharp': {'aot-report-${{ matrix.rid }}'}, 'java': {'jni-${{ matrix.rid }}', 'jni-osx-x64'},
           'typescript': {'node-${{ matrix.rid }}', 'node-osx-x64'}, 'ruby': set(), 'python': set(), 'r': set(),
           'swift': set()}
RIDS = {'linux-x64', 'linux-arm64', 'osx-arm64', 'win-x64', 'win-arm64'}
FORK_GUARD = 'github.event.pull_request.head.repo.full_name == github.repository'


def load(binding):
    text = (WORKFLOWS / f'hyper-test-{binding}.yml').read_text()
    return text, yaml_lite.load(text)


def downloads(job):
    return {s['with']['name'] for s in job['steps']
            if s.get('uses', '').startswith('actions/download-artifact') and 'name' in s['with']}


class TestBindingWorkflowTests(unittest.TestCase):
    def test_each_downloads_its_hosts_core(self):
        for binding in BINDINGS:
            with self.subTest(binding=binding):
                _, workflow = load(binding)
                self.assertEqual(downloads(workflow['jobs']['test']), DESKTOP_DOWNLOADS[binding])
                if binding in MUSL:
                    self.assertIn(NATIVE, downloads(workflow['jobs']['test-musl']))
                for job in workflow['jobs'].values():
                    self.assertNotIn('cargo cdylib', str(job), f'{binding} builds a second core')

    def test_csharp_legs_download_static_libs(self):
        _, workflow = load('csharp')
        for name in ('test', 'test-musl'):
            self.assertIn('static-libs', downloads(workflow['jobs'][name]), name)

    def test_java_musl_builds_jni_with_core_false(self):
        _, workflow = load('java')
        steps = workflow['jobs']['test-musl']['steps']
        uses = [s.get('uses', '') for s in steps]
        shim = next(s for s in steps if s.get('uses') == './.hyperforge/.github/actions/musl-native')
        self.assertEqual(shim['with']['core'], 'false')
        self.assertEqual(shim['with']['jni'], 'true')
        self.assertLess(uses.index('./.hyperforge/.github/actions/musl-native'),
                        uses.index('./.hyperforge/.github/actions/musl-suite'))
        bindings = [s['with']['binding'] for s in steps if s.get('uses') == './.hyperforge/.github/actions/musl-suite']
        self.assertEqual(bindings, ['java-jni', 'java'])
        self.assertLess(uses.index('actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333'),
                        uses.index('./.hyperforge/.github/actions/musl-native'))

    def test_musl_jobs_only_where_the_suite_supports_them(self):
        for binding in BINDINGS:
            with self.subTest(binding=binding):
                _, workflow = load(binding)
                self.assertEqual('test-musl' in workflow['jobs'], binding in MUSL)
                if binding in MUSL:
                    rows = workflow['jobs']['test-musl']['strategy']['matrix']['include']
                    self.assertEqual({r['rid'] for r in rows}, {'linux-musl-x64', 'linux-musl-arm64'})

    def test_uploads_only_the_bindings_names(self):
        for binding in BINDINGS:
            with self.subTest(binding=binding):
                text, workflow = load(binding)
                names = {s['with']['name'] for job in workflow['jobs'].values() for s in job['steps']
                         if s.get('uses', '').startswith('actions/upload-artifact')}
                self.assertEqual(names, UPLOADS[binding])
                if binding == 'ruby':
                    self.assertIn('./.hyperforge/.github/actions/ruby-magnus\n', text)
                    self.assertIn('./.hyperforge/.github/actions/ruby-magnus-musl', text)

    def test_labels_explicit(self):
        for binding in BINDINGS:
            with self.subTest(binding=binding):
                text, workflow = load(binding)
                for job in workflow['jobs'].values():
                    rows = job.get('strategy', {}).get('matrix', {}).get('include', [])
                    labels = {r['os'] for r in rows} if rows else {job['runs-on']}
                    self.assertLessEqual(labels, LABELS, labels - LABELS)
                    if job is workflow['jobs']['test']:
                        self.assertEqual({r['rid'] for r in rows}, RIDS - ({'win-arm64'} if binding == 'r' else set()))
                for line in text.splitlines():
                    if 'runs-on:' in line:
                        self.assertNotIn('latest', line)

    def test_attest_guards(self):
        for binding in BINDINGS:
            with self.subTest(binding=binding):
                text, workflow = load(binding)
                for job in workflow['jobs'].values():
                    for step in job['steps']:
                        if step.get('uses', '').startswith('actions/attest-build-provenance'):
                            for needle in ("inputs.mode != 'pr'", 'inputs.attest_native', FORK_GUARD):
                                self.assertIn(needle, step['if'], step.get('name'))
                        if 'attest' in step.get('with', {}):
                            for needle in ("inputs.mode != 'pr'", 'inputs.attest_native', FORK_GUARD):
                                self.assertIn(needle, step['with']['attest'], step.get('name'))

    def test_pr_trims(self):
        _, python = load('python')
        ft = next(s for s in python['jobs']['test']['steps'] if s.get('name', '').endswith('free-threaded'))
        self.assertIn("(inputs.mode != 'pr' || matrix.rid == 'linux-x64')", ft['if'])
        _, r = load('r')
        floor = next(s for s in r['jobs']['test']['steps'] if s.get('name', '').endswith('the floor R'))
        self.assertIn("inputs.mode != 'pr'", floor['if'])
        self.assertIn("matrix.rid == 'linux-x64'", floor['if'])
        _, swift = load('swift')
        steps = swift['jobs']['test']['steps']
        for step in steps:
            if 'setup-swift' in step.get('uses', ''):
                self.assertIn("runner.os == 'Windows'", step['if'])
        assertion = next(s for s in steps if 'swift --version' in (s.get('run') or ''))
        self.assertIn("runner.os != 'Windows'", assertion['if'])
        self.assertIn('Swift version', assertion['run'])

    def test_osx_x64_release_only(self):
        for binding in BINDINGS:
            with self.subTest(binding=binding):
                _, workflow = load(binding)
                self.assertEqual('test-osx-x64' in workflow['jobs'], binding in OSX_X64)
                if binding in OSX_X64:
                    job = workflow['jobs']['test-osx-x64']
                    self.assertIn("inputs.mode != 'pr'", job['if'])
                    self.assertEqual(job['runs-on'], 'xcode-27')

    def test_inputs_subset_of_native(self):
        native = set(yaml_lite.load((WORKFLOWS / 'hyper-build-native.yml').read_text())['on']['workflow_call']['inputs'])
        static = set(yaml_lite.load((WORKFLOWS / 'hyper-test-swift-static.yml').read_text())['on']['workflow_call']['inputs'])
        for binding in BINDINGS:
            with self.subTest(binding=binding):
                _, workflow = load(binding)
                inputs = set(workflow['on']['workflow_call']['inputs'])
                self.assertLessEqual({'mode', 'crate', 'project', 'attest_native'}, inputs)
                allowed = native | (static if binding == 'swift' else set())
                self.assertLessEqual(inputs, allowed, inputs - allowed)
                self.assertEqual(workflow['on']['workflow_call']['inputs']['mode']['default'], 'release')


class GoWorkflowTests(unittest.TestCase):
    """hyper-test-go.yml: the translated core needs no artifact, so the suite runs on the seven
    hosts with nothing to wait for but its lint."""

    def test_go_downloads_no_core_and_runs_on_seven_hosts(self):
        text, workflow = load('go')
        self.assertNotIn('download-artifact', text)
        self.assertNotIn('cargo', text)
        test, musl = workflow['jobs']['test'], workflow['jobs']['test-musl']
        self.assertEqual({r['rid'] for r in test['strategy']['matrix']['include']}, RIDS)
        self.assertEqual({r['rid'] for r in musl['strategy']['matrix']['include']}, {'linux-musl-x64', 'linux-musl-arm64'})
        labels = {r['os'] for job in (test, musl) for r in job['strategy']['matrix']['include']}
        self.assertLessEqual(labels, LABELS, labels - LABELS)
        setup = next(s for s in test['steps'] if s.get('uses', '').startswith('actions/setup-go'))
        self.assertIs(setup['with']['cache'], False)
        self.assertEqual(setup['with']['go-version-file'], '${{ inputs.go_version_file }}')
        suite = next(s for s in test['steps'] if s.get('name') == 'Test Go bindings, the translated core')
        self.assertEqual(suite['env']['CGO_ENABLED'], '0')
        self.assertEqual(suite['run'], 'go test ./...')
        alpine = next(s for s in musl['steps'] if s.get('uses') == './.hyperforge/.github/actions/musl-suite')
        self.assertEqual(alpine['with']['binding'], 'go')
        inputs = workflow['on']['workflow_call']['inputs']
        self.assertEqual(set(inputs), {'mode', 'crate', 'go_version_file'})
        self.assertEqual(inputs['go_version_file']['default'], 'go/go.mod')


if __name__ == '__main__':
    unittest.main()
