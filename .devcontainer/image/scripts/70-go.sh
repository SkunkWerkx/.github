# Go (vscode): newest stable release (floats), the tools CI pins, TinyGo.
source /usr/local/share/hyperforge/lib.sh
V=$(curl -fsSL 'https://go.dev/dl/?mode=json' | jq -r '[.[] | select(.stable) | .version][0]')
curl -fsSL "https://go.dev/dl/$V.linux-$DEB_ARCH.tar.gz" | sudo tar -xzf - -C /usr/local
export GOBIN=/usr/local/go-tools/bin GOPATH=/tmp/gopath
sudo install -d -o vscode -g vscode "$GOBIN"
go install golang.org/x/tools/gopls@latest
go install github.com/go-delve/delve/cmd/dlv@latest
go install github.com/mgechev/revive@v1.17.1
go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
go clean -modcache
rm -rf /tmp/gopath
T=$(gh_latest_tag tinygo-org/tinygo)
fetch "https://github.com/tinygo-org/tinygo/releases/download/$T/tinygo_${T#v}_$DEB_ARCH.deb" /tmp/tinygo.deb
sudo apt-get update
sudo apt-get install -y /tmp/tinygo.deb
rm /tmp/tinygo.deb
sudo rm -rf /var/lib/apt/lists/*
