"""Validate a consumer's companion pin and move it.

A pin (`companions` in .github/hyper.json) names a commit of the companion repository and a
full-board run of its ci.yml at that commit. A pin is only good when that run is green in the
way a board run is: `pr-board`, the aggregate that turns red when a job its gate wanted run was
skipped, is `success`; every other job is `success` or `skipped` (a full board skips inner jobs
through their own `if:`); and the release tail is skipped on a board run (a schedule run or a
`purpose=board` dispatch) and `success` on a `purpose=release` dispatch. The tail list is read from the companion's own pr_board.py at the pinned
commit, so the Forge keeps no copy of it. The commit must also be on the companion's default
branch.

  companion_pin.py --root <consumer> --companion <name> --run <id>   validate, then move the pin
  companion_pin.py --check --root <consumer>                          validate every pin
"""
import argparse
import ast
import base64
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hyper_config  # noqa: E402
import release_guard  # noqa: E402

WORKFLOW = '.github/workflows/ci.yml'
BOARD_SCRIPT = '.github/scripts/pr_board.py'
PURPOSE = re.compile(r'purpose=(board|release)\b')


# A page of a run's jobs. GitHub answers 100 a page of a large run with a 502 every time (HyperCast
# run 41661544217, 139 jobs over three attempts, 2026-11-03) and serves 50; the paging reads any size.
JOBS_PER_PAGE = 50


def read_tail(contents):
    """TAIL from a contents-API response for pr_board.py: parsed with ast, never executed."""
    if contents.get('encoding') != 'base64':
        raise ValueError(f'{BOARD_SCRIPT}: unexpected contents encoding {contents.get("encoding")!r}')
    try:
        tree = ast.parse(base64.b64decode(contents['content']).decode('utf-8'))
    except (SyntaxError, ValueError) as error:
        raise ValueError(f'{BOARD_SCRIPT} could not be parsed: {error}') from None
    found = None
    for node in tree.body:  # the last assignment wins, as in Python
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TAIL' for t in node.targets):
            try:
                tail = ast.literal_eval(node.value)
            except ValueError:
                raise ValueError(f'{BOARD_SCRIPT}: TAIL is not a literal list of strings') from None
            if not isinstance(tail, list) or not tail or not all(isinstance(name, str) for name in tail):
                raise ValueError(f'{BOARD_SCRIPT}: TAIL is not a literal list of strings')
            found = tail
    if found is None:
        raise ValueError(f'{BOARD_SCRIPT} has no TAIL list')
    return found


def caller(name):
    """The caller job of a (possibly reusable-workflow or matrix) job name."""
    return name.split(' / ', 1)[0].split(' (', 1)[0]


def check_run(run, jobs, companion, compare, tail):
    """Raise ValueError naming the run and every rule it breaks."""
    run_id = run.get('id')
    broken = []
    if run.get('status') != 'completed' or run.get('conclusion') != 'success':
        broken.append(f'it is not completed/success (status {run.get("status")}, conclusion {run.get("conclusion")})')
    event = run.get('event')
    if event not in ('workflow_dispatch', 'schedule'):
        broken.append(f'event {event} is neither workflow_dispatch nor schedule: a {event} run is a gated run')
    purpose = None
    if event == 'workflow_dispatch':
        found = PURPOSE.search(f'{run.get("display_title")} {run.get("name")}')
        purpose = found.group(1) if found else None
        if purpose is None:
            broken.append('a dispatch whose name lacks purpose=board or purpose=release')
    if run.get('path') != WORKFLOW:
        broken.append(f'workflow path {run.get("path")} is not {WORKFLOW}')
    full_name = (run.get('repository') or {}).get('full_name')
    if full_name != companion.repo:
        broken.append(f'repository {full_name} is not {companion.repo}')
    if run.get('head_sha') != companion.commit:
        broken.append(f'head_sha {run.get("head_sha")} is not the pinned commit {companion.commit}')
    if compare.get('status') not in ('ahead', 'identical'):
        broken.append(f'the commit is not on the default branch (compare status {compare.get("status")}, need ahead or identical)')

    release = purpose == 'release'
    groups = {}
    for job in jobs:
        groups.setdefault(caller(job['name']), []).append(job)
    for name in tail:
        if name not in groups:
            broken.append(f'tail job {name} is missing')
    if 'pr-board' not in groups:
        broken.append('job pr-board is missing')
    want = 'success' if release else 'skipped'
    for name, group in groups.items():
        for job in group:
            result = job['conclusion']
            if name in tail:
                if result != want:
                    broken.append(f'tail job {name} must be {want} on a {"release dispatch" if release else "board run"}, was {result} ({job["name"]})')
            elif name == 'pr-board':
                if result != 'success':
                    broken.append(f'job pr-board is {result}, not success: the board is not green')
            elif result not in ('success', 'skipped'):
                broken.append(f'job {job["name"]} is {result}, neither success nor skipped')
    if broken:
        raise ValueError(f'run {run_id}: ' + '; '.join(broken))


def jobs_of(repo, run_id, api):
    jobs, page = [], 1
    while True:
        got = api(repo, f'actions/runs/{run_id}/jobs?per_page={JOBS_PER_PAGE}&page={page}')
        jobs += [{'name': j['name'], 'conclusion': j['conclusion']} for j in got['jobs']]
        if not got['jobs'] or len(jobs) >= got['total_count']:
            return jobs
        page += 1


def fetch(companion, api=release_guard.api):
    """(run, jobs, compare, tail) of a companion's pin, read-only."""
    run = api(companion.repo, f'actions/runs/{companion.run}')
    jobs = jobs_of(companion.repo, companion.run, api)
    # HEAD names the default branch; `repos/<repo>` itself is not reachable through api()
    compare = api(companion.repo, f'compare/{companion.commit}...HEAD')
    tail = read_tail(api(companion.repo, f'contents/{BOARD_SCRIPT}?ref={companion.commit}'))
    return run, jobs, compare, tail


def validate(companion, api=release_guard.api):
    try:
        run, jobs, compare, tail = fetch(companion, api)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'run {companion.run}: reading {companion.repo} failed: {error}') from None
    check_run(run, jobs, companion, compare, tail)


def rewrite(text, name, commit, run):
    """Replace only the named entry's `commit` and `run` values in the text, then re-parse and
    compare: nothing else may differ."""
    block = re.search(r'"companions"\s*:\s*\{', text)
    if not block:
        raise ValueError(f'no companions block in {hyper_config.CONFIG_PATH}')
    start, depth = block.end() - 1, 0
    for end in range(start, len(text)):
        depth += {'{': 1, '}': -1}.get(text[end], 0)
        if depth == 0:
            break
    entry = re.compile(rf'"{re.escape(name)}"\s*:\s*\{{[^{{}}]*\}}').search(text, start + 1, end)
    if not entry:
        raise ValueError(f'no companion {name} in {hyper_config.CONFIG_PATH}')
    body, count_commit = re.subn(r'("commit"\s*:\s*")[^"]*(")', lambda m: f'{m[1]}{commit}{m[2]}', entry.group(0))
    body, count_run = re.subn(r'("run"\s*:\s*)\d+', lambda m: f'{m[1]}{run}', body)
    if count_commit != 1 or count_run != 1:
        raise ValueError(f'companion {name} needs exactly one commit and one run value')
    new = text[:entry.start()] + body + text[entry.end():]
    expected = json.loads(text)
    expected['companions'][name].update(commit=commit, run=run)
    if json.loads(new) != expected:
        raise ValueError(f'rewriting companion {name} changed more than its commit and run')
    return new


def main(argv=None, api=release_guard.api):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--companion')
    parser.add_argument('--run', type=int)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    companions = hyper_config.load_companions(args.root)
    if args.check:
        failures = []
        for name, companion in companions.items():
            try:
                validate(companion, api)
            except ValueError as error:
                failures.append(f'{name}: {error}')
        if failures:
            raise ValueError('companion pins are not green:\n' + '\n'.join(failures))
        print(f'{len(companions)} companion pin(s) green')
        return 0
    if not args.companion or args.run is None:
        parser.error('--companion and --run are required unless --check')
    if args.companion not in companions:
        raise ValueError(f'no companion {args.companion} in {hyper_config.CONFIG_PATH}')
    old = companions[args.companion]
    run = api(old.repo, f'actions/runs/{args.run}')
    new = hyper_config.Companion(old.name, old.repo, run['head_sha'], args.run)
    run, jobs, compare, tail = fetch(new, api)
    check_run(run, jobs, new, compare, tail)
    path = args.root / hyper_config.CONFIG_PATH
    path.write_text(rewrite(path.read_text(encoding='utf-8'), old.name, new.commit, new.run), encoding='utf-8')
    log = api(old.repo, f'compare/{old.commit}...{new.commit}')
    print(f'{old.name}: {old.commit} (run {old.run}) -> {new.commit} (run {new.run})')
    for commit in log.get('commits', []):
        print(f'{commit["sha"][:7]} {commit["commit"]["message"].splitlines()[0]}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ValueError as error:
        sys.exit(str(error))
