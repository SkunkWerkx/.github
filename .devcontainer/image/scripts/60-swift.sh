# Swift (vscode): swiftly (detects debian13 by itself), newest release (floats), its static Linux SDK
# (hyper-test-swift-static.yml), SwiftLint static binary at CI's pin.
# Everything under /opt/swiftly, nothing in $HOME (a volume seeded only once):
#   SWIFTLY_TOOLCHAINS_DIR=/opt/swiftly/toolchains  (default would be ~/.local/share/swiftly/toolchains)
#   /opt/swiftly/current -> the installed toolchain; PATH ends with /opt/swiftly/current/usr/bin.
#     swiftly 1.2.0 creates no swift proxy in SWIFTLY_BIN_DIR (found 2026-11-09), so PATH points at the
#     toolchain itself. It goes LAST: the toolchain carries its own clang.
#   /opt/swift-toolchain -> /opt/swiftly/current: what devcontainer.json's swift.path uses. The VS Code Swift extension
#     wraps every command in `swiftly run` when swift.path starts with $SWIFTLY_HOME_DIR (a plain string prefix, no
#     realpath, no setting to turn it off: vscode-swift src/toolchain/swiftly.ts isManagedBySwiftly), and
#     `swiftly run` breaks in every Hyper* repo root (see devcontainer.json). This path must NOT start with /opt/swiftly.
#   /opt/swiftly/swift-sdks holds Swift SDKs; ~/.swiftpm/swift-sdks links there (postCreateCommand re-links it).
source /usr/local/share/hyperforge/lib.sh
# swiftly's dependency check for debian13 (it exits 1 without this; found 2026-11-09).
sudo apt-get update
sudo apt-get install -y --no-install-recommends binutils-gold
sudo rm -rf /var/lib/apt/lists/*
d=$(mktemp -d)
curl -fsSL "https://download.swift.org/swiftly/linux/swiftly-$UNAME_ARCH.tar.gz" | tar -xzf - -C "$d"
"$d/swiftly" init --assume-yes --no-modify-profile --quiet-shell-followup
rm -rf "$d"
SV=$(jq -r '.inUse' "$SWIFTLY_HOME_DIR/config.json")
ln -sfn "$SWIFTLY_TOOLCHAINS_DIR/$SV" /opt/swiftly/current
ln -sfn /opt/swiftly/current /opt/swift-toolchain
swift --version
# Static Linux SDK matching the installed toolchain; tag, SDK version and checksum from swift.org's release index.
REL=$(curl -fsSL https://www.swift.org/api/v1/install/releases.json)
TAG=$(jq -r --arg v "$SV" '.[] | select(.name == $v) | .tag' <<<"$REL")
SDKV=$(jq -r --arg v "$SV" '.[] | select(.name == $v) | .platforms[] | select(.platform == "static-sdk") | .version' <<<"$REL")
SUM=$(jq -r --arg v "$SV" '.[] | select(.name == $v) | .platforms[] | select(.platform == "static-sdk") | .checksum' <<<"$REL")
[ -n "$TAG" ] && [ -n "$SDKV" ] && [ -n "$SUM" ] || { echo "no static SDK for Swift $SV in releases.json"; exit 1; }
TAGL=$(tr '[:upper:]' '[:lower:]' <<<"${TAG%-RELEASE}")
mkdir -p /opt/swiftly/swift-sdks "$HOME/.swiftpm"
# Swift 6.4.2's `swift sdk install` reports "successfully installed" and then aborts with a core dump on exit
# (seen 2026-11-09 under wslc), so judge it by `swift sdk list`, not its exit code.
swift sdk install --swift-sdks-path /opt/swiftly/swift-sdks \
  "https://download.swift.org/$TAGL-release/static-sdk/$TAG/${TAG}_static-linux-$SDKV.artifactbundle.tar.gz" --checksum "$SUM" \
  || echo "swift sdk install exited $? (checking the result instead)"
ln -sfn /opt/swiftly/swift-sdks "$HOME/.swiftpm/swift-sdks"
swift sdk list | tee /tmp/sdks.txt
grep -q static-linux /tmp/sdks.txt || { echo "static Linux SDK not installed"; exit 1; }
rm /tmp/sdks.txt
case $DEB_ARCH in arm64) SA=arm64 ;; *) SA=amd64 ;; esac
d=$(mktemp -d)
fetch "https://github.com/realm/SwiftLint/releases/download/0.65.1/swiftlint_linux_$SA.zip" "$d/s.zip"
unzip -q "$d/s.zip" -d "$d"
sudo install -m 0755 "$d/swiftlint-static" /usr/local/bin/swiftlint
rm -rf "$d"
