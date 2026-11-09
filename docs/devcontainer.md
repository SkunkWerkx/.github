# Dev container — engine-agnostic polyglot stack (plan)

**Status:** design, not built. Drafted 2026-11-09. The image and container haven't been built yet. Engine checks done so far are recorded under [Verify before building](#verify-before-building), and every other claim about a tool is cited or listed there.

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
- **Install logic is HyperForge's own and Debian-native:** apt for system packages, and each language's official manager where TOOLCHAIN.md already found one that works (rustup, SDKMAN, pyenv, rbenv/ruby-build, phpenv/php-build `--with-ffi`, swiftly, dotnet-install, the Go tarball). TOOLCHAIN.md's gotchas (wget2's `-P`, `cargo-php` needing `php` on `PATH`, `swiftlint-static`, GitHub API tokens) are read and carried over where they apply, not imported as code.
- **Published prebuilt:** a multi-arch `linux/arm64` + `linux/amd64` image on GHCR, each architecture built on a native GitHub runner (no QEMU; wslc can't cross-build anyway). Repos reference it by tag, so opening a container is a pull, not a 30-minute build of 11 toolchains.
- **Contents (current release of each):**

| Language | Toolchain | Plus |
|---|---|---|
| Rust | rustup stable; clippy, rustfmt, llvm-tools, rust-src | targets `wasm32-wasip1`, `wasm32-unknown-unknown`, `thumbv7em-none-eabi`; cbindgen, cargo-nextest, cargo-semver-checks, samply |
| C | clang/LLVM (incl. `llvm-dlltool`, `llvm-readobj`), CMake, pkg-config | clang-format |
| .NET | SDK 11 (GA 2026-11-10) | the 10.0 runtime (CI's second TFM), dotnet-trace, dotnet-counters |
| Java | Temurin 25 (SDKMAN), Gradle via wrapper | async-profiler |
| Python | 3.14 (pyenv) | maturin, pytest, ruff, mypy |
| Ruby | 4.0 (rbenv) | rbspy; rubocop per repo Gemfile |
| PHP | 8.5 `--with-ffi` (phpenv) | cargo-php, Composer (bundled) |
| Go | 1.26 | gopls, dlv, revive, TinyGo, wasmtime |
| TypeScript | Node current + **Bun** | Biome per repo |
| R | R release | devtools, lintr |
| Swift | 6.4 (swiftly) | SwiftLint (static) |
| Cross-cutting | gh, actionlint + ShellCheck, pwsh, Claude Code, hyperfine | |

- **Left out on purpose:** Android NDK (no aarch64 Linux host build), emsdk and the .NET `wasm-tools` workload (add only when working on the Emscripten target), GraalVM, Mono, Ruby 3.4 / Python floors / JDK 21 / Node floors (CI matrix), `cargo no-panic` (needs GNU ld, which Debian has; add when wanted).

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
2. Whether Compose or a socket has landed in wslc since the 2.9.4.0 guides.
3. `swiftly` platform value on Debian 13 (it only recognized `debian12` among Debians in the last check, see TOOLCHAIN.md's Swift note).
4. R release and CRAN binary availability for Debian arm64.
5. Bind-mount build timings with and without the named-volume split, on the Spark (wslc vs Docker Desktop) and on the Mac, before calling the layout done.

## Open questions

- Whether this doc stays standalone or folds into `README.md` (the Forge's design record) once the container is built and verified.

## Port back to buvinghausen later (not part of this work)

Whether the personal `.devcontainer` (Debian + Features + Compose, .NET OSS + Mono) adopts the same engine-agnostic rules. Plus TOOLCHAIN.md drift found while planning this:

- Add Bun, R, CMake/pkg-config (none are in the doc or scripts today).
- .NET: SDK 11 becomes primary at GA (2026-11-10); drop the 8.0/9.0 runtimes (both end support that day); retire the "11 Preview — TEMPORARY" section.
- Machine and distro references: "Surface Snapdragon" / `FedoraLinux-44` → current state; receipts stay as dated history.
- Node: confirm what `fnm install --lts` resolves to today against CI's Node 26.
