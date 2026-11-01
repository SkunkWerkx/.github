# Companion pins implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HyperTabular's pull-request, schedule and `board` boards test against a pinned, proven HyperCast commit with nothing published, while a `release` dispatch still resolves HyperCast from the registries, after a gate proves it is live everywhere but CRAN.

**Architecture:** Each repo's `ci.yml` gains a `purpose` dispatch input that gates the release tail. In the Forge, a `companions` job holds a read-only GitHub App key and mirrors the pinned run's artifacts into the consumer's own run. A `fetch-companions` action in every board workflow wires each ecosystem to the pinned source and artifacts, then proves origin and integrity. In release mode the same job runs the registry gate instead.

**Tech Stack:** GitHub Actions (reusable workflows, composite actions), Python 3 standard library with `unittest` (the Forge's and the repos' test style; `yaml_lite.py` for workflow parsing), Bash, `actions/create-github-app-token`, and each binding's package manager: Cargo, Go, pip and micropip, npm, Bundler, Gradle, NuGet, Composer, SwiftPM, R.

**Spec:** `../specs/2026-11-01-companion-pins-design.md` (approved 2026-11-01). Read it first; this plan only adds the decisions it leaves open.

## Global Constraints

- The owner drives git. No task commits, pushes, tags, opens a PR or dispatches a workflow. Each task ends with its diff left in the working tree, verified, and reported. A step that needs git or a dispatch is marked **Owner**.
- Never stage anything; delete with plain `rm`.
- `docs/superpowers/` in HyperCast is the owner's in-flight v1.1 work: don't touch it.
- A verification counts only when its negative control fails. Copy the flags the repo's own CI step uses.
- Prove locally first, on this Mac plus Docker. The report names what only CI can prove: the App token's cross-repo download, fork and Dependabot behaviour, the Windows, Android, Apple mobile and s390x legs, and the live registry probes.
- Two "modes" exist, and they are different things:
  - The existing board input `mode: pr|release` means a gated board versus a full board. Leave it alone.
  - The new **companion mode** comes from `hyper_mode.py`: `release` exactly when `github.event_name == 'workflow_dispatch' && github.event.inputs.purpose == 'release'`; an error for a dispatch whose `purpose` is neither `board` nor `release`; `companion` for every other event.
  - No Forge file reads `inputs.purpose`.
- Pin block: `"companions": {"<crate>": {"repo": "owner/name", "commit": "<40 lowercase hex>", "run": <positive int>}}`, with no other keys.
- App names:
  - org variable `HYPER_COMPANION_APP_ID`;
  - secret `HYPER_COMPANION_APP_KEY` (an org Actions secret plus an org Dependabot secret of the same name);
  - token minted with `owner: SkunkWerkx`, `repositories:` the companion names, and `permission-actions: read`.
- Mirrored artifact: `companion-<name>`, holding the leg files, `SHA256SUMS` and `pin.json` (`{"repo","commit","run"}`).
- Working directory in a consumer checkout: `.companions/<name>/` (source), `.companions/<name>-artifacts/`, `.companions/env`, `.companions/m2/`, `.companions/nuget/`.
- Release tail jobs (from `test_workflow_contracts.py`'s `TAIL`): `forge` (its "needs the whole board" rule stays), `nuget-org-pack`, `crate-pack`, `maven-pack`, `ruby-pack`, `npm-pack`, `r-pack`, `c-pack`, `pypi-pack`, `release-prepare`, `codeql`, `stage-release`. Their gate becomes `github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master' && inputs.purpose == 'release'`.
- Forge tests: `python3 -m unittest discover -s .github/scripts -p 'test_*.py' -v`, plus `actionlint -shellcheck= -ignore 'property "workflow_(sha|repository)" is not defined'` (validate.yml's flags).
- Repo contract tests: `python3 -m unittest discover -s .github/scripts/tests -v` in each repo.
- Fix stale docs on sight. READMEs state only the current state; history goes in CHANGELOGs.

## Review Focus

1. **A release dispatch whose companion mode is computed inside a called workflow.** It must come out `release`, never `companion`. Pinned in Task 2 (`hyper_mode` fixtures) and Task 7 (a structural test that no Forge file reads `inputs.purpose`).
2. **A `pull_request` from a fork, or from Dependabot.** The `companions` job fails with its own message, and no leg falls back to a registry. Pinned in Task 7 (`test_missing_key_message`, `test_dependabot_secret_documented`).
3. **A pin edited by hand to a gated or red run, or whose commit isn't on master.** It must be refused before any download. Pinned in Task 5 (`companion_pin` fixtures) and Task 8 (`pin.json` against `hyper.json`).
4. **A leg where the override silently didn't take, with HyperCast 1.0.0 reachable on its registry.** The origin check must fail. Pinned per ecosystem in Tasks 9–15 (each has a `*_registry_copy_is_rejected` control).
5. **A cache restored across modes.** A pin-built "1.0.0" must never feed a release job. Pinned in Task 16 (`test_cache_keys_never_cross_modes`).

---

### Task 1: `purpose` input in every repo, defaulting to `release` (rollout step 1)

**Files:**
- Modify: `HyperUuid/.github/workflows/ci.yml`, `HyperCast/.github/workflows/ci.yml`, `HyperTabular/.github/workflows/ci.yml`, in three places each:
  - the `on.workflow_dispatch` block;
  - a top-level `run-name`;
  - the 12 tail `if:` lines;
  - the `pr-board` job's `env:`, which gains `PURPOSE: ${{ inputs.purpose }}`.
- Modify: `<repo>/.github/scripts/pr_board.py`, the `gates()` tail line (`train = event == 'workflow_dispatch' and ref == 'refs/heads/master'`). It becomes `... and purpose == 'release'`, with `purpose` read from `PURPOSE` (empty off a dispatch).
- Test: `<repo>/.github/scripts/tests/test_workflow_contracts.py` and `<repo>/.github/scripts/tests/test_pr_board.py`, in each of the three repos.

**Interfaces:**
- Produces:
  - `workflow_dispatch.inputs.purpose` (`type: choice`, options `board`, `release`, `default: release`);
  - `run-name: ${{ github.event_name == 'workflow_dispatch' && format('CI purpose={0}', inputs.purpose) || '' }}`. Task 5 reads `purpose=<value>` from it. Before relying on the `''` branch, confirm on one PR run that GitHub then shows its default run name (the PR title); if it doesn't, the fallback is `github.event.pull_request.title || github.workflow`.
  - `TAIL_GATE = "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master' && inputs.purpose == 'release'"`.

- [ ] **Step 1:** In each repo, change `TAIL_GATE` in `test_workflow_contracts.py` to the new string. Add `test_dispatch_declares_purpose`, asserting:
  - the `purpose` input is a choice of exactly `board` and `release`, with default `release`;
  - `run-name` yields `CI purpose=<purpose>` on a dispatch;
  - `pr-board`'s env passes `PURPOSE`.

  In `test_pr_board.py`, add:
  - `test_board_dispatch_with_skipped_tail_is_green`: the master dispatch with `purpose=board`, every board job `success` and every `TAIL` job `skipped`, gives `verdict` green;
  - `test_release_dispatch_with_skipped_tail_is_red`: the same results with `purpose=release` are red, naming `forge`;
  - `test_release_dispatch_with_tail_success_is_green`.
- [ ] **Step 2:** Run the repo contract tests. Expected: FAIL on the tail gate and the new tests. Reproduce the bug first: `NEEDS` (board success, tail skipped), `CHANGES` (all true), `GITHUB_EVENT_NAME=workflow_dispatch` and `GITHUB_REF=refs/heads/master` today give "forge skipped but gated on".
- [ ] **Step 3:** Edit the three `ci.yml` files.
- [ ] **Step 4:** Run the contract tests and `actionlint` in each repo. Expected: PASS.
- [ ] **Step 5 (negative controls):**
  - Drop `&& inputs.purpose == 'release'` from one tail job: the tail-gate test must fail.
  - Drop `and purpose == 'release'` from `pr_board.py`: `test_board_dispatch_with_skipped_tail_is_green` must fail.

  Restore both.
- [ ] **Checkpoint:** Report three diffs. **Owner** merges them before Task 2.

### Task 2: `hyper_mode.py`, and `dispatch_preparation.py` sends `purpose=release` (rollout step 2)

**Files:**
- Create: `HyperForge/.github/scripts/hyper_mode.py`, `HyperForge/.github/scripts/test_hyper_mode.py`, and fixtures under `HyperForge/.github/scripts/fixtures/events/`: `dispatch_board.json`, `dispatch_release.json`, `dispatch_bogus.json`, `dispatch_none.json`, `pull_request.json`, `schedule.json`, `push.json`.
- Modify: `HyperForge/.github/scripts/dispatch_preparation.py:14-17`, and its test (find it with `grep -l dispatch_preparation .github/scripts/test_*.py`).

**Interfaces:**
- Produces:
  - `hyper_mode.companion_mode(event_name: str, event: dict) -> str`, returning `'release'` or `'companion'` and raising `ValueError` naming `purpose` for a dispatch whose `purpose` is missing or unknown;
  - CLI `python3 hyper_mode.py`, which reads `GITHUB_EVENT_NAME` and `GITHUB_EVENT_PATH` and writes `companion_mode=<value>` to `$GITHUB_OUTPUT`;
  - the YAML expression `MODE_RELEASE = "github.event_name == 'workflow_dispatch' && github.event.inputs.purpose == 'release'"`, exported as a module constant for the structural tests.

- [ ] **Step 1:** Write `test_hyper_mode.py`, one test per fixture:
  - `board` → `companion`;
  - `release` → `release`;
  - bogus → `ValueError`;
  - none → `ValueError`;
  - `pull_request`, `schedule`, `push` → `companion`.

  Add `test_dispatch_sends_purpose_release`: the argv of `dispatch()` (mock `subprocess.check_output`) contains `-f`, `inputs[purpose]=release`.
- [ ] **Step 2:** Run the Forge tests. Expected: FAIL (no module; no input).
- [ ] **Step 3:** Implement `hyper_mode.py`. In `dispatch_preparation.dispatch`, add `'-f', 'inputs[purpose]=release'`.
- [ ] **Step 4:** Run the Forge tests and `actionlint`. Expected: PASS.
- [ ] **Step 5 (negative control):** Make `companion_mode` read a key that doesn't exist. The `release` fixture test must fail. Restore it.
- [ ] **Checkpoint:** Report. **Owner** merges after Task 1 is on all three masters.

### Task 3: `purpose` required (rollout step 3)

**Files:** the same three `ci.yml` files and contract tests as Task 1.

- [ ] **Step 1:** Change `test_dispatch_declares_purpose` to assert `required: true` and no `default`.
- [ ] **Step 2:** Run it. Expected: FAIL. Then edit the three workflows.
- [ ] **Step 3:** Run it. Expected: PASS. Run `actionlint`.
- [ ] **Checkpoint:** Report. **Owner** merges.

### Task 4: `hyper_config.py` reads the `companions` block, with the legacy inputs kept

**Files:**
- Modify: `HyperForge/.github/scripts/hyper_config.py`:
  - `HyperConfig.__init__`, after `version_files`;
  - `shell()`.
- Modify: `HyperForge/.github/workflows/hyper-build-php-extension.yml` (`php_companions` step, ~line 346).
- Modify: `HyperForge/.github/workflows/hyper-pie-acceptance.yml` (`companions` input).
- Test: `HyperForge/.github/scripts/test_hyper_config.py`.

**Interfaces:**
- Produces:
  - `HyperConfig.companions: dict[str, Companion]`, where `Companion` is a frozen dataclass `(name: str, repo: str, commit: str, run: int)`, empty when the block is absent;
  - `shell()` emits `companions='hypercast …'`, the names space-separated, so actions can read the list;
  - two independent checks, each called only by the workflow that has that input. Both are no-ops without a block, and an empty input is accepted with a block, because the block then supplies the list:
    - `hyper_config.check_php_companions(cfg, names: str) -> None` raises when a name isn't in the block;
    - `hyper_config.check_pie_companions(cfg, packages: str) -> None` raises when a non-empty list's names (with `owner/` dropped, lowercased) differ from the block's.
  - block-derived lists, which is what the two workflows execute from now on:
    - `hyper_config.effective_php_companions(cfg, input: str) -> str` returns the block's names when a block exists, else the input;
    - `hyper_config.effective_pie_companions(cfg, input: str) -> str` returns `skunkwerkx/<name>` (the Packagist package, `owner/repo` lowercased) for each block entry, else the input.
  - In `hyper-build-php-extension.yml`, the `php_ext_companions.py` step's `COMPANIONS` comes from `effective_php_companions`:
    - the step is skipped **only when a block exists and companion mode is active**; Task 12's artifact wiring then supplies `<COMPANION>_PHP_EXTENSION`;
    - in every other case it builds from the registry exactly as today: no block in either mode (HyperTabular's current PR caller, which passes `php_companions: hypercast` with no block), or a block in release mode.
  - In `hyper-pie-acceptance.yml`, `COMPANIONS` comes from `effective_pie_companions`.

- [ ] **Step 1:** Add tests:
  - `test_companions_absent_is_empty`;
  - `test_companions_valid`;
  - one `test_companions_rejects_<case>` each for: a repo without `/`, a 39-character commit, an uppercase commit, `run: 0`, `run: "1"`, an extra key;
  - `test_php_companion_not_in_block`;
  - `test_pie_list_must_equal_block`;
  - `test_php_check_ignores_absent_pie_list` (the PHP workflow, with a block, an input of `hypercast` and no PIE list: passes);
  - `test_empty_inputs_with_block_pass`;
  - `test_effective_lists_come_from_the_block`;
  - `test_effective_lists_fall_back_to_inputs_without_block`;
  - in `test_companion_workflows.py`, `test_php_registry_build_skips_only_with_block_in_companion_mode`, which reads the step's `if:` and evaluates it for all four combinations of block present/absent and mode companion/release: only block-and-companion skips.

  Local proof for the no-block caller: run `php_ext_companions.py --companions hypercast` against HyperTabular's current `Cargo.lock`, with no block. It must build ext-hypercast from the registry source, and `php -d extension=<built> -r 'echo (new ReflectionExtension("hypercast"))->getVersion();'` must load it. Negative control: the same with a block and companion mode set must skip.
  
  In `test_companion_workflows.py`, add `test_php_and_pie_workflows_execute_the_effective_lists`: neither workflow's execution step reads `inputs.php_companions` or `inputs.companions` directly.

  Each error message names `.github/hyper.json` and the field.
- [ ] **Step 2:** Run the Forge tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Add the `main()` subcommands `check-php-companions`, `check-pie-companions`, `effective-php-companions` and `effective-pie-companions`, each writing `companions=<list>` to `$GITHUB_OUTPUT`. Each workflow calls only its own check, then its own effective list.
- [ ] **Step 4:** Run the Forge tests and `actionlint`. Expected: PASS.
- [ ] **Checkpoint:** Report.

### Task 5: `companion_pin.py`

**Files:**
- Create: `HyperForge/.github/scripts/companion_pin.py` and `HyperForge/.github/scripts/test_companion_pin.py`.
- Create fixtures under `fixtures/pin/`, recorded with `gh api` from real HyperCast runs. Record each by hand with the command noted in the fixture's README line:
  - `run_green_dispatch_board.json` and its `_jobs.json`;
  - `run_red.json`;
  - `run_pr.json` (gated run 41346771778);
  - `run_other_workflow.json`;
  - `run_other_repo.json`;
  - `compare_ahead.json` and `compare_diverged.json`.

**Interfaces:**
- Produces:
  - `check_run(run: dict, jobs: list[dict], companion: Companion, compare: dict) -> None`, which raises `ValueError(rule)` for: a run not `completed`/`success`; a job set other than the expected one, where `tail` is the companion's `TAIL` list, read with `ast` from `.github/scripts/pr_board.py` at the pinned commit (through the contents API), so the Forge holds no copy of it. Every non-`tail` job (the `pr-board` aggregate among them) must be `success`. Every `tail` job must be `skipped` on a `schedule` run or a `purpose=board` dispatch, and `success` on a `purpose=release` dispatch. A reusable workflow's jobs are named `<caller> / <job>`; match on the caller part; `event` neither `workflow_dispatch` nor `schedule`; a dispatch whose `display_title`/`name` lacks `purpose=board` or `purpose=release`; a workflow path other than `.github/workflows/ci.yml`; a `repository.full_name` other than `companion.repo`; a `head_sha` other than `companion.commit`; and a `compare` status (`repos/{repo}/compare/{commit}...{default}`) other than `ahead` or `identical`;
  - `fetch(companion, api=release_guard.api) -> tuple[run, jobs, compare]`;
  - CLI `companion_pin.py --root <dir> --companion <name> --run <id>`, which validates the run, rewrites only that entry's `commit` and `run` in `hyper.json` (keeping the layout: replace the two values in the text, then re-parse and compare), and prints the old and new pins plus `git log --oneline old..new` from the API's compare `commits`;
  - CLI `companion_pin.py --check --root <dir>`, which validates every pin without rewriting (Task 7 uses it).

- [ ] **Step 1:** Add one test per rule, against the fixtures, asserting the message names the run id and the rule. These include:
  - `test_board_dispatch_with_skipped_tail_passes`, with a fixture whose tail jobs are all `skipped`;
  - `test_skipped_board_job_fails`, with the same fixture but one non-tail job (`test-python`) `skipped`;
  - `test_tail_skipped_on_release_dispatch_fails`.

  Record the `_jobs.json` fixtures from Task 18's real board run once it exists. Until then, derive them from run 41346771778's job list, with conclusions edited and the edits noted in the fixture README. Add `test_rewrite_keeps_layout`: a `hyper.json` with a comment-free custom indent keeps every byte outside the two values.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement, reusing `release_guard.api`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5 (local, read-only):** `python3 companion_pin.py --check` against a scratch `hyper.json` pinned at run 41346771778 must fail with the gated-run rule.
- [ ] **Checkpoint:** Report.

### Task 6: `companion_map.py`

**Files:**
- Create: `HyperForge/.github/scripts/companion_map.py` and `HyperForge/.github/scripts/test_companion_map.py`.

**Interfaces:**
- Produces:
  - `Leg = (workflow: str, job: str, rid: str | None, extra: dict)`;
  - `artifacts_for(leg: Leg, crate: str, cfg: HyperConfig) -> list[str]`, the artifact names from the spec's mapping table, rid-expanded (for example `pypi-wheels-<rid>`, `node-<rid>`, `ruby-native-<rid>-<abi>`, `jni-<rid>`, `php-ext-<rid>-<minor>`, `native-<rid>`, `static-libs`, `wasm-emscripten-staticlib`, `swift-release-assets`, `c-packages-*`);
  - `ecosystems_for(leg) -> list[str]`, a subset of `rust`, `go`, `python`, `pyodide`, `npm`, `ruby`, `java`, `csharp`, `php`, `swift`, `r`, `c`;
  - `all_artifacts(cfg) -> set[str]`, the union, which Task 7 downloads.
- The leg list is derived from the Forge workflows' matrices with `yaml_lite`, not hand-kept.

- [ ] **Step 1:** Write the tests:
  - `test_every_matrix_leg_maps`: every job and matrix entry in `hyper-core*.yml`, `hyper-build-*.yml`, `hyper-lint-*.yml` and `hyper-test-*.yml` has a non-empty ecosystem list, except jobs listed in an explicit `NO_COMPANION` set with a one-line reason each;
  - `test_artifact_names_match_producers`: every name `artifacts_for` returns is one some Forge `upload-artifact` step can produce, matched against the workflows' `name:` templates;
  - one test per mapping row with its expected list.
- [ ] **Step 2:** Run the tests. Expected: FAIL. Implement. Run them. Expected: PASS.
- [ ] **Step 3 (negative control):** Rename `node-<rid>` in one producer's upload name in a scratch copy. `test_artifact_names_match_producers` must fail.
- [ ] **Checkpoint:** Report.

### Task 7: `hyper-companions.yml` and `companion_gate.py`

The spec's `companion-gate` ("the first job of a release-mode run, before any build") is the release-mode step of the `companions` job: every board job already needs that job, so the gate precedes every build without a second job or new wiring.

**Files:**
- Create:
  - `HyperForge/.github/workflows/hyper-companions.yml`;
  - `HyperForge/.github/scripts/companion_gate.py`;
  - `HyperForge/.github/scripts/test_companion_gate.py`;
  - `HyperForge/.github/scripts/test_companion_workflows.py` (structural tests for this and every later workflow change);
  - registry fixtures under `fixtures/gate/` (recorded live: HyperUuid 1.1.0 present everywhere, HyperCast 1.1.0 absent).

**Interfaces:**
- `hyper-companions.yml`:
  - `on: workflow_call` with `secrets: HYPER_COMPANION_APP_KEY: required: false`;
  - no inputs;
  - one job, `companions`, with no job-level `if:`.
- Steps:
  1. checkout (no persisted credentials);
  2. `hyper_mode.py` → `companion_mode`;
  3. **release mode only**: assert there is no `.companions/`, then `companion_gate.py --root .`;
  4. **companion mode only**: fail with the fork message if `github.event.pull_request.head.repo.fork` is true, or with "set `HYPER_COMPANION_APP_KEY` (an Actions secret, and a Dependabot secret for Dependabot's PRs) and the variable `HYPER_COMPANION_APP_ID`" when either is empty;
  5. mint the token, with the inputs from Global Constraints;
  6. `companion_pin.py --check` with `GH_TOKEN` set to that token;
  7. download `companion_map.all_artifacts` from each pinned run (`actions/download-artifact` with `github-token`, `repository`, `run-id`). A 404 or expired artifact fails with the spec's "re-run `<repo>` CI at `<sha>` with `purpose=board` and move the pin" message;
  8. write `SHA256SUMS` and `pin.json`;
  9. upload `companion-<name>`.
- `companion_gate.check(crate: str, version: str, http=urllib) -> list[str]` returns the missing items, from the spec's registry table, CRAN excluded. `companion_gate.required_version(root) -> str` reads every manifest's requirement and raises if they disagree. Manifests: `rust/Cargo.toml`, `go/go.mod`, `python/pyproject.toml`, `typescript/package.json`, the Ruby gemspec, `java/build.gradle.kts`, `csharp/Directory.Build.props` (`HyperCastVersion`), `composer.json`, `Package.swift`, `r/DESCRIPTION`. Normalize `~> 1.1`, `>= 1.1.0`, `^1.1.0` and `=1.1.0` to the lower bound; the bounds must agree.

- [ ] **Step 1:** Write the tests:
  - In `test_companion_gate.py`:
    - `test_hypercast_1_1_0_missing_everywhere` (lists every registry);
    - `test_hyperuuid_1_1_0_live_but_cran` (empty list; CRAN never queried);
    - `test_manifests_disagree`.
  - In `test_companion_workflows.py`:
    - `test_companions_job_has_no_if`;
    - `test_only_release_assertion_and_gate_run_in_release_mode`;
    - `test_no_forge_file_reads_inputs_purpose` (scans `.github/workflows` and `.github/actions`);
    - `test_mode_conditions_are_MODE_RELEASE`;
    - `test_only_hyper_companions_declares_the_key`;
    - `test_token_requests_only_actions_read`;
    - `test_missing_key_message`;
    - `test_dependabot_secret_documented` (the message names both secret kinds).
- [ ] **Step 2:** Run the tests. Expected: FAIL. Implement. Run them, plus `actionlint`. Expected: PASS.
- [ ] **Step 3 (local):** `companion_gate.py` against the live registries for `hyperuuid 1.1.0` (pass) and `hypercast 1.1.0` (fail, every registry listed).
- [ ] **Checkpoint:** Report. Name what only CI can prove: the mint, the cross-repo download, fork and Dependabot.

### Task 8: `fetch-companions` action: mode guard, pin confirmation, source, artifacts, env

**Files:**
- Create: `HyperForge/.github/actions/fetch-companions/action.yml`, `HyperForge/.github/scripts/companion_fetch.py` and `HyperForge/.github/scripts/test_companion_fetch.py`.

**Interfaces:**
- Consumes: `hyper_mode`, `HyperConfig.companions`, `companion_map.artifacts_for`.
- Action inputs:
  - `leg` (`<workflow>:<job>[:<rid>]`, from the calling workflow);
  - `ecosystems` (optional override, space-separated).
- Action outputs: `active` (`true` or `false`).
- `companion_fetch.release_guard(root) -> list[str]` returns what it finds:
  - a `.companions/`;
  - `[patch.crates-io]` in `$CARGO_HOME/config.toml` or `.cargo/config.toml`;
  - a `replace` for the companion in `go/go.mod`;
  - a `file:` companion dependency in any `package.json`;
  - `path:` in `Gemfile`;
  - a `nuget.config` source named `companions`;
  - a Gradle init script under `~/.gradle/init.d/companions*`;
  - a Python requirement on the companion that is a direct reference (`hypercast @ file:` or any `@ <url>`) in `python/pyproject.toml` or a requirements file, or an installed `hypercast` whose `direct_url.json` exists (`pip inspect`, where an interpreter is on the path);
  - Swift: `swift/.build/workspace-state.json` (or the root's `.build/`) listing `HyperCast` in `edited` state, or a `.package(path:` for the companion in `Package.swift`;
  - `HYPERCAST_PACKAGES` or `HYPERCAST_PHP_EXTENSION` set in the environment.
- `companion_fetch.verify(dir, sums_path) -> None` raises naming the file.
- `companion_fetch.write_env(root, companion, leg_files, ecosystems) -> Path` writes `.companions/env` as `KEY=value` lines:
  - `HYPERCAST_SOURCE`, `HYPERCAST_ARTIFACTS`, `HYPERCAST_COMMIT`, `HYPERCAST_VERSION` (from the source's `rust/Cargo.toml`);
  - one `HYPERCAST_<ECOSYSTEM>_REQ` per ecosystem.

  All paths are relative to the checkout root, so a container that mounts it at `/work` resolves them.
- Action steps:
  1. no companions → `active=false`, exit;
  2. release mode → `release_guard`, fail if anything is found, else `active=false`;
  3. download `companion-<name>` from this run (`actions/download-artifact`, default token);
  4. `pin.json` must equal the block;
  5. checkout `commit` into `.companions/<name>/` (`persist-credentials: false`);
  6. copy only the leg's files into `.companions/<name>-artifacts/` and verify them;
  7. `write_env`;
  8. call the wiring for each ecosystem (Tasks 9–15 add these: `companion_wire.py <ecosystem>`), then `companion_origin.py <ecosystem>`.

- [ ] **Step 1:** Write the tests:
  - `test_release_guard_finds_each_kind`, with one fixture tree per kind listed above, the Python direct reference, the Python `direct_url.json`, the Swift edited state and the Swift `.package(path:` among them;
  - `test_release_guard_clean_tree`;
  - `test_pin_json_mismatch`;
  - `test_tampered_byte_fails_verify`;
  - `test_env_paths_are_relative`;
  - in `test_companion_workflows.py`, `test_fetch_is_noop_without_block_and_guard_only_in_release`.
- [ ] **Step 2:** Run them. Expected: FAIL. Implement. Run them. Expected: PASS.
- [ ] **Checkpoint:** Report.

### Tasks 9–15: wiring and origin, one ecosystem per task

**Shared files** (each task adds one subcommand and its tests):
- `HyperForge/.github/scripts/companion_wire.py`: `wire_<eco>(root: Path, env: dict) -> None`, CLI `companion_wire.py <eco> --root <dir>`.
- `HyperForge/.github/scripts/companion_origin.py`: `origin_<eco>(root: Path, env: dict, sums: dict) -> None`, raising `OriginError(binding, found, expected)`. Its message is the spec's: it names the binding, and the origin found against the one expected.
- `test_companion_wire.py` and `test_companion_origin.py`, with fixture manifests under `fixtures/wire/<eco>/`.

**Each task's steps:**

- [ ] **Step 1:** Rewrite tests against fixture manifests. The HyperTabular files to copy in are named per task.
- [ ] **Step 2:** Origin tests against recorded tool output: a pass, a registry-copy fail and a tampered-hash fail.
- [ ] **Step 3:** Run them. Expected: FAIL. Implement. Run them. Expected: PASS.
- [ ] **Step 4 (local, end to end):** Build a pin from the HyperCast checkout (`cargo cdylib`, `local-core.sh`, plus the binding's own build). Lay it out as `.companions/` in a scratch copy of HyperTabular. Wire it, run the origin check, then run HyperTabular's binding suite.
- [ ] **Step 5 (negative control, `*_registry_copy_is_rejected`):** In the same scratch tree, skip the wiring with HyperCast 1.0.0 reachable on its registry. The origin check must fail, naming the registry origin. Where no registry copy exists (npm, Go proxy, PIE, C), the build fails, or the check names the missing origin.
- [ ] **Step 6:** Add the `fetch-companions` step to the binding's Forge workflows, plus the isolated environments listed for that task. Run `actionlint`.
- [ ] **Checkpoint:** Report what ran locally, and what is CI-only.

| Task | Ecosystem | Wiring decisions beyond the spec | Isolated environments in this task | HyperTabular fixtures |
|---|---|---|---|---|
| 9 | Rust + Go | Cargo: write `[patch.crates-io] hypercast = { path = "<abs>/rust" }` to `$CARGO_HOME/config.toml`, then `cargo update -p hypercast --manifest-path` per workspace (`rust/`, `typescript/`, `java/jni/`, `python/`). Origin: `cargo tree -i hypercast --locked` must show the path; warnings are captured and `patch .* was not used` is fatal. Go: `go mod edit -replace github.com/SkunkWerkx/HyperCast/go=<abs>/go` in `go/`; origin is `go list -m -json github.com/SkunkWerkx/HyperCast/go` `.Replace.Dir` | job-level `container:` jobs (nothing extra); `musl-native/build-musl.sh`, `ruby-magnus-musl/build-magnus-musl.sh`, `php_ext_musl.sh`: copy `.companions/`, write the container `$CARGO_HOME/config.toml` from `env` with `/work` paths, run `cargo tree` inside. musl-suite's Go branch (`cp -r /src/go /work/go`): also copy `.companions/hypercast/go` to `/work/.companions/hypercast/go`, re-run `go mod edit -replace` with that container path, and run `go list -m -json` (the origin check) inside before `go test`. The negative control runs the container with the host's absolute replace left in, and the build must fail | `rust/Cargo.toml`, `rust/Cargo.lock`, `go/go.mod`, `go/go.sum` |
| 10 | Python | pip: `pip install <wheel>` before the consumer, then rewrite the requirement to `hypercast @ file:///<abs>/<wheel>`. Origin: `pip inspect`, the `direct_url` `archive_info.hashes.sha256`. Pyodide in Node: add `PYODIDE_SUITE_DEPS` to `suite-pyodide.mjs`, writing to `/companions/<wheel>` in the emfs, hashing in-interpreter, `micropip.install('emfs:/companions/<wheel>')`. Browser: `browser-pyodide.mjs` reads each dependency once, checks it against `SHA256SUMS`, serves that buffer, and the page hashes it with `crypto.subtle.digest` before install. Origins as in the spec's two rows | the sdist and fallthrough venvs in `hyper-build-wheels.yml` (mount `.companions/`; preinstall the wheel, or the sdist where the platform has no wheel) | `python/pyproject.toml` |
| 11 | TypeScript | Pack from `.companions/hypercast/typescript` with a temporary `package.json` whose `files` adds the leg's `*.node` (or `hypercast.wasm32-wasi.wasm` plus `wasi-worker*.mjs`; take the names from HyperCast's `napi.targets` output). Restore the file after `npm pack`. Rewrite the devDependency and peerDependency to `file:<abs tarball>`; `npm install --package-lock-only`. Origin: the lock's `resolved` and `integrity` against `npm pack --json`'s `integrity` | npm consumer tests: build the HyperCast set with `npm_package.py` from the mirrored `node-*` artifacts and publish it to the job's Verdaccio before the consumer's set; musl-suite Node branch | `typescript/package.json`, `typescript/package-lock.json` |
| 12 | Ruby + PHP | Ruby: Gemfile gains `gem "hypercast", path: "<abs>/ruby"`, with the leg's `ruby-native-<rid>-<abi>` extension copied to where HyperCast's `lib/hypercast.rb` loads it (read that file for the path). Origin: `bundle info hypercast --path`, `HyperCast::BACKEND`, `$LOADED_FEATURES` hash. PHP: `HYPERCAST_PHP_EXTENSION=<abs>` (the variable `php_ext_companions.py` already sets), with `COMPOSER_IGNORE_PLATFORM_REQ=ext-hypercast`. Origin: `php -d extension=<abs> -r` prints `ReflectionExtension('hypercast')->getVersion()` and the loaded path. Legacy `php_companions`: with a block present in companion mode, skip the registry build and use the artifact | musl-suite Ruby branch; `ruby-magnus-musl` | `ruby/Gemfile`, `ruby/*.gemspec`, `composer.json`, `php/composer.json` |
| 13 | Java + C# | Java: from `.companions/hypercast/java`, `./gradlew publishToMavenLocal -Dmaven.repo.local=<abs>/.companions/m2` with the leg's `jni-<rid>` and `native-<rid>` staged where HyperCast's Gradle stages them. Write `~/.gradle/init.d/companions.gradle`, which declares that `maven { url }` first and adds `resolutionStrategy.force "io.github.skunkwerkx:hypercast:<version>"` (`group` in HyperCast's `java/build.gradle.kts`). Origin: the init script also registers a task, `companionOrigin`, which resolves `configurations.runtimeClasspath.incoming.artifacts` and prints the selected `io.github.skunkwerkx:hypercast` artifact's absolute `file` and its SHA-256. The path must lie under `.companions/m2/`, and the hash must equal the jar the job published. The `dependencies` report shows only the tree, not files, so it isn't the probe. C#: `dotnet pack` HyperCast with the leg's natives and static libs under `runtimes/` and `staticlibs/` as HyperCast's pack job lays them, into `.companions/nuget/`. Generate `nuget.config` with `packageSourceMapping` `HyperCast` → `companions` only; `-p:HyperCastVersion=<version>`. Origin: `~/.nuget/packages/hypercast/<v>/.nupkg.metadata` `source` and `contentHash` | musl-suite C#, Java and JNI branches; Blazor, Apple and Android legs (wiring only; CI proves them) | `java/build.gradle.kts`, `java/settings.gradle.kts`, `csharp/Directory.Build.props`, `csharp/nuget.config` if present |
| 14 | Swift + C | Swift: `swift-release-assets` holds two zips, `HyperCastCore.artifactbundle.zip` and `HyperCastCoreApple.xcframework.zip`. Check each zip's SHA-256 against `SHA256SUMS` before extracting. (`swift package compute-checksum` takes an archive, not a directory, and its value is that same SHA-256.) Extract them to `swift/HyperCastCore.artifactbundle/` and `swift/HyperCastCoreApple.xcframework/` inside the companion checkout, the local paths HyperCast's `Package.swift` uses (`localAppleCore` and the bundle). Then run `swift package edit HyperCast --path <abs>/.companions/hypercast/swift`. Origin: `swift package show-dependencies --format json` gives the edited path. The build log (`swift build -v`) links the extracted bundle's `libhypercast.a` on Linux and the static legs; Apple mobile builds link the extracted xcframework. Each path is asserted to lie under `.companions/`. C: `HYPERCAST_PACKAGES=<abs>` pointing at the extracted `c-packages-*`. Origin: `hypercast.sh`'s report line, plus the archive SHA-256 | `swift-static-suite`; the Apple mobile Swift legs (the xcframework); the musl and Apple-mobile C smokes | `Package.swift`, `c/smoke/hypercast.sh` |
| 15 | R | Run `.companions/hypercast/r/tools/vendor.sh`, stamp `Hyper-Pin: <commit>` into that `r/DESCRIPTION`, and `R CMD INSTALL` it into the job's library before `setup-r-dependencies`, which then finds it satisfied. Relax HyperTabular's `hypercast (>= X)` in the job's `r/DESCRIPTION` to the pin's version. Origin: `packageDescription('hypercast')$'Hyper-Pin'` equals the commit, and no `Repository` field. Release mode (spec "R in release mode"): `finalize-release` attaches `cran/<name>_X.Y.Z.tar.gz`, and the R jobs add `url::https://github.com/<repo>/releases/download/vX.Y.Z/<name>_X.Y.Z.tar.gz` | none | `r/DESCRIPTION` |

Each task's `Step 1` and `Step 2` tests are named `test_wire_<eco>_<case>` and `test_origin_<eco>_<case>`. The cases are `pinned_passes`, `registry_copy_is_rejected` and `tampered_hash_is_rejected` (except Rust, Go and R, which have no hash, as in the spec). Task 11 adds `test_wire_npm_no_overrides_for_direct_dependency` and `test_pack_without_files_list_has_no_addon`.

### Task 16: caches never cross modes, and every environment reads `.companions/env`

**Files:**
- Modify: every Forge workflow and action with an `actions/cache` or `setup-*` `cache:` step (find them with `grep -rn "cache" .github/workflows .github/actions`).
- Test: `test_companion_workflows.py`.

- [ ] **Step 1:** Write the tests:
  - `test_cache_keys_never_cross_modes`: every cache key that a step can restore in companion mode includes `steps.<fetch>.outputs.active` and the pin commit (`hashFiles('.github/hyper.json')`). Every `setup-*` built-in cache is `false` when `active`.
  - `test_every_isolated_environment_reads_companion_env`: the spec's environment table, as a list of script paths, each of which must reference `.companions/env`.
  - `test_every_board_workflow_runs_fetch_companions_first`: the step precedes the first step that resolves the companion (any `cargo`, `npm`, `pip`, `bundle`, `gradlew`, `dotnet`, `composer`, `swift`, `go`, `R` command).
- [ ] **Step 2:** Run them. Expected: FAIL. Implement. Run them, plus `actionlint`. Expected: PASS.
- [ ] **Checkpoint:** Report.

### Task 17: runbook and docs

**Files:** `HyperForge/docs/release-runbook.md`, `HyperForge/README.md` (the ledger, if it lists actions and workflows).

- [ ] **Step 1:** Runbook sections:
  - "Moving a companion pin": the `purpose=board` dispatch, then `companion_pin.py`;
  - "Release order": companion first; the gate holds the consumer until the companion is live everywhere but CRAN; CRAN submissions by hand, the companion before the consumer, from preserved tarballs;
  - "`purpose`".
- [ ] **Checkpoint:** Report. **Owner** takes the Forge PR (rollout step 4), after step 0 (the App, variable and secrets).

### Task 18: HyperCast's first pin (rollout step 5)

(This task's run also feeds Task 19a.)

- [ ] **Owner:** dispatch HyperCast `ci.yml` on master with `purpose=board`.
- [ ] Once it's green, run `companion_pin.py --check` against it. It must pass, and every `companion_map.all_artifacts` name must exist on the run (check with `gh api repos/SkunkWerkx/HyperCast/actions/runs/<id>/artifacts`).

### Task 19: HyperTabular adopts the pin (rollout step 6)

**Files:**
- Modify: `HyperTabular/.github/hyper.json`, `HyperTabular/.github/workflows/ci.yml`, `HyperTabular/.github/workflows/release.yml`, `HyperTabular/.github/scripts/tests/test_workflow_contracts.py`, `HyperTabular/CLAUDE.md`, `HyperTabular/.github/RELEASING.md`.

- [ ] **Step 1:** Contract tests:
  - `companions` is the third unconditional job beside `changes` and `pr-board`: update `test_changes_and_pr_board_are_the_only_unconditional_jobs`;
  - every Forge board call `needs` `companions`: update `test_every_lint_caller_needs_only_changes` and `test_every_test_caller_needs_its_lint_and_core` to admit it;
  - `HYPER_COMPANION_APP_KEY` is passed only to `companions`;
  - there is no `php_companions` or PIE `companions:` left.
- [ ] **Step 2:** Run them. Expected: FAIL. Run `companion_pin.py --root . --companion hypercast --run <Task 18 run>`. Edit the workflows. Update CLAUDE.md (a "Lists that move together" entry for the pin, and the release order) and RELEASING.md.
- [ ] **Step 3:** Run the contract tests and `actionlint`. Expected: PASS.
- [ ] **Owner:** push to the `hyper_conversion` PR. Acceptance: the board goes green, with the 30 previously lint-gated suites running. When the board runs, `go mod tidy` isn't needed (the replace covers it), but the release bump later still needs it.

### Task 19a: CI-only controls (the rollout counts as proven only when every row passes)

These run on a scratch branch of HyperTabular, `companion-controls`, which is never merged. Its `ci.yml` is Task 19's, plus the probe workflow below. Every dispatch, push and PR here is **Owner**; the agent prepares the branch's files in the working tree and reads the results with `gh`. Off master, no tail job can run, so no step here can prepare a release.

| # | Control | How (Owner) | Expected |
|---|---|---|---|
| 1 | Release-mode detection crosses the reusable-workflow boundary | Dispatch `ci.yml` on `companion-controls` with `purpose=release` | `companions` logs `companion_mode=release`. Its mint, check and download steps are `skipped`, and the gate step runs. While HyperCast 1.1.0 isn't live, the gate fails, listing every registry, and no board job starts |
| 2 | Its mutation is caught | On a Forge scratch branch `companion-controls`, change `hyper-companions.yml`'s mode condition to `inputs.purpose == 'release'`, point the HyperTabular scratch branch's `uses:` at `@companion-controls`, and dispatch with `purpose=release` | the mint step **runs** (`success`), proving control 1 would catch the bug. Delete the Forge scratch branch afterwards |
| 3 | Board and tail start in a real release | HyperTabular's real `prepare-release`, after HyperCast 1.1.0 is live | the gate passes, `companions` succeeds without minting, and every board and tail job reaches `in_progress` (not `skipped`) |
| 4 | Metadata-only token is refused | Dispatch the scratch-only `companion-auth-probe.yml`. It mints three tokens with `create-github-app-token`: `permission-metadata: read` only, `permission-actions: read`, and the job's `GITHUB_TOKEN`. Each runs `gh api repos/SkunkWerkx/HyperCast/actions/artifacts/<id>/zip` against one artifact of the Task 18 run | metadata-only: 403 or 404; `GITHUB_TOKEN`: 403 or 404 (401 if sent without a token); `actions: read`: 302 to the download, and the zip's SHA-256 matches a second download |
| 5 | Secret missing | Push a `companion-controls` commit whose `ci.yml` omits the `secrets:` passed to `companions`, and dispatch it with `purpose=board` | `companions` fails with the "set `HYPER_COMPANION_APP_KEY`…" message, and every board job is `skipped` |
| 6 | Fork PR | From the owner's personal fork of HyperTabular, open a draft PR of `companion-controls` | `companions` fails with the fork message, no board job starts, and nothing falls back to a registry. Close the PR |
| 7 | Dependabot PR (**a default-branch control, run only after Task 19 is merged**: HyperTabular's `dependabot.yml` sets no `target-branch`, so Dependabot's PRs always target master, and before adoption they would test the old CI) | After Task 19 is on master, trigger Dependabot ("Check for updates" under Insights, Dependency graph, Dependabot), or wait for the monthly PR | The PR's run has the `companions` job (confirming it tests the adopted `ci.yml`), mints with the Dependabot secret, and succeeds. Control: temporarily remove the Dependabot copy of the secret and re-run that PR's `companions` job. It must fail with the missing-key message. Restore the secret, then re-run |

- [ ] The agent writes `companion-auth-probe.yml` and the control-5 variant on the scratch branch, in the working tree.
- [ ] **Owner** runs rows 1, 2 and 4–6 on the scratch branch, row 7 after Task 19 merges, and row 3 at the release. The agent reads each result with `gh run view --json jobs` and records it against the Expected column in the final report. Rows 3 and 7 wait as stated.
- [ ] Once rows 1, 2 and 4–6 pass, **Owner** deletes both scratch branches and the probe workflow. Row 7 needs neither.

### Task 20: drop the legacy inputs (rollout step 7)

- [ ] Once `gh search code --owner SkunkWerkx 'php_companions OR "companions:" path:.github/workflows'` finds no caller, remove both inputs, the two `check_*_companions` functions and their tests. The `effective_*` functions stay, reading the block alone. **Owner** merges.
