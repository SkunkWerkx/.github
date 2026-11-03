"""Each ecosystem's override, written into the leg's own tree from .companions/env (the
companion-pins spec, "The mapping"): the step fetch-companions runs once per ecosystem, before
companion_origin.py proves the override took.

  companion_wire.py <eco> --root <checkout>

wire_<eco>(root, env) does one ecosystem; `env` is .companions/env as a dict (read_env), whose
paths are relative to root. A wiring that can see it didn't take (a lock still naming the
registry's copy, a go.mod without the replace) fails here; what only the tool can tell is
companion_origin's.

  rust  Every requirement on <name> in the tree's manifests rewritten to `=<NAME>_VERSION`, the
        pin's exact version (the consumer's manifests name the next release, the pin the last);
        `[patch.crates-io] <name> = { path = "<root>/<RUST_REQ>" }` in $CARGO_HOME/config.toml
        (~/.cargo without it); then `cargo update -p <name>` in every workspace whose
        Cargo.lock holds the companion, so later --locked calls see a consistent lock.
  go    `go mod edit -replace <module>=<root>/<GO_REQ>` in every go.mod requiring the module,
        named by the pinned source's own go.mod. A local replace needs no go.sum line.
  python
        Every requirement on <name> in the tree's pyproject.toml files rewritten to
        `<name>==<NAME>_VERSION` (extras and markers kept), then the pinned file
        (<NAME>_PYTHON_REQ, the leg's wheels and sdist) pip chooses for the interpreter
        installed by its path, so PEP 610's direct_url.json records it and its hash. The
        interpreter is --python, else $HYPER_COMPANION_PYTHON, else this one; `none` rewrites
        only, for a leg whose interpreters are containers or venvs that each wire themselves.
  pyodide
        The same rewrite, and with --deps the pinned Pyodide wheel (<NAME>_PYODIDE_REQ) copied
        into pyodide-suite's dependency_wheels directory, which the suite installs first.
  npm   The pinned package (<NAME>_NPM_REQ) packed into .companions/<name>-npm/ with a job-local
        `files` list adding the leg's node-* artifacts (the committed whitelist excludes every
        addon), its package.json restored; each consumer's direct dependency on it rewritten to
        `file:<that tarball>` and its peer to the pin's exact version (no `overrides`: npm
        refuses one for a direct dependency), then `npm install --package-lock-only`. The WASI
        package, where a consumer names it, is packed by npm_package.pack_wasi; on a leg staging
        every addon (the consumer tests) the release's whole set by npm_package.pack, for the
        job's own registry, named to later steps as <NAME>_TARBALLS through $GITHUB_ENV.
  ruby  Every gemspec requirement on <name> rewritten to `= <NAME>_VERSION`, `gem '<name>', path:
        '<root>/<RUBY_REQ>'` appended to each Gemfile beside such a gemspec that takes it
        (`gemspec`), and the leg's binaries, each checked against SHA256SUMS, placed in the pinned
        gem where it loads them: each ruby-native-<rid>-<abi> extension in lib/<name>/<abi>/, the
        native-<rid> library in lib/<name>/native/<rid>/ for Fiddle, each ruby-wasm-<abi> archive
        in ext/<name>_native/<abi>/ (where the pin's `rake wasm:gem[<abi>]` packs it from), and
        then the smoke Gemfile (the one reading WASM_GEM_DIR) also takes `<name>-wasm` by path
        from .companions/<name>-wasm/<name>-wasm-<version>/, which suite-ruby-wasm.sh fills. A
        Gemfile already naming the gem is refused. No tool runs: Bundler resolves the path when
        the job installs.
  php   The leg's one pinned PIE zip (<NAME>_PHP_REQ), checked against SHA256SUMS, its one
        extension extracted into .companions/<name>-php/, and `<NAME>_PHP_EXTENSION` (what the
        consumer's suite loads) with `COMPOSER_IGNORE_PLATFORM_REQ=ext-<name>` (its manifests
        require the next release) appended to $GITHUB_ENV, printed without it. A
        `<NAME>_PHP_EXTENSION` already naming another build is refused.
  java  The leg's native-<rid> and jni-<rid> libraries, each checked against SHA256SUMS, placed
        in the pinned source's src/main/resources/native/<rid>/ (where its Gradle packs them),
        then its `publishToMavenLocal` into the job-local .companions/m2/ (with
        companion_java_publish.gradle, which makes signing optional: no job holds the key), the
        jar read back (its native/ entries exactly the staged files) and recorded in
        .companions/<name>-java.record; then companion_java.init.gradle, copied unchanged to
        $GRADLE_USER_HOME/init.d/companions.gradle (~/.gradle without it) with its data beside it
        in companions.properties (never Groovy source): that repository, and
        no other, for the module, its version forced, and the `companionOrigin` task every Test
        and JavaExec task depends on. The consumer's build files are left as committed.
  csharp
        The leg's native-<rid> libraries, the static-libs artifact's csharp/<Project>/staticlibs/
        tree and the browser-wasm archive, each checked against SHA256SUMS, laid into the pinned
        package project as HyperCast's pack job lays them (runtimes/<rid>/native/, staticlibs/,
        runtimes/browser-wasm/nativeassets/net11.0/); `dotnet pack` into the job-local
        .companions/nuget/, the package read back (its runtimes/ and staticlibs/ exactly those
        files) and recorded in .companions/<name>-csharp.record; every PackageReference and
        PackageVersion of it, and every `<{Project}Version>` property, in the tree's MSBuild files
        rewritten to `[<NAME>_VERSION]`, and nuget.config at the root mapping the package to
        that feed alone (nuget.org serves every other). A NuGet config of the consumer's own is
        refused: it would merge with this one. Later steps restore into the job-local global
        packages folder .companions/nuget-packages/ (NUGET_PACKAGES, through $GITHUB_ENV), so no
        shared folder's copy of the package (nuget.org's, at the pinned version) is taken instead.
  swift The leg's swift-release-assets zips (the artifact bundle and the XCFramework the pin's
        hyper.json names), each checked against SHA256SUMS and then extracted over the pin's own
        trees (a stale bundle its checkout holds is removed), their files' hashes recorded in
        .companions/<name>-swift.sums; then every `.package(...)` requirement on <name> in the
        tree's manifests rewritten to `.package(path: "<relative path to the pin>")`
        (<NAME>_SWIFT_REQ, the pin's root package, which links that bundle and XCFramework as its
        own). A path in the manifest is what every SwiftPM client reads, xcodebuild and a
        container's copy included; `swift package edit` is not (it needs the remote resolvable
        first, lives in one .build, and xcodebuild ignores it). A registry (`id:`) or other-path
        requirement is refused.
  c     The leg's C archives (<NAME>_C_REQ, c-packages-ci), each checked against SHA256SUMS,
        unpacked into .companions/<name>-c/ (hashes in .companions/<name>-c.sums), every Cargo
        requirement on <name> set to `=<NAME>_VERSION` (c/smoke/<name>.sh asks for the version
        rust/Cargo.toml requires), and `<NAME>_PACKAGES`, which that script reads in place of the
        GitHub Release download, appended to $GITHUB_ENV (printed without it). A
        `<NAME>_PACKAGES` already naming another directory is refused.
  r     The pin's R package (<NAME>_R_REQ) staged by its own tools/vendor.sh --stage, its
        DESCRIPTION stamped `Hyper-Pin: <NAME>_COMMIT`, and `R CMD INSTALL`ed into the job's
        library (R_LIBS_USER, else R's first), where setup-r-dependencies finds it satisfied;
        every versioned requirement on <name> in the tree's DESCRIPTION files relaxed to
        `(>= <the pin's version>)`; every Cargo requirement on the crate set to
        `=<NAME>_VERSION`, and `<NAME>_PATH`, which the consumer's r/tools/vendor.sh vendors in
        place of crates.io's crate, at the pin's rust/, appended to $GITHUB_ENV (printed without
        it). Run again with another R_LIBS_USER (the floor R's), it installs the same tree there.
        A `<NAME>_PATH` already naming another crate is refused.

A container that builds afresh (the musl builds and suites) gets the same override from
companion_container.sh, with its own paths, and proves its origin there.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from companion_fetch import ENV, SKIP_DIRS, WORKDIR, _go_module, _pep503, find_bash  # noqa: E402


def read_env(root):
    """.companions/env as a dict."""
    path = Path(root) / WORKDIR / ENV
    if not path.is_file():
        raise ValueError(f'no {WORKDIR}/{ENV} under {root}: fetch-companions writes it before wiring')
    env = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        key, sep, value = line.partition('=')
        if line.strip() and not sep:
            raise ValueError(f'{path}: {line!r} is not KEY=value')
        if sep:
            env[key] = value
    return env


def requirement(root, env, eco):
    """(companion name, absolute path) from the env's one `<NAME>_<ECO>_REQ`."""
    suffix = f'_{eco.upper()}_REQ'
    found = [(k[:-len(suffix)], v) for k, v in env.items() if k.endswith(suffix)]
    if len(found) != 1:
        raise ValueError(f'{WORKDIR}/{ENV} has {len(found)} *{suffix} lines; fetch-companions writes one for each '
                         f'ecosystem the leg wires')
    prefix, relative = found[0]
    return prefix.lower(), (Path(root).resolve() / relative)


def _walk_dirs(root):
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        yield Path(directory), set(files)


def _locked(lock, name):
    """The lock's packages named `name`."""
    import tomllib
    try:
        data = tomllib.loads(lock.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f'{lock} cannot be read as a Cargo lock: {error}') from None
    return [p for p in data.get('package') or [] if isinstance(p, dict) and p.get('name') == name]


def cargo_workspaces(root, name):
    """Every directory under root, outside SKIP_DIRS (the pinned source and build trees among
    them), holding a Cargo.toml and a Cargo.lock that locks `name`."""
    return [d for d, files in _walk_dirs(Path(root).resolve())
            if {'Cargo.toml', 'Cargo.lock'} <= files and _locked(d / 'Cargo.lock', name)]


def cargo_home(environ=None, home=None):
    environ = os.environ if environ is None else environ
    if environ.get('CARGO_HOME'):
        return Path(environ['CARGO_HOME'])
    return (Path.home() if home is None else Path(home)) / '.cargo'


def _patch_config(config, name, path):
    """Adds `[patch.crates-io] name = { path }` to config, keeping what is there. One already
    present for the same path is left; any other patch of crates-io is refused, since two
    tables can't both hold and an existing patch would decide what this leg tests."""
    import tomllib
    text = config.read_text(encoding='utf-8') if config.is_file() else ''
    try:
        existing = (tomllib.loads(text).get('patch') or {}).get('crates-io')
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f'{config} cannot be parsed as TOML ({error}), so the patch cannot be added') from None
    wanted = {name: {'path': path}}
    if existing == wanted:
        return
    if existing is not None:
        raise ValueError(f'{config} already has a [patch.crates-io] table ({json.dumps(existing, sort_keys=True)}); '
                         f'the companion needs it to hold only {name} = {{ path = "{path}" }}')
    config.parent.mkdir(parents=True, exist_ok=True)
    separator = '' if not text or text.endswith('\n') else '\n'
    config.write_text(f'{text}{separator}\n# fetch-companions: the pinned {name} (companion-pins)\n'
                      f'[patch.crates-io]\n{name} = {{ path = {json.dumps(path)} }}\n', encoding='utf-8')


def _dependency_entries(data, name):
    """(table path, key, value) for every dependency on crate `name` in a parsed manifest: the
    [dependencies], [dev-dependencies] and [build-dependencies] tables, their target-specific
    forms, and [workspace.dependencies]; a key named for the crate or renamed with `package`."""
    tables = []
    for kind in ('dependencies', 'dev-dependencies', 'build-dependencies'):
        tables.append(((kind,), data.get(kind) or {}))
        for target, body in (data.get('target') or {}).items():
            tables.append((('target', target, kind), (body or {}).get(kind) or {}))
    tables.append((('workspace', 'dependencies'), (data.get('workspace') or {}).get('dependencies') or {}))
    for path, table in tables:
        for key, value in table.items():
            package = value.get('package', key) if isinstance(value, dict) else key
            if package == name:
                yield path, key, value


def _header_path(header):
    """A `[table.header]`'s key path, as TOML reads it (quoted keys and spaces included)."""
    import tomllib
    try:
        data = tomllib.loads(f'[{header}]\n')
    except tomllib.TOMLDecodeError:
        return None
    path = []
    while isinstance(data, dict) and data:
        key = next(iter(data))
        path.append(key)
        data = data[key]
    return tuple(path)


def _with_requirement(data, entries, wanted):
    """`data` with every companion entry's requirement set to `wanted`, and nothing else changed:
    what the rewritten manifest must parse to."""
    import copy
    data = copy.deepcopy(data)
    for path, key, value in entries:
        table = data
        for part in path:
            table = table[part]
        if isinstance(value, str):
            table[key] = wanted
        elif isinstance(value, dict) and not (value.get('workspace') and 'version' not in value):
            table[key]['version'] = wanted
    return data


def _pin_requirement(manifest, name, version):
    """Rewrites every requirement on `name` in the manifest to `=version`, text-wise (comments,
    layout and line endings kept), in the string form (`name = "1"`), the inline-table form
    (`name = { version = "1", ... }`, renamed with `package` too), the dotted form
    (`name.version = "1"`) and the table form (`[dependencies.name]` with its `version =` line).
    An entry that only inherits from the workspace (`workspace = true`) is left to
    [workspace.dependencies]; a path or git requirement on the companion is refused, with a
    version or without (Cargo builds it from there and never patches it). Returns whether the
    file changed. The result is re-parsed: every entry must then say `=version`, and the whole
    manifest must equal the original with only those requirements changed."""
    import tomllib
    text = manifest.read_bytes().decode('utf-8')
    original = tomllib.loads(text)
    entries = list(_dependency_entries(original, name))
    if not entries:
        return False
    for path, key, value in entries:
        if isinstance(value, dict) and not value.get('workspace') and ({'path', 'git'} & set(value)):
            raise ValueError(f'{manifest} takes {name} from a {"path" if "path" in value else "git"} source; '
                             'the companion is patched over crates.io, so its requirement must be a version '
                             'alone, and Cargo never patches a path or git dependency')
    wanted = f'={version}'
    targets = {(path, key) for path, key, _ in entries}
    key_re = r'(?:"[^"]*"|\'[^\']*\'|[A-Za-z0-9_-]+)'
    header = re.compile(r'^\s*\[\s*([^\[\]]+?)\s*\]\s*(?:#.*)?$')
    table, out, in_string = None, [], None
    for line in text.splitlines(keepends=True):
        if in_string:
            # Inside a multi-line string: nothing here is a header or a requirement.
            if line.count(in_string) % 2:
                in_string = None
            out.append(line)
            continue
        match = header.match(line)
        if match and not line.lstrip().startswith('[['):
            table = _header_path(match.group(1))
            out.append(line)
            continue
        table_path = table or ()
        if table_path[:-1] and (table_path[:-1], table_path[-1]) in targets:
            # [dependencies.name] (or a renamed key whose table names `package`): its version line.
            line = re.sub(r'^(\s*version\s*=\s*)(["\'])[^"\']*\2', lambda m: f'{m.group(1)}"{wanted}"', line)
        else:
            m = re.match(rf'^(\s*)({key_re})(\s*(?:\.\s*version\s*)?=\s*)(.*)$', line, re.S)
            if m and (table_path, m.group(2).strip('"\'')) in targets:
                rest = m.group(4)
                if rest.lstrip().startswith(('"', "'")):
                    rest = re.sub(r'^(\s*)(["\'])[^"\']*\2', lambda r: f'{r.group(1)}"{wanted}"', rest)
                elif rest.lstrip().startswith('{'):
                    rest = re.sub(r'(\bversion\s*=\s*)(["\'])[^"\']*\2', lambda r: f'{r.group(1)}"{wanted}"', rest, count=1)
                line = m.group(1) + m.group(2) + m.group(3) + rest
        for delimiter in ('"""', "'''"):
            if line.count(delimiter) % 2:
                in_string = delimiter
                break
        out.append(line)
    new = ''.join(out)
    rewritten = tomllib.loads(new)
    for path, key, value in _dependency_entries(rewritten, name):
        given = value if isinstance(value, str) else value.get('version') if isinstance(value, dict) else None
        if isinstance(value, dict) and value.get('workspace') and given is None:
            continue
        if given != wanted:
            raise ValueError(f'{manifest}: [{".".join(path)}] {key} still requires {given!r}, not {wanted!r}; '
                             'its form is one the rewrite does not know')
    if rewritten != _with_requirement(original, entries, wanted):
        raise ValueError(f'{manifest}: the rewrite changes more than the {name} requirements (a line inside a '
                         'string, or another crate under the same key); its layout is one the rewrite does not know')
    if new != text:
        manifest.write_bytes(new.encode('utf-8'))
        return True
    return False


def wire_rust(root, env, run=subprocess.run, environ=None, home=None):
    name, pin = requirement(root, env, 'rust')
    if not (pin / 'Cargo.toml').is_file():
        raise ValueError(f'the pinned crate has no {pin.relative_to(Path(root).resolve()).as_posix()}/Cargo.toml')
    workspaces = cargo_workspaces(root, name)
    if not workspaces:
        raise ValueError(f'no Cargo.lock under {root} locks {name}: nothing for the patch to reach')
    version = env.get(f'{name.upper()}_VERSION')
    if not version:
        raise ValueError(f'{WORKDIR}/{ENV} has no {name.upper()}_VERSION, the pinned crate\'s version')
    root = Path(root).resolve()
    # The consumer's manifests name the version its code needs (the next release); the pin
    # still carries the last one. In this job's tree only, each requirement becomes the pin's
    # exact version, which the patch then meets (the spec, "Manifests keep requiring what the
    # code needs").
    for directory, files in _walk_dirs(root):
        if 'Cargo.toml' in files and _pin_requirement(directory / 'Cargo.toml', name, version):
            print(f'{(directory / "Cargo.toml").relative_to(root).as_posix()}: {name} = "={version}"')
    config = cargo_home(environ, home) / 'config.toml'
    _patch_config(config, name, pin.as_posix())
    print(f'{config}: [patch.crates-io] {name} = {{ path = "{pin.as_posix()}" }}')
    for workspace in workspaces:
        run(['cargo', 'update', '-p', name, '--manifest-path', str(workspace / 'Cargo.toml')], check=True)
        lock = workspace / 'Cargo.lock'
        sources = sorted({p['source'] for p in _locked(lock, name) if p.get('source')})
        if sources:
            raise ValueError(f'{lock.relative_to(root).as_posix()} still takes {name} from {", ".join(sources)}, '
                             f'not {pin.as_posix()}: the patch was not used')


def _strip_go_comments(text):
    return re.sub(r'//[^\n]*', '', text)


def go_modules(root, module):
    """Every directory under root, outside SKIP_DIRS, whose go.mod requires `module`."""
    requires = re.compile(r'(?m)^\s*(?:require\s+)?' + re.escape(module) + r'\s+v\S+\s*$')
    return [d for d, files in _walk_dirs(Path(root).resolve())
            if 'go.mod' in files and requires.search(_strip_go_comments((d / 'go.mod').read_text(encoding='utf-8')))]


def _replaced(go_mod, module):
    """The replacement go.mod names for `module`, or None."""
    text = _strip_go_comments(go_mod.read_text(encoding='utf-8'))
    single = re.findall(r'(?m)^\s*replace\s+' + re.escape(module) + r'(?:\s+v\S+)?\s*=>\s*(.+?)\s*$', text)
    block = [m for b in re.findall(r'(?ms)^\s*replace\s*\((.*?)^\s*\)', text)
             for m in re.findall(r'(?m)^\s*' + re.escape(module) + r'(?:\s+v\S+)?\s*=>\s*(.+?)\s*$', b)]
    found = single + block
    return found[-1].strip('"') if found else None


def wire_go(root, env, run=subprocess.run):
    _, pin = requirement(root, env, 'go')
    module = _go_module(pin)
    modules = go_modules(root, module)
    if not modules:
        raise ValueError(f'no go.mod requires {module} (the pinned source\'s module) under {root}')
    root = Path(root).resolve()
    for directory in modules:
        run(['go', 'mod', 'edit', f'-replace={module}={pin}'], cwd=directory, check=True)
        replaced = _replaced(directory / 'go.mod', module)
        relative = (directory / 'go.mod').relative_to(root).as_posix()
        if replaced is None or Path(replaced) != pin:
            raise ValueError(f'{relative} replaces {module} with {replaced or "nothing"}, not {pin}: '
                             'the module proxy would decide')
        print(f'{relative}: replace {module} => {pin}')


PYTHON_VARIABLE = 'HYPER_COMPANION_PYTHON'


def pinned_files(root, env, eco):
    """(companion name, [absolute pinned files]) from the env's one `<NAME>_<ECO>_REQ`, a
    comma-separated list of staged artifacts (empty for none)."""
    suffix = f'_{eco.upper()}_REQ'
    found = [(k[:-len(suffix)], v) for k, v in env.items() if k.endswith(suffix)]
    if len(found) != 1:
        raise ValueError(f'{WORKDIR}/{ENV} has {len(found)} *{suffix} lines; fetch-companions writes one for each '
                         f'ecosystem the leg wires')
    prefix, value = found[0]
    root = Path(root).resolve()
    return prefix.lower(), [root / part for part in value.split(',') if part]


# A PEP 508 requirement: its name, extras, version specifiers, then a direct reference or a marker.
_REQUIREMENT = re.compile(r'^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*([^;@]*?)\s*'
                          r'(@[^;]*?)?\s*(;.*)?$', re.S)


def _python_requirements(data):
    """(location, index, requirement) for every requirement string in a parsed pyproject.toml:
    [project] dependencies, each [project.optional-dependencies] list and each
    [dependency-groups] list (an include-group table is not a requirement)."""
    project = data.get('project') or {}
    lists = [(('project', 'dependencies'), project.get('dependencies') or [])]
    lists += [(('project', 'optional-dependencies', k), v) for k, v in (project.get('optional-dependencies') or {}).items()]
    lists += [(('dependency-groups', k), v) for k, v in (data.get('dependency-groups') or {}).items()]
    for location, values in lists:
        for index, value in enumerate(values if isinstance(values, list) else []):
            if isinstance(value, str):
                yield location, index, value


def _set(data, location, index, value):
    table = data
    for part in location:
        table = table[part]
    table[index] = value


def _pin_python_requirement(manifest, name, version):
    """Rewrites every requirement on `name` in a pyproject.toml to `name==version`, its extras
    and marker kept, text-wise (each requirement's quoted literal replaced, so comments and
    layout stay). A direct reference (`name @ url`) is refused. The result must parse to the
    original with only those requirements changed, or nothing is written. Returns whether the
    file changed."""
    import copy
    import tomllib
    text = manifest.read_bytes().decode('utf-8')
    original = tomllib.loads(text)
    wanted = copy.deepcopy(original)
    replacements = {}
    for location, index, value in _python_requirements(original):
        match = _REQUIREMENT.match(value)
        if not match or _pep503(match.group(1)) != _pep503(name):
            continue
        if match.group(4):
            raise ValueError(f'{manifest} requires {name} by a direct reference ({value!r}); the wiring installs the '
                             'pinned file itself and rewrites only a version requirement')
        marker = f' {match.group(5)}' if match.group(5) else ''
        new = f'{match.group(1)}{match.group(2) or ""}=={version}{marker}'
        _set(wanted, location, index, new)
        if new != value:
            replacements[value] = new
    if wanted == original:
        return False
    new_text = text
    for old, new in replacements.items():
        for quote in ('"', "'"):
            new_text = new_text.replace(f'{quote}{old}{quote}', json.dumps(new) if quote == '"' else f"'{new}'")
    if tomllib.loads(new_text) != wanted:
        raise ValueError(f'{manifest}: the rewrite changes more than the {name} requirements (the same text '
                         'outside a dependency list); its layout is one the rewrite does not know')
    manifest.write_bytes(new_text.encode('utf-8'))
    return True


def python_manifests(root, name):
    """Every pyproject.toml under root, outside SKIP_DIRS, with a requirement on `name`."""
    import tomllib
    found = []
    for directory, files in _walk_dirs(Path(root).resolve()):
        if 'pyproject.toml' not in files:
            continue
        path = directory / 'pyproject.toml'
        try:
            data = tomllib.loads(path.read_text(encoding='utf-8'))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f'{path} cannot be parsed as TOML: {error}') from None
        if any((m := _REQUIREMENT.match(v)) and _pep503(m.group(1)) == _pep503(name)
               for _, _, v in _python_requirements(data)):
            found.append(path)
    return found


def _rewrite_python(root, name, version):
    root = Path(root).resolve()
    manifests = python_manifests(root, name)
    if not manifests:
        raise ValueError(f'no pyproject.toml under {root} requires {name}: nothing for the pin to meet')
    for manifest in manifests:
        if _pin_python_requirement(manifest, name, version):
            print(f'{manifest.relative_to(root).as_posix()}: {name}=={version}', flush=True)


def python_command(python=None, environ=None):
    """The interpreter a Python wiring installs into, as a command: `python` (from --python),
    else $HYPER_COMPANION_PYTHON, else this interpreter. `none` means no interpreter on this
    host runs the consumer: its containers and venvs install the pin themselves."""
    import shlex
    environ = os.environ if environ is None else environ
    given = python if python is not None else environ.get(PYTHON_VARIABLE)
    if given is None or given == '':
        return [sys.executable]
    if given == 'none':
        return None
    # An interpreter's own path, taken whole (a Windows path's backslashes, a space); otherwise a
    # command such as `arch -x86_64 <path>`.
    if Path(given).is_file():
        return [given]
    return shlex.split(given)


def _sha256(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _checked(root, env, path):
    """The file, when its bytes are the ones SHA256SUMS lists for it (the stage step's copy of the
    companions job's), else a ValueError naming it."""
    artifacts = Path(root).resolve() / env.get(next((k for k in env if k.endswith('_ARTIFACTS')), ''), '')
    from companion_fetch import SUMS, _sums
    sums = _sums(artifacts / SUMS) if (artifacts / SUMS).is_file() else {}
    relative = path.relative_to(artifacts).as_posix() if path.is_relative_to(artifacts) else str(path)
    digest = _sha256(path)
    if sums.get(relative) != digest:
        raise ValueError(f'{relative} (sha256 {digest}) is not the file SHA256SUMS lists '
                         f'({sums.get(relative) or "absent"}): it changed after staging')
    return path


def _choose_python_file(run, python, name, version, files):
    """The pinned file pip would take for this interpreter: the best-fitting pinned wheel, chosen
    by pip itself from those files alone (no index), else the pinned sdist."""
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname
    wheels = [f for f in files if f.name.endswith('.whl')]
    sdists = [f for f in files if f.name.endswith(('.tar.gz', '.zip'))]
    if wheels:
        argv = python + ['-m', 'pip', 'install', '--disable-pip-version-check', '--dry-run', '--ignore-installed',
                         '--no-deps', '--no-index', '--only-binary=:all:', '--quiet', '--report', '-']
        for directory in dict.fromkeys(str(f.parent) for f in wheels):
            argv += ['--find-links', directory]
        result = run(argv + [f'{name}=={version}'], capture_output=True, text=True)
        if result.returncode == 0:
            try:
                url = json.loads(result.stdout)['install'][0]['download_info']['url']
            except (ValueError, KeyError, IndexError, TypeError):
                raise ValueError(f'pip\'s dry run printed no report: {result.stdout.strip()[-300:]}') from None
            parsed = urlparse(url)
            chosen = Path(url2pathname(unquote(parsed.path))) if parsed.scheme == 'file' else None
            if chosen is None or chosen.resolve() not in {f.resolve() for f in wheels}:
                raise ValueError(f'pip chose {url}, not a pinned file ({", ".join(str(f) for f in wheels)})')
            return chosen
        if 'No matching distribution' not in (result.stderr or ''):
            raise ValueError(f'pip could not choose among the pinned wheels with {" ".join(python)} '
                             f'(exit {result.returncode}): {(result.stderr or "").strip()[-300:]}')
    if sdists:
        return sdists[0]
    raise ValueError(f'no pinned wheel fits {" ".join(python)} and the leg has no pinned sdist '
                     f'({", ".join(f.name for f in files) or "no files"})')


def wire_python(root, env, run=subprocess.run, python=None, environ=None):
    """Every requirement on the companion rewritten to `==<version>` (the consumer's manifests
    name the next release), then the pinned file pip chooses for the interpreter installed by its
    path, so PEP 610's direct_url.json records it and its hash."""
    name, files = pinned_files(root, env, 'python')
    version = env.get(f'{name.upper()}_VERSION')
    if not version:
        raise ValueError(f'{WORKDIR}/{ENV} has no {name.upper()}_VERSION, the pinned package\'s version')
    _rewrite_python(root, name, version)
    command = python_command(python, environ)
    if command is None:
        print(f'{PYTHON_VARIABLE}=none: no interpreter on this host; each container and venv that runs the '
              f'consumer installs {name} {version} from the pin itself')
        return
    if not files:
        raise ValueError(f'the leg stages no pinned Python file ({name.upper()}_PYTHON_REQ is empty)')
    chosen = _checked(root, env, _choose_python_file(run, command, name, version, files))
    run(command + ['-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '--force-reinstall',
                   str(chosen)], check=True)
    print(f'{" ".join(command)}: {name} {version} installed from {chosen}')


def wire_pyodide(root, env, deps=None):
    """The requirement rewritten as for wire_python, and with `deps` (pyodide-suite's
    dependency_wheels directory) the pinned Pyodide wheel copied there, which the suite installs
    before the consumer's wheel. Another wheel of the companion already there is refused: it
    would be installed beside the pin, or instead of it."""
    import shutil
    name, files = pinned_files(root, env, 'pyodide')
    version = env.get(f'{name.upper()}_VERSION')
    if not version:
        raise ValueError(f'{WORKDIR}/{ENV} has no {name.upper()}_VERSION, the pinned package\'s version')
    wheels = [f for f in files if f.name.endswith('.whl')]
    if not wheels:
        raise ValueError(f'no pinned Pyodide wheel ({name.upper()}_PYODIDE_REQ names {len(files)} file(s))')
    for wheel in wheels:
        _checked(root, env, wheel)
    _rewrite_python(root, name, version)
    if deps is None:
        return
    deps = Path(deps)
    names = {w.name for w in wheels}
    others = sorted(p.name for p in deps.glob('*.whl') if _pep503(p.name.split('-', 1)[0]) == _pep503(name))
    if others:
        raise ValueError(f'{deps} already holds {", ".join(others)}: the pinned {", ".join(sorted(names))} must be the '
                         f'only {name} there')
    for wheel in wheels:
        shutil.copyfile(wheel, deps / wheel.name)
        print(f'{deps / wheel.name}: the pinned {name} {version}')


NPM_RECORD = 'wired.json'
# The manifest sections npm installs from (a root's devDependencies included), and the peer
# section, which a package's own install takes from its devDependency on the same name.
NPM_INSTALLED = ('dependencies', 'devDependencies', 'optionalDependencies')
NPM_PEER = 'peerDependencies'


def npm_dir(root, name):
    """Where the wiring packs the companion's npm packages: .companions/<name>-npm/."""
    return Path(root).resolve() / WORKDIR / f'{name}-npm'


def npm_integrity(path):
    """npm's integrity for a file: `sha512-` and the base64 of its SHA-512."""
    import base64
    import hashlib
    return 'sha512-' + base64.b64encode(hashlib.sha512(Path(path).read_bytes()).digest()).decode()


def _npm_run(argv, cwd=None, **kwargs):
    """subprocess.run with npm found the way npm_package.default_runner finds it (npm.cmd on Windows)."""
    import shutil
    return subprocess.run([shutil.which(argv[0]) or argv[0], *argv[1:]], cwd=cwd, **kwargs)


def pinned_npm(root, env):
    """(companion name, pinned package directory, its package.json)."""
    name, source = requirement(root, env, 'npm')
    try:
        manifest = json.loads((source / 'package.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError(f'the pinned package has no readable {source}/package.json: {error}') from None
    return name, source, manifest


def npm_payload(root, env, binary, cfg):
    """The leg's staged node-* artifacts (an addon, or the WASI module and its loaders, or every one
    for the consumer tests): each checked against SHA256SUMS, each named for the binary, as napi's
    loader requires them beside binding.js; a debug module is never shipped (npm_package). Each
    node-<rid> directory is a napi target of the pinned project (cfg.node_platforms, from
    napi.targets, or cfg.node_wasi) and holds the file napi's loader asks for on that platform:
    `<binary>.<platformArchABI>.node` and no other addon, or the WASI module and its loader."""
    root = Path(root).resolve()
    artifacts = root / env.get(next((k for k in env if k.endswith('_ARTIFACTS')), ''), '')
    files = sorted(p for d in sorted(artifacts.glob('node-*')) if d.is_dir()
                   for p in sorted(d.iterdir()) if p.is_file() and not p.name.endswith('.debug.wasm'))
    names = [p.name for p in files]
    for path in files:
        if not path.name.startswith(f'{binary}.'):
            raise ValueError(f'{path.relative_to(root).as_posix()} is not one of the binary {binary}\'s files, which '
                             'napi\'s loader looks for beside binding.js')
        _checked(root, env, path)
    for directory in sorted({p.parent for p in files}):
        rid = directory.name[len('node-'):]
        held = sorted(p.name for p in files if p.parent == directory)
        if cfg.node_wasi and rid == cfg.node_wasi:
            wanted = [f'{binary}.{rid}.wasm', f'{binary}.{rid.split("-", 1)[-1]}.cjs']
        elif rid in cfg.node_platforms:
            wanted = [f'{binary}.{cfg.node_platforms[rid]}.node']
        else:
            raise ValueError(f'{directory.name} is no napi target of the pinned project (node_platforms: '
                             f'{", ".join(sorted(cfg.node_platforms))}; wasi: {cfg.node_wasi or "none"})')
        for name in wanted:
            if name not in held:
                raise ValueError(f'{directory.name} has no {name}, the file napi\'s loader on {rid} asks for '
                                 f'(it holds {", ".join(held)})')
        stray = [n for n in held if n.endswith('.node') and n not in wanted]
        if stray:
            raise ValueError(f'{directory.name} holds {", ".join(stray)} beside {wanted[0]}: napi\'s loader on {rid} '
                             'loads only the one')
    clashes = sorted({n for n in names if names.count(n) > 1})
    if clashes:
        raise ValueError(f'two staged artifacts carry {", ".join(clashes)}')
    return files


def _npm_manifests(root, packages):
    """(package.json, {(section, dependency): spec}) for every manifest under root, outside
    SKIP_DIRS, naming one of `packages` in an installed or the peer section."""
    found = []
    for directory, files in _walk_dirs(Path(root).resolve()):
        if 'package.json' not in files:
            continue
        path = directory / 'package.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except ValueError as error:
            raise ValueError(f'{path} cannot be parsed as JSON: {error}') from None
        entries = {(section, dep): spec for section in NPM_INSTALLED + (NPM_PEER,)
                   for dep, spec in (data.get(section) or {}).items() if dep in packages}
        if entries:
            found.append((path, data, entries))
    return found


def _pack_with_payload(source, manifest, payload, out, run):
    """`npm pack` of the pinned package with a job-local `files` list that adds the payload, which
    the committed whitelist leaves out; the package.json is restored byte for byte and the
    payload's copies removed whatever happens. The tarball, and npm's integrity for it."""
    import shutil
    manifest_path = source / 'package.json'
    original = manifest_path.read_bytes()
    names = [p.name for p in payload]
    clashes = [n for n in names if (source / n).exists()]
    if clashes:
        raise ValueError(f'the pinned checkout already has {", ".join(clashes)} beside package.json')
    local = dict(manifest)
    if 'files' in local:
        local['files'] = list(local['files']) + names
    copied = []
    try:
        for path in payload:
            shutil.copyfile(path, source / path.name)
            copied.append(source / path.name)
        manifest_path.write_text(json.dumps(local, indent=2) + '\n', encoding='utf-8')
        out.mkdir(parents=True, exist_ok=True)
        result = run(['npm', 'pack', '--json', '--ignore-scripts', '--pack-destination', str(out)], cwd=source,
                     capture_output=True, text=True, check=True)
    finally:
        manifest_path.write_bytes(original)
        for path in copied:
            path.unlink(missing_ok=True)
    try:
        report = json.loads(result.stdout)[0]
        tarball, packed = out / report['filename'], {f['path'] for f in report['files']}
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError(f'npm pack printed no report: {str(result.stdout).strip()[-300:]}') from None
    missing = [n for n in names if n not in packed]
    if missing:
        raise ValueError(f'npm pack left {", ".join(missing)} out of {tarball.name}: the job-local files list did not '
                         'take, and the package would load no addon')
    if report.get('integrity') != npm_integrity(tarball):
        raise ValueError(f'{tarball.name}: npm pack reported integrity {report.get("integrity")}, the file has '
                         f'{npm_integrity(tarball)}')
    return tarball


def _complete_set(cfg, payload):
    """Whether the leg staged every napi addon and the WASI build: the consumer tests' leg."""
    dirs = {p.parent.name for p in payload}
    return bool(cfg.node_wasi) and {f'node-{rid}' for rid in cfg.node_platforms} | {f'node-{cfg.node_wasi}'} <= dirs


def _rewrite_npm(path, data, wanted):
    """Every (section, dependency) in `wanted` set to its spec, text-wise inside that section's
    object, so the rest of the file keeps its bytes. The result must parse to `data` with only
    those specs changed, or nothing is written."""
    import copy
    text = path.read_bytes().decode('utf-8')
    expected = copy.deepcopy(data)
    for (section, dep), spec in wanted.items():
        expected[section][dep] = spec
    new = text
    for (section, dep), spec in wanted.items():
        opening = re.search(r'"' + re.escape(section) + r'"\s*:\s*\{', new)
        end = new.index('}', opening.end()) if opening else -1
        body = new[opening.end():end] if opening else ''
        entry = re.compile(r'("' + re.escape(dep) + r'"\s*:\s*)"(?:[^"\\]|\\.)*"')
        if not opening or len(entry.findall(body)) != 1:
            raise ValueError(f'{path}: "{section}" has no single "{dep}" entry the rewrite can find')
        new = new[:opening.end()] + entry.sub(lambda m: m.group(1) + json.dumps(spec), body) + new[end:]
    if json.loads(new) != expected:
        raise ValueError(f'{path}: the rewrite changes more than the companion\'s entries; its layout is one the '
                         'rewrite does not know')
    if new != text:
        path.write_bytes(new.encode('utf-8'))
    return expected


def _npm_lock_entry(directory, package):
    lock = directory / 'package-lock.json'
    try:
        packages = json.loads(lock.read_text(encoding='utf-8')).get('packages') or {}
    except (OSError, ValueError) as error:
        raise ValueError(f'{lock} cannot be read as an npm lock: {error}') from None
    return packages.get(f'node_modules/{package}')


def wire_npm(root, env, run=_npm_run, environ=None):
    """The pinned package packed with this leg's artifacts (a job-local files list, since the
    committed whitelist excludes every addon), and each consumer's direct dependency on it
    rewritten to `file:<that tarball>`, its peer to the pin's exact version, and its lock
    regenerated (`npm install --package-lock-only`) to resolve the tarball. A consumer naming the
    WASI package gets it packed by npm_package.pack_wasi as a release packs it; on the consumer
    tests' leg, which stages every addon, the whole set the release publishes is packed for the
    job's own registry, and <NAME>_TARBALLS names it to the job's later steps ($GITHUB_ENV)."""
    import npm_package
    environ = os.environ if environ is None else environ
    root = Path(root).resolve()
    name, source, manifest = pinned_npm(root, env)
    version = env.get(f'{name.upper()}_VERSION')
    if not version:
        raise ValueError(f'{WORKDIR}/{ENV} has no {name.upper()}_VERSION, the pinned package\'s version')
    if manifest.get('version') != version:
        raise ValueError(f'the pinned package.json says {manifest.get("version")}, the pin {version}')
    package = manifest['name']
    binary = (manifest.get('napi') or {}).get('binaryName') or name
    import hyper_config
    cfg = hyper_config.load(source.parent)
    payload = npm_payload(root, env, binary, cfg)
    if not payload:
        raise ValueError(f'the leg stages no node-* artifact of {name}: the package would load no addon')
    wasi_package = f'{package}-{cfg.node_wasi}' if cfg.node_wasi else None
    consumers = _npm_manifests(root, {package} | ({wasi_package} if wasi_package else set()))
    if not consumers:
        raise ValueError(f'no package.json under {root} requires {package}: nothing for the pin to meet')
    out = npm_dir(root, name)
    for path, data, entries in consumers:
        for (section, dep), spec in entries.items():
            # A spec this wiring wrote (a re-run) is its own; any other path spec is refused.
            ours = isinstance(spec, str) and spec.startswith(f'file:{out.as_posix()}/')
            if not isinstance(spec, str) or (re.match(r'^(file:|link:|\.{1,2}/|/|~/)', spec) and not ours):
                raise ValueError(f'{path} takes {dep} from {spec}; the wiring points it at the pinned tarball itself, '
                                 'so a committed path requirement is one it would silently replace')
        overrides = json.dumps(data.get('overrides') or {})
        if any(f'"{p}' in overrides for p in (package, wasi_package) if p):
            raise ValueError(f'{path} has an overrides entry for {package}: npm refuses an override of a direct '
                             'dependency (EOVERRIDE); the wiring rewrites the dependency itself')
    out.mkdir(parents=True, exist_ok=True)
    tarballs = {package: (_pack_with_payload(source, manifest, payload, out, run), payload)}
    wasi_files = [p for p in payload if cfg.node_wasi and p.parent.name == f'node-{cfg.node_wasi}']
    record = {'packages': {}}
    if _complete_set(cfg, payload):
        registry = out / 'registry'
        if registry.exists():
            import shutil
            shutil.rmtree(registry)
        packed = npm_package.pack(source, root / env[f'{name.upper()}_ARTIFACTS'], registry, cfg)
        record['registry'] = {}
        for path in packed:
            data = json.loads(_tar_member(path, 'package/package.json'))
            record['registry'][data['name']] = {'tarball': path.relative_to(root).as_posix(),
                                                'integrity': npm_integrity(path)}
            if data['name'] == wasi_package:
                tarballs[wasi_package] = (path, wasi_files)
        if environ.get('GITHUB_ENV'):
            with open(environ['GITHUB_ENV'], 'a', encoding='utf-8') as github_env:
                github_env.write(f'{name.upper()}_TARBALLS={registry}\n')
        print(f'{name.upper()}_TARBALLS={registry}: the {len(packed)} packages the release publishes, for the job\'s '
              'own registry', flush=True)
    elif wasi_files and any(dep == wasi_package and section != NPM_PEER
                            for _, _, entries in consumers for section, dep in entries):
        tarballs[wasi_package] = (npm_package.pack_wasi(source, wasi_files[0].parent, out, cfg), wasi_files)
    artifacts = root / env[f'{name.upper()}_ARTIFACTS']
    for dep, (tarball, files) in tarballs.items():
        record['packages'][dep] = {'tarball': tarball.relative_to(root).as_posix(), 'integrity': npm_integrity(tarball),
                                   'payload': [p.relative_to(artifacts).as_posix() for p in files]}
        print(f'{tarball.relative_to(root).as_posix()}: {dep} {version} with {", ".join(p.name for p in files)}',
              flush=True)
    for path, data, entries in consumers:
        relative = path.relative_to(root).as_posix()
        missing = sorted({dep for (section, dep) in entries if section != NPM_PEER and dep not in tarballs})
        if missing:
            print(f'::notice::{relative} requires {", ".join(missing)}, which this leg stages no artifact for: left '
                  'as committed, and this leg installs nothing there', flush=True)
            continue
        wanted = {(section, dep): version if section == NPM_PEER else f'file:{tarballs[dep][0].as_posix()}'
                  for (section, dep) in entries}
        _rewrite_npm(path, data, wanted)
        for (section, dep), spec in sorted(wanted.items()):
            print(f'{relative}: {section} {dep} = {spec}', flush=True)
        run(['npm', 'install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=path.parent,
            check=True)
        for dep in sorted({dep for (section, dep) in entries if section != NPM_PEER}):
            entry = _npm_lock_entry(path.parent, dep) or {}
            resolved = str(entry.get('resolved', ''))
            target = (path.parent / resolved[len('file:'):]).resolve() if resolved.startswith('file:') else None
            if target != tarballs[dep][0].resolve() or entry.get('integrity') != npm_integrity(tarballs[dep][0]):
                raise ValueError(f'{path.parent.relative_to(root).as_posix()}/package-lock.json resolves {dep} from '
                                 f'{resolved or "nothing"} ({entry.get("integrity")}), not the pinned '
                                 f'{tarballs[dep][0].relative_to(root).as_posix()}: the rewrite was not used')
    (out / NPM_RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')


# A gemspec's requirement on a gem: the call, the quoted name, then any number of quoted
# requirement strings, an optional closing parenthesis and a comment. Anything else naming the
# gem (a constant, a call over several lines) is a form the rewrite refuses rather than guesses.
_GEMSPEC_DEPENDENCY = r'add_(?:runtime_|development_)?dependency\b'


def _gemspec_line(name):
    return re.compile(r'^(?P<call>\s*\w+\.' + _GEMSPEC_DEPENDENCY + r')(?P<open>\s*\(?\s*)(?P<name>(["\'])'
                      + re.escape(name) + r'\4)(?P<reqs>(?:\s*,\s*(["\'])[^"\'\n]*\6)*)(?P<close>\s*\)?)'
                      r'(?P<rest>\s*(?:#.*)?)$')


def _pin_gemspec(text, path, name, version):
    """The gemspec's text with every requirement on `name` made `= <version>`, and how many there
    were; a line naming the gem in a form the rewrite does not know is a ValueError."""
    line_form = _gemspec_line(name)
    mention = re.compile(_GEMSPEC_DEPENDENCY + r'\s*\(?\s*["\']' + re.escape(name) + r'["\']')
    lines, count = [], 0
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        body = line.rstrip('\r\n')
        if not mention.search(body):
            lines.append(line)
            continue
        match = line_form.match(body)
        if not match:
            raise ValueError(f'{path}:{number}: {body.strip()!r} requires {name} in a form the wiring cannot rewrite '
                             f'to "= {version}"')
        count += 1
        lines.append(f'{match["call"]}{match["open"]}{match["name"]}, "= {version}"{match["close"]}{match["rest"]}'
                     + line[len(body):])
    return ''.join(lines), count


def _ruby_quote(text):
    return "'" + text.replace('\\', '\\\\').replace("'", "\\'") + "'"


# The Magnus extension each ruby-native-<rid>-<abi> artifact holds, the shared library a
# native-<rid> artifact holds for the Fiddle backend, and the wasm32-wasip1 archive a
# ruby-wasm-<abi> artifact holds for the <name>-wasm gem.
_RUBY_NATIVE = re.compile(r'ruby-native-(?P<rid>[a-z0-9-]+)-(?P<abi>\d+\.\d+)')
_RUBY_WASM = re.compile(r'ruby-wasm-(?P<abi>\d+\.\d+)')
# The smoke Gemfile hyper-build-wasm.yml hands rbwasm (suite-ruby-wasm.sh's contract with the
# consumer): the one taking the consumer's own wasm gem from WASM_GEM_DIR.
_WASM_GEM_DIR = re.compile(r'ENV\.fetch\(\s*["\']WASM_GEM_DIR["\']')


def ruby_wasm_gem(root, name, version):
    """Where suite-ruby-wasm.sh unpacks the <name>-wasm gem the pin packs (`gem unpack` names the
    directory after the gem), and where the smoke Gemfile takes it from."""
    return Path(root).resolve() / WORKDIR / f'{name}-wasm' / f'{name}-wasm-{version}'


def ruby_wasm_gemfiles(root):
    """The smoke Gemfiles: each Gemfile under root reading WASM_GEM_DIR."""
    return [d / f for d, files in _walk_dirs(Path(root).resolve()) for f in sorted(files & {'Gemfile', 'gems.rb'})
            if _WASM_GEM_DIR.search((d / f).read_text(encoding='utf-8'))]


def ruby_binaries(root, env, name, gem):
    """{staged artifact: where the pinned gem loads it}: each ABI's extension in
    lib/<name>/<abi>/ (where lib/<name>.rb requires it for that Ruby), the Fiddle library in
    lib/<name>/native/<rid>/, and each ABI's wasm32-wasip1 archive in ext/<name>_native/<abi>/
    (where `rake wasm:gem[<abi>]` packs it from). Any other file in those artifacts is a
    ValueError."""
    artifacts = Path(root).resolve() / env.get(f'{name.upper()}_ARTIFACTS', '')
    placed = {}
    if not artifacts.is_dir():
        return placed
    libraries = (f'lib{name}.so', f'lib{name}.dylib', f'{name}.dll')
    for directory in sorted(d for d in artifacts.iterdir() if d.is_dir()):
        native = _RUBY_NATIVE.fullmatch(directory.name)
        wasm = _RUBY_WASM.fullmatch(directory.name)
        if directory.name.startswith('ruby-native-') and not native:
            raise ValueError(f'{directory.name} is not ruby-native-<rid>-<abi>: no Ruby ABI to place it under')
        if directory.name.startswith('ruby-wasm') and not wasm:
            raise ValueError(f'{directory.name} is not ruby-wasm-<abi>: no Ruby ABI to place it under')
        if not native and not wasm and not directory.name.startswith('native-'):
            continue
        for path in sorted(p for p in directory.rglob('*') if p.is_file()):
            if native and path.parent == directory and re.fullmatch(re.escape(name) + r'_native\.(bundle|so|dll)',
                                                                    path.name):
                placed[path] = gem / 'lib' / name / native['abi'] / path.name
            elif wasm and path.parent == directory and path.name == f'{name}_native.a':
                placed[path] = gem / 'ext' / f'{name}_native' / wasm['abi'] / path.name
            elif not native and not wasm and path.parent == directory and path.name in libraries:
                placed[path] = gem / 'lib' / name / 'native' / directory.name[len('native-'):] / path.name
            else:
                raise ValueError(f'{path.relative_to(artifacts).as_posix()} is not a file the pinned {name} gem loads')
    return placed


def _gemspec_statement(statement):
    return statement == 'gemspec' or statement.startswith(('gemspec ', 'gemspec('))


def wire_ruby(root, env):
    """The pinned gem made the one Bundler resolves: every gemspec requirement on it rewritten to
    `= <version>`, `gem '<name>', path: '<pin>'` added to each Gemfile that takes such a gemspec,
    and the leg's binaries, each checked against SHA256SUMS, placed where the gem loads them (each
    ABI's Magnus extension and the Fiddle library). A Gemfile already naming the gem is refused:
    the pin would silently override it, or it the pin.

    On build-ruby-wasm's leg, whose ruby-wasm-<abi> archive is placed where the pin's
    `rake wasm:gem[<abi>]` packs it from, the smoke Gemfile (ruby_wasm_gemfiles, exactly one)
    also takes `<name>-wasm` by path from ruby_wasm_gem, which suite-ruby-wasm.sh fills; one
    already naming that gem is refused the same way."""
    import shutil
    from companion_fetch import _ruby_statements
    name, gem = requirement(root, env, 'ruby')
    version = env.get(f'{name.upper()}_VERSION')
    if not version:
        raise ValueError(f'{WORKDIR}/{ENV} has no {name.upper()}_VERSION, the pinned gem\'s version')
    root = Path(root).resolve()
    gem_call = re.compile(r'gem\s*\(?\s*["\']' + re.escape(name) + r'["\']')
    block = (f'\n# The pinned {name} (fetch-companions, {name.upper()}_COMMIT in {WORKDIR}/{ENV}).\n'
             f'gem {_ruby_quote(name)}, path: {_ruby_quote(gem.as_posix())}\n')
    gemspecs, gemfiles = {}, {}
    walked = list(_walk_dirs(root))
    for directory, files in walked:
        for file in sorted(f for f in files if f.endswith('.gemspec')):
            path = directory / file
            text, count = _pin_gemspec(path.read_text(encoding='utf-8'), path.relative_to(root).as_posix(), name,
                                       version)
            if count:
                gemspecs[path] = text
    for directory, files in walked:
        for file in sorted(files & {'Gemfile', 'gems.rb'}):
            path = directory / file
            text = path.read_text(encoding='utf-8')
            if text.endswith(block):
                continue
            statements = _ruby_statements(text)
            if any(gem_call.match(s) for s in statements):
                raise ValueError(f'{path.relative_to(root).as_posix()} already names {name}: the pin would override it '
                                 f'unseen')
            if any(map(_gemspec_statement, statements)) and any(p.parent == directory for p in gemspecs):
                gemfiles[path] = text + block
    if not gemspecs:
        raise ValueError(f'no gemspec under {root} requires {name}: nothing for the pin to stand in for')
    if not gemfiles and not any((d / f).read_text(encoding='utf-8').endswith(block)
                                for d, files in walked for f in files & {'Gemfile', 'gems.rb'}):
        raise ValueError(f'no Gemfile under {root} takes a gemspec that requires {name}')
    placed = ruby_binaries(root, env, name, gem)
    if any(_RUBY_WASM.fullmatch(staged.parent.name) for staged in placed):
        wasm_name = f'{name}-wasm'
        wasm_block = (f'\n# The pinned {wasm_name}, packed from the pin around the staged ruby-wasm archive by '
                      f'suite-ruby-wasm.sh\n# (fetch-companions, {name.upper()}_COMMIT in {WORKDIR}/{ENV}).\n'
                      f'gem {_ruby_quote(wasm_name)}, path: {_ruby_quote(ruby_wasm_gem(root, name, version).as_posix())}\n')
        smoke = ruby_wasm_gemfiles(root)
        if len(smoke) != 1:
            raise ValueError(f'{len(smoke)} Gemfiles under {root} read WASM_GEM_DIR, not 1: no one smoke Gemfile for '
                             f'the pinned {wasm_name}')
        text = smoke[0].read_text(encoding='utf-8')
        if not text.endswith(wasm_block):
            wasm_call = re.compile(r'gem\s*\(?\s*["\']' + re.escape(wasm_name) + r'["\']')
            if any(wasm_call.match(s) for s in _ruby_statements(text)):
                raise ValueError(f'{smoke[0].relative_to(root).as_posix()} already names {wasm_name}: the pin would '
                                 f'override it unseen')
            gemfiles[smoke[0]] = text + wasm_block
    for staged, target in placed.items():
        _checked(root, env, staged)
        if target.exists() and target.read_bytes() != staged.read_bytes():
            raise ValueError(f'{target} is already there and is not the staged '
                             f'{staged.parent.name}/{staged.name}: the pin holds another build')
    for path, text in {**gemspecs, **gemfiles}.items():
        path.write_text(text, encoding='utf-8')
        taken = (f'{name}-wasm {version} from {ruby_wasm_gem(root, name, version)}' if _WASM_GEM_DIR.search(text)
                 else f'{name} {version} from {gem}')
        print(f'{path.relative_to(root).as_posix()}: {taken}')
    for staged, target in placed.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staged, target)
        print(f'{target.relative_to(gem).as_posix()}: the staged {staged.parent.name}/{staged.name}')


PHP_IGNORE = 'COMPOSER_IGNORE_PLATFORM_REQ'


def php_extension_entry(zip_path, name):
    """(entry, bytes) of the one extension a PIE zip holds: `<name>.so`, or a Windows
    `php_<name>...dll`."""
    import zipfile
    with zipfile.ZipFile(zip_path) as archive:
        entries = [i for i in archive.infolist() if not i.is_dir()]
        if len(entries) != 1:
            raise ValueError(f'{Path(zip_path).name} holds {len(entries)} files, not the one extension')
        entry = entries[0].filename
        if not (entry == f'{name}.so' or re.fullmatch(r'php_' + re.escape(name) + r'[\w.-]*\.dll', entry)):
            raise ValueError(f'{Path(zip_path).name} holds {entry}, not {name}.so or php_{name}*.dll')
        return entry, archive.read(entries[0])


def php_extension_dir(root, name):
    """Where wire_php extracts the pinned extension: .companions/<name>-php/."""
    return Path(root).resolve() / WORKDIR / f'{name}-php'


def wire_php(root, env, environ=None):
    """The pinned PIE zip (<NAME>_PHP_REQ, the leg's one minor), checked against SHA256SUMS, its
    one extension extracted into .companions/<name>-php/, and later steps told to load it:
    `<NAME>_PHP_EXTENSION` (the variable the consumer's suite loads, and the registry build's
    when no pin is set) and `COMPOSER_IGNORE_PLATFORM_REQ=ext-<name>`, since the consumer's
    manifests require the next release's version, through $GITHUB_ENV (printed without it).
    A `<NAME>_PHP_EXTENSION` already naming another file is refused."""
    environ = os.environ if environ is None else environ
    name, zips = pinned_files(root, env, 'php')
    if len(zips) != 1:
        raise ValueError(f'no pinned PHP extension: {name.upper()}_PHP_REQ names {len(zips)} PIE zips, not one'
                         if zips else f'no pinned PHP extension ({name.upper()}_PHP_REQ is empty)')
    zip_path = _checked(root, env, zips[0])
    entry, data = php_extension_entry(zip_path, name)
    target = php_extension_dir(root, name) / entry
    variable = f'{name.upper()}_PHP_EXTENSION'
    if environ.get(variable) and Path(environ[variable]).resolve() != target.resolve():
        raise ValueError(f'{variable} is already set to {environ[variable]}: the pinned {entry} and that build cannot '
                         f'both be the one loaded')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    lines = f'{variable}={target.as_posix()}\n{PHP_IGNORE}=ext-{name}\n'
    if environ.get('GITHUB_ENV'):
        with open(environ['GITHUB_ENV'], 'a', encoding='utf-8') as github_env:
            github_env.write(lines)
    else:
        print(lines, end='')
    print(f'{target}: the pinned {name} {env.get(f"{name.upper()}_VERSION", "")} from {zip_path.name}')


SCRIPTS = Path(__file__).resolve().parent
JAVA_INIT_TEMPLATE = SCRIPTS / 'companion_java.init.gradle'
JAVA_PUBLISH_SCRIPT = SCRIPTS / 'companion_java_publish.gradle'
JAVA_INIT_SCRIPT = 'companions.gradle'
JAVA_INIT_PROPERTIES = 'companions.properties'


def record_path(root, name, eco):
    """.companions/<name>-<eco>.record: what the wiring published or packed, for the origin check."""
    return Path(root).resolve() / WORKDIR / f'{name}-{eco}.record'


def read_record(path):
    try:
        return dict(line.split('=', 1) for line in Path(path).read_text(encoding='utf-8').splitlines() if line)
    except (OSError, ValueError) as error:
        raise ValueError(f'{path} cannot be read: {error}; the wiring writes it') from None


def _write_record(path, fields):
    Path(path).write_text(''.join(f'{k}={v}\n' for k, v in fields.items()), encoding='utf-8')


def _artifacts_dir(root, env):
    return Path(root).resolve() / env.get(next((k for k in env if k.endswith('_ARTIFACTS')), ''), '')


def _gradle_value(text, pattern, path):
    found = re.findall(pattern, text, re.M)
    if len(found) != 1:
        raise ValueError(f'{path} sets it {len(found)} times, not once ({pattern})')
    return found[0]


def java_coordinate(pin):
    """(group, module) of the pinned Gradle build: its build script's `group = "..."` and its
    settings' `rootProject.name = "..."`, the coordinate its publication carries."""
    build, settings = pin / 'build.gradle.kts', pin / 'settings.gradle.kts'
    if not build.is_file() or not settings.is_file():
        raise ValueError(f'the pinned source has no Gradle build at {pin} (build.gradle.kts and settings.gradle.kts)')
    group = _gradle_value(build.read_text(encoding='utf-8'), r'^group\s*=\s*"([^"]+)"', build)
    module = _gradle_value(settings.read_text(encoding='utf-8'), r'^rootProject\.name\s*=\s*"([^"]+)"', settings)
    return group, module


def java_natives(root, env, name):
    """{jar entry: staged file} for the leg's native-<rid> and jni-<rid> libraries, each checked
    against SHA256SUMS: the core and the JNI shim, packed at native/<rid>/ as the pinned build's
    own staging puts them (java_resources_dir)."""
    artifacts = _artifacts_dir(root, env)
    libraries = {f'lib{name}.so', f'lib{name}.dylib', f'{name}.dll',
                 f'lib{name}_jni.so', f'lib{name}_jni.dylib', f'{name}_jni.dll'}
    found = {}
    for directory in sorted(artifacts.iterdir()) if artifacts.is_dir() else ():
        prefix = next((p for p in ('native-', 'jni-') if directory.name.startswith(p)), None)
        if not prefix or not directory.is_dir():
            continue
        rid = directory.name[len(prefix):]
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(artifacts).as_posix()
            if not path.is_file() or path.name not in libraries:
                raise ValueError(f'{relative} is not one of the libraries the Java binding loads '
                                 f'({", ".join(sorted(libraries))})')
            entry = f'native/{rid}/{path.name}'
            if entry in found:
                raise ValueError(f'two staged artifacts carry {entry}')
            found[entry] = _checked(root, env, path)
    if not any(e.split('/')[-1] in {f'lib{name}.so', f'lib{name}.dylib', f'{name}.dll'} for e in found):
        raise ValueError(f'no native-<rid> core staged for the Java binding (the leg maps native-{{rid}} and '
                         'jni-{rid})')
    return found


def _jar_natives(jar):
    """{entry: sha256} for every file the jar holds under native/."""
    import hashlib
    import zipfile
    with zipfile.ZipFile(jar) as archive:
        return {i.filename: hashlib.sha256(archive.read(i)).hexdigest() for i in archive.infolist()
                if i.filename.startswith('native/') and not i.is_dir()}


def compare_payload(what, held, wanted):
    """None when `held` ({entry: sha256}) is `wanted` exactly, else the first difference."""
    for entry in sorted(set(held) | set(wanted)):
        if entry not in held:
            return f'{what} lacks {entry}'
        if entry not in wanted:
            return f'{what} carries {entry}, which the leg did not stage'
        if held[entry] != wanted[entry]:
            return f'{what} carries {entry} with sha256 {held[entry]}, not the staged {wanted[entry]}'
    return None


def gradle_home(environ=None, home=None):
    environ = os.environ if environ is None else environ
    if environ.get('GRADLE_USER_HOME'):
        return Path(environ['GRADLE_USER_HOME'])
    return (Path.home() if home is None else Path(home)) / '.gradle'


JAVA_PROPERTY_KEYS = ('repository', 'group', 'module', 'version', 'sha256')
JAVA_PROPERTIES_HEADER = "# The pinned companion's data, which companions.gradle beside it reads (companion_wire.py java).\n"


def _property_escape(value):
    """`value` as a java.util.Properties value: ASCII only, every character Properties treats
    specially escaped, the rest as \\uXXXX (UTF-16, as Properties reads it)."""
    out = []
    for char in value:
        if char in '\\=:#! ':
            out.append('\\' + char)
        elif char in '\n\r\t\f':
            out.append({'\n': '\\n', '\r': '\\r', '\t': '\\t', '\f': '\\f'}[char])
        elif ' ' < char <= '~':
            out.append(char)
        else:
            data = char.encode('utf-16-be')
            out.extend(f'\\u{int.from_bytes(data[i:i + 2], "big"):04x}' for i in range(0, len(data), 2))
    return ''.join(out)


def java_properties(repository, group, module, version, sha256):
    """companions.properties for these values: the init script's data, never its source."""
    values = dict(zip(JAVA_PROPERTY_KEYS, (repository, group, module, version, sha256)))
    for key, value in values.items():
        if not value:
            raise ValueError(f'the Gradle init script needs a {key}')
    return JAVA_PROPERTIES_HEADER + ''.join(f'{k}={_property_escape(v)}\n' for k, v in values.items())


def read_java_properties(text):
    """The values of a companions.properties java_properties wrote (one `key=value` per line)."""
    found = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        key, sep, raw = line.partition('=')
        if not sep:
            raise ValueError(f'{line!r} is not key=value')
        units, i = [], 0
        while i < len(raw):
            if raw[i] == '\\' and i + 1 < len(raw):
                nxt = raw[i + 1]
                if nxt == 'u':
                    units.append(int(raw[i + 2:i + 6], 16))
                    i += 6
                    continue
                units.append(ord({'n': '\n', 'r': '\r', 't': '\t', 'f': '\f'}.get(nxt, nxt)))
                i += 2
            else:
                units.append(ord(raw[i]))
                i += 1
        found[key] = b''.join(u.to_bytes(2, 'big') for u in units).decode('utf-16-be')
    return found


def java_repository(root):
    return Path(root).resolve() / WORKDIR / 'm2'


def wire_java(root, env, run=subprocess.run, environ=None, home=None):
    """The leg's natives (each checked against SHA256SUMS) placed in the pinned source's
    src/main/resources/native/<rid>/, where its build packs them; its `publishToMavenLocal` into
    the job-local .companions/m2/ (signing not required there: a job-local repository has no key);
    the jar read back, its native/ entries exactly the staged ones; then the init script, copied as
    it is to $GRADLE_USER_HOME/init.d/companions.gradle (~/.gradle without it), and its data
    beside it in companions.properties: that repository, exclusively,
    for the module, the pin's version forced, and the `companionOrigin` task every Test and JavaExec
    task depends on. The consumer's own build files are left as committed."""
    import hashlib
    import shutil
    environ = os.environ if environ is None else environ
    name, pin = requirement(root, env, 'java')
    root = Path(root).resolve()
    version = env.get(f'{name.upper()}_VERSION', '')
    group, module = java_coordinate(pin)
    repository = java_repository(root)
    natives = java_natives(root, env, name)
    resources = pin / 'src' / 'main' / 'resources' / 'native'
    shutil.rmtree(resources, ignore_errors=True)
    for entry, path in natives.items():
        target = resources.parent / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    shutil.rmtree(repository, ignore_errors=True)
    gradlew = pin / ('gradlew.bat' if os.name == 'nt' else 'gradlew')
    if gradlew.is_file():
        gradlew.chmod(gradlew.stat().st_mode | 0o111)
    run([str(gradlew), '--no-daemon', '--console=plain', '--init-script', str(JAVA_PUBLISH_SCRIPT),
         f'-Dmaven.repo.local={repository.as_posix()}', 'publishToMavenLocal'],
        cwd=pin, check=True, env={**environ, f'{name.upper()}_VERSION': version})
    jar = repository.joinpath(*group.split('.'), module, version, f'{module}-{version}.jar')
    if not jar.is_file():
        raise ValueError(f'publishToMavenLocal left no {jar.name} at {jar.parent}: the pinned build published '
                         'another coordinate or version, and Gradle would resolve the registry\'s')
    difference = compare_payload(jar.name, _jar_natives(jar), {e: _sha256(p) for e, p in natives.items()})
    if difference:
        raise ValueError(f'{difference}: the published jar is not the leg\'s')
    sha256 = hashlib.sha256(jar.read_bytes()).hexdigest()
    _write_record(record_path(root, name, 'java'), {'group': group, 'module': module, 'version': version,
                                                    'jar': jar.relative_to(root).as_posix(), 'sha256': sha256})
    script = gradle_home(environ, home) / 'init.d' / JAVA_INIT_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(JAVA_INIT_TEMPLATE.read_text(encoding='utf-8'), encoding='utf-8')
    (script.parent / JAVA_INIT_PROPERTIES).write_text(
        java_properties(repository.as_posix(), group, module, version, sha256), encoding='latin-1')
    print(f'{script}: {group}:{module}:{version} from {repository} (sha256 {sha256})')


NUGET_ORG = 'https://api.nuget.org/v3/index.json'
NUGET_CONFIG = 'nuget.config'
NUGET_MARKER = '<!-- companion_wire.py csharp: the pinned companion\'s job-local feed. -->'
# HyperCast's pack job puts the browser-wasm archive here (hyper-pack-nuget.yml).
BROWSER_WASM_ASSETS = 'runtimes/browser-wasm/nativeassets/net11.0'
MSBUILD_FILES = ('.csproj', '.fsproj', '.vbproj', '.props', '.targets')
_MSBUILD_ITEM = re.compile(r'<(PackageReference|PackageVersion)\b([^>]*?)(/?)>', re.S)
_MSBUILD_ATTR = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def csharp_package(source):
    """(hyper_config, PackageId, the package project) of the pinned source."""
    import hyper_config
    cfg = hyper_config.load(source.parent)
    project = source / cfg.project / f'{cfg.project}.csproj'
    if not project.is_file():
        raise ValueError(f'the pinned source has no {project}')
    ids = re.findall(r'<PackageId>\s*([^<\s]+)\s*</PackageId>', project.read_text(encoding='utf-8'))
    return cfg, (ids[0] if ids else cfg.project), project


def csharp_entry(relative, cfg):
    """The package entry a staged artifact (its path under the artifacts directory) packs as, or
    None for another binding's."""
    from companion_map import WASM_ARCHIVE
    directory, _, rest = relative.partition('/')
    if directory.startswith('native-') and rest:
        return f'runtimes/{directory[len("native-"):]}/native/{rest}'
    tree = f'static-libs/csharp/{cfg.project}/staticlibs/'
    if relative.startswith(tree) and len(relative) > len(tree):
        return f'staticlibs/{relative[len(tree):]}'
    if directory == WASM_ARCHIVE and rest:
        return f'{BROWSER_WASM_ASSETS}/{rest}'
    return None


def csharp_payload(root, env, cfg):
    """{package entry: staged file}, each checked against SHA256SUMS, laid out as the pack job lays
    them: native-<rid>/ in runtimes/<rid>/native/, the static-libs artifact's C# tree (laid out by
    repository path; its Swift and Go trees are not C#'s) in staticlibs/, the browser-wasm
    archive in runtimes/browser-wasm/nativeassets/net11.0/. Other artifacts are other bindings'."""
    from companion_map import WASM_ARCHIVE
    artifacts = _artifacts_dir(root, env)
    found = {}
    for path in sorted(artifacts.rglob('*')) if artifacts.is_dir() else ():
        entry = csharp_entry(path.relative_to(artifacts).as_posix(), cfg) if path.is_file() else None
        if entry:
            found[entry] = _checked(root, env, path)
    if not found:
        raise ValueError(f'no C# artifact staged (native-<rid>, static-libs\'s csharp/{cfg.project}/staticlibs/ or '
                         f'{WASM_ARCHIVE})')
    return found


def _nuget_configs(root):
    """Every NuGet config under root (outside SKIP_DIRS) but the one this wiring writes."""
    found = []
    for directory, files in _walk_dirs(root):
        for file in sorted(files):
            if file.lower() == NUGET_CONFIG:
                path = directory / file
                if path.parent == root and NUGET_MARKER in path.read_text(encoding='utf-8', errors='replace'):
                    continue
                found.append(path)
    return found


def _rewrite_csharp(path, text, package, prop, version):
    """(new text, requirements found, the versions they named) with every PackageReference or
    PackageVersion of `package` (by Include or Update, case-insensitively) and every `<{prop}>`
    property set to `[version]`, in its Version and its VersionOverride (central management's
    per-project override). A reference through `$({prop})` stays; any other form is refused. An
    Update that names no version (metadata only) requires nothing and stays."""
    count, values = 0, []
    pinned = f'[{version}]'

    def item(match):
        nonlocal count
        attrs = dict((k.lower(), v) for k, v in _MSBUILD_ATTR.findall(match.group(2)))
        named = attrs.get('include', attrs.get('update', ''))
        if named.strip().lower() != package.lower():
            return match.group(0)
        given = [k for k in ('version', 'versionoverride') if k in attrs]
        if not given:
            if not match.group(3):
                raise ValueError(f'{path}: {match.group(0).strip()} may set its version in a child element; write '
                                 'it as a Version or VersionOverride attribute')
            if 'include' not in attrs:
                return match.group(0)
        count += 1
        attributes = match.group(2)
        for key in given:
            value = attrs[key]
            if value.startswith('$('):
                if value == f'$({prop})':
                    continue
                raise ValueError(f'{path}: {match.group(0).strip()} takes its version from {value}, which the wiring '
                                 f'cannot follow (only $({prop}))')
            values.append(value.strip())
            attributes = re.sub(rf'(\b{key}\s*=\s*")[^"]*(")', lambda m: m.group(1) + pinned + m.group(2),
                                attributes, count=1, flags=re.I)
        return f'<{match.group(1)}{attributes}{match.group(3)}>'

    text = _MSBUILD_ITEM.sub(item, text)
    values += [v.strip() for v in re.findall(rf'<{prop}>([^<]*)</{prop}>', text)]
    text = re.sub(rf'(<{prop}>)[^<]*(</{prop}>)', lambda m: m.group(1) + pinned + m.group(2), text)
    return text, count, values


def _nuget_config_text(feed, package):
    from xml.sax.saxutils import quoteattr
    return (f'<?xml version="1.0" encoding="utf-8"?>\n{NUGET_MARKER}\n<configuration>\n  <packageSources>\n'
            f'    <clear />\n    <add key="companions" value={quoteattr(feed)} />\n'
            f'    <add key="nuget.org" value="{NUGET_ORG}" protocolVersion="3" />\n  </packageSources>\n'
            f'  <packageSourceMapping>\n    <packageSource key="companions">\n'
            f'      <package pattern={quoteattr(package)} />\n    </packageSource>\n'
            f'    <packageSource key="nuget.org">\n      <package pattern="*" />\n    </packageSource>\n'
            f'  </packageSourceMapping>\n</configuration>\n')


def nupkg_payload(nupkg):
    """{entry: sha256} for every file the package holds under runtimes/ and staticlibs/."""
    import hashlib
    import zipfile
    from urllib.parse import unquote
    with zipfile.ZipFile(nupkg) as archive:
        return {unquote(i.filename): hashlib.sha256(archive.read(i)).hexdigest() for i in archive.infolist()
                if i.filename.startswith(('runtimes/', 'staticlibs/')) and not i.is_dir()}


def csharp_feed(root):
    return Path(root).resolve() / WORKDIR / 'nuget'


def csharp_packages(root):
    return Path(root).resolve() / WORKDIR / 'nuget-packages'


def wire_csharp(root, env, run=subprocess.run, environ=None):
    """The leg's artifacts (each checked against SHA256SUMS) laid into the pinned package
    project as HyperCast's pack job lays them, `dotnet pack` into the job-local feed
    .companions/nuget/, the package read back (its runtimes/ and staticlibs/ exactly the staged
    files); every requirement on it in the tree's MSBuild files (PackageReference, PackageVersion,
    a `<{Project}Version>` property) rewritten to `[<NAME>_VERSION]`, and nuget.config at the
    root: the feed the only source for the package, nuget.org every other's. A NuGet config of
    the consumer's own is refused: it would merge with this one. Later steps restore into the
    job-local global packages folder .companions/nuget-packages/ (NUGET_PACKAGES, through
    $GITHUB_ENV, printed without it, over any the runner set): NuGet takes a package already in
    the global folder whatever its source, so a shared one holding nuget.org's copy at the pinned
    version (a cache's, a runner's) would win over the feed."""
    environ = os.environ if environ is None else environ
    import base64
    import hashlib
    import shutil
    name, source = requirement(root, env, 'csharp')
    root = Path(root).resolve()
    version = env.get(f'{name.upper()}_VERSION', '')
    cfg, package, project = csharp_package(source)
    configs = _nuget_configs(root)
    if configs:
        raise ValueError(f'{", ".join(p.relative_to(root).as_posix() for p in configs)}: a NuGet config of the '
                         'consumer\'s own would merge with the job-local feed\'s (its <clear/> would drop it)')
    rewrites, requirements = {}, 0
    for directory, files in _walk_dirs(root):
        for file in sorted(files):
            if not file.endswith(MSBUILD_FILES):
                continue
            path = directory / file
            text = path.read_text(encoding='utf-8')
            new, count, _ = _rewrite_csharp(path.relative_to(root).as_posix(), text, package,
                                            f'{cfg.project}Version', version)
            requirements += count
            if new != text:
                rewrites[path] = new
    if not requirements:
        raise ValueError(f'no project under {root} requires {package} (a PackageReference or PackageVersion)')
    payload = csharp_payload(root, env, cfg)
    for directory in ('runtimes', 'staticlibs'):
        shutil.rmtree(project.parent / directory, ignore_errors=True)
    for entry, path in payload.items():
        target = project.parent / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    feed = csharp_feed(root)
    shutil.rmtree(feed, ignore_errors=True)
    run(['dotnet', 'pack', str(project), '-c', 'Release', '-o', str(feed)], cwd=source, check=True)
    nupkg = feed / f'{package}.{version}.nupkg'
    if not nupkg.is_file():
        raise ValueError(f'dotnet pack left no {nupkg.name} in {feed}: the pinned project packs another id or '
                         'version, and restore would take nuget.org\'s')
    difference = compare_payload(nupkg.name, nupkg_payload(nupkg), {e: _sha256(p) for e, p in payload.items()})
    if difference:
        raise ValueError(f'{difference}: the packed package is not the leg\'s')
    digest = hashlib.sha512(nupkg.read_bytes()).digest()
    _write_record(record_path(root, name, 'csharp'), {
        'package': package, 'version': version, 'nupkg': nupkg.relative_to(root).as_posix(),
        'sha512': digest.hex(), 'content_hash': base64.b64encode(digest).decode()})
    for path, text in rewrites.items():
        path.write_text(text, encoding='utf-8')
        print(f'{path.relative_to(root).as_posix()}: {package} [{version}]')
    (root / NUGET_CONFIG).write_text(_nuget_config_text(feed.as_posix(), package), encoding='utf-8')
    print(f'{NUGET_CONFIG}: {package} {version} from {feed} only')
    line = f'NUGET_PACKAGES={csharp_packages(root).as_posix()}\n'
    if environ.get('GITHUB_ENV'):
        with open(environ['GITHUB_ENV'], 'a', encoding='utf-8') as github_env:
            github_env.write(line)
    else:
        print(line, end='')


def _swift_scan(text):
    """[(start, end)] of every `.package(...)` call in a Swift manifest, its parentheses balanced,
    skipping comments (`//`, `/* */`) and string literals, so a comment naming a URL is left."""
    calls, i, n = [], 0, len(text)
    while i < n:
        if text.startswith('//', i):
            i = text.find('\n', i)
            i = n if i < 0 else i
        elif text.startswith('/*', i):
            i = text.find('*/', i + 2)
            i = n if i < 0 else i + 2
        elif text[i] == '"':
            i = _swift_string_end(text, i)
        elif text.startswith('.package(', i):
            start, depth, j = i, 0, i + len('.package')
            while j < n:
                if text[j] == '"':
                    j = _swift_string_end(text, j)
                    continue
                depth += {'(': 1, ')': -1}.get(text[j], 0)
                j += 1
                if depth == 0:
                    break
            if depth:
                raise ValueError('a .package( call is never closed')
            calls.append((start, j))
            i = j
        else:
            i += 1
    return calls


def _swift_string_end(text, i):
    quote = '"""' if text.startswith('"""', i) else '"'
    j = i + len(quote)
    while j < len(text):
        if text[j] == '\\':
            j += 2
        elif text.startswith(quote, j):
            return j + len(quote)
        else:
            j += 1
    raise ValueError('a string literal is never closed')


_SWIFT_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def swift_identity(location):
    """SwiftPM's identity for a URL or a path: its last component, `.git` dropped, lowercased."""
    return re.sub(r'\.git$', '', location.rstrip('/').replace(':', '/').rsplit('/', 1)[-1]).lower()


# The labels a requirement on the companion may carry: where it comes from (replaced by the pin's
# path), which version (meaningless for a path), and the traits it enables (kept verbatim). An
# unlabelled argument is a requirement (`"1.0.0"..<"2.0.0"`, `.upToNextMajor(from:)`).
SWIFT_DROPPED = ('url', 'path', 'name', 'from', 'exact', 'branch', 'revision', None)
SWIFT_KEPT = ('traits',)


def swift_arguments(call):
    """[(label or None, value text)] of a `.package(...)` call's top-level arguments, split at
    commas outside brackets, parentheses, braces and string literals; comments are skipped."""
    inner = call[call.index('(') + 1:call.rindex(')')]
    args, depth, i, start, n = [], 0, 0, 0, len(inner)
    pieces = []
    while i < n:
        if inner.startswith('//', i):
            j = inner.find('\n', i)
            j = n if j < 0 else j
            pieces.append(inner[start:i])
            i = start = j
        elif inner.startswith('/*', i):
            j = inner.find('*/', i + 2)
            j = n if j < 0 else j + 2
            pieces.append(inner[start:i])
            i = start = j
        elif inner[i] == '"':
            i = _swift_string_end(inner, i)
        elif inner[i] in '([{':
            depth += 1
            i += 1
        elif inner[i] in ')]}':
            depth -= 1
            i += 1
        elif inner[i] == ',' and depth == 0:
            pieces.append(inner[start:i])
            args.append(''.join(pieces))
            pieces = []
            i = start = i + 1
        else:
            i += 1
    pieces.append(inner[start:])
    args.append(''.join(pieces))
    found = []
    for arg in (a.strip() for a in args):
        if not arg:
            continue
        match = re.match(r'([A-Za-z_]\w*)\s*:(?!:)\s*(.*)\Z', arg, re.S)
        found.append((match.group(1), match.group(2).strip()) if match else (None, arg))
    return found


def swift_requirements(text, name):
    """(start, end, kind, location) for every `.package(...)` call naming the companion `name`:
    `url` (remote source control), `path`, or `id` (the package registry; `scope.name`)."""
    found = []
    for start, end in _swift_scan(text):
        # Top-level arguments only: a trait's own `name:` is not the package's.
        args = {label: m.group(1) for label, value in swift_arguments(text[start:end])
                if label in ('url', 'path', 'id', 'name') and (m := _SWIFT_STRING.fullmatch(value))}
        for kind in ('url', 'path'):
            if kind in args and (swift_identity(args[kind]) == name or args.get('name', '').lower() == name):
                found.append((start, end, kind, args[kind]))
                break
        else:
            if 'id' in args and args['id'].lower().rsplit('.', 1)[-1] == name:
                found.append((start, end, 'id', args['id']))
    return found


def swift_manifests(root):
    """Every SwiftPM manifest under root (Package.swift and its Package@swift-*.swift variants),
    outside SKIP_DIRS."""
    root = Path(root).resolve()
    return [d / f for d, files in _walk_dirs(root) for f in sorted(files)
            if f == 'Package.swift' or re.fullmatch(r'Package@swift-[\d.]+\.swift', f)]


def swift_pin_path(manifest, pin):
    """The pin's root package as `.package(path:)` names it from `manifest`: relative, so a
    container that copies the tree (suite-swift-static.sh) resolves the same package."""
    return Path(os.path.relpath(pin, manifest.parent)).as_posix()


def swift_trees(pin):
    """{zip name: the tree it unpacks to, relative to the pin}, from the pin's own hyper.json:
    the artifact bundle and the XCFramework its swift-release-assets carries."""
    import hyper_config
    cfg = hyper_config.HyperConfig.load(pin)
    if not cfg.swift_remote:
        raise ValueError(f'the pin\'s {hyper_config.CONFIG_PATH} does not set swift_remote: its run uploads no '
                         'swift-release-assets')
    return dict(zip(cfg.swift_assets, (cfg.bundle, cfg.xcframework)))


SWIFT_ASSETS = 'swift-release-assets'


def swift_zip_entries(zip_path, tree, modes=None):
    """{name relative to the zip's parent tree: bytes} of a swift-release-assets zip, every entry a
    regular file or directory under `<tree basename>/`, with no `..` and no absolute name."""
    import stat
    import zipfile
    top = Path(tree).name
    files = {}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            parts = name.rstrip('/').split('/')
            if name.startswith('/') or '\\' in name or parts[0] != top or any(p in ('', '.', '..') for p in parts):
                raise ValueError(f'{Path(zip_path).name}: {name!r} is not a plain path under {top}/')
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f'{Path(zip_path).name}: {name} is a symlink; the zip holds regular files only')
            if not info.is_dir():
                files[name] = archive.read(info)
                if modes is not None:
                    modes[name] = mode
    if not files:
        raise ValueError(f'{Path(zip_path).name} holds no file')
    return files


def swift_record_path(root, name):
    """.companions/<name>-swift.sums: every extracted file's SHA-256, relative to the pin, in
    sha256sum's format (the containers check it with `sha256sum -c`)."""
    return Path(root).resolve() / WORKDIR / f'{name}-swift.sums'


def swift_expected(root, env, name, pin, modes=None):
    """{path relative to the pin: bytes} the extracted trees must hold, each zip checked against
    SHA256SUMS first, and the zips' paths (`modes`, given, gets each file's mode bits)."""
    trees = swift_trees(pin)
    assets = _artifacts_dir(root, env) / SWIFT_ASSETS
    expected, zips = {}, []
    for asset, tree in trees.items():
        path = assets / asset
        if not path.is_file():
            raise ValueError(f'no {SWIFT_ASSETS}/{asset} staged: the leg takes both of {", ".join(trees)}')
        _checked(root, env, path)
        zips.append(path)
        parent = Path(tree).parent.as_posix()
        held = {}
        expected.update({f'{parent}/{k}': v for k, v in swift_zip_entries(path, tree, held).items()})
        if modes is not None:
            modes.update({f'{parent}/{k}': v for k, v in held.items()})
    return trees, expected, zips


def wire_swift(root, env):
    """The pinned Swift core: the leg's swift-release-assets zips (the artifact bundle and the
    XCFramework), each checked against SHA256SUMS, extracted over the pin's own trees (whatever
    its checkout held there is removed first), the files' hashes recorded in
    .companions/<name>-swift.sums; then every `.package(...)` requirement on the companion in the
    tree's manifests rewritten to `.package(path: "<relative path to the pin>")`, the pin's root
    package, which links that bundle (and, on a Mac, that XCFramework) as its own. A path in the
    manifest is what every SwiftPM client reads, xcodebuild included; an edited dependency
    (`swift package edit`) lives in one .build's state, needs the remote resolvable first, and
    xcodebuild ignores it. A requirement from the registry (`id:`) or another path is refused."""
    import hashlib
    import shutil
    name, pin = requirement(root, env, 'swift')
    root = Path(root).resolve()
    if not (pin / 'Package.swift').is_file():
        raise ValueError(f'the pinned source has no {pin.relative_to(root).as_posix()}/Package.swift')
    manifests = []
    for manifest in swift_manifests(root):
        text = manifest.read_text(encoding='utf-8')
        found = swift_requirements(text, name)
        for start, end, kind, location in found:
            wanted = swift_pin_path(manifest, pin)
            if kind == 'id' or (kind == 'path' and (manifest.parent / location).resolve() != pin):
                raise ValueError(f'{manifest.relative_to(root).as_posix()} takes {name} from '
                                 f'{"the package registry" if kind == "id" else "the path"} {location!r}; the wiring '
                                 f'points a remote requirement at the pin ({wanted}) and knows no other')
            unknown = [label for label, _ in swift_arguments(text[start:end])
                       if label not in SWIFT_DROPPED + SWIFT_KEPT]
            if unknown:
                raise ValueError(f'{manifest.relative_to(root).as_posix()}: {name}\'s requirement passes '
                                 f'{", ".join(f"{u}:" for u in unknown)} which the rewrite to a path can neither '
                                 f'carry nor drop without building something else')
        if found:
            manifests.append((manifest, text, found))
    if not manifests:
        raise ValueError(f'no Package.swift under {root} requires {name}: nothing for the pin to reach')
    modes = {}
    trees, expected, zips = swift_expected(root, env, name, pin, modes)
    for tree in trees.values():
        _refuse_symlinks(root, (pin / tree).relative_to(root))
        if (pin / tree).exists():
            shutil.rmtree(pin / tree)
    for relative, data in expected.items():
        target = pin / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        _keep_mode(target, modes.get(relative))
    record = swift_record_path(root, name)
    record.write_text(''.join(f'{hashlib.sha256(d).hexdigest()}  {r}\n' for r, d in sorted(expected.items())),
                      encoding='utf-8')
    for path in zips:
        print(f'{path.name}: extracted into {pin.relative_to(root).as_posix()}/'
              f'{trees[path.name]} (sha256 {_sha256(path)})')
    for manifest, text, found in manifests:
        path = swift_pin_path(manifest, pin)
        written = []
        for start, end, _, _ in sorted(found, reverse=True):
            kept = ''.join(f', {label}: {value}' for label, value in swift_arguments(text[start:end])
                           if label in SWIFT_KEPT)
            wanted = f'.package(path: "{path}"{kept})'
            text = text[:start] + wanted + text[end:]
            written.append(wanted)
        manifest.write_text(text, encoding='utf-8')
        left = [(k, loc) for _, _, k, loc in swift_requirements(text, name)
                if k != 'path' or (manifest.parent / loc).resolve() != pin]
        if left:
            raise ValueError(f'{manifest.relative_to(root).as_posix()}: {name} is still required from {left}; its '
                             'form is one the rewrite does not know')
        for wanted in reversed(written):
            print(f'{manifest.relative_to(root).as_posix()}: {wanted}')


C_VARIABLE = '{NAME}_PACKAGES'


def _refuse_symlinks(root, relative):
    """A ValueError when `relative` under root, or any directory on the way to it, is a symlink:
    removing or writing through one would act outside .companions/."""
    for i in range(1, len(relative.parts) + 1):
        step = Path(*relative.parts[:i])
        if (root / step).is_symlink():
            raise ValueError(f'{step.as_posix()} is a symlink; the wiring extracts into real directories under '
                             f'{WORKDIR}/ only')


def _keep_mode(path, mode):
    """The archive's execute bits on an extracted file (a shared library or tool packed 0755 stays
    so, as `tar -x` and `unzip` leave it); everything else 0644."""
    os.chmod(path, 0o644 | ((mode or 0) & 0o111))


def c_packages_dir(root, name):
    """Where wire_c unpacks the pinned C archives: .companions/<name>-c/."""
    return Path(root).resolve() / WORKDIR / f'{name}-c'


def c_record_path(root, name):
    """.companions/<name>-c.sums: every unpacked file's SHA-256, relative to the packages
    directory, in sha256sum's format."""
    return Path(root).resolve() / WORKDIR / f'{name}-c.sums'


def c_archive_entries(path, name, version, modes=None):
    """{path relative to the packages directory: bytes} of one C archive (`<name>-<version>-<rid>`
    .tar.gz, or .zip on Windows), every member a regular file or directory under that name."""
    import tarfile
    import zipfile
    stem = re.sub(r'\.(tar\.gz|zip)$', '', path.name)
    if stem == path.name or not stem.startswith(f'{name}-{version}-'):
        raise ValueError(f'{path.name} is not a {name}-{version}-<rid> archive (.tar.gz or .zip): the pin is {version}')
    files = {}

    def add(member, data, mode=0):
        parts = member.rstrip('/').split('/')
        if member.startswith('/') or '\\' in member or parts[0] != stem or any(p in ('', '.', '..') for p in parts):
            raise ValueError(f'{path.name}: {member!r} is not a plain path under {stem}/')
        if data is not None:
            files[member] = data
            if modes is not None:
                modes[member] = mode
    if path.name.endswith('.zip'):
        import stat
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError(f'{path.name}: {info.filename} is a symlink')
                add(info.filename, None if info.is_dir() else archive.read(info), info.external_attr >> 16)
    else:
        with tarfile.open(path, 'r:gz') as archive:
            for member in archive.getmembers():
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f'{path.name}: {member.name} is neither a file nor a directory')
                add(member.name, archive.extractfile(member).read() if member.isfile() else None, member.mode)
    if not files:
        raise ValueError(f'{path.name} holds no file')
    return files


def c_expected(root, env, modes=None):
    """(name, version, archives, {path relative to the packages directory: bytes}): every pinned
    archive (<NAME>_C_REQ, c-packages-ci), each checked against SHA256SUMS before it is read."""
    name, directory = requirement(root, env, 'c')
    version = env.get(f'{name.upper()}_VERSION')
    if not version:
        raise ValueError(f'{WORKDIR}/{ENV} has no {name.upper()}_VERSION, the pinned version')
    archives = sorted(p for p in directory.glob('*') if p.is_file()) if directory.is_dir() else []
    if not archives:
        raise ValueError(f'no pinned C archive under {directory}')
    expected = {}
    for path in archives:
        _checked(root, env, path)
        expected.update(c_archive_entries(path, name, version, modes))
    return name, version, archives, expected


def wire_c(root, env, environ=None):
    """The pinned C archives (<NAME>_C_REQ, the leg's c-packages-ci), each checked against
    SHA256SUMS, unpacked into .companions/<name>-c/ (their files' hashes recorded in
    .companions/<name>-c.sums), every requirement on <name> in the tree's Cargo manifests set to
    the pin's exact version (c/smoke/<name>.sh asks for the version rust/Cargo.toml requires), and
    `<NAME>_PACKAGES`, which that script reads in place of the GitHub Release download, appended
    to $GITHUB_ENV (printed without it). A `<NAME>_PACKAGES` already naming another directory is
    refused."""
    import hashlib
    import shutil
    environ = os.environ if environ is None else environ
    root = Path(root).resolve()
    modes = {}
    name, version, archives, expected = c_expected(root, env, modes)
    target = c_packages_dir(root, name)
    _refuse_symlinks(root, target.relative_to(root))
    variable = C_VARIABLE.format(NAME=name.upper())
    if environ.get(variable) and Path(environ[variable]).resolve() != target:
        raise ValueError(f'{variable} is already set to {environ[variable]}: the pinned archives and that set cannot '
                         'both be the one the smoke links')
    for directory, files in _walk_dirs(root):
        if 'Cargo.toml' in files and _pin_requirement(directory / 'Cargo.toml', name, version):
            print(f'{(directory / "Cargo.toml").relative_to(root).as_posix()}: {name} = "={version}"')
    if target.exists():
        shutil.rmtree(target)
    for relative, data in expected.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        _keep_mode(path, modes.get(relative))
    c_record_path(root, name).write_text(
        ''.join(f'{hashlib.sha256(d).hexdigest()}  {r}\n' for r, d in sorted(expected.items())), encoding='utf-8')
    line = f'{variable}={target.as_posix()}\n'
    if environ.get('GITHUB_ENV'):
        with open(environ['GITHUB_ENV'], 'a', encoding='utf-8') as github_env:
            github_env.write(line)
    else:
        print(line, end='')
    print(f'{target}: {len(archives)} pinned {name} {version} archive(s) unpacked')


# The variable a consumer's r/tools/vendor.sh reads for a companion crate to vendor in place of
# crates.io's (HyperTabular's HYPERCAST_PATH), pointed at the pin's rust/.
R_PATH_VARIABLE = '{NAME}_PATH'
# The DESCRIPTION fields that require another package (Writing R Extensions, 1.1.3).
R_DEPENDENCY_FIELDS = ('Depends', 'Imports', 'LinkingTo', 'Suggests', 'Enhances')
R_PIN_FIELD = 'Hyper-Pin'
# What R would install into without R_LIBS_USER: the code is constant, nothing is spliced in.
R_LIBRARY_PROBE = 'cat(.libPaths()[1L])'


def _read_text(path):
    return Path(path).read_bytes().decode('utf-8')


def r_fields(text):
    """A DESCRIPTION's fields: {name: (start, end)}, the offsets of each field's value with its
    continuation lines (a DCF continuation line starts with whitespace)."""
    return {m.group(1): (m.start(2), m.end(2))
            for m in re.finditer(r'^([^\s:#][^:\n]*):[ \t]*([^\n]*(?:\n[ \t]+[^\n]*)*)', text, re.M)}


def r_field(text, name):
    span = r_fields(text).get(name)
    return re.sub(r'\s+', ' ', text[span[0]:span[1]]).strip() if span else None


def _r_entry(name):
    # The package's name as a whole entry (R names hold letters, digits and dots), with its one
    # optional version constraint.
    return re.compile(rf'(?<![A-Za-z0-9.]){re.escape(name)}(?![A-Za-z0-9.])(\s*\(\s*([^)]*?)\s*\))?')


def r_requirements(text, name):
    """(field, constraint or None) for every requirement on `name` in a DESCRIPTION's text."""
    found = []
    for field, (start, end) in r_fields(text).items():
        if field in R_DEPENDENCY_FIELDS:
            found += [(field, re.sub(r'\s+', ' ', m.group(2)) if m.group(1) else None)
                      for m in _r_entry(name).finditer(text, start, end)]
    return found


def r_descriptions(root, name):
    """Every DESCRIPTION under root (outside SKIP_DIRS, so never the pin's) requiring `name`."""
    return [d / 'DESCRIPTION' for d, files in _walk_dirs(Path(root).resolve())
            if 'DESCRIPTION' in files and r_requirements(_read_text(d / 'DESCRIPTION'), name)]


def _relax_r_requirement(path, name, version):
    """Every versioned requirement on `name` in the DESCRIPTION at path set to `(>= version)`, in
    place; an unversioned one is left alone, and so is everything outside the dependency fields.
    The result is re-read, and a requirement it still finds otherwise is refused, the file left
    as it was."""
    text = _read_text(path)
    pieces, at = [], 0
    for start, end in sorted(span for field, span in r_fields(text).items() if field in R_DEPENDENCY_FIELDS):
        pieces += [text[at:start], _r_entry(name).sub(lambda m: f'{name} (>= {version})' if m.group(1) else m.group(0),
                                                      text[start:end])]
        at = end
    new = ''.join(pieces) + text[at:]
    wrong = [c for _, c in r_requirements(new, name) if c not in (None, f'>= {version}')]
    if wrong:
        raise ValueError(f'{path}: a requirement on {name} is still ({wrong[0]}) after the rewrite to (>= {version})')
    if new == text:
        return False
    path.write_bytes(new.encode('utf-8'))
    return True


def _stamp_r(path, commit):
    """`Hyper-Pin: <commit>` as the DESCRIPTION's one such field (another pin's is replaced)."""
    text = _read_text(path)
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = [line for line in text.splitlines() if not line.startswith(f'{R_PIN_FIELD}:')]
    path.write_bytes((newline.join(lines + [f'{R_PIN_FIELD}: {commit}']) + newline).encode('utf-8'))


def r_pin(root, env):
    """(name, the pin's R package directory, its version, the commit) for the R leg."""
    root = Path(root).resolve()
    name, source = requirement(root, env, 'r')
    commit = env.get(f'{name.upper()}_COMMIT', '')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError(f'{WORKDIR}/{ENV} has no {name.upper()}_COMMIT, the pinned commit the install is stamped with')
    description = source / 'DESCRIPTION'
    where = description.relative_to(root).as_posix() if description.is_relative_to(root) else str(description)
    if not description.is_file():
        raise ValueError(f'the pin has no {where}')
    text = _read_text(description)
    package, version = r_field(text, 'Package'), r_field(text, 'Version')
    if package != name:
        raise ValueError(f'{where} is the package {package}, not {name}')
    if not version:
        raise ValueError(f'{where} has no Version')
    return name, source, version, commit


def r_library(run, environ):
    """The library the job's R installs into and setup-r-dependencies resolves against: the
    first of R_LIBS_USER (created, as setup-r-dependencies creates it), else R's own first."""
    users = [p for p in environ.get('R_LIBS_USER', '').split(os.pathsep) if p]
    if users:
        Path(users[0]).mkdir(parents=True, exist_ok=True)
        return users[0]
    library = run(['Rscript', '-e', R_LIBRARY_PROBE], capture_output=True, text=True, check=True).stdout.strip()
    if not library:
        raise ValueError('R names no library to install into (.libPaths() is empty)')
    return library


def _bash(environ):
    """Git's bash on Windows (System32's is WSL's), PATH's elsewhere (companion_fetch.find_bash)."""
    try:
        return find_bash(environ)
    except ValueError as error:
        raise ValueError(f'no bash to run vendor.sh with: {error}') from None


def wire_r(root, env, run=subprocess.run, environ=None):
    """The pin's R package (<NAME>_R_REQ) staged by its own tools/vendor.sh --stage (its core,
    crates and corpus, from the pinned source), its DESCRIPTION stamped `Hyper-Pin: <commit>`,
    and installed by `R CMD INSTALL` into the job's library (R_LIBS_USER, else R's first), where
    setup-r-dependencies finds it satisfied; every versioned requirement on <name> in the tree's
    DESCRIPTION files relaxed to `(>= <the pin's version>)`; every Cargo requirement on the crate
    set to `=<NAME>_VERSION`, and `<NAME>_PATH` (the crate a consumer's vendor.sh vendors in place
    of crates.io's) at the pin's rust/, appended to $GITHUB_ENV (printed without it). Run again
    (the floor R, with its own R_LIBS_USER), it installs the same tree there. A `<NAME>_PATH`
    already naming another crate is refused."""
    environ = os.environ if environ is None else environ
    root = Path(root).resolve()
    name, source, version, commit = r_pin(root, env)
    crate = source.parent / 'rust'
    variable = R_PATH_VARIABLE.format(NAME=name.upper())
    if environ.get(variable) and Path(environ[variable]).resolve() != crate.resolve():
        raise ValueError(f'{variable} is already set to {environ[variable]}: the pinned crate and that one cannot both '
                         'be the one the R package vendors')
    descriptions = r_descriptions(root, name)
    if not descriptions:
        raise ValueError(f'no DESCRIPTION under {root} requires {name}: nothing for the pinned install to satisfy')
    for path in descriptions:
        if _relax_r_requirement(path, name, version):
            print(f'{path.relative_to(root).as_posix()}: {name} (>= {version})')
    crate_version = env.get(f'{name.upper()}_VERSION')
    if crate_version:
        for directory, files in _walk_dirs(root):
            if 'Cargo.toml' in files and _pin_requirement(directory / 'Cargo.toml', name, crate_version):
                print(f'{(directory / "Cargo.toml").relative_to(root).as_posix()}: {name} = "={crate_version}"')
    run([_bash(environ), (source / 'tools' / 'vendor.sh').as_posix(), '--stage'], cwd=source, check=True)
    _stamp_r(source / 'DESCRIPTION', commit)
    library = r_library(run, environ)
    run(['R', 'CMD', 'INSTALL', '--preclean', f'--library={library}', source.as_posix()], check=True)
    print(f'{library}: {name} {version} installed from {source.as_posix()} ({R_PIN_FIELD}: {commit})')
    if (crate / 'Cargo.toml').is_file():
        line = f'{variable}={crate.as_posix()}\n'
        if environ.get('GITHUB_ENV'):
            with open(environ['GITHUB_ENV'], 'a', encoding='utf-8') as github_env:
                github_env.write(line)
        else:
            print(line, end='')


def _tar_member(tarball, member):
    import tarfile
    with tarfile.open(tarball) as archive:
        return archive.extractfile(member).read()


def main(argv=None):
    wirings = sorted(n[len('wire_'):] for n in globals() if n.startswith('wire_'))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ecosystem', choices=wirings)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--python', help='python: the interpreter command to install into (else '
                        f'${PYTHON_VARIABLE}, else this one); `none` rewrites the requirement only')
    parser.add_argument('--deps', type=Path, help="pyodide: pyodide-suite's dependency_wheels directory")
    args = parser.parse_args(argv)
    extra = {}
    if args.python is not None:
        if args.ecosystem != 'python':
            parser.error('--python is for python')
        extra['python'] = args.python
    if args.deps is not None:
        if args.ecosystem != 'pyodide':
            parser.error('--deps is for pyodide')
        extra['deps'] = args.deps
    try:
        globals()[f'wire_{args.ecosystem}'](args.root, read_env(args.root), **extra)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'::error::companion_wire.py {args.ecosystem}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
