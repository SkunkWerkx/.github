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

Don't open pull requests or push unless asked.
