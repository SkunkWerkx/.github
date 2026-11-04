# The companion's override inside a container that builds afresh (the companion-pins spec,
# "Isolated environments get the companion explicitly"). The host's override can't reach one:
# its Cargo patch lives in the host's $CARGO_HOME, and its Go replace names a host path.
#
# POSIX sh, sourced inside the container (`. /companion.sh`), which mounts the consumer's
# checkout read-only at <src> and copies what it builds into <work>; or with <src> and <work>
# the same checkout, mounted read-write (maturin-action's image, through before-script-linux). Each function reads
# <src>/.companions/env, which fetch-companions wrote on the host; without that file (no
# companion block, or release mode) it does nothing, and the registries decide as before.
# With it, it copies the pinned source to the same relative path under <work>, writes the
# override with <work> paths, and proves the origin inside the container before anything
# builds, failing with the binding, the origin found and the origin expected.
#
#   companion_cargo <src> <work> <workspace-dir>...
#       [patch.crates-io] <name> = { path = "<work>/<RUST_REQ>" } in the container's own
#       $CARGO_HOME/config.toml (kept, not added again, when that file already holds the same
#       entry; refused when it holds another patch of crates-io), then
#       `cargo tree -i <name> --locked` in each workspace. The
#       host's `cargo update -p` already moved the lock (a path package has no source in it),
#       so --locked holds; Cargo's "patch ... was not used" warning is an error.
#   companion_go <src> <work> <module-dir>
#       `go mod edit -replace <module>=<work>/<GO_REQ>` in <module-dir>, over the host's
#       replace, then `go list -m` must give that directory as the replacement.
#   companion_python <src> <scripts> <python> wire|origin
#       An interpreter in a Python image (which has one to run them): the Forge's own
#       companion_wire.py and companion_origin.py, from <scripts> (mounted), with <src> as the
#       root. `wire` installs the pinned file pip chooses for <python> (a wheel, else the
#       sdist) and proves it; `origin` proves it again, after the consumer's install. The host
#       already rewrote the requirement, so nothing is written under <src>.
#   companion_npm <src> <work> <scripts> <package-dir> wire|origin|unpack
#       The tarballs the host packed (.companions/<name>-npm/, with its wired.json) and the leg's
#       artifacts copied under <work>, then <scripts>/companion_npm.mjs under the image's Node (or
#       Bun, in an image without Node). `wire` points <package-dir>'s dependencies at the <work>
#       tarballs (the host's `file:` names a host path), regenerates its lock (`bun install
#       --lockfile-only`, or npm's beside a package-lock.json) and proves it; `origin` proves the
#       lock (the `file:` tarball, its integrity, the leg's payload inside), and after the install
#       with --installed the package Node resolves from <package-dir>/node_modules; `unpack`, for
#       an image that runs a suite without installing (Bun's), installs each wired tarball there
#       itself and proves it.
#   companion_ruby <src> <work> <scripts> wire|origin
#       A Ruby image, which runs the suite without Bundler (`gem install` of the gemspec's
#       runtime dependencies): `wire` copies the pinned gem, with the binaries the host placed
#       in it (companion_wire.py ruby), under <work>, puts its lib/ on RUBYLIB and names it in
#       HYPER_COMPANION_GEM, which the install loop skips; `origin`, after the last install,
#       runs <scripts>/companion_ruby.rb under this Ruby: the gem's <name>.rb must be the pin's,
#       at its version, on the backend the leg staged for this ABI (Magnus, else Fiddle), and
#       the extension or library it loaded must hash as SHA256SUMS lists the staged artifact; on
#       Fiddle, the load forced, /proc/self/maps must show that library as the one copy mapped.
#   companion_java <src> <work> <scripts> wire|origin [<gradle-dir>]
#       A JDK image (no Python): `wire` copies the job-local Maven repository the host published
#       the pin into (.companions/m2/, its jar checked against the host's record) under <work>,
#       and copies <scripts>/companion_java.init.gradle, unchanged, to the container's
#       ${GRADLE_USER_HOME:-~/.gradle}/init.d/companions.gradle, with that copy's data beside it
#       in companions.properties (escaped as companion_wire.java_properties escapes it); `origin`,
#       right before the suite, runs `./gradlew -q companionOrigin` in <gradle-dir>: every copy
#       of the module the suites load must be the <work> copy with the recorded SHA-256.
#   companion_csharp <src> <work> wire | origin <project> [restore args...]
#       A .NET SDK image (no Python): `wire` copies the feed the host packed (.companions/nuget/,
#       its nupkg checked against the host's record) under <work> and writes <work>/nuget.config
#       for it (the host already rewrote the requirements); `origin` restores <project> with the
#       given arguments, then, in the global packages folder the project's assets file names
#       (packageFolders), the package's .nupkg.metadata must name that feed and the recorded
#       contentHash and its nupkg must be the packed bytes, and the assets file's own entry for
#       the package must carry that sha512.
#   companion_swift <src> <work> <package-dir>...
#       A Swift image (no Python): the pin's root Package.swift and swift/ (the host extracted its
#       swift-release-assets there) copied under <work>, with the env and the host's record of
#       the extracted files (.companions/<name>-swift.sums); those files must hash as recorded,
#       with no other file in their trees. The host already pointed every manifest at the pin by
#       a relative path, so `swift package show-dependencies` in each <package-dir> must give the
#       <work> copy as the companion's path.
#   companion_swift_link <work> <package-dir> [swift build arguments...]
#       Right before a check's own build: `swift build -v` with those arguments, and every archive
#       of the companion it links (by path, or by -l name in its -L directories) must hash as one
#       of the recorded files. A build with nothing to do links nothing and fails, so it runs on a
#       fresh one.
#   companion_c <root> <rid>
#       An image with bash (the smoke's own shell), the checkout mounted at <root>: the pin's
#       unpacked C archives (.companions/<name>-c/) must hash as the host recorded them, with no
#       other file; <NAME>_PACKAGES is exported as that directory (the host's names a runner
#       path), and the consumer's own c/smoke/<name>.sh, sourced by bash, must ask for the pin's
#       version and report its archive for <rid>; then CMake (the image's, as the smoke's), with
#       that directory on its prefix path, must choose that archive's package and import its files.

# "<PREFIX> <relative path>" from the env's <PREFIX>_<ECO>_REQ line.
companion_req() {
  sed -n "s/^\([A-Z][A-Z0-9_]*\)_$2_REQ=\(.*\)\$/\1 \2/p" "$1/.companions/env"
}

# Nothing when <src> is <work> (maturin-action's image mounts the checkout read-write at its own
# path): tar onto the files it is reading would truncate them.
companion_copy() {
  [ "$1" != "$2" ] || return 0
  mkdir -p "$2"
  (cd "$1" && tar cf - --exclude=target "./$3") | tar xf - -C "$2"
}

companion_cargo() {
  c_src=$1
  c_work=$2
  shift 2
  [ -f "$c_src/.companions/env" ] || return 0
  c_line=$(companion_req "$c_src" RUST)
  if [ -z "$c_line" ]; then
    echo "::error::$c_src/.companions/env has no *_RUST_REQ: the host never wired Rust, so this container would build the registry's copy"
    return 1
  fi
  c_name=$(printf '%s' "${c_line%% *}" | tr '[:upper:]' '[:lower:]')
  c_path="$c_work/${c_line#* }"
  companion_copy "$c_src" "$c_work" "${c_line#* }"
  c_config="${CARGO_HOME:-$HOME/.cargo}/config.toml"
  mkdir -p "$(dirname "$c_config")"
  c_entry="$c_name = { path = \"$c_path\" }"
  # A config already holding this entry (the host's own, which fetch-companions wrote, as
  # under a step that runs on the runner) is kept and still proven: a second
  # [patch.crates-io] table is a duplicate key cargo cannot load. Any other one is refused.
  if [ -f "$c_config" ] && C_ENTRY=$c_entry awk '
      p == "[patch.crates-io]" && $0 == ENVIRON["C_ENTRY"] { held = 1 } { p = $0 } END { exit !held }' "$c_config"; then
    echo "$c_config already holds [patch.crates-io] $c_entry"
  elif [ -f "$c_config" ] && grep -q '^[[:space:]]*\[[[:space:]]*patch\.crates-io[[:space:]]*\]' "$c_config"; then
    echo "::error::$c_config already has a [patch.crates-io] table without $c_entry: two tables cannot both hold, and that one would decide what this builds"
    return 1
  else
    printf '\n# fetch-companions: the pinned %s, inside the container\n[patch.crates-io]\n%s\n' \
      "$c_name" "$c_entry" >> "$c_config"
    echo "$c_config: [patch.crates-io] $c_entry"
  fi
  c_expected="$c_name from $c_path (the pin, a path source)"
  for c_dir in "$@"; do
    c_err=$(mktemp)
    c_code=0
    # --color never, and a pattern that holds without it (an ANSI code is an ESC, which isn't
    # printable, then [...m) and on Cargo 1.85-1.90's "Patch ... graph.": as companion_origin.py.
    c_out=$(cargo tree -i "$c_name" --locked --all-features --depth 0 --prefix none --color never \
      --manifest-path "$c_dir/Cargo.toml" 2>"$c_err") || c_code=$?
    cat "$c_err" >&2
    c_unused=$(sed 's/[^[:print:]]\[[0-9;]*m//g' "$c_err" |
      grep -Ei '^warning: patch .* was not used in the crate graph' || true)
    rm -f "$c_err"
    if [ -n "$c_unused" ]; then
      c_found="Cargo's '$c_unused'"
    elif [ "$c_code" != 0 ]; then
      c_found="cargo tree failing (exit $c_code)"
    else
      case "$c_out" in
        "$c_name v"*" ($c_path)")
          echo "Rust ($c_dir): $c_out"
          continue
          ;;
        "$c_name v"*" ("*")") c_found=$c_out ;;
        "$c_name v"*) c_found="$c_out from a registry (crates.io)" ;;
        *) c_found=${c_out:-nothing} ;;
      esac
    fi
    echo "::error::Rust ($c_dir): found $c_found, expected $c_expected"
    return 1
  done
}

companion_go() {
  g_src=$1
  g_work=$2
  g_dir=$3
  [ -f "$g_src/.companions/env" ] || return 0
  g_line=$(companion_req "$g_src" GO)
  if [ -z "$g_line" ]; then
    echo "::error::$g_src/.companions/env has no *_GO_REQ: the host never wired Go, so this container would take the module proxy's copy"
    return 1
  fi
  g_path="$g_work/${g_line#* }"
  companion_copy "$g_src" "$g_work" "${g_line#* }"
  g_module=$(sed -n 's/^module[[:space:]]*"\{0,1\}\([^"[:space:]]*\).*/\1/p' "$g_path/go.mod")
  [ -n "$g_module" ] || { echo "::error::$g_path/go.mod names no module"; return 1; }
  (cd "$g_dir" && go mod edit "-replace=$g_module=$g_path")
  g_expected="$g_module replaced by $g_path (the pin)"
  if ! g_found=$(cd "$g_dir" && go list -m -f '{{with .Replace}}{{.Dir}}{{end}}' "$g_module"); then
    echo "::error::Go ($g_dir): found no copy (go list failed), expected $g_expected"
    return 1
  fi
  if [ "$g_found" != "$g_path" ]; then
    echo "::error::Go ($g_dir): found $g_module replaced by ${g_found:-nothing}, expected $g_expected"
    return 1
  fi
  echo "Go ($g_dir): $g_module => $g_found"
}

companion_python() {
  p_src=$1
  p_scripts=$2
  p_python=$3
  p_step=$4
  [ -f "$p_src/.companions/env" ] || return 0
  if [ -z "$(companion_req "$p_src" PYTHON)" ]; then
    echo "::error::$p_src/.companions/env has no *_PYTHON_REQ: the host never wired Python, so this interpreter would install the index's copy"
    return 1
  fi
  case $p_step in
    wire)
      "$p_python" "$p_scripts/companion_wire.py" python --root "$p_src" --python "$p_python" &&
        "$p_python" "$p_scripts/companion_origin.py" python --root "$p_src" --python "$p_python"
      ;;
    origin) "$p_python" "$p_scripts/companion_origin.py" python --root "$p_src" --python "$p_python" ;;
    *) false ;;
  esac || {
    echo "::error::Python ($p_src): companion_python $p_step failed with $p_python"
    return 1
  }
}

companion_npm() {
  n_src=$1
  n_work=$2
  n_scripts=$3
  n_dir=$4
  n_step=$5
  shift 5
  [ -f "$n_src/.companions/env" ] || return 0
  n_line=$(companion_req "$n_src" NPM)
  if [ -z "$n_line" ]; then
    echo "::error::$n_src/.companions/env has no *_NPM_REQ: the host never wired npm, so this container would install the registry's copy"
    return 1
  fi
  n_prefix=${n_line%% *}
  n_name=$(printf '%s' "$n_prefix" | tr '[:upper:]' '[:lower:]')
  # This companion's own line: an empty path would copy the whole checkout into <work>.
  n_artifacts=$(sed -n "s/^${n_prefix}_ARTIFACTS=//p" "$n_src/.companions/env")
  if [ -z "$n_artifacts" ]; then
    echo "::error::$n_src/.companions/env has no ${n_prefix}_ARTIFACTS: the host never staged the leg's artifacts"
    return 1
  fi
  companion_copy "$n_src" "$n_work" ".companions/env"
  companion_copy "$n_src" "$n_work" ".companions/$n_name-npm"
  companion_copy "$n_src" "$n_work" "$n_artifacts"
  if command -v node >/dev/null 2>&1; then n_js=node; else n_js=bun; fi
  "$n_js" "$n_scripts/companion_npm.mjs" "$n_step" "$n_work" "$n_dir" "$@" || {
    echo "::error::npm ($n_dir): companion_npm $n_step failed with $n_js"
    return 1
  }
}

companion_ruby() {
  r_src=$1
  r_work=$2
  r_scripts=$3
  r_step=$4
  [ -f "$r_src/.companions/env" ] || return 0
  r_line=$(companion_req "$r_src" RUBY)
  if [ -z "$r_line" ]; then
    echo "::error::$r_src/.companions/env has no *_RUBY_REQ: the host never wired Ruby, so this container would install RubyGems' copy"
    return 1
  fi
  r_prefix=${r_line%% *}
  r_name=$(printf '%s' "$r_prefix" | tr '[:upper:]' '[:lower:]')
  r_gem="$r_work/${r_line#* }"
  r_version=$(sed -n "s/^${r_prefix}_VERSION=//p" "$r_src/.companions/env")
  r_sums="$r_src/$(sed -n "s/^${r_prefix}_ARTIFACTS=//p" "$r_src/.companions/env")/SHA256SUMS"
  r_expected="$r_name $r_version from $r_gem/lib (the pin, on RUBYLIB), each binary it loads as SHA256SUMS lists it"
  case $r_step in
    wire)
      companion_copy "$r_src" "$r_work" "${r_line#* }"
      RUBYLIB="$r_gem/lib${RUBYLIB:+:$RUBYLIB}"
      HYPER_COMPANION_GEM=$r_name
      export RUBYLIB HYPER_COMPANION_GEM
      echo "Ruby: RUBYLIB=$RUBYLIB; $r_name $r_version from the pin, not gem install"
      return 0
      ;;
    origin) ;;
    *) return 1 ;;
  esac
  if ! r_facts=$(ruby "$r_scripts/companion_ruby.rb" "$r_name"); then
    echo "::error::Ruby ($(ruby -e 'print RUBY_VERSION')): found requiring $r_name failing, expected $r_expected"
    return 1
  fi
  r_fact() { printf '%s\n' "$r_facts" | sed -n "s/^$1=//p"; }
  r_binding="Ruby $(r_fact ruby) ($r_work)"
  r_source=$(r_fact source)
  r_backend=$(r_fact backend)
  r_abi=$(r_fact abi)
  r_rid=$(r_fact rid)
  r_found=
  if [ "$r_source" != "$r_gem/lib/$r_name.rb" ]; then
    r_found="$r_name required from ${r_source:-nowhere}"
  elif [ "$(r_fact version)" != "$r_version" ]; then
    r_found="$r_name $(r_fact version) from $r_source"
  elif [ "$r_backend" = native ]; then
    r_file=$(r_fact extension)
    r_digest=$(r_fact extension_sha256)
    r_key="ruby-native-$r_rid-$r_abi/$(basename "$r_file")"
    [ "$r_file" = "$r_gem/lib/$r_name/$r_abi/$(basename "$r_file")" ] || r_found="the native backend loading ${r_file:-no file of the pin}"
  elif [ "$r_backend" = fiddle ]; then
    if [ -z "$(r_fact pure)" ] && ls "$r_gem/lib/$r_name/$r_abi/${r_name}_native."* >/dev/null 2>&1; then
      r_found="the Fiddle backend, though the leg staged lib/$r_name/$r_abi/ for Ruby $r_abi and ${r_prefix}_PURE is unset"
    fi
    r_file=$(r_fact library)
    r_digest=$(r_fact library_sha256)
    r_key="native-$r_rid/$(basename "$r_file")"
    [ -n "$r_found" ] || [ "$r_file" = "$r_gem/lib/$r_name/native/$r_rid/$(basename "$r_file")" ] ||
      r_found="the Fiddle backend loading ${r_file:-no file of the pin}"
    # What the forced load mapped (/proc/self/maps): that one file, no other copy.
    [ -n "$r_found" ] || { [ "$(r_fact observed)" = 1 ] && [ "$(r_fact mapped)" = "$r_file" ]; } ||
      r_found="the Fiddle backend mapping $(r_fact mapped | sed 's/^$/no/') $(basename "$r_file") after the load, though its search named $r_file"
  else
    r_found="backend '$r_backend'"
  fi
  if [ -z "$r_found" ]; then
    r_listed=$(R_KEY=$r_key awk '$2 == ENVIRON["R_KEY"] { print $1 }' "$r_sums" 2>/dev/null)
    [ -n "$r_listed" ] && [ "$r_digest" = "$r_listed" ] ||
      r_found="$r_file with sha256 ${r_digest:-none}, not $r_key as SHA256SUMS lists it (${r_listed:-absent})"
  fi
  if [ -n "$r_found" ]; then
    echo "::error::$r_binding: found $r_found, expected $r_expected"
    return 1
  fi
  echo "$r_binding: $r_name $r_version from $r_source, $r_backend backend, $r_file (sha256 $r_digest)"
}

# The record companion_wire.py wrote for an ecosystem: "<prefix> <name> <record path>", or an
# ::error:: naming what is missing.
companion_record() {
  cr_line=$(companion_req "$1" "$2")
  if [ -z "$cr_line" ]; then
    echo "::error::$1/.companions/env has no *_$2_REQ: the host never wired $3, so this container would take $4"
    return 1
  fi
  cr_prefix=${cr_line%% *}
  cr_name=$(printf '%s' "$cr_prefix" | tr '[:upper:]' '[:lower:]')
  cr_record="$1/.companions/$cr_name-$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]').record"
  if [ ! -f "$cr_record" ]; then
    echo "::error::no $cr_record: the host's companion_wire.py never built the pin, so this container would take $4"
    return 1
  fi
}

companion_java() {
  j_src=$1
  j_work=$2
  j_scripts=$3
  j_step=$4
  j_dir=${5:-}
  [ -f "$j_src/.companions/env" ] || return 0
  companion_record "$j_src" JAVA Java "Maven Central's copy" || return 1
  j_field() { sed -n "s/^$1=//p" "$cr_record"; }
  j_group=$(j_field group)
  j_module=$(j_field module)
  j_version=$(j_field version)
  j_jar=$(j_field jar)
  j_sha=$(j_field sha256)
  # Canonical, so the paths Gradle names compare as the same strings (a symlinked work root).
  j_repo="$(cd "$j_work" 2>/dev/null && pwd -P || printf '%s' "$j_work")/.companions/m2"
  j_expected="$j_group:$j_module:$j_version from $j_repo (the pin, sha256 $j_sha)"
  case $j_step in
    wire)
      companion_copy "$j_src" "$j_work" .companions/m2
      j_held=$(sha256sum 2>/dev/null < "$j_work/$j_jar" | cut -d' ' -f1)
      if [ "$j_held" != "$j_sha" ]; then
        echo "::error::Java ($j_work): found $j_work/$j_jar with sha256 ${j_held:-absent}, expected $j_expected"
        return 1
      fi
      # The init script is copied as it is; its data goes beside it in companions.properties,
      # escaped as java.util.Properties reads it (companion_wire.java_properties, byte for byte).
      if printf '%s' "$j_repo$j_group$j_module$j_version$j_sha" | LC_ALL=C grep -q '[^ -~]'; then
        echo "::error::Java ($j_work): $j_repo or the record holds a character outside printable ASCII"
        return 1
      fi
      j_init="${GRADLE_USER_HOME:-$HOME/.gradle}/init.d"
      mkdir -p "$j_init"
      cp "$j_scripts/companion_java.init.gradle" "$j_init/companions.gradle"
      j_prop() { printf '%s=%s\n' "$1" "$(printf '%s' "$2" | sed -e 's/\\/\\\\/g' -e 's/[=:#! ]/\\&/g')"; }
      {
        echo "# The pinned companion's data, which companions.gradle beside it reads (companion_wire.py java)."
        j_prop repository "$j_repo"
        j_prop group "$j_group"
        j_prop module "$j_module"
        j_prop version "$j_version"
        j_prop sha256 "$j_sha"
      } > "$j_init/companions.properties"
      echo "$j_init/companions.gradle: $j_group:$j_module:$j_version from $j_repo (sha256 $j_sha)"
      return 0
      ;;
    origin) ;;
    *) return 1 ;;
  esac
  j_err=$(mktemp)
  j_code=0
  j_out=$(cd "$j_dir" && ./gradlew -q --console=plain companionOrigin 2>"$j_err") || j_code=$?
  printf '%s\n' "$j_out"
  cat "$j_err" >&2
  # The first selection of the module that is not the pin, else "none" when Gradle named none.
  # The values reach awk through its environment: -v would read a backslash in a path as an escape.
  j_found=$(printf '%s\n' "$j_out" | J_REPO="$j_repo/" J_SHA="$j_sha" J_WANT="$j_group:$j_module:" \
      J_VERSION="$j_version" awk -F '\t' '
    BEGIN { repo = ENVIRON["J_REPO"]; sha = ENVIRON["J_SHA"]; want = ENVIRON["J_WANT"]; version = ENVIRON["J_VERSION"] }
    $1 == "companion-origin" && index($3, want) == 1 {
      n++
      if (index($4, repo) != 1 || $5 != sha || $3 != want version) { print "at " $2 ": " $3 " from " $4 " (sha256 " $5 ")"; bad = 1; exit }
    }
    END { if (!bad && !n) print "none" }')
  case $j_found in
    none)
      if grep -q "Task 'companionOrigin' not found" "$j_err"; then
        j_found="no companionOrigin task in the build: the init script is not installed, so Gradle resolves $j_group:$j_module from the build's own repositories (Maven Central)"
      elif [ "$j_code" != 0 ]; then
        j_found="gradlew companionOrigin failing (exit $j_code)"
      else
        j_found="no companion-origin line: no project's classpath held $j_group:$j_module"
      fi
      ;;
    *"/caches/modules-2/"*) j_found="$j_found, a remote repository's copy (Maven Central, from Gradle's module cache)" ;;
    '')
      if [ "$j_code" != 0 ]; then j_found="gradlew companionOrigin failing (exit $j_code)"; fi
      ;;
  esac
  rm -f "$j_err"
  if [ -n "$j_found" ]; then
    echo "::error::Java ($j_dir): found $j_found, expected $j_expected"
    return 1
  fi
  echo "Java ($j_dir): $j_group:$j_module:$j_version from $j_repo (sha256 $j_sha)"
}

# nuget.config for the job-local feed at <feed>: companion_wire.py's _nuget_config_text, byte for
# byte (test_companion_workflows holds the two equal).
companion_nuget_config() {
  printf '%s\n' '<?xml version="1.0" encoding="utf-8"?>' \
    "<!-- companion_wire.py csharp: the pinned companion's job-local feed. -->" \
    '<configuration>' '  <packageSources>' '    <clear />' \
    "    <add key=\"companions\" value=\"$1\" />" \
    '    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" protocolVersion="3" />' \
    '  </packageSources>' '  <packageSourceMapping>' '    <packageSource key="companions">' \
    "      <package pattern=\"$2\" />" '    </packageSource>' '    <packageSource key="nuget.org">' \
    '      <package pattern="*" />' '    </packageSource>' '  </packageSourceMapping>' '</configuration>'
}

companion_csharp() {
  d_src=$1
  d_work=$2
  d_step=$3
  [ -f "$d_src/.companions/env" ] || return 0
  companion_record "$d_src" CSHARP 'C#' "nuget.org's copy" || return 1
  d_field() { sed -n "s/^$1=//p" "$cr_record"; }
  d_package=$(d_field package)
  d_version=$(d_field version)
  d_nupkg=$(d_field nupkg)
  d_sha=$(d_field sha512)
  d_hash=$(d_field content_hash)
  d_feed="$d_work/.companions/nuget"
  d_expected="$d_package $d_version from $d_feed (the pin, contentHash $d_hash)"
  case $d_step in
    wire)
      companion_copy "$d_src" "$d_work" .companions/nuget
      d_held=$(sha512sum 2>/dev/null < "$d_work/$d_nupkg" | cut -d' ' -f1)
      if [ "$d_held" != "$d_sha" ]; then
        echo "::error::C# ($d_work): found $d_work/$d_nupkg with sha512 ${d_held:-absent}, expected $d_expected"
        return 1
      fi
      case "$d_feed$d_package" in
        *[\"\<\>\&]*)
          echo "::error::C# ($d_work): $d_feed or $d_package holds a character nuget.config would need escaped"
          return 1
          ;;
      esac
      companion_nuget_config "$d_feed" "$d_package" > "$d_work/nuget.config"
      echo "$d_work/nuget.config: $d_package $d_version from $d_feed only"
      return 0
      ;;
    origin) ;;
    *) return 1 ;;
  esac
  d_project=$4
  shift 4
  if ! dotnet restore "$d_project" "$@"; then
    echo "::error::C# ($d_project): found dotnet restore failing, expected $d_expected"
    return 1
  fi
  d_lower=$(printf '%s' "$d_package" | tr '[:upper:]' '[:lower:]')
  d_assets="$(dirname "$d_project")/obj/project.assets.json"
  # The global packages folder restore used is the assets file's packageFolders (its first key):
  # NUGET_PACKAGES's, or a RestorePackagesPath or globalPackagesFolder that moved it.
  d_default="${NUGET_PACKAGES:-$HOME/.nuget/packages}"
  d_packages=$(awk '/"packageFolders"/ { getline; if (match($0, /"[^"]*"/)) print substr($0, RSTART + 1, RLENGTH - 2); exit }' \
    "$d_assets" 2>/dev/null || true)
  d_packages=${d_packages%/}
  d_note=
  if [ -n "$d_packages" ] && [ "$d_packages" != "${d_default%/}" ]; then
    d_note=" (the assets file's global packages folder, not NUGET_PACKAGES $d_default)"
  fi
  d_folder="${d_packages:-${d_default%/}}/$d_lower/$d_version"
  d_metadata="$d_folder/.nupkg.metadata"
  d_source=$(sed -n 's/.*"source"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$d_metadata" 2>/dev/null || true)
  d_content=$(sed -n 's/.*"contentHash"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$d_metadata" 2>/dev/null || true)
  d_copy=$(sha512sum 2>/dev/null < "$d_folder/$d_lower.$d_version.nupkg" | cut -d' ' -f1)
  # The sha512 of the package's own entry under "libraries", not any line that carries the hash.
  d_library=$(D_KEY="\"$d_package/$d_version\": {" awk '
    /"libraries"/ { libraries = 1 }
    libraries && index($0, ENVIRON["D_KEY"]) {
      getline
      if ($0 ~ /"sha512"/) { sub(/.*"sha512"[[:space:]]*:[[:space:]]*"/, ""); sub(/".*/, ""); print }
      else print "absent"
      exit
    }' "$d_assets" 2>/dev/null || true)
  d_found=
  if [ ! -f "$d_assets" ]; then
    d_found="$d_assets: restore wrote no assets file"
  elif [ ! -f "$d_metadata" ]; then
    d_found="no $d_metadata: restore extracted no $d_package $d_version$d_note"
  elif [ "${d_source%/}" != "$d_feed" ]; then
    case $d_source in
      *nuget.org*) d_found="$d_package $d_version at $d_folder, nuget.org's copy (source $d_source)" ;;
      *) d_found="$d_package $d_version at $d_folder, another source's copy (source $d_source)" ;;
    esac
  elif [ "$d_content" != "$d_hash" ]; then
    d_found="$d_package $d_version at $d_folder with contentHash $d_content"
  elif [ "$d_copy" != "$d_sha" ]; then
    d_found="$d_folder/$d_lower.$d_version.nupkg with sha512 ${d_copy:-absent}"
  elif [ -z "$d_library" ]; then
    d_found="no $d_package/$d_version in $d_assets"
  elif [ "$d_library" != "$d_hash" ]; then
    d_found="$d_assets restoring $d_package $d_version with sha512 $d_library"
  fi
  if [ -n "$d_found" ]; then
    echo "::error::C# ($d_project): found $d_found, expected $d_expected"
    return 1
  fi
  echo "C# ($d_project): $d_package $d_version from $d_source at $d_folder$d_note (contentHash $d_hash)"
}

# <dir> holds every file <record> (sha256sum's format, paths relative to <dir>) lists, with that
# hash, and no other file in the trees those paths start (their first <depth> components; 0:
# all of <dir>).
companion_sums() {
  (cd "$1" && sha256sum -c "$2" >/dev/null 2>&1) || return 1
  cs_want=$(wc -l < "$2")
  if [ "$3" = 0 ]; then
    cs_trees=.
  else
    cs_trees=$(sed -n 's/^[0-9a-f]\{64\}  //p' "$2" | cut -d/ -f1-"$3" | sort -u)
  fi
  cs_have=0
  for cs_tree in $cs_trees; do
    cs_have=$((cs_have + $(find "$1/$cs_tree" -type f | wc -l)))
  done
  [ "$cs_have" -eq "$cs_want" ]
}

# "<prefix> <name> <pin> <record> <expected>" for Swift, from <dir>/.companions/env, or an
# ::error:: naming what is missing.
companion_swift_req() {
  sr_line=$(companion_req "$1" SWIFT)
  if [ -z "$sr_line" ]; then
    echo "::error::$1/.companions/env has no *_SWIFT_REQ: the host never wired Swift, so this container would take the remote package"
    return 1
  fi
  sr_name=$(printf '%s' "${sr_line%% *}" | tr '[:upper:]' '[:lower:]')
  sr_rel=${sr_line#* }
  sr_record=".companions/$sr_name-swift.sums"
}

companion_swift() {
  s_src=$1
  s_work=$2
  shift 2
  [ -f "$s_src/.companions/env" ] || return 0
  companion_swift_req "$s_src" || return 1
  s_name=$sr_name
  s_pin="$s_work/$sr_rel"
  s_expected="$s_name from $s_pin (the pin, with the bundle and XCFramework from swift-release-assets)"
  if [ ! -f "$s_src/$sr_record" ]; then
    echo "::error::no $s_src/$sr_record: the host's companion_wire.py never extracted the pin, so this container would take the remote package"
    return 1
  fi
  # The pin's root manifest and swift/, never its .build or the rest of its checkout; the env and
  # the record beside them, for companion_swift_link.
  if [ "$s_src" != "$s_work" ]; then
    mkdir -p "$s_pin"
    (cd "$s_src/$sr_rel" && tar cf - --exclude=.build ./Package.swift ./swift) | tar xf - -C "$s_pin"
    cp "$s_src/.companions/env" "$s_src/$sr_record" "$s_work/.companions/"
  fi
  if ! companion_sums "$s_pin" "$s_work/$sr_record" 2; then
    echo "::error::Swift ($s_work): found $s_pin differing from the host's record ($sr_record), expected $s_expected"
    return 1
  fi
  for s_dir in "$@"; do
    if ! s_out=$(cd "$s_dir" && swift package show-dependencies --format json); then
      echo "::error::Swift ($s_dir): found show-dependencies failing, expected $s_expected"
      return 1
    fi
    # The companion's node: its url, version and path, one per line (SwiftPM prints each key on
    # its own line; the split makes a one-line document the same).
    s_node=$(printf '%s\n' "$s_out" | tr ',{}[]' '\n' | S_NAME=$s_name awk '
      function value(line) { sub(/^[^:]*:[ \t]*"/, "", line); sub(/"[ \t]*$/, "", line); gsub(/\\\//, "/", line); return line }
      /"identity"[ \t]*:/ { inside = (value($0) == ENVIRON["S_NAME"]); next }
      inside && /"url"[ \t]*:/ { url = value($0) }
      inside && /"version"[ \t]*:/ { version = value($0) }
      inside && /"path"[ \t]*:/ { print value($0); print version; print url; exit }')
    s_path=$(printf '%s\n' "$s_node" | sed -n 1p)
    if [ -z "$s_path" ]; then
      echo "::error::Swift ($s_dir): found no $s_name in the package graph, expected $s_expected"
      return 1
    fi
    if [ "$(cd "$s_path" 2>/dev/null && pwd -P)" != "$(cd "$s_pin" && pwd -P)" ]; then
      echo "::error::Swift ($s_dir): found $s_name $(printf '%s\n' "$s_node" | sed -n 2p) from $(printf '%s\n' "$s_node" | sed -n 3p) (at $s_path), expected $s_expected"
      return 1
    fi
    echo "Swift ($s_dir): $s_name from $s_path"
  done
}

companion_swift_link() {
  sl_work=$1
  sl_dir=$2
  shift 2
  [ -f "$sl_work/.companions/env" ] || return 0
  companion_swift_req "$sl_work" || return 1
  sl_name=$sr_name
  sl_expected="$sl_name from $sl_work/$sr_rel (the pin, with the bundle and XCFramework from swift-release-assets)"
  sl_log=$(mktemp)
  sl_code=0
  (cd "$sl_dir" && swift build -v "$@") > "$sl_log" 2>&1 || sl_code=$?
  if [ "$sl_code" != 0 ]; then
    # The build's errors (coloured or not), wherever they fell in a verbose log, then its end.
    grep 'error: ' "$sl_log" | head -n 20 || true
    tail -n 20 "$sl_log"
    rm -f "$sl_log"
    echo "::error::Swift ($sl_dir): found swift build failing (exit $sl_code), expected $sl_expected"
    return 1
  fi
  # Every archive of the companion the build names: by path (Swift Build), or by -l name, found
  # in the line's -L directories in order (the pre-6.4 build system's copy beside the products).
  sl_linked=$(S_NAME=$sl_name awk '
    function bare(t) { gsub(/^["\047]|["\047]$/, "", t); return t }
    {
      n = split($0, f, /[ \t]+/); m = 0; lib = 0
      for (i = 1; i <= n; i++) {
        t = bare(f[i])
        if (t ~ ("/lib" ENVIRON["S_NAME"] "\\.a$")) print t
        else if (t == "-l" ENVIRON["S_NAME"]) lib = 1
        else if (t == "-L" && i < n) dirs[++m] = bare(f[++i])
        else if (t ~ /^-L./) dirs[++m] = substr(t, 3)
      }
      if (lib) for (j = 1; j <= m; j++) {
        candidate = dirs[j] "/lib" ENVIRON["S_NAME"] ".a"
        if ((getline line < candidate) >= 0) { close(candidate); print candidate; break }
      }
    }' "$sl_log" | sort -u)
  rm -f "$sl_log"
  if [ -z "$sl_linked" ]; then
    echo "::error::Swift ($sl_dir): found a verbose build that linked no lib$sl_name.a (an up-to-date build links nothing, so the proof runs on a fresh one), expected $sl_expected"
    return 1
  fi
  for sl_file in $sl_linked; do
    sl_hash=$(sha256sum 2>/dev/null < "$sl_file" | cut -d' ' -f1)
    if [ -z "$sl_hash" ] || ! grep -q "^$sl_hash  " "$sl_work/$sr_record"; then
      echo "::error::Swift ($sl_dir): found $sl_file linked, expected $sl_expected"
      return 1
    fi
    echo "Swift ($sl_dir): links $sl_file"
  done
}

companion_c() {
  cc_root=$1
  cc_rid=$2
  [ -f "$cc_root/.companions/env" ] || return 0
  cc_line=$(companion_req "$cc_root" C)
  if [ -z "$cc_line" ]; then
    echo "::error::$cc_root/.companions/env has no *_C_REQ: the host never wired C, so the smoke would download the GitHub Release's archive"
    return 1
  fi
  cc_prefix=${cc_line%% *}
  cc_name=$(printf '%s' "$cc_prefix" | tr '[:upper:]' '[:lower:]')
  cc_version=$(sed -n "s/^${cc_prefix}_VERSION=//p" "$cc_root/.companions/env")
  cc_packages="$cc_root/.companions/$cc_name-c"
  cc_script="c/smoke/$cc_name.sh"
  cc_expected="$cc_name $cc_version from $cc_packages (the pin's c-packages-ci, unpacked), as $cc_script reports it"
  if [ ! -f "$cc_root/.companions/$cc_name-c.sums" ] || ! companion_sums "$cc_packages" "$cc_root/.companions/$cc_name-c.sums" 0; then
    echo "::error::C ($cc_root): found $cc_packages differing from the host's record (.companions/$cc_name-c.sums), expected $cc_expected"
    return 1
  fi
  export "${cc_prefix}_PACKAGES=$cc_packages"
  if [ ! -f "$cc_root/$cc_script" ]; then
    echo "::error::C ($cc_root): found no $cc_script, expected $cc_expected"
    return 1
  fi
  # The consumer's own script, as the smoke sources it: the version it asks for and the archive it
  # reports for this platform. Data reaches it as arguments, never as code.
  cc_work=$(mktemp -d)
  cc_err=$(mktemp)
  cc_code=0
  cc_out=$(cd "$cc_root" && bash -c '. "$1"; wanted="$("$5"_version "$2")"; printf "%s\n" "$wanted"; "$5"_archive "$3" "$wanted" "$4"' \
    probe "$cc_root/$cc_script" "$cc_root" "$cc_rid" "$cc_work" "$cc_name" 2> "$cc_err") || cc_code=$?
  cc_tail=$(tail -n 1 "$cc_err")
  rm -rf "$cc_work" "$cc_err"
  cc_wanted=$(printf '%s\n' "$cc_out" | sed -n 1p)
  cc_reported=$(printf '%s\n' "$cc_out" | sed -n 2p)
  cc_binding="C ($cc_script, $cc_rid)"
  cc_found=
  if [ -n "$cc_wanted" ] && [ "$cc_wanted" != "$cc_version" ]; then
    cc_found="a request for $cc_name $cc_wanted (rust/Cargo.toml)"
  elif [ "$cc_code" != 0 ] || [ -z "$cc_reported" ]; then
    cc_found="the script failed (exit $cc_code): $cc_tail"
  elif [ "$(cd "$cc_reported" 2>/dev/null && pwd -P)" != "$(cd "$cc_packages/$cc_name-$cc_version-$cc_rid" 2>/dev/null && pwd -P)" ]; then
    cc_found="$cc_name $cc_wanted from $cc_reported"
  fi
  if [ -n "$cc_found" ]; then
    echo "::error::$cc_binding: found $cc_found, expected $cc_expected"
    return 1
  fi
  echo "$cc_binding: $cc_name $cc_wanted from $cc_packages/$cc_name-$cc_version-$cc_rid"
  companion_c_cmake || return 1
}

# CMake's find_package, with the reported directory on its prefix path as run.sh puts it: the
# package it chooses must be that archive's, and every library it imports one of the archive's
# recorded files (companion_origin.py's _CMAKE_PROBE, byte for byte; data as cache variables).
companion_c_cmake() {
  cc_binding="C (CMake, $cc_rid)"
  cc_archive=$(cd "$cc_packages/$cc_name-$cc_version-$cc_rid" && pwd -P)
  if ! command -v cmake >/dev/null 2>&1; then
    echo "::error::$cc_binding: found no cmake on PATH to ask which package find_package chooses, expected $cc_expected"
    return 1
  fi
  cc_probe=$(mktemp -d)
  cat > "$cc_probe/CMakeLists.txt" <<'COMPANION_CMAKE_PROBE'
cmake_minimum_required(VERSION 3.16)
project(companion_probe C)
find_package(${PROBE_NAME} ${PROBE_VERSION} CONFIG REQUIRED)
message(STATUS "companion-probe dir=${${PROBE_NAME}_DIR}")
foreach(_target ${PROBE_NAME}::${PROBE_NAME} ${PROBE_NAME}::${PROBE_NAME}_static)
  if(TARGET ${_target})
    foreach(_property IMPORTED_LOCATION IMPORTED_IMPLIB)
      get_target_property(_value ${_target} ${_property})
      if(_value)
        message(STATUS "companion-probe ${_target} ${_property}=${_value}")
      endif()
    endforeach()
  endif()
endforeach()
COMPANION_CMAKE_PROBE
  cc_code=0
  cc_out=$(cmake -S "$cc_probe" -B "$cc_probe/build" "-DPROBE_NAME=$cc_name" "-DPROBE_VERSION=$cc_version" \
    "-DCMAKE_PREFIX_PATH=$cc_reported" 2>&1) || cc_code=$?
  rm -rf "$cc_probe"
  if [ "$cc_code" != 0 ]; then
    printf '%s\n' "$cc_out" | tail -n 20
    echo "::error::$cc_binding: found find_package($cc_name $cc_version) failing (exit $cc_code), expected $cc_expected"
    return 1
  fi
  cc_chosen=$(printf '%s\n' "$cc_out" | sed -n 's/.*companion-probe dir=//p' | head -n 1)
  case "$(cd "$cc_chosen" 2>/dev/null && pwd -P)/" in
    "$cc_archive/"*) ;;
    *)
      echo "::error::$cc_binding: found find_package($cc_name $cc_version) choosing ${cc_chosen:-nothing}, expected $cc_expected"
      return 1
      ;;
  esac
  cc_imports=$(printf '%s\n' "$cc_out" | sed -n 's/.*companion-probe [^ ]* [A-Z_]*=//p')
  if [ -z "$cc_imports" ]; then
    echo "::error::$cc_binding: found find_package($cc_name $cc_version) importing no library, expected $cc_expected"
    return 1
  fi
  for cc_import in $cc_imports; do
    cc_hash=$(sha256sum 2>/dev/null < "$cc_import" | cut -d' ' -f1)
    case "$(cd "$(dirname "$cc_import")" 2>/dev/null && pwd -P)/" in
      "$cc_archive/"*) ;;
      *) cc_hash= ;;
    esac
    if [ -z "$cc_hash" ] || ! grep -q "^$cc_hash  $cc_name-$cc_version-$cc_rid/" "$cc_root/.companions/$cc_name-c.sums"; then
      echo "::error::$cc_binding: found an import of $cc_import, expected $cc_expected"
      return 1
    fi
  done
  echo "CMake ($cc_rid): find_package($cc_name $cc_version) chose $cc_chosen, importing $(printf '%s\n' "$cc_imports" | tr '\n' ' ')"
}
