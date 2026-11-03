# Fixtures for test_companion_pin.py

Recorded from SkunkWerkx/HyperCast with read-only `gh api` calls, trimmed to the fields
`companion_pin.py` reads. The run fields are `--jq '{id,status,conclusion,event,name,display_title,path,head_sha,repository:{full_name:.repository.full_name}}'`.

Recorded:
- `run_pr.json`, `run_pr_jobs.json`: `repos/SkunkWerkx/HyperCast/actions/runs/41346771778` and its
  `/jobs` (115 jobs, `name` and `conclusion` only). A gated pull-request run: test-java and others skipped.
- `run_red.json`: schedule run 39245328402, conclusion `failure`.
- `run_other_workflow.json`: run 41346591124, the Dependency Graph workflow (path `dynamic/dependabot/update-graph`).
- `run_other_repo.json`: SkunkWerkx/HyperUuid run 41366901359 (a ci.yml run of a different repository).
- `compare_ahead.json`: `repos/SkunkWerkx/HyperCast/compare/facbda30...master`, `status` ahead; `commits` cut to the first three, `sha` and first message line.
- `pr_board_contents.json`: `contents/.github/scripts/pr_board.py?ref=master`, `encoding` and `content` only (carries the real `TAIL`).

Derived (no fully green dispatch or schedule run of the new shape exists yet; replace with
Task 18's real board run when it does):
- `run_green_dispatch_board.json`: `run_pr.json` with `id` 41400000001, `event` workflow_dispatch,
  `name` and `display_title` `CI purpose=board`.
- `run_green_dispatch_board_jobs.json`: `run_pr_jobs.json` with every job whose caller is in `TAIL`
  `skipped` and every other job `success` (the PR run had skipped test-java, test-typescript,
  test-android and others; the matrix legs and pack-c had mixed results).
- `compare_diverged.json`: `compare_ahead.json` with `status` diverged and `behind_by` 2 (every
  HyperCast branch head found was an ancestor of master, so a real diverged compare could not be recorded).

Tests build the release-dispatch, schedule and one-job-skipped variants from the derived files in code.

The derived board fixture (`run_green_dispatch_board_jobs.json`) must be replaced by the jobs of a
real `purpose=board` run once Task 18's run exists. Its `name` is the real workflow name
("CI — build native, build wasm, test every binding"); only `display_title` carries `purpose=`.
Real full boards skip inner jobs through their own `if:` (schedule run 40318063189 has
`check-core / check-core` and `check-core / check-corpus` skipped and concluded success), so
the check demands `pr-board` success and only `success` or `skipped` elsewhere.
