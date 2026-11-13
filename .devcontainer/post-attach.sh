# Runs on every attach (devcontainer.json postAttachCommand), as vscode, after VS Code's server is up and has done
# its own setup, so nothing below is overwritten by it.
set -euo pipefail
machine=~/.vscode-server/data/Machine
mkdir -p "$machine"

# The machine settings are machine-settings.jsonc, whole (see its header). VS Code reloads the file when it changes.
cp /workspaces/HyperForge/.devcontainer/machine-settings.jsonc "$machine/settings.json"

# Extensions: VS Code installs devcontainer.json's list only while this marker is absent, and it sits in the home
# volume, so after the first create an added extension was never installed. Deleting it after VS Code is done makes
# the next create install whatever the list gained.
rm -f "$machine/.installExtensionsMarker"

# Java: two warnings per project that are true by design, since JavaSE-21 runs on JDK 25 (machine-settings.jsonc):
# no strictly compatible JRE, and compliance 21 on a JRE 25. They are Eclipse launching preferences, which
# java.settings.url cannot set (the Java language server applies only compiler options from it), so each of its
# workspaces gets them here. One it creates later takes them from the next attach.
for ws in ~/.vscode-server/data/User/workspaceStorage/*/redhat.java/jdt_ws; do
  [ -d "$ws" ] || continue
  prefs="$ws/.metadata/.plugins/org.eclipse.core.runtime/.settings/org.eclipse.jdt.launching.prefs"
  mkdir -p "$(dirname "$prefs")"
  touch "$prefs"
  for key in PREF_STRICTLY_COMPATIBLE_JRE_NOT_AVAILABLE PREF_COMPILER_COMPLIANCE_DOES_NOT_MATCH_JRE; do
    sed -i "/^org\.eclipse\.jdt\.launching\.$key=/d" "$prefs"
    printf 'org.eclipse.jdt.launching.%s=ignore\n' "$key" >> "$prefs"
  done
done
