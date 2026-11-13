# workspace/

The Hyper* repos are cloned here, each one its own git repository on its own branch, and ignored by HyperForge's
git. `.devcontainer/post-create.sh` clones any that is missing, so cloning HyperForge and opening the dev container
is the whole setup. Outside the container, clone them here by hand:

    git clone https://github.com/SkunkWerkx/HyperCast.git    workspace/HyperCast
    git clone https://github.com/SkunkWerkx/HyperTabular.git workspace/HyperTabular
    git clone https://github.com/SkunkWerkx/HyperUuid.git    workspace/HyperUuid

The files beside them are committed in HyperForge. Each build tool finds its file by searching parent directories,
so development builds every repo against its peers' working trees instead of their published packages. CI checks
out one repository at a time, never sees this folder, and keeps resolving the pinned (companion) or released
versions.

`bash workspace/verify-peers.sh`, in the dev container, rebuilds and tests HyperTabular against `HyperCast/` in every
ecosystem below, checks that the Forge's guard refuses this layout and passes the tracked tree alone, and that the
run changed no tracked file.

| File      | Tool | What it does |
|-----------|------|--------------|
| `.cargo/config.toml` | Cargo | A `paths` override: every crate here builds against `HyperCast/rust`. No lock changes; expect Cargo's "altered the original list of dependencies" warning while HyperCast has dependencies its release lacks. |
| `go.work`, `go.work.sum` | Go | Every Go module here builds against the others; `go.work.sum` is its checksum file, committed like a lock. List each new `go.mod` in `go.work`. |
| `Gemfile.peers` | Bundler | Each companion gem by path, for a Gemfile whose two-line hook evaluates it (HyperTabular's `ruby/Gemfile`). `companion_fetch.py` (`gemfile_evals`) allows exactly that hook and no other `eval_gemfile`, and fails a release leg, as `companion_wire.py` fails a companion leg, where the file it names exists. |
| `package.json`, `bun.lock` | Bun | A workspace of the TypeScript packages that depend on each other: `bun install` in a member links its peers, and the lock is this one, never the member's. A new dependent package is added to `workspaces`. |
| `Directory.Build.targets` | MSBuild | Each `PackageReference` to a package whose project is checked out here (`<Repo>/csharp/<Repo>/<Repo>.csproj`) becomes a `ProjectReference` to it, when `CI` is unset. The csproj files keep the `PackageReference` that CI and the Forge's version gate read. A repo that adds a `Directory.Build.targets` of its own stops MSBuild's search below this one: it must import this one (`GetPathOfFileAbove`). |
| `HyperForge.slnx` | .NET | One solution over every repo's `csharp/` projects (the same ones as each repo's own `.slnx`), for C# Dev Kit in the dev container. Never built by CI. Regenerate it with `dotnet sln add --include-references false --solution-folder <Repo>` when a repo's `.slnx` changes. |

The ecosystems with no parent-directory file switch in the dependent repo itself, only when `CI` is unset and the
peer checkout is beside it. `CI` is a convenience, not the enforcement: the Forge fails a release leg (the guard)
and a companion leg (the wiring) that finds a companion checkout beside it, or any of the files above (a `paths`
override, a `go.work`, a workspace `package.json`, a hooked `Gemfile.peers`, a `Directory.Build.props` or
`.targets`); `peer_findings` in
`.github/scripts/companion_fetch.py`. A CI checkout has none of them.

| Ecosystem | Where | What it does |
|-----------|-------|--------------|
| Java | `java/settings.gradle.kts` | `includeBuild` of the peer's Gradle build. |
| Swift | `Package.swift`, `swift/Package.swift` | A computed `.package(path:)` to the peer, also never under a consumer's `checkouts/`. Both repos' cores must be built: `.github/scripts/local-core.sh` in each. |
| Python | `python/tests/conftest.py` | The peer's `python/src` on `sys.path`; its `_native` extension staged as CI stages it. |
| R | `r/tools/vendor.sh` | `HYPERCAST_PATH` defaults to the peer's `rust/`; the peer's R package installed with `R CMD INSTALL r` after its own `vendor.sh`. |
