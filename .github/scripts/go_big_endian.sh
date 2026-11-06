#!/usr/bin/env bash
# The Go binding's suites on a CPU other than the host's, under QEMU: cross-builds each package's
# test binary and runs it in a container of that architecture. The generated core's load and store
# helpers carry a code path of their own for a big-endian host and another for a CPU without
# unaligned access, and neither path runs on the hosts the rest of hyper-test-go.yml uses. Both the
# hyper-test-go.yml job and the local gate run this.
#
#   go_big_endian.sh <go-dir>
#
# ARCH      GOARCH to run: s390x (the default), riscv64 or mips64le.
# IMAGE     Image to run in, default golang:<the minor go.mod's go line names>. The binaries are
#           static; the image is the shell and whatever a test looks for at run time.
# PACKAGES  Packages to run, space-separated, default every package `go list ./...` finds.
#
# GOFLAGS and GOWORK reach the cross-build, so an overlay or a workspace works. The repository is
# mounted at its own path, so a test reading the corpus by an absolute path finds it. Tests that
# skip themselves in that Go-only container stay skipped and the output names them: the
# regeneration tests, which need cargo, and HyperCast's downgrade test, which needs
# HYPERCAST_DOWNGRADE_ROOT and the same prerequisites; both run on the regular host legs. Every
# package runs even after one fails, and the status is non-zero if any did.
#
# Docker keeps the image's tag at the architecture it last pulled, so on a developer's machine name
# --platform on any later run of that tag.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <go-dir>" >&2
  exit 2
fi
go_dir=$(cd "$1" && pwd -P)
repo=$(cd "$go_dir/.." && pwd -P)

arch=${ARCH:-s390x}
case "$arch" in
  s390x | riscv64) machine=$arch ;;
  mips64le) machine=mips64 ;;
  *)
    echo "go_big_endian: no machine name known for GOARCH=$arch" >&2
    exit 2
    ;;
esac

cd "$go_dir"
if [ -n "${IMAGE:-}" ]; then
  image=$IMAGE
else
  minor=$(sed -n 's/^go \([0-9]*\.[0-9]*\).*/\1/p' go.mod)
  if [ -z "$minor" ]; then
    echo "go_big_endian: $go_dir/go.mod has no go line" >&2
    exit 2
  fi
  image=golang:$minor
fi

bin_dir=$(mktemp -d)
running=
cleanup() {
  if [ -n "$running" ]; then
    docker rm -f "$running" >/dev/null 2>&1 || true
  fi
  rm -rf "$bin_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -n "${PACKAGES:-}" ]; then
  # shellcheck disable=SC2206 # the list is space-separated on purpose
  packages=($PACKAGES)
else
  packages=()
  while IFS= read -r package; do
    packages+=("$package")
  done < <(go list ./...)
fi

failed=0
n=0
for package in "${packages[@]}"; do
  n=$((n + 1))
  binary=$bin_dir/$n.test
  CGO_ENABLED=0 GOOS=linux GOARCH=$arch go test -c -o "$binary" "$package"
  if [ ! -f "$binary" ]; then
    echo "$package: no test files"
    continue
  fi
  dir=$(go list -f '{{.Dir}}' "$package")
  log=$bin_dir/$n.log
  running=go-big-endian-$$-$n
  echo "$package: running on $arch ($image)"
  status=0
  docker run --rm --name "$running" --platform "linux/$arch" \
    -v "$repo:$repo" -v "$bin_dir:/bin-under-test:ro" -w "$dir" "$image" \
    sh -c 'machine=$(uname -m)
      if [ "$machine" != "$1" ]; then
        echo "go_big_endian: expected machine $1, the container reports $machine"
        exit 125
      fi
      exec "/bin-under-test/$2" -test.count=1 -test.timeout=30m -test.v' \
    sh "$machine" "$n.test" >"$log" 2>&1 || status=$?
  running=
  if [ "$status" -ne 0 ]; then
    failed=1
    cat "$log"
    echo "$package: FAILED on $arch (exit $status)"
  else
    grep -E -e '^ *--- SKIP' "$log" || true
    echo "$package: ok on $arch, $(grep -c -E -e '^--- PASS' "$log" || true) top-level tests passed"
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "go_big_endian: at least one suite failed on $arch" >&2
  exit 1
fi
