# HyperForge — notes for agents

`README.md` is the design record: the pipeline, the conventions, the Rust core recipe and
the ledger of lessons paid for in red runs. Read the relevant part before changing a
workflow. This file holds what the code alone won't tell you.

## Blast radius

- Everything here ships to HyperUuid, HyperCast and HyperTabular, which call it at `@master`:
  a Forge merge reaches every consumer's next run at once, and breakage it causes is fixed
  forward. Each release run resolves master to one commit (`referenced_workflows`), checks
  the Forge out at it as `.hyperforge` and records it in the inventory as `forge_sha`;
  recovery needs master unchanged since the packaging run, so never merge here while a
  release is in flight anywhere.
- A change must keep the consumers' current workflows working until they move, or merge in
  lockstep with their PRs; prove it on a consumer's PR CI before merging here.
- Reusable workflows check out their own revision (`job.workflow_sha`) into `.hyperforge`
  and call local actions as `./.hyperforge/.github/actions/...`; actions find the Forge's
  scripts through `github.action_path`. A bare `./.github/...` resolves to the caller's
  repo, not this one.
- Identity rule: a *reusable workflow* runs under the Forge's OIDC identity (its
  attestations verify with `--signer-repo SkunkWerkx/.github`; nuget.org, PyPI and RubyGems
  Trusted Publishing reject it). A *composite action* runs in the caller's job and keeps the
  caller's identity. Anything that publishes, or that a consumer must verify with `--repo`
  alone (the gems, the inventory, the release attestation), is a composite action in the
  consumer's `release.yml`; anything that only builds, commits or checks can be a reusable
  workflow.
- Each consumer describes itself in `.github/hyper.json` (`hyper_config.py` is the schema
  and the derived defaults: library names per RID, the Swift bundle, the Go and Swift target
  sets, the Ruby ABIs and platform gems, the version files, the export rule and the ABI
  probe). Every action, reusable workflow and script reads names from it; nothing else may
  hard-code a consumer's name. Inside a composite action `secrets` and `vars` do not exist
  and `inputs.*` means the action's inputs, so they arrive as inputs and every `run` names
  its `shell`.

## Names that are load-bearing

- crates.io Trusted Publishing is registered against `hyper-publish-crate.yml`'s `publish`
  job, and the Maven publisher's identity is kept stable the same way. Don't rename or split
  those files or jobs.
- The NuGet push must stay in each consumer's `release.yml`: nuget.org validates the
  executing workflow file, which for anything in here is the Forge.
- `package_preparation.py` proves "no earlier upload was attempted" by matching the
  consumers' job names (`pack-crates / publish`, `maven-publish / publish`) and the publish
  step names in run history. A renamed job makes recovery refuse every fresh upload; a
  renamed publish step is worse, because the check then silently stops seeing it.
- The tests extract and run step scripts by their `- name:`. Renaming a step breaks them.

## Publishing is irreversible

- crates.io, Maven Central, NuGet and RubyGems never let a version be replaced. Attest
  before upload, upload only the attested bytes, never repack at publish time, never retry
  an HTTP mutation blindly, and write the receipt before the request (the
  `package_preparation.py` header).
- The release is two deliberate acts: the tag push packs, attests (build provenance and the
  release attestation, `docs/predicates/release-v1.md`), preserves and proves every
  Trusted Publisher exchange without uploading; `gh workflow run release.yml --ref vX.Y.Z
  -f prepared_run_id=<tag run>` publishes from the preserved bytes through the recovery
  path, so every release exercises recovery. `publish` inputs and the `inputs.publish ==
  'true'` gates are what keep the tag run from uploading; the contract tests hold every
  mutation step to them.
- The finalizer writes the GitHub Release from the CHANGELOG's `## [X.Y.Z]` section
  (`release_notes.py`); `release_guard.py record` refuses a tag whose CHANGELOG lacks it, so
  the failure comes before anything publishes.
- Release and recovery steps: `docs/release-runbook.md`. Its "Before the first release on
  this flow" section stays until a release has gone through cleanly.

## Modes

`mode: pr` skips everything only a release needs (attestations, the second Ruby ABI,
osx-x64, native wheels, ruby.wasm, ext-php-rs). Release-only work gates on
`inputs.mode != 'pr'`; a PR run must sign nothing.

## Versions and floors

- Each language's primary toolchain is its current release; check endoflife.date rather
  than assuming. The only exceptions, each commented where it is pinned: a real platform
  constraint, or a deliberate older floor shipped beside the primary and dropped at its EOL.
- Floors follow upstream support and match across all three repos; raise them together.
- Every third-party `uses:` is pinned to a commit SHA with its version as a trailing
  comment (a test enforces it, in workflows and in `action.yml` files alike). Dependabot
  bumps them monthly in one grouped PR, after a 7-day cooldown; `dtolnay/rust-toolchain` is
  ignored there and refreshed by hand to the head of its `stable` branch. A Dependabot PR
  only runs `validate.yml`; the consumers' next runs are where it proves itself.
- Every binding's tool pin (ruff, revive, SwiftLint, PHP, Ruby, JDK, actionlint) lives in
  `hyper-check-core.yml`, once; `validate.yml` repeats the actionlint pin for the Forge itself.

## Writing docs here

- When a red run teaches something, add a ledger row and a comment at the workflow step
  that pays for it.
- Byte counts, run IDs, dates and benchmark figures are point-in-time receipts. Don't
  refresh them without re-measuring. `docs/superpowers/` is a historical record.
- Don't paste real commit SHAs into prose or examples (write `<A>`, `<B>` or `<forge-sha>`).

## Validating

`validate.yml` runs `python3 -m unittest discover -s .github/scripts -p 'test_*.py'` and
actionlint (pinned version, flags in the workflow). It runs on pull requests that touch
`.github/**` or `docs/**` and on pushes to master that touch `.github/**`. actionlint never reads an `action.yml`; `test_forge_actions.py` is their lint
(a shell on every `run`, pinned `uses:`, the publish gates). Nothing here builds a
platform: real behaviour shows up only in the consumers' release-mode CI and in a real
release.

## Working with the owner

- The owner drives git. Don't commit, push, tag, open pull requests or dispatch workflows
  unless asked for that step by name; a go on the edits is not a go on git. Leave the work
  in the working tree, verified, and report it.
- Agreeing that an approach is sound, or stating a preference, is a design decision, not
  an instruction to start. Reading to scope the work is fine; edits wait for the go. During
  a release the owner may be acting in parallel: `git fetch` and compare with origin before
  editing.
- Prove everything locally first, installing toolchains under `~` as needed. Every push to
  a PR branch runs the full matrix, so only what cannot run locally goes to CI, and the
  report says which parts those were.
- Fix stale docs, comments and links on sight; that permission is standing. READMEs state
  the current state only, history goes in the CHANGELOG, and README examples are verified
  by hand in the same pass. No CI step for docs.
- A change reaches all seven bindings and the sibling repos in the same pass, with parity
  in behaviour, API and wording. Where a binding can't follow, say why for that binding.
  Never leave a repo half-edited.
- Pre-1.0, a breaking change is a plain CHANGELOG entry under Unreleased; no semver
  callout, no upgrade hand-wringing.
- Install snippets keep `<version>` (Go: `@latest`); a concrete version there is the bug.
- Drafting release notes includes the pre-publish fixes: rename `[Unreleased]`, add the
  bullet for every package the diff touched, then draft from the fixed changelog.
- Never report commit, push or release state from memory; check with git or gh in the same
  turn, or leave it out.
- A verification counts only when its negative control fails. Copy the flags the repo's own
  alias or CI step uses before trusting a check.
- Formatters and their CI checks cover src, tests, benchmarks and smoke tests in every
  language.
- Tags are lightweight and unsigned (`git -c tag.gpgsign=false tag`); commits are always
  signed.
- Remove agent worktrees and their branches once the work is in the main checkout;
  `.claude/` is untracked and `git add` picks it up.

## Open follow-ups (as of 2026-10-25)

- After the first release on the two-step flow: remove the schema 1 paths from
  `release_inventory.py` and the "Before the first release" runbook section.
