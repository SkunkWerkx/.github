# SkunkWerkx dev image: one current toolchain per language for the whole Hyper* stack. Design: docs/devcontainer.md.
# Plain Dockerfile on purpose (no BuildKit-only syntax, no heredocs, no --platform): it has to build with `wslc build`
# as well as `docker build`. Build context = this folder.
#
# Toolchains live under /opt and /usr/local, NOT under /home/vscode: the home directory is a named volume
# (skunkwerkx-home), and a volume is only seeded from the image when it is first created, so anything installed in
# $HOME would never update with the image. Home keeps caches and auth only.
#
# Layer order: SLOW layers first, so editing a fast one never rebuilds a slow one.
#   apt -> Rust (Ruby's YJIT needs rustc, cargo-php needs cargo) -> the slow four: Ruby, PHP (compiled),
#   R (compiles its packages), Swift (~2 GB of downloads) -> the fast ones: Go, Python (uv), Java, Node, tools
#   -> .NET last.
# Each layer's ENV sits next to it, so changing one language's variables only rebuilds from that layer down.
FROM mcr.microsoft.com/devcontainers/base:trixie

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

COPY scripts/lib.sh /usr/local/share/hyperforge/lib.sh

COPY scripts/10-apt.sh /tmp/hf/
RUN bash /tmp/hf/10-apt.sh

# /opt is vscode-owned so each manager creates its own root (the SDKMAN installer refuses an existing SDKMAN_DIR).
RUN chown vscode:vscode /opt && install -d -o vscode -g vscode /usr/local/dotnet-tools

# bindgen uses Debian's libclang, never Swift's bundled clang (CI sets the same).
# ~/.local/bin is where Claude Code's native installer lives.
ENV LIBCLANG_PATH=/usr/local/lib/llvm/lib \
    CLANG_PATH=/usr/bin/clang \
    PATH=/home/vscode/.local/bin:$PATH

USER vscode
WORKDIR /home/vscode

# ---- Rust (first: Ruby and PHP build on it) ----
ENV RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo \
    PATH=/opt/cargo/bin:$PATH
COPY scripts/20-rust.sh /tmp/hf/
RUN bash /tmp/hf/20-rust.sh

# ---- slow layers ----
ENV RBENV_ROOT=/opt/rbenv \
    PATH=/opt/rbenv/shims:/opt/rbenv/bin:$PATH
COPY scripts/30-ruby.sh /tmp/hf/
RUN bash /tmp/hf/30-ruby.sh

ENV PHPENV_ROOT=/opt/phpenv \
    PATH=/opt/phpenv/shims:/opt/phpenv/bin:$PATH
COPY scripts/40-php.sh /tmp/hf/
RUN bash /tmp/hf/40-php.sh

USER root
COPY scripts/50-r.sh /tmp/hf/
RUN bash /tmp/hf/50-r.sh
USER vscode

# Swift: toolchains under /opt (not ~/.local/share). PATH ENDS with the in-use toolchain: it carries its own clang,
# which must not shadow Debian's (see 60-swift.sh).
ENV SWIFTLY_HOME_DIR=/opt/swiftly \
    SWIFTLY_BIN_DIR=/opt/swiftly/bin \
    SWIFTLY_TOOLCHAINS_DIR=/opt/swiftly/toolchains \
    PATH=$PATH:/opt/swiftly/bin:/opt/swiftly/current/usr/bin
COPY scripts/60-swift.sh /tmp/hf/
RUN bash /tmp/hf/60-swift.sh

# ---- fast layers ----
ENV GOPATH=/home/vscode/go \
    PATH=/usr/local/go/bin:/usr/local/go-tools/bin:/home/vscode/go/bin:$PATH
COPY scripts/70-go.sh /tmp/hf/
RUN bash /tmp/hf/70-go.sh

ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PYTHON_BIN_DIR=/opt/uv/bin \
    UV_TOOL_DIR=/opt/uv/tools \
    UV_TOOL_BIN_DIR=/opt/uv/bin \
    PATH=/opt/uv/bin:$PATH
COPY scripts/75-python.sh /tmp/hf/
RUN bash /tmp/hf/75-python.sh

ENV SDKMAN_DIR=/opt/sdkman \
    SDKMAN_CANDIDATES_DIR=/opt/sdkman/candidates \
    JAVA_HOME=/opt/sdkman/candidates/java/current \
    PATH=/opt/sdkman/candidates/java/current/bin:/opt/async-profiler/bin:$PATH
COPY scripts/80-java.sh /tmp/hf/
RUN bash /tmp/hf/80-java.sh

ENV BUN_INSTALL=/opt/bun \
    PATH=/usr/local/node/bin:/opt/bun/bin:$PATH
COPY scripts/85-node.sh /tmp/hf/
RUN bash /tmp/hf/85-node.sh

COPY scripts/90-tools.sh /tmp/hf/
RUN bash /tmp/hf/90-tools.sh

# ---- .NET: last, so a new SDK rebuilds only this layer ----
ENV DOTNET_ROOT=/opt/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_USE_POLLING_FILE_WATCHER=1 \
    PATH=/opt/dotnet:/usr/local/dotnet-tools:$PATH
COPY scripts/99-dotnet.sh /tmp/hf/
RUN bash /tmp/hf/99-dotnet.sh

USER root
RUN rm -rf /tmp/hf
USER vscode
