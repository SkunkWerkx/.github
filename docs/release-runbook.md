# Release and recovery after v0.9.0

Deploy Forge first, then adopt the same immutable Forge SHA in HyperUuid,
HyperCast and HyperTabular. Keep the commit available; unavailable revisions fail.
Do not run this new flow on the already published v0.9.0 tags.

## Before the first release on this flow (v1.0.0)

As of Forge `e07bcd9` (merged in #4), this flow has never run end to end: PR CI does
not exercise `release.yml`, so the following are covered only by unit tests and
actionlint:

- the `prepare-crate-package` and `prepare-maven-package` actions, including
  `cargo package`'s verify build;
- receipt lookup across this run's earlier attempts and `publication_run_id`;
- the recovery-rerun check that refuses a second crates.io or Central upload;
- the Maven confirmation wait (up to an hour after Central reports PUBLISHED; a
  guess, not a measurement);
- the finalizer's `go/vX.Y.Z` tag and GitHub Release.

Dry-run `release.yml` in one consumer against a test registry or staging namespace,
then rerun its failed jobs once to exercise recovery. A Forge fix found this way
means repinning all three consumers to the new SHA, and merging the Forge PR with a
merge commit so that SHA stays on master. Delete this section once a release has
gone through cleanly.

## Normal release

1. Merge the intended source and version changes to master. Dispatch
   `prepare-release.yml` with the next version using its existing inputs.
   Unchanged version files still dispatch the full release-mode CI on A.
2. The successful full CI stages verified native outputs. Inspect B's
   `Release-Source-SHA`, `Release-CI-Run-ID`, digest listing, allowed binary diff,
   and successful staging run. If master advanced beyond A, prepare again.
3. Create the root `vX.Y.Z` tag at B. This intentionally exposes the tagged
   source-tree bindings. Registry publication starts with package preparation:
   all five registries must have complete, version-correct, attested packages
   before any publishing identity authenticates.
4. The global gate preserves `release-packages` and its attested inventory for
   90 days (subject to repository policy). All publishers consume those bytes.
   After every publisher succeeds, the workflow creates or validates
   `go/vX.Y.Z` and the GitHub Release at B, attaching the inventory.

There is one native build matrix, in A's CI. Static production and Android shared
production are separate producers; Apple and Android consumer tests start when
their own artifacts are ready. PHP extension work runs in parallel through the
explicit `php_extension_schedule` input; other consumers keep the inline default.

## After publication

Smoke every package from its public registry, in fresh isolated directories, never from
the checkout: a version probe that reports the new version, then the repo's own corpus or
test suite run against the installed package. Fan out one check per binding; keep the
Blazor publish and the free-threaded Python wheels in hand, since those are the ones that
have failed only on the real registries.

- Attestations: the `.nupkg`, the gems and the jar's inner natives verify with
  `--repo SkunkWerkx/<Repo>`; the wheels and the natives inside the packages are signed by
  the Forge's reusable workflows and need `--signer-repo SkunkWerkx/.github` (or
  `--owner SkunkWerkx`). `gh attestation verify` prints nothing on success when stdout is
  not a TTY: check the exit code or use `--format json`. Add
  `--source-digest <the commit the natives were built from>` (A, the parent of B).
- Where each package carries its native: nupkg `runtimes/<rid>/native`, `staticlibs/<rid>`
  and `runtimes/browser-wasm/nativeassets/net11.0`; Go
  `$(go env GOMODCACHE)/github.com/!skunk!werkx/<repo>/go@vX/staticlib/<os_arch>`; Swift
  `.build/checkouts/<Repo>/swift/<Repo>Core.artifactbundle/<triple>`; PHP
  `vendor/skunkwerkx/<repo>/php/src/native/<rid>` (Packagist ships the repo zipball).
- Registry lag is not failure: Maven Central takes 20 to 30 minutes to serve a new
  version, and PyPI's JSON endpoint can report a stale latest for a few minutes (read
  `releases["X.Y.Z"]` directly).
- pip on a 3.15 GIL build picks the `cp315-abi3.abi3t` wheel over `cp311-abi3` by design
  (PEP 803). `dotnet new blazorwasm -o App` does not compile (the `App` namespace collides
  with the component); use another name, and prove the missing-workload diagnostic with
  `-p:WasmNativeWorkloadAvailable=false`.
- Check an agent's claim against the published artifact before reporting it; the 0.8.0
  gauntlet produced one false alarm.

## Recover a failed publication

Prefer rerunning **failed jobs** after a transient failure. A new dispatch is the
explicit preserved-artifact recovery path:

```sh
gh workflow run release.yml --repo SkunkWerkx/HyperUuid --ref vX.Y.Z \
  -f prepared_run_id=PACKAGE_RUN_ID -f publication_run_id=PUBLICATION_RUN_ID
```

Use the appropriate consumer repository. `PACKAGE_RUN_ID` is an exact earlier
`release.yml` run at the same B which reached the global gate and preserved
`release-packages`. `PUBLICATION_RUN_ID` identifies the attempt holding registry
receipts; omit it if no publication was attempted. `ci_run_id`, if supplied, is
only an assertion of B's recorded CI run and cannot choose a different build.

A recovery dispatch can itself have its failed jobs rerun. The crates.io and Maven
publishers look for receipts in this run's earlier attempts before
`PUBLICATION_RUN_ID`, and refuse a fresh upload unless the history of both shows none
was attempted. Their caller jobs must grant `actions: read` alongside `contents: read`,
`id-token: write` and `attestations: write`; the publishers declare exactly those.
The NuGet publisher does not search earlier attempts yet: once nuget.org has accepted a
package, rerunning failed jobs stops and asks for `publication_run_id`. Dispatch again
with it naming the run that pushed the package.

Recovery verifies the selected release run, A/B source record, inventory, complete
file set, hashes, embedded versions and provenance again. It does not require A's
native artifacts to remain downloadable once the complete verified registry inputs
have been preserved. It does not rebuild packages. Missing or expired preserved
artifacts fail: inspect the cause and deliberately prepare fresh inputs if needed.

RubyGems and PyPI reconcile every already published file against the prepared
bytes, then upload only missing files. Any conflict fails. crates.io verifies the
public archive before skipping or sending the preserved framed request. Maven
reuses its recorded Central deployment and checks the public signed coordinate.

If Central accepted an upload but its response was lost, find the exact deployment
ID in the Publisher Portal and also pass `-f maven_deployment_id=ID`. The workflow
checks the recovered deployment against the saved package; it never blindly uploads
a second deployment after an ambiguous response. A partial Maven coordinate still
propagating is a failed reconciliation until all bytes are visible and matching.

NuGet changes ZIP bytes with its repository signature. Recovery compares all
unsigned ZIP entries and verifies the repository signature. The symbol service does
not expose the original `.snupkg` bytes, so a successful, attested
`nuget-publication-receipt` from `PUBLICATION_RUN_ID` is required to skip a matching
existing package. If publication failed between main-package and symbol acceptance,
reconcile the symbols explicitly; the workflow fails instead of assuming success.
The finalized served `.nupkg` is verified, preserved and attested after publication.

Concurrency serializes releases without canceling an active publisher. A failed
registry leaves other successful publications in place; global gating avoids
preparation failures after publishing starts, but does not make five independent
registries an atomic transaction. A failed finalizer can be rerun after the five
publisher jobs succeed. Existing tags pointing elsewhere or conflicting public
bytes require reconciliation, not an override.

## Validation and timing

Forge's fast validation workflow runs the Python contract tests and syntax/context
lint. Each consumer's PR CI runs its staging/runtime and workflow contracts in
addition to its existing coverage. See [CI timing baseline](ci-timing-baseline.md)
for the measured historical waits and the command to measure a subsequent run.
Local checks do not establish successful platform builds or live registry behavior;
the next release-mode CI and next release are the production validation.

The own-workflow checkout uses documented
[`job.workflow_sha`](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).
This context is GitHub.com-specific and unavailable on GitHub Enterprise Server;
an absent SHA fails instead of resolving to a mutable default.
