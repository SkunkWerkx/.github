# Node (vscode): always the newest LTS line (policy: CI tests the current LTS + every LTS in maintenance;
# the container carries the current one), resolved from nodejs.org's release index at build time. Then Bun.
source /usr/local/share/hyperforge/lib.sh
case $DEB_ARCH in arm64) NA=arm64 ;; *) NA=x64 ;; esac
V=$(curl -fsSL https://nodejs.org/dist/index.json | jq -r '[.[] | select(.lts != false)][0].version')
sudo install -d -o vscode -g vscode /usr/local/node
curl -fsSL "https://nodejs.org/dist/$V/node-$V-linux-$NA.tar.xz" | tar -xJf - -C /usr/local/node --strip-components=1
npm install -g npm@latest
curl -fsSL https://bun.sh/install | bash
