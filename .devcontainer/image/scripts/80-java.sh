# Java (vscode): SDKMAN with two Temurin JDKs:
#   - 21, FIXED until it leaves support: the Gradle floor JDK (JNI path; FFM is final only from 22, JEP 454).
#     Auto-detected by Gradle, never downloaded (java/gradle.properties).
#   - newest LTS, FLOATS and is the default: SDKMAN's default java candidate (25 now, 29 in September 2027).
source /usr/local/share/hyperforge/lib.sh
curl -fsSL "https://get.sdkman.io?rcupdate=false&ci=true" | bash
sed -i 's/^sdkman_auto_answer=.*/sdkman_auto_answer=true/' "$SDKMAN_DIR/etc/config"
# From SDKMAN's API, not `sdk list` (paged/table output). Temurin ids carry a build number: 25.0.5.0+8-tem.
case $DEB_ARCH in arm64) SP=linuxarm64 ;; *) SP=linuxx64 ;; esac
J21=$(curl -fsSL "https://api.sdkman.io/2/candidates/java/$SP/versions/all" | tr ',' '\n' | grep -E '^21\.[0-9.]+(\+[0-9]+)?-tem$' | sort -V | tail -1)
JLTS=$(curl -fsSL https://api.sdkman.io/2/candidates/default/java)
[ -n "$J21" ] && [[ $JLTS == *-tem ]] || { echo "could not resolve Temurin 21 / default LTS ($J21 / $JLTS)"; exit 1; }
# sdkman-init.sh and sdk itself are neither errexit- nor nounset-clean, so run them loose and check results by hand.
set +eu
source "$SDKMAN_DIR/bin/sdkman-init.sh"
sdk install java "$J21"
sdk install java "$JLTS"
sdk default java "$JLTS"
sdk flush tmp
sdk flush archives
set -eu
for j in "$J21" "$JLTS"; do "$SDKMAN_CANDIDATES_DIR/java/$j/bin/java" -version; done
T=$(gh_latest_tag async-profiler/async-profiler)
case $DEB_ARCH in arm64) AP=arm64 ;; *) AP=x64 ;; esac
sudo install -d -o vscode -g vscode /opt/async-profiler
curl -fsSL "https://github.com/async-profiler/async-profiler/releases/download/$T/async-profiler-${T#v}-linux-$AP.tar.gz" \
  | tar -xzf - -C /opt/async-profiler --strip-components=1
