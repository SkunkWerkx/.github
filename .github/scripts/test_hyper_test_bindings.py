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

import companion_map
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
                    rows = companion_map.matrix_rows(workflow['jobs']['test-musl'])
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
                    rows = companion_map.matrix_rows(job) or []
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
                            for needle in ("github.event.inputs.purpose == 'release'", 'inputs.attest_native'):
                                self.assertIn(needle, step['if'], step.get('name'))
                        if 'attest' in step.get('with', {}):
                            for needle in ("github.event.inputs.purpose == 'release'", 'inputs.attest_native'):
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
                allowed = native | {'lint'} | (static if binding == 'swift' else set())
                self.assertLessEqual(inputs, allowed, inputs - allowed)
                self.assertEqual(workflow['on']['workflow_call']['inputs']['mode']['default'], 'release')


class DevLoopStagingTests(unittest.TestCase):
    """hyper-build-native.yml built the core in place, so a csproj or Gradle build that stages
    from rust/target/release (the empty default of csharp_runtimes_dir and java_resources_dir)
    found it there. These jobs only download it, so each desktop leg puts the download where
    that default looks, unconditionally, before the suite."""

    def test_csharp_and_java_stage_the_core_for_the_dev_loop_default(self):
        for binding, suite in (('csharp', 'dotnet test'), ('java', 'gradlew')):
            with self.subTest(binding=binding):
                _, workflow = load(binding)
                steps = workflow['jobs']['test']['steps']
                keys = [s.get('name') or s.get('uses', '') for s in steps]
                download = next(i for i, s in enumerate(steps) if s.get('uses', '').startswith('actions/download-artifact')
                                and s['with']['name'] == 'native-${{ matrix.rid }}')
                stage = next(i for i, s in enumerate(steps) if s.get('name') == 'Stage the core where a dev-loop build looks')
                first_suite = next(i for i, s in enumerate(steps) if suite in (s.get('run') or ''))
                self.assertLess(download, stage, keys)
                self.assertLess(stage, first_suite, keys)
                self.assertNotIn('if', steps[stage])
                self.assertIn('rust/target/release', steps[stage]['run'])
                self.assertIn('out/${{ matrix.lib_name }}', steps[stage]['run'])


class GoWorkflowTests(unittest.TestCase):
    """hyper-test-go.yml: the translated core needs no artifact, so the suite runs on the eight
    hosts (the five desktop ones, two Alpine, s390x under QEMU) with nothing to wait for."""

    def test_go_downloads_no_core_and_runs_on_the_desktop_and_alpine_hosts(self):
        text, workflow = load('go')
        self.assertNotIn('download-artifact', text)
        self.assertNotIn('cargo build', text)
        test, musl = workflow['jobs']['test'], workflow['jobs']['test-musl']
        self.assertEqual({r['rid'] for r in companion_map.matrix_rows(test)}, RIDS)
        self.assertEqual({r['rid'] for r in companion_map.matrix_rows(musl)}, {'linux-musl-x64', 'linux-musl-arm64'})
        labels = {r['os'] for job in (test, musl) for r in companion_map.matrix_rows(job)}
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
        self.assertEqual(set(inputs), {'mode', 'crate', 'go_version_file', 'linters'})
        self.assertEqual(inputs['go_version_file']['default'], 'go/go.mod')

    def test_go_runs_on_a_big_endian_host_off_a_pull_request(self):
        _, workflow = load('go')
        job = workflow['jobs']['test-big-endian']
        self.assertIn("inputs.mode != 'pr'", job['if'])
        self.assertEqual(job['runs-on'], 'ubuntu-26.04')
        uses = [s.get('uses', '') for s in job['steps']]
        self.assertTrue(any(u.startswith('docker/setup-qemu-action@') for u in uses))
        self.assertIn('./.hyperforge/.github/actions/fetch-companions', uses)
        fetch = next(s for s in job['steps'] if s.get('name') == 'Fetch the companions')
        self.assertEqual(fetch['with']['leg'], 'hyper-test-go.yml:test-big-endian:linux-x64')
        suite = next(s for s in job['steps'] if s.get('name') == 'Go on s390x, under QEMU')
        self.assertIn('go_big_endian.sh', suite['run'])



class PullRequestLegs(unittest.TestCase):
    """A pull request asks whether the code broke: linux-x64 alone, except Swift, on osx-arm64.
    Every other platform runs on the board and a release, so their lists stay whole."""
    FILES = ['hyper-core', 'hyper-test-csharp', 'hyper-test-go', 'hyper-test-java', 'hyper-test-python',
             'hyper-test-r', 'hyper-test-ruby', 'hyper-test-swift', 'hyper-test-typescript']
    RID = {'hyper-test-swift': 'osx-arm64'}

    def jobs(self, name):
        path = Path(__file__).resolve().parent.parent / 'workflows' / f'{name}.yml'
        return yaml_lite.load(path.read_text(encoding='utf-8'))['jobs']

    def test_the_main_job_runs_one_leg_on_a_pull_request(self):
        for name in self.FILES:
            job = self.jobs(name)['core' if name == 'hyper-core' else 'test']
            with self.subTest(name):
                self.assertEqual([r['rid'] for r in companion_map.matrix_rows(job, 'pr')],
                                 [self.RID.get(name, 'linux-x64')])

    def test_a_pull_requests_row_is_the_release_row(self):
        for name in self.FILES:
            job = self.jobs(name)['core' if name == 'hyper-core' else 'test']
            with self.subTest(name):
                release = {r['rid']: r for r in companion_map.matrix_rows(job, 'release')}
                self.assertEqual(companion_map.matrix_rows(job, 'pr'), [release[self.RID.get(name, 'linux-x64')]])
                self.assertGreater(len(release), 1)

    def test_the_crate_name_survives_format(self):
        row = companion_map.matrix_rows(self.jobs('hyper-test-java')['test'], 'pr')[0]
        self.assertEqual(row['lib_name'], 'lib${{ inputs.crate }}.so')
        self.assertEqual(row['jni_lib_name'], 'lib${{ inputs.crate }}_jni.so')

    def test_musl_is_off_on_a_pull_request(self):
        musl = {'hyper-core': 'core-musl', 'hyper-test-csharp': 'test-musl', 'hyper-test-go': 'test-musl',
                'hyper-test-java': 'test-musl', 'hyper-test-python': 'test-musl', 'hyper-test-ruby': 'test-musl',
                'hyper-test-typescript': 'test-musl'}
        for name, job in musl.items():
            with self.subTest(name):
                self.assertIn("inputs.mode != 'pr'", self.jobs(name)[job]['if'])

    def test_the_go_big_endian_host_is_off_on_a_pull_request(self):
        self.assertIn("inputs.mode != 'pr'", self.jobs('hyper-test-go')['test-big-endian']['if'])


if __name__ == '__main__':
    unittest.main()
