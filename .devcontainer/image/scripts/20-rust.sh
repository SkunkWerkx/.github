# Rust (vscode): rustup stable + CI's components/targets, the wasm2go pinned toolchain, prebuilt dev tools.
source /usr/local/share/hyperforge/lib.sh
curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs | sh -s -- -y --no-modify-path --profile minimal \
  --default-toolchain stable -c clippy,rustfmt,llvm-tools,rust-src,rust-analyzer
rustup target add wasm32-wasip1 wasm32-unknown-unknown thumbv7em-none-eabi
# go/internal/wasm2go/rust-toolchain.toml pins this; preinstalled so regenerating the Go core needs no download.
rustup toolchain install 1.99.1 --profile minimal -t wasm32-wasip1
# MSRV: rust-version in every Hyper* rust/Cargo.toml (1.89 as of 2026-11-09), for local `cargo +1.89 check`.
# Raise it here when the repos raise rust-version.
rustup toolchain install 1.89 --profile minimal
case $UNAME_ARCH in aarch64) NEXTEST=linux-arm ;; *) NEXTEST=linux ;; esac
curl -fsSL "https://get.nexte.st/latest/$NEXTEST" | tar -xzf - -C "$CARGO_HOME/bin"
T=$(gh_latest_tag obi1kenobi/cargo-semver-checks)
install_bin "https://github.com/obi1kenobi/cargo-semver-checks/releases/download/$T/cargo-semver-checks-$UNAME_ARCH-unknown-linux-gnu.tar.gz" cargo-semver-checks
T=$(gh_latest_tag mstange/samply)
install_bin "https://github.com/mstange/samply/releases/download/$T/samply-$UNAME_ARCH-unknown-linux-gnu.tar.xz" samply
T=$(gh_latest_tag rustwasm/wasm-pack)
install_bin "https://github.com/rustwasm/wasm-pack/releases/download/$T/wasm-pack-$T-$UNAME_ARCH-unknown-linux-musl.tar.gz" wasm-pack
# wasmtime: CI pins 50.0.0.
install_bin "https://github.com/bytecodealliance/wasmtime/releases/download/v50.0.0/wasmtime-v50.0.0-$UNAME_ARCH-linux.tar.xz" wasmtime
