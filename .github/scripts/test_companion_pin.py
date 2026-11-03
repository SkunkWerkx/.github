import base64
import copy
import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import companion_pin as pin
import hyper_config

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'pin'
COMMIT = 'fcc0eccfd8229fc2f5dc5e0e0eb5768f424e966e'
OLD = 'a' * 40


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def tail_of_fixture():
    return pin.read_tail(load('pr_board_contents.json'))


def companion(commit=COMMIT, run=41400000001, repo='SkunkWerkx/HyperCast'):
    return hyper_config.Companion('hypercast', repo, commit, run)


def caller(job):
    return job['name'].split(' / ')[0].split(' (')[0]


class CheckRun(unittest.TestCase):
    def setUp(self):
        self.tail = tail_of_fixture()
        self.run = load('run_green_dispatch_board.json')
        self.jobs = load('run_green_dispatch_board_jobs.json')
        self.compare = load('compare_ahead.json')

    def check(self, run=None, jobs=None, compare=None, comp=None):
        pin.check_run(run or self.run, jobs or self.jobs, comp or companion(), compare or self.compare, self.tail)

    def fails(self, rule, **kw):
        with self.assertRaises(ValueError) as caught:
            self.check(**kw)
        self.assertIn(str(self.run['id']) if 'run' not in kw else str(kw['run']['id']), str(caught.exception))
        self.assertIn(rule, str(caught.exception))

    def release_jobs(self):
        return [{**j, 'conclusion': 'success'} for j in self.jobs]

    def test_tail_is_read_with_ast(self):
        self.assertEqual(self.tail[0], 'forge')
        self.assertEqual(len(self.tail), 12)
        self.assertEqual(self.tail[-1], 'stage-release')

    def test_board_dispatch_with_skipped_tail_passes(self):
        self.assertTrue(all(j['conclusion'] == 'skipped' for j in self.jobs if caller(j) in self.tail))
        self.check()

    def test_release_dispatch_with_green_tail_passes(self):
        run = {**self.run, 'display_title': 'CI purpose=release'}
        self.check(run=run, jobs=self.release_jobs())

    def test_purpose_is_read_from_display_title_with_the_real_workflow_name(self):
        self.assertEqual(self.run['name'], 'CI — build native, build wasm, test every binding')
        self.check()
        self.fails('tail job forge must be success', run={**self.run, 'display_title': 'CI purpose=release'})

    def test_schedule_with_skipped_tail_passes(self):
        run = {**self.run, 'event': 'schedule', 'name': 'CI', 'display_title': 'CI'}
        self.check(run=run)

    def test_identical_compare_passes(self):
        self.check(compare={**self.compare, 'status': 'identical'})

    def with_conclusion(self, prefix, conclusion):
        return [{**j, 'conclusion': conclusion} if j['name'].startswith(prefix) else j for j in self.jobs]

    def test_skipped_inner_board_job_passes(self):
        jobs = self.with_conclusion('test-python', 'skipped')
        self.assertEqual(next(j for j in jobs if j['name'] == 'pr-board')['conclusion'], 'success')
        self.check(jobs=jobs)

    def test_skipped_board_job_fails(self):
        # a top-level skip its gate wanted run turns pr-board red; the pin is refused on that
        self.fails('pr-board', jobs=self.with_conclusion('pr-board', 'failure'))

    def test_failed_board_job_fails(self):
        for conclusion in ('failure', 'cancelled', 'timed_out', 'action_required', 'neutral', 'stale', None):
            with self.subTest(conclusion=conclusion):
                self.fails('test-python', jobs=self.with_conclusion('test-python', conclusion))

    def test_pr_board_not_success_fails(self):
        for conclusion in ('skipped', 'failure'):
            with self.subTest(conclusion=conclusion):
                self.fails('pr-board', jobs=self.with_conclusion('pr-board', conclusion))

    def test_tail_skipped_on_release_dispatch_fails(self):
        run = {**self.run, 'display_title': 'CI purpose=release'}
        self.fails('tail job forge must be success', run=run)

    def test_tail_success_on_board_dispatch_fails(self):
        self.fails('tail job codeql must be skipped', jobs=self.release_jobs())

    def test_missing_tail_job_fails(self):
        jobs = [j for j in self.jobs if caller(j) != 'codeql']
        self.fails('codeql', jobs=jobs)

    def test_missing_pr_board_fails(self):
        jobs = [j for j in self.jobs if j['name'] != 'pr-board']
        self.fails('pr-board', jobs=jobs)

    def test_unfinished_or_red_run_fails(self):
        self.fails('completed/success', run=load('run_red.json'))
        self.fails('completed/success', run={**self.run, 'status': 'in_progress', 'conclusion': None})

    def test_gated_pull_request_run_fails(self):
        run, jobs = load('run_pr.json'), load('run_pr_jobs.json')
        with self.assertRaises(ValueError) as caught:
            pin.check_run(run, jobs, companion(run=run['id']), self.compare, self.tail)
        message = str(caught.exception)
        self.assertIn('41346771778', message)
        self.assertIn('gated run', message)
        self.assertIn('neither workflow_dispatch nor schedule', message)

    def test_dispatch_without_purpose_fails(self):
        run = {**self.run, 'name': 'CI', 'display_title': 'CI'}
        self.fails('purpose=board or purpose=release', run=run)

    def test_other_workflow_fails(self):
        run = {**self.run, 'path': '.github/workflows/other.yml'}
        self.fails('.github/workflows/ci.yml', run=run)
        self.fails('.github/workflows/ci.yml', run={**load('run_other_workflow.json'), 'id': self.run['id']})

    def test_other_repo_fails(self):
        self.fails('SkunkWerkx/HyperCast', run={**load('run_other_repo.json'), 'id': self.run['id'], 'event': 'workflow_dispatch',
                                                   'name': 'CI purpose=board', 'display_title': 'CI purpose=board', 'head_sha': COMMIT})

    def test_other_commit_fails(self):
        self.fails('head_sha', comp=companion(commit='b' * 40))

    def test_diverged_commit_fails(self):
        self.fails('default branch', compare=load('compare_diverged.json'))

    def test_matrix_leg_names_match_on_caller(self):
        jobs = copy.deepcopy(self.jobs) + [{'name': 'forge / resolve (x)', 'conclusion': 'skipped'}]
        self.check(jobs=jobs)
        jobs = copy.deepcopy(self.jobs) + [{'name': 'forge / resolve (x)', 'conclusion': 'success'}]
        self.fails('tail job forge must be skipped', jobs=jobs)


class Fetch(unittest.TestCase):
    def test_fetch_reads_run_jobs_compare_and_tail(self):
        jobs = load('run_green_dispatch_board_jobs.json')
        seen = []

        def api(repo, endpoint):
            seen.append(endpoint)
            self.assertEqual(repo, 'SkunkWerkx/HyperCast')
            if endpoint == 'actions/runs/41400000001':
                return load('run_green_dispatch_board.json')
            if endpoint.startswith('actions/runs/41400000001/jobs?'):
                page = int(endpoint.rsplit('page=', 1)[1])
                return {'total_count': len(jobs), 'jobs': jobs[(page - 1) * 100:page * 100]}
            if endpoint == f'compare/{COMMIT}...HEAD':
                return load('compare_ahead.json')
            if endpoint == f'contents/.github/scripts/pr_board.py?ref={COMMIT}':
                return load('pr_board_contents.json')
            raise AssertionError(endpoint)

        run, got_jobs, compare, tail = pin.fetch(companion(), api)
        self.assertEqual(got_jobs, jobs)
        self.assertEqual(len(jobs), 115)
        self.assertEqual(compare['status'], 'ahead')
        self.assertEqual(tail[-1], 'stage-release')
        pin.check_run(run, got_jobs, companion(), compare, tail)
        self.assertEqual(sum('jobs?' in s for s in seen), 2)

    def test_last_tail_assignment_wins(self):
        src = 'TAIL = ["a"]\nTAIL = ["b", "c"]\n'
        self.assertEqual(pin.read_tail({'encoding': 'base64', 'content': base64.b64encode(src.encode()).decode()}), ['b', 'c'])

    def test_unparsable_tail_source_is_a_value_error(self):
        with self.assertRaises(ValueError):
            pin.read_tail({'encoding': 'base64', 'content': base64.b64encode(b'TAIL = (').decode()})

    def test_api_failure_is_a_value_error_naming_the_run(self):
        def api(repo, endpoint):
            raise subprocess.CalledProcessError(1, ['gh', 'api'])
        with self.assertRaises(ValueError) as caught:
            pin.validate(companion(), api)
        self.assertIn('41400000001', str(caught.exception))

    def test_tail_parse_never_executes(self):
        src = 'import os\nTAIL = ["a", "b"]\nos.system("exit 1")\n'
        content = {'encoding': 'base64', 'content': base64.encodebytes(src.encode()).decode()}
        self.assertEqual(pin.read_tail(content), ['a', 'b'])
        bad = {'encoding': 'base64', 'content': base64.b64encode(b'TAIL = [x()]').decode()}
        with self.assertRaises(ValueError):
            pin.read_tail(bad)


HYPER = '''{
   "crate": "hypercast",
   "companions": {
      "hypercast": {"repo": "SkunkWerkx/HyperCast", "commit": "%s", "run": 11},
      "other": {"repo": "SkunkWerkx/Other", "commit": "%s", "run": 22}
   },
   "tail": [ 1,2 ]
}
'''


class Rewrite(unittest.TestCase):
    def test_rewrite_keeps_layout(self):
        text = HYPER % (OLD, 'c' * 40)
        new = pin.rewrite(text, 'hypercast', COMMIT, 41400000001)
        self.assertEqual(new, text.replace(OLD, COMMIT).replace('"run": 11', '"run": 41400000001'))
        self.assertEqual(json.loads(new)['companions']['other'], json.loads(text)['companions']['other'])

    def test_rewrite_touches_only_the_named_entry(self):
        text = HYPER % (OLD, OLD)  # the other entry carries the same commit
        new = pin.rewrite(text, 'hypercast', COMMIT, 5)
        self.assertEqual(json.loads(new)['companions']['other']['commit'], OLD)
        self.assertEqual(json.loads(new)['companions']['hypercast']['commit'], COMMIT)

    def test_rewrite_search_is_confined_to_the_companions_object(self):
        text = '{"hypercast": {"commit": "x", "run": 1}, "companions": {"hypercast": {"commit": "%s", "run": 2}}}' % OLD
        new = pin.rewrite(text, 'hypercast', COMMIT, 5)
        self.assertEqual(json.loads(new)['hypercast'], {'commit': 'x', 'run': 1})
        self.assertEqual(json.loads(new)['companions']['hypercast'], {'commit': COMMIT, 'run': 5})

    def test_rewrite_unknown_companion(self):
        with self.assertRaises(ValueError):
            pin.rewrite(HYPER % (OLD, 'c' * 40), 'nope', COMMIT, 5)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / '.github').mkdir()
        self.path = self.root / '.github' / 'hyper.json'

    def write(self, commit, run):
        self.path.write_text(HYPER % (commit, 'c' * 40), encoding='utf-8')

    def fetch_for(self, run, jobs, compare):
        tail = tail_of_fixture()
        return lambda comp, api=None: (run, jobs, compare, tail)

    def test_move_rewrites_and_prints_log(self):
        self.write(OLD, 11)
        fetched = self.fetch_for(load('run_green_dispatch_board.json'), load('run_green_dispatch_board_jobs.json'), load('compare_ahead.json'))
        def api(repo, endpoint):
            if endpoint == 'actions/runs/41400000001':
                return load('run_green_dispatch_board.json')
            self.assertEqual(endpoint, f'compare/{OLD}...{COMMIT}')
            return load('compare_ahead.json')

        with patch.object(pin, 'fetch', fetched), patch('builtins.print') as out:
            pin.main(['--root', str(self.root), '--companion', 'hypercast', '--run', '41400000001'], api)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data['companions']['hypercast']['commit'], COMMIT)
        self.assertEqual(data['companions']['hypercast']['run'], 41400000001)
        printed = '\n'.join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn(OLD, printed)
        self.assertIn('17781f4 Fix the osx floor', printed)

    def test_move_leaves_file_on_failure(self):
        self.write(OLD, 11)
        before = self.path.read_text(encoding='utf-8')
        fetched = self.fetch_for(load('run_pr.json'), load('run_pr_jobs.json'), load('compare_ahead.json'))
        with patch.object(pin, 'fetch', fetched), self.assertRaises(ValueError):
            pin.main(['--root', str(self.root), '--companion', 'hypercast', '--run', '41346771778'],
                     lambda repo, endpoint: load('run_pr.json'))
        self.assertEqual(self.path.read_text(encoding='utf-8'), before)

    def test_check_validates_every_pin_without_rewriting(self):
        self.write(COMMIT, 41400000001)
        before = self.path.read_text(encoding='utf-8')
        fetched = self.fetch_for(load('run_green_dispatch_board.json'), load('run_green_dispatch_board_jobs.json'), load('compare_ahead.json'))
        with patch.object(pin, 'fetch', fetched):
            with self.assertRaises(ValueError) as caught:  # "other" is pinned at a commit the run does not name
                pin.main(['--check', '--root', str(self.root)], None)
        self.assertIn('other', str(caught.exception))
        self.assertEqual(self.path.read_text(encoding='utf-8'), before)

    def test_check_passes_when_all_pins_are_green(self):
        self.path.write_text(HYPER.replace('"other"', '"zzz_unused"') % (COMMIT, COMMIT), encoding='utf-8')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        del data['companions']['zzz_unused']
        self.path.write_text(json.dumps(data), encoding='utf-8')
        data['companions']['hypercast']['run'] = 11
        fetched = self.fetch_for({**load('run_green_dispatch_board.json'), 'id': 11}, load('run_green_dispatch_board_jobs.json'), load('compare_ahead.json'))
        with patch.object(pin, 'fetch', fetched), patch('builtins.print') as out:
            self.assertEqual(pin.main(['--check', '--root', str(self.root)], None), 0)
        self.assertIn('green', out.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
