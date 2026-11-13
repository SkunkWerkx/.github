# Dev container — engine-agnostic polyglot stack (plan)

**Status:** image built locally with wslc on the Spark (arm64) on 2026-11-09; the .NET layer (SDK 11.0.100, runtimes 11.0.0 and 10.0.15, dotnet-trace/counters) added at 11.0 GA on 2026-11-10. 15.2 GB before .NET. A smoke test as `vscode` ran every toolchain and tool; the results are under [Verify before building](#verify-before-building). Not yet done: opening it through VS Code, the GHCR publish workflow, and the timing check. Drafted 2026-11-09.

**Ownership:** this is a Hyper* concern and HyperForge owns all of it: the doc, the Dockerfile, the install logic and the image publish workflow. There is no dependency on the owner's personal toolchain repo (`buvinghausen/buvinghausen`, TOOLCHAIN.md). That repo is prior art to read, not something to consume. Lessons learned here get ported back to it by hand later.

## Goals

- **Same out-of-box experience on Windows, Linux and macOS.** Clone the repo, open it in VS Code, "Reopen in Container", and every Hyper* binding builds and tests with nothing installed on the host beyond VS Code and a container engine.
- **Works on any engine.** wslc, Docker Desktop and Docker on the Mac all run the same `devcontainer.json`. That means only what all of them support: a prebuilt image, `workspaceMount`/`mounts`, `forwardPorts`. No Dev Container Features, no Compose, no `build.platform`.
- **One current toolchain per language, not the CI matrix.** Floors, compat ABIs and older runtimes are CI's job (`HyperForge/.github/workflows/hyper-test-*.yml`). The container carries each language's current release, matching the Hyper repos' own rule: "Each language's primary toolchain is its current release" (HyperCast `CLAUDE.md`).
- **Machine roles.** The M6 Mac mini drives agent-based development. The RTX Spark (Surface Laptop Ultra, Windows 11 ARM64) is for hands-on work: cracking things open, poking around, tweaks, new ideas. The image is built for that hands-on use. No agent plumbing inside it beyond Claude Code itself.
- **Commit by hand, at the end, after review.** Git through the host bind mount pays the cross-OS file-sharing cost on Windows. That's accepted, because commits are occasional and deliberate.

## Engines

| | wslc (Windows, GA 2026-09-29) | Docker Desktop (Windows) | Docker (macOS) |
|---|---|---|---|
| `image` + `workspaceMount`/`mounts` | yes | yes | yes |
| Dev Container Features | **no** (needs BuildKit metadata `wslc build` doesn't emit) | yes | yes |
| `dockerComposeFile` | **no** (no Compose runtime; Microsoft's stated next focus) | yes | yes |
| Docker API socket (Testcontainers, docker-outside-of-docker, Verdaccio consumer tests) | **no** (daemonless, no socket, no HTTP API) | yes | yes |
| `build.platform` | **no** (host architecture only) | yes | yes |
| Windows-path bind mount transport | VirtioFS (~2x Docker Desktop's 9p) | 9p | VirtioFS |

Sources: [Windows Developer Blog, WSL containers GA](https://blogs.windows.com/windowsdeveloper/2026/09/29/wsl-containers-now-generally-available/); [wslcontainers.com dev containers guide](https://wslcontainers.com/guides/vscode-dev-containers/) and [Testcontainers guide](https://wslcontainers.com/guides/testcontainers/). Those guides are a community site verified against wslc **2.9.4.0 (preview)**. Microsoft's GA post doesn't cover these details, so the wslc column is re-checked on the Spark before relying on it.

**Consequence:** Docker Desktop is also installed on the Spark, because Testcontainers needs a Docker API. wslc is the everyday engine. Anything that needs a socket is documented as "Docker engine only" rather than designed around.

## Image

- **Base:** `mcr.microsoft.com/devcontainers/base:trixie` (Debian 13). MCR has no Fedora variant: the tag list checked 2026-11-09 has `debian*`, `trixie`, `bookworm`, `ubuntu*`, `noble` and no `fedora*`. Debian is preferred over Ubuntu.
- **Install logic is HyperForge's own and Debian-native:** `.devcontainer/image/` (Dockerfile + `scripts/NN-<lang>.sh`, one layer each). apt for system packages, and each language's official manager (rustup, SDKMAN, uv, rbenv/ruby-build, phpenv/php-build, swiftly, dotnet-install, the Go and Node tarballs). TOOLCHAIN.md's gotchas (wget2's `-P`, `cargo-php` needing `php` on `PATH`, `swiftlint-static`, GitHub API rate limits) are carried over where they apply, not imported as code. "Latest release" lookups follow GitHub's `/releases/latest` redirect instead of the API, so builds need no token.
- **Toolchains live under `/opt` and `/usr/local`, never `$HOME`.** `/home/vscode` is a named volume, and a volume is only seeded from the image when it's created, so toolchains in home would never update with the image. Home holds caches and auth only. A tool whose home dir mixes the two is split: `CARGO_HOME` is `/opt/cargo`, for rustup's proxies and the prebuilt tools, and `post-create.sh` links its `registry` and `git` caches into `~/.cache/cargo` on every create. (Claude Code is the exception: its native installer lands in `~/.local` and self-updates there.)
- **`apt-get upgrade` runs in the first layer**, so every rebuild picks up Debian security fixes the base image lacks (example: DSA-6582-1, the brotli CVE-2026-111497 fix, which PHP reaches through libcurl).
- **Published prebuilt:** a multi-arch `linux/arm64` + `linux/amd64` image on GHCR, each architecture built on a native GitHub runner (no QEMU; wslc can't cross-build anyway), **rebuilt on a weekly schedule** as well as on change. Repos reference it by tag, so opening a container is a pull, not a long build. Dev Containers doesn't re-pull a tag by itself: "Rebuild Container" picks up the new image.

### Version policy (decided 2026-11-09)

**The container floats; CI holds the floors.** Every toolchain resolves to the newest stable (or newest LTS, where the language has LTS lines) **at image build time**, so a weekly rebuild keeps it current with no version edits. Floors and compat versions are CI's job, and the CI matrix is revised whenever a line drops out of support (Node 20 recently; .NET 8 and 9 on 2026-11-10). Fixed versions are exceptions with a reason:

| Language | Floats | Fixed (why) | Plus |
|---|---|---|---|
| Rust | `stable` | **1.89** (MSRV = `rust-version` in every repo; raise with the repos), **1.99.1** (`go/internal/wasm2go/rust-toolchain.toml`) | clippy, rustfmt, llvm-tools, rust-src, rust-analyzer; targets `wasm32-wasip1`, `wasm32-unknown-unknown`, `thumbv7em-none-eabi`; cargo-nextest, cargo-semver-checks, samply, wasm-pack, wasmtime 50.0.0 (CI pin) |
| C | Debian's clang/LLVM (19 in trixie), CMake, pkg-config | — | clang-format, clangd. `LIBCLANG_PATH`/`CLANG_PATH` point at Debian's LLVM so Swift's bundled clang can't shadow it (CI does the same) |
| .NET | SDK, newest major (11 at GA 2026-11-10); last layer | .NET 10 runtime (CI's second TFM) | dotnet-trace, dotnet-counters |
| Java | newest LTS Temurin, default (SDKMAN's default candidate: 25 now, 29 in Sep 2027) | **21** until it leaves support: the Gradle floor JDK, JNI path (FFM is final only from 22, JEP 454); auto-detected, never downloaded | async-profiler; Gradle via wrapper |
| Python | newest **free-threaded** release (3.15 now; 3.16 in Oct 2027) | **3.14.x with the GIL**, default `python`: our last GIL line (a policy; CPython 3.15+ still defaults to the GIL). 3.15 is where free-threading becomes usable: abi3t, the free-threaded stable ABI (PEP 803, Final) | **uv only**: prebuilt CPython (python-build-standalone), no compiling, no pyenv; installs and tools under `/opt/uv`. uv's Pythons are externally managed (PEP 668), so projects use `uv venv`/`uv run`; ruff 0.17.3 (CI pin), mypy, maturin, pyflakes, pytest are `uv tool`s. No shared-libpython requirement (CSnakes isn't used) |
| Ruby | newest stable, YJIT | — | gems per repo Gemfile |
| PHP | newest stable (8.5 now; 8.6 GA 2026-11-19), **NTS (default) + ZTS** (`<ver>-zts`), since extensions install via PIE and NTS/ZTS is the delta that matters | — | PIE, Composer, cargo-php 0.2.2 (repo pin). **No FFI**: since 1.1 the bindings are native extensions; ext-ffi only backs two optional interop writers and `InteropTest` skips without it |
| Go | newest stable | — | gopls, dlv, revive 1.17.1, actionlint 1.7.12 (CI pins), TinyGo |
| TypeScript | Node **newest LTS** (26 now; 27 from 2027-10-20 under Node's one-major-a-year schedule), npm latest, Bun latest | — | Biome per repo |
| R | newest release, from the newest CRAN `trixie-cranNN` suite (`trixie-cran46` now) | — | rcmdcheck, testthat, lintr (what CI's R legs use) |
| Swift | newest release via swiftly, plus its **static Linux SDK** (`hyper-test-swift-static`) | — | SwiftLint 0.65.1 (CI pin, static binary) |
| Cross-cutting | gh, ShellCheck, pwsh, hyperfine, Claude Code | | |

Tools whose output CI compares (ruff, revive, actionlint, SwiftLint, wasmtime, cargo-php) stay pinned to CI's versions, because a different linter version gives different results.

**Node's LTS policy, for the CI matrix:** CI tests the current LTS plus every LTS line still in maintenance. Today that's 26 (current) plus 22 and 24, which matches the defaults in `hyper-test-typescript.yml`, `hyper-build-native.yml` and `hyper-build-wasm.yml`. Node 22 reaches end of life on 2027-04-30.

- **Left out on purpose:** Android NDK (no aarch64 Linux host build), emsdk and the .NET `wasm-tools` workload (add only when working on the Emscripten target), GraalVM, Mono, floor versions other than the fixed ones above (CI matrix), `cargo no-panic` (needs GNU ld, which Debian has; add when wanted).

## Docker mode (Testcontainers)

A container started by wslc has no Docker socket, so Testcontainers can't run inside it. When a session needs it, reopen the same container through Docker Desktop: set `dev.containers.dockerPath` to `docker` and add a bind mount of the engine socket (`/var/run/docker.sock`). That's a plain `mounts` entry, not a Feature, so the any-engine rule still holds. The other option is running those tests on bare metal. Docker Desktop on the Spark is a per-user install (29.9.3, linux/arm64, containerd image store), started by hand only, never at sign-in.

## Where it lives: HyperForge, one container for the whole stack

Decided 2026-11-09: the dev container, its Dockerfile, and the image publish workflow live in **HyperForge**, next to the CI it already owns. They are kept apart from the reusable workflows, so a dev container change can't alter what consumers run at `@master`.

One container holds the **entire SkunkWerkx stack**, not one repo:

- `HyperForge/.devcontainer/devcontainer.json`, with `image` set to the GHCR tag.
- `workspaceMount`: the **parent** folder (`D:\src\SkunkWerkx` on the Spark) at `/workspaces/SkunkWerkx`. HyperCast, HyperTabular, HyperUuid and HyperForge all sit side by side, and any newly cloned SkunkWerkx repo appears without a config change. This replaces the Compose-only sibling mount the buvinghausen container uses today.
- `HyperForge/SkunkWerkx.code-workspace`: a multi-root workspace with one root per repo. Each repo keeps its own rust-analyzer project, CLAUDE.md and git status.
- **Generated folders off the bind mount, into named volumes:** one `skunkwerkx-home` volume at `/home/vscode` holds the cargo, Bun, NuGet, pip, Gradle and Go module caches and auth state (gh, claude), which survives rebuilds. Build output is per repo, so the cores don't contend for one lock: `CARGO_TARGET_DIR=/home/vscode/.cache/target/<repo>`, and a `node_modules` named volume per repo's `typescript/`. Builds read source across the mount but write output at native speed.
- **Optional per-repo stub:** each Hyper repo carries a ~10-line `devcontainer.json` pointing at the same image, so someone who clones only one repo gets the same setup without HyperForge beside it.
- SSH agent forwarding plus `configure-git-ssh-signing.sh`, carried over from the existing buvinghausen container.
- `customizations.vscode.extensions`: the extension list below.

## VS Code extensions (inside the container)

rust-analyzer, CodeLLDB, Even Better TOML, C# Dev Kit, clangd, Go, Extension Pack for Java, Python + Pylance, Ruff, Biome, Ruby LSP, Intelephense, R (`reditorsupport.r`), Swift (`swiftlang.swift-vscode`), GitHub Actions, EditorConfig, Claude Code. On the host: Dev Containers, Remote - WSL.

## Bare metal (Spark only)

The win-arm64 CI legs can't run in a Linux container. Native, on `D:\src` (Dev Drive): rustup with `aarch64-pc-windows-msvc` (needs VS Build Tools C++ ARM64), and the .NET 11 SDK (per-user `dotnet-install`). That covers the C ABI and the C# binding on Windows, and also real .NET Framework for SequentialGuid/TaskTupleAwaiter net472 legs (no Mono needed there). Python/Java/Ruby/Go/TS natively only when a Windows-specific bug appears. PHP and R Windows legs are x64-only in CI, so they stay in CI.

## Verify before building

1. **Partly done 2026-11-09.** The Spark has WSL 3.0.3.0 with wslc 3.0.3.0 at `C:\Program Files\WSL\wslc.exe`, which is newer than the 2.9.4.0 preview the community guides tested. `wslc --help` has no `compose` command. `wslc build` has no `--platform` option. `wslc info` shows a session manager, not a daemon or socket. `wslc run` does have `--mount`, `-v`, `-u` and `--gpus`, and `wslc volume` exists. So the Engines table holds for the GA build. Engine-level runs (`wslc run -u vscode`, image `devcontainers/base:trixie` pulled in 24 s, `aarch64`):
   - **Bind mount of `D:\` as non-root works.** A file written by `vscode` landed on D: as uid 1000. The community guide's permission warning didn't reproduce.
   - **A named volume over `/home/vscode` works.** wslc copies the image's home in with uid 1000 ownership, and the contents survive across runs.
   - **A named volume at a path the image doesn't have comes up root-owned**, and `vscode` gets `Permission denied`. That's also Docker's behaviour. Rule: every volume mount target (for example each repo's `typescript/node_modules`) is either created in the image owned by `vscode`, or chowned in `postCreateCommand`.
   - Dev Containers extension v0.469.0 installed (wslc needs ≥ 0.462.0).

   - **Through VS Code: done 2026-11-09.** With `dev.containers.dockerPath` set to `wslc`, "Reopen in Container" on a throwaway folder (`image` trixie, `remoteUser` vscode, a named volume over `/home/vscode` in `mounts`, a `postCreateCommand`) came up with no errors. The workspace is at `/workspaces/<folder>` over **virtiofs** (`drvfs`, rw). The home volume is ext4 and holds `.vscode-server`. `postCreateCommand` ran as uid 1000 on `aarch64` and wrote back to D:. The extension also creates its own `vscode` volume.

   Originally listed for this check: `dev.containers.dockerPath = wslc`, named volumes from `devcontainer.json`, and a non-root `vscode` user writing to the Windows bind mount (the community guide flags permission problems).
2. **Done 2026-11-09 (covered by item 1):** wslc 3.0.3 has no `compose` command and no socket. Re-checked 2026-11-10 on 3.0.5: still neither (`wslc --help`, `wslc system`, `wslc info`). Re-check on wslc upgrades.
3. **Done 2026-11-09:** swiftly 1.2.0 detects `debian13` on its own (`config.json` platform `debian13`, no `--platform` needed). swift.org publishes Debian 13 toolchains for `x86_64` and `aarch64` (6.4.1 and 6.4.2), and `swiftly list-available 6.4` lists 6.4.2. The TOOLCHAIN.md `debian12`-only note is stale.
4. **Done 2026-11-09:** Debian trixie ships R 4.5.0, and CRAN's `trixie-cran40` suite stops at 4.5.3 (old-release). The current release (4.6.2, 2026-10-29) is only in CRAN's **`trixie-cran46`** suite, which has amd64 and arm64 builds (`r-base-core 4.6.2-1~trixiecran.0`). CRAN created new suites for 4.6 because packages with compiled code break across the upgrade, so the image uses `trixie-cran46` from the start. CRAN packages themselves (devtools, lintr) still install from source on Linux, so build time is part of item 5.
5. **First local build, 2026-11-09 (wslc 3.0.3, arm64).** Smoke test as `vscode`: rustc 1.99.1 (+1.89 MSRV, +1.99.1), nextest 0.9.151, semver-checks 0.51.0, samply 0.13.1, wasm-pack 0.15.1, wasmtime 50.0.0; Debian clang 19.1.7 with `which clang` = `/usr/bin/clang` (Swift's stays behind it); Go 1.27.3, gopls, dlv, revive 1.17.1, actionlint 1.7.12, TinyGo 0.44.0; Python 3.14.10 + 3.15.2 free-threaded (`gil False`; built with pyenv in that run, uv since), ruff 0.17.3, maturin 1.16.0; Ruby 4.0.9 with YJIT; PHP 8.5.12 NTS + ZTS, Composer 2.11.1, PIE 1.5.5, cargo-php 0.2.2; Temurin 25.0.5 (default) + 21.0.13; Node 26.13.0, npm 12.2.2, Bun 1.4.4; R 4.6.2, lintr 3.4.0; Swift 6.4.2 + static Linux SDK, SwiftLint 0.65.1; gh, ShellCheck, pwsh 7.6.9, hyperfine, Claude Code. Things the build found:
   - SDKMAN: `sdk list java` is useless non-interactively, and its init script isn't `set -eu` clean. Versions come from its API (`/2/candidates/java/<platform>/versions/all`, `/2/candidates/default/java`). Temurin ids carry a build number (`25.0.5.0+8-tem`).
   - swiftly 1.2.0 needs `binutils-gold` on debian13 (it exits 1 without it), defaults toolchains to `~/.local/share` (set `SWIFTLY_TOOLCHAINS_DIR`), and puts no `swift` proxy in its bin dir, so PATH points at `/opt/swiftly/current/usr/bin`. Swift SDKs go to `/opt/swiftly/swift-sdks`, linked from `~/.swiftpm/swift-sdks` (re-linked by `postCreateCommand`).
   - `swift sdk install` once reported success and then aborted with a core dump on exit, so the build checks `swift sdk list` instead of the exit code.
   - **Opened through VS Code with wslc, 2026-11-09:** works (parent folder on virtiofs, home volume ext4, `postCreateCommand` ran). The Swift extension found swiftly and ran everything as `swiftly run swift …`, which failed in each repo root: swiftly 1.2.0 resolves `swift` against the current directory first, and every Hyper* root has a `swift/` folder. Fixed with `"swift.path": "/opt/swiftly/current/usr/bin"` in `devcontainer.json`, so the extension uses the toolchain directly.
6. Bind-mount build timings with and without the named-volume split, on the Spark (wslc vs Docker Desktop) and on the Mac, before calling the layout done.

## Open questions

- Whether this doc stays standalone or folds into `README.md` (the Forge's design record) once the container is built and verified.

## Port back to buvinghausen later (not part of this work)

Whether the personal `.devcontainer` (Debian + Features + Compose, .NET OSS + Mono) adopts the same engine-agnostic rules. Plus TOOLCHAIN.md drift found while planning this:

- Add Bun, R, CMake/pkg-config (none are in the doc or scripts today).
- .NET: SDK 11 becomes primary at GA (2026-11-10); drop the 8.0/9.0 runtimes (both end support that day); retire the "11 Preview — TEMPORARY" section.
- Machine and distro references: "Surface Snapdragon" / `FedoraLinux-44` → current state; receipts stay as dated history.
- Node: confirm what `fnm install --lts` resolves to today against CI's Node 26.
