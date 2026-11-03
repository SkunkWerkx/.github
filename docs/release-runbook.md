# Release and recovery

The consumers call the Forge at `@master`. Each release run resolves that to one commit
(`find-ci-run`'s `forge_sha`), checks the Forge out at it and records it in the package
inventory; recovering a release needs the Forge's master unchanged since the run that
packaged it. Hold Forge merges while a release is in flight anywhere on the flow below; on
the release train, hold them during preparation only (the tag run verifies what a known Forge
packed with a Forge that descends from it). Do not run this flow on the already published
v0.9.0 tags.

## A new crate or package

crates.io and npm are the registries whose first version is published by hand: each can
register a Trusted Publisher only for a package that already exists. Before a new Hyper\*
crate's first release, run [`crate-placeholder/reserve-crate.sh`](../crate-placeholder/) with a short-lived
token (it pushes and yanks `0.0.1`), register the publisher on the crate's settings page
against the repository and the workflow `release.yml` (the token's `workflow_ref` names the
top-level caller, not `hyper-publish-crate.yml`), and revoke the token. Every version after
that publishes through Trusted Publishing. PyPI and RubyGems take pending publishers, and a
nuget.org policy belongs to the account, so none of them needs a first publish by hand.

npm is the same rule once per package: a consumer with a TypeScript binding ships ten (the
root, `@skunkwerkx/<crate>`, the eight `@skunkwerkx/<crate>-<platform>` platform packages and
the WASI fallback package, `@skunkwerkx/<crate>-wasm32-wasip1`; every name is scoped, since an
unscoped one can already be anyone's, as `hypercast` is). Before the first release that carries them,
the owner publishes a placeholder version below the release, `0.0.1`, under each of the ten
names with a granular token, registers `release.yml` as the trusted publisher on every one of
the ten packages' settings pages and revokes the token.
The release version itself is never published by hand, so the first real version is the
first one `release.yml` publishes through Trusted Publishing, and it carries npm provenance.

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
3. Create the root `vX.Y.Z` tag at B (annotated and signed). **The tag run prepares and
   proves; it uploads nothing.** It verifies B against A's run, packs every registry input,
   builds and attests the inventory, attests every file from the tag (`release/v1`),
   preserves `release-packages` for 90 days, plans every registry (what nuget.org, RubyGems,
   PyPI and npm already serve) and completes the NuGet, RubyGems and crates.io Trusted
   Publishing exchanges without pushing. PyPI's action cannot separate the exchange from the
   upload and is skipped; npm's exchange happens inside `npm publish`, so the tag run cannot
   pre-flight it either, and its plan is the check. No Go tag, no GitHub Release.
4. Inspect the tag run's summary: the inventory, the checksums every subject carries, each
   publisher's plan. Then publish from the preserved bytes:

   ```sh
   gh workflow run release.yml --repo SkunkWerkx/<Repo> --ref vX.Y.Z -f prepared_run_id=<tag run id>
   ```

   That is the recovery path, so every release exercises it: the dispatch re-verifies the
   preserved inventory, every hash and the release attestation (never re-attesting), each
   publisher verifies its own files' build provenance just before uploading them, it
   publishes to the six registries, then creates `go/vX.Y.Z` and the GitHub Release at B
   with the CHANGELOG section, a Verifying section, `inventory.json` and the release's own
   files attached (the Swift zips, the C archives, the CRAN tarball and the PHP extension's
   PIE zips). Its last
   job, `pie-acceptance` (`hyper-pie-acceptance.yml`), then has PIE install the extension from
   Packagist and that Release on every platform and PHP minor, and holds the installed file to
   that leg's zip on the Release, whose release attestation it verifies first. The CRAN
   tarball (`cran/hyperuuid_<v>.tar.gz`) is attested and inventoried like every other file
   and attached to the Release, but no job uploads it to CRAN: the maintainer submits it by
   hand from the preserved bytes. A consumer's release installs it from the Release by
   `url::` meanwhile (the companion-pins spec, "R in release mode").

There is one native build matrix, in A's CI. Static production and Android shared
production are separate producers; Apple and Android consumer tests start when their own
artifacts are ready. The PHP extension's PIE zips, one per platform, PHP minor and thread
safety (NTS and ZTS), are built in jobs of their own (`hyper-build-php-extension.yml`), never
inside the native matrix; the publication gate takes every `php-ext-<rid>-<minor>` and
`php-ext-<rid>-<minor>-zts` artifact of A's run into the inventory's
`php/` group, and the finalizer attaches them to the GitHub Release, where PIE looks.

## The release train

HyperUuid and HyperCast run this flow; HyperTabular stays on the one above until it moves.
It is the same release in two acts, with the packing, the inventory, CodeQL and B moved into
the preparation run, so the tag run only signs and publishes what that run preserved.

**Act one**, `prepare-release.yml` dispatched with the bare version: the Forge's
`hyper-prepare-release.yml` bumps every version file, settles the CHANGELOG heading to
`## [X.Y.Z] — <date>` and `r/NEWS.md`'s `# <crate> (development version)` to `# <crate> X.Y.Z`
(`release_headings.py`, a no-op when they already read so; a NEWS heading naming another
release is refused, not relabelled), runs `release_guard.py`'s changelog check on the result (a missing
section or a `⟨placeholder⟩` fails the dispatch with nothing pushed), pushes A and dispatches
the release-mode `ci.yml` (dispatched with `purpose=release`). After the board, the run's tail: `forge` resolves the one Forge
commit the run uses; the eight pack jobs pack from the run's own artifacts (NuGet, crate and
Maven through the three reusable workflows, the gems, npm, CRAN, C and the wheels through
`pack-gems`, `pack-npm`, `pack-r`, `pack-c` and `verify-wheels`), each attesting what it packs;
`release-prepare` downloads them all, verifies every file's build provenance, builds and
attests the inventory and preserves `release-packages` for 90 days in that run; `codeql`
dispatches the consumer's `codeql.yml` with the run's id (its Swift leg unpacks the run's
attested zips, `swift_release.py unpack`), waits, and requires zero open alerts on master,
still A; `stage-release` writes the zips' checksums into the root `Package.swift` and commits B
on master with `Release-Source-SHA`, `Release-CI-Run-ID` and `Release-Forge-SHA`.

**Act two**, the tag push at B, `release.yml`: `tag-guard` (`release_guard.py tag`: B's
trailers, the tag, the versions, the tree, the green run at A, the CHANGELOG, the Swift
manifest, and that the run still holds `release-packages`); `tag-gate` (downloads the
preserved packages, `release_inventory.py prepared` re-verifies every file and the inventory's
attestation at A, B's `Package.swift` is held to the preserved zips, the inventory's `forge_sha`
must be B's trailer and an ancestor of the running Forge, then one `actions/attest` statement
over every file from the tag, `release/v1`); the six publishers upload on the tag push, each
verifying its files at A first (`hyper-publish-crate.yml` and `hyper-publish-maven.yml` take
`source_sha` for that); the finalizer writes the Go tag and the GitHub Release; PIE acceptance
runs last. A dispatch at the tag with `publication_run_id` replays: the gate verifies the tag
run's release attestation instead of making one, and every publisher reports nothing to do.

The inventory carries the preparation identity: A, the `ci.yml` run that packed, the Forge
that packed, `package_workflow: .github/workflows/ci.yml`, every file at A and no
`staging_sha`. The publication identity (B, the tag, the tag run, its Forge) is never in the
inventory: the release attestation's own provenance carries it, signed at `refs/tags/vX.Y.Z`.
A legacy inventory is unchanged in shape and reads as before (`normalize_identity`).

The hand steps:

1. Write the bullets under `## [Unreleased]` (or the version's heading) and fill every
   placeholder. Nothing else about the release is prose.
2. Dispatch `prepare-release.yml` with the bare version.
3. Read act one's summary: B's trailers, the inventory, the digest listing, CodeQL clean.
4. Push `vX.Y.Z` at B, annotated and signed: `git tag -m "<Project> vX.Y.Z" vX.Y.Z <B>`
   (`tag.gpgsign` on; the guard reads the peeled commit, so an annotated tag is held to B
   exactly as a lightweight one is).
5. Read the tag run's summary: six registries confirmed, the Release written, PIE green. Then
   dispatch `release.yml` at the tag once with that run's id as `publication_run_id`: every
   publisher must report nothing to do. That is the replay proof, so every release still
   exercises recovery, as a no-op instead of as the normal path.
6. CRAN: run the printed command on the preserved tarball, click the confirmation email.

The retry ladder: rerun failed jobs on the same run; then dispatch at the tag with the
receipts (`publication_run_id`, and `maven_deployment_id` after an ambiguous Central upload);
then, for a wrong artifact, a patch release. Never a retag, a repack or a re-attest. A
preparation that failed, or whose `release-packages` expired, is prepared again from a new
`prepare-release.yml` dispatch; the tag guard refuses a B whose run has nothing preserved.

## `purpose`

Every Hyper\* repo's `ci.yml` takes a required `workflow_dispatch` input, `purpose`, and its
`run-name` carries it (`CI purpose=<p>`):

- `board` runs the full board and stops. It is how a companion pin is built (below), and it
  can never prepare a release.
- `release` runs the board, then the release tail, every job of which is gated on
  `inputs.purpose == 'release'`. `hyper-prepare-release.yml` sends it
  (`dispatch_preparation.py`).

The Forge never declares or receives the input: inside a reusable workflow `inputs.purpose` is
empty, so every Forge workflow and action reads the mode from the triggering event
(`hyper_mode.py`, and its `MODE_RELEASE` expression in the `if:`s). A `release` dispatch is
release mode; every other event, a `board` dispatch included, is companion mode;
`hyper_mode.py` refuses a dispatch whose purpose is neither. Without a `companions` block the
mode changes nothing, and `purpose` only decides whether the tail runs.

## Moving a companion pin

A consumer whose bindings are built on a sibling's (HyperTabular on HyperCast) tests every pull
request, schedule and `board` dispatch against one commit of that companion and the green run
that proved it, never against its registries. The pin is in the consumer's
`.github/hyper.json`, one companion per block (`companion_mirror.py` refuses a second):

```json
"companions": {
  "hypercast": {"repo": "SkunkWerkx/HyperCast", "commit": "<40-hex sha>", "run": <run id>}
}
```

To move it:

1. Get a pinnable run of the companion: a `board` dispatch,
   `gh workflow run ci.yml --repo SkunkWerkx/<Companion> --ref master -f purpose=board`, a
   green scheduled run, or the green `purpose=release` run that prepared one of its releases.
   It must be a full board (`workflow_dispatch` or `schedule`, never a pull request's gated
   board) of the companion's own `ci.yml`, green as a board (`pr-board` `success`, every other
   job `success` or `skipped`, the release tail skipped, or on a `purpose=release` dispatch
   green), at a commit on the companion's default branch.
2. In the consumer's checkout, on a branch:
   `python3 <forge>/.github/scripts/companion_pin.py --root . --companion hypercast --run <run id>`.
   It reads the run through `gh api`, refuses it naming every rule it breaks, rewrites only that
   companion's `commit` and `run`, and prints the old and new pins and the commits between them
   for the pull request's description.
3. Open the pull request. Its `companions` job (`hyper-companions.yml`) checks the pin again
   (`companion_pin.py --check`), mirrors the pinned run's artifacts into the consumer's run as
   `companion-<name>` with their `SHA256SUMS`, and every leg's `fetch-companions` step takes its
   share, wires it and proves the origin before anything resolves the companion.

A run's artifacts expire after 90 days. A pin older than that fails the `companions` job with
"re-run `<repo>` CI at `<sha>` with `purpose=board` and move the pin": dispatch a new `board`
run and move the pin to it.

Every move starts the pin-keyed caches cold (R's `setup-r-dependencies`, Bundler, the ruby.wasm
build tree and cargo-semver-checks' rustdoc, each keyed with the pin's commit), so the first
board after it is slower, and adds their entries to the consumer's 10 GB repository cache, whose
eviction can then drop pin-less and release entries. The Go and npm caches are
content-addressed and shared; NuGet's global packages folder is job-local under a pin.

The `companions` job holds the one credential: a token minted from a SkunkWerkx GitHub App, for
the companion repositories only, with one permission, Actions read (a run's artifacts can't be
downloaded without it). The App is set up once, by the owner:

- a SkunkWerkx-owned GitHub App, webhook off, with the one repository permission Actions: read,
  installed on the companion repositories only (HyperCast; a new companion is added to the
  installation, and the mint's failure names the repositories it must include);
- its ID as the org variable `HYPER_COMPANION_APP_ID`;
- its private key as `HYPER_COMPANION_APP_KEY`, twice under that name: an org Actions secret and
  an org Dependabot secret (Dependabot's pull requests get only Dependabot secrets), both
  visible to the consumer repositories only.

A fork's pull request gets neither secret, and its `companions` job fails at once, saying so: a
maintainer re-runs it from a branch in the repository. Nothing falls back to a registry.

## Release order

A consumer's release takes its companion from the registries, at the version its manifests
require, never from the pin. So the companion releases first:

1. Release the companion. The Forge change that attaches the CRAN tarball to every GitHub
   Release (`finalize-release`) must be live before it: the consumer's gate requires that
   tarball on the companion's Release, so a companion released without it holds the consumer
   until the companion releases again.
2. Every manifest of the consumer must require the same companion version: the gate reads each
   lower bound and fails on a disagreement. Before dispatching the consumer's
   `prepare-release.yml`, run the gate from its checkout:
   `python3 <forge>/.github/scripts/companion_gate.py --root .`.
3. Prepare the consumer. In the release-mode run, the `companions` job mints nothing: it
   refuses any companion override committed to the tree (a Cargo patch, a Go replace, a `file:`
   or path requirement, a companion feed or repository) and any `.companions/`, then runs the
   gate, which requires the version on crates.io, NuGet, Maven Central, PyPI, RubyGems, npm,
   the Go proxy, Packagist and the companion's GitHub Release (the PIE zips, the Swift zips, the
   C archives and the CRAN tarball). Every board job needs `companions`, so a companion not yet
   live everywhere but CRAN holds the whole run, with every missing item listed; re-run its
   failed jobs once it is. Every leg's `fetch-companions` only guards, and the R jobs install the companion
   from its GitHub Release by `url::` (`companion_gate.py --r-package`).
4. PIE acceptance runs after publishing and never takes a pin: it installs the released
   companion from Packagist first (`hyper-pie-acceptance.yml` reads the block; its `companions`
   input must equal it or be empty).
5. CRAN, by hand, after both releases and in order: the companion's tarball first, then the
   consumer's once the companion is on CRAN. Both come from their preserved `release-packages`,
   within 90 days of each preparation, which covers both reviews. Until then R users install
   either package from its GitHub Release.

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

  Run it on at least a wheel and a Swift archive or zip (the two that fail without the
  predicate type), the served `.nupkg` and a gem. Build provenance is the
  second check: the gems, the inventory and the pre-push `.nupkg` verify with `--repo` alone
  (signed at the tag by the consumer's `release.yml`); the wheels and the sdist, the committed natives,
  the PHP extension's zips and the natives inside the `.nupkg` and the jar were built at A by the Forge's workflows and
  need `--signer-repo SkunkWerkx/.github --source-digest <A>`. `--owner SkunkWerkx` also
  accepts them, with less said. PyPI additionally holds a PEP 740 publish attestation per
  wheel and sdist, filed by the publish action, visible on each file's PyPI page. It must name
  `release.yml`, as every earlier release's does; PyPI flags a release whose publishing
  workflow differs from the one before as a change of provenance.
- `gh attestation verify` prints nothing on success when stdout is not a TTY: check the exit
  code or use `--format json`, whose `predicate` is the inventory for the release attestation.
- Where each package carries its native: nupkg `runtimes/<rid>/native`, `staticlibs/<rid>`
  and `runtimes/browser-wasm/nativeassets/net11.0`; Go has none (the core is Go source at
  `$(go env GOMODCACHE)/github.com/!skunk!werkx/<repo>/go@vX/internal/core/core.go`); Swift
  `.build/checkouts/<Repo>/swift/<Repo>Core.artifactbundle/<triple>` (with `swift_remote`, the
  GitHub Release's two zips, unpacked under `.build/artifacts/`); PHP the target PHP's
  `extension_dir` (`php -r 'echo ini_get("extension_dir");'`), where `pie install
  skunkwerkx/<repo>:vX.Y.Z` puts the `.so` or `.dll` from the GitHub Release's
  `php_<crate>-vX.Y.Z...` zip for that platform, minor and thread safety, and the ini line PIE wrote to enable
  it; C (with `c_assets`) `<crate>-<version>-<rid>/lib` and `bin` in the GitHub Release's
  archives.
- `pie-acceptance` is the PHP smoke, already run: a red leg there is the release's, and is
  fixed as the `pie-acceptance` paragraph under Recover a failed publication says, not by a
  re-run that happens to pass.
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
deployment and checks the public signed coordinate. npm reconciles each of the ten
packages by `dist.integrity` against the prepared tarball, byte-compares the served
`dist.tarball`, and publishes only the missing ones, the platform and WASI packages before the root
so the root never names a version that is not yet installable; a conflict fails. CRAN has
nothing to recover: no job uploaded it, so the preserved tarball is resubmitted by hand.

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

A red `pie-acceptance` leg after publication (PIE cannot find, install or enable a zip, the
file it installed is not that zip's, or the extension fails a check) is fixed with a patch release, never in place: no
asset rename, no repack and no re-attest. Renaming an asset leaves the inventory's recorded
paths wrong; repacking a zip, a Windows one to fix the DLL name inside it say, changes the digest
the release attestation names; and rebuilding or re-attesting a published version is what this
runbook forbids everywhere else. Re-run the failed leg once if the failure was transient (a
runner, a Homebrew or Packagist outage); otherwise fix the cause, release X.Y.Z+1 and leave
X.Y.Z as it is, recorded as broken for PHP in the CHANGELOG. Naming errors are meant to be
caught before publication, by the packer's check against PIE's own names in every CI leg and by
the rehearsal (`hyper-pie-acceptance.yml` dispatched against a scratch repository's Release).

Concurrency serializes releases without canceling an active publisher. A failed registry
leaves other successful publications in place; the gate avoids preparation failures after
publishing starts, but does not make six independent registries an atomic transaction. A
failed finalizer can be rerun after the six publisher jobs succeed. Existing tags pointing
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
