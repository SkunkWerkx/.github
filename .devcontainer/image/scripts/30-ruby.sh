# Ruby (vscode): rbenv + ruby-build, newest stable release (floats) with YJIT (needs rustc, so Rust is installed first).
source /usr/local/share/hyperforge/lib.sh
git clone --depth 1 https://github.com/rbenv/rbenv.git "$RBENV_ROOT"
git clone --depth 1 https://github.com/rbenv/ruby-build.git "$RBENV_ROOT/plugins/ruby-build"
V=$(rbenv install --list-all | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)
RUBY_CONFIGURE_OPTS="--enable-yjit" MAKE_OPTS="-j$(nproc)" rbenv install "$V"
rbenv global "$V"
rbenv rehash
