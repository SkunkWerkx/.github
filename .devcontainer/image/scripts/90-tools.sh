# Cross-cutting tools (vscode): PowerShell, Claude Code.
source /usr/local/share/hyperforge/lib.sh
T=$(gh_latest_tag PowerShell/PowerShell)
case $DEB_ARCH in arm64) PA=arm64 ;; *) PA=x64 ;; esac
sudo install -d /opt/microsoft/powershell/7
curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/$T/powershell-${T#v}-linux-$PA.tar.gz" \
  | sudo tar -xzf - -C /opt/microsoft/powershell/7
sudo chmod +x /opt/microsoft/powershell/7/pwsh
sudo ln -sfn /opt/microsoft/powershell/7/pwsh /usr/local/bin/pwsh
# Claude Code: the native installer puts it in ~/.local (the home volume), where it self-updates.
curl -fsSL https://claude.ai/install.sh | bash
