# HyperForge release flow after v0.9.0

The v0.9.0 releases are complete. This change prepares the next production release; it never republishes v0.9.0. The user authorized the previously deferred redesign across Forge, HyperUuid, HyperCast, and HyperTabular.

## Source and trust

A is the preparation source commit tested by the full release-mode CI. B is its sole-child staging commit, containing only the existing binary and generated-metadata paths. Staging records Release-Source-SHA and Release-CI-Run-ID plus its digest list. Every consumer checks the exact repository, workflow, event, source, successful run, allowed diff, complete payload bytes, project exports, runtime ABI, and branch advancement. No ancestor search or unrestricted CI override is permitted. Reused bytes retain provenance naming A; packages assembled from B name B. Attestation verification checks repository, signer repository, and source digest.

Forge's reusable workflows check out Forge independently at job.workflow_sha and invoke its seven existing composite actions locally. Missing or invalid workflow identity fails; no master fallback. Consumers pin one immutable Forge revision consistently.

## Build graph

Static-library and Android shared-library production become independent reusable workflows. The existing native workflow keeps compatible defaults and all per-platform/static-link tests. Consumers opt into separate producers. Apple tests need only static production; Android tests need static and Android shared production. Swift static consumer tests remain intact in a separate workflow. ext-php-rs scheduling is an explicit inline(default)/parallel input, with coordinated consumer opt-in; its existing three Unix targets and load checks remain.

Record baseline job timestamps and dependency wait using completed release-mode runs. Report structural critical-path estimates separately from measured new-run results. Preserve all existing PR checks and platform coverage.

## Package preparation and global registry gate

The intentional root vX.Y.Z tag continues to initiate registry publication. This already exposes the tagged Go/Swift/PHP source trees; the global gate covers all five package registries, not atomic visibility of git tags and registries. All NuGet, RubyGems, PyPI, crates.io, and Maven outputs must be present, version-correct, source-bound, and attested before any registry authentication/publication job begins. Assembly never repeats the native matrix. Maven signs and assembles a local Maven repository bundle before Central upload. crates.io publishes the exact saved crate request while retaining authentication in hyper-publish-crate.yml/jobs.publish. NuGet publishing remains in consumer release.yml, as do PyPI and RubyGems identities.

The gate uploads one immutable release-packages artifact containing the final input files and a versioned JSON inventory recording repository, A, B, CI run, package run, Forge revision, paths, sizes, hashes, provenance source and signer for every file. Attest the inventory itself. NuGet's repository signing necessarily changes the served ZIP after upload; retain and attest that finalized download separately.

## Recovery, concurrency and finalization

A prepared_run_id selects an exact earlier release.yml run for the same repository and B, and its preserved release-packages artifact. Verify the run identity, inventory, file set, hashes and attestations again; never rebuild packages or select an ancestor run. Missing/expired artifacts fail explicitly and require an intentional new preparation run. Run retries can reuse the first attempt's preserved bytes using the same explicit run ID.

Registry recovery checks existing public content against prepared bytes before skipping, publishes only missing content, and rejects conflicts. Central uses preserved deployment receipts and an optional exact deployment ID after ambiguous upload. Ambiguous publication is a failed operation until reconciled, never an unconditional duplicate skip. Retention is explicitly finite (90 days, bounded by repository policy).

Release concurrency is serialized without canceling an in-progress publication. Staging is serialized and rechecks master=A before writing B. After all five publishers succeed, create/validate the matching go/vX.Y.Z tag and GitHub Release at B idempotently. Root tag creation remains the deliberate release initiation. Existing v0.9.0 tags/releases are untouched.

## Acceptance

Tests exercise stale version, missing artifact, wrong provenance source, incorrect run, unrelated A-to-B change, branch advancement, altered preserved package, wrong release subject, partial registry publication, and conflicting existing registry bytes. Positive fixtures prove complete source-bound bytes survive staging, inventory preservation, recovery and publication selection. Workflow lint validates graph/interfaces/permissions. Native platform success and performance improvements require a real subsequent release-mode CI run; local checks do not claim them.

ABI baselining, publishing-identity migrations, broad documentation cleanup, and changing the intentional v0.9 ABI breaks are excluded.
