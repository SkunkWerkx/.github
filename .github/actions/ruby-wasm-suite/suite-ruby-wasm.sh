#!/usr/bin/env bash
# Links a binding's Magnus extension into a ruby.wasm interpreter for one Ruby minor and runs
# it there, one stage per argument, from the consumer's checkout root:
#
#   suite-ruby-wasm.sh <crate> <abi> <stage> [<stage> ...]
#
# e.g. `suite-ruby-wasm.sh hyperuuid 4.0 archive gem node browser`. The stages, in order:
#
#   archive  the {crate}-wasm gem packed from source and linked into source.wasm; the gem's
#            extconf.rb, run by rbwasm with {CRATE}_RUST_DIR set, builds the crate for
#            wasm32-wasip1 against that minor's headers and leaves the archive at
#            $OUT/archives/<abi>/{crate}_native.a, which hyper-build-wasm.yml attests and
#            uploads before the next stage
#   gem      that archive packed into the gem the way it ships, with no Rust in reach, and
#            linked into ruby.wasm
#   node     ruby.wasm under Node (node-test.mjs)
#   browser  ruby.wasm in headless Chrome (index.html), which must print DONE and no FAIL
#
# From the environment, with defaults: SMOKE (ruby/wasm-smoke), the directory whose Gemfile
# rbwasm builds; RUST_DIR (rust), the crate; OUT ($TMPDIR/ruby-wasm), where the archive, the
# unpacked gems and the browser page go; CHROME (google-chrome). The host needs Ruby with
# Bundler, which only runs Bundler, rake and rbwasm (the interpreter rbwasm builds is <abi>
# whatever the host's is), Rust with wasm32-wasip1, Node, python3 and Chrome.
#
# rbwasm builds the interpreter itself (OpenSSL, zlib, libyaml, Ruby): 15-20 minutes cold,
# about 8 with $SMOKE/build kept between runs. Every build passes --remake. Without it rbwasm
# returns rubies/<name>.tar.gz whenever that exists, and the name hashes only the gem names,
# so the gem stage would hand back the archive stage's interpreter and the gem as shipped
# would never be linked; a kept build/ would ship last run's crate. --remake rebuilds every
# extension and relinks Ruby, and reuses everything else.
#
# On macOS, pkg-config is narrowed to Homebrew's openssl@3, because Homebrew's pkg-config
# reaches into rbwasm's build twice. rbwasm first builds a native baseruby, whose ext/openssl
# takes whatever openssl.pc it finds, and Homebrew's `openssl` is openssl@4, which Ruby 3.4
# and 4.0 don't compile against. Then the wasm Ruby's ext/psych asks pkg-config for yaml-0.1
# before it honours rbwasm's --with-libyaml-dir and links Homebrew's arm64 libyaml.a into the
# wasm module (wasi-sdk 24, for Ruby 4.0, fails on it). On Ubuntu pkg-config omits the system
# -L the same lookup returns there, so the runners are unaffected and get no override.
set -euo pipefail

usage="usage: suite-ruby-wasm.sh <crate> <abi> <stage> [<stage> ...]"
crate="${1:?$usage}"
abi="${2:?$usage}"
shift 2
[ $# -gt 0 ] || { echo "$usage" >&2; exit 2; }
for stage in "$@"; do
  case "$stage" in
    archive|gem|node|browser) ;;
    *) echo "unknown stage: $stage" >&2; exit 2 ;;
  esac
done

root="$PWD"
smoke="$root/${SMOKE:-ruby/wasm-smoke}"
rust_dir="$root/${RUST_DIR:-rust}"
out="${OUT:-${TMPDIR:-/tmp}/ruby-wasm}"
chrome="${CHROME:-google-chrome}"
gem_name="$crate-wasm"
archive="$out/archives/$abi/${crate}_native.a"
upper="$(echo "$crate" | tr '[:lower:]' '[:upper:]')"
mkdir -p "$out"

if [ "$(uname -s)" = Darwin ]; then
  openssl3="$(brew --prefix openssl@3 2>/dev/null || true)/lib/pkgconfig"
  [ -f "$openssl3/openssl.pc" ] || { echo "error: ruby.wasm on macOS needs Homebrew's openssl@3" >&2; exit 1; }
  export PKG_CONFIG_LIBDIR="$openssl3"
fi

# Bundler reads a path gem's .gemspec, which a packed .gem does not contain, so each gem is
# unpacked with its spec written beside it. Prints the unpacked directory.
unpack() {
  local gem="$1" dest="$2" dir
  rm -rf "$dest"
  gem unpack "$gem" --target "$dest" >&2
  dir="$(ls -d "$dest"/*/)"
  gem spec "$gem" --ruby >"$dir/$gem_name.gemspec"
  echo "$dir"
}

# The gem packed by ruby/Rakefile's wasm:gem task, for <variant> (source, or an ABI).
pack() {
  (cd "$root/ruby" && bundle install --quiet && rm -f pkg/"$gem_name"-*.gem && bundle exec rake "wasm:gem[$1]") >&2
  echo "$root/ruby/pkg/$(cd "$root/ruby/pkg" && ls "$gem_name"-*.gem)"
}

# rbwasm over the smoke Gemfile, which takes the {crate}-wasm gem from WASM_GEM_DIR.
link() {
  local gem_dir="$1" wasm="$2"
  (
    cd "$smoke"
    export BUNDLE_GEMFILE="$PWD/Gemfile" WASM_GEM_NAME="$gem_name" WASM_GEM_DIR="$gem_dir"
    rm -f Gemfile.lock
    bundle install
    bundle exec rbwasm build --ruby-version "$abi" --target wasm32-unknown-wasip1 --remake -o "$wasm"
  )
}

for stage in "$@"; do
  case "$stage" in
    archive)
      gem="$(pack source)"
      (
        export "${upper}_RUST_DIR=$rust_dir" "${upper}_WASM_OUT=$out/archives"
        link "$(unpack "$gem" "$out/gem-source")" source.wasm
      )
      ls -l "$archive"
      ;;
    gem)
      [ -f "$archive" ] || { echo "error: no $archive; run the archive stage first" >&2; exit 1; }
      ext="$root/ruby/ext/${crate}_native/$abi"
      mkdir -p "$ext"
      cp "$archive" "$ext/"
      gem="$(pack "$abi")"
      link "$(unpack "$gem" "$out/gem-prebuilt")" ruby.wasm
      echo "ruby.wasm: $(wc -c <"$smoke/ruby.wasm") bytes"
      ;;
    node)
      (cd "$smoke" && npm ci && node node-test.mjs ruby.wasm)
      ;;
    browser)
      site="$out/site"
      rm -rf "$site"
      mkdir -p "$site"
      (cd "$smoke" && cp index.html test.rb ruby.wasm node_modules/@ruby/wasm-wasi/dist/browser.umd.js "$site/")
      (cd "$site" && exec python3 -m http.server 8091 --bind 127.0.0.1 >/dev/null 2>&1) &
      server=$!
      sleep 1
      "$chrome" --headless=new --no-sandbox --disable-gpu --virtual-time-budget=60000 \
        --dump-dom "http://127.0.0.1:8091/index.html" 2>/dev/null >"$site/dom.html" || true
      kill "$server" 2>/dev/null || true
      sed -n '/<pre/,/<\/pre>/p' "$site/dom.html"
      if grep -Eq '(^|>)FAIL' "$site/dom.html" || ! grep -Eq '(^|>)DONE(<|$)' "$site/dom.html"; then
        echo "::error::The ruby.wasm smoke page did not print DONE with every check passing."
        exit 1
      fi
      ;;
  esac
done
