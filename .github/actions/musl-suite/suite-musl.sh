#!/usr/bin/env bash
# Runs one binding's test suite on Alpine against the musl library build-musl.sh produced.
#
#   suite-musl.sh <binding> <crate> <rid> <repo-root> <musl-lib-dir>
#
# <binding> is one of: csharp, go, java, php, python, ruby. <rid> is linux-musl-x64 or linux-musl-arm64 and has to match
# the container's architecture (DOCKER_PLATFORM overrides the host's, as in build-musl.sh).
# SUITE_IMAGE overrides the binding's default image, which is how the same suite runs a
# second time on the oldest runtime the binding claims: `php:8.2-cli-alpine` beside
# `php:8.5-cli-alpine`, `ruby:3.3-alpine` beside `ruby:4.0-alpine`. A floor nothing runs is
# a floor nobody knows is true, and the Alpine images exist for every version, so this job
# is where the floors get tested as well as musl.
#
# Every suite follows the same three rules, each of which is there because the obvious
# alternative was tried:
#
#  * The repo is mounted read-only and the binding's directory is copied into the container.
#    Docker runs as root; a read-write mount leaves root-owned vendor/, build/ and lock files
#    in the caller's checkout.
#  * The glibc libraries the tree carries for this architecture are removed from the copy
#    before the musl one is placed. A binding that resolved the wrong RID would otherwise
#    find a file, fail in dlopen, and report a load error instead of the real mistake.
#  * Each image is the language's own official `*-alpine` image, not a bare Alpine with the
#    runtime installed from apk: it is what a consumer's Dockerfile starts FROM.
set -euo pipefail

usage='usage: suite-musl.sh <binding> <crate> <rid> <repo-root> <musl-lib-dir>'
binding=${1:?$usage}
crate=${2:?$usage}
rid=${3:?$usage}
repo_root=$(cd "${4:?$usage}" && pwd)
lib_dir=$(cd "${5:?$usage}" && pwd)
[ -f "$lib_dir/lib$crate.so" ] || { echo "::error::no $lib_dir/lib$crate.so — run build-musl.sh first"; exit 1; }

platform_args=()
[ -n "${DOCKER_PLATFORM:-}" ] && platform_args=(--platform "$DOCKER_PLATFORM")

# $1 image, $2 script. The script sees /src (the repo, read-only), /musl (the library),
# $CRATE and $RID, and starts with a /work that already holds rust/Cargo.toml and corpus/ —
# the two things outside a binding's own directory its tests read. A suite that needs more
# of the caller's environment inside the container names it in extra_env first.
extra_env=()
in_alpine() {
  docker run --rm "${platform_args[@]}" \
    -v "$repo_root:/src:ro" -v "$lib_dir:/musl:ro" \
    -e CRATE="$crate" -e RID="$rid" "${extra_env[@]}" \
    "$1" sh -euc '
      mkdir -p /work/rust
      cp /src/rust/Cargo.toml /work/rust/
      if [ -d /src/corpus ]; then cp -r /src/corpus /work/corpus; fi
      '"$2"
}

case "$binding" in
  # The suite, then the Native AOT smoke test published for this RID and run — the same two
  # receipts the six-leg job takes, on the RID .NET itself selects on Alpine. The library
  # goes where the NuGet package puts it (runtimes/{rid}/native/), so this is the layout a
  # consumer's restore produces. The `-aot` image variant carries clang and the linker the
  # AOT compiler needs; the plain `-alpine` one runs `dotnet test` and stops there.
  #
  # The publish log goes to a file, not through `tee`: this is plain sh, which has no
  # pipefail, and a pipe would report tee's exit status and hide a failed publish.
  csharp)
    : "${CSHARP_TEST_PROJECT:?the C# suite needs CSHARP_TEST_PROJECT}" "${CSHARP_RUNTIMES_DIR:?the C# suite needs CSHARP_RUNTIMES_DIR}"
    extra_env=(-e TEST_PROJECT="$CSHARP_TEST_PROJECT" -e RUNTIMES_DIR="$CSHARP_RUNTIMES_DIR" -e AOT_PROJECT="${CSHARP_AOT_PROJECT:-}")
    in_alpine "${SUITE_IMAGE:-mcr.microsoft.com/dotnet/sdk:10.0-alpine-aot}" '
      (cd /src && tar --exclude=bin --exclude=obj --exclude=runtimes --exclude=TestResults \
          --exclude=BenchmarkDotNet.Artifacts -cf - csharp global.json LICENSE) | tar -C /work -xf -
      cd /work
      mkdir -p "$RUNTIMES_DIR/$RID/native"
      cp "/musl/lib$CRATE.so" "$RUNTIMES_DIR/$RID/native/"
      echo "== .NET SDK $(dotnet --version), Alpine $(cat /etc/alpine-release), $RID"
      dotnet test --project "$TEST_PROJECT" -c Release
      if [ -n "$AOT_PROJECT" ]; then
        dotnet publish "$AOT_PROJECT" -c Release -r "$RID" -p:PublishAot=true -o /work/aot-out \
          > /tmp/aot.log 2>&1 || { cat /tmp/aot.log; echo "::error::Native AOT publish failed for $RID"; exit 1; }
        cat /tmp/aot.log
        if grep -nE "warning (IL|AOT)[0-9]{4}" /tmp/aot.log; then
          echo "::error::Native AOT publish emitted trim/AOT warnings on $RID"; exit 1
        fi
        "/work/aot-out/$(basename "$AOT_PROJECT" .csproj)"
      fi
    '
    ;;

  # cgo, then purego, then the cgo test binary again with the C toolchain — and with it
  # libgcc — removed, which is the state a multi-stage Dockerfile's runtime image is in. That
  # last run is the one that proves the library needs nothing but musl's libc.
  go)
    in_alpine "${SUITE_IMAGE:-golang:1.27-alpine}" '
      apk add --no-cache build-base >/dev/null
      cp -r /src/go /work/go
      rm -rf /work/go/native/linux-x64 /work/go/native/linux-arm64
      mkdir -p "/work/go/native/$RID"
      cp "/musl/lib$CRATE.so" "/work/go/native/$RID/"
      cd /work/go
      go test -count=1 ./...
      CGO_ENABLED=0 go test -count=1 ./...
      go test -c -o /work/suite.test .
      apk del build-base >/dev/null
      if ls /usr/lib/libgcc_s.so* >/dev/null 2>&1; then echo "::error::libgcc_s is still installed, so this run proves nothing"; exit 1; fi
      /work/suite.test -test.count=1
    '
    ;;

  # The official image ships without ext-ffi; building it is three packages and one command.
  php)
    in_alpine "${SUITE_IMAGE:-php:8.5-cli-alpine}" '
      apk add --no-cache libffi-dev git unzip >/dev/null
      docker-php-ext-install ffi >/dev/null
      php -r "copy(\"https://getcomposer.org/installer\", \"/tmp/composer-setup.php\");"
      php /tmp/composer-setup.php --quiet --install-dir=/usr/local/bin --filename=composer
      cp -r /src/php /work/php
      rm -rf /work/php/vendor /work/php/composer.lock /work/php/src/native/linux-x64 /work/php/src/native/linux-arm64
      mkdir -p "/work/php/src/native/$RID"
      cp "/musl/lib$CRATE.so" "/work/php/src/native/$RID/"
      cd /work/php
      composer install --no-interaction --prefer-dist --no-progress --quiet
      php vendor/bin/phpunit
    '
    ;;

  # The FFM suite, then the smoke program on a plain JVM. The wasm module rides along when
  # the checkout has one (WASM_MODULE names it), because the jar always carries both and two
  # backend-selection tests skip without it — but only the musl library is on the classpath
  # as a native build, so `backend: native` in the smoke program's output, and the test
  # that asserts the backend follows the property, mean the musl library is what loaded.
  java)
    extra_env=(-e WASM_MODULE="${WASM_MODULE:-}")
    in_alpine "${SUITE_IMAGE:-eclipse-temurin:25-jdk-alpine}" '
      cp -r /src/java /work/java
      cp /src/LICENSE /work/
      cd /work/java
      rm -rf build .gradle aot-smoke-test/build benchmarks/build src/main/resources/native
      mkdir -p "src/main/resources/native/$RID"
      cp "/musl/lib$CRATE.so" "src/main/resources/native/$RID/"
      if [ -n "$WASM_MODULE" ] && [ -f "/src/$WASM_MODULE" ]; then
        mkdir -p src/main/resources/native/wasm32-wasip1
        cp "/src/$WASM_MODULE" src/main/resources/native/wasm32-wasip1/
      fi
      chmod +x gradlew
      ./gradlew test :aot-smoke-test:run --console=plain
    '
    ;;

  # Two containers, because the claim has two halves. The first builds a musllinux wheel
  # the way the release does (maturin, with patchelf so the extension's libgcc_s is bundled
  # into the wheel). The second installs that wheel on a BARE image — no compiler, no libgcc
  # — and runs the suite: that is the consumer's machine, and it is what proves the bundling.
  # The wasm backend is then run too, after `apk add libgcc`, which wasmtime's own musl wheel
  # needs and does not bundle.
  #
  # SUITE_IMAGE picks the interpreter the suite runs on; the wheel is always built on the
  # default image, since it is abi3 and one build serves every supported Python.
  python)
    wheels=$(mktemp -d)
    trap 'rm -rf "$wheels"' EXIT
    chmod 777 "$wheels"
    extra_env=(-v "$wheels:/wheels" -e WASM_MODULE="${WASM_MODULE:-}")
    in_alpine "python:3.14-alpine" '
      apk add --no-cache build-base curl >/dev/null
      curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal -q
      . "$HOME/.cargo/env"
      pip install -q --root-user-action=ignore "maturin[patchelf]>=1.9,<2.0"
      (cd /src && tar --exclude=target --exclude=.pytest_cache --exclude=__pycache__ \
          --exclude="_native*.so" --exclude="_native*.pyd" -cf - rust python) | tar -C /work -xf -
      if [ -n "$WASM_MODULE" ] && [ -f "/src/$WASM_MODULE" ]; then
        mkdir -p "/work/python/src/$CRATE/native/wasm32-wasip1"
        cp "/src/$WASM_MODULE" "/work/python/src/$CRATE/native/wasm32-wasip1/"
      fi
      cd /work/python
      maturin build --release --compatibility musllinux_1_2 --out /wheels
      chmod -R a+rX /wheels
    '
    extra_env=(-v "$wheels:/wheels:ro" -e WASM_MODULE="${WASM_MODULE:-}")
    in_alpine "${SUITE_IMAGE:-python:3.14-alpine}" '
      if ls /usr/lib/libgcc_s* >/dev/null 2>&1; then echo "::error::libgcc is installed, so this run proves nothing"; exit 1; fi
      pip install -q --root-user-action=ignore pytest /wheels/*.whl
      mkdir /t && cp -r /src/python/tests /t/tests
      if [ -d /work/corpus ]; then cp -r /work/corpus /t/corpus; fi
      mkdir -p /t/rust && cp /work/rust/Cargo.toml /t/rust/
      cd /t
      pytest -q -p no:cacheprovider tests
      if [ -n "$WASM_MODULE" ]; then
        pip install -q --root-user-action=ignore wasmtime
        apk add --no-cache libgcc >/dev/null
        env "$(echo "$CRATE" | tr a-z A-Z)_WASM=1" pytest -q -p no:cacheprovider tests
      fi
    '
    ;;

  # The Fiddle backend, forced: there is no Magnus extension for musl, so Fiddle is what an
  # Alpine consumer runs — RubyGems installs the `x86_64-linux` platform gem there, its
  # glibc extension fails to load, and the gem falls back to Fiddle over this library.
  #
  # rspec is installed as a gem and run directly, without Bundler, and that is deliberate.
  # On Ruby 3.3 and 3.4 fiddle is a default gem, and Bundler resolves the newer fiddle from
  # rubygems.org and compiles its C extension, which needs a toolchain this image does not
  # have. A plain `gem install` of the published gem is satisfied by the default fiddle, so
  # this is also the closer match to what a consumer's install does.
  ruby)
    in_alpine "${SUITE_IMAGE:-ruby:4.0-alpine}" '
      cp -r /src/ruby /work/ruby
      rm -rf "/work/ruby/lib/$CRATE/native/linux-x64" "/work/ruby/lib/$CRATE/native/linux-arm64"
      mkdir -p "/work/ruby/lib/$CRATE/native/$RID"
      cp "/musl/lib$CRATE.so" "/work/ruby/lib/$CRATE/native/$RID/"
      cd /work/ruby
      rm -f Gemfile Gemfile.lock
      gem install rspec -v "~> 3.13" --no-document --silent
      export "$(echo "$CRATE" | tr a-z A-Z)_PURE=1"
      rspec
    '
    ;;

  *)
    echo "::error::unknown binding \"$binding\""
    exit 1
    ;;
esac
