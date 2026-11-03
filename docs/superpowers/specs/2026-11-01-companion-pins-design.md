# Companion pins: testing against an unpublished sibling — design

Status: design, agreed in conversation 2026-11-01; revised after two written reviews (the
second's authorization finding settled by the owner: a GitHub App); awaiting review.
Scope: the Forge, then HyperTabular (the first consumer) and HyperCast (the first companion).
HyperUuid and HyperCast have no companion. Their only change is the dispatch discriminator,
which every repo takes.

## Purpose

HyperTabular's bindings are built on HyperCast's: every cell is a HyperCast verdict, in every
language. A HyperTabular change often needs a HyperCast change that is merged but not released.
Today each binding resolves HyperCast from its registry, so HyperTabular's board stays red
until HyperCast publishes. Its first pull request after the 1.1 port failed 19 jobs, all for
that one reason (run 41351309703). Among them:

- the core's committed header differs from what crates.io's 1.0.0 generates;
- npm, PyPI, NuGet and the Go proxy have no 1.1.0;
- the C++ smoke downloads a HyperCast C archive that does not exist yet;
- R cannot solve `hypercast (>= 1.1.0)`.

Its 30 lint-gated suites never ran.

The goal: a pull request or push board tests HyperTabular against a chosen, proven HyperCast
commit, with nothing published. A release still proves what users will install.

| Board | HyperCast comes from |
|---|---|
| Pull request, schedule, and a `board` dispatch | the **pinned commit**: its source and the artifacts of the green full-board run that proved it |
| `prepare-release` (a `release` dispatch) | the **registries**, at the version the manifests require, which must be live everywhere except CRAN; R takes HyperCast's GitHub Release tarball |

## Decisions

- **A pinned commit, not HyperCast master.** A HyperCast merge never turns HyperTabular's
  board red, and a re-run tests the same thing. The pin moves deliberately.
- **A pin is a commit and the run that proved it.** The run must be HyperCast's own `ci.yml`,
  green on every board job, a full board (`schedule` or `workflow_dispatch`; never a gated
  `pull_request` run) and at that commit. HyperTabular only ever tests against a HyperCast
  that was green on every leg.
- **A dispatch says what it is for.** Today every `ci.yml` dispatch on master runs the release
  tail: the packers, CodeQL and `stage-release`, which commits B to master. Every one of those
  jobs is gated only on `workflow_dispatch` plus master, and `dispatch_preparation.py` sends a
  plain dispatch. So `ci.yml`'s `workflow_dispatch`, in every Hyper* repo, gains a required
  `purpose` input:
  - `board` runs the full board and stops;
  - `release` runs the board and the tail.

  Every tail job's gate adds `inputs.purpose == 'release'`, and `dispatch_preparation.py` sends
  `purpose=release`. A pin-building dispatch is `purpose=board`, and can never prepare a
  release. The same input tells `fetch-companions` and `companion-gate` which mode a run is
  in: a `release` dispatch is release mode, everything else companion mode.
- **The Forge reads the purpose from the event, never from `inputs`.** A dispatch input
  belongs to the dispatched workflow: a reusable workflow sees only its own `workflow_call`
  inputs, so `inputs.purpose` inside a Forge workflow is empty, and `'' != 'release'` would
  mint tokens in a release run. The `github` context of a called workflow is the caller's,
  so every Forge workflow and action reads the mode from the triggering event:
  - release mode is `github.event_name == 'workflow_dispatch' &&
    github.event.inputs.purpose == 'release'`;
  - a `workflow_dispatch` whose `purpose` is neither `board` nor `release` is an error;
  - every other event is companion mode.

  One Forge helper, `hyper_mode.py`, computes this from `GITHUB_EVENT_NAME` and
  `GITHUB_EVENT_PATH` for scripts, and the workflows' `if:` expressions spell the same
  condition. No Forge workflow declares or is passed a `purpose` input, so the 30-odd callers
  change nothing. Only the consumer's own `ci.yml` uses `inputs.purpose`, in its tail gates,
  where the input is its own.
- **Pinnable runs come from a `board` dispatch.** To move the pin, the owner dispatches
  HyperCast's `ci.yml` at the commit with `purpose=board`, or uses a green scheduled run.
  HyperCast gains no push trigger: a full board is paid for only when the pin moves.
- **Artifacts for the native half, source for the managed half.** The pinned run's artifacts
  are the expensive builds: native libraries, node addons, Ruby, JNI and PHP extensions,
  wheels, static libraries, C archives and the wasm archive. The managed layers (assemblies,
  jars, JavaScript, Ruby and Go source) are compiled from HyperCast's source at the pin,
  which is quick. No Rust is rebuilt except where the consumer builds its own core (Rust
  takes HyperCast's source through the patch) and for R, which always builds from source as
  CRAN does.
- **Manifests keep requiring what the code needs.** HyperTabular's manifests name the
  HyperCast version it actually requires (1.1.0), because Go and Swift users resolve straight
  from git. HyperCast's master still says its last release (1.0.0), since `prepare-release`
  bumps it. So in companion mode each leg rewrites that requirement, in the job's working
  tree only, to point at the pinned package itself: a file or path reference where the
  ecosystem has one, the exact pinned version where it does not. Nothing is committed or
  published. Rejected:
  - lowering the manifests between releases, which would claim a compatibility Go and Swift
    users would find false;
  - making HyperCast's master carry the next version, which changes HyperCast's bump model
    for a version not known until release.
- **Evidence of origin, not version.** A pin-built package and the registry's copy can carry
  the same version, 1.0.0, so a version check proves nothing. Every leg proves where the bytes
  came from: the path, file or feed the package manager recorded, and where it records one, a
  hash equal to the downloaded artifact's.
- **One job holds a credential, and it can only read.** A run's artifacts are public to list
  but not to download: the zip endpoint needs a token with Actions read on the companion repo,
  and a job's `GITHUB_TOKEN` reaches only its own repo (an unauthenticated download of a
  HyperCast artifact returns 401). A SkunkWerkx GitHub App with one permission, Actions read,
  installed on the companion repos only, supplies it. Its key reaches exactly one job,
  `companions`, which mints a token scoped to the companion repo, downloads the pinned
  artifacts and re-uploads them into the consumer's own run. Every leg then downloads with its
  own `GITHUB_TOKEN` and never sees a secret. Rejected: a personal access token (one
  person's account, hand-rotated); a public ghcr.io bundle (a new public channel for unreleased
  bytes).
- **Release mode never sees a pin.** In a `release` run the overrides are off and a gate
  confirms the registries before anything is packed.

## The pin

In the consumer's `.github/hyper.json`:

```json
"companions": {
  "hypercast": {"repo": "SkunkWerkx/HyperCast", "commit": "<40-hex sha>", "run": <run id>}
}
```

- The key is the companion's crate name: the name every ecosystem's package uses, and the
  prefix of its artifacts.
- `hyper_config.py` validates the block:
  - `repo` is `owner/name`;
  - `commit` is 40 lowercase hex characters;
  - `run` is a positive integer;
  - no other keys are present.
- An absent block means no companions, which is the case for HyperUuid and HyperCast.

### The inputs it supersedes, and their compatibility

Two reusable-workflow inputs declare companions today, and HyperTabular's callers pass both
(`ci.yml` passes `php_companions: hypercast`, `release.yml` passes
`companions: skunkwerkx/hypercast`):

| Input | Today | After the Forge change |
|---|---|---|
| `php_companions` (`hyper-build-php-extension.yml`) | builds ext-hypercast from the crates.io version the consumer's lock pins | **kept and working.** With no `companions` block it does what it does today. With one, the block wins: companion mode loads the pinned run's extension, and release mode builds from the registry as today. A name in the input that the block lacks is an error |
| `companions` (`hyper-pie-acceptance.yml`) | PIE installs the published companion after release | **kept and working**, with the same meaning. Its list must equal the block's names when a block exists |

Each input is removed only after every caller has migrated, in a Forge change of its own, once
a search of every consumer's workflows finds no caller left. The migration of both HyperTabular callers is
part of the rollout.

### Moving the pin

`python3 .github/scripts/companion_pin.py --root <consumer> --companion hypercast --run <id>`
does the following:

1. It reads the run and its jobs through the GitHub API and checks every condition above, plus
   one more: the commit must be reachable from the companion's default branch. For a
   dispatched run, the run's name must show `purpose=board` or `release`: `ci.yml`'s
   `run-name` carries the purpose for exactly this.
2. It rewrites only that companion's `commit` and `run` in `hyper.json`, keeping the file's
   layout.
3. It prints the old and new pins and the commits between them, for the PR description.

## The pull-request and board-dispatch board

### The `companions` job

`hyper-companions.yml` is a Forge reusable workflow. The consumer's `ci.yml` calls it once, as
`companions`, passing one secret, and every job that calls a Forge board workflow `needs:` it.

1. **Mint a token.** `actions/create-github-app-token` with the org variable
   `HYPER_COMPANION_APP_ID` and the secret `HYPER_COMPANION_APP_KEY`, `owner: SkunkWerkx`,
   `repositories:` the companions' names from `hyper.json`, and `permission-actions: read`. The
   token lives for at most an hour and is revoked when the job ends.
2. **Check every pin** with `companion_pin.py`'s rules, through that token.
3. **Download** every artifact the mapping names for any leg, from each pinned run, and write
   `SHA256SUMS` beside them.
4. **Upload** them into this run as one artifact per companion, `companion-<name>`, with
   `SHA256SUMS` and the checked commit and run inside. Measured on HyperCast run 41346771778, a gated board's artifacts total
   11 MiB; a full board's are a few times that.

Where the key is absent the job fails at once, saying why:

- **A pull request from a fork** gets no secrets. The message says the board needs the
  companion artifacts and a maintainer must re-run it from a branch in the repo. Nothing falls
  back to a registry.
- **Dependabot's pull requests** get only Dependabot secrets, so the same key is also stored as
  an org Dependabot secret under the same name.

In a `release` run the job still runs, has no job-level `if:`, and succeeds without minting a
token or downloading anything: its steps are gated on the event's purpose (above), not on
`inputs.purpose`, which is empty inside a reusable workflow, and its release-mode guard
asserts there is no companion override (the ones `fetch-companions` refuses, below) and no
`.companions/` in the checkout. It is never skipped,
because a skipped need fails GitHub's implicit `success()` and would skip every dependant,
whatever their own `if:` admits. So no dependant's `if:` changes: the existing change gates and
failure blocking stay as they are. A repo without companions doesn't call the workflow, and its jobs don't need
it.

### `fetch-companions`

`fetch-companions` is a Forge composite action. A job runs it once, before anything resolves
HyperCast. It is a no-op when `hyper.json` has no companions. In release mode it only guards:
it fails if a companion override is wired (a Cargo patch, a Go replace, a `file:` or path
requirement, a companion feed or repository) or a `.companions/` directory exists, naming
what it found; otherwise it succeeds at once, before reading any mirrored artifact or
installing anything, and the leg resolves HyperCast from the registries. The steps below are
companion mode only.

1. **Confirm the pin was checked**: the `companion-<name>` artifact records the commit and
   run the `companions` job checked, and they must equal `hyper.json`'s.
2. **Check out the source** at `commit` into `.companions/<name>/` *inside the consumer's
   checkout*, without persisted credentials. Every container that mounts the checkout then
   sees it at the same relative path.
3. **Download the leg's artifacts** from this run's `companion-<name>` artifact, with the job's
   own `GITHUB_TOKEN`, into `.companions/<name>-artifacts/`, keeping only the leg's files (the
   mapping below), and verify each against the `SHA256SUMS` the `companions` job wrote. The
   leg never talks to the companion's run.
4. **Write the companion environment file**, `.companions/env`. It holds what every later
   step and every isolated environment reads: the source path, the artifact paths, and the
   rewritten requirement for each ecosystem.
5. **Wire the host's overrides** (the table below), then **prove the origin** (see
   [Failure modes](#failure-modes)).

### Isolated environments get the companion explicitly

A host-side override does not reach an environment the job builds afresh. Each one receives the
companion from `.companions/env` and proves its own origin, inside itself:

| Environment | Where | What it does |
|---|---|---|
| The musl builds | `musl-native/build-musl.sh`, `ruby-magnus-musl/build-magnus-musl.sh`, `php_ext_musl.sh` | copy `.companions/` into `/work` beside `rust/`; write the patch, with `/work` paths, to the container's own `$CARGO_HOME/config.toml`; run `cargo tree -i hypercast` inside before building |
| The musl suites | `musl-suite/suite-musl.sh`, every binding branch (C#, Go, Java, JNI, Node, Python, Ruby) | copy `.companions/` into `/work` with the binding's tree; regenerate that binding's configuration from `.companions/env` with `/work` as the root (the NuGet feed and `nuget.config`, the Maven repository, the Cargo patch, the Go replace, the npm tarball reference, the wheel, the Gemfile path); run that binding's origin check inside before the suite |
| Swift static (musl) | `swift-static-suite` | the bundle and source copied in; the same `.package(path:)` rewrite inside |
| Job-level `container:` jobs | `hyper-core`, `hyper-check-core`, `hyper-build-native`, `hyper-build-wheels`, `hyper-test-c` | the host steps already run inside the container, so `fetch-companions` runs there unchanged |
| Pyodide in Node | `pyodide-suite/suite-pyodide.mjs` | gains the browser runner's `PYODIDE_SUITE_DEPS`: the pinned Pyodide wheel installed with micropip before the consumer's, so micropip never fetches HyperCast |
| Pyodide in the browser | `pyodide-suite/browser-pyodide.mjs` | `PYODIDE_SUITE_DEPS` set to the pinned wheel's directory (the hook exists today) |
| The sdist and fallthrough venvs | `hyper-build-wheels.yml`'s fresh venvs, several in Docker | the pinned wheel, or the pinned sdist where the venv's platform has none, installed into each venv from a mounted directory before the consumer, so the consumer's requirement is already met and pip never resolves HyperCast from an index |
| npm consumer tests (Verdaccio) | `typescript/consumer-tests/run.sh` | the pinned HyperCast npm set, built by `npm_package.py` from the pinned run's addons as a release would, published into the job's own Verdaccio first |

The Forge test that maps legs to artifacts also lists every container, isolated interpreter and
consumer-test entry point, and fails on one that never reads `.companions/env`.

### The mapping

`companion_map.py` is a Forge module with tests. It is the one place that knows which of the
companion's artifacts a leg needs and how that leg's ecosystem is overridden. HyperCast's
artifact names and HyperTabular's use of them are pinned together there, so a renamed artifact
fails a Forge test, not a run.

| Binding and legs | From the pinned run | From the source | Override, in the job's tree only |
|---|---|---|---|
| Rust core: every core leg, `core-checks`, and every crate that builds it (napi, JNI, PHP, Python, Blazor) | (none) | `rust/` | `patch.crates-io.hypercast.path` in `$CARGO_HOME/config.toml` on the host, and in each container's own (above); then `cargo update -p hypercast` once per workspace, so later `--locked` calls see a consistent lock |
| Go: native, TinyGo, `js`, `wasip1` | (none) | `go/` | `go mod edit -replace github.com/SkunkWerkx/HyperCast/go=…`; a local replace needs no `go.sum` line |
| Python: wheels, sdist, Pyodide | `pypi-wheels-<rid>`, `-pyodide`, `-sdist` | (none) | the requirement rewritten to `hypercast==<pinned>`, and the pinned wheel installed by its path first in every interpreter that runs a test (above), since a `@ file://` reference names one wheel for every interpreter (abi3 or abi3t) and a host path a container lacks |
| TypeScript: addon and wasm fallback | `node-<rid>`, `node-wasm32-wasip1` | `typescript/` | HyperCast packed with a **job-local `files` list** that adds the leg's addon (or the WASI payload), since the committed whitelist excludes them; the consumer's direct devDependency rewritten to `file:<that tarball>` (an `overrides` entry for a direct dependency is `EOVERRIDE`); the peer rewritten to the exact pinned version, which that devDependency satisfies; the lockfile regenerated with `npm install --package-lock-only` before the workflows' `npm ci` |
| Ruby: native and Fiddle | `ruby-native-<rid>-<abi>`, the leg's native library | `ruby/lib` | Gemfile `gem "hypercast", path:` with the extension staged where `lib/hypercast.rb` looks |
| Java: JNI and FFM | `jni-<rid>`, `native-<rid>` | `java/` | HyperCast published into the job-local repository `.companions/m2/` (`-Dmaven.repo.local`), declared first as a `maven { url }` repository by a Gradle init script, with a `resolutionStrategy.force` to the pinned version |
| C#: desktop, Native AOT, Blazor, Apple, Android | `native-<rid>`, `static-libs`, `wasm-emscripten-staticlib`, the mobile natives | `csharp/` | `dotnet pack` HyperCast into the job-local feed `.companions/nuget/`, which a generated `nuget.config` makes the *only* source for `HyperCast` (package source mapping); the `HyperCast` requirement rewritten to `[<pinned>]` (HyperTabular's is a literal PackageReference, not `HyperCastVersion`) |
| PHP | `php-ext-<rid>-<minor>` | (none) | loaded with `-d extension=<absolute path>`; Composer told to ignore `ext-hypercast`'s version; the lint takes no companion (phpcs loads no extension) |
| Swift: macOS, Linux, static, Android | `swift-release-assets` (the artifactbundle and xcframework) | `swift/` | the requirement rewritten to `.package(path: ".companions/hypercast")`, the pin's root package, with the bundle unpacked into it (`swift package edit` needs the remote resolvable first, and xcodebuild ignores it) |
| R | (none) | `r/`, `rust/` | HyperCast's `vendor.sh`, then `R CMD INSTALL` of a source tree whose `DESCRIPTION` the job stamps with `Hyper-Pin: <commit>`; the consumer's floor relaxed in the job |
| C and C++: smoke, musl, Apple mobile | `c-packages-*` | (none) | `HYPERCAST_PACKAGES=<dir>`, which `c/smoke/hypercast.sh` already honours, in place of the release download |

The host steps go into the Forge workflows the legs already call: `hyper-core`,
`hyper-core-checks`, `hyper-build-*`, `hyper-lint-*`, `hyper-test-*`. Each workflow gains one
`fetch-companions` step and no new inputs, because the action reads `hyper.json`, and the
run's purpose from the event (`hyper_mode.py`). `hyper-pie-acceptance.yml` gets no fetch step:
it is a release-only check, run after publishing, that installs the released companion from
Packagist.

### Caches never cross modes

A pin-built "HyperCast 1.0.0" with 1.1 code inside must never reach a cache a release-mode job
restores. So:

- Companion-mode jobs keep every cache that could hold the companion job-local (NuGet's
  global packages folder), keyed with the pin's commit (Bundler, R, the ruby.wasm build tree,
  cargo-semver-checks' rustdoc), or off (pip, Gradle, Maven).
- The Go and npm caches are exempt, because they are content-addressed: a Go `replace` never
  enters the module cache, and npm's cache is keyed by integrity, so neither can serve the
  pin's bytes for the registry's.
- Release-mode jobs only restore keys that carry no pin.
- A Forge test reads every cache step in the workflows and holds that rule.

## The release

### `companion-gate`

`companion-gate` is the release-mode step of the `companions` job, after its guard; every
board job needs that job, so in a release-mode run (a `release` dispatch) it runs before any
build. It reads the version of each companion the consumer's manifests require; they must all
agree, or the gate fails. It then checks that version is live:

| Where | Checked by |
|---|---|
| crates.io | the sparse index |
| NuGet | the flat container |
| Maven Central | `maven-metadata.xml` |
| PyPI | the JSON API |
| RubyGems | the versions API |
| npm | the root package and every platform package |
| Go | the module proxy (`go/vX.Y.Z`) |
| Packagist | the package's versions |
| the companion's GitHub Release | the PIE zips, the two Swift zips, the C archives and the R tarball |

CRAN is not checked: its review takes up to ten business days, and the consumer's own CRAN
submission follows the companion's by hand. The gate fails with every missing item listed.
Any companion override, or a `.companions/` directory, found in release mode is also a
failure.

### R in release mode

R takes the companion from its GitHub Release, never from CRAN:

- `finalize-release` attaches the preserved, attested `cran/<name>_X.Y.Z.tar.gz` to every
  release's GitHub Release, beside the PIE zips and C archives.
- The R jobs add `url::<that asset>` to `setup-r-dependencies`.

This also gives users an install path while CRAN reviews. The CRAN submissions happen after
the release and in order: the companion first, then the consumer once the companion is on
CRAN. Both come from preserved bytes, whose 90 days cover both reviews.

## Failure modes

Each failure stops at the first sign, with one message that says what to do.

| Failure | Where | Message |
|---|---|---|
| No pin, or a malformed one | `hyper_config.py` | names `hyper.json` and the field |
| Run red, gated, foreign, at another commit, or commit not on the default branch | `fetch-companions`, `companion_pin.py` | names the run and the rule |
| A dispatch with no `purpose` | GitHub, since the input is required | names the input |
| No App key: a fork's pull request, or the secret missing | `companions` | says a fork's PR can't fetch the companion and a maintainer re-runs it from a branch; otherwise names the variable or secret to set |
| The App lacks access to the companion repo | `companions` | names the repo the installation must include |
| A leg without the `companions` artifact in its run | `fetch-companions` | "this repo's `ci.yml` must call `hyper-companions.yml` and need it" |
| A file differs from the `companions` job's `SHA256SUMS` | `fetch-companions` | names the file |
| Artifacts expired (90 days) | `companions` | "re-run `<repo>` CI at `<sha>` with `purpose=board` and move the pin" |
| An artifact the leg needs is absent | `fetch-companions` | names the leg and the artifact expected |
| **An override that did not take** | each leg and each isolated environment, right after wiring | names the binding, and the origin it found against the origin it expected |
| The companion's version not live | `companion-gate` | lists every missing registry |
| An override or `.companions/` in release mode | `companion-gate` and `fetch-companions` | names it |

The silent failure is the dangerous one: a leg that quietly tests a registry copy, or nothing.
A version cannot tell them apart, so each check proves **origin**, and **integrity** where the
package manager records a hash:

| Binding | Origin | Integrity |
|---|---|---|
| Rust | `cargo tree -i hypercast` names the path, not a registry; the "patch was not used" warning promoted to an error | n/a (path source) |
| Go | `go list -m -json` shows the `Replace` directory | n/a (path source) |
| Python, pip (wheel) | `pip inspect`: the PEP 610 `direct_url.json` names the pinned wheel file (a registry install has none) | its `archive_info.hashes.sha256` equals the wheel's in `SHA256SUMS` (`RECORD` holds per-file hashes and is not compared) |
| Python, pip (sdist) | the same `direct_url.json`, naming the pinned sdist | its `archive_info.hashes.sha256` equals the sdist's in `SHA256SUMS` |
| Python, Pyodide in Node (`suite-pyodide.mjs`) | `micropip.list()["hypercast"].source` is `emfs:` plus the path the runner wrote the pinned wheel to | the SHA-256 of the bytes at that `emfs:` path, computed in the interpreter before `micropip.install`, equals the one in `SHA256SUMS` |
| Python, Pyodide in the browser (`browser-pyodide.mjs`) | `micropip.list()["hypercast"].source` is the runner's `http://127.0.0.1:<port>/deps/<wheel>` URL, which serves only files from `PYODIDE_SUITE_DEPS` | the page fetches that URL and hashes the bytes with `crypto.subtle.digest` before `micropip.install`; the digest equals the one in `SHA256SUMS`. The runner reads each dependency wheel into memory once, checks that buffer against `SHA256SUMS`, and serves only that buffer for its URL, so the page's fetch and micropip's receive the same bytes |
| npm | `package-lock.json`'s `resolved` is the `file:` tarball | its `integrity` equals the tarball's |
| C# | the global packages folder's `.nupkg.metadata` names `.companions/nuget/` as its `source` | its `contentHash` equals the packed `.nupkg`'s |
| Java | the resolved jar's path is under `.companions/m2/`, not Gradle's remote cache | its SHA-256 equals the jar the job published |
| Ruby | `bundle info hypercast --path` is the companion's `lib/` source; `HyperCast::BACKEND` is `:native` from the staged extension's path | the loaded extension's SHA-256 is in `SHA256SUMS` |
| PHP | `ReflectionExtension` loaded, and the `-d extension=` path is the artifact's | the loaded file's SHA-256 is in `SHA256SUMS` |
| R | `packageDescription("hypercast")$"Hyper-Pin"` equals the pin; no `Repository` field | n/a (built in the job) |
| Swift | `swift package show-dependencies --format json` gives the rewritten path | the bundle's checksum equals the artifact's |
| C | `hypercast.sh` reports the `HYPERCAST_PACKAGES` directory | the archive's SHA-256 is in `SHA256SUMS` |

The negative control for every row runs **with a same-version registry copy reachable**.
HyperCast 1.0.0 is on crates.io, NuGet, Maven Central, PyPI and RubyGems, so removing an
override lets the registry copy in, and the check must fail. Where no registry copy exists
(npm, the Go proxy, PIE, the C archives), the control removes the override and requires the
build to fail or the check to name the missing origin.

## Proof

- **Forge unit tests:**
  - `hyper_config.py`'s validation;
  - `companion_pin.py` against recorded API responses: green, red, gated, foreign, other
    commit, unreachable, and the `purpose` in the run name;
  - a test that enumerates every matrix leg of every Forge workflow, and every container,
    isolated interpreter and consumer-test entry point, and requires each to map to its
    artifacts, its override and its origin check;
  - each ecosystem's requirement rewrite, against fixture manifests (HyperTabular's npm
    manifest among them: the rewrite must leave no `overrides` entry for a direct
    dependency);
  - the release-tail gates: every tail job in every repo's `ci.yml` requires
    `purpose == 'release'`, and `dispatch_preparation.py` sends it;
  - the legacy inputs' compatibility, with and without a `companions` block;
  - the cache rule;
  - the `companions` job has no job-level `if:`, and no step of it but the release-mode
    guard and gate runs when `purpose` is `release`;
  - no Forge workflow or action references `inputs.purpose`; each mode condition is the
    event-based one, and `hyper_mode.py` is tested against recorded event payloads
    (`workflow_dispatch` with `board`, `release`, an unknown value and none; `pull_request`;
    `schedule`; `push`);
  - the secret rule: in every repo's `ci.yml`, the App key is passed to `hyper-companions.yml`
    and to nothing else; with a `companions` block, every job calling a Forge board workflow
    needs `companions`; and only `hyper-companions.yml` among the Forge workflows declares the
    secret;
  - the gate's registry probes against recorded responses.
- **Negative controls:**
  - a pin at a red or a gated run fails the fetch;
  - a `board` dispatch runs no tail job;
  - each origin check, with its override removed and a same-version registry copy reachable,
    fails;
  - an npm pack without the job-local `files` list yields a tarball the origin check rejects
    (no addon);
  - `companion-gate` fails for HyperCast 1.1.0 today, listing every registry;
  - an override or `.companions/` left in release mode fails;
  - without the key (the secret unset in a test dispatch) the `companions` job fails with its
    message, and no leg starts;
  - credentials that demonstrably lack Actions read on HyperCast cannot download: a token
    minted from the App with only `permission-metadata: read` (omitting every permission
    input would inherit the installation's Actions read, so it is no control), and the
    consumer's own `GITHUB_TOKEN`; each must be refused, while the `permission-actions: read`
    token downloads the same artifact;
  - a `release` dispatch with a companions block, crossing the reusable-workflow boundary:
    `companions` (a called workflow) reports release mode, its mint and download steps are
    skipped, `fetch-companions` in a called board workflow does nothing, and every board and
    tail job starts (the jobs actually run, not just evaluate their `if:`). The same test
    with the event condition swapped for `inputs.purpose` must mint, which proves the test
    catches the boundary;
  - a byte changed in a mirrored file fails `fetch-companions`' checksum.
- **Positive control:** `companion-gate` passes for a release that is live everywhere but CRAN
  and whose GitHub Release carries the R tarball. HyperUuid 1.1.0's Release doesn't carry it,
  so the gate fails HyperUuid 1.1.0 on that one item.
- **Locally first:**
  - each override and its origin check, on macOS against a pin built from the HyperCast
    checkout;
  - the musl container paths in Docker on this machine;
  - Node Pyodide, before CI.
- **No change for repos without companions:** HyperUuid's and HyperCast's boards run the same
  jobs, with the same results, before and after; their release dispatches still prepare a
  release.
- **Acceptance:** HyperTabular's 1.1 port PR goes green with only the pin added. That run also
  lets the 30 lint-gated suites run, for the first time on this port.

Only CI can prove:
- the App token's cross-repo download, and the fork and Dependabot behaviour;
- the Windows, Android, Apple mobile and s390x legs;
- the gate's registry probes against the live registries.

## Rollout

Each step leaves every repo working, and no step needs two repos merged at once.

0. **The owner creates the App**, before step 4 is exercised:
   - a SkunkWerkx-owned GitHub App, webhook off, one repository permission: Actions, read
     (Metadata read comes with it);
   - installed on HyperCast only (later companions are added to the installation);
   - its id as the org variable `HYPER_COMPANION_APP_ID`, its private key as the org secret
     `HYPER_COMPANION_APP_KEY` and as an org Dependabot secret of the same name, both visible
     to the consumer repos only (HyperTabular today).
1. **Every repo's `ci.yml` takes `purpose`, defaulting to `release`.**
   - The tail gates add `inputs.purpose == 'release'`, and the `run-name` carries the purpose.
   - Behaviour is unchanged: a plain dispatch still prepares a release.
   - HyperUuid, HyperCast and HyperTabular each change, with their contract tests.
2. **The Forge sends `purpose=release`** from `dispatch_preparation.py`. That is safe only
   after step 1, since GitHub rejects an input a workflow does not declare.
3. **Every repo makes `purpose` required, with no default.** From then on a dispatch must
   say what it is for.
4. **The Forge**, in one pull request, with the legacy inputs kept working:
   - `hyper_config.py`'s `companions`;
   - `companion_pin.py`, `companion_map.py`, `hyper-companions.yml`, `fetch-companions` and
     `companion-gate`;
   - the step in each workflow, and the isolated environments' wiring;
   - `finalize-release` attaching the R tarball;
   - the tests;
   - the runbook: moving a pin, the release order (companion first, CRAN by hand after), and
     the `purpose` input.
5. **HyperCast:** a `purpose=board` dispatch of `ci.yml` at master. That run is the first pin.
   No file changes.
6. **HyperTabular:**
   - `.github/hyper.json` gains the pin;
   - `ci.yml` calls `hyper-companions.yml` with the App key, and every board job needs it;
   - `ci.yml`'s `php_companions` and `release.yml`'s `companions:` are removed, now that the
     block supplies them;
   - CLAUDE.md and `.github/RELEASING.md` say how to move the pin, that HyperCast releases
     first, and that the gate holds the release until it is live everywhere but CRAN;
   - the 1.1 port PR re-runs to green.
7. **The Forge drops the two legacy inputs**, once no consumer's workflows pass them.

## Out of scope

- A HyperCast push trigger on master. Pins come from `board` dispatches.
- Testing HyperTabular against HyperCast master continuously. That can be a scheduled job
  later, moving a pin in a pull request when it passes.
- An r-universe. The GitHub Release tarball is the interim R channel.
- Overriding a companion for a local developer loop. The repos' existing local paths
  (`HYPERCAST_PATH`, `local-core.sh`) stay as they are.
