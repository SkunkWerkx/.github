# HyperForge — notes for agents

`README.md` is the design record: the pipeline, the conventions, the Rust core recipe and
the ledger of lessons paid for in red runs. Read the relevant part before changing a
workflow. This file holds what the code alone won't tell you.

## Blast radius

- Everything here ships to HyperUuid, HyperCast and HyperTabular. Each pins every Forge
  `uses:`, `release.yml`'s `FORGE_SHA` and `.hyperforge` checkout ref, and `RELEASING.md`'s
  runbook link to one exact Forge commit. A Forge change does nothing until all three are
  repinned together; prove it on their PR CI before merging here.
- Merge Forge PRs with a merge commit, never squash or rebase. The consumers pin the PR
  branch's commit, and the branch is deleted on merge.
- Reusable workflows check out their own revision (`job.workflow_sha`) into `.hyperforge`
  and call local actions as `./.hyperforge/.github/actions/...`; actions find the Forge's
  scripts through `github.action_path`. A bare `./.github/...` resolves to the caller's
  repo, not this one.

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

## Writing docs here

- When a red run teaches something, add a ledger row and a comment at the workflow step
  that pays for it.
- Byte counts, run IDs, dates and benchmark figures are point-in-time receipts. Don't
  refresh them without re-measuring. `docs/superpowers/` is a historical record.
- Don't paste real commit SHAs into prose or examples (the README uses `<forge-sha>`).

## Validating

`validate.yml` runs `python3 -m unittest discover -s .github/scripts -p 'test_*.py'` and
actionlint (pinned version, flags in the workflow). It only triggers on `.github/**`
changes. Nothing here builds a platform: real behaviour shows up only in the consumers'
release-mode CI and in a real release.

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

## Open follow-ups (as of 2026-10-21)

- Confirm the Blazor emscripten archive packed into each `.nupkg` is attested by
  `hyper-build-wasm.yml` the way the native libraries are, and that
  `hyper-pack-nuget.yml`'s verify step covers it; when 0.8.0 shipped it was the one input
  with no provenance.
- The consumers' `EmccExportedFunction` lists may be dead weight on .NET 11 (the forge's
  export checks don't depend on them); see each consumer's follow-ups.
