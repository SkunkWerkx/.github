# .NET (vscode): the newest SDK (11, GA 2026-11-10) + the 10.0 runtime (CI's second TFM). The last layer on purpose: a
# new SDK or runtime patch rebuilds only this one.
source /usr/local/share/hyperforge/lib.sh
fetch https://dot.net/v1/dotnet-install.sh /tmp/dotnet-install.sh
bash /tmp/dotnet-install.sh --channel 11.0 --install-dir "$DOTNET_ROOT"
bash /tmp/dotnet-install.sh --channel 10.0 --runtime dotnet --install-dir "$DOTNET_ROOT"
rm /tmp/dotnet-install.sh
dotnet tool install --tool-path /usr/local/dotnet-tools dotnet-trace
dotnet tool install --tool-path /usr/local/dotnet-tools dotnet-counters
