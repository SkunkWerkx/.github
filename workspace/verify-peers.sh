#!/usr/bin/env bash
# Rebuilds and tests HyperTabular against the HyperCast checkout beside it, in every ecosystem with a peer switch
# (README.md has the table), then proves the layout leaves no trace and that the Forge's
# guard refuses it. Run in the dev container, from anywhere: bash workspace/verify-peers.sh
#
# What each step shows: that HyperCast resolves to workspace/HyperCast, and the suite passes against it. HyperTabular
# needs HyperCast 1.1's API, which no registry has yet, so a step that quietly took the published 1.0.0 fails to
# build. Order matters: Swift's local-core.sh first (its local-clean.sh removes every binding's staged library),
# and Python's extension builds before `cargo cdylib` (both write rust/target/release).
#
# It changes no tracked file: the three repositories' tracked state is compared before and after.
set -uo pipefail
W="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORGE="$(cd "$W/.." && pwd)"
HC="$W/HyperCast" HT="$W/HyperTabular"
LOG="$(mktemp -d)"
results=()

snapshot() { for r in HyperCast HyperTabular HyperUuid; do git -C "$W/$r" status --porcelain=v1; git -C "$W/$r" diff; done | sha256sum; }
step() {  # step <name> <command...>: runs it with its output in $LOG/<name>.log, records the outcome
  local name="$1"; shift
  local start=$SECONDS
  printf '== %-10s ' "$name"
  if (set -e; "$@") > "$LOG/$name.log" 2>&1; then
    results+=("PASS $name"); echo "pass ($((SECONDS - start))s)"
  else
    results+=("FAIL $name ($LOG/$name.log)"); echo "FAIL ($((SECONDS - start))s): tail of $LOG/$name.log"; tail -15 "$LOG/$name.log"
  fi
}
# cargo without the paths override's expected warning (README.md), keeping cargo's own exit status.
quiet_cargo() {
  local out status
  out="$(cargo "$@" 2>&1)"; status=$?
  printf '%s\n' "$out" | grep -v 'path override for crate\|altered the original list\|known to produce buggy\|^$'
  return "$status"
}

before="$(snapshot)"

rust() {
  for m in rust rust/browser-test rust/fuzz typescript java/jni; do
    (cd "$HT/$m" && cargo tree -i hypercast --depth 0 2>/dev/null | head -1 | grep -F "($HC/rust)" && quiet_cargo check --all-targets -q)
  done
}
go_() {
  cd "$HT/go" && go list -m -f '{{.Dir}}' github.com/SkunkWerkx/HyperCast/go | grep -Fx "$HC/go" && go vet ./...
}
java() {
  # The composite build stages both repositories' native pairs: each one's cdylib and JNI library (--locked: the
  # paths override leaves the locks alone).
  for r in HyperCast HyperTabular; do
    (cd "$W/$r/rust" && quiet_cargo -q cdylib) && (cd "$W/$r/java/jni" && quiet_cargo build -q --release --locked --lib)
  done
  cd "$HT/java" && ./gradlew -q dependencyInsight --dependency hypercast --configuration compileClasspath \
    | grep -F "project ':java' (by composite build)"
  ./gradlew test
}
swift_() {
  (cd "$HC" && .github/scripts/local-core.sh) && (cd "$HT" && .github/scripts/local-core.sh)
  cd "$HT" && swift package show-dependencies | grep -F "<$HC@" && swift build && cd swift && swift test
}
typescript() {
  (cd "$HT/typescript" && bun install)
  node -e "console.log(require.resolve('@skunkwerkx/hypercast/package.json', {paths: ['$HT/typescript']}))" \
    | grep -F "$HC/typescript/"
  (cd "$HC/typescript" && bun run build) && cd "$HT/typescript" && bun run build && bun run test
}
python_() {
  for r in HyperCast HyperTabular; do
    n="$(echo "$r" | tr '[:upper:]' '[:lower:]')"
    (cd "$W/$r/rust" && quiet_cargo rustc -q --release --crate-type cdylib --features python \
      && cp "target/release/lib$n.so" "../python/src/$n/_native.so")
  done
  cd "$HT/python" && pytest -q -p no:cacheprovider
}
ruby_() {
  (cd "$HC/rust" && quiet_cargo -q cdylib) && (cd "$HT/rust" && quiet_cargo -q cdylib)
  (cd "$HC/ruby" && bundle install --quiet && bundle exec rake native:dev)
  cd "$HT/ruby" && bundle install --quiet
  bundle exec ruby -e 'require "bundler/setup"; require "hypercast"; puts Gem.loaded_specs["hypercast"].full_gem_path' \
    | grep -Fx "$HC/ruby"
  bundle exec rspec
}
r_() {
  mkdir -p "$(Rscript -e 'cat(Sys.getenv("R_LIBS_USER"))')"
  (cd "$HC" && r/tools/vendor.sh && R CMD INSTALL r)
  cd "$HT" && r/tools/vendor.sh
  tar -xJOf r/src/rust/vendor.tar.xz vendor/hypercast/Cargo.toml | grep -q 'dependencies.simdutf8'  # 1.1's, not 1.0's
  R CMD INSTALL r && Rscript -e 'testthat::test_local("r", stop_on_failure = TRUE)'
}
php_() {
  (cd "$HC/rust" && quiet_cargo -q php-ext) && (cd "$HT/rust" && quiet_cargo -q php-ext)
  cd "$HT/php" && composer install -q
  php -r 'require "tools/extension.php"; echo implode(PHP_EOL, hypertabular_extensions(getcwd())), PHP_EOL;' \
    | grep -F "$HC/rust/target/php/release/"
  composer test
}
dotnet_() {
  # Both cores as plain cdylibs (Python's step leaves extension builds in rust/target/release); each project file
  # copies its own to the output.
  (cd "$HC/rust" && quiet_cargo -q cdylib) && (cd "$HT/rust" && quiet_cargo -q cdylib)
  cd "$HT/csharp"
  dotnet msbuild HyperTabular/HyperTabular.csproj -p:TargetFramework=net11.0 -getItem:ProjectReference \
    | grep -F "\"$HC/csharp/HyperCast/HyperCast.csproj\""
  dotnet build "$W/HyperForge.slnx" && dotnet test --project HyperTabular.Tests
}
guard() {
  local guard="$FORGE/.github/scripts/companion_fetch.py" copy
  ! python3 "$guard" guard --root "$HT"                   # here, in the layout: refused
  copy="$(mktemp -d)/HyperTabular" && mkdir -p "$copy"     # the tracked tree alone, as CI checks it out: clean
  (cd "$HT" && git ls-files -z | tar --null -T - -cf - | tar -xf - -C "$copy")
  python3 "$guard" guard --root "$copy"
}

step rust rust
step go go_
step java java
step swift swift_
step typescript typescript
step python python_
step ruby ruby_
step r r_
step php php_
step dotnet dotnet_
step guard guard
if [ "$(snapshot)" = "$before" ]; then results+=("PASS no tracked file changed"); else
  results+=("FAIL tracked files changed during the run (git status in each repository)"); fi

echo; printf '%s\n' "${results[@]}"; echo "logs: $LOG"
! printf '%s\n' "${results[@]}" | grep -q '^FAIL'
