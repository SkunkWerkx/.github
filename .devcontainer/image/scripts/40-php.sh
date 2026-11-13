# PHP (vscode): phpenv + php-build, newest stable release (floats: 8.5 now, 8.6 once php-build lists it after the
# 2026-11-19 GA) as NTS (global) and ZTS, Composer, PIE, cargo-php at the repos' pin.
# Extensions install via PIE, so the NTS/ZTS split is the delta that matters (CI builds both).
# No --with-ffi: since 1.1 the bindings are native extensions (ext-php-rs); ext-ffi only backs two optional
# interop writers, and InteropTest skips without it.
source /usr/local/share/hyperforge/lib.sh
git clone --depth 1 https://github.com/phpenv/phpenv.git "$PHPENV_ROOT"
git clone --depth 1 https://github.com/php-build/php-build.git "$PHPENV_ROOT/plugins/php-build"
V=$(phpenv install --list | sed 's/^ *//' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)
export PHP_BUILD_EXTRA_MAKE_ARGUMENTS="-j$(nproc)"
phpenv install "$V"
# ZTS beside it as "<version>-zts"; switch with `phpenv shell <version>-zts` (or local/global).
PHP_BUILD_CONFIGURE_OPTS="--enable-zts" "$PHPENV_ROOT/plugins/php-build/bin/php-build" "$V" "$PHPENV_ROOT/versions/$V-zts"
phpenv global "$V"
phpenv rehash
php -r 'echo "NTS: ", PHP_VERSION, " zts=", PHP_ZTS, PHP_EOL;'
"$PHPENV_ROOT/versions/$V-zts/bin/php" -r 'echo "ZTS: ", PHP_VERSION, " zts=", PHP_ZTS, PHP_EOL;'
if ! command -v composer >/dev/null; then
  fetch https://getcomposer.org/download/latest-stable/composer.phar /tmp/composer.phar
  sudo install -m 0755 /tmp/composer.phar /usr/local/bin/composer
  rm /tmp/composer.phar
fi
T=$(gh_latest_tag php/pie)
fetch "https://github.com/php/pie/releases/download/$T/pie.phar" /tmp/pie.phar
sudo install -m 0755 /tmp/pie.phar /usr/local/bin/pie
rm /tmp/pie.phar
# CARGO_PHP_VERSION in php/tools/generate-stubs.php. Must run with php already on PATH.
cargo install cargo-php --version 0.2.2 --locked
rm -rf "$CARGO_HOME/registry" "$CARGO_HOME/git"
