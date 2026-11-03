"""Shape contracts for the companion lists the PHP build and the PIE acceptance workflows execute.

Both keep their legacy inputs (`php_companions`, `companions`), but what runs is the list
hyper_config.py derives from the consumer's `companions` block, falling back to the input
only when there is no block. The registry build is skipped only when a block exists and the
run is in companion mode (hyper_mode.py, from the event, never `inputs.purpose`).
"""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import unittest
from unittest import mock

import yaml_lite

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'
PHP = WORKFLOWS / 'hyper-build-php-extension.yml'
PIE = WORKFLOWS / 'hyper-pie-acceptance.yml'
COMPANIONS = WORKFLOWS / 'hyper-companions.yml'
ACTIONS = WORKFLOWS.parent / 'actions'
SCRIPTS = WORKFLOWS.parent / 'scripts'
PACKAGE = '@skunkwerkx/hypercast'
REGISTRY_STEP = 'Build the companion extensions'


def evaluate(expression, context):
    """A workflow `if:` over dotted context names, string literals, ==, !=, &&, || and !."""
    py = expression.replace('&&', ' and ').replace('||', ' or ')
    py = re.sub(r'!(?!=)', ' not ', py)
    py = re.sub(r"'[^']*'|\b[a-z_]+(?:\.[a-z_]+)+\b",
                lambda m: m.group(0) if m.group(0).startswith("'") else f'ctx[{m.group(0)!r}]', py)
    return eval(py, {'ctx': context})


class CompanionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.php_text = PHP.read_text()
        self.pie_text = PIE.read_text()
        self.php = yaml_lite.load(self.php_text)
        self.pie = yaml_lite.load(self.pie_text)

    def php_step(self, name):
        return next(s for s in self.php['jobs']['build']['steps'] if s.get('name') == name)

    def test_php_and_pie_workflows_execute_the_effective_lists(self):
        build = self.php['jobs']['build']
        # The job env no longer feeds the input to the scripts.
        self.assertNotIn('COMPANIONS', build['env'])
        for step in build['steps']:
            if 'php_ext_companions.py' in step.get('run', '') or 'php_ext_musl.sh' in step.get('run', ''):
                with self.subTest(step=step.get('name')):
                    self.assertNotIn('inputs.', str(step))
        # The one place the input is read is the step that derives the effective list.
        reads = [s.get('name') for s in build['steps'] if 'inputs.php_companions' in str(s)]
        self.assertEqual(reads, ['The companions this run builds'])
        self.assertEqual(self.php_text.count('inputs.php_companions'), 1)
        # PIE: the install job reads the derived list, the companions job reads the input.
        install = self.pie['jobs']['install']
        self.assertNotIn('inputs.companions', str(install))
        self.assertIn('needs.companions.outputs.companions', install['env']['COMPANIONS'])
        self.assertIn('companions', install['needs'])
        self.assertEqual([n for n, j in self.pie['jobs'].items() if 'inputs.companions' in str(j)], ['companions'])

    def test_php_registry_build_skips_only_with_block_in_companion_mode(self):
        step = self.php_step(REGISTRY_STEP)
        condition = step['if']
        self.assertIn('steps.companions.outputs.has_block', condition)
        self.assertIn('steps.mode.outputs.companion_mode', condition)
        for has_block in (True, False):
            for mode in ('companion', 'release'):
                context = {'steps.companions.outputs.companions': 'hypercast',
                           'steps.companions.outputs.has_block': 'true' if has_block else 'false',
                           'steps.mode.outputs.companion_mode': mode, 'matrix.kind': 'glibc'}
                with self.subTest(block=has_block, mode=mode):
                    self.assertEqual(bool(evaluate(condition, context)), not (has_block and mode == 'companion'))
        # Still only on the legs that run it, and never with nothing to build.
        base = {'steps.companions.outputs.companions': 'hypercast', 'steps.companions.outputs.has_block': 'false',
                'steps.mode.outputs.companion_mode': 'release', 'matrix.kind': 'musl'}
        self.assertFalse(evaluate(condition, base))
        self.assertFalse(evaluate(condition, {**base, 'matrix.kind': 'macos', 'steps.companions.outputs.companions': ''}))
        self.assertTrue(evaluate(condition, {**base, 'matrix.kind': 'macos'}))

    def test_every_leg_builds_the_list_the_skip_step_leaves(self):
        # Unix, Windows and Alpine all read COMPANIONS, which one step sets: empty with a block in
        # companion mode, else the effective list.
        step = self.php_step('The companions built from the registry')
        self.assertIn('steps.companions.outputs.companions', step['env']['LIST'])
        self.assertIn('steps.companions.outputs.has_block', step['env']['HAS_BLOCK'])
        self.assertIn('steps.mode.outputs.companion_mode', step['env']['COMPANION_MODE'])
        self.assertIn('[ "$HAS_BLOCK" = true ] && [ "$COMPANION_MODE" = companion ]', step['run'])
        self.assertIn('COMPANIONS=$LIST', step['run'])
        names = [s.get('name') for s in self.php['jobs']['build']['steps']]
        self.assertLess(names.index('The companions built from the registry'), names.index(REGISTRY_STEP))
        self.assertLess(names.index('The companions built from the registry'), names.index('Build the extension (Windows)'))
        self.assertLess(names.index('The companions built from the registry'), names.index('Build, load-check, test and pack on Alpine'))

    def test_mode_comes_from_the_event_never_inputs_purpose(self):
        step = self.php_step('The mode this run is in')
        self.assertEqual(step['id'], 'mode')
        self.assertIn('hyper_mode.py', step['run'])
        self.assertNotIn('inputs.purpose', self.php_text)
        self.assertNotIn('inputs.purpose', self.pie_text)

    def test_mode_runs_only_with_a_block_and_after_the_companions_step(self):
        # Without a block nothing needs the mode, and hyper_mode.py refuses a dispatch with no
        # purpose: a block-less caller (HyperTabular's ci.yml, old re-runs) must not meet that.
        self.assertEqual(self.php_step('The mode this run is in')['if'], "steps.companions.outputs.has_block == 'true'")
        names = [s.get('name') for s in self.php['jobs']['build']['steps']]
        self.assertLess(names.index('The companions this run builds'), names.index('The mode this run is in'))
        # hyper_mode.py is called nowhere else in the workflows but the companions job, which only
        # a consumer with a block calls.
        callers = sorted(p.name for p in WORKFLOWS.glob('*.yml') if 'hyper_mode.py' in p.read_text().replace(
            '(hyper_mode.py, from the event', ''))
        self.assertEqual(callers, [PHP.name, COMPANIONS.name])

    def test_companion_steps_read_only_the_block_and_pass_values_with_equals(self):
        for text in (self.php_text, self.pie_text):
            self.assertNotRegex(text, r'--(names|packages) ["$]')
            self.assertEqual(len(re.findall(r'--(?:names|packages)="\$', text)), 2)

    def test_php_derives_its_list_from_the_checked_out_consumer(self):
        step = self.php_step('The companions this run builds')
        self.assertEqual(step['id'], 'companions')
        self.assertIn('check-php-companions', step['run'])
        self.assertIn('effective-php-companions', step['run'])
        self.assertLess(self.php_text.index('The companions this run builds'),
                        self.php_text.index(f'name: {REGISTRY_STEP}'))

    def test_pie_reads_the_consumers_block_only_when_called(self):
        job = self.pie['jobs']['companions']
        checkout = next(s for s in job['steps'] if s.get('with', {}).get('path') == 'consumer')
        self.assertEqual(checkout['with']['sparse-checkout'].strip(), '.github/hyper.json')
        self.assertIn('job.workflow_repository', checkout['if'])
        step = next(s for s in job['steps'] if s.get('id') == 'companions')
        self.assertIn('check-pie-companions', step['run'])
        self.assertIn('effective-pie-companions', step['run'])
        self.assertEqual(job['outputs']['companions'], '${{ steps.companions.outputs.companions }}')



SETUP = ('Check out the consumer', 'Validate Forge workflow revision', 'Check out the executing Forge revision',
         'The mode this run is in')
RELEASE_STEPS = ('No companion override or .companions/ in release mode', 'The companions are live on every registry but CRAN')
MISSING_KEY = ("set `HYPER_COMPANION_APP_KEY` (an Actions secret, and a Dependabot secret for Dependabot's PRs) "
               "and the variable `HYPER_COMPANION_APP_ID`")
EVENTS = {
    'release dispatch': {'github.event_name': 'workflow_dispatch', 'github.event.inputs.purpose': 'release'},
    'board dispatch': {'github.event_name': 'workflow_dispatch', 'github.event.inputs.purpose': 'board'},
    'pull_request': {'github.event_name': 'pull_request', 'github.event.inputs.purpose': ''},
    'schedule': {'github.event_name': 'schedule', 'github.event.inputs.purpose': ''},
    'push': {'github.event_name': 'push', 'github.event.inputs.purpose': ''},
}


def condition(step):
    """A step's `if:` without its `${{ }}`, or None."""
    value = step.get('if')
    if value is None:
        return None
    return re.sub(r'^\$\{\{\s*(.*?)\s*\}\}$', r'\1', value.strip())


def forge_files():
    """Every workflow and composite action the Forge ships."""
    return sorted(list(WORKFLOWS.glob('*.yml')) + list(ACTIONS.rglob('*.yml')))


class CompanionsJobTests(unittest.TestCase):
    def setUp(self):
        import hyper_mode
        self.mode_release = hyper_mode.MODE_RELEASE
        self.text = COMPANIONS.read_text()
        self.workflow = yaml_lite.load(self.text)
        self.job = self.workflow['jobs']['companions']
        self.steps = self.job['steps']

    def step(self, name):
        return next(s for s in self.steps if s.get('name') == name)

    def running(self, context):
        return [s['name'] for s in self.steps if condition(s) is None or evaluate(condition(s), context)]

    def test_companions_job_has_no_if(self):
        # A skipped need fails GitHub's implicit success() and skips every dependant, so the job
        # always runs; its steps choose.
        self.assertEqual(list(self.workflow['jobs']), ['companions'])
        self.assertNotIn('if', self.job)
        call = self.workflow['on']['workflow_call']
        self.assertNotIn('inputs', call)
        self.assertEqual(list(call['secrets']), ['HYPER_COMPANION_APP_KEY'])
        self.assertIs(call['secrets']['HYPER_COMPANION_APP_KEY']['required'], False)
        self.assertEqual([s['name'] for s in self.steps][:len(SETUP)], list(SETUP))
        for name in SETUP:
            with self.subTest(step=name):
                self.assertIsNone(condition(self.step(name)))
        for step in self.steps:
            if step.get('uses', '').startswith('actions/checkout@'):
                with self.subTest(step=step['name']):
                    self.assertIs(step['with']['persist-credentials'], False)

    def test_only_release_assertion_and_gate_run_in_release_mode(self):
        for event, context in EVENTS.items():
            with self.subTest(event=event):
                running = self.running(context)
                if event == 'release dispatch':
                    self.assertEqual(running, list(SETUP + RELEASE_STEPS))
                else:
                    self.assertEqual(running, [s['name'] for s in self.steps if s['name'] not in RELEASE_STEPS])
                    self.assertGreater(len(running), len(SETUP))
        assertion = self.step(RELEASE_STEPS[0])['run']
        self.assertEqual(assertion.strip(), 'python3 .hyperforge/.github/scripts/companion_fetch.py guard --root .')
        gate = self.step(RELEASE_STEPS[1])['run']
        self.assertIn('companion_gate.py', gate)
        self.assertIn('--root .', gate)

    def test_no_forge_file_reads_inputs_purpose(self):
        # Inside a called workflow `inputs.purpose` is empty: the purpose is read from the event,
        # `github.event.inputs.purpose`, and never from the reusable workflow's own inputs.
        files = forge_files()
        self.assertIn(COMPANIONS, files)
        self.assertTrue([f for f in files if f.parent != WORKFLOWS])
        readers = [f.name for f in files if re.search(r'(?<!event\.)\binputs\.purpose\b', f.read_text())]
        self.assertEqual(readers, [])

    def test_mode_conditions_are_MODE_RELEASE(self):
        conditions = {s['name']: condition(s) for s in self.steps if condition(s) is not None}
        self.assertTrue(conditions)
        for name, value in conditions.items():
            with self.subTest(step=name):
                if name in RELEASE_STEPS:
                    self.assertEqual(value, self.mode_release)
                else:
                    self.assertEqual(value, f'!({self.mode_release})')
        self.assertIn('hyper_mode.py', self.step('The mode this run is in')['run'])

    def test_only_hyper_companions_declares_the_key(self):
        declaring = [f.name for f in forge_files()
                     if 'HYPER_COMPANION_APP_KEY' in (yaml_lite.load(f.read_text()).get('on') or {}).get('workflow_call', {}).get('secrets', {})]
        self.assertEqual(declaring, [COMPANIONS.name])
        # And only it reads the secret, in the key check and the mint.
        readers = [f.name for f in forge_files() if 'secrets.HYPER_COMPANION_APP_KEY' in f.read_text()]
        self.assertEqual(readers, [COMPANIONS.name])
        users = [s['name'] for s in self.steps if 'secrets.HYPER_COMPANION_APP_KEY' in str(s)]
        self.assertEqual(users, ['The App key is here', 'Mint a read-only token for the companion repositories'])

    def test_token_requests_only_actions_read(self):
        mint = self.step('Mint a read-only token for the companion repositories')
        self.assertRegex(mint['uses'], r'^actions/create-github-app-token@[0-9a-f]{40}$')
        given = mint['with']
        self.assertEqual({k: v for k, v in given.items() if k.startswith('permission-')}, {'permission-actions': 'read'})
        self.assertEqual(given['owner'], 'SkunkWerkx')
        self.assertEqual(given['repositories'], '${{ steps.plan.outputs.repositories }}')
        self.assertEqual(given['client-id'], '${{ vars.HYPER_COMPANION_APP_ID }}')
        self.assertEqual(given['private-key'], '${{ secrets.HYPER_COMPANION_APP_KEY }}')
        self.assertNotIn('skip-token-revoke', given)
        # The token reaches the pin check and the download, and nothing else.
        users = [s['name'] for s in self.steps if 'steps.token.outputs.token' in str(s)]
        self.assertEqual(users, ['Check every pin', "Download the pinned run's artifacts"])
        download = self.step("Download the pinned run's artifacts")
        self.assertEqual(download['with']['github-token'], '${{ steps.token.outputs.token }}')
        self.assertEqual(download['with']['repository'], '${{ steps.plan.outputs.repo }}')
        self.assertEqual(download['with']['run-id'], '${{ steps.plan.outputs.run }}')
        self.assertEqual(download['with']['pattern'], '${{ steps.plan.outputs.pattern }}')
        self.assertIn('companion_pin.py --check --root .', self.step('Check every pin')['run'])
        self.assertEqual(self.step('Check every pin')['env']['GH_TOKEN'], '${{ steps.token.outputs.token }}')
        upload = self.step('Upload the mirror')
        self.assertEqual(upload['with']['name'], 'companion-${{ steps.plan.outputs.name }}')
        self.assertEqual(upload['with']['if-no-files-found'], 'error')

    def key_check(self, **values):
        """Run the key check's script with its env set as the expressions would give it."""
        import os
        import subprocess
        step = self.step('The App key is here')
        env = {**os.environ, **{name: values.get(name, 'false' if name == 'FORK' else 'x') for name in step['env']}}
        return subprocess.run(['bash', '-c', step['run']], env=env, capture_output=True, text=True)

    def test_missing_key_message(self):
        step = self.step('The App key is here')
        self.assertEqual(step['env'], {'FORK': '${{ github.event.pull_request.head.repo.fork }}',
                                       'HAS_KEY': "${{ secrets.HYPER_COMPANION_APP_KEY != '' }}",
                                       'APP_ID': '${{ vars.HYPER_COMPANION_APP_ID }}'})
        self.assertEqual(self.key_check(FORK='', HAS_KEY='true', APP_ID='123').returncode, 0)
        for missing in ({'HAS_KEY': 'false'}, {'APP_ID': ''}, {'HAS_KEY': 'false', 'APP_ID': ''}):
            with self.subTest(missing=missing):
                result = self.key_check(**{'HAS_KEY': 'true', 'APP_ID': '123', **missing})
                self.assertEqual(result.returncode, 1)
                self.assertIn(f'::error::{MISSING_KEY}', result.stdout)
        fork = self.key_check(FORK='true', HAS_KEY='false', APP_ID='')
        self.assertEqual(fork.returncode, 1)
        self.assertIn("::error::A pull request from a fork gets no secrets, so this board can't fetch the companion "
                      'artifacts. A maintainer re-runs it from a branch in this repository; nothing falls back to a '
                      'registry.', fork.stdout)
        self.assertNotIn(MISSING_KEY, fork.stdout)

    def test_dependabot_secret_documented(self):
        self.assertIn('an Actions secret', MISSING_KEY)
        self.assertIn("a Dependabot secret for Dependabot's PRs", MISSING_KEY)
        self.assertIn(MISSING_KEY, self.step('The App key is here')['run'])
        # The header says why both kinds exist.
        self.assertIn('Dependabot secret', self.text.split('\non:', 1)[0])

    def test_the_mirror_steps_read_the_plan_and_check_every_name(self):
        plan = self.step('The companions this run mirrors')
        self.assertEqual(plan['id'], 'plan')
        self.assertIn('companion_mirror.py plan --root .', plan['run'])
        download = self.step("Download the pinned run's artifacts")
        self.assertEqual(download['id'], 'download')
        # A failed download (an expired or absent artifact) is reported by the seal step, with
        # the spec's message, and still fails the job there.
        self.assertIs(download.get('continue-on-error'), True)
        seal = self.step('Seal the mirror')
        self.assertEqual(seal['env']['DOWNLOAD_OUTCOME'], '${{ steps.download.outcome }}')
        self.assertIn('companion_mirror.py seal', seal['run'])
        mint = self.step('Mint a read-only token for the companion repositories')
        self.assertIs(mint.get('continue-on-error'), True)
        self.assertEqual(self.step('Check every pin')['env']['TOKEN_OUTCOME'], '${{ steps.token.outcome }}')
        names = [s['name'] for s in self.steps]
        self.assertEqual(names[len(SETUP) + len(RELEASE_STEPS):],
                         ['The App key is here', 'The companions this run mirrors',
                          'Mint a read-only token for the companion repositories', 'Check every pin',
                          "Download the pinned run's artifacts", 'Seal the mirror', 'Upload the mirror'])

    def run_release_guard(self, files):
        """The release assertion's script, run in a consumer checkout holding `files`, with the
        Forge checked out beside it as .hyperforge, as the job lays them out."""
        import json
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / '.github' / 'hyper.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'crate': 'hypertabular', 'project': 'HyperTabular', 'companions': {
                'hypercast': {'repo': 'SkunkWerkx/HyperCast', 'commit': 'c' * 40, 'run': 1}}}))
            for relative, text in files.items():
                (Path(root) / relative).parent.mkdir(parents=True, exist_ok=True)
                (Path(root) / relative).write_text(text)
            os.symlink(WORKFLOWS.parents[1], Path(root) / '.hyperforge')
            # A home of its own, so the runner's (or this machine's) Cargo, Gradle and NuGet
            # configuration can't decide the result; no interpreter but this one is inspected.
            env = {'PATH': os.environ['PATH'], 'HOME': root, 'CARGO_HOME': f'{root}/.cargo-home'}
            return subprocess.run(['bash', '-c', self.step(RELEASE_STEPS[0])['run']], cwd=root, env=env,
                                  capture_output=True, text=True)

    def test_release_assertion_refuses_a_committed_override(self):
        clean = self.run_release_guard({'ruby/Gemfile': 'gemspec\n'})
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        for files, word in (({'ruby/Gemfile': 'gem "hypercast", path: "../HyperCast/ruby"\n'}, 'ruby/Gemfile'),
                            ({'.cargo/config.toml': '[patch.crates-io]\nhypercast = { path = "x" }\n'}, 'patch.crates-io'),
                            ({'.companions/env': 'x'}, '.companions')):
            with self.subTest(word=word):
                result = self.run_release_guard(files)
                self.assertEqual(result.returncode, 1)
                self.assertIn('::error::', result.stdout)
                self.assertIn(word, result.stdout)


FETCH = ACTIONS / 'fetch-companions' / 'action.yml'
PLAN = ACTIONS / 'fetch-companions' / 'plan.sh'
CONTEXTS = {
    # (has_block, companion_mode) -> the steps that run
    'no block': {'steps.plan.outputs.has_block': 'false', 'steps.mode.outputs.companion_mode': ''},
    'release': {'steps.plan.outputs.has_block': 'true', 'steps.mode.outputs.companion_mode': 'release'},
    'companion': {'steps.plan.outputs.has_block': 'true', 'steps.mode.outputs.companion_mode': 'companion'},
}
FETCH_STEPS = ('The companions this leg takes', 'The mode this run is in',
               'No companion override or .companions/ in release mode', "Download this run's companion mirror",
               'The mirror is the pin this repository names', "Check out the companion's source at the pin",
               "Stage and verify this leg's artifacts", 'Write .companions/env',
               'Wire each ecosystem and prove its origin', 'This leg is wired')
GUARD = 'No companion override or .companions/ in release mode'


class FetchCompanionsActionTests(unittest.TestCase):
    def setUp(self):
        self.text = FETCH.read_text()
        self.action = yaml_lite.load(self.text)
        self.steps = self.action['runs']['steps']

    def step(self, name):
        return next(s for s in self.steps if s.get('name') == name)

    def running(self, context):
        return [s['name'] for s in self.steps if condition(s) is None or evaluate(condition(s), context)]

    def test_fetch_is_noop_without_block_and_guard_only_in_release(self):
        self.assertEqual(self.action['runs']['using'], 'composite')
        self.assertEqual([s['name'] for s in self.steps], list(FETCH_STEPS))
        self.assertEqual(self.running(CONTEXTS['no block']), ['The companions this leg takes'])
        # Every later step needs the block itself, not only a mode the block-less leg never computes.
        for mode in ('', 'companion', 'release'):
            with self.subTest(has_block=False, mode=mode):
                self.assertEqual(self.running({'steps.plan.outputs.has_block': 'false',
                                               'steps.mode.outputs.companion_mode': mode}),
                                 ['The companions this leg takes'])
        self.assertEqual(self.running(CONTEXTS['release']),
                         ['The companions this leg takes', 'The mode this run is in', GUARD])
        self.assertEqual(self.running(CONTEXTS['companion']), [n for n in FETCH_STEPS if n != GUARD])
        # The plan step reads the block before anything else, and only it is unconditional.
        self.assertEqual([s['name'] for s in self.steps if condition(s) is None], ['The companions this leg takes'])
        self.assertEqual(self.step('The companions this leg takes')['run'], 'bash "$GITHUB_ACTION_PATH/plan.sh"')
        self.assertIn('"$python" "$FORGE_SCRIPTS/companion_fetch.py" plan --root "$ROOT"', PLAN.read_text())
        self.assertEqual(self.step('The companions this leg takes')['id'], 'plan')
        self.assertIn('hyper_mode.py', self.step('The mode this run is in')['run'])
        self.assertEqual(self.step('The mode this run is in')['id'], 'mode')
        self.assertIn('companion_fetch.py" guard --root "$ROOT"', self.step(GUARD)['run'])
        # active is true only when the last companion-mode step ran.
        self.assertEqual(self.action['outputs']['active']['value'], "${{ steps.active.outputs.active || 'false' }}")
        # mode is the mode step's, which runs only with a block: '' without one, so a leg's
        # release-mode step (R's url:: reference) never runs for a repository without companions.
        self.assertEqual(set(self.action['outputs']), {'active', 'mode', 'commit', 'python'})
        self.assertEqual(self.action['outputs']['python']['value'], '${{ steps.plan.outputs.python }}')
        self.assertEqual(self.action['outputs']['mode']['value'], '${{ steps.mode.outputs.companion_mode }}')
        self.assertEqual(self.step('This leg is wired')['id'], 'active')
        self.assertIn('active=true', self.step('This leg is wired')['run'])
        # commit, which keys every cache of a wired leg (test_cache_keys_never_cross_modes): the
        # pin's, set by the same last step as active, so '' wherever active is not 'true'.
        self.assertEqual(self.action['outputs']['commit']['value'], '${{ steps.active.outputs.commit }}')
        self.assertEqual(self.step('This leg is wired')['env'], {'COMMIT': '${{ steps.plan.outputs.commit }}'})
        self.assertIn('echo "commit=$COMMIT"', self.step('This leg is wired')['run'])
        for name, context in CONTEXTS.items():
            with self.subTest(context=name):
                self.assertEqual('This leg is wired' in self.running(context), name == 'companion')

    def test_fetch_reads_the_mode_from_hyper_mode_never_inputs_purpose(self):
        self.assertNotRegex(self.text, r'\binputs\.purpose\b')
        self.assertNotIn('event_name', self.text)
        for step in self.steps:
            if condition(step) is not None and step['name'] != 'The mode this run is in':
                with self.subTest(step=step['name']):
                    self.assertIn('steps.mode.outputs.companion_mode', condition(step))

    def test_fetch_downloads_from_this_run_with_its_own_token(self):
        download = self.step("Download this run's companion mirror")
        self.assertRegex(download['uses'], r'^actions/download-artifact@[0-9a-f]{40}$')
        self.assertEqual(download['with'], {'name': 'companion-${{ steps.plan.outputs.name }}',
                                            'path': '${{ runner.temp }}/companion-${{ steps.plan.outputs.name }}'})
        self.assertIs(download.get('continue-on-error'), True)
        self.assertNotIn('github-token', self.text)
        self.assertNotIn('secrets.', self.text)
        confirm = self.step('The mirror is the pin this repository names')
        self.assertEqual(confirm['env']['DOWNLOAD_OUTCOME'], '${{ steps.download.outcome }}')
        self.assertIn('companion_fetch.py" confirm', confirm['run'])
        checkout = self.step("Check out the companion's source at the pin")
        self.assertRegex(checkout['uses'], r'^actions/checkout@[0-9a-f]{40}$')
        self.assertEqual(checkout['with'], {'repository': '${{ steps.plan.outputs.repo }}',
                                            'ref': '${{ steps.plan.outputs.commit }}',
                                            'path': '${{ inputs.root }}/.companions/${{ steps.plan.outputs.name }}',
                                            # A job's second pass keeps the first's wiring.
                                            'clean': False,
                                            'persist-credentials': False})

    def test_fetch_passes_inputs_through_env_only(self):
        # An input is never spliced into a script.
        for step in self.steps:
            with self.subTest(step=step['name']):
                self.assertNotIn('${{', step.get('run', ''))
                if 'run' in step:
                    self.assertEqual(step['shell'], 'bash')
        self.assertEqual(set(self.action['inputs']), {'leg', 'ecosystems', 'root'})
        self.assertIs(self.action['inputs']['leg']['required'], True)
        self.assertEqual(self.action['inputs']['ecosystems']['default'], '')
        # The consumer's checkout: `.` unless a job checks it out elsewhere (the PIE acceptance's
        # consumer/). Every script takes it as --root, through the environment.
        self.assertEqual(self.action['inputs']['root']['default'], '.')
        rooted = [s for s in self.steps if '--root' in s.get('run', '')]
        self.assertEqual(len(rooted), 5)
        # The plan's own call is in plan.sh, given the same environment.
        rooted.append(self.step('The companions this leg takes'))
        self.assertIn('--root "$ROOT"', PLAN.read_text())
        self.assertNotIn('--root .', PLAN.read_text())
        for step in rooted:
            with self.subTest(step=step['name']):
                self.assertIn('--root "$ROOT"', step['run'] if step.get('id') != 'plan' else PLAN.read_text())
                self.assertEqual(step['env']['ROOT'], '${{ inputs.root }}')
        self.assertNotIn('--root .', self.text)
        for name in ('Stage and verify this leg\'s artifacts', 'Write .companions/env',
                     'Wire each ecosystem and prove its origin'):
            with self.subTest(step=name):
                self.assertIn('companion_fetch.py', self.step(name)['run'])


# A command position's `python3`: not `py=python3`, not a path's `/usr/bin/python3`.
PYTHON3 = re.compile(r'(^|[\s;&|(!]|\$\()python3\b')
# The Forge's companion scripts, run on the host by a leg after its fetch.
COMPANION_SCRIPT = re.compile(r'(\.hyperforge/\.github/scripts/|"\$forge/|"\$FORGE_SCRIPTS/)companion_\w+\.py')
# python3 calls a Windows-capable board job keeps, each never on a Windows runner without
# setup-python, and why. Everything else there runs the fetch's `python` output.
WINDOWS_PYTHON3 = {
    ('hyper-core.yml', 'core', "Name this RID's library and the smoke script"): 'after setup-python, as at HEAD',
    ('hyper-core.yml', 'core', 'Smoke-test the C archive'): 'after setup-python, as at HEAD',
    ('hyper-core-checks.yml', 'checks', 'A default-features = false consumer builds on the host and for '
                                        'wasm32-unknown-unknown'): "matrix.arch == 'x64', after setup-python",
    ('hyper-core-checks.yml', 'checks', 'Negative control, a corrupted corpus vector must fail conformance'):
        "matrix.arch == 'x64', after setup-python",
    ('hyper-test-java.yml', 'test', 'Build the JNI library'): 'the nm -D check, under RUNNER_OS = Linux',
}


FETCH_PY = '${{ steps.companions.outputs.python }}'


def without_fetch_python(step):
    """A proof step's env without FETCH_PYTHON, which must be the fetch's `python` output; None
    when nothing else is left."""
    env = dict(step.get('env') or {})
    assert env.pop('FETCH_PYTHON', None) == FETCH_PY, step.get('name')
    return env or None


def windows_capable(job):
    return 'windows' in (str(job.get('runs-on', '')) + str(job.get('strategy', ''))).lower()


def job_steps(job):
    """(step, the local action call it is inside or None) for every step a job runs."""
    for step in job.get('steps') or []:
        yield step, None
        uses = str(step.get('uses', ''))
        if uses.startswith(LOCAL_ACTION):
            action = yaml_lite.load((ACTIONS / uses[len(LOCAL_ACTION):] / 'action.yml').read_text())
            for inner in action['runs'].get('steps') or []:
                yield inner, step


class ResolvedPythonTests(unittest.TestCase):
    """fetch-companions resolves one Python 3.11+ (plan.sh) and every companion step runs it: on a
    Windows runner without setup-python, python3 may be missing or the WindowsApps alias. Without
    one, a leg with no block still passes, as it did before the action ran any Python."""

    def test_every_step_of_the_action_runs_the_resolved_python(self):
        action = yaml_lite.load(FETCH.read_text())
        for step in action['runs']['steps']:
            run = step.get('run', '')
            with self.subTest(step=step['name']):
                self.assertIsNone(PYTHON3.search(run))
                if step.get('id') == 'plan' or 'python' not in run.lower():
                    continue
                self.assertTrue(run.startswith('"$PYTHON" "$FORGE_SCRIPTS/'), run)
                self.assertEqual(step['env']['PYTHON'], '${{ steps.plan.outputs.python }}')
        plan = PLAN.read_text()
        self.assertIn("for candidate in python3 python 'py -3'; do", plan)
        self.assertIn('assert sys.version_info >= (3, 11)', plan)
        self.assertIn('*WindowsApps*) continue', plan)
        self.assertIn('echo "python=$python" >> "$GITHUB_OUTPUT"', plan)

    def test_every_companion_script_a_leg_runs_is_the_fetchs_python(self):
        checked = 0
        for path, job_name, _listed, fetch_id, _index in fetching_steps(local=False):
            job = yaml_lite.load(path.read_text())['jobs'][job_name]
            for step, call in job_steps(job):
                run = str(step.get('run', ''))
                if not COMPANION_SCRIPT.search(run) or str(step.get('uses', '')) or call and call.get('uses') == FETCH_USES:
                    continue
                with self.subTest(job=f'{path.name}:{job_name}', step=step.get('name')):
                    lines = [line for line in run.splitlines()
                             if COMPANION_SCRIPT.search(line) and 'companion_python /src' not in line]
                    # Never the runner's python3. The Python legs run the scripts with the
                    # setup-python interpreter they test; every other leg with the fetch's.
                    for line in lines:
                        self.assertIsNone(PYTHON3.search(line), line)
                    if path.name == 'hyper-test-python.yml' and not any('"$FETCH_PYTHON"' in x for x in lines):
                        continue
                    for line in lines:
                        self.assertIn('"$FETCH_PYTHON" ', line)
                    want = '${{ inputs.python }}' if call else f'${{{{ steps.{fetch_id}.outputs.python }}}}'
                    self.assertEqual((step.get('env') or {}).get('FETCH_PYTHON'), want)
                    if call:
                        self.assertEqual(call['with']['python'], f'${{{{ steps.{fetch_id}.outputs.python }}}}')
                    checked += 1
        self.assertGreater(checked, 30)

    def test_every_python3_on_a_windows_leg_is_the_fetchs(self):
        import companion_map
        kept = set()
        for path in sorted(WORKFLOWS.glob('*.yml')):
            if path.name in companion_map.LEGACY_WORKFLOWS:
                continue
            for job_name, job in (yaml_lite.load(path.read_text()).get('jobs') or {}).items():
                if not windows_capable(job):
                    continue
                for step, call in job_steps(job):
                    run = str(step.get('run', ''))
                    # In a container: the image's own python3, never the runner's.
                    calls = [line for line in run.splitlines() if PYTHON3.search(line) and 'in_docker ' not in line]
                    key = (path.name, job_name, step.get('name'))
                    with self.subTest(step=key, via=call and call.get('uses')):
                        if calls:
                            self.assertIn(key, WINDOWS_PYTHON3, calls)
                            kept.add(key)
                        # The PHP build's switch: python on Windows, as HEAD's PIE pin step ran it.
                        if 'py=python3' in run:
                            self.assertIn('if [ "$RUNNER_OS" = Windows ]; then py=python; fi', run)
        self.assertEqual(kept, set(WINDOWS_PYTHON3))

    def run_plan(self, path_dirs, config):
        import subprocess
        import tempfile
        bash = shutil.which('bash')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'consumer'
            (root / '.github').mkdir(parents=True)
            if config is not None:
                (root / '.github' / 'hyper.json').write_text(json.dumps(config))
            output = Path(tmp) / 'output'
            output.write_text('')
            tools = Path(tmp) / 'tools'
            tools.mkdir()
            for tool in ('grep', 'tr'):
                (tools / tool).symlink_to(shutil.which(tool))
            dirs = []
            for name, files in path_dirs:
                directory = Path(tmp) / name
                directory.mkdir(parents=True, exist_ok=True)
                for file, body in files.items():
                    (directory / file).write_text(body)
                    (directory / file).chmod(0o755)
                dirs.append(str(directory))
            env = {'PATH': os.pathsep.join(dirs + [str(tools)]), 'GITHUB_OUTPUT': str(output),
                   'FORGE_SCRIPTS': str(SCRIPTS), 'ROOT': str(root), 'LEG': 'hyper-test-c.yml:smoke:win-arm64',
                   'ECOSYSTEMS': ''}
            result = subprocess.run([bash, str(PLAN)], env=env, capture_output=True, text=True, timeout=120)
            return result, output.read_text()

    # The WindowsApps alias: on PATH, runs nothing, exits non-zero.
    ALIAS = '#!/bin/sh\necho "Python was not found; run without arguments to install from the Microsoft Store" >&2\nexit 49\n'
    # A real interpreter reporting 3.10: runs the probe's code, which must refuse it.
    OLD = ('#!/bin/sh\n[ "$1" = -c ] || exit 2\nexec ' + sys.executable
           + ' -c \'import sys; code = sys.argv[1]; sys.version_info = (3, 10, 0); exec(code)\' "$2"\n')
    NO_BLOCK = {'crate': 'hypercast'}
    BLOCK = {'crate': 'hypercast', 'companions': {'hyperuuid': {'repo': 'SkunkWerkx/HyperUuid'}}}

    def real(self):
        return f'#!/bin/sh\nexec {sys.executable} "$@"\n'

    def test_without_python_a_block_less_leg_skips_and_a_block_fails(self):
        none = [('bin', {'python3': self.ALIAS, 'python': self.OLD})]
        result, output = self.run_plan(none, self.NO_BLOCK)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(output, 'has_block=false\n')
        self.assertIn('nothing to fetch', result.stdout)
        # A real Python under WindowsApps is the alias's directory: not taken either.
        aliased = [('WindowsApps', {'python3': self.real()})]
        result, output = self.run_plan(aliased, self.NO_BLOCK)
        self.assertEqual((result.returncode, output), (0, 'has_block=false\n'), result.stdout + result.stderr)
        # With a block, the leg needs Python: it fails, naming what it tried.
        result, output = self.run_plan(none, self.BLOCK)
        self.assertEqual(result.returncode, 1)
        self.assertIn('::error::', result.stdout)
        self.assertIn('none of python3, python or py -3', result.stdout)
        self.assertEqual(output, '')
        # And with no hyper.json, as companion_fetch.py would say.
        result, _output = self.run_plan(none, None)
        self.assertEqual(result.returncode, 1)
        self.assertIn('is missing', result.stdout)

    def test_the_first_python_3_11_is_resolved_and_handed_on(self):
        for name, dirs in (('python', [('bin', {'python3': self.ALIAS, 'python': self.real()})]),
                           ('py -3', [('bin', {'python3': self.OLD,
                                               'py': f'#!/bin/sh\n[ "$1" = -3 ] || exit 2\nshift\nexec {sys.executable} "$@"\n'})])):
            with self.subTest(candidate=name):
                result, output = self.run_plan(dirs, self.NO_BLOCK)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(output, f'python={sys.executable}\nhas_block=false\n')


# The ecosystems whose wiring exists (companion_fetch.available holds for each): every
# non-legacy leg that overrides nothing else must fetch the companions.
WIRED = {'rust', 'go', 'python', 'pyodide', 'npm', 'ruby', 'php', 'java', 'csharp', 'swift', 'c', 'r'}
FETCH_USES = './.hyperforge/.github/actions/fetch-companions'
# A step that resolves the companion: one that runs a tool which reads a manifest, or a Forge
# action that runs one in a container.
RESOLVES = re.compile(r'(^|[\s;&|(])(cargo|go|tinygo|wasm-pack|maturin|npm|bundle|gradlew|dotnet|composer|swift|R|Rscript)\s'
                      # pip, which reads the consumer's requirement, and pytest, whose conftest.py may
                      # install it; a venv's own (`"$RUNNER_TEMP/v/bin/pip" install`) too.
                      r'|(^|[\s;&|(/])(pip3?|pytest)"?\s'
                      # The C smokes, which source c/smoke/<name>.sh for the companion's archive.
                      r'|(^|[\s;&|(])(bash )?c/smoke/[\w-]+\.sh\s', re.M)
RESOLVING_ACTIONS = ('musl-native', 'musl-suite', 'static-libs', 'ruby-magnus', 'ruby-magnus-musl', 'ruby-wasm-suite',
                     'pyodide-suite', 'swift-static-suite')
# Third-party actions that resolve the companion: pak (setup-r-dependencies) solves the R
# package's requirements, and the R check installs and loads them.
RESOLVING_REMOTE_ACTIONS = ('r-lib/actions/setup-r-dependencies@', 'r-lib/actions/check-r-package@')
RUST_TOOLCHAIN = 'dtolnay/rust-toolchain@'
# The legs with a WIRED ecosystem that take no fetch step yet, each with why and the task that
# adds it: their Rust is built where neither the host's patch nor a container helper reaches.
# An entry whose leg gains the step fails below.
DEFERRED = {}


# A toolchain's banner (`swift --version`, the image check) reads no manifest.
VERSION_ONLY = re.compile(r'(^|[\s;&|(])(cargo|go|npm|dotnet|swift)\s+--version\b')


def resolves(step):
    uses = str(step.get('uses', ''))
    return (bool(RESOLVES.search(VERSION_ONLY.sub(' ', str(step.get('run', '')))))
            or any(uses.endswith('/' + a) for a in RESOLVING_ACTIONS)
            or uses.startswith(('PyO3/maturin-action@', *RESOLVING_REMOTE_ACTIONS)))


class FetchStepTests(unittest.TestCase):
    def wired_legs(self):
        """Every leg (one per job) with a WIRED ecosystem among its own: the ones whose wiring
        exists, so the fetch step must be there for it to run."""
        import companion_map
        found = {}
        for leg in companion_map.legs():
            ecosystems = companion_map.ecosystems_for(leg)
            if leg.workflow not in companion_map.LEGACY_WORKFLOWS and set(ecosystems) & WIRED:
                found.setdefault((leg.workflow, leg.job), leg)
        return found

    def test_every_wired_leg_fetches_the_companions_first(self):
        import companion_map
        legs = self.wired_legs()
        self.assertEqual(len(legs), 40)
        self.assertLessEqual(set(DEFERRED), set(legs))
        for (workflow, job_name), leg in sorted(legs.items()):
            with self.subTest(leg=f'{workflow}:{job_name}'):
                job = yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][job_name]
                steps = job['steps']
                fetches = [i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES]
                ecosystems = set(companion_map.ecosystems_for(leg))
                if (workflow, job_name) in DEFERRED:
                    self.assertEqual(fetches, [], 'wired now: take it off DEFERRED')
                    self.assertTrue(ecosystems - WIRED, 'every ecosystem wired: nothing left to defer for')
                    continue
                self.assertEqual(len(fetches), 1)
                fetch = steps[fetches[0]]
                self.assertEqual(fetch['name'], 'Fetch the companions')
                # The leg string names this very job, its matrix rid where it has one, and the
                # matrix's PHP minor where the leg's artifacts need it.
                given = fetch['with']['leg']
                parsed = companion_map.parse_leg(given.replace('${{ matrix.rid }}', leg.rid or 'x')
                                                 .replace('${{ matrix.minor }}', '8.4')
                                                 .replace('${{ matrix.abi }}', '4.0'))
                self.assertEqual((parsed.workflow, parsed.job), (workflow, job_name))
                if 'rid' in str((job.get('strategy') or {}).get('matrix')) or 'matrix.rid' in str(job):
                    self.assertEqual(given.split(':')[2:3], ['${{ matrix.rid }}'], given)
                    self.assertEqual(parsed.rid, leg.rid or 'x')
                # A leg whose artifacts are named by rid carries this job's (artifacts_for refuses
                # a leg without one; a wrong one stages another platform's).
                if any('{rid}' in name for role in companion_map._roles(leg) for name in companion_map.ROLES[role][1]):
                    self.assertEqual(parsed.rid, leg.rid, given)
                # Likewise the matrix's PHP minor, where they are named by one.
                if any('{minor}' in name for role in companion_map._roles(leg) for name in companion_map.ROLES[role][1]):
                    self.assertIn(':minor=${{ matrix.minor }}', given)
                # And the matrix's Ruby ABI where they are named by one and no rid (the ruby.wasm
                # archive): without it the fetch would stage every minor's.
                if any('{abi}' in name and '{rid}' not in name
                       for role in companion_map._roles(leg) for name in companion_map.ROLES[role][1]):
                    self.assertIn(':abi=${{ matrix.abi }}', given)
                self.assertEqual(set(companion_map.ecosystems_for(parsed)), ecosystems)
                # After the consumer checkout (which would remove .companions/) and the Forge's,
                # before anything resolves the companion.
                self.assertTrue(steps[0]['uses'].startswith('actions/checkout@'))
                forge = [i for i, s in enumerate(steps) if (s.get('with') or {}).get('path') == '.hyperforge']
                self.assertEqual(len(forge), 1)
                self.assertIn(steps[forge[0]].get('if'), (None, fetch.get('if')))
                self.assertLess(forge[0], fetches[0])
                first = next(i for i, s in enumerate(steps) if resolves(s))
                self.assertLess(fetches[0], first, steps[first].get('name') or steps[first].get('uses'))
                # Gated only by the job's own switch for the tool the wiring runs.
                self.assertIn(fetch.get('if'), (None, 'inputs.lint_go', 'inputs.lint_java_jni', 'inputs.lint_node',
                                                 'inputs.lint_ruby', 'inputs.lint_r'))
                # Wiring Rust runs cargo (cargo update) on the host: the job's own toolchain is
                # set up first, under no narrower condition than the fetch, never the image's.
                if 'rust' in ecosystems:
                    # A pair split on one condition (`== 'win'`, `!= 'win'`) covers every leg too.
                    conditions = {steps[i].get('if') for i in range(fetches[0])
                                  if str(steps[i].get('uses', '')).startswith(RUST_TOOLCHAIN)}
                    paired = any(c and c.replace('!=', '==') in conditions for c in conditions if '!=' in (c or ''))
                    self.assertTrue(conditions & {None, fetch.get('if')} or paired,
                                    f'no Rust toolchain step for every leg before the fetch: {conditions}')

    def test_maturin_images_take_the_patch(self):
        # maturin-action builds Linux wheels in its own image, which the host's $CARGO_HOME
        # never reaches: every maturin build step of a fetching job writes and proves the patch
        # there. `command: sdist` is the exception: maturin-action runs it on the runner
        # (useDocker only for build and publish), whose $CARGO_HOME the fetch already patched,
        # so it takes no before-script.
        BEFORE = ('. "$GITHUB_WORKSPACE/.hyperforge/.github/scripts/companion_container.sh"\n'
                  'companion_cargo "$GITHUB_WORKSPACE" "$GITHUB_WORKSPACE" "$GITHUB_WORKSPACE/rust"\n')
        found, sdists = 0, 0
        for workflow in sorted(WORKFLOWS.glob('hyper-*.yml')):
            for job_name, job in (yaml_lite.load(workflow.read_text()).get('jobs') or {}).items():
                steps = job.get('steps') or []
                if not any(s.get('uses') == FETCH_USES for s in steps):
                    continue
                for step in steps:
                    if str(step.get('uses', '')).startswith('PyO3/maturin-action@'):
                        with self.subTest(job=f'{workflow.name}:{job_name}', step=step.get('name')):
                            if step['with'].get('command') == 'sdist':
                                sdists += 1
                                self.assertIsNone(step['with'].get('before-script-linux'))
                            else:
                                found += 1
                                self.assertEqual(step['with'].get('before-script-linux'), BEFORE)
        self.assertEqual((found, sdists), (2, 1))

    def test_python_legs_install_the_pin_in_every_interpreter(self):
        # The Python wiring installs the pinned wheel into one interpreter. A fetch without
        # HYPER_COMPANION_PYTHON installs into the job's python3, which is set up first; with
        # `none` (Rosetta, containers, fresh venvs) every interpreter the job installs the
        # consumer into is wired after it, by companion_wire.py or companion_python, and proven
        # again by companion_origin.py once the consumer is in.
        import companion_map
        seen = {}
        for (workflow, job_name), leg in sorted(self.wired_legs().items()):
            if 'python' not in companion_map.ecosystems_for(leg):
                continue
            with self.subTest(leg=f'{workflow}:{job_name}'):
                steps = yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][job_name]['steps']
                index = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
                mode = (steps[index].get('env') or {}).get('HYPER_COMPANION_PYTHON')
                later = '\n'.join(str(s.get('run', '')) + str(s.get('with', '')) for s in steps[index + 1:])
                if mode != 'none':
                    # The interpreter the fetch installs into is named, by setup-python's own
                    # output (on Windows, python3 on PATH is not it), and set up first.
                    named = re.fullmatch(r'\$\{\{ steps\.([\w-]+)\.outputs\.python-path \}\}', str(mode))
                    self.assertTrue(named, f'HYPER_COMPANION_PYTHON is {mode!r}')
                    setup = [i for i, s in enumerate(steps) if s.get('id') == named.group(1)]
                    self.assertTrue(setup and setup[0] < index, 'the interpreter the fetch installs into comes first')
                    self.assertTrue(str(steps[setup[0]].get('uses', '')).startswith('actions/setup-python@'))
                    self.assertIsNot((steps[setup[0]].get('with') or {}).get('update-environment'), False)
                else:
                    self.assertTrue(re.search(r'companion_wire\.py" python|companion_python .* wire|\'binding\': \'python\'', later),
                                    'nothing wires an interpreter after a fetch with none')
                seen[f'{workflow}:{job_name}'] = 'none' if mode == 'none' else 'setup-python'
        self.assertEqual(seen, {'hyper-build-wheels.yml:build-sdist': 'none', 'hyper-build-wheels.yml:build-wheels': 'none',
                                'hyper-build-wheels.yml:sdist-fallthrough': 'none',
                                'hyper-test-python.yml:test': 'setup-python', 'hyper-test-python.yml:test-musl': 'none'})
        job = yaml_lite.load((WORKFLOWS / 'hyper-test-python.yml').read_text())['jobs']['test']
        for name in ('Test Python bindings', 'Test Python bindings — free-threaded'):
            step = next(s for s in job['steps'] if s.get('name') == name)
            self.assertEqual(step['env']['COMPANIONS'], '${{ steps.companions.outputs.active }}', name)

    # A pip install after the proof could swap the pin for an index's copy, unseen: in every
    # Python leg the origin is proven after the last install of each interpreter, and before
    # the suite or check that runs on it. Each needle is found after the one before it.
    INSTALL = re.compile(r'pip3?"? install')
    ORIGIN_ORDER = {
        ('hyper-test-python.yml', 'test', 'Test Python bindings'): (
            'pip install pytest mypy',
            '"$COMPANIONS" == true',
            'companion_origin.py python --root .. --python "$(python -c \'import sys; print(sys.executable)\')"',
            '\npytest\n'),
        ('hyper-test-python.yml', 'test', 'Test Python bindings — free-threaded'): (
            'companion_wire.py python --root . --python "$PY"',
            '"$PY" -m pip install --quiet pytest',
            'companion_origin.py python --root .. --python "$PY"',
            '"$PY" -m pytest'),
        ('hyper-build-wheels.yml', 'sdist-fallthrough', 'Install the sdist and run the suite against the install'): (
            'companion_wire.py" python --root . --python "$RUNNER_TEMP/v/bin/python"',
            '"$RUNNER_TEMP/v/bin/pip" install "$sdist"',
            '"$RUNNER_TEMP/v/bin/pip" install pytest mypy',
            'companion_origin.py" python --root . --python "$RUNNER_TEMP/v/bin/python"',
            '"$RUNNER_TEMP/v/bin/python" -m pytest'),
        # $pre wires, the consumer installs and the check runs, then $post proves: the last
        # thing in the container.
        ('hyper-build-wheels.yml', 'build-sdist', 'Install the sdist at the floor on manylinux2014, and one minor below'): (
            'companion_python /src $forge /v/bin/python wire && "',
            'post=" && companion_python /src $forge /v/bin/python origin"',
            '$pre$then/v/bin/pip install -q /dist/$CRATE-$WANT.tar.gz && /v/bin/python -I -c \\"$check\\"$post"'),
        ('hyper-build-wheels.yml', 'build-sdist', 'Install the sdist at the floor on Alpine'): (
            'companion_python /src $forge /v/bin/python wire && "',
            'post=" && companion_python /src $forge /v/bin/python origin"',
            '$pre/v/bin/pip install -q /dist/$CRATE-$WANT.tar.gz && /v/bin/python -I -c \\"$check\\"$post"'),
        # Run as shell in BuildWheelsInstallTests, both with and without a pin.
        ('hyper-build-wheels.yml', 'build-wheels', 'Install each wheel and call into it'): None,
    }

    def test_python_legs_prove_the_origin_after_the_last_install(self):
        import companion_map
        covered = set()
        for (workflow, job_name), leg in sorted(self.wired_legs().items()):
            if 'python' not in companion_map.ecosystems_for(leg):
                continue
            steps = yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][job_name]['steps']
            index = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
            for step in steps[index + 1:]:
                run = str(step.get('run', ''))
                if not self.INSTALL.search(run):
                    continue
                key = (workflow, job_name, step.get('name'))
                covered.add(key)
                with self.subTest(step=key):
                    self.assertIn(key, self.ORIGIN_ORDER, 'an install step no ordering covers')
                    needles = self.ORIGIN_ORDER[key]
                    if needles is None:
                        continue
                    at = 0
                    for needle in needles:
                        found = run.find(needle, at)
                        self.assertGreaterEqual(found, 0, f'{needle!r} not found after position {at}')
                        at = found + len(needle)
                    # build-sdist's proof is $post, used last on the install's own line.
                    origin = max(run.rfind('companion_origin.py'), run.rfind('python origin"'), run.rfind('$post"'))
                    self.assertFalse(self.INSTALL.search(run, origin), 'an install after the last proof')
        self.assertEqual(covered, set(self.ORIGIN_ORDER))
        # The musl suite's interpreter, inside its container.
        text = (WORKFLOWS.parent / 'actions' / 'musl-suite' / 'suite-musl.sh').read_text()
        at = 0
        for needle in ('companion_python /src /forge python3 wire', 'pip install -q --root-user-action=ignore pytest /wheels/*.whl',
                       'companion_python /src /forge python3 origin', 'pytest -q'):
            at = text.index(needle, at) + len(needle)
        self.assertFalse(self.INSTALL.search(text, text.index('companion_python /src /forge python3 origin')))

    def test_pyodide_suite_proves_the_pinned_wheel(self):
        job = yaml_lite.load((WORKFLOWS / 'hyper-build-wheels.yml').read_text())['jobs']['build-pyodide-wheel']
        names = [s.get('name') or s.get('uses') for s in job['steps']]
        deps = next(s for s in job['steps'] if s.get('id') == 'pyodide-deps')
        self.assertIn('companion_wire.py pyodide --root . --deps "$RUNNER_TEMP/pyodide-deps"', deps['run'])
        self.assertIn('_PYODIDE_REQ=', deps['run'])
        suite = next(s for s in job['steps'] if str(s.get('uses', '')).endswith('/pyodide-suite'))
        self.assertEqual(suite['with']['dependency_sums'], '${{ steps.pyodide-deps.outputs.sums }}')
        self.assertLess(names.index(deps['name']), job['steps'].index(suite))
        action = yaml_lite.load((ACTIONS / 'pyodide-suite' / 'action.yml').read_text())
        # The browser pass installs the dependency wheels first, as at HEAD (it cannot reach
        # PyPI); the Node pass, which at HEAD let micropip resolve them from PyPI, takes them
        # only with a pin, so a leg without one runs exactly as before.
        deps = {'node suite-pyodide.mjs':
                "${{ inputs.dependency_sums != '' && inputs.dependency_wheels || '' }}",
                'node browser-pyodide.mjs': '${{ inputs.dependency_wheels }}'}
        seen = set()
        for step in action['runs']['steps']:
            for command, expected in deps.items():
                if command in str(step.get('run', '')):
                    seen.add(command)
                    with self.subTest(step=step['name']):
                        self.assertEqual(step['env']['PYODIDE_SUITE_SUMS'], '${{ inputs.dependency_sums }}')
                        self.assertEqual(step['env']['PYODIDE_SUITE_DEPS'], expected)
        self.assertEqual(seen, set(deps))

    # Each npm leg: the steps that install the consumer (npm ci, npm install), the proof after
    # the last of them, and the suite the proof precedes, by step name.
    NPM_ORDER = {
        ('hyper-test-typescript.yml', 'test'): (
            ('Build the TypeScript (napi-rs) addon',), 'The suites take the pinned companion',
            'Test TypeScript bindings on Node', '--dir typescript --installed'),
        ('hyper-test-typescript.yml', 'test-osx-x64'): (
            ('Cross-build the TypeScript addon for x86_64',), 'The suite takes the pinned companion',
            'Test TypeScript bindings on x64 Node, under Rosetta', '--dir typescript --installed'),
        ('hyper-lint-typescript.yml', 'lint'): (
            ('npm ci',), 'npm ci took the pinned companion', 'npm run lint', '--dir typescript --installed'),
        ('hyper-build-wasm.yml', 'build-node-wasi'): (
            ('Build the WASI module and its loaders',), 'The suite takes the pinned companion',
            'The conformance suite against the WASI module, native refused', '--dir typescript --installed'),
    }
    NPM_INSTALL = re.compile(r'\bnpm (ci|install)\b')

    def test_npm_legs_set_up_node_first_and_prove_the_origin_after_the_last_install(self):
        import companion_map
        covered = set()
        for (workflow, job_name), leg in sorted(self.wired_legs().items()):
            if 'npm' not in companion_map.ecosystems_for(leg):
                continue
            covered.add((workflow, job_name))
            with self.subTest(leg=f'{workflow}:{job_name}'):
                steps = yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][job_name]['steps']
                index = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
                fetch = steps[index]
                # The wiring runs npm (pack, install --package-lock-only): the job's own Node is set
                # up first, under no narrower condition than the fetch.
                nodes = [s for s in steps[:index] if str(s.get('uses', '')).startswith('actions/setup-node@')]
                self.assertTrue(nodes and nodes[0].get('if') in (None, fetch.get('if')), 'no Node before the fetch')
                if job_name == 'test-musl':
                    continue  # the containers prove it: suite-musl.sh, below
                self.assertEqual(fetch.get('id'), 'companions')
                installs, origin, suite, flags = self.NPM_ORDER[(workflow, job_name)]
                names = [s.get('name') for s in steps]
                proof = steps[names.index(origin)]
                self.assertEqual(proof['run'], '"$FETCH_PYTHON" .hyperforge/.github/scripts/companion_origin.py npm --root . '
                                               + flags)
                self.assertEqual(proof['if'].replace('inputs.lint_node && ', ''),
                                 "steps.companions.outputs.active == 'true'")
                # Every install comes before the proof, the suite after it, and nothing installs in
                # between (the x64 Node of the Rosetta leg is set up before its proof).
                last = max(i for i, s in enumerate(steps) if self.NPM_INSTALL.search(str(s.get('run', '')))
                           and i < names.index(suite))
                self.assertEqual({names[i] for i in range(index, names.index(suite))
                                  if self.NPM_INSTALL.search(str(steps[i].get('run', '')))}, set(installs))
                self.assertLess(last, names.index(origin))
                self.assertLess(names.index(origin), names.index(suite))
                between = steps[names.index(origin) + 1:names.index(suite)]
                self.assertFalse([s for s in between if self.NPM_INSTALL.search(str(s.get('run', '')))])
                if job_name == 'test-osx-x64':
                    setups = [i for i, s in enumerate(steps) if str(s.get('uses', '')).startswith('actions/setup-node@')]
                    self.assertLess(setups[-1], names.index(origin), 'the proof runs under the x64 Node')
        self.assertEqual(covered, set(self.NPM_ORDER) | {('hyper-test-typescript.yml', 'test-musl')})
        # browser-smoke installs and bundles in one step (HEAD's): its proof follows it.
        steps = yaml_lite.load((WORKFLOWS / 'hyper-build-wasm.yml').read_text())['jobs']['build-node-wasi']['steps']
        names = [s.get('name') for s in steps]
        page = names.index('The page took the pinned companion')
        self.assertEqual(page, names.index('The package bundled into a page, in headless Chrome') + 1)
        self.assertIn('--dir typescript/browser-smoke --installed', steps[page]['run'])
        # The musl suite: the build container wires before `npm ci` and proves after it, before the
        # build and the suite; Bun's installs the tarball itself and proves it before `bun run test:bun`.
        text = (WORKFLOWS.parent / 'actions' / 'musl-suite' / 'suite-musl.sh').read_text()
        at = 0
        for needle in ('companion_cargo /src /work /work/rust /work/typescript',
                       'companion_npm /src /work /forge /work/typescript wire', 'npm ci || exit 1',
                       'companion_npm /src /work /forge /work/typescript origin --installed',
                       'npm run build && npm test || exit 1',
                       'companion_npm /src /work /forge /work/typescript unpack', 'bun run test:bun'):
            at = text.index(needle, at) + len(needle)
        node = text[text.index('\n  node)'):text.index('\n  ruby)')]
        self.assertEqual(node.count('-v "$forge_scripts:/forge:ro"'), 2)
        self.assertEqual(len(self.NPM_INSTALL.findall(node)), 1)

    # HEAD 666a621's commands in each npm leg, by step: without a pin, every step but the fetch
    # and the steps gated on its `active` output runs exactly these (sha256 of `run`, first 16
    # hex digits). The lint job's Validate step came with the Rust wiring (Task 9).
    HEAD_RUNS = {
        ('hyper-test-typescript.yml', 'test'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Build the TypeScript (napi-rs) addon': '515b3f603dbe7a6e',
            'glibc the TypeScript addon references (printed, never fails)': '20a027e529cef154',
            'Test TypeScript bindings on Node': '2023031b4e8ebede',
            'Test TypeScript bindings on Bun': 'b4bf8cb9c39c3ee1'},
        ('hyper-test-typescript.yml', 'test-musl'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Node on Alpine — the declared floors': 'c0e4e6fcb88abe27'},
        ('hyper-test-typescript.yml', 'test-osx-x64'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Cross-build the TypeScript addon for x86_64': 'db400deedfeedc3c',
            'Test TypeScript bindings on x64 Node, under Rosetta': 'd1f3c723c01bd405'},
        ('hyper-lint-typescript.yml', 'lint'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'npm ci': '9db3f780def6105e', 'npm run lint': '326d9800e732eb9d',
            'cargo fmt --check': '194fabc0129ce4a9', 'cargo clippy': 'e89ad37e84720f8f'},
        ('hyper-build-wasm.yml', 'build-node-wasi'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Build the WASI module and its loaders': 'c7b681cada2e5d78',
            'The conformance suite against the WASI module, native refused': '249b08603e3c40f4',
            'Collect the module and its loaders': '3399c30676c03abb',
            'Pack the WASI package as the release does': '1c38a3a4e930f5fa',
            'The package bundled into a page, in headless Chrome': '7ea6a953339f3c55'},
    }

    def test_without_a_pin_the_npm_legs_run_heads_commands(self):
        import hashlib
        for (workflow, job_name), runs in self.HEAD_RUNS.items():
            with self.subTest(leg=f'{workflow}:{job_name}'):
                steps = yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][job_name]['steps']
                ungated = {s.get('name'): hashlib.sha256(s['run'].encode()).hexdigest()[:16] for s in steps
                           if 'run' in s and 'steps.companions.outputs.active' not in str(s.get('if', ''))}
                self.assertEqual(ungated, runs)

    # HEAD 666a621's commands in each Ruby and PHP leg, by step, as for npm above: without a pin,
    # every step but the fetch and the proofs gated on its `active` output (in ruby-magnus, on
    # the `companions` input it is passed) runs exactly these. The lint's Validate step came with
    # the Ruby wiring (Task 12); the PHP build's first three steps with the gating (Task 4).
    RUBY_PHP_HEAD_RUNS = {
        ('workflows/hyper-test-ruby.yml', 'test'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Point Xcode at a full toolchain (works around a Ruby native-extension mkmf issue)': 'a5ddfc440d5e810f',
            "Point bindgen at one system LLVM, not at Swift's clang": '6047d04ccc1a8ca6',
            'Place native lib for Ruby in-repo testing': '2bdcfa8bacea3e8a',
            "Test Ruby bindings (Fiddle backend) against this platform's native lib": '8415f389a494955f'},
        ('workflows/hyper-test-ruby.yml', 'test-musl'): {'Validate Forge workflow revision': 'b43ed1cffc80ac65'},
        ('workflows/hyper-build-wasm.yml', 'build-ruby-wasm'): {'Validate Forge workflow revision': 'b43ed1cffc80ac65'},
        ('actions/ruby-wasm-suite/action.yml', None): {'Ruby ${{ inputs.abi }} — ${{ inputs.stages }}': '7c5f2504abacd323'},
        ('workflows/hyper-lint-ruby.yml', 'lint'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'rake docs:check': '2fe7ba0d29cf77eb', 'rubocop': '9bd27eb7ae1f5167'},
        ('actions/ruby-magnus/action.yml', None): {
            'Install the CLANG64 toolchain (win-x64)': 'de8b4aff30f0b2de',
            'Drop a lockfile generated by an earlier Ruby ABI': '0ef8454245834733',
            'Build and stage the Magnus extension — Ruby ${{ inputs.ruby-version }}': '275b9dcc84c7fe5b',
            'Test Ruby bindings (Magnus backend) — Ruby ${{ inputs.ruby-version }}': 'f8e2d40e29bbb266'},
        ('workflows/hyper-build-php-extension.yml', 'build'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'The companions this run builds': '0dc39067430cedef', 'The mode this run is in': 'a464654c90805cac',
            'The companions built from the registry': '41daeff08859aa2c',
            'Fetch PIE, and verify its checksum and attestation': '398dfe2df8a8fa28',
            "The image's PHP is the leg's minor": '7a4946fd61efd2f0',
            'PHP from the shivammathur/php tap (macOS)': '92cb4f7678f45776', 'Composer (macOS)': '151159ac80bde2d6',
            "Point bindgen at one system LLVM, not at Swift's clang": '6047d04ccc1a8ca6',
            'Stub generator prerequisites (cargo-php, libclang)': '0e40ac69461a81d2',
            'Build the extension': 'dd1d34742ea30b31', 'Build the companion extensions': '95f6d0e396a42d0c',
            'Clippy over the extension (the php feature)': 'af851fc95df05132',
            "MSVC toolset of this minor's PHP build (Windows)": '4f0fd46af6f0d405',
            'Build the extension (Windows)': '8845d10eabab233b',
            'glibc floor — nothing newer than GLIBC_2.34 is referenced': '3509cbc69a7e1ae8',
            'Load-check the extension, and record the ABI it is pinned to': '005027588823a9f8',
            'Build, load-check, test and pack on Alpine': '7219ee47a7ffb641',
            "Install the suite's Composer dependencies": 'b632c22ee404ce9d',
            'Test the extension (composer test)': '6fdeb4480825562b', 'Pack the PIE zip': '02d67df990823c21'},
    }
    PROOF_GATES = ('steps.companions.outputs.active', 'steps.fetch.outputs.active', 'inputs.companions')

    def test_without_a_pin_the_ruby_and_php_legs_run_heads_commands(self):
        import hashlib
        for (path, job_name), runs in self.RUBY_PHP_HEAD_RUNS.items():
            with self.subTest(leg=f'{path}:{job_name}'):
                data = yaml_lite.load((WORKFLOWS.parent / path).read_text())
                steps = data['jobs'][job_name]['steps'] if job_name else data['runs']['steps']
                ungated = {s.get('name'): hashlib.sha256(s['run'].encode()).hexdigest()[:16] for s in steps
                           if 'run' in s and not any(g in str(s.get('if', '')) for g in self.PROOF_GATES)}
                self.assertEqual(ungated, runs)

    # HEAD 666a621's commands in each Java and C# leg, by step, as for npm above: without a pin,
    # every step but the fetch and the proofs gated on its `active` output runs exactly these.
    # The Blazor job's Validate step came with the C# wiring (Task 13), with the Forge checkout
    # the fetch needs.
    JVM_DOTNET_HEAD_RUNS = {
        ('hyper-test-java.yml', 'test'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Stage the core where a dev-loop build looks': '0fda8b8ce8153626',
            'Build the JNI library': '16a5a04fe2d76414', 'Place both libraries for the Java suites': '92d3295521c6d0ff',
            'Test Java (JNI) on the primary JDK and the floor': 'b23f2168055cf5e8',
            'Place native lib for Java in-repo testing': '663f3b398764f341',
            "Test Java bindings against this platform's native lib": 'd712536c8f777800'},
        ('hyper-test-java.yml', 'test-musl'): {'Validate Forge workflow revision': 'b43ed1cffc80ac65'},
        ('hyper-test-java.yml', 'test-osx-x64'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Cross-build the JNI library for x86_64': 'c4e2ef0fc7638d4b',
            'Place both libraries for the Java suites': 'e081616a7d48018f',
            'Test Java (JNI) on an x64 JDK, primary and floor': '798089259f35ec71'},
        ('hyper-test-csharp.yml', 'test'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Stage the core where a dev-loop build looks': '0fda8b8ce8153626',
            'Place native lib for C# in-repo testing': 'c040c04fec94695d',
            "Test C# bindings against this platform's native lib": 'a63e1a2395ae912b',
            'Native AOT publish (zero trim warnings required)': '0ef5ea810eba57a1',
            'Run the AOT-published binary (must exit 0)': 'e8d7857ee344a4c6'},
        ('hyper-test-csharp.yml', 'test-musl'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            "Resolve the Alpine image that follows the caller's SDK": 'b1fb95f9bb5a45dc'},
        ('hyper-build-wasm.yml', 'test-blazor'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'wasm-tools workload': '27fc66aef44e902f', 'wasm-tools-net10 workload': '8ba9d62e83f0ae57',
            'Blazor WebAssembly app in headless Chrome': '9d2713c7f17124eb'},
    }

    def test_without_a_pin_the_java_and_csharp_legs_run_heads_commands(self):
        import hashlib
        for (workflow, job_name), runs in self.JVM_DOTNET_HEAD_RUNS.items():
            with self.subTest(leg=f'{workflow}:{job_name}'):
                steps = yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][job_name]['steps']
                ungated = {s.get('name'): hashlib.sha256(s['run'].encode()).hexdigest()[:16] for s in steps
                           if 'run' in s and 'steps.companions.outputs.active' not in str(s.get('if', ''))}
                self.assertEqual(ungated, runs)

    def test_java_legs_set_up_the_jdk_first_and_prove_the_origin_right_before_each_suite(self):
        # The wiring runs the pin's Gradle (publishToMavenLocal): every JDK the job sets up comes
        # before the fetch. The proof runs Gradle in the suite's own build, right before the suite
        # (whose Test tasks depend on companionOrigin too).
        PROOF = '"$FETCH_PYTHON" .hyperforge/.github/scripts/companion_origin.py java --root . --dir java --installed'
        for job_name, suites in (('test', {'Test Java (JNI) on the primary JDK and the floor': 'inputs.java_jni && ',
                                           "Test Java bindings against this platform's native lib": '${{ !inputs.java_jni && '}),
                                 ('test-osx-x64', {'Test Java (JNI) on an x64 JDK, primary and floor': ''}),
                                 ('test-musl', {})):
            with self.subTest(job=job_name):
                steps = yaml_lite.load((WORKFLOWS / 'hyper-test-java.yml').read_text())['jobs'][job_name]['steps']
                fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
                self.assertEqual(steps[fetch]['id'], 'companions')
                jdks = [i for i, s in enumerate(steps) if str(s.get('uses', '')).startswith('actions/setup-java@')]
                self.assertTrue(jdks)
                self.assertLess(max(jdks), fetch)
                for suite, condition in suites.items():
                    at = self._index(steps, suite)
                    proof = steps[at - 1]
                    self.assertEqual(proof['name'], 'The suite takes the pinned companion')
                    self.assertEqual(proof['if'], f"{condition}steps.companions.outputs.active == 'true'"
                                     + (' }}' if condition.startswith('${{') else ''))
                    self.assertEqual(proof['run'].strip(), PROOF)
                    self.assertEqual(proof.get('working-directory'), None)

    def test_comments_stay_on_the_steps_they_document(self):
        # A proof step goes in above the comment of the step it precedes, never between the two.
        text = (WORKFLOWS / 'hyper-test-csharp.yml').read_text()
        for comment, step in (
                ('      # single-TFM project loads either way.\n',
                 "      - name: Test C# bindings against this platform's native lib\n"),
                ('      # is identical to MSBuild and left alone everywhere.\n',
                 '      - name: Native AOT publish (zero trim warnings required)\n'),
                ("      # folder's copy proven to be the feed's, byte for byte, right before the suite.\n",
                 '      - name: The suite takes the pinned companion\n'),
                ('      # proven the same way, right before the publish.\n',
                 '      - name: The Native AOT publish takes the pinned companion\n')):
            with self.subTest(step=step.strip()):
                self.assertIn(comment + step, text)
        # The fetch goes in above the comments of the steps it precedes in hyper-core.yml too.
        core = (WORKFLOWS / 'hyper-core.yml').read_text()
        for comment, step in (
                ('      # builds the library without std, abort-on-panic, output in target/release/.\n',
                 '      - name: Build native core (release)\n'),
                ('      # the failure is "installing Rosetta" rather than a test binary that will not start.\n',
                 '      - name: Make sure Rosetta 2 is there to run the x86_64 tests\n')):
            with self.subTest(step=step.strip()):
                self.assertIn(comment + step, core)
        self.assertNotIn('\n\n\n', core)

    def test_csharp_legs_set_up_dotnet_first_and_prove_the_origin_after_each_restore(self):
        steps = yaml_lite.load((WORKFLOWS / 'hyper-test-csharp.yml').read_text())['jobs']['test']['steps']
        fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
        self.assertEqual(steps[fetch]['id'], 'companions')
        self.assertLess(self._index(steps, 'actions/setup-dotnet@a98b56852c35b8e3190ac28c8c2271da59106c68'), fetch)
        # The suite: its project restored, then proven, right before `dotnet test`.
        suite = self._index(steps, "Test C# bindings against this platform's native lib")
        proof = steps[suite - 1]
        self.assertEqual(proof['name'], 'The suite takes the pinned companion')
        self.assertEqual(proof['if'], "steps.companions.outputs.active == 'true'")
        at = 0
        for needle in ('dotnet restore "$GITHUB_WORKSPACE/$CSHARP_TEST_PROJECT"',
                       '"$FETCH_PYTHON" .hyperforge/.github/scripts/companion_origin.py csharp --root . --installed'):
            at = proof['run'].index(needle, at) + len(needle)
        # Native AOT: restored for the RID, as the publish restores, then proven, right before it.
        publish = self._index(steps, 'Native AOT publish (zero trim warnings required)')
        proof = steps[publish - 1]
        self.assertEqual(proof['name'], 'The Native AOT publish takes the pinned companion')
        self.assertEqual(proof['if'], "inputs.csharp_aot_project != '' && steps.companions.outputs.active == 'true'")
        at = 0
        for needle in ('dotnet restore "$AOT_PROJECT" -r "$RID" -p:PublishAot=true',
                       '"$FETCH_PYTHON" .hyperforge/.github/scripts/companion_origin.py csharp --root . --dir "$(dirname "$AOT_PROJECT")" --installed'):
            at = proof['run'].index(needle, at) + len(needle)
        # The musl leg packs on the runner: the SDK first.
        musl = yaml_lite.load((WORKFLOWS / 'hyper-test-csharp.yml').read_text())['jobs']['test-musl']['steps']
        fetch = next(i for i, s in enumerate(musl) if s.get('uses') == FETCH_USES)
        self.assertLess(self._index(musl, 'actions/setup-dotnet@a98b56852c35b8e3190ac28c8c2271da59106c68'), fetch)
        # Blazor: the check's own project restored and proven right before the check.
        blazor = yaml_lite.load((WORKFLOWS / 'hyper-build-wasm.yml').read_text())['jobs']['test-blazor']['steps']
        fetch = next(i for i, s in enumerate(blazor) if s.get('uses') == FETCH_USES)
        self.assertEqual(blazor[fetch]['id'], 'companions')
        self.assertEqual(blazor[fetch]['with']['leg'], 'hyper-build-wasm.yml:test-blazor')
        self.assertLess(self._index(blazor, 'The .NET SDKs'), fetch)
        check = self._index(blazor, 'Blazor WebAssembly app in headless Chrome')
        proof = blazor[check - 1]
        self.assertEqual(proof['if'], "steps.companions.outputs.active == 'true'")
        at = 0
        for needle in ('dotnet restore "$(dirname "$BLAZOR_CHECK")"',
                       'companion_origin.py csharp --root . --dir "$(dirname "$BLAZOR_CHECK")" --installed'):
            at = proof['run'].index(needle, at) + len(needle)

    def test_musl_java_and_csharp_branches_wire_and_prove_before_each_suite(self):
        text = (ACTIONS / 'musl-suite' / 'suite-musl.sh').read_text()
        for branch, needles in (
                ('java', ('extra_env=(-v "$forge_scripts:/forge:ro")', 'cp -r /src/java /work/java', '. /companion.sh',
                          'companion_java /src /work /forge wire', 'companion_java /src /work /forge origin /work/java',
                          './gradlew test :aot-smoke-test:run')),
                ('java-jni', ('extra_env=(-v "$forge_scripts:/forge:ro")', 'cp -r /src/java /work/java', '. /companion.sh',
                              'companion_java /src /work /forge wire', 'companion_java /src /work /forge origin /work/java',
                              './gradlew test testFloor checkSuiteParity')),
                ('csharp', ('csharp global.json LICENSE) | tar -C /work -xf -', '. /companion.sh',
                            'companion_csharp /src /work wire',
                            'companion_csharp /src /work origin "/work/$TEST_PROJECT"', 'dotnet test --project',
                            'companion_csharp /src /work origin "$AOT_PROJECT" -r "$RID" -p:PublishAot=true',
                            'dotnet publish "$AOT_PROJECT"'))):
            with self.subTest(branch=branch):
                body = text[text.index(f'\n  {branch})\n'):]
                body = body[:body.index('\n    ;;\n')]
                at = 0
                for needle in needles:
                    found = body.find(needle, at)
                    self.assertGreaterEqual(found, 0, f'{needle!r} not found after position {at}')
                    at = found + len(needle)

    # suite-ruby-wasm.sh at HEAD: what is left of it once its companion-pins regions go.
    SUITE_RUBY_WASM_HEAD = '03ae0a0ecaf6c18625072a1b4239c2d3c1ef530442e76d8fe1cb6efeae13d370'
    REGION = re.compile(r'^[ \t]*# >>> companion-pins\n.*?^[ \t]*# <<< companion-pins\n', re.M | re.S)

    def test_without_a_pin_suite_ruby_wasm_is_heads(self):
        import hashlib
        text = (ACTIONS / 'ruby-wasm-suite' / 'suite-ruby-wasm.sh').read_text()
        regions = self.REGION.findall(text)
        self.assertEqual(len(regions), 3)
        self.assertEqual(hashlib.sha256(self.REGION.sub('', text).encode()).hexdigest(), self.SUITE_RUBY_WASM_HEAD)
        # Every region does nothing without .companions/env: a definition, or a call guarded on
        # the file (companion_wasm returns at once without it).
        for region in regions:
            body = [l.strip() for l in region.splitlines() if l.strip() and not l.strip().startswith('#')]
            self.assertTrue(body[0].startswith(('companions=', 'companion_wasm', 'if [ -f "$companions" ]')), body[0])
        self.assertIn('[ -f "$companions" ] || return 0', regions[0])

    def test_ruby_wasm_proves_the_pinned_gem_between_install_and_rbwasm(self):
        steps = yaml_lite.load((WORKFLOWS / 'hyper-build-wasm.yml').read_text())['jobs']['build-ruby-wasm']['steps']
        fetch = next(s for s in steps if s.get('uses') == FETCH_USES)
        self.assertEqual(fetch['with']['leg'], 'hyper-build-wasm.yml:build-ruby-wasm:abi=${{ matrix.abi }}')
        self.assertNotIn('RubyGems', ' '.join(str(s.get('name', '')) for s in steps))
        text = (ACTIONS / 'ruby-wasm-suite' / 'suite-ruby-wasm.sh').read_text()
        link = text[text.index('\nlink() {'):text.index('\n}\n', text.index('\nlink() {'))]
        at = 0
        for needle in ('companion_wasm\n', 'bundle install\n',
                       'python3 "$forge_scripts/companion_origin.py" ruby-wasm --root "$root" --dir "${SMOKE:-ruby/wasm-smoke}"',
                       'bundle exec rbwasm build'):
            found = link.find(needle, at)
            self.assertGreaterEqual(found, 0, f'{needle!r} not found in link() after position {at}')
            at = found + len(needle)
        wasm = text[text.index('\ncompanion_wasm() {'):text.index('\n}\n', text.index('\ncompanion_wasm() {'))]
        at = 0
        for needle in ('bundle exec rake "wasm:gem[$abi]"', 'gem unpack', 'gem spec'):
            found = wasm.find(needle, at)
            self.assertGreaterEqual(found, 0, f'{needle!r} not found in companion_wasm() after position {at}')
            at = found + len(needle)

    @staticmethod
    def _index(steps, name):
        return next(i for i, s in enumerate(steps) if (s.get('name') or s.get('uses')) == name)

    def test_ruby_legs_prove_the_origin_right_before_each_suite(self):
        # Bundler installs in setup-ruby (bundler-cache), so the proof follows the last
        # setup-ruby of the Ruby the suite runs on and is the step right before the suite.
        PROOF = re.compile(r'companion_origin\.py"? ruby --root \. --dir ruby --installed$')
        test = yaml_lite.load((WORKFLOWS / 'hyper-test-ruby.yml').read_text())['jobs']['test']['steps']
        fetch = next(s for s in test if s.get('uses') == FETCH_USES)
        self.assertEqual(fetch['id'], 'companions')
        magnus = [s for s in test if str(s.get('uses', '')).endswith('/ruby-magnus')]
        self.assertEqual(len(magnus), 2)
        for step in magnus:
            self.assertEqual(step['with']['companions'], '${{ steps.companions.outputs.active }}', step['name'])
        fiddle = self._index(test, "Test Ruby bindings (Fiddle backend) against this platform's native lib")
        proof = test[fiddle - 1]
        self.assertEqual(proof['name'], 'The Fiddle suite takes the pinned companion')
        self.assertEqual(proof['if'], "steps.companions.outputs.active == 'true'")
        self.assertRegex(proof['run'], PROOF)
        setups = [i for i, s in enumerate(test) if str(s.get('uses', '')).startswith('ruby/setup-ruby@')
                  or str(s.get('uses', '')).endswith('/ruby-magnus')]
        self.assertLess(max(setups), fiddle - 1)
        # ruby-magnus: after its setup-ruby, right before its rspec, on the input the job passes.
        action = yaml_lite.load((ACTIONS / 'ruby-magnus' / 'action.yml').read_text())
        self.assertEqual(action['inputs']['companions']['default'], 'false')
        steps = action['runs']['steps']
        rspec = self._index(steps, 'Test Ruby bindings (Magnus backend) — Ruby ${{ inputs.ruby-version }}')
        proof = steps[rspec - 1]
        self.assertEqual(proof['if'], "inputs.companions == 'true'")
        self.assertRegex(proof['run'], PROOF)
        self.assertLess(self._index(steps, 'ruby/setup-ruby@7f797815d8adceb1c412e7abefdd1ab4abc47a5f'), rspec - 1)
        # The lint: after setup-ruby's install, before anything runs under Bundler.
        lint = yaml_lite.load((WORKFLOWS / 'hyper-lint-ruby.yml').read_text())['jobs']['lint']['steps']
        proof = self._index(lint, 'Bundler took the pinned companion')
        self.assertEqual(lint[proof]['if'], "inputs.lint_ruby && steps.companions.outputs.active == 'true'")
        self.assertRegex(lint[proof]['run'], PROOF)
        self.assertTrue(str(lint[proof - 1].get('uses', '')).startswith('ruby/setup-ruby@'))
        self.assertLess(proof, self._index(lint, 'rake docs:check'))

    def test_php_legs_prove_the_origin_right_before_the_suite(self):
        steps = yaml_lite.load((WORKFLOWS / 'hyper-build-php-extension.yml').read_text())['jobs']['build']['steps']
        fetch = next(s for s in steps if s.get('uses') == FETCH_USES)
        self.assertEqual(fetch['id'], 'fetch')
        suite = self._index(steps, 'Test the extension (composer test)')
        proof = steps[suite - 1]
        self.assertEqual(proof['name'], 'The suite takes the pinned companion')
        self.assertEqual(proof['if'], "matrix.kind != 'musl' && steps.fetch.outputs.active == 'true'")
        self.assertIn('companion_origin.py" php --root . --installed', proof['run'])
        self.assertEqual(steps[suite - 2]['name'], "Install the suite's Composer dependencies")
        # The registry build stays off in companion mode with a block: the pin is the extension.
        registry = steps[self._index(steps, 'Build the companion extensions')]
        self.assertIn("steps.mode.outputs.companion_mode == 'companion'", registry['if'])
        # The musl legs: in the suite container, after composer install, before the suite.
        text = (WORKFLOWS.parent / 'scripts' / 'php_ext_musl.sh').read_text()
        at = 0
        for needle in ('composer install --no-interaction --prefer-dist --no-progress',
                       'python3 /forge/.github/scripts/companion_origin.py php --root /src --installed',
                       'php tools/run-tests.php'):
            at = text.index(needle, at) + len(needle)
        self.assertIn('"${pinned[@]}"', text)
        self.assertIn('_PHP_EXTENSION=/src/.companions/$name-php/$name.so', text)

    def test_ruby_containers_put_the_pin_on_rubylib_and_prove_it_before_rspec(self):
        LOOP = 'runtime_dependencies.each { |d| next if d.name == ENV[%q(HYPER_COMPANION_GEM)]; system('
        github = WORKFLOWS.parent
        for script in ('actions/musl-suite/suite-musl.sh', 'actions/ruby-magnus-musl/build-magnus-musl.sh'):
            with self.subTest(script=script):
                text = (github / script).read_text()
                if script.startswith('actions/musl-suite'):
                    text = text[text.index('\n  ruby)\n'):]
                at = 0
                for needle in ('. /companion.sh', 'companion_ruby /src /work /forge wire', 'gem install rspec',
                               LOOP, 'companion_ruby /src /work /forge origin', 'rspec\n'):
                    found = text.find(needle, at)
                    self.assertGreaterEqual(found, 0, f'{needle!r} not found after position {at}')
                    at = found + len(needle)
                self.assertNotIn('gem install', text[text.index('companion_ruby /src /work /forge origin'):
                                                     text.index('rspec\n', at - len('rspec\n'))])
        self.assertIn('-v "$(dirname "$companion_sh"):/forge:ro"',
                      (github / 'actions/ruby-magnus-musl/build-magnus-musl.sh').read_text())

    def test_container_helpers_are_reached_by_a_fetch(self):
        # A container helper does nothing without .companions/env, which only the fetch writes:
        # every job running one (a legacy workflow's aside) fetches first, or the helper never fires.
        import companion_map
        helpers = ('/musl-native', '/ruby-magnus-musl')
        found = set()
        for workflow in sorted(WORKFLOWS.glob('hyper-*.yml')):
            if workflow.name in companion_map.LEGACY_WORKFLOWS:
                continue
            for job_name, job in (yaml_lite.load(workflow.read_text()).get('jobs') or {}).items():
                steps = job.get('steps') or []
                for i, step in enumerate(steps):
                    if str(step.get('uses', '')).endswith(helpers) or 'php_ext_musl.sh' in str(step.get('run', '')):
                        found.add(f'{workflow.name}:{job_name}')
                        with self.subTest(job=f'{workflow.name}:{job_name}', step=step.get('name')):
                            self.assertTrue(any(s.get('uses') == FETCH_USES for s in steps[:i]))
        self.assertEqual(found, {'hyper-core.yml:core-musl', 'hyper-test-java.yml:test-musl',
                                 'hyper-test-ruby.yml:test-musl', 'hyper-build-php-extension.yml:build'})
        # suite-musl.sh's Node branch, through musl-suite and the floors' direct call.
        for job_name in ('test-musl',):
            steps = yaml_lite.load((WORKFLOWS / 'hyper-test-typescript.yml').read_text())['jobs'][job_name]['steps']
            fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
            for i, step in enumerate(steps):
                if str(step.get('uses', '')).endswith('/musl-suite') or 'suite-musl.sh' in str(step.get('run', '')):
                    self.assertLess(fetch, i, step.get('name'))

    def test_container_scripts_wire_the_companion_before_building(self):
        github = WORKFLOWS.parent
        for script, call, build in (
                ('actions/musl-native/build-musl.sh',
                 'companion_cargo /src /work /work/rust $([ "$JNI" = true ] && echo /work/java/jni)', 'cargo cdylib'),
                ('actions/ruby-magnus-musl/build-magnus-musl.sh', 'companion_cargo /src /work /work/rust',
                 'cargo rustc --release'),
                ('scripts/php_ext_musl.sh', 'companion_cargo /src /work /work/rust', 'cargo php-ext'),
                ('actions/musl-suite/suite-musl.sh', 'companion_go /src /work /work/go', 'go test -count=1'),
                ('actions/musl-suite/suite-musl.sh', 'companion_cargo /src /work /work/rust\n      cd /work/python',
                 'maturin build'),
                ('actions/musl-suite/suite-musl.sh', 'companion_python /src /forge python3 wire',
                 'pip install -q --root-user-action=ignore pytest /wheels/*.whl'),
                ('actions/musl-suite/suite-musl.sh', 'companion_python /src /forge python3 origin', 'pytest -q'),
                ('actions/musl-suite/suite-musl.sh', 'companion_cargo /src /work /work/rust /work/typescript',
                 'npm run build && npm test'),
                ('actions/musl-suite/suite-musl.sh', 'companion_npm /src /work /forge /work/typescript wire',
                 'npm ci || exit 1')):
            with self.subTest(script=script):
                text = (github / script).read_text()
                self.assertEqual(text.count(call), 1)
                self.assertLess(text.index(call), text.index(build))
                self.assertRegex(text, r'\. /(companion\.sh|forge/\.github/scripts/companion_container\.sh)\n')
                if 'php_ext' not in script:
                    self.assertIn('-v "$companion_sh:/companion.sh:ro"', text)
                    self.assertIn('/../../scripts" && pwd)/companion_container.sh', text)

    def test_cache_keys_never_cross_modes(self):
        # Every cache a fetching job can restore or save (its own steps', and those of the local
        # actions it calls) either carries the pin in its key, by KEYED, after the fetch that
        # computes it and with no fallback restore key a pin-less key prefixes; or is off; or
        # holds no package at all (TOOLCHAIN_CACHES); or is content-addressed, so the pin's bytes
        # can never answer for the registry's (CONTENT_ADDRESSED). The classification is closed:
        # every other action a fetching job runs is in NO_CACHE, with why and, where it could
        # cache, the condition under which it does not; anything unlisted fails.
        seen = {}
        for path, job_name, steps, fetch_id, fetch in fetching_steps():
            for i, step, inputs in steps:
                uses = str(step.get('uses', ''))
                if not uses or uses.startswith('./'):
                    continue
                kind = uses.split('@')[0]
                where = f'{path.name}:{job_name}:{step.get("name") or kind}'
                seen.setdefault(kind, []).append(where)
                with self.subTest(step=where):
                    w = {k: resolve_inputs(v, inputs) for k, v in (step.get('with') or {}).items()}
                    if kind in NO_CACHE:
                        reason, holds = NO_CACHE[kind]
                        self.assertTrue(holds(w), f'{kind}: {reason}, which {w} no longer holds')
                        self.assertNotIn(kind, (*KEYED_KINDS, *CONTENT_ADDRESSED, *CACHE_OFF))
                    elif kind == SEMVER:
                        # Its key, `<prefix-key or "semver">-<job>-…`, is also its restore key: the
                        # pin's prefix shares nothing with the pin-less "semver".
                        self.assert_keyed(w.get('prefix-key', ''), fetch_id, i, fetch, '')
                        pinned = keyed_value(w['prefix-key'], True, 'a' * 40)
                        self.assertFalse(pinned.startswith('semver') or 'semver-'.startswith(pinned), pinned)
                    elif kind in ('actions/cache', 'actions/cache/restore', 'actions/cache/save'):
                        if w.get('path') in TOOLCHAIN_CACHES:
                            continue
                        self.assertNotIn('restore-keys', w, 'a fallback restore key would cross pins')
                        self.assert_keyed(w['key'], fetch_id, i, fetch, '')
                    elif kind == 'r-lib/actions/setup-r-dependencies':
                        if str(w.get('cache', 'true')).lower() == 'false':
                            continue
                        # Its restore key is `<os>-<r>-<arch>-<cache-version>-`: neither mode's
                        # (nor another pin's) may prefix the other's.
                        self.assert_keyed(w.get('cache-version', ''), fetch_id, i, fetch, '1', prefix_free=True)
                    elif kind == 'ruby/setup-ruby':
                        if str(w.get('bundler-cache', 'false')).lower() != 'true':
                            continue
                        # Its key gains `-v-<cache-version>` before the lockfile's path, its restore
                        # key ends at that path: '0' (the default) adds nothing.
                        self.assert_keyed(w.get('cache-version', ''), fetch_id, i, fetch, '0')
                    elif kind == 'actions/setup-node':
                        # Only the automatic cache (CONTENT_ADDRESSED); a `cache:` input names another.
                        self.assertNotIn('cache', w, CONTENT_ADDRESSED[kind])
                    elif kind in CONTENT_ADDRESSED:
                        pass
                    elif kind in CACHE_OFF:
                        self.assertIn(str(w.get('cache', '')).lower(), ('', 'false'),
                                      f'{kind} would cache the companion in companion mode')
                    else:
                        self.fail(f'{kind}: an action this test does not know; key its cache with the pin, '
                                  'turn it off, or list it in NO_CACHE with why it caches nothing')
        # The caches the decision covers, so a renamed action or a moved step fails here.
        self.assertEqual({k: len(v) for k, v in seen.items() if k in KEYED_KINDS},
                         {'actions/cache': 3, 'actions/cache/restore': 1, 'actions/cache/save': 1,
                          'r-lib/actions/setup-r-dependencies': 8, 'ruby/setup-ruby': 5, SEMVER: 1})
        # Every NO_CACHE entry is still in use, so the table cannot hide a stale reason.
        self.assertLessEqual(set(NO_CACHE), set(seen))

    def assert_keyed(self, value, fetch_id, index, fetch, default, prefix_free=False):
        self.assertTrue(KEYED.search(value), f'{value!r}: not keyed with the pin ({KEYED_FORM})')
        self.assertEqual({m.group('id') for m in KEYED.finditer(value)}, {fetch_id})
        self.assertLess(fetch, index, 'keyed on the fetch, so after it')
        off, one, two = (keyed_value(value, False, ''), keyed_value(value, True, 'a' * 40),
                         keyed_value(value, True, 'b' * 40))
        self.assertEqual(len({off, one, two}), 3)
        self.assertIn('a' * 40, one)
        if default:
            self.assertEqual(keyed_value(value, False, ''), default)
        if prefix_free:
            for x, y in ((off, one), (one, off), (one, two)):
                self.assertFalse(f'{y}-'.startswith(f'{x}-'), (x, y))

    def test_without_a_pin_every_cache_is_heads(self):
        # HEAD 666a621's settings, by step: without a pin each keyed expression reduces to the
        # action's default (R's '1', Ruby's '0') or to nothing, and the key is HEAD's byte for byte.
        defaults = {'r-lib/actions/setup-r-dependencies': {'cache-version': '1', 'install-pandoc': PANDOC},
                    'ruby/setup-ruby': {'cache-version': '0'}, './.hyperforge/.github/actions/ruby-magnus':
                    {'cache-version': '0'}, SEMVER: {'prefix-key': ''}}
        found = {}
        for path, job_name, steps, _, _ in fetching_steps(local=False):
            for _, step, _ in steps:
                uses = str(step.get('uses', '')).split('@')[0]
                if not any(KEYED.search(str(v)) for v in (step.get('with') or {}).values()):
                    continue
                if RWorkflowTests.RELEASE in str(step.get('if', '')):
                    continue  # the release-mode twins: RWorkflowTests holds them to their HEAD step
                reduced = {}
                for key, value in step['with'].items():
                    value = KEYED.sub(lambda m: m.group('default'), str(value)) if isinstance(value, str) else value
                    if defaults.get(uses, {}).get(key) != value:
                        reduced[key] = value
                found.setdefault((path.name, job_name, uses), []).append(reduced)
        action = yaml_lite.load((ACTIONS / 'ruby-magnus' / 'action.yml').read_text())
        setup = next(s for s in action['runs']['steps'] if str(s.get('uses', '')).startswith('ruby/setup-ruby@'))
        self.assertEqual(action['inputs']['cache-version']['default'], '0')
        self.assertEqual(setup['with'], {**HEAD_CACHE_WITH[('ruby-magnus', 'action', 'ruby/setup-ruby')][0],
                                         'cache-version': '${{ inputs.cache-version }}'})
        self.assertEqual(found, {k: v for k, v in HEAD_CACHE_WITH.items() if k[0] != 'ruby-magnus'})

    def test_every_isolated_environment_reads_companion_env(self):
        # The spec's table of environments a host-side override cannot reach, by entry point,
        # with every script in the Forge that starts a container, a venv elsewhere, or an
        # interpreter the job builds afresh: each reads .companions/env itself, or sources
        # companion_container.sh, whose every helper does.
        github = WORKFLOWS.parent
        helper = (SCRIPTS / 'companion_container.sh').read_text()
        self.assertIn('.companions/env', helper)
        for function in re.findall(r'^(companion_(?:cargo|go|python|ruby|java|csharp|npm|swift|c))\(\)', helper, re.M):
            with self.subTest(helper=function):
                body = helper[helper.index(f'{function}() {{'):]
                body = body[:body.index('\n}\n')]
                self.assertTrue(re.search(r'\.companions/env|companion_record ', body), f'{function} never reads it')
        guard = (github / 'actions/pyodide-suite/companion-guard.mjs').read_text()
        self.assertIn('".companions",\n    "env"', guard)
        for script in ISOLATED_ENVIRONMENTS:
            with self.subTest(script=script):
                text = (github / script).read_text()
                self.assertTrue('.companions/env' in text or 'companion_container.sh' in text
                                or ('from "./companion-guard.mjs"' in text and '\nrequirePinnedSums();\n' in text),
                                f'{script} never reads .companions/env')
        # Every script beside an action or among the Forge's scripts that runs a container is listed.
        starts = re.compile(r'\bdocker run\b|\bpodman run\b')
        runners = {p.relative_to(github).as_posix() for p in [*github.glob('actions/*/*'), *SCRIPTS.glob('*')]
                   if p.is_file() and p.suffix in ('.sh', '.mjs', '.py', '.rb') and not p.name.startswith('test_')
                   and starts.search(p.read_text(errors='replace'))}
        self.assertLessEqual(runners, set(ISOLATED_ENVIRONMENTS))
        # The spec's "sdist and fallthrough venvs" and every inline container a workflow starts:
        # each step that starts one (or installs into a fresh venv) in a WORKFLOW_ENVIRONMENTS job
        # carries that job's wiring; any other sources companion_container.sh, or is exempt by
        # name, with why.
        builds = re.compile(r'\bdocker run\b|\bpodman run\b|\bin_image\b|/v/bin/pip" install')
        found, exempt = set(), set()
        for workflow in sorted(WORKFLOWS.glob('*.yml')):
            for job_name, job in (yaml_lite.load(workflow.read_text()).get('jobs') or {}).items():
                for step in job.get('steps') or []:
                    run = str(step.get('run', ''))
                    if not builds.search(run):
                        continue
                    key = (workflow.name, job_name)
                    with self.subTest(step=f'{workflow.name}:{job_name}:{step.get("name")}'):
                        if key in WORKFLOW_ENVIRONMENTS:
                            found.add(key)
                            self.assertIn(WORKFLOW_ENVIRONMENTS[key], run)
                        elif 'companion_container.sh' in run:
                            pass  # wired inside, as the C smokes' pinned twins are
                        else:
                            exempt.add((*key, step.get('name')))
        self.assertEqual(found, set(WORKFLOW_ENVIRONMENTS))
        self.assertEqual(exempt, set(INLINE_CONTAINERS_EXEMPT))
        # The npm consumer tests (Verdaccio) are the companion's own script, typescript/consumer-
        # tests/run.sh, which the Forge never runs: nothing of it here to hold.
        self.assertEqual(sorted(github.rglob('consumer-tests')), [])

    def test_pyodide_suites_refuse_a_pin_they_were_not_given(self):
        # A companion-mode leg whose .companions/env names a pinned Pyodide wheel, calling the
        # suite without its SHA256SUMS: micropip would resolve the companion from PyPI.
        import subprocess
        import tempfile
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not installed')
        for script in ('suite-pyodide.mjs', 'browser-pyodide.mjs'):
            for given in (False, True):
                with self.subTest(script=script, sums=given), tempfile.TemporaryDirectory() as tmp:
                    tmp = Path(tmp)
                    work = tmp / 'work'
                    for package, body in (('pyodide', 'export async function loadPyodide() { throw new Error("pyodide loaded"); }'),
                                          ('puppeteer-core', 'export default { launch() { throw new Error("chrome launched"); } };')):
                        (work / 'node_modules' / package).mkdir(parents=True)
                        (work / 'node_modules' / package / 'package.json').write_text(
                            json.dumps({'name': package, 'type': 'module', 'main': 'index.js'}))
                        (work / 'node_modules' / package / 'index.js').write_text(body)
                    shutil.copy(ACTIONS / 'pyodide-suite' / script, work / script)
                    shutil.copy(ACTIONS / 'pyodide-suite' / 'companion-guard.mjs', work / 'companion-guard.mjs')
                    wheel = 'hypercast-1.0.0-cp312-abi3-pyemscripten_2025_0_wasm32.whl'
                    artifacts = tmp / 'ws' / '.companions' / 'hypercast-artifacts' / 'pypi-wheels-pyodide'
                    artifacts.mkdir(parents=True)
                    (artifacts / wheel).write_bytes(b'pinned')
                    (tmp / 'ws' / '.companions' / 'env').write_text(
                        f'HYPERCAST_PYODIDE_REQ=.companions/hypercast-artifacts/pypi-wheels-pyodide/{wheel}\n')
                    deps = tmp / 'deps'
                    deps.mkdir()
                    shutil.copy(artifacts / wheel, deps / wheel)
                    sums = tmp / 'SHA256SUMS'
                    sums.write_text('a2b4' * 16 + f'  pypi-wheels-pyodide/{wheel}\n')
                    (tmp / 'tests').mkdir()
                    (tmp / 'own.whl').write_bytes(b'x')
                    # The consumer checked out under ws/ (fetch-companions' root), not at the workspace.
                    env = {'PATH': os.environ['PATH'], 'GITHUB_WORKSPACE': str(tmp), 'PYODIDE_SUITE_COMPANIONS': 'ws',
                           'PYODIDE_SUITE_DEPS': str(deps) if given else '',
                           'PYODIDE_SUITE_SUMS': str(sums) if given else ''}
                    result = subprocess.run([node, script, 'tabular', '1.0.0', str(tmp / 'own.whl'), str(tmp / 'tests')],
                                            cwd=work, env=env, capture_output=True, text=True, timeout=60)
                    self.assertNotEqual(result.returncode, 0)
                    out = result.stdout + result.stderr
                    if given:
                        # Past the guard, to the stand-in Pyodide (or the browser pass's check
                        # that the Node pass ran first).
                        self.assertNotIn('PYODIDE_SUITE_SUMS', out)
                        self.assertIn('pyodide loaded' if script == 'suite-pyodide.mjs'
                                      else 'micropip is not cached', out)
                    else:
                        self.assertIn(f'.companions/env pins {wheel} but PYODIDE_SUITE_SUMS is not set', out)
                        self.assertNotIn('pyodide loaded', out)
                        self.assertNotIn('chrome launched', out)

    def test_both_pyodide_passes_share_one_guard(self):
        # The .companions/env guard, the SHA256SUMS parser and the micropip assertion live once,
        # in companion-guard.mjs, which action.yml copies beside the two scripts.
        directory = ACTIONS / 'pyodide-suite'
        imported = 'import { PINNED_INSTALLED, distName, readSums, requirePinnedSums } from "./companion-guard.mjs";'
        for script in ('suite-pyodide.mjs', 'browser-pyodide.mjs'):
            with self.subTest(script=script):
                text = (directory / script).read_text()
                self.assertIn(imported, text)
                for copied in ('_PYODIDE_REQ', '[0-9a-f]{64}', 'micropip.list()', '.replace(/[-_.]+/g'):
                    self.assertNotIn(copied, text)
        copy = next(s for s in yaml_lite.load((directory / 'action.yml').read_text())['runs']['steps']
                    if 'suite-pyodide.mjs" ' in str(s.get('run', '')))
        self.assertIn('"$GITHUB_ACTION_PATH/companion-guard.mjs" "$work/"', copy['run'])

    def test_pyodide_suite_looks_where_the_fetch_wrote(self):
        # Each call's companions_root is its job's fetch root, so the guard reads the env file the
        # fetch wrote; both passes take it.
        action = yaml_lite.load((ACTIONS / 'pyodide-suite' / 'action.yml').read_text())
        self.assertEqual(action['inputs']['companions_root']['default'], '.')
        passes = [s for s in action['runs']['steps'] if 'node ' in str(s.get('run', ''))]
        self.assertEqual(len(passes), 2)
        for step in passes:
            self.assertEqual(step['env']['PYODIDE_SUITE_COMPANIONS'], '${{ inputs.companions_root }}')
        calls = 0
        for path in sorted(WORKFLOWS.glob('*.yml')):
            for job_name, job in (yaml_lite.load(path.read_text()).get('jobs') or {}).items():
                steps = job.get('steps') or []
                for step in steps:
                    if str(step.get('uses', '')).endswith('/pyodide-suite'):
                        calls += 1
                        fetch = next(s for s in steps if s.get('uses') == FETCH_USES)
                        self.assertEqual((step.get('with') or {}).get('companions_root', '.'),
                                         (fetch.get('with') or {}).get('root', '.'), f'{path.name}:{job_name}')
        self.assertEqual(calls, 1)

    def test_every_board_workflow_runs_fetch_companions_first(self):
        # In every board workflow (hyper-core*, hyper-build-*, hyper-lint-*, hyper-test-*), each job
        # that fetches the companions does so before its first step that resolves anything the
        # companion could be (BOARD_RESOLVES: a package manager, a build tool reading a manifest,
        # an install-and-cache action); a toolchain's install may come first, and the fetch's own
        # wiring is the fetch. A job with no fetch resolves nothing of the companion's (NO_FETCH).
        import companion_map
        boards = [p for glob in companion_map.PRODUCER_GLOBS for p in sorted(WORKFLOWS.glob(glob))]
        checked, unfetched = set(), set()
        for workflow in boards:
            for job_name, job in (yaml_lite.load(workflow.read_text()).get('jobs') or {}).items():
                steps = job.get('steps') or []
                if not steps:
                    continue
                where = f'{workflow.name}:{job_name}'
                fetches = [i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES]
                resolving = [i for i, s in enumerate(steps) if board_resolves(s)
                             and (workflow.name, job_name, s.get('name')) not in NO_COMPANION_STEPS]
                with self.subTest(job=where):
                    if not fetches:
                        if resolving and workflow.name not in companion_map.LEGACY_WORKFLOWS:
                            unfetched.add(where)
                        continue
                    checked.add(where)
                    self.assertEqual(len(fetches), 1)
                    early = [steps[i].get('name') or steps[i].get('uses') for i in resolving if i < fetches[0]]
                    self.assertEqual(early, [], 'resolves before the fetch')
        self.assertEqual(unfetched, set(NO_FETCH))
        # Not a job-level `container:` job either (the spec's table names it so; Task 17 fixes that).
        lint_swift = yaml_lite.load((WORKFLOWS / 'hyper-lint-swift.yml').read_text())['jobs']['lint']
        self.assertNotIn('container', lint_swift)
        # Every wired leg, and the legacy static Swift workflow, which still fetches.
        self.assertEqual(checked, {f'{w}:{j}' for w, j in self.wired_legs()}
                         | {'hyper-test-swift-static.yml:test-swift-static'})


# Local composite actions a fetching job calls, whose steps run in that job.
LOCAL_ACTION = './.hyperforge/.github/actions/'


def fetching_steps(local=True):
    """(workflow path, job, [(index, step, inputs)], fetch id, fetch index) for every job of every
    Forge workflow that fetches the companions. With `local`, a local action's steps follow its
    call, at the call's index, with the call's `with` as their `inputs`."""
    for path in sorted(WORKFLOWS.glob('*.yml')):
        for job_name, job in (yaml_lite.load(path.read_text()).get('jobs') or {}).items():
            steps = job.get('steps') or []
            fetches = [i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES]
            if not fetches:
                continue
            listed = []
            for i, step in enumerate(steps):
                listed.append((i, step, {}))
                uses = str(step.get('uses', ''))
                if local and uses.startswith(LOCAL_ACTION) and uses != FETCH_USES:
                    action = WORKFLOWS.parent / 'actions' / uses[len(LOCAL_ACTION):] / 'action.yml'
                    data = yaml_lite.load(action.read_text())
                    given = {k: str(v.get('default', '')) for k, v in (data.get('inputs') or {}).items()}
                    given.update({k: str(v) for k, v in (step.get('with') or {}).items()})
                    inner = data['runs'].get('steps') or []
                    # One level: a local action calling another would hide its steps from the tests.
                    assert not any(str(x.get('uses', '')).startswith('./') for x in inner), action
                    listed += [(i, x, given) for x in inner]
            fetch = steps[fetches[0]]
            yield path, job_name, listed, fetch.get('id'), fetches[0]


def resolve_inputs(value, inputs):
    """A local action's `${{ inputs.x }}`, whole, replaced by what the call passed (or its default)."""
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r'\$\{\{ inputs\.([\w-]+) \}\}', value)
    return inputs.get(match.group(1), value) if match and inputs else value


# The one form a pin enters a cache key: '' or the action's default without a pin, the commit
# fetch-companions' `commit` output gives with one.
KEYED_FORM = ("${{ steps.<fetch>.outputs.active == 'true' && format('<...{0}...>', "
              "steps.<fetch>.outputs.commit) || '<default>' }}")
KEYED = re.compile(r"\$\{\{ steps\.(?P<id>[\w-]+)\.outputs\.active == 'true' && "
                   r"format\('(?P<format>[^']*\{0\}[^']*)', steps\.(?P=id)\.outputs\.commit\) "
                   r"\|\| '(?P<default>[^']*)' \}\}")


def keyed_value(value, active, commit):
    return KEYED.sub(lambda m: m.group('format').replace('{0}', commit) if active else m.group('default'), value)


# What a fetching job may cache without the pin in the key, and why.
TOOLCHAIN_CACHES = {
    # The Swift toolchain RubyInstaller-style tool cache on Windows and the static Linux SDK
    # bundles: compilers and sysroots, downloaded and checksummed, never a package.
    '${{ runner.tool_cache }}/swift-*': 'the Swift toolchain',
    '~/.cache/hyper-swift-sdk': 'the static Linux SDK bundles',
}
CONTENT_ADDRESSED = {
    # GOMODCACHE holds module@version trees the go.sum hashes verify, and a local `replace`
    # (the only way the pin enters) is never copied there; GOCACHE keys every action by the
    # hash of its inputs. Neither can answer for the registry's module with the pin's bytes.
    'actions/setup-go': 'a local replace never enters GOMODCACHE, and GOCACHE is keyed by content',
    # No `cache:` input: only setup-node's automatic npm cache, on when a package.json declares
    # packageManager npm, and npm's cache is keyed by each tarball's integrity hash.
    'actions/setup-node': 'npm\'s cache is keyed by integrity; no `cache:` input is set',
}
CACHE_OFF = ('actions/setup-python', 'actions/setup-java', 'actions/setup-dotnet')
# cargo-semver-checks' rustdoc cache (target/semver-checks/cache): the baseline it builds sees the
# host's Cargo patch, so a pin's rustdoc is the pin's.
SEMVER = 'obi1kenobi/cargo-semver-checks-action'
# setup-r-dependencies' install-pandoc, decided by the `pandoc` step only with a pin; without
# one that step is skipped, its output is empty and the action makes its own decision, as HEAD's
# steps, which set none.
PANDOC = '${{ steps.pandoc.outputs.install }}'

KEYED_KINDS = ('actions/cache', 'actions/cache/restore', 'actions/cache/save', 'r-lib/actions/setup-r-dependencies',
               'ruby/setup-ruby', SEMVER)


def _false(value):
    return str(value).lower() in ('false', '')


# Every other action a fetching job runs (and the local actions it calls), by why it caches no
# package, with the condition under which that holds.
NO_CACHE = {
    'actions/checkout': ('a checkout, no cache', lambda w: True),
    'actions/download-artifact': ("this run's artifacts, no cache", lambda w: True),
    'actions/upload-artifact': ('uploads, never restores', lambda w: True),
    'actions/attest-build-provenance': ('signs, no cache', lambda w: True),
    'dtolnay/rust-toolchain': ('rustup, no cache', lambda w: True),
    'taiki-e/install-action': ('prebuilt tool binaries, no actions cache', lambda w: True),
    'shivammathur/setup-php': ('PHP and its extensions from the runner\'s sources, no package cache',
                               lambda w: True),
    'r-lib/actions/setup-r': ('installs R, caches no package', lambda w: True),
    'r-lib/actions/check-r-package': ('runs the check, caches nothing', lambda w: True),
    # Off unless asked for: with sccache on, compiled crates (the pin's among them) would be cached.
    'PyO3/maturin-action': ('sccache, its only cache, is off', lambda w: _false(w.get('sccache', 'false'))),
    # Its cache, when on, holds downloaded Swift toolchain snapshots only; off in every fetching job.
    'SwiftyLab/setup-swift': ('cache-snapshot is off', lambda w: _false(w.get('cache-snapshot', 'true'))),
    # Caches the bun binary it downloads, a toolchain; it installs no package.
    'oven-sh/setup-bun': ('a toolchain cache of the bun binary only',
                          lambda w: set(w) <= {'bun-version', 'bun-version-file', 'no-cache'}),
}


# HEAD 666a621's `with`, for each step whose cache is now keyed (the ruby-magnus action's setup-ruby,
# and every call of the action from a fetching job, which passes its cache-version).
HEAD_CACHE_WITH = {
    ('ruby-magnus', 'action', 'ruby/setup-ruby'): [
        {'ruby-version': '${{ inputs.ruby-version }}', 'bundler': 'default', 'bundler-cache': True,
         'working-directory': 'ruby'}],
    ('hyper-test-r.yml', 'test', 'r-lib/actions/setup-r-dependencies'): [
        {'working-directory': 'r', 'extra-packages': 'any::rcmdcheck'},
        {'working-directory': 'r', 'extra-packages': 'any::rcmdcheck'}],
    ('hyper-lint-r.yml', 'lint', 'r-lib/actions/setup-r-dependencies'): [
        {'working-directory': 'r', 'extra-packages': 'any::lintr', 'needs': 'lint'},
        {'working-directory': 'r', 'extra-packages': 'any::rcmdcheck'}],
    ('hyper-lint-ruby.yml', 'lint', 'ruby/setup-ruby'): [
        {'ruby-version': '4.0', 'bundler-cache': True, 'working-directory': 'ruby'}],
    ('hyper-test-ruby.yml', 'test', './.hyperforge/.github/actions/ruby-magnus'): [
        {'ruby-version': '${{ inputs.ruby_compat_version }}', 'crate': '${{ inputs.crate }}', 'rid': '${{ matrix.rid }}',
         'attest': "${{ inputs.attest_native && inputs.mode != 'pr' && (github.event_name != 'pull_request' || "
                   "github.event.pull_request.head.repo.full_name == github.repository) }}",
         'companions': '${{ steps.companions.outputs.active }}', 'python': FETCH_PY},
        {'ruby-version': '${{ inputs.ruby_version }}', 'crate': '${{ inputs.crate }}', 'rid': '${{ matrix.rid }}',
         'attest': "${{ inputs.attest_native && inputs.mode != 'pr' && (github.event_name != 'pull_request' || "
                   "github.event.pull_request.head.repo.full_name == github.repository) }}",
         'companions': '${{ steps.companions.outputs.active }}', 'python': FETCH_PY}],
    ('hyper-test-ruby.yml', 'test', 'ruby/setup-ruby'): [
        {'ruby-version': '${{ inputs.ruby_version }}', 'bundler': 'default', 'bundler-cache': True,
         'working-directory': 'ruby'}],
    ('hyper-build-wasm.yml', 'build-ruby-wasm', 'ruby/setup-ruby'): [
        {'ruby-version': '4.0', 'bundler-cache': True, 'working-directory': 'ruby'}],
    ('hyper-core-checks.yml', 'checks', SEMVER): [
        {'manifest-path': 'rust/Cargo.toml', 'feature-group': 'default-features'}],
    ('hyper-build-wasm.yml', 'build-ruby-wasm', 'actions/cache'): [
        {'path': '${{ inputs.ruby_wasm_smoke }}/build',
         'key': "ruby-wasm-${{ inputs.crate }}-${{ matrix.abi }}-${{ hashFiles(format('{0}/Gemfile', "
                "inputs.ruby_wasm_smoke)) }}"}],
}

# The environments a host-side override cannot reach (the spec's "Isolated environments get the
# companion explicitly"), by the script that builds each, relative to .github/.
ISOLATED_ENVIRONMENTS = (
    'actions/musl-native/build-musl.sh',
    'actions/musl-suite/suite-musl.sh',
    'actions/ruby-magnus-musl/build-magnus-musl.sh',
    'scripts/php_ext_musl.sh',
    'actions/swift-static-suite/suite-swift-static.sh',
    'actions/pyodide-suite/suite-pyodide.mjs',
    'actions/pyodide-suite/browser-pyodide.mjs',
    'actions/ruby-wasm-suite/suite-ruby-wasm.sh',
)

# The jobs whose inline steps build an environment the host's override cannot reach (the spec's
# "sdist and fallthrough venvs", several in Docker), each with the wiring every such step carries.
WORKFLOW_ENVIRONMENTS = {
    ('hyper-build-wheels.yml', 'build-sdist'): 'companion_container.sh',
    ('hyper-build-wheels.yml', 'build-wheels'): 'companion_container.sh',
    ('hyper-build-wheels.yml', 'sdist-fallthrough'): 'companion_wire.py" python --root . --python "$RUNNER_TEMP/v/bin/python"',
}
# Inline containers that take no companion, and why.
INLINE_CONTAINERS_EXEMPT = {
    ('hyper-core.yml', 'core-musl', 'Load it and call every export in Alpine'):
        'loads the core the host built (with the host\'s patch) and resolves nothing',
    ('hyper-core.yml', 'core-musl', 'Smoke-test the C archive'):
        'consumes PACKAGE, the staged C archive; its pinned twin runs with a pin (test_alpine_smokes_have_a_pinned_twin)',
    ('hyper-test-c.yml', 'smoke', 'Smoke-test the ${{ matrix.rid }} archive on Alpine'):
        'consumes PACKAGE, the staged C archive; its pinned twin runs with a pin',
    ('hyper-pie-acceptance.yml', 'install', 'Install with PIE, then check from a fresh PHP (Alpine)'):
        'release-only: PIE installs the published extension',
    ('hyper-check-core.yml', 'check-cdylib', 'Build, check, load and call it in Alpine'):
        'a legacy workflow no board calls',
}

# What resolves a package on a board, beyond `resolves`: any package manager or build tool
# (`./gradlew` included), and setup-ruby's `bundler-cache`, which runs `bundle install`.
BOARD_RESOLVES = re.compile(r'(^|[\s;&|(/"])(cargo|go|tinygo|wasm-pack|maturin|npm|npx|pnpm|yarn|bun|bundle|gem|rake|'
                            r'gradlew|mvn|dotnet|composer|swift|xcodebuild|R|Rscript|pip3?|pytest|uv)"?\s', re.M)
# A toolchain's own install reads no consumer manifest.
TOOL_INSTALL = re.compile(r'(^|[\s;&|(])(cargo install|go install \S+@|rustup|npm install -g|gem install bundler)\b')


def board_resolves(step):
    run = TOOL_INSTALL.sub(' ', VERSION_ONLY.sub(' ', str(step.get('run', ''))))
    return (resolves(step) or bool(BOARD_RESOLVES.search(run))
            or str((step.get('with') or {}).get('bundler-cache', '')).lower() == 'true')


# Steps that resolve something before the fetch but nothing of the companion's.
NO_COMPANION_STEPS = {
    # The lint's Java side is formatting: the Gradle build is configured, never resolved
    # (hyper-lint-java.yml says so beside the fetch, which serves java/jni's Cargo).
    ('hyper-lint-java.yml', 'lint', 'spotlessCheck'): 'Spotless reads sources and its own formatter only',
}
# Board jobs that resolve packages but take no companion, and why.
NO_FETCH = {
    'hyper-build-php-extension.yml:version': 'reads the crate version from Cargo.toml',
    'hyper-lint-csharp.yml:lint': 'dotnet format whitespace reads the sources, restores nothing',
    'hyper-lint-php.yml:lint': 'Composer installs the dev tools; the extension is never a Composer package',
    'hyper-lint-python.yml:lint': 'installs ruff, a tool, and runs it over the sources',
    'hyper-lint-swift.yml:lint': 'swift format reads the sources, resolves no package',
}


class SuiteRubyWasmTests(unittest.TestCase):
    """suite-ruby-wasm.sh's gem stage over a wired consumer, with Bundler, RubyGems and rbwasm
    stubbed (fixtures/suite-ruby-wasm/): the pin's hypercast-wasm packed around the staged
    archive and proven between the smoke Gemfile's install and rbwasm, and, without the
    wiring's Gemfile line, RubyGems' copy named before rbwasm runs."""
    def setUp(self):
        import subprocess
        import tempfile
        from test_companion_wire import RUBY_ENV, WASM_ARTIFACTS, WASM_GEM, ruby_checkout
        import companion_wire
        self.subprocess = subprocess
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = ruby_checkout(self.tmp / 'consumer', WASM_ARTIFACTS)
        (self.root / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in RUBY_ENV.items()))
        with mock.patch('sys.stdout'):
            companion_wire.wire_ruby(self.root, RUBY_ENV)
        self.wasm = self.root / WASM_GEM
        self.out = self.tmp / 'out'
        (self.out / 'archives' / '4.0').mkdir(parents=True)
        (self.out / 'archives' / '4.0' / 'hypertabular_native.a').write_bytes(b'consumer archive')
        openssl = self.tmp / 'openssl' / 'lib' / 'pkgconfig'
        openssl.mkdir(parents=True)
        (openssl / 'openssl.pc').write_text('')
        self.log = self.tmp / 'log'
        self.env = {**os.environ, 'PATH': f'{SCRIPTS / "fixtures" / "suite-ruby-wasm"}:{os.environ["PATH"]}',
                    'HOME': str(self.tmp / 'home'), 'OUT': str(self.out), 'STUB_LOG': str(self.log),
                    'STUB_OPENSSL': str(self.tmp / 'openssl')}

    def run_stage(self):
        return self.subprocess.run(['bash', str(ACTIONS / 'ruby-wasm-suite' / 'suite-ruby-wasm.sh'), 'hypertabular',
                                    '4.0', 'gem'], cwd=self.root, env=self.env, capture_output=True, text=True)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_the_pinned_gem_is_packed_and_proven_before_rbwasm(self):
        result = self.run_stage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'Ruby wasm (ruby/wasm-smoke/): hypercast-wasm 1.0.0 from {self.wasm}', result.stdout)
        gz = self.wasm / 'ext' / 'hypercast_native' / '4.0' / 'hypercast_native.a.gz'
        import gzip
        self.assertEqual(gzip.decompress(gz.read_bytes()), b'wasm 4.0 archive')
        calls = self.calls()
        pin = self.root / '.companions' / 'hypercast' / 'ruby'
        self.assertIn(f'{pin}: bundle exec rake wasm:gem[4.0]', calls)
        smoke = self.root / 'ruby' / 'wasm-smoke'
        self.assertLess(calls.index(f'{smoke}: bundle info hypercast-wasm --path'),
                        next(i for i, c in enumerate(calls) if c.startswith(f'{smoke}: bundle exec rbwasm build')))

    def test_without_the_wiring_the_registry_copy_is_named_and_rbwasm_never_runs(self):
        smoke = self.root / 'ruby' / 'wasm-smoke' / 'Gemfile'
        smoke.write_text(smoke.read_text().split('\n# The pinned hypercast-wasm')[0] + '\n')
        result = self.run_stage()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'found hypercast-wasm from {self.tmp}/home/.gem/ruby/4.0.0/gems/hypercast-wasm-1.0.0 '
                      '(RubyGems, not the pin)', result.stderr)
        self.assertFalse([c for c in self.calls() if 'rbwasm' in c])

    def test_a_swapped_archive_is_named_and_rbwasm_never_runs(self):
        (self.root / '.companions' / 'hypercast' / 'ruby' / 'ext' / 'hypercast_native' / '4.0' /
         'hypercast_native.a').write_bytes(b'another build')
        result = self.run_stage()
        self.assertEqual(result.returncode, 1)
        self.assertIn('ext/hypercast_native/4.0/hypercast_native.a.gz', result.stderr)
        self.assertFalse([c for c in self.calls() if 'rbwasm' in c])

    def test_without_env_nothing_of_the_pin_runs(self):
        (self.root / '.companions' / 'env').unlink()
        result = self.run_stage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.wasm.exists())
        self.assertFalse([c for c in self.calls() if '.companions' in c.split(':')[0] or ' info ' in c])
        self.assertTrue([c for c in self.calls() if 'rbwasm' in c])


class BuildWheelsInstallTests(unittest.TestCase):
    """build-wheels' install step, its in_docker and on() run under bash with docker, the
    interpreters and the Forge's scripts recorded: without a pin, HEAD's commands exactly; with
    one, each interpreter wired, the consumer installed, and the origin proven last."""

    HEAD_DOCKER = ['docker', 'run', '--rm', '-v', '{pwd}/python/dist:/dist:ro']
    PIN_DOCKER = ['docker', 'run', '--rm', '-e', 'PIP_ROOT_USER_ACTION=ignore', '-v', '{pwd}:/src:ro',
                  '-v', '{pwd}/python/dist:/dist:ro']

    @staticmethod
    def function(run, name):
        lines = run.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == f'{name}() {{')
        indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == f'{indent}}}')
        return '\n'.join(lines[start:end + 1])

    def calls(self, pin, under=''):
        import os
        import subprocess
        import tempfile
        job = yaml_lite.load((WORKFLOWS / 'hyper-build-wheels.yml').read_text())['jobs']['build-wheels']
        run = next(s for s in job['steps'] if s.get('name') == 'Install each wheel and call into it')['run']
        companion = next(line.strip() for line in run.splitlines() if line.strip().startswith("companion='"))
        record = 'printf "%s" "$1"; shift; for a in "$@"; do printf "\\037%s" "$a"; done; printf "\\n"'
        script = '\n'.join([
            'set -euo pipefail', 'forge=.hyperforge/.github/scripts', 'companion=""',
            companion if pin else '', f'under="{under}"', 'RUNNER_TEMP=/',
            f'record() {{ {record}; }}',
            'docker() { record docker "$@"; }', 'run() { record run "$@"; }', 'python3() { record python3 "$@"; }',
            # fetch-companions' `python` output, which runs the Forge's scripts on the host.
            'FETCH_PYTHON=fetch-python', 'fetch-python() { record fetch-python "$@"; }',
            'check() { echo "CHECK$1"; }',
            self.function(run, 'in_docker'), self.function(run, 'on'),
            'in_docker IMG "" python3 "INSTALL-A"',
            'in_docker IMG "SETUP" /v/bin/python "INSTALL-B"',
            'on gil /py WHEEL True'])
        with tempfile.TemporaryDirectory() as cwd:
            pwd = os.path.realpath(cwd)
            result = subprocess.run(['bash', '-c', script], cwd=pwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        fill = lambda argv: [a.replace('{pwd}', pwd) for a in argv]
        return [line.split('\x1f') for line in result.stdout.splitlines()], fill

    def test_without_a_pin_the_commands_are_heads(self):
        calls, fill = self.calls(pin=False)
        probe = 'import platform, sys; print("gil interpreter:", platform.machine(), sys.version.split()[0])'
        self.assertEqual(calls, [
            fill(self.HEAD_DOCKER) + ['IMG', 'sh', '-euc', 'INSTALL-A'],
            fill(self.HEAD_DOCKER) + ['IMG', 'sh', '-euc', 'SETUP && INSTALL-B'],
            ['run', '/py', '-c', probe],
            ['run', '/py', '-m', 'pip', 'install', '--quiet', '--force-reinstall', 'WHEEL'],
            ['run', '/py', '-W', 'error::RuntimeWarning', '-c', 'CHECKTrue']])

    def test_with_a_pin_the_origin_is_proven_after_the_last_install(self):
        forge = '.hyperforge/.github/scripts'
        helper = f'. /src/{forge}/companion_container.sh && companion_python /src /src/{forge} '
        for under in ('', 'arch -x86_64 '):
            with self.subTest(under=under):
                calls, fill = self.calls(pin=True, under=under)
                self.assertEqual(calls[:2], [
                    fill(self.PIN_DOCKER) + ['IMG', 'sh', '-euc',
                                             f'{helper}python3 wire; {{ INSTALL-A; }}; {helper}python3 origin'],
                    fill(self.PIN_DOCKER) + ['IMG', 'sh', '-euc',
                                             f'SETUP && {helper}/v/bin/python wire; {{ INSTALL-B; }}; '
                                             f'{helper}/v/bin/python origin']])
                self.assertEqual(calls[3:], [
                    ['fetch-python', f'{forge}/companion_wire.py', 'python', '--root', '.', '--python', f'{under}/py'],
                    ['run', '/py', '-m', 'pip', 'install', '--quiet', '--force-reinstall', '--no-deps', 'WHEEL'],
                    ['run', '/py', '-m', 'pip', 'install', '--quiet', 'WHEEL'],
                    ['fetch-python', f'{forge}/companion_origin.py', 'python', '--root', '.', '--python', f'{under}/py'],
                    ['run', '/py', '-W', 'error::RuntimeWarning', '-c', 'CHECKTrue']])


class CompanionContainerTests(unittest.TestCase):
    """companion_container.sh under sh, with recorded cargo and go stand-ins on PATH."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.src, self.work, self.bin = base / 'src', base / 'work', base / 'bin'
        for d in (self.src / '.companions' / 'hypercast' / 'rust', self.src / '.companions' / 'hypercast' / 'go',
                  self.work / 'rust', self.work / 'go', self.bin, base / 'cargo-home'):
            d.mkdir(parents=True)
        (self.src / '.companions' / 'hypercast' / 'go' / 'go.mod').write_text('module github.com/SkunkWerkx/HyperCast/go\n')
        (self.src / '.companions' / 'hypercast' / 'rust' / 'Cargo.toml').write_text('[package]\nname = "hypercast"\n')
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n'
                                                      'HYPERCAST_RUST_REQ=.companions/hypercast/rust\n'
                                                      'HYPERCAST_GO_REQ=.companions/hypercast/go\n')
        self.calls = base / 'calls'
        for tool in ('cargo', 'go'):
            stub = self.bin / tool
            stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "$CALLS"\n'
                            'case "$*" in "mod edit"*) exit 0 ;; esac\n'
                            'printf "%s" "$FAKE_ERR" >&2\nprintf "%s\\n" "$FAKE_OUT"\nexit "${FAKE_CODE:-0}"\n')
            stub.chmod(0o755)
        self.cargo_home = base / 'cargo-home'
        self.pin = self.work / '.companions' / 'hypercast'

    def run_sh(self, call, out='', err='', code=0):
        import os
        import subprocess
        script = f'. "{WORKFLOWS.parent / "scripts" / "companion_container.sh"}"\n{call}\n'
        env = {'PATH': f'{self.bin}:/usr/bin:/bin', 'CALLS': str(self.calls), 'CARGO_HOME': str(self.cargo_home),
               'HOME': self.tmp.name, 'FAKE_OUT': out, 'FAKE_ERR': err, 'FAKE_CODE': str(code)}
        return subprocess.run(['sh', '-euc', script], env={**env, 'TMPDIR': os.environ.get('TMPDIR', '/tmp')},
                              capture_output=True, text=True)

    def cargo(self, **kwargs):
        return self.run_sh(f'companion_cargo "{self.src}" "{self.work}" "{self.work}/rust"', **kwargs)

    def test_cargo_pinned_passes(self):
        result = self.cargo(out=f'hypercast v1.0.0 ({self.pin}/rust)')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.pin / 'rust' / 'Cargo.toml').is_file())
        config = (self.cargo_home / 'config.toml').read_text()
        self.assertIn(f'[patch.crates-io]\nhypercast = {{ path = "{self.pin}/rust" }}', config)
        self.assertIn(f'cargo tree -i hypercast --locked --all-features --depth 0 --prefix none --color never '
                      f'--manifest-path {self.work}/rust/Cargo.toml', self.calls.read_text())

    def test_cargo_registry_copy_is_rejected(self):
        result = self.cargo(out='hypercast v1.0.0')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::Rust ({self.work}/rust): found hypercast v1.0.0 from a registry (crates.io), '
                      f'expected hypercast from {self.pin}/rust (the pin, a path source)', result.stdout)

    def test_cargo_unused_patch_and_failure_are_rejected(self):
        # Every recorded shape of Cargo's warning (fixtures/origin/rust/), coloured ones included.
        for name in ('unused.stderr', 'unused-color.stderr', 'unused-old.stderr', 'unused-old-color.stderr'):
            with self.subTest(stderr=name):
                unused = (WORKFLOWS.parent / 'scripts' / 'fixtures' / 'origin' / 'rust' / name).read_text()
                result = self.cargo(out=f'hypercast v1.0.0 ({self.pin}/rust)',
                                    err=unused.replace('@ROOT@', str(self.work)))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("found Cargo's '", result.stdout)
                self.assertIn('was not used', result.stdout)
        result = self.cargo(err='error: cannot update the lock file\n', code=101)
        self.assertEqual(result.returncode, 1)
        self.assertIn('found cargo tree failing (exit 101)', result.stdout)

    def test_cargo_in_place_copies_nothing(self):
        # maturin-action's image mounts the checkout at its own path, read-write: <src> is <work>,
        # the patch names the checkout's own copy, and nothing is copied onto itself.
        pin = self.src / '.companions' / 'hypercast' / 'rust'
        (self.src / 'rust').mkdir()
        before = (pin / 'Cargo.toml').stat()
        result = self.run_sh(f'companion_cargo "{self.src}" "{self.src}" "{self.src}/rust"',
                             out=f'hypercast v1.0.0 ({pin})')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # tar's extraction replaces each file (a new inode); none is rewritten.
        after = (pin / 'Cargo.toml').stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(sorted(p.name for p in pin.iterdir()), ['Cargo.toml'])
        self.assertFalse((self.src / '.companions' / 'hypercast' / '.companions').exists())
        self.assertIn(f'hypercast = {{ path = "{pin}" }}', (self.cargo_home / 'config.toml').read_text())

    def test_cargo_keeps_an_identical_patch_and_still_proves_it(self):
        # The host's own $CARGO_HOME, where fetch-companions already wrote the same entry: a
        # second [patch.crates-io] table is a TOML duplicate key cargo cannot load.
        config = self.cargo_home / 'config.toml'
        held = (f'[net]\nretry = 3\n\n# fetch-companions: the pinned hypercast (companion-pins)\n'
                f'[patch.crates-io]\nhypercast = {{ path = "{self.pin}/rust" }}\n')
        config.write_text(held)
        result = self.cargo(out=f'hypercast v1.0.0 ({self.pin}/rust)')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(config.read_text(), held)
        self.assertIn('already holds', result.stdout)
        self.assertIn('cargo tree -i hypercast --locked', self.calls.read_text())
        result = self.cargo(out='hypercast v1.0.0')
        self.assertEqual(result.returncode, 1)
        self.assertIn('from a registry (crates.io)', result.stdout)

    def test_cargo_refuses_another_patch_table(self):
        config = self.cargo_home / 'config.toml'
        held = '[patch.crates-io]\nhypercast = { path = "/elsewhere/rust" }\n'
        config.write_text(held)
        result = self.cargo(out=f'hypercast v1.0.0 ({self.pin}/rust)')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(config.read_text(), held)
        self.assertIn(f'::error::{config} already has a [patch.crates-io] table without '
                      f'hypercast = {{ path = "{self.pin}/rust" }}', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_cargo_without_env_does_nothing(self):
        (self.src / '.companions' / 'env').unlink()
        result = self.cargo(out='hypercast v1.0.0')
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.cargo_home / 'config.toml').exists())

    def test_env_without_the_ecosystem_fails(self):
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n')
        for call in (f'companion_cargo "{self.src}" "{self.work}" "{self.work}/rust"',
                     f'companion_go "{self.src}" "{self.work}" "{self.work}/go"'):
            with self.subTest(call=call.split()[0]):
                result = self.run_sh(call)
                self.assertEqual(result.returncode, 1)
                self.assertIn('the host never wired', result.stdout)

    def python(self, step, code=0):
        """companion_python with a stand-in interpreter that records what it is asked to run."""
        stub = self.bin / 'python3'
        stub.write_text('#!/bin/sh\necho "python3 $*" >> "$CALLS"\nexit "${FAKE_CODE:-0}"\n')
        stub.chmod(0o755)
        return self.run_sh(f'companion_python "{self.src}" /forge "{stub}" {step}', code=code), stub

    def test_python_wires_then_proves_inside(self):
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n'
                                                      'HYPERCAST_PYTHON_REQ=.companions/hypercast-artifacts/a.whl\n')
        result, stub = self.python('wire')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The Forge's own scripts, run by the container's interpreter on the mounted checkout:
        # the pinned file installed into that interpreter, then its origin proven there.
        self.assertEqual(self.calls.read_text().splitlines(), [
            f'python3 /forge/companion_wire.py python --root {self.src} --python {stub}',
            f'python3 /forge/companion_origin.py python --root {self.src} --python {stub}'])
        self.calls.unlink()
        result, _ = self.python('origin')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls.read_text().splitlines(),
                         [f'python3 /forge/companion_origin.py python --root {self.src} --python {stub}'])

    def test_python_failure_stops_the_container(self):
        (self.src / '.companions' / 'env').write_text('HYPERCAST_PYTHON_REQ=.companions/hypercast-artifacts/a.whl\n')
        for step in ('wire', 'origin'):
            with self.subTest(step=step):
                result, _ = self.python(step, code=1)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f'::error::Python ({self.src}): companion_python {step} failed', result.stdout)
        result, _ = self.python('install')
        self.assertEqual(result.returncode, 1)

    def test_python_without_env_does_nothing(self):
        (self.src / '.companions' / 'env').unlink()
        result, _ = self.python('wire')
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_python_env_without_python_fails(self):
        result, _ = self.python('wire')
        self.assertEqual(result.returncode, 1)
        self.assertIn('has no *_PYTHON_REQ: the host never wired Python', result.stdout)
        self.assertFalse(self.calls.exists())

    def ruby_pin(self):
        """A pinned gem as the host's wiring leaves it in <src> (a musl leg's extension for 4.0 and
        library placed in it), its env lines, and the leg's SHA256SUMS."""
        import hashlib
        gem = self.src / '.companions' / 'hypercast' / 'ruby' / 'lib'
        (gem / 'hypercast' / '4.0').mkdir(parents=True)
        (gem / 'hypercast' / 'native' / 'linux-musl-arm64').mkdir(parents=True)
        (gem / 'hypercast.rb').write_text('module HyperCast; end\n')
        files = {'ruby-native-linux-musl-arm64-4.0/hypercast_native.so': (gem / 'hypercast' / '4.0' / 'hypercast_native.so', b'magnus'),
                 'native-linux-musl-arm64/libhypercast.so': (gem / 'hypercast' / 'native' / 'linux-musl-arm64' / 'libhypercast.so', b'fiddle')}
        artifacts = self.src / '.companions' / 'hypercast-artifacts'
        artifacts.mkdir(parents=True)
        sums = ''
        for key, (path, data) in files.items():
            path.write_bytes(data)
            sums += f'{hashlib.sha256(data).hexdigest()}  {key}\n'
        (artifacts / 'SHA256SUMS').write_text(sums)
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n'
                                                      'HYPERCAST_ARTIFACTS=.companions/hypercast-artifacts\n'
                                                      'HYPERCAST_VERSION=1.0.0\n'
                                                      'HYPERCAST_RUBY_REQ=.companions/hypercast/ruby\n')
        stub = self.bin / 'ruby'
        stub.write_text('#!/bin/sh\necho "ruby $*" >> "$CALLS"\nprintf "%s\\n" "$FAKE_OUT"\nexit "${FAKE_CODE:-0}"\n')
        stub.chmod(0o755)
        return {k: hashlib.sha256(d).hexdigest() for k, (_, d) in files.items()}

    def facts(self, **changes):
        """companion_ruby.rb's facts for the /work copy of the pin on Ruby 4.0, Magnus loaded."""
        import hashlib
        lib = self.pin / 'ruby' / 'lib'
        facts = {'name': 'hypercast', 'version': '1.0.0', 'ruby': '4.0.2', 'abi': '4.0', 'rid': 'linux-musl-arm64',
                 'backend': 'native', 'pure': '', 'source': f'{lib}/hypercast.rb',
                 'extension': f'{lib}/hypercast/4.0/hypercast_native.so',
                 'extension_sha256': hashlib.sha256(b'magnus').hexdigest(),
                 'library': f'{lib}/hypercast/native/linux-musl-arm64/libhypercast.so',
                 'library_sha256': hashlib.sha256(b'fiddle').hexdigest(), 'observed': '1',
                 'mapped': f'{lib}/hypercast/native/linux-musl-arm64/libhypercast.so', 'gem': ''}
        facts.update(changes)
        return '\n'.join(f'{k}={v}' for k, v in facts.items())

    def ruby(self, step, **kwargs):
        # The variables as a child process (gem install, rspec) sees them: exported.
        return self.run_sh(f'companion_ruby "{self.src}" "{self.work}" /forge {step}\n'
                           'sh -c \'echo "RUBYLIB=${RUBYLIB:-}"; echo "GEM=${HYPER_COMPANION_GEM:-}"\'', **kwargs)

    def test_ruby_wire_puts_the_pin_on_rubylib(self):
        self.ruby_pin()
        result = self.ruby('wire')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The pinned gem, with the binaries the host placed, copied under <work>; its lib/ first
        # on RUBYLIB, and its name for the install loop to skip.
        self.assertEqual((self.pin / 'ruby' / 'lib' / 'hypercast' / '4.0' / 'hypercast_native.so').read_bytes(), b'magnus')
        self.assertIn(f'RUBYLIB={self.pin}/ruby/lib\n', result.stdout)
        self.assertIn('GEM=hypercast\n', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_ruby_origin_pinned_passes(self):
        self.ruby_pin()
        self.ruby('wire')
        for facts in (self.facts(),
                      # Fiddle where no extension is staged for the ABI (the floor), or asked for.
                      self.facts(abi='3.3', backend='fiddle', extension='', extension_sha256=''),
                      self.facts(backend='fiddle', pure='1', extension='', extension_sha256='')):
            with self.subTest(facts=facts):
                result = self.ruby('origin', out=facts)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Ruby 4.0.2 (', result.stdout)
        self.assertIn('ruby /forge/companion_ruby.rb hypercast', self.calls.read_text())

    def test_ruby_origin_registry_copy_is_rejected(self):
        self.ruby_pin()
        self.ruby('wire')
        registry = '/usr/local/bundle/gems/hypercast-1.0.0-aarch64-linux-musl/lib'
        result = self.ruby('origin', out=self.facts(source=f'{registry}/hypercast.rb',
                                                    extension=f'{registry}/hypercast/4.0/hypercast_native.so'))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'found hypercast required from {registry}/hypercast.rb, expected hypercast 1.0.0 from '
                      f'{self.pin}/ruby/lib (the pin, on RUBYLIB)', result.stdout)

    def test_ruby_origin_tampered_hash_is_rejected(self):
        self.ruby_pin()
        self.ruby('wire')
        for facts, needle in ((self.facts(extension_sha256='0' * 64),
                               'not ruby-native-linux-musl-arm64-4.0/hypercast_native.so as SHA256SUMS lists it'),
                              (self.facts(backend='fiddle', pure='1', library_sha256='0' * 64),
                               'not native-linux-musl-arm64/libhypercast.so as SHA256SUMS lists it'),
                              (self.facts(backend='fiddle'), 'the Fiddle backend, though the leg staged lib/hypercast/4.0/'),
                              (self.facts(extension='/tmp/hypercast_native.so'), 'the native backend loading /tmp/'),
                              (self.facts(version='1.1.0'), 'hypercast 1.1.0 from'),
                              # Fiddle's search naming the pin while the dlopen mapped another
                              # copy, or nothing (the load never happened).
                              (self.facts(backend='fiddle', pure='1', mapped='/usr/lib/libhypercast.so'),
                               'the Fiddle backend mapping /usr/lib/libhypercast.so libhypercast.so after the load'),
                              (self.facts(backend='fiddle', pure='1', mapped=''),
                               'the Fiddle backend mapping no libhypercast.so after the load'),
                              (self.facts(backend='fiddle', pure='1', observed=''), 'the Fiddle backend mapping')):
            with self.subTest(needle=needle):
                result = self.ruby('origin', out=facts)
                self.assertEqual(result.returncode, 1)
                self.assertIn(needle, result.stdout)

    def test_ruby_without_env_does_nothing_and_env_without_ruby_fails(self):
        result = self.ruby('wire')
        self.assertEqual(result.returncode, 1)
        self.assertIn('has no *_RUBY_REQ: the host never wired Ruby', result.stdout)
        (self.src / '.companions' / 'env').unlink()
        for step in ('wire', 'origin'):
            result = self.ruby(step)
            self.assertEqual(result.returncode, 0)
            self.assertIn('RUBYLIB=\nGEM=\n', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_go_pinned_passes_and_the_host_replace_is_rejected(self):
        call = f'companion_go "{self.src}" "{self.work}" "{self.work}/go"'
        result = self.run_sh(call, out=f'{self.pin}/go')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'go mod edit -replace=github.com/SkunkWerkx/HyperCast/go={self.pin}/go', self.calls.read_text())
        # go list still giving the host's path: the rewrite didn't take.
        result = self.run_sh(call, out='/home/runner/work/x/.companions/hypercast/go')
        self.assertEqual(result.returncode, 1)
        self.assertIn('::error::Go (', result.stdout)
        self.assertIn('replaced by /home/runner/work/x/.companions/hypercast/go, expected', result.stdout)
        result = self.run_sh(call, code=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn('found no copy (go list failed)', result.stdout)


class CompanionJvmDotnetContainerTests(unittest.TestCase):
    """companion_java and companion_csharp (companion_container.sh) under sh, on a consumer the
    host wired with companion_wire.py (Gradle and dotnet stood in for), with recorded gradlew and
    dotnet stand-ins in the container's <work>."""

    def setUp(self):
        import tempfile
        # Every character java.util.Properties treats specially, in the paths the helpers write.
        self.tmp = tempfile.TemporaryDirectory(prefix='a b=c:d#e!f\\g ')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.work, self.calls = self.base / 'work', self.base / 'calls'
        # The stand-ins' directory goes on PATH, which a ':' would split.
        self.tools = tempfile.TemporaryDirectory()
        self.addCleanup(self.tools.cleanup)
        self.bin = Path(self.tools.name).resolve() / 'bin'
        self.bin.mkdir()
        self.gradle_home = self.base / 'container-gradle'
        self.packages = self.base / 'container-nuget'

    def host(self, eco):
        import io
        from contextlib import redirect_stdout
        import companion_wire
        import test_companion_wire as tw
        env = tw.JAVA_ENV if eco == 'java' else tw.CSHARP_ENV
        with redirect_stdout(io.StringIO()):
            if eco == 'java':
                self.src = tw.java_checkout(self.base / 'src')
                companion_wire.wire_java(self.src, env, run=tw.FakeGradle(),
                                         environ={'GRADLE_USER_HOME': str(self.base / 'host-gradle')})
            else:
                self.src = tw.csharp_checkout(self.base / 'src')
                companion_wire.wire_csharp(self.src, env, run=tw.FakeDotnetPack(), environ={})
        (self.src / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in env.items()))
        shutil.copytree(self.src / eco, self.work / eco)
        self.record = dict(l.split('=', 1) for l in (self.src / '.companions' / f'hypercast-{eco}.record').read_text().splitlines())

    def run_sh(self, call, out='', err='', code=0):
        import os
        import subprocess
        script = f'. "{SCRIPTS / "companion_container.sh"}"\n{call}\n'
        env = {'PATH': f'{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin', 'CALLS': str(self.calls), 'HOME': str(self.base),
               'GRADLE_USER_HOME': str(self.gradle_home), 'NUGET_PACKAGES': str(self.packages),
               'FAKE_OUT': out, 'FAKE_ERR': err, 'FAKE_CODE': str(code), 'TMPDIR': os.environ.get('TMPDIR', '/tmp')}
        return subprocess.run(['sh', '-euc', script], env=env, capture_output=True, text=True)

    def gradlew(self):
        stub = self.work / 'java' / 'gradlew'
        stub.write_text('#!/bin/sh\necho "gradlew $*" >> "$CALLS"\nprintf "%s" "$FAKE_ERR" >&2\n'
                        'printf "%s" "$FAKE_OUT"\nexit "${FAKE_CODE:-0}"\n')
        stub.chmod(0o755)

    def java(self, step, **kwargs):
        return self.run_sh(f'companion_java "{self.src}" "{self.work}" "{SCRIPTS}" {step} "{self.work}/java"', **kwargs)

    def recorded(self, name, suffix):
        return ((SCRIPTS / 'fixtures' / 'origin' / 'java' / f'{name}.{suffix}').read_text()
                .replace('@ROOT@', self.work.as_posix()).replace('@HOME@', str(self.base))
                .replace('@SHA256@', self.record['sha256']))

    def test_java_wire_writes_the_hosts_init_script_for_the_work_copy(self):
        import companion_wire
        self.host('java')
        result = self.java('wire')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        repository = self.work / '.companions' / 'm2'
        self.assertEqual((self.work / self.record['jar']).read_bytes(), (self.src / self.record['jar']).read_bytes())
        # The Forge's script, unchanged, and the properties companion_wire.py would write, byte for
        # byte, with the container's repository.
        self.assertEqual((self.gradle_home / 'init.d' / 'companions.gradle').read_text(),
                         (SCRIPTS / 'companion_java.init.gradle').read_text())
        self.assertEqual((self.gradle_home / 'init.d' / 'companions.properties').read_text(),
                         companion_wire.java_properties(repository.as_posix(), 'io.github.skunkwerkx', 'hypercast',
                                                        '1.0.0', self.record['sha256']))
        self.assertFalse(self.calls.exists())

    def test_java_origin_pinned_passes(self):
        self.host('java')
        self.java('wire')
        self.gradlew()
        result = self.java('origin', out=self.recorded('pinned', 'stdout'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'Java ({self.work}/java): io.github.skunkwerkx:hypercast:1.0.0 from {self.work}/.companions/m2',
                      result.stdout)
        self.assertEqual(self.calls.read_text(), 'gradlew -q --console=plain companionOrigin\n')

    def test_java_origin_registry_copy_is_rejected(self):
        self.host('java')
        self.java('wire')
        self.gradlew()
        expected = (f'expected io.github.skunkwerkx:hypercast:1.0.0 from {self.work}/.companions/m2 (the pin, sha256 '
                    f'{self.record["sha256"]})')
        result = self.java('origin', out=self.recorded('registry', 'stdout'), err=self.recorded('registry', 'stderr'),
                           code=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::Java ({self.work}/java): found at :: io.github.skunkwerkx:hypercast:1.0.0 from '
                      f'{self.base}/.gradle/caches/modules-2/', result.stdout)
        self.assertIn("a remote repository's copy (Maven Central, from Gradle's module cache), " + expected, result.stdout)
        result = self.java('origin', err=self.recorded('unwired', 'stderr'), code=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn('found no companionOrigin task in the build: the init script is not installed', result.stdout)
        result = self.java('origin')
        self.assertEqual(result.returncode, 1)
        self.assertIn('found no companion-origin line', result.stdout)

    def test_java_tampered_hash_is_rejected(self):
        self.host('java')
        self.java('wire')
        self.gradlew()
        result = self.java('origin', out=self.recorded('swapped', 'stdout'), err=self.recorded('swapped', 'stderr'),
                           code=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn('(sha256 2acd78c17c1f2793b89c2c80c2502b3fcb916f790d7b01633de7e96bef869805)', result.stdout)
        # The host's jar swapped after the publish: the wire refuses to copy it in.
        (self.src / self.record['jar']).write_bytes(b'another jar')
        (self.gradle_home / 'init.d' / 'companions.gradle').unlink()
        result = self.java('wire')
        self.assertEqual(result.returncode, 1)
        self.assertIn('with sha256 ', result.stdout)
        self.assertFalse((self.gradle_home / 'init.d' / 'companions.gradle').exists())

    def test_java_without_env_does_nothing_and_without_the_wiring_fails(self):
        self.host('java')
        (self.src / '.companions' / 'hypercast-java.record').unlink()
        result = self.java('wire')
        self.assertEqual(result.returncode, 1)
        self.assertIn("never built the pin, so this container would take Maven Central's copy", result.stdout)
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n')
        self.assertIn('has no *_JAVA_REQ: the host never wired Java', self.java('wire').stdout)
        (self.src / '.companions' / 'env').unlink()
        for step in ('wire', 'origin'):
            result = self.java(step)
            self.assertEqual((result.returncode, result.stdout), (0, ''))
        self.assertFalse(self.gradle_home.exists())
        self.assertFalse(self.calls.exists())

    # C#
    def dotnet(self):
        stub = self.bin / 'dotnet'
        stub.write_text('#!/bin/sh\necho "dotnet $*" >> "$CALLS"\nexit "${FAKE_CODE:-0}"\n')
        stub.chmod(0o755)

    def restored(self, metadata='pinned', nupkg=None, packages=None, assets=None):
        """What `dotnet restore` leaves in the container (recorded .nupkg.metadata and assets), in
        `packages`, the global packages folder the assets file names (NUGET_PACKAGES's by default)."""
        packages = packages or self.packages
        folder = packages / 'hypercast' / '1.0.0'
        folder.mkdir(parents=True, exist_ok=True)
        text = (SCRIPTS / 'fixtures' / 'origin' / 'csharp' / f'{metadata}.nupkg.metadata').read_text()
        (folder / '.nupkg.metadata').write_text(text.replace('@ROOT@', self.work.as_posix())
                                                .replace('@CONTENT_HASH@', self.record['content_hash']))
        (folder / 'hypercast.1.0.0.nupkg').write_bytes(nupkg or (self.src / self.record['nupkg']).read_bytes())
        obj = self.work / 'csharp' / 'HyperTabular' / 'obj'
        obj.mkdir(parents=True, exist_ok=True)
        text = assets or (SCRIPTS / 'fixtures' / 'origin' / 'csharp' / 'project.assets.json').read_text()
        (obj / 'project.assets.json').write_text(text.replace('@NUGET_PACKAGES@', packages.as_posix() + '/')
                                                 .replace('@CONTENT_HASH@', self.record['content_hash']))

    def csharp(self, step, extra=''):
        project = f' "{self.work}/csharp/HyperTabular/HyperTabular.csproj"' if step == 'origin' else ''
        return self.run_sh(f'companion_csharp "{self.src}" "{self.work}" {step}{project}{extra}')

    def test_csharp_wire_writes_the_hosts_config_for_the_work_feed(self):
        import companion_wire
        self.host('csharp')
        result = self.csharp('wire')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        feed = self.work / '.companions' / 'nuget'
        self.assertEqual((feed / 'HyperCast.1.0.0.nupkg').read_bytes(), (self.src / self.record['nupkg']).read_bytes())
        self.assertEqual((self.work / 'nuget.config').read_text(),
                         companion_wire._nuget_config_text(feed.as_posix(), 'HyperCast'))

    def test_csharp_origin_pinned_passes(self):
        self.host('csharp')
        self.csharp('wire')
        self.dotnet()
        self.restored()
        result = self.csharp('origin', ' -r linux-musl-x64 -p:PublishAot=true')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'HyperCast 1.0.0 from {self.work}/.companions/nuget at', result.stdout)
        self.assertEqual(self.calls.read_text(), f'dotnet restore {self.work}/csharp/HyperTabular/HyperTabular.csproj '
                                                 '-r linux-musl-x64 -p:PublishAot=true\n')

    def test_csharp_origin_registry_copy_is_rejected(self):
        self.host('csharp')
        self.csharp('wire')
        self.dotnet()
        self.restored('registry', b'nuget.org bytes')
        result = self.csharp('origin')
        self.assertEqual(result.returncode, 1)
        self.assertIn("nuget.org's copy (source https://api.nuget.org/v3/index.json), expected HyperCast 1.0.0 from "
                      f"{self.work}/.companions/nuget (the pin, contentHash {self.record['content_hash']})", result.stdout)
        shutil.rmtree(self.packages)
        result = self.csharp('origin')
        self.assertEqual(result.returncode, 1)
        self.assertIn('.nupkg.metadata: restore extracted no HyperCast 1.0.0', result.stdout)

    def test_csharp_tampered_hash_is_rejected(self):
        import hashlib
        self.host('csharp')
        self.csharp('wire')
        self.dotnet()
        self.restored(nupkg=b'another package')
        result = self.csharp('origin')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'with sha512 {hashlib.sha512(b"another package").hexdigest()}', result.stdout)
        (self.src / self.record['nupkg']).write_bytes(b'swapped')
        (self.work / 'nuget.config').unlink()
        result = self.csharp('wire')
        self.assertEqual(result.returncode, 1)
        self.assertIn('with sha512 ', result.stdout)
        self.assertFalse((self.work / 'nuget.config').exists())

    def test_csharp_origin_reads_the_folder_restore_used(self):
        # A RestorePackagesPath (or a nuget.config globalPackagesFolder) moves the global folder
        # away from NUGET_PACKAGES: the assets file's packageFolders is where restore put it.
        self.host('csharp')
        self.csharp('wire')
        self.dotnet()
        elsewhere = self.base / 'restore-path'
        self.restored(packages=elsewhere)
        result = self.csharp('origin')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'at {elsewhere}/hypercast/1.0.0', result.stdout)
        self.assertIn(f'(the assets file\'s global packages folder, not NUGET_PACKAGES {self.packages})', result.stdout)

    def test_csharp_origin_ties_the_sha512_to_the_companions_entry(self):
        # Another library carries the pin's hash; HyperCast's own entry restored other bytes.
        self.host('csharp')
        self.csharp('wire')
        self.dotnet()
        text = (SCRIPTS / 'fixtures' / 'origin' / 'csharp' / 'project.assets.json').read_text()
        text = text.replace('"sha512": "@CONTENT_HASH@"', '"sha512": "b3RoZXI="', 1)
        text = text.replace('  "libraries": {\n', '  "libraries": {\n    "Other/1.0.0": {\n      "sha512": '
                            '"@CONTENT_HASH@",\n      "type": "package"\n    },\n', 1)
        self.restored(assets=text)
        result = self.csharp('origin')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('restoring HyperCast 1.0.0 with sha512 b3RoZXI=', result.stdout)
        # No assets file at all: restore wrote nothing to prove.
        (self.work / 'csharp' / 'HyperTabular' / 'obj' / 'project.assets.json').unlink()
        result = self.csharp('origin')
        self.assertEqual(result.returncode, 1)
        self.assertIn('project.assets.json: restore wrote no assets file', result.stdout)

    def test_csharp_without_env_does_nothing(self):
        self.host('csharp')
        (self.src / '.companions' / 'env').unlink()
        self.dotnet()
        for step in ('wire', 'origin'):
            result = self.csharp(step)
            self.assertEqual((result.returncode, result.stdout), (0, ''))
        self.assertFalse((self.work / 'nuget.config').exists())
        self.assertFalse(self.calls.exists())


@unittest.skipUnless(shutil.which('node') and shutil.which('npm'), 'needs node and npm')
class CompanionNpmContainerTests(unittest.TestCase):
    """companion_npm (companion_container.sh, companion_npm.mjs) with the real node and npm, on a
    consumer the host wired with real npm: the copy under <work> rewired, installed, proven."""

    def setUp(self):
        import tempfile
        from test_companion_wire import ADDON, NPM_ENV, checkout, npm_stage, quiet
        import companion_wire
        quiet(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.src = npm_stage(checkout(base / 'src'), ADDON)
        source = self.src / '.companions' / 'hypercast' / 'typescript'
        for name in json.loads((source / 'package.json').read_text())['files']:
            if name != 'package.json':
                (source / name).write_text(f'// {name}\n')
        consumer = self.src / 'typescript'
        consumer.mkdir()
        (consumer / 'package.json').write_text(json.dumps({
            'name': '@skunkwerkx/hypertabular', 'version': '1.0.0', 'peerDependencies': {PACKAGE: '^1.1.0'},
            'devDependencies': {PACKAGE: '1.1.0'}}, indent=2) + '\n')
        (consumer / 'package-lock.json').write_text(json.dumps({
            'name': '@skunkwerkx/hypertabular', 'version': '1.0.0', 'lockfileVersion': 3, 'requires': True,
            'packages': {'': {'name': '@skunkwerkx/hypertabular', 'version': '1.0.0'}}}, indent=2) + '\n')
        (self.src / '.companions' / 'env').write_text(''.join(f'{k}={v}\n' for k, v in NPM_ENV.items()))
        self.environ = {**os.environ, 'npm_config_cache': str(base / 'npm-cache'), 'npm_config_offline': 'true',
                        'npm_config_userconfig': str(base / 'npmrc'), 'npm_config_update_notifier': 'false'}
        with mock.patch.dict(os.environ, self.environ):
            companion_wire.wire_npm(self.src, NPM_ENV, environ={})
        # The container's copy, as suite-musl.sh's node branch takes it: the package directory alone.
        self.work = base / 'work'
        shutil.copytree(consumer, self.work / 'typescript')
        self.dir = self.work / 'typescript'

    def run_sh(self, call):
        import subprocess
        script = f'. "{WORKFLOWS.parent / "scripts" / "companion_container.sh"}"\n{call}\n'
        return subprocess.run(['sh', '-euc', script], env=self.environ, capture_output=True, text=True)

    def npm(self, step, *flags):
        return self.run_sh(f'companion_npm "{self.src}" "{self.work}" "{WORKFLOWS.parent / "scripts"}" '
                           f'"{self.dir}" {step} {" ".join(flags)}')

    def npm_ci(self):
        import subprocess
        return subprocess.run([shutil.which('npm'), 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=self.dir,
                              env=self.environ, capture_output=True, text=True)

    def test_npm_wires_then_proves_inside(self):
        result = self.npm('wire')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tarball = self.work / '.companions' / 'hypercast-npm' / 'skunkwerkx-hypercast-1.0.0.tgz'
        manifest = json.loads((self.dir / 'package.json').read_text())
        self.assertEqual(manifest['devDependencies'][PACKAGE], f'file:{tarball}')
        self.assertIn(f'npm ({self.dir}): {PACKAGE} 1.0.0 from file:../.companions/hypercast-npm/', result.stdout)
        installed = self.npm_ci()
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        result = self.npm('origin', '--installed')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('installed in node_modules with the pinned hypercast.darwin-arm64.node', result.stdout)

    def test_npm_unwired_copy_does_not_install(self):
        # The host's spec names a host path the container lacks: without the rewire, npm ci fails
        # (the build fails, the spec's npm control: no registry copy to fall back to).
        self.run_sh(f'companion_copy "{self.src}" "{self.work}" .companions')
        shutil.rmtree(self.src / '.companions' / 'hypercast-npm')
        installed = self.npm_ci()
        self.assertNotEqual(installed.returncode, 0)

    def test_npm_registry_copy_and_tampered_addon_are_rejected(self):
        self.assertEqual(self.npm('wire').returncode, 0)
        lock_path = self.dir / 'package-lock.json'
        lock = json.loads(lock_path.read_text())
        good = dict(lock['packages'][f'node_modules/{PACKAGE}'])
        registry = json.loads((WORKFLOWS.parent / 'scripts' / 'fixtures' / 'origin' / 'npm' / 'registry.json').read_text())
        lock['packages'][f'node_modules/{PACKAGE}'] = registry
        lock_path.write_text(json.dumps(lock))
        result = self.npm('origin')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::npm ({self.dir}): found {PACKAGE} 0.0.1 from {registry["resolved"]} (a registry), '
                      f'expected {PACKAGE} 1.0.0 from file:.companions/hypercast-npm/', result.stdout)
        lock['packages'][f'node_modules/{PACKAGE}'] = good
        lock_path.write_text(json.dumps(lock))
        self.assertEqual(self.npm_ci().returncode, 0)
        addon = self.dir / 'node_modules' / PACKAGE / 'hypercast.darwin-arm64.node'
        addon.write_bytes(b'a registry addon')
        result = self.npm('origin', '--installed')
        self.assertEqual(result.returncode, 1)
        self.assertIn('hypercast.darwin-arm64.node with sha256', result.stdout)

    def test_npm_tarball_without_the_listed_addon_is_rejected(self):
        # The tarball is the host's, byte for byte, but its addon is not the one SHA256SUMS lists.
        from test_companion_wire import NPM_ENV
        sums = self.src / NPM_ENV['HYPERCAST_ARTIFACTS'] / 'SHA256SUMS'
        sums.write_text(''.join(('0' * 64 + line[64:]) if 'hypercast.darwin-arm64.node' in line else line
                                for line in sums.read_text().splitlines(keepends=True)))
        result = self.npm('wire')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"found {PACKAGE}'s hypercast.darwin-arm64.node with sha256 ", result.stdout)
        self.assertIn("not SHA256SUMS' " + '0' * 64, result.stdout)

    def test_npm_unpack_installs_the_pinned_tarball(self):
        # Bun's image has no npm: the tarball itself is unpacked into node_modules, then proven.
        result = self.npm('unpack')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.dir / 'node_modules' / PACKAGE / 'hypercast.darwin-arm64.node').is_file())
        tarball = self.work / '.companions' / 'hypercast-npm' / 'skunkwerkx-hypercast-1.0.0.tgz'
        # A tarball that changed after the host packed it is refused before anything is unpacked.
        shutil.rmtree(self.dir / 'node_modules')
        (self.src / '.companions' / 'hypercast-npm' / tarball.name).write_bytes(b'tampered')
        result = self.npm('unpack')
        self.assertEqual(result.returncode, 1)
        self.assertIn("tarball with integrity sha512-", result.stdout)
        self.assertFalse((self.dir / 'node_modules').exists())

    def test_host_origin_with_real_node_and_npm(self):
        # The host's own proof (companion_origin.py npm --installed) with the real npm ci and the
        # real `node -p`, the package name passed as an argument.
        import subprocess
        import companion_origin
        from test_companion_wire import NPM_ENV
        consumer = self.src / 'typescript'
        installed = subprocess.run([shutil.which('npm'), 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                                   cwd=consumer, env=self.environ, capture_output=True, text=True)
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        sums = companion_origin.read_sums(self.src, NPM_ENV)
        with mock.patch.dict(os.environ, self.environ):
            companion_origin.origin_npm(self.src, NPM_ENV, sums, installed=True)
            (consumer / 'node_modules' / PACKAGE / 'index.js').write_text('// a registry loader\n')
            with self.assertRaises(companion_origin.OriginError) as caught:
                companion_origin.origin_npm(self.src, NPM_ENV, sums, installed=True)
        self.assertIn('index.js unlike the tarball', caught.exception.found)

    def test_npm_without_artifacts_or_record_fails_by_name(self):
        # No *_ARTIFACTS line: refused before anything is copied (an empty path would copy the
        # whole checkout into <work>).
        env = self.src / '.companions' / 'env'
        full = env.read_text()
        env.write_text(''.join(line for line in full.splitlines(keepends=True) if '_ARTIFACTS=' not in line))
        result = self.npm('wire')
        self.assertEqual(result.returncode, 1)
        self.assertIn('has no HYPERCAST_ARTIFACTS: the host never staged the leg\'s artifacts', result.stdout)
        self.assertFalse((self.work / '.companions').exists())
        env.write_text(full)
        # No wiring record, or no SHA256SUMS: an ::error:: naming the file, not a stack trace.
        for missing in (self.src / '.companions' / 'hypercast-npm' / 'wired.json',
                        self.src / '.companions' / 'hypercast-artifacts' / 'SHA256SUMS'):
            saved = missing.read_bytes()
            missing.unlink()
            shutil.rmtree(self.work / '.companions', ignore_errors=True)
            result = self.npm('origin')
            self.assertEqual(result.returncode, 1)
            relative = missing.relative_to(self.src).as_posix()
            self.assertIn(f'::error::npm ({self.dir}): found no {relative}', result.stdout)
            self.assertNotIn('    at ', result.stdout + result.stderr)
            missing.write_bytes(saved)

    def test_npm_installed_package_is_the_tarball_whole(self):
        self.assertEqual(self.npm('wire').returncode, 0)
        self.assertEqual(self.npm_ci().returncode, 0)
        installed = self.dir / 'node_modules' / PACKAGE
        for change, found in (
                (lambda: (installed / 'index.js').write_text('// a registry loader\n'),
                 f'node_modules/{PACKAGE}/index.js unlike the tarball\'s package/index.js'),
                (lambda: (installed / 'extra.js').write_text('// extra\n'),
                 f'node_modules/{PACKAGE}/extra.js, which the tarball does not hold'),
                (lambda: (installed / 'binding.js').unlink(),
                 f'node_modules/{PACKAGE} without the tarball\'s package/binding.js'),
                (lambda: (installed / 'package.json').write_text(json.dumps({'name': PACKAGE, 'version': '0.0.1'})),
                 f'{PACKAGE} 0.0.1 installed (node_modules/{PACKAGE}/package.json)')):
            shutil.rmtree(self.dir / 'node_modules')
            self.assertEqual(self.npm_ci().returncode, 0)
            self.assertEqual(self.npm('origin', '--installed').returncode, 0)
            change()
            result = self.npm('origin', '--installed')
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(f'found {found}', result.stdout)

    def test_npm_without_env_does_nothing_and_without_npm_req_fails(self):
        (self.src / '.companions' / 'env').unlink()
        result = self.npm('wire')
        self.assertEqual((result.returncode, result.stdout), (0, ''))
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n')
        result = self.npm('wire')
        self.assertEqual(result.returncode, 1)
        self.assertIn('has no *_NPM_REQ: the host never wired npm', result.stdout)


class SwiftCWorkflowTests(unittest.TestCase):
    """The Swift and C legs (companion-pins Task 14): fetched after the toolchain, each build
    proven right before it, and HEAD's commands untouched without a pin."""

    ACTIVE = "steps.companions.outputs.active == 'true'"
    ORIGIN = '"$FETCH_PYTHON" .hyperforge/.github/scripts/companion_origin.py'

    def test_containers_get_every_step_variable_their_script_reads(self):
        # A step's env reaches the runner's shell, not a container: each one a `docker run`'s
        # single-quoted script reads must be passed with `-e` (PR #11's review: hyper-core.yml's
        # pinned Alpine smoke handed companion_c an empty $RID).
        checked = 0
        for path in sorted(WORKFLOWS.glob('hyper-*.yml')):
            for job_name, job in (yaml_lite.load(path.read_text()).get('jobs') or {}).items():
                for step in job.get('steps') or []:
                    run, env = str(step.get('run') or ''), step.get('env') or {}
                    for args, script in re.findall(r"docker run ((?:[^'\n]|\\\n)*?)sh -c \\?\s*'([^']*)'", run):
                        passed = set(re.findall(r'-e ([A-Za-z_][A-Za-z0-9_]*)(?=[\s=])', args))
                        read = set(re.findall(r'\$\{?([A-Za-z_][A-Za-z0-9_]*)', script)) & set(env)
                        checked += 1
                        with self.subTest(workflow=path.name, job=job_name, step=step.get('name')):
                            self.assertEqual(read - passed, set())
        self.assertGreater(checked, 0)

    # HEAD 666a621's commands, by step, as for npm above: without a pin, every step but the fetch
    # and the steps gated on its `active` output being 'true' runs exactly these (the Alpine
    # smokes stay, under `!= 'true'`, with their commands unchanged). The C smoke jobs' Validate
    # step came with the C wiring (Task 14), with the Forge checkout the fetch needs.
    HEAD_RUNS = {
        ('hyper-test-swift.yml', 'test'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            "The image's Swift is the matrix's": 'ba7ac5c5707e3dc2',
            'Key the installed Swift toolchain (Windows)': '3e1367e780b4d0f6',
            'Test Swift bindings, the core linked in': '0ba113233a842b14',
            "Test Swift bindings — the pre-6.4 build system's link": '67c31c01d56a45d2',
            'Build the root Swift manifest (the one consumers resolve)': '3acfa58a89167e17'},
        ('hyper-test-c.yml', 'smoke'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Unpack the ${{ matrix.rid }} archive': 'ae394e0973865dba', 'Install pkg-config': '725fe1c1d16f662d',
            'Install Rosetta': '1447d6ed60266a1a', 'Smoke-test the ${{ matrix.rid }} archive': '9c7dd445d4412750',
            'Smoke-test the ${{ matrix.rid }} archive on Alpine': 'a3ab228748445b4e'},
        ('hyper-test-c.yml', 'smoke-apple-mobile'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Unpack the four Apple mobile archives': 'b9897113f6db802c', 'Install Rosetta': '1447d6ed60266a1a',
            'Boot an iPhone simulator': '5b4f619fde02fa48', 'Smoke-test the Apple mobile archives': '7a2996ebe86614ee'},
        ('hyper-core.yml', 'core'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65', 'Build native core (release)': '4b92aa994002d469',
            'glibc floor — nothing newer than GLIBC_2.34 is referenced': 'a66d3b4b6423d628',
            'Run Rust test suite': '103a96ae7a127d21', 'Collect native artifact': 'e4b91d74a3980fa3',
            "Name this RID's library and the smoke script": '07beacf56b8fe0f4',
            'Check the cdylib exports': '66655c13dfe148d3', 'Load it and call every export': '89ced316be54238b',
            'Install pkg-config': '725fe1c1d16f662d', 'Smoke-test the C archive': 'bbe84c5448ff3a62'},
        ('hyper-core.yml', 'core-musl'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            "Name this RID's library and the smoke script": '07beacf56b8fe0f4',
            'Check the cdylib exports': 'ae601323b831a4b3', 'Load it and call every export in Alpine': '22fb9298a778e3a1',
            'Smoke-test the C archive': '1f8a7e57c92a17a5'},
        ('hyper-core.yml', 'core-osx-x64'): {
            'Validate Forge workflow revision': 'b43ed1cffc80ac65',
            'Make sure Rosetta 2 is there to run the x86_64 tests': '5abbd670eab7dbed',
            'Build native core for x86_64 (release)': 'd1f6c124e9a8f046',
            'The library is x86_64, and nothing else': '648da80c4e624615',
            'Run Rust test suite on x86_64, under Rosetta': 'c8eeeeb5b921d588',
            'Collect native artifact': '6fad91e9d15ba9b2',
            "Name this RID's library and the smoke script": '61487d2a266290fb',
            'Check the cdylib exports': '3588e83842973430',
            'Load it and call every export, under Rosetta': '6ebede69be682810',
            'Install pkg-config': 'd8075aac86ffa841', 'Smoke-test the C archive': '5f1d8a6773f14d38'},
    }

    def steps(self, workflow, job_name):
        return yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][job_name]['steps']

    @staticmethod
    def index(steps, name):
        return next(i for i, s in enumerate(steps) if s.get('name') == name)

    def test_without_a_pin_the_swift_and_c_legs_run_heads_commands(self):
        import hashlib
        for (workflow, job_name), runs in self.HEAD_RUNS.items():
            with self.subTest(leg=f'{workflow}:{job_name}'):
                ungated = {s.get('name'): hashlib.sha256(s['run'].encode()).hexdigest()[:16]
                           for s in self.steps(workflow, job_name)
                           if 'run' in s and self.ACTIVE not in str(s.get('if', ''))}
                self.assertEqual(ungated, runs)

    def proof(self, steps, before, name, condition, run, env=None):
        at = self.index(steps, before)
        proof = steps[at - 1]
        self.assertEqual((proof.get('name'), proof.get('if'), proof.get('run', '').strip()), (name, condition, run))
        self.assertEqual(without_fetch_python(proof), env)
        self.assertIsNone(proof.get('working-directory'))
        self.assertNotIn('uses', proof)

    def test_swift_leg_sets_up_swift_first_and_proves_before_each_build(self):
        steps = self.steps('hyper-test-swift.yml', 'test')
        fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
        self.assertEqual(steps[fetch]['id'], 'companions')
        self.assertEqual(steps[fetch]['with']['leg'], 'hyper-test-swift.yml:test:${{ matrix.rid }}')
        # The fetch's own proof runs `swift package show-dependencies`: every leg's Swift is
        # there first (the image's, checked, or Windows' installed one).
        self.assertLess(self.index(steps, "The image's Swift is the matrix's"), fetch)
        setup = [i for i, s in enumerate(steps) if str(s.get('uses', '')).startswith('SwiftyLab/setup-swift@')]
        self.assertEqual(len(setup), 1)
        self.assertLess(setup[0], fetch)
        self.assertLess(self.index(steps, 'Save the installed Swift toolchain (Windows)'), fetch)
        swift = f'{self.ORIGIN} swift --root .'
        self.proof(steps, 'Test Swift bindings, the core linked in', 'The suite takes the pinned companion',
                   self.ACTIVE, f'{swift} --dir swift --installed')
        self.proof(steps, "Test Swift bindings — the pre-6.4 build system's link",
                   "The pre-6.4 build system's link takes the pinned companion",
                   f"matrix.rid == 'win-x64' && {self.ACTIVE}", f'{swift} --dir swift --installed --build-system native')
        self.proof(steps, 'Build the root Swift manifest (the one consumers resolve)',
                   'The root manifest takes the pinned companion', self.ACTIVE, f'{swift} --dir . --installed')
        # The static suites run after the host's proofs, and prove again in their container.
        first = min(i for i, s in enumerate(steps) if str(s.get('uses', '')).endswith('/swift-static-suite'))
        self.assertLess(fetch, first)

    def test_legacy_static_workflow_fetches_the_companions(self):
        # hyper-test-swift-static.yml is no Hyper* board's, but a consumer calling it with a block
        # would otherwise test the remote package: the suite's container helper fires only on the
        # .companions/env the fetch writes.
        import companion_map
        steps = self.steps('hyper-test-swift-static.yml', 'test-swift-static')
        fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
        leg = steps[fetch]['with']['leg']
        self.assertEqual(leg, 'hyper-test-swift-static.yml:test-swift-static')
        self.assertEqual(list(companion_map.ecosystems_for(companion_map.parse_leg(leg))), ['swift'])
        forge = next(i for i, s in enumerate(steps) if (s.get('with') or {}).get('path') == '.hyperforge')
        suites = [i for i, s in enumerate(steps) if str(s.get('uses', '')).endswith('/swift-static-suite')]
        self.assertEqual(len(suites), 2)
        self.assertLess(forge, fetch)
        self.assertLess(fetch, min(suites))

    def test_swift_static_suite_wires_and_proves_in_its_container(self):
        text = (ACTIONS / 'swift-static-suite' / 'suite-swift-static.sh').read_text()
        self.assertIn('/../../scripts" && pwd)/companion_container.sh', text)
        self.assertIn('-v "$companion_sh:/companion.sh:ro"', text)
        for before, after in (
                ('| tar -xf - -C /work\n', '    . /companion.sh\n    companion_swift /src /work /work/swift '
                 '/work/swift/StaticSmokeTest\n'),
                ('companion_swift /src /work /work/swift /work/swift/StaticSmokeTest\n', '[ -z "$STATIC_FILE" ]'),
                ('companion_swift_link /work /work/swift/StaticSmokeTest "$@"\n', '      swift build "$@"\n'),
                ('companion_swift_link /work /work/swift --build-tests\n', '          cd /work/swift && swift test\n'),
                ('companion_swift_link /work /work/swift --build-tests --swift-sdk "$wasm_sdk"\n',
                 '          cd /work/swift && swift test --swift-sdk "$wasm_sdk"\n')):
            with self.subTest(before=before):
                self.assertEqual(text.count(before), 1)
                self.assertLess(text.index(before), text.index(after))

    def test_c_legs_prove_the_origin_right_before_each_smoke(self):
        # --rid: the archive the smoke builds against, whose package CMake must choose.
        c = f'{self.ORIGIN} c --root . --installed --rid "$RID"'
        rid = {'RID': '${{ matrix.rid }}'}
        smoke = self.steps('hyper-test-c.yml', 'smoke')
        mobile = self.steps('hyper-test-c.yml', 'smoke-apple-mobile')
        for steps, leg in ((smoke, 'hyper-test-c.yml:smoke:${{ matrix.rid }}'),
                           (mobile, 'hyper-test-c.yml:smoke-apple-mobile')):
            fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
            self.assertEqual((steps[fetch]['id'], steps[fetch]['with']['leg']), ('companions', leg))
        # One host proof covers the runner's smoke and, before its own in the container, Alpine's.
        self.proof(smoke, 'Smoke-test the ${{ matrix.rid }} archive', 'The smoke takes the pinned companion',
                   f"${{{{ steps.unpack.outputs.skip != 'true' && {self.ACTIVE} }}}}", c, rid)
        self.proof(mobile, 'Smoke-test the Apple mobile archives', 'The smoke takes the pinned companion',
                   self.ACTIVE, f'{self.ORIGIN} c --root . --installed --rid ios-arm64 --rid iossimulator-arm64 '
                                '--rid maccatalyst-arm64 --rid maccatalyst-x64')
        gate = '${{ inputs.c_smoke && inputs.static_libs && ' + self.ACTIVE + ' }}'
        for job_name in ('core', 'core-musl', 'core-osx-x64'):
            with self.subTest(job=job_name):
                steps = self.steps('hyper-core.yml', job_name)
                fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
                self.assertEqual(steps[fetch]['id'], 'companions')
                if job_name == 'core-osx-x64':
                    self.proof(steps, 'Smoke-test the C archive', 'The smoke takes the pinned companion', gate,
                               f'{self.ORIGIN} c --root . --installed --rid osx-x64')
                else:
                    self.proof(steps, 'Smoke-test the C archive', 'The smoke takes the pinned companion', gate, c, rid)

    def test_alpine_smokes_have_a_pinned_twin(self):
        # The host's <NAME>_PACKAGES names a runner path: in the container, companion_c sets it
        # to the /work one and proves it there. The twin is the original's command and nothing
        # else, but for that call and the rid it needs, in the step's env and passed into the
        # container (`-e RID`; core-musl's env had it, its container didn't: PR #11's review).
        source = '. /work/.hyperforge/.github/scripts/companion_container.sh && companion_c /work "$RID" && '
        for workflow, job_name, original, extra in (
                ('hyper-test-c.yml', 'smoke', 'Smoke-test the ${{ matrix.rid }} archive on Alpine',
                 {'RID': '${{ matrix.rid }}'}),
                ('hyper-core.yml', 'core-musl', 'Smoke-test the C archive', {})):
            with self.subTest(job=f'{workflow}:{job_name}'):
                steps = self.steps(workflow, job_name)
                at = self.index(steps, original)
                first, twin = steps[at], steps[at + 1]
                self.assertEqual(twin['name'], original + ', the companion pinned')
                condition = str(first['if']).removeprefix('${{ ').removesuffix(' }}')
                self.assertTrue(condition.endswith(" && steps.companions.outputs.active != 'true'"), condition)
                base = condition.removesuffix(" && steps.companions.outputs.active != 'true'")
                self.assertEqual(twin['if'], f'${{{{ {base} && {self.ACTIVE} }}}}')
                self.assertEqual(twin['env'], {**first['env'], **extra})
                self.assertEqual(twin['env']['RID'], '${{ matrix.rid }}')
                run = first['run'].replace('-e SMOKE_CXX=1', '-e RID -e SMOKE_CXX=1')
                self.assertEqual(twin['run'], run.replace('&& bash c/smoke/run.sh', '&& ' + source + 'bash c/smoke/run.sh'))
                self.assertEqual(run.count('bash c/smoke/run.sh'), 1)


class CompanionSwiftCContainerTests(unittest.TestCase):
    """companion_swift, companion_swift_link and companion_c under sh, with a recorded swift
    stand-in and the consumer's own c/smoke/hypercast.sh under bash."""

    def setUp(self):
        import hashlib
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.src, self.work, self.bin = base / 'src', base / 'work', base / 'bin'
        self.bin.mkdir()
        (self.work / 'swift' / 'StaticSmokeTest').mkdir(parents=True)
        pin = self.src / '.companions' / 'hypercast'
        files = {'swift/HyperCastCore.artifactbundle/info.json': b'{}',
                 'swift/HyperCastCore.artifactbundle/x86_64-unknown-linux-gnu/libhypercast.a': b'pinned archive',
                 'swift/HyperCastCoreApple.xcframework/Info.plist': b'<plist/>'}
        for relative, data in {**files, 'Package.swift': b'// pin\n', 'swift/Sources/HyperCast/a.swift': b'',
                               'swift/.build/stale': b'', 'rust/Cargo.toml': b''}.items():
            (pin / relative).parent.mkdir(parents=True, exist_ok=True)
            (pin / relative).write_bytes(data)
        (self.src / '.companions' / 'hypercast-swift.sums').write_text(
            ''.join(f'{hashlib.sha256(d).hexdigest()}  {r}\n' for r, d in sorted(files.items())))
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n'
                                                      'HYPERCAST_VERSION=1.1.0\n'
                                                      'HYPERCAST_SWIFT_REQ=.companions/hypercast\n'
                                                      'HYPERCAST_C_REQ=.companions/hypercast-artifacts/c-packages-ci\n')
        self.pin = self.work / '.companions' / 'hypercast'
        self.calls = base / 'calls'
        stub = self.bin / 'swift'
        stub.write_text('#!/bin/sh\necho "swift $* in $PWD" >> "$CALLS"\n'
                        'case "$1" in build) printf "%s\\n" "$FAKE_BUILD"; exit "${FAKE_CODE:-0}" ;; esac\n'
                        'printf "%s\\n" "$FAKE_OUT"\n')
        stub.chmod(0o755)
        # The images' sha256sum; macOS's own is not on this PATH.
        checker = self.bin / 'sha256sum'
        checker.write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
        checker.chmod(0o755)
        bash = shutil.which('bash')
        (self.bin / 'bash').symlink_to(bash)
        # The images' cmake (the C smoke's own), where this host has one.
        if shutil.which('cmake'):
            (self.bin / 'cmake').symlink_to(shutil.which('cmake'))

    def run_sh(self, call, out='', build='', code=0):
        import subprocess
        script = f'. "{SCRIPTS / "companion_container.sh"}"\n{call}\n'
        env = {'PATH': f'{self.bin}:/usr/bin:/bin', 'CALLS': str(self.calls), 'HOME': self.tmp.name,
               'FAKE_OUT': out, 'FAKE_BUILD': build, 'FAKE_CODE': str(code),
               'TMPDIR': os.environ.get('TMPDIR', '/tmp')}
        return subprocess.run(['sh', '-euc', script], env=env, capture_output=True, text=True)

    def graph(self, path, url=None, version='unspecified'):
        # As SwiftPM prints it (fixtures/origin/swift/), a key per line.
        return json.dumps({'identity': 'swift', 'name': 'HyperTabular', 'url': f'{self.work}/swift',
                           'version': 'unspecified', 'path': f'{self.work}/swift', 'dependencies': [
                               {'identity': 'hypercast', 'name': 'HyperCast', 'url': url or path, 'version': version,
                                'path': path, 'dependencies': []}]}, indent=2)

    def swift(self, **kwargs):
        return self.run_sh(f'companion_swift "{self.src}" "{self.work}" "{self.work}/swift" '
                           f'"{self.work}/swift/StaticSmokeTest"', **kwargs)

    def test_swift_pinned_passes(self):
        result = self.swift(out=self.graph(str(self.pin)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.pin / 'swift/HyperCastCore.artifactbundle/x86_64-unknown-linux-gnu/libhypercast.a')
                        .is_file())
        self.assertTrue((self.pin / 'Package.swift').is_file())
        # The pin's Package.swift and swift/ only, never its .build or the rest of its checkout.
        self.assertFalse((self.pin / 'rust').exists())
        self.assertFalse((self.pin / 'swift' / '.build').exists())
        calls = self.calls.read_text()
        for package in ('swift', 'swift/StaticSmokeTest'):
            self.assertIn(f'swift package show-dependencies --format json in {self.work}/{package}\n', calls)
        self.assertIn(f'Swift ({self.work}/swift): hypercast from {self.pin}', result.stdout)

    def test_swift_registry_copy_is_rejected(self):
        checkout = f'{self.work}/swift/.build/checkouts/HyperCast'
        result = self.swift(out=self.graph(checkout, 'https://github.com/SkunkWerkx/HyperCast', '1.0.0'))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::Swift ({self.work}/swift): found hypercast 1.0.0 from https://github.com/SkunkWerkx/HyperCast '
                      f'(at {checkout}), expected hypercast from {self.pin} (the pin, with the bundle and XCFramework '
                      'from swift-release-assets)', result.stdout)

    def test_swift_tampered_hash_is_rejected(self):
        (self.src / '.companions/hypercast/swift/HyperCastCore.artifactbundle/x86_64-unknown-linux-gnu/libhypercast.a'
         ).write_bytes(b'another archive')
        result = self.swift(out=self.graph(str(self.pin)))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::Swift ({self.work}): found {self.pin} differing from the host\'s record '
                      '(.companions/hypercast-swift.sums)', result.stdout)
        self.assertFalse(self.calls.exists())

    def test_swift_extra_file_in_a_tree_is_rejected(self):
        (self.src / '.companions/hypercast/swift/HyperCastCore.artifactbundle/stale.a').write_bytes(b'')
        result = self.swift(out=self.graph(str(self.pin)))
        self.assertEqual(result.returncode, 1)
        self.assertIn("differing from the host's record", result.stdout)

    def link(self, build):
        return self.run_sh(f'companion_swift "{self.src}" "{self.work}" "{self.work}/swift" && '
                           f'companion_swift_link "{self.work}" "{self.work}/swift" --build-tests',
                           out=self.graph(str(self.pin)), build=build)

    def test_swift_link_of_the_pin_passes(self):
        archive = f'{self.pin}/swift/HyperCastCore.artifactbundle/x86_64-unknown-linux-gnu/libhypercast.a'
        result = self.link(f'/usr/bin/swiftc -o a.out x.o {archive} -lc')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'swift build -v --build-tests in {self.work}/swift\n', self.calls.read_text())
        self.assertIn(f'Swift ({self.work}/swift): links {archive}', result.stdout)

    def test_swift_link_of_a_copy_of_the_pin_passes(self):
        # The pre-6.4 build system copies the archive beside the products and links it by name.
        debug = self.work / 'swift' / '.build' / 'x86_64-unknown-linux-gnu' / 'debug'
        debug.mkdir(parents=True)
        (debug / 'libhypercast.a').write_bytes(b'pinned archive')
        result = self.link(f'"/usr/bin/swiftc" -L "{debug}" -o a.out x.o -lhypercast')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'links {debug}/libhypercast.a', result.stdout)

    def test_swift_link_of_another_archive_is_rejected(self):
        other = self.work / 'swift' / '.build' / 'checkouts' / 'HyperCast' / 'libhypercast.a'
        other.parent.mkdir(parents=True)
        other.write_bytes(b'released archive')
        result = self.link(f'swiftc -o a.out {other}')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::Swift ({self.work}/swift): found {other} linked, expected hypercast from {self.pin}',
                      result.stdout)

    def test_swift_link_of_nothing_is_rejected(self):
        result = self.link('swiftc -o a.out x.o')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::Swift ({self.work}/swift): found a verbose build that linked no libhypercast.a',
                      result.stdout)

    def test_swift_link_build_failure_is_rejected(self):
        result = self.run_sh(f'companion_swift "{self.src}" "{self.work}" && '
                             f'companion_swift_link "{self.work}" "{self.work}/swift"', build='boom', code=3)
        self.assertEqual(result.returncode, 1)
        self.assertIn('found swift build failing (exit 3)', result.stdout)

    def test_swift_without_env_does_nothing_and_without_swift_req_fails(self):
        (self.src / '.companions' / 'env').unlink()
        for call in (f'companion_swift "{self.src}" "{self.work}" "{self.work}/swift"',
                     f'companion_swift_link "{self.src}" "{self.src}/swift"'):
            result = self.run_sh(call)
            self.assertEqual((result.returncode, result.stdout), (0, ''), call)
        self.assertFalse(self.calls.exists())
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n')
        result = self.run_sh(f'companion_swift "{self.src}" "{self.work}" "{self.work}/swift"')
        self.assertEqual(result.returncode, 1)
        self.assertIn('has no *_SWIFT_REQ: the host never wired Swift', result.stdout)

    def c_tree(self, requires='=1.1.0', rid='linux-musl-x64'):
        import hashlib
        root = self.src
        fixtures = SCRIPTS / 'fixtures' / 'wire' / 'c'
        (root / 'c' / 'smoke').mkdir(parents=True)
        shutil.copy(fixtures / 'c' / 'smoke' / 'hypercast.sh', root / 'c' / 'smoke' / 'hypercast.sh')
        (root / 'rust').mkdir()
        (root / 'rust' / 'Cargo.toml').write_text(
            f'[dependencies]\nhypercast = {{ version = "{requires}", default-features = false }}\n')
        from test_companion_origin import CMAKE_TREE
        tree = {**CMAKE_TREE, 'lib/cmake/hypercast/hypercastConfigVersion.cmake':
                CMAKE_TREE['lib/cmake/hypercast/hypercastConfigVersion.cmake'].replace(b'"1.0.0"', b'"1.1.0"')}
        files = {f'hypercast-1.1.0-{rid}/{relative}': data for relative, data in tree.items()}
        for relative, data in files.items():
            path = root / '.companions' / 'hypercast-c' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (root / '.companions' / 'hypercast-c.sums').write_text(
            ''.join(f'{hashlib.sha256(d).hexdigest()}  {r}\n' for r, d in sorted(files.items())))
        return root / '.companions' / 'hypercast-c'

    def c(self, rid='linux-musl-x64'):
        return self.run_sh(f'companion_c "{self.src}" {rid} && printf "PACKAGES=%s\\n" "${{HYPERCAST_PACKAGES:-}}"')

    @unittest.skipUnless(shutil.which('cmake'), 'no cmake')
    def test_c_pinned_passes(self):
        packages = self.c_tree()
        result = self.c()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'C (c/smoke/hypercast.sh, linux-musl-x64): hypercast 1.1.0 from {packages}/'
                      'hypercast-1.1.0-linux-musl-x64\n', result.stdout)
        self.assertIn(f'CMake (linux-musl-x64): find_package(hypercast 1.1.0) chose {packages}/'
                      'hypercast-1.1.0-linux-musl-x64/lib/cmake/hypercast, importing ', result.stdout)
        self.assertIn(f'PACKAGES={packages}\n', result.stdout)

    @unittest.skipUnless(shutil.which('cmake'), 'no cmake')
    def test_c_cmake_choosing_another_package_is_rejected(self):
        self.c_tree()
        other = Path(self.tmp.name) / 'installed'
        from test_companion_origin import CMAKE_TREE
        for relative, data in CMAKE_TREE.items():
            (other / relative).parent.mkdir(parents=True, exist_ok=True)
            (other / relative).write_bytes(data.replace(b'"1.0.0"', b'"1.1.0"'))
        result = self.run_sh(f'export hypercast_ROOT="{other}"; companion_c "{self.src}" linux-musl-x64')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::C (CMake, linux-musl-x64): found find_package(hypercast 1.1.0) choosing '
                      f'{other}/lib/cmake/hypercast, expected', result.stdout)

    def test_container_hashes_read_stdin(self):
        # GNU sha256sum and sha512sum escape a file name holding a backslash or newline and put
        # a `\` before the hash, so `sha512sum "$f" | cut -f1` gives `\<hash>` for such a path
        # (CI run 41570924946); a stream's name is `-`, never escaped. macOS's shasum, which the
        # shims here stand in with, doesn't escape, so only this pins it off Linux.
        text = (SCRIPTS / 'companion_container.sh').read_text()
        hashed = re.findall(r'\$\((sha(?:256|512)sum[^|\n]*\|)', text)
        self.assertEqual(len(hashed), 5)
        for line in hashed:
            with self.subTest(line=line):
                self.assertRegex(line, r'^sha(?:256|512)sum 2>/dev/null < "\$[^"]+" \|$')

    def test_c_cmake_probe_is_the_hosts(self):
        # One probe for both: the container's heredoc is companion_origin.py's _CMAKE_PROBE.
        import companion_origin
        text = (SCRIPTS / 'companion_container.sh').read_text()
        body = text.split("<<'COMPANION_CMAKE_PROBE'\n", 1)[1].split('COMPANION_CMAKE_PROBE\n', 1)[0]
        self.assertEqual(body, companion_origin._CMAKE_PROBE)

    def test_c_registry_copy_is_rejected(self):
        # Unwired, rust/Cargo.toml asks for the released version, which the pin's archives are not.
        packages = self.c_tree(requires='1.0.0')
        result = self.c()
        self.assertEqual(result.returncode, 1)
        self.assertIn('::error::C (c/smoke/hypercast.sh, linux-musl-x64): found a request for hypercast 1.0.0 '
                      f'(rust/Cargo.toml), expected hypercast 1.1.0 from {packages} (the pin\'s c-packages-ci, '
                      'unpacked)', result.stdout)

    def test_c_tampered_hash_is_rejected(self):
        packages = self.c_tree()
        (packages / 'hypercast-1.1.0-linux-musl-x64' / 'lib' / 'libhypercast.a').write_bytes(b'another')
        result = self.c()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'::error::C ({self.src}): found {packages} differing from the host\'s record '
                      '(.companions/hypercast-c.sums)', result.stdout)

    def test_c_rid_without_a_pinned_archive_is_rejected(self):
        packages = self.c_tree()
        result = self.c('linux-musl-arm64')
        self.assertEqual(result.returncode, 1)
        self.assertIn('::error::C (c/smoke/hypercast.sh, linux-musl-arm64): found the script failed (exit 1): '
                      f'smoke: no hypercast-1.1.0-linux-musl-arm64 under {packages}', result.stdout)

    def test_c_without_env_does_nothing_and_without_c_req_fails(self):
        (self.src / '.companions' / 'env').unlink()
        result = self.c()
        self.assertEqual((result.returncode, result.stdout), (0, 'PACKAGES=\n'))
        (self.src / '.companions' / 'env').write_text('HYPERCAST_SOURCE=.companions/hypercast\n')
        result = self.c()
        self.assertEqual(result.returncode, 1)
        self.assertIn('has no *_C_REQ: the host never wired C', result.stdout)



class RWorkflowTests(unittest.TestCase):
    """The R legs (companion-pins Task 15): the pin installed after R and the toolchain, before
    setup-r-dependencies; proven right before each suite; in release mode, the companion's
    GitHub Release tarball as a url:: reference; HEAD's steps untouched without a pin."""

    ACTIVE = "steps.companions.outputs.active == 'true'"
    RELEASE = "steps.companions.outputs.mode == 'release'"
    NOT_RELEASE = "steps.companions.outputs.mode != 'release'"
    SRD = 'r-lib/actions/setup-r-dependencies@f9a764fea8d5c63df6ef9a5c7795bf7deb5d7e05'
    PROOF = '"$FETCH_PYTHON" .hyperforge/.github/scripts/companion_origin.py r --root . --installed'
    FLOOR_INSTALL = '"$FETCH_PYTHON" .hyperforge/.github/scripts/companion_wire.py r --root .'
    PACKAGE = '"$FETCH_PYTHON" .hyperforge/.github/scripts/companion_gate.py --root . --r-package >> "$GITHUB_OUTPUT"'
    FLOOR = {'hyper-test-r.yml': "inputs.mode != 'pr' && matrix.rid == 'linux-x64' && inputs.r_floor_version != '' "
                                 "&& inputs.r_floor_version != inputs.r_version",
             'hyper-lint-r.yml': "inputs.lint_r && inputs.mode == 'pr' && inputs.r_floor_version != '' "
                                 "&& inputs.r_floor_version != inputs.r_version"}
    JOBS = {'hyper-test-r.yml': 'test', 'hyper-lint-r.yml': 'lint'}
    # HEAD 666a621's commands, by step, as for npm above: without a pin, every `run` step but the
    # ones gated on the fetch's outputs runs exactly these. The lint's Validate step came with
    # the R wiring, with the Forge checkout the fetch needs.
    HEAD_RUNS = {
        'hyper-test-r.yml': {'Validate Forge workflow revision': 'b43ed1cffc80ac65',
                             "Vendor the R package's core, crates and corpus": '0d38af4c60e4d15c'},
        'hyper-lint-r.yml': {'Validate Forge workflow revision': 'b43ed1cffc80ac65',
                             'Install the package for lintr': '37a1d3dadcfa6f8e', 'lintr': '859471b03caf5db6',
                             'cargo fmt --check': 'f734e50e77428b7d', 'clang-format --dry-run': '3ca522cb73ba0422'},
    }
    # HEAD 666a621's steps, whole (sorted JSON, so the lint's toolchain may move ahead of the
    # fetch): what is left once the companion steps go and the release-mode clause leaves each
    # setup-r-dependencies `if`.
    HEAD_STEPS = {'hyper-test-r.yml': '8fba8832f8ae11d5b3a2112c43461db4d7fd158745d14d6a1023bd03bdffcbe2',
                  'hyper-lint-r.yml': 'aba96a5f9358289d4116bdc16c76e612202edd088f4fabb99b42353e33589a51'}

    def steps(self, workflow):
        return yaml_lite.load((WORKFLOWS / workflow).read_text())['jobs'][self.JOBS[workflow]]['steps']

    @staticmethod
    def index(steps, name):
        return next(i for i, s in enumerate(steps) if s.get('name') == name)

    def companion_step(self, step):
        condition = str(step.get('if', ''))
        return (step.get('uses') == FETCH_USES or step.get('id') == 'companion_r'
                or self.ACTIVE in condition or self.RELEASE in condition)

    def headless(self, step):
        """The step as HEAD had it: the release-mode clause gone from its condition."""
        step = dict(step)
        condition = step.pop('if', None)
        if condition is not None:
            inner = condition.removeprefix('${{ ').removesuffix(' }}')
            braced = inner != condition
            inner = inner.removesuffix(f' && {self.NOT_RELEASE}')
            if inner != self.NOT_RELEASE:
                step['if'] = f'${{{{ {inner} }}}}' if braced else inner
        # The pin's cache-version (test_cache_keys_never_cross_modes) is the action's own '1'
        # without one: HEAD's step, which set none.
        if KEYED.fullmatch(str((step.get('with') or {}).get('cache-version', ''))):
            step['with'] = {k: v for k, v in step['with'].items() if k != 'cache-version'}
        if (step.get('with') or {}).get('install-pandoc') == PANDOC:
            step['with'] = {k: v for k, v in step['with'].items() if k != 'install-pandoc'}
        return step

    def test_with_a_pin_pandoc_is_decided_without_pak(self):
        # pak::pkg_deps("."), the action's own pandoc check, resolves from the repositories alone
        # and failed on a pinned companion CRAN does not have yet. With a pin the `pandoc` step
        # decides by DESCRIPTION, before every install, and each install takes its answer.
        for workflow in self.JOBS:
            with self.subTest(workflow=workflow):
                steps = self.steps(workflow)
                fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
                pandoc = next(i for i, s in enumerate(steps) if s.get('id') == 'pandoc')
                self.assertIn(self.ACTIVE, steps[pandoc]['if'])
                self.assertEqual(steps[pandoc]['shell'], 'Rscript {0}')
                self.assertIn('"rmarkdown" %in% deps', steps[pandoc]['run'])
                installs = [i for i, s in enumerate(steps) if str(s.get('uses', '')).startswith(self.SRD)]
                self.assertLess(fetch, pandoc)
                self.assertLess(pandoc, min(installs))
                for i in installs:
                    self.assertEqual(steps[i]['with'].get('install-pandoc'), PANDOC)

    def test_without_a_pin_the_r_legs_run_heads_commands(self):
        import hashlib
        for workflow, runs in self.HEAD_RUNS.items():
            with self.subTest(workflow=workflow):
                steps = self.steps(workflow)
                ungated = {s.get('name'): hashlib.sha256(s['run'].encode()).hexdigest()[:16] for s in steps
                           if 'run' in s and not self.companion_step(s)}
                self.assertEqual(ungated, runs)
                kept = [self.headless(s) for s in steps if not self.companion_step(s)]
                if workflow == 'hyper-lint-r.yml':
                    kept = [s for s in kept if s.get('name') != 'Validate Forge workflow revision'
                            and (s.get('with') or {}).get('path') != '.hyperforge']
                digest = hashlib.sha256(json.dumps(sorted(json.dumps(s, sort_keys=True) for s in kept)).encode())
                self.assertEqual(digest.hexdigest(), self.HEAD_STEPS[workflow])

    def test_the_fetch_follows_r_and_the_toolchain_and_precedes_every_install(self):
        import companion_map
        for workflow, job_name in self.JOBS.items():
            with self.subTest(workflow=workflow):
                steps = self.steps(workflow)
                fetch = next(i for i, s in enumerate(steps) if s.get('uses') == FETCH_USES)
                leg = steps[fetch]['with']['leg']
                self.assertEqual(steps[fetch]['id'], 'companions')
                self.assertEqual(leg, f'{workflow}:{job_name}' + (':${{ matrix.rid }}' if job_name == 'test' else ''))
                self.assertEqual(list(companion_map.ecosystems_for(companion_map.parse_leg(
                    leg.replace('${{ matrix.rid }}', 'linux-x64')))), ['r'])
                # The wiring runs R (R CMD INSTALL) and cargo (vendor.sh, the build): both first,
                # under no narrower condition than the fetch.
                for prefix in ('r-lib/actions/setup-r@', RUST_TOOLCHAIN):
                    first = next(i for i, s in enumerate(steps) if str(s.get('uses', '')).startswith(prefix))
                    self.assertLess(first, fetch, prefix)
                    self.assertIn(steps[first].get('if'), (None, steps[fetch].get('if')))
                installs = [i for i, s in enumerate(steps) if str(s.get('uses', '')).startswith(self.SRD)]
                self.assertEqual(len(installs), 4)
                self.assertLess(fetch, min(installs))

    def test_release_mode_takes_the_companions_github_release_tarball(self):
        for workflow in self.JOBS:
            with self.subTest(workflow=workflow):
                steps = self.steps(workflow)
                package = steps[self.index(steps, "The companion's R package, from its GitHub Release")]
                gate = 'inputs.lint_r && ' if workflow == 'hyper-lint-r.yml' else ''
                self.assertEqual((package['id'], package['if'], package['run'].strip()),
                                 ('companion_r', gate + self.RELEASE, self.PACKAGE))
                # Every setup-r-dependencies is a pair: HEAD's, outside release mode, and its twin
                # in release mode, which adds the url:: reference and nothing else.
                installs = [s for s in steps if str(s.get('uses', '')).startswith(self.SRD)]
                for head, twin in zip(installs[::2], installs[1::2]):
                    condition = head['if'].removeprefix('${{ ').removesuffix(' }}')
                    self.assertTrue(condition.endswith(self.NOT_RELEASE), condition)
                    self.assertEqual(twin['if'], head['if'].replace(self.NOT_RELEASE, self.RELEASE))
                    self.assertEqual(twin.get('env'), head.get('env'))
                    self.assertEqual(twin['with'], {**head['with'], 'extra-packages': head['with']['extra-packages']
                                                    + ' ${{ steps.companion_r.outputs.package }}'})

    def test_each_suite_is_proven_right_before_it_in_its_own_r(self):
        lint_env = {'R_LIBS': '${{ runner.temp }}/rlib'}
        for workflow, suites in (('hyper-test-r.yml', {'R CMD check the R package': ('The check takes the pinned '
                                                                                     'companion', self.ACTIVE, None)}),
                                 ('hyper-lint-r.yml', {'lintr': ('lintr takes the pinned companion',
                                                                 f'inputs.lint_r && {self.ACTIVE}', lint_env)})):
            steps = self.steps(workflow)
            floor = self.FLOOR[workflow]
            braced = workflow == 'hyper-test-r.yml'
            check = steps[self.index(steps, 'R CMD check the R package — the floor R')]
            suites['R CMD check the R package — the floor R'] = (
                'The floor check takes the pinned companion',
                (f'${{{{ {floor} && steps.r_vendor.conclusion == \'success\' && {self.ACTIVE} }}}}' if braced
                 else f"{floor} && steps.r_vendor.conclusion == 'success' && {self.ACTIVE}"), check['env'])
            for suite, (name, condition, env) in suites.items():
                with self.subTest(workflow=workflow, suite=suite):
                    proof = steps[self.index(steps, suite) - 1]
                    self.assertEqual((proof['name'], proof['if'], proof['run'].strip()), (name, condition, self.PROOF))
                    self.assertEqual(without_fetch_python(proof), env)
                    self.assertNotIn('working-directory', proof)
            # The floor R builds its own copy of the pin, into the floor's library, right after
            # it is set up and before its setup-r-dependencies.
            with self.subTest(workflow=workflow, step='floor install'):
                setup = max(i for i, s in enumerate(steps) if str(s.get('uses', '')).startswith('r-lib/actions/setup-r@'))
                install = steps[setup + 1]
                self.assertEqual((install['name'], install['run'].strip()),
                                 ('Install the pinned companion for the floor R', self.FLOOR_INSTALL))
                self.assertEqual(install['if'], f'${{{{ {floor} && {self.ACTIVE} }}}}' if braced
                                 else f'{floor} && {self.ACTIVE}')
                self.assertEqual(without_fetch_python(install), check['env'])
                self.assertTrue(str(steps[setup + 2].get('uses', '')).startswith(self.SRD))

if __name__ == '__main__':
    unittest.main()
