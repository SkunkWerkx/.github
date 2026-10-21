# Release and recovery after v0.9.0

Deploy Forge first, then adopt the same immutable Forge SHA in HyperUuid,
HyperCast and HyperTabular. Keep the commit available; unavailable revisions fail.
Do not run this new flow on the already published v0.9.0 tags.

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
