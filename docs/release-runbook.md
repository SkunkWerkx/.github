# Release and recovery

The consumers call the Forge at `@master`. Each release run resolves that to one commit
(`find-ci-run`'s `forge_sha`), checks the Forge out at it and records it in the package
inventory; recovering a release needs the Forge's master unchanged since the run that
packaged it. Hold Forge merges while a release is in flight anywhere. Do not run this flow
on the already published v0.9.0 tags.

## A new crate

crates.io is the one registry whose first version is published by hand: it can register a
Trusted Publisher only for a crate that already exists. Before a new Hyper\* crate's first
release, run [`crate-placeholder/reserve-crate.sh`](../crate-placeholder/) with a short-lived
token (it pushes and yanks `0.0.1`), register the publisher on the crate's settings page
against the repository and the workflow `release.yml` (the token's `workflow_ref` names the
top-level caller, not `hyper-publish-crate.yml`), and revoke the token. Every version after
that publishes through Trusted Publishing. PyPI and RubyGems take pending publishers, and a
nuget.org policy belongs to the account, so none of them needs a first publish by hand.

## Normal release

1. Rename the CHANGELOG's `[Unreleased]` to `[X.Y.Z] - <date>` and add the bullet for every
   package the diff touched; `release_guard.py record` refuses a tag without that section,
   and the finalizer writes the GitHub Release from it. Point every `Package.swift` dependency
   at a released version: the same check refuses a tag whose manifests pin a `revision:` or
   `branch:`, which SwiftPM would refuse to every consumer. Merge to master. Dispatch
   `prepare-release.yml` with the bare version. Unchanged version files still dispatch the
   full release-mode CI on A.
2. Once every job of A's release-mode CI run is green, its last job (`request-staging`)
   dispatches `stage-native-binaries.yml` with the run's id: GitHub raises no `workflow_run`
   for a run `prepare-release.yml` started with a `GITHUB_TOKEN`. Staging waits for the run to
   complete, then commits the verified natives as B. A CI run a human dispatched also raises
   `workflow_run`; whichever request comes second finds A staged and is skipped with a notice.
   If no staging run appears, dispatch it by hand:
   `gh workflow run stage-native-binaries.yml --ref master -f head_sha=<A>`.
   Inspect B: `Release-Source-SHA`, `Release-CI-Run-ID`, `Release-Forge-SHA`, the digest
   listing, a diff touching only committed payloads, and a green staging run. If master
   advanced beyond A for any other reason, prepare again. Run CodeQL on B and tag only once
   it is green.
3. Create the root `vX.Y.Z` tag at B (lightweight, unsigned). **The tag run prepares and
   proves; it uploads nothing.** It verifies B against A's run, packs every registry input,
   builds and attests the inventory, attests every file from the tag (`release/v1`),
   preserves `release-packages` for 90 days, plans every registry (what nuget.org, RubyGems
   and PyPI already serve) and completes the NuGet, RubyGems and crates.io Trusted Publishing
   exchanges without pushing. PyPI's action cannot separate the exchange from the upload and
   is skipped. No Go tag, no GitHub Release.
4. Inspect the tag run's summary: the inventory, the checksums every subject carries, each
   publisher's plan. Then publish from the preserved bytes:

   ```sh
   gh workflow run release.yml --repo SkunkWerkx/<Repo> --ref vX.Y.Z -f prepared_run_id=<tag run id>
   ```

   That is the recovery path, so every release exercises it: the dispatch re-verifies the
   preserved inventory, every hash and the release attestation (never re-attesting), each
   publisher verifies its own files' build provenance just before uploading them, it
   publishes to the five registries, then creates `go/vX.Y.Z` and the GitHub Release at B
   with the CHANGELOG section, a Verifying section and `inventory.json` attached.

There is one native build matrix, in A's CI. Static production and Android shared
production are separate producers; Apple and Android consumer tests start when their own
artifacts are ready. The release-mode ext-php-rs extensions are built in jobs of their own
(`hyper-build-php-extension.yml`), never inside the native matrix.

## After publication

Smoke every package from its public registry, in fresh isolated directories, never from the
checkout: a version probe that reports the new version, then the repo's own corpus or test
suite run against the installed package. Fan out one check per binding; keep the Blazor
publish and the free-threaded Python wheels in hand, since those are the ones that have
failed only on the real registries.

- Every shipped file verifies from the tag alone, the one command the READMEs give:

  ```sh
  gh attestation verify <file> --repo SkunkWerkx/<Repo> --source-ref refs/tags/vX.Y.Z \
    --predicate-type https://github.com/SkunkWerkx/.github/predicates/release/v1
  ```

  Run it on at least a wheel and a Go archive from the module cache (the two that fail
  without the predicate type), the served `.nupkg` and a gem. Build provenance is the
  second check: the gems, the inventory and the pre-push `.nupkg` verify with `--repo` alone
  (signed at the tag by the consumer's `release.yml`); the wheels, the committed natives and
  the natives inside the `.nupkg` and the jar were built at A by the Forge's workflows and
  need `--signer-repo SkunkWerkx/.github --source-digest <A>`. `--owner SkunkWerkx` also
  accepts them, with less said. PyPI additionally holds a PEP 740 publish attestation per
  wheel, filed by the publish action, visible on each file's PyPI page. It must name
  `release.yml`, as every earlier release's does; PyPI flags a release whose publishing
  workflow differs from the one before as a change of provenance.
- `gh attestation verify` prints nothing on success when stdout is not a TTY: check the exit
  code or use `--format json`, whose `predicate` is the inventory for the release attestation.
- Where each package carries its native: nupkg `runtimes/<rid>/native`, `staticlibs/<rid>`
  and `runtimes/browser-wasm/nativeassets/net11.0`; Go
  `$(go env GOMODCACHE)/github.com/!skunk!werkx/<repo>/go@vX/staticlib/<os_arch>`; Swift
  `.build/checkouts/<Repo>/swift/<Repo>Core.artifactbundle/<triple>`; PHP
  `vendor/skunkwerkx/<repo>/php/src/native/<rid>` (Packagist ships the repo zipball).
- Registry lag is not failure: Maven Central takes 20 to 30 minutes to serve a new version,
  and PyPI's JSON endpoint can report a stale latest for a few minutes (read
  `releases["X.Y.Z"]` directly).
- pip on a 3.15 GIL build picks the `cp315-abi3.abi3t` wheel over `cp311-abi3` by design
  (PEP 803). `dotnet new blazorwasm -o App` does not compile (the `App` namespace collides
  with the component); use another name, and prove the missing-workload diagnostic with
  `-p:WasmNativeWorkloadAvailable=false`.
- Check an agent's claim against the published artifact before reporting it; the 0.8.0
  gauntlet produced one false alarm.

## Recover a failed publication

Prefer rerunning **failed jobs** after a transient failure. A new dispatch is the explicit
preserved-artifact recovery path, the same command as a normal publish plus the attempt
that holds receipts:

```sh
gh workflow run release.yml --repo SkunkWerkx/<Repo> --ref vX.Y.Z \
  -f prepared_run_id=PACKAGE_RUN_ID -f publication_run_id=PUBLICATION_RUN_ID
```

`PACKAGE_RUN_ID` is an exact earlier `release.yml` run at the same B which reached the
gate and preserved `release-packages` (normally the tag run). `PUBLICATION_RUN_ID`
identifies the attempt holding registry receipts; omit it if no publication was attempted.
`ci_run_id`, if supplied, is only an assertion of B's recorded CI run and cannot choose a
different build. A dispatch at the tag without `prepared_run_id` packs fresh and publishes
in one run; it skips the inspection step and exists as the escape hatch, not the ritual.

A recovery dispatch can itself have its failed jobs rerun. The crates.io and Maven
publishers look for receipts in this run's earlier attempts before `PUBLICATION_RUN_ID`, and
refuse a fresh upload unless the history of both shows none was attempted (a step skipped by
the tag run's `publish: false` counts as not attempted). Their caller jobs must grant
`actions: read` alongside `contents: read`, `id-token: write` and `attestations: write`; the
publishers declare exactly those. The NuGet publisher does not search earlier attempts: once
nuget.org has accepted a package, rerunning failed jobs stops and asks for
`publication_run_id`. Dispatch again with it naming the run that pushed the package.

Recovery verifies the selected release run, the A/B source record, the inventory, the
complete file set, hashes, embedded versions and the release attestation again, and each
publisher the build provenance of every file it uploads; it never re-attests. It does not require A's native artifacts to remain
downloadable once the complete verified registry inputs have been preserved; it does need the
checkout at B, since the committed natives are part of the inventory. It does not rebuild
packages. Missing or expired preserved artifacts fail: inspect the cause and deliberately
prepare fresh inputs if needed.

RubyGems and PyPI reconcile every already published file against the prepared bytes, then
upload only missing files. Any conflict fails. crates.io verifies the public archive before
skipping or sending the preserved framed request. Maven reuses its recorded Central
deployment and checks the public signed coordinate.

If Central accepted an upload but its response was lost, find the exact deployment ID in
the Publisher Portal and also pass `-f maven_deployment_id=ID`. The workflow checks the
recovered deployment against the saved package; it never blindly uploads a second deployment
after an ambiguous response. A partial Maven coordinate still propagating is a failed
reconciliation until all bytes are visible and matching.

NuGet changes ZIP bytes with its repository signature. Recovery compares all unsigned ZIP
entries and verifies the repository signature. The symbol service does not expose the
original `.snupkg` bytes, so a successful, attested `nuget-publication-receipt` from
`PUBLICATION_RUN_ID` is required to skip a matching existing package. If publication failed
between main-package and symbol acceptance, reconcile the symbols explicitly; the workflow
fails instead of assuming success. The finalized served `.nupkg` is verified, preserved and
attested twice after publication (build provenance and `release/v1`), on the run that
pushed it.

Concurrency serializes releases without canceling an active publisher. A failed registry
leaves other successful publications in place; the gate avoids preparation failures after
publishing starts, but does not make five independent registries an atomic transaction. A
failed finalizer can be rerun after the five publisher jobs succeed. Existing tags pointing
elsewhere or conflicting public bytes require reconciliation, not an override.

## Validation and timing

The Forge's `validate.yml` runs the Python contract tests (the inventory, the guard, the
notes, the actions' lint and gates) and actionlint. Each consumer's PR CI runs its own
release-guard tests through `hyper-check-core.yml`, and its workflow contract tests hold its
thin `release.yml` to the gates. See [CI timing baseline](ci-timing-baseline.md) for the
measured historical waits and the command to measure a subsequent run. Local checks do not
establish successful platform builds or live registry behavior; the next release-mode CI
and the next release are the production validation.

The own-workflow checkout uses documented
[`job.workflow_sha`](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).
This context is GitHub.com-specific and unavailable on GitHub Enterprise Server; an absent
SHA fails instead of resolving to a mutable default.
