# HyperForge release flow implementation plan

> **For agentic workers:** Use the execution and parallel-agent skills for the already authorized refactor. Steps use checkbox syntax.

**Goal:** Make the next Hyper* release prepare once, validate globally, publish preserved bytes, and recover explicitly.

**Architecture:** Keep A/B provenance and the current publishing identities. Independently extract Forge producers and package assembly; compose them through consumer gates and a shared inventory/recovery script.

**Tech stack:** GitHub Actions, Python standard library, Bash, Gradle Maven Publish, registry HTTP APIs.

**Spec:** ../specs/2026-10-21-release-flow-design.md

## Global constraints

- No v0.9.0 republishing or tag changes.
- Native artifacts name A; staging/tag and newly assembled packages name B.
- crates.io identity stays Forge hyper-publish-crate.yml/jobs.publish; NuGet stays consumer.
- Preserve existing public workflow inputs and defaults and full PR/release coverage.
- No Forge master fallback; consumers use one immutable Forge revision.

## Review focus

- Missing workflow identity must fail before invoking a local Forge action.
- Recovery after only part of a registry's files uploaded must not overwrite or silently skip conflicts.
- A saved inventory for a different B or with extra/missing files must fail before authentication.
- Missing artifact or Central upload response must not trigger an unsafe fallback.
- A concurrent branch advance must prevent staging unrelated source or finalizing a different tag.

### Task 1: Forge pinning and producer graph

Files: hyper-build-native.yml, hyper-build-wasm.yml, hyper-build-wheels.yml; new hyper-build-static.yml, hyper-test-swift-static.yml, hyper-build-android.yml, hyper-build-php-extension.yml; test_forge_producers.py.
Interfaces: static_libs_artifact/swift_static_tests/android_shared_artifact true defaults; php_extension_schedule inline default/parallel opt-in. Separate producer inputs preserve existing crate/project/platform switches.

- [x] Write and run failing tests for immutable local action identity and compatible extraction switches.
- [x] Extract producers and static Swift tests, reuse all seven composites, implement explicit PHP scheduling.
- [x] Run producer tests and workflow lint; record old-run job timing baseline and structural changes.

### Task 2: Prepare immutable Maven and crate packages

Files: hyper-pack-maven.yml, hyper-pack-crate.yml; existing NuGet/Maven/crate reusable workflows; package helper scripts and test_package_preparation.py.
Interfaces: prepared_run_id optional on shared publishers; maven-central-package/crate-package preserved artifacts; Maven publication_run_id and deployment_id for exact receipt recovery.

- [x] Test exact-byte package preparation and source-bound consumption before editing publishers.
- [x] Build/sign local Maven bundle without upload; attest and preserve final files. Package crate once, preserve exact framed request, authenticate only in original publisher.
- [x] Split existing publishers into saved-byte consumption with backwards-compatible default assembly, preserve NuGet symbol attestation.
- [x] Run functional helper tests and source-provenance regression suite.

### Task 3: Coordinated consumer preparation/staging

Files: all three prepare-release.yml/stage-native-binaries.yml, release_guard.py, dispatch_preparation.py and tests.
Interfaces: release_guard.py record/run/master/payloads/compare; record outputs source_sha/run_id. Core-specific export/runtime adapters remain consumer-owned.

- [x] Add failing exact-source/run/branch/version/ABI/allowed-diff tests for Cast/Tabular.
- [x] Port Uuid's strict A/B preparation/staging protocol, preserving all platform and ABI coverage.
- [x] Verify positive and rejection paths for each consumer without publishing.

### Task 4: Inventory, registry gate and recovery

Files: Forge .github/scripts/release_inventory.py and tests; each consumer release.yml and ci.yml.
Interfaces: release_inventory.py create/verify/registry-plan/finalize; release dispatch prepared_run_id selects preserved bytes from release.yml at B. Inventory records schema/repository/version/source_sha/staging_sha/ci_run_id/package_run_id/forge_sha and every file's digest/size/provenance.

- [x] Test altered/missing/extra packages, wrong B/run/provenance, partial and conflicting registry content.
- [x] Separate Ruby/PyPI preparation from publishing; integrate shared NuGet/crate/Maven prep. Gate all publishers on complete verified preserved inventory.
- [x] Reverify exact bytes in consumers; implement explicit recovery and fail on ambiguous/unverifiable publication.
- [x] Narrow producer needs in CI, serialize release/staging, and finalize Go tag/GitHub Release only after all publishers succeed.
- [x] Run all workflow tests/lint; perform independent review before creating reviewable branches/PRs. Record live-CI/performance limitations and rollout order.

## Execution record

Implemented in isolated checkouts of Forge and all three consumers. Contract suites cover the complete staging path, immutable package hand-off, metadata, source-bound attestations, recovery run identity, complete publication attempt history, lost responses, registry conflicts and final tag/Release idempotency. Real pinned Gradle 9.7.0 / vanniktech0.37.0 dry-run confirmed the local ReleaseBundle publication graph registers no Central upload task.

Ruling: retain the root version tag as deliberate initiation; automate the Go tag and GitHub Release after publication. This preserves the existing public entry point and publishing registrations; the global gate covers registries, while git-tree bindings become visible at the initial tag.

Ruling: missing publication receipts never alone prove that no upload occurred. Reconcile public bytes first; otherwise require complete prior job/step history proving no upload or a byte-verified Central deployment ID. This can require explicit reconciliation after an ambiguous upload instead of automatic retry.

Review fixes: explicit status predicates let recovery publishers run after skipped preparation ancestors; complete crate request metadata is checked at the global gate; unrelated registry receipts no longer block reconciliation; publication history has scoped GitHub credentials and explicit caller identity; existing Release inventory conflicts fail rather than overwrite.

Validation scope: local workflow/guard/metadata/recovery suites and syntax/interface lint, including configured runner labels and narrow ignores for official job.workflow_* properties newer than actionlint1.7.12. Shellcheck-enabled lint still reports existing shell style warnings; no full shellcheck-clean claim. The next actual full platform CI run and registry release provide production/performance validation. No v0.9.0 tag or publication is changed.
