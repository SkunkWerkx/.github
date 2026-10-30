#!/usr/bin/env bash
# Runs one binding's test suite on Alpine against the musl library build-musl.sh produced.
#
#   suite-musl.sh <binding> <crate> <rid> <repo-root> <musl-lib-dir>
#
# <binding> is one of: csharp, go, java, java-jni, node, python, ruby. <rid> is linux-musl-x64 or linux-musl-arm64 and has to match
# the container's architecture (DOCKER_PLATFORM overrides the host's, as in build-musl.sh).
# The csharp suite reads CSHARP_TEST_PROJECT, CSHARP_RUNTIMES_DIR and CSHARP_AOT_PROJECT, plus
# two optional ones for a multi-targeted project: CSHARP_AOT_FRAMEWORKS (space-separated TFMs,
# one `-f` AOT publish and run each; empty keeps the single publish) and CSHARP_EXTRA_RUNTIME
# (a runtime channel such as 10.0, copied from its runtime image into the SDK image so
# `dotnet test` can run the older target frameworks; empty adds none).
# The java-jni suite reads JDK_FLOOR_VERSION (the floor JDK's Temurin major) and needs the
# JNI shim, lib<crate>_jni.so, beside the core in <musl-lib-dir>.
# SUITE_IMAGE overrides the binding's default image, which is how the same suite runs a
# second time on the oldest runtime the binding claims: `ruby:3.3-alpine` beside
# `ruby:4.0-alpine`, `python:3.11-alpine` beside `python:3.14-alpine`. A floor nothing runs is
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
extra_mounts=()
in_alpine() {
  docker run --rm "${platform_args[@]}" \
    -v "$repo_root:/src:ro" -v "$lib_dir:/musl:ro" "${extra_mounts[@]}" \
    -e CRATE="$crate" -e RID="$rid" "${extra_env[@]}" \
    "$1" sh -euc '
      mkdir -p /work/rust
      cp /src/rust/Cargo.toml /work/rust/
      if [ -d /src/corpus ]; then cp -r /src/corpus /work/corpus; fi
      '"$2"
}

case "$binding" in
  # The suite, then the Native AOT smoke test published for this RID and run — the same two
  # receipts the five-leg job takes, on the RID .NET itself selects on Alpine. The library
  # goes where the NuGet package puts it (runtimes/{rid}/native/), so this is the layout a
  # consumer's restore produces. The `-aot` image variant carries clang and the linker the
  # AOT compiler needs; the plain `-alpine` one runs `dotnet test` and stops there.
  #
  # The publish log goes to a file, not through `tee`: this is plain sh, which has no
  # pipefail, and a pipe would report tee's exit status and hide a failed publish.
  csharp)
    : "${CSHARP_TEST_PROJECT:?the C# suite needs CSHARP_TEST_PROJECT}" "${CSHARP_RUNTIMES_DIR:?the C# suite needs CSHARP_RUNTIMES_DIR}"
    extra_env=(-e TEST_PROJECT="$CSHARP_TEST_PROJECT" -e RUNTIMES_DIR="$CSHARP_RUNTIMES_DIR" -e AOT_PROJECT="${CSHARP_AOT_PROJECT:-}" -e AOT_FRAMEWORKS="${CSHARP_AOT_FRAMEWORKS:-}")
    # An older target framework's runtime: the SDK image carries only its own, and Alpine has
    # no apt-style second install, so the runtime image's shared framework is copied out on
    # the host and mounted in.
    if [ -n "${CSHARP_EXTRA_RUNTIME:-}" ]; then
      runtime_tmp=$(mktemp -d)
      cid=
      trap 'rm -rf "$runtime_tmp"; [ -z "$cid" ] || docker rm "$cid" >/dev/null 2>&1 || true' EXIT
      cid=$(docker create "${platform_args[@]}" "mcr.microsoft.com/dotnet/runtime:${CSHARP_EXTRA_RUNTIME}-alpine")
      docker cp "$cid:/usr/share/dotnet/shared/Microsoft.NETCore.App/." "$runtime_tmp/"
      docker rm "$cid" >/dev/null
      cid=
      extra_mounts=(-v "$runtime_tmp:/extra-runtime:ro")
    fi
    in_alpine "${SUITE_IMAGE:-mcr.microsoft.com/dotnet/sdk:11.0-alpine-aot}" '
      (cd /src && tar --exclude=bin --exclude=obj --exclude=runtimes --exclude=TestResults \
          --exclude=BenchmarkDotNet.Artifacts -cf - csharp global.json LICENSE) | tar -C /work -xf -
      cd /work
      mkdir -p "$RUNTIMES_DIR/$RID/native"
      cp "/musl/lib$CRATE.so" "$RUNTIMES_DIR/$RID/native/"
      echo "== .NET SDK $(dotnet --version), Alpine $(cat /etc/alpine-release), $RID"
      if [ -d /extra-runtime ]; then
        cp -a /extra-runtime/. /usr/share/dotnet/shared/Microsoft.NETCore.App/
      fi
      # An absolute project path: a multi-targeted project fails to load a relative one
      # (the path is re-resolved against the project directory and doubles).
      dotnet test --project "/work/$TEST_PROJECT" -c Release
      if [ -n "$AOT_PROJECT" ]; then
        # No frameworks named: today'"'"'s single publish into aot-out.
        for tfm in ${AOT_FRAMEWORKS:--}; do
          if [ "$tfm" = "-" ]; then out=/work/aot-out; set --; else out="/work/aot-out/$tfm"; set -- -f "$tfm"; fi
          dotnet publish "$AOT_PROJECT" -c Release -r "$RID" -p:PublishAot=true "$@" -o "$out" \
            > /tmp/aot.log 2>&1 || { cat /tmp/aot.log; echo "::error::Native AOT publish failed for $RID $tfm"; exit 1; }
          cat /tmp/aot.log
          if grep -nE "warning (IL|AOT)[0-9]{4}" /tmp/aot.log; then
            echo "::error::Native AOT publish emitted trim/AOT warnings on $RID"; exit 1
          fi
          # Where the package has a static library for this RID the core is linked into
          # the executable, and the shared library must not have been published beside it.
          if ls "$(dirname "$RUNTIMES_DIR")/staticlibs/$RID"/* >/dev/null 2>&1; then
            if ls "$out" | grep -E "^lib$CRATE\.so$"; then
              echo "::error::lib$CRATE.so was published beside an executable that should have it linked in"; exit 1
            fi
            echo "core linked into the executable: no lib$CRATE.so in the publish output"
          fi
          "$out/$(basename "$AOT_PROJECT" .csproj)"
        done
      fi
    '
    ;;

  # The suite, then its test binary again with the C toolchain — and with it libgcc —
  # removed, which is the state a multi-stage Dockerfile's runtime image is in. The core is
  # linked in (the archive is staged by the job, from the musl target), so that last run
  # proves the binary needs nothing but musl's libc.
  go)
    in_alpine "${SUITE_IMAGE:-golang:1.27-alpine}" '
      apk add --no-cache build-base >/dev/null
      cp -r /src/go /work/go
      cd /work/go
      go test -count=1 ./...
      go test -c -o /work/suite.test .
      apk del build-base >/dev/null
      if ls /usr/lib/libgcc_s.so* >/dev/null 2>&1; then echo "::error::libgcc_s is still installed, so this run proves nothing"; exit 1; fi
      /work/suite.test -test.count=1
    '
    ;;

  # The FFM suite, then the smoke program on a plain JVM. Only the musl library is on the
  # classpath, so a suite that passes means the musl library is what loaded.
  java)
    in_alpine "${SUITE_IMAGE:-eclipse-temurin:25-jdk-alpine}" '
      cp -r /src/java /work/java
      cp /src/LICENSE /work/
      cd /work/java
      rm -rf build .gradle aot-smoke-test/build benchmarks/build src/main/resources/native
      mkdir -p "src/main/resources/native/$RID"
      cp "/musl/lib$CRATE.so" "src/main/resources/native/$RID/"
      chmod +x gradlew
      ./gradlew test :aot-smoke-test:run --console=plain
    '
    ;;

  # The JNI base: both musl libraries on the classpath, then `test` on the image's JDK (25),
  # `testFloor` on the floor JDK and the smoke program. The floor JDK is the one in the
  # eclipse-temurin:<floor>-jdk-alpine image, copied out on the host and visible at
  # /musl/jdk-floor (the read-only library mount), because Gradle is told to use exactly
  # that install and nothing else (auto-detect off), as the other legs are.
  java-jni)
    [ -f "$lib_dir/lib${crate}_jni.so" ] || { echo "::error::no $lib_dir/lib${crate}_jni.so — run build-musl.sh with JNI=true first"; exit 1; }
    : "${JDK_FLOOR_VERSION:?the java-jni suite needs JDK_FLOOR_VERSION}"
    rm -rf "$lib_dir/jdk-floor"
    cid=
    trap 'rm -rf "$lib_dir/jdk-floor"; [ -z "$cid" ] || docker rm "$cid" >/dev/null 2>&1 || true' EXIT
    cid=$(docker create "${platform_args[@]}" "eclipse-temurin:$JDK_FLOOR_VERSION-jdk-alpine")
    docker cp "$cid:/opt/java/openjdk" "$lib_dir/jdk-floor"
    docker rm "$cid" >/dev/null
    cid=
    in_alpine "${SUITE_IMAGE:-eclipse-temurin:25-jdk-alpine}" '
      cp -r /src/java /work/java
      cp /src/LICENSE /work/
      cd /work/java
      rm -rf build .gradle aot-smoke-test/build benchmarks/build jni/target src/main/resources/native
      mkdir -p "src/main/resources/native/$RID"
      cp "/musl/lib$CRATE.so" "/musl/lib${CRATE}_jni.so" "src/main/resources/native/$RID/"
      chmod +x gradlew
      ./gradlew test testFloor checkSuiteParity :aot-smoke-test:run --console=plain \
        -Porg.gradle.java.installations.paths=/musl/jdk-floor -Porg.gradle.java.installations.auto-detect=false
    '
    ;;

  # Two containers, because the claim has two halves. The first builds a musllinux wheel
  # the way the release does (maturin, with patchelf so the extension's libgcc_s is bundled
  # into the wheel). The second installs that wheel on a BARE image — no compiler, no libgcc
  # — and runs the suite: that is the consumer's machine, and it is what proves the bundling.
  #
  # SUITE_IMAGE picks the interpreter the suite runs on; the wheel is always built on the
  # default image, since it is abi3 and one build serves every supported Python.
  python)
    wheels=$(mktemp -d)
    trap 'rm -rf "$wheels"' EXIT
    chmod 777 "$wheels"
    extra_env=(-v "$wheels:/wheels")
    in_alpine "python:3.14-alpine" '
      apk add --no-cache build-base curl >/dev/null
      curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal -q
      . "$HOME/.cargo/env"
      pip install -q --root-user-action=ignore "maturin[patchelf]>=1.9,<2.0"
      (cd /src && tar --exclude=target --exclude=.pytest_cache --exclude=__pycache__ \
          --exclude="_native*.so" --exclude="_native*.pyd" -cf - rust python) | tar -C /work -xf -
      cd /work/python
      maturin build --release --compatibility musllinux_1_2 --out /wheels
      chmod -R a+rX /wheels
    '
    extra_env=(-v "$wheels:/wheels:ro")
    in_alpine "${SUITE_IMAGE:-python:3.14-alpine}" '
      if ls /usr/lib/libgcc_s* >/dev/null 2>&1; then echo "::error::libgcc is installed, so this run proves nothing"; exit 1; fi
      pip install -q --root-user-action=ignore pytest /wheels/*.whl
      mkdir /t && cp -r /src/python/tests /t/tests
      if [ -d /work/corpus ]; then cp -r /work/corpus /t/corpus; fi
      mkdir -p /t/rust && cp /work/rust/Cargo.toml /t/rust/
      cd /t
      pytest -q -p no:cacheprovider tests
    '
    ;;

  # Two containers again. The first builds the TypeScript addon in Node's Alpine image (a
  # napi-rs cdylib of its own, path-depending on rust/, so the musl library above is not what
  # it loads) and runs the suite on that Node; SUITE_IMAGE is how the same build and suite run
  # on each older major `engines` admits. The second runs the suite on Bun's Alpine image
  # against that same .node, which is left in <musl-lib-dir> for the job to upload. (Bun's
  # image carries libgcc for Bun itself, so the bare-image proof is the readelf gate's.)
  #
  # `|| exit 1` on the build line: the scripts run under plain `sh -e`, which does not stop on
  # a failure that is not the last command of an && list.
  #
  # The addon links std, so build-musl.sh's two linker facts both apply here: a dynamic CRT
  # (RUSTFLAGS replaces the crate's own .cargo/config.toml rustflags, so -crt-static is
  # repeated) and the static unwinder, without which the .node needs a libgcc_s that neither
  # image ships. The readelf gate is build-musl.sh's. The wrappers are compared with the
  # checkout's after the build: without --js and --dts napi writes its own loader over them.
  # The linker is named outright because napi assumes the aarch64 musl target is a cross-build
  # from x86_64 and asks for aarch64-linux-musl-gcc, which a native arm64 Alpine does not have
  # (measured: "linker `aarch64-linux-musl-gcc` not found"); napi leaves a set one alone. The
  # x86_64 one is set the same way so a napi release that maps that target too changes nothing.
  node)
    addon=$(mktemp -d)
    trap 'rm -rf "$addon"' EXIT
    chmod 777 "$addon"
    extra_env=(-v "$addon:/addon" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)")
    in_alpine "${SUITE_IMAGE:-node:${NODE_VERSION:-26}-alpine}" '
      apk add --no-cache build-base curl >/dev/null
      curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal -q
      . "$HOME/.cargo/env"
      echo "== node $(node --version), $(rustc -vV | sed -n "s/^host: //p"), Alpine $(cat /etc/alpine-release), $RID"
      (cd /src && tar --exclude=target --exclude=node_modules --exclude="*.node" -cf - rust typescript) | tar -C /work -xf -
      mkdir /unwind
      echo "GROUP ( $(gcc -print-file-name=libgcc_eh.a) $(gcc -print-file-name=libgcc.a) )" > /unwind/libgcc_s.so
      export RUSTFLAGS="-C target-feature=-crt-static -L native=/unwind"
      export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=gcc CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=gcc
      cd /work/typescript
      npm ci && npm run build && npm test || exit 1
      for wrapper in index.js index.d.ts binding.js binding.d.ts; do
        cmp "$wrapper" "/src/typescript/$wrapper" || { echo "::error::napi build rewrote typescript/$wrapper"; exit 1; }
      done
      for lib in ./*.node; do
        needed=$(readelf -d "$lib" | sed -n "s/.*(NEEDED).*\[\(.*\)\]/\1/p" | sort | tr "\n" " ")
        echo "NEEDED ($lib): $needed"
        # The count as well as the name: a bare "libc.musl-"*".so.1 " pattern also matches
        # "libc.musl-aarch64.so.1 libgcc_s.so.1 ", since * spans the space (measured).
        set -- $needed
        case "$#:${1:-}" in
          "1:libc.musl-"*".so.1") ;;
          *) echo "::error::$lib must depend on musl libc and nothing else, found: $needed"; exit 1 ;;
        esac
      done
      cp ./*.node /addon/
      chown "$HOST_UID:$HOST_GID" /addon/*.node
    '
    extra_env=(-v "$addon:/addon:ro")
    in_alpine "oven/bun:alpine" '
      (cd /src && tar --exclude=target --exclude=node_modules --exclude="*.node" -cf - typescript) | tar -C /work -xf -
      cp /addon/*.node /work/typescript/
      cd /work/typescript
      echo "== bun $(bun --version), Alpine $(cat /etc/alpine-release), $RID"
      bun test tests/
    '
    cp "$addon"/*.node "$lib_dir/"
    ;;

  # The Fiddle backend, forced, over this library: what the universal gem runs on Alpine for
  # a Ruby no musl platform gem covers — the declared floor among them, which is why this
  # suite also runs on ruby_floor_version. The Magnus extension those platform gems carry is
  # built and tested by the ruby-magnus-musl action, not here.
  #
  # rspec is installed as a gem and run directly, without Bundler, and that is deliberate.
  # On Ruby 3.3 and 3.4 fiddle is a default gem, and Bundler resolves the newer fiddle from
  # rubygems.org and compiles its C extension, which needs a toolchain this image does not
  # have. A plain `gem install` of the published gem is satisfied by the default fiddle, so
  # this is also the closer match to what a consumer's install does.
  # The gem's own runtime dependencies (HyperTabular's on hypercast), from its gemspec, since
  # Bundler is not used: each a plain `gem install`, --conservative so that one an image
  # already has (its default or bundled fiddle) is kept rather than rebuilt from source.
  ruby)
    in_alpine "${SUITE_IMAGE:-ruby:4.0-alpine}" '
      cp -r /src/ruby /work/ruby
      rm -rf "/work/ruby/lib/$CRATE/native/linux-x64" "/work/ruby/lib/$CRATE/native/linux-arm64"
      mkdir -p "/work/ruby/lib/$CRATE/native/$RID"
      cp "/musl/lib$CRATE.so" "/work/ruby/lib/$CRATE/native/$RID/"
      cd /work/ruby
      rm -f Gemfile Gemfile.lock
      gem install rspec -v "~> 3.13" --no-document --silent
      ruby -e "Gem::Specification.load(Dir[%q(*.gemspec)].first).runtime_dependencies.each { |d| system(%q(gem), %q(install), d.name, %q(-v), d.requirement.to_s, %q(--conservative), %q(--no-document), %q(--silent)) or abort(%(could not install #{d.name} #{d.requirement})) }"
      export "$(echo "$CRATE" | tr a-z A-Z)_PURE=1"
      rspec
    '
    ;;

  *)
    echo "::error::unknown binding \"$binding\""
    exit 1
    ;;
esac
