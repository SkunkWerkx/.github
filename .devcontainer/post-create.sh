# Runs on every container create (devcontainer.json postCreateCommand), as vscode, after the home volume is mounted.
set -euo pipefail

git config --global --add safe.directory '*'
# LF in and out, whatever a repo's .gitattributes says (every Hyper* repo also forces it there): checkouts are left
# as committed, and a CRLF that slips in is converted on commit. The same settings belong in the host's global git
# config, since the host checks out the bind-mounted trees (Git for Windows defaults to core.autocrlf=true).
git config --global core.autocrlf input
git config --global core.eol lf

# The Hyper* repos, nested under workspace/ beneath the committed workspace files (workspace/README.md). Only a
# missing one is cloned, on its default branch; an existing clone is never touched. A failed clone (offline) only
# warns: the container is still usable, and the next create tries again.
for repo in HyperCast HyperTabular HyperUuid; do
  [ -d "/workspaces/HyperForge/workspace/$repo/.git" ] \
    || git clone "https://github.com/SkunkWerkx/$repo.git" "/workspaces/HyperForge/workspace/$repo" \
    || echo "post-create: cloning $repo failed"
done

# Swift SDKs: re-link into home every time; the home volume is only seeded from the image once.
mkdir -p ~/.swiftpm ~/.local/bin
ln -sfn /opt/swiftly/swift-sdks ~/.swiftpm/swift-sdks

# Cargo's download caches into the home volume. CARGO_HOME is /opt/cargo, in the image, for rustup's proxies and the
# prebuilt tools; left there, its registry and git checkouts would go with every container rebuild. The links live in
# the container layer, so they are made again on every create; whatever the image itself left there is dropped.
mkdir -p ~/.cache/cargo/registry ~/.cache/cargo/git
for d in registry git; do
  [ -L "$CARGO_HOME/$d" ] || rm -rf "${CARGO_HOME:?}/$d"
  ln -sfn ~/.cache/cargo/$d "$CARGO_HOME/$d"
done

# R's user library (R_LIBS_USER, in the home volume): R only uses it once it exists, and the site
# library is root's, so without it `R CMD INSTALL` of a peer package (HyperCast's, for HyperTabular)
# has nowhere to go. The path names R's minor version, so a new R gets a new one here.
mkdir -p "$(Rscript -e 'cat(Sys.getenv("R_LIBS_USER"))')"

# Agent CLIs: native installers, installed into the home volume only if missing, so each one's own
# `claude update` / `codex update` / `opencode upgrade` sticks across container rebuilds. Installing them in the
# image would not work: a volume is only seeded from the image when it is first created.
# A failed download must not break the container, so each install only warns.
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash \
  || echo "post-create: Claude Code install failed"
# Codex: ~/.local/bin/codex -> ~/.codex/packages/standalone; `codex update` reruns this installer.
command -v codex >/dev/null || curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh \
  || echo "post-create: Codex install failed"
# OpenCode: the installer's location is fixed at ~/.opencode/bin, and `opencode upgrade` only recognizes its own
# installs there (it compares realpaths), so link it onto PATH instead of moving it.
[ -x ~/.opencode/bin/opencode ] || curl -fsSL https://opencode.ai/v2/install | bash -s -- --no-modify-path \
  || echo "post-create: OpenCode install failed"
[ -x ~/.opencode/bin/opencode ] && ln -sfn ~/.opencode/bin/opencode ~/.local/bin/opencode
true
