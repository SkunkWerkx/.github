"""Each ecosystem's proof that the leg took the pinned companion (the companion-pins spec,
"Failure modes", the origin table): run right after companion_wire.py, by fetch-companions.

  companion_origin.py <eco> --root <checkout>

origin_<eco>(root, env, sums) asks the ecosystem's own tool where the companion resolves from
and raises OriginError naming the binding, the origin found and the origin expected. `env` is
.companions/env (companion_wire.read_env); `sums` is the leg's SHA256SUMS
(`<NAME>_ARTIFACTS/SHA256SUMS`), for the ecosystems whose package manager records a hash.

  rust  `cargo tree -i <name> --locked` in every workspace locking the companion names the
        pinned path; Cargo's "patch ... was not used" warning is an error. No hash: a path
        source has none.
  go    `go list -m -json <module>` in every module requiring it has `.Replace.Dir` at the
        pinned source. No hash: a replaced module has no go.sum line.
  python
        Every requirement on the companion says `==<version>`, and `<python> -m pip inspect`
        gives the companion's PEP 610 direct_url.json naming a pinned file, with the
        archive_info sha256 SHA256SUMS lists for it (a registry install has none). The
        interpreter is chosen as for the wiring; with `none`, the requirement alone.
  pyodide
        The requirement as for python, and each pinned Pyodide wheel's bytes as SHA256SUMS
        lists them. pyodide-suite proves the rest inside each interpreter.
  npm   Each consumer's package-lock.json resolves every companion package it requires from
        the `file:` tarball the wiring packed, with that tarball's integrity (npm pack's); the
        tarball holds the leg's addon (or WASI payload) byte for byte as SHA256SUMS lists it.
        --installed, after the install and before the suite: Node resolves the package from
        that consumer's own node_modules, whose payload files are the pinned ones and whose
        package.json and files are the tarball's, whole. --installs
        <dir>, after the consumer tests: every scenario lock under it holds the integrity of
        the set the wiring packed for the job's own registry; --expect a,b names the scenario
        directories under <dir> that must each have left such a lock.
  ruby  Each Gemfile taking <name> takes it by the pin's path, every gemspec requirement on it is
        `= <version>`, and each binary the wiring placed in the pin hashes as SHA256SUMS lists
        it. --installed, after the install and right before the suite (--dir: one consumer):
        `bundle info <name> --path` is the pin (a RubyGems copy names its gem directory), and
        `bundle exec ruby companion_ruby.rb <name>` loads the pin's <name>.rb at its version, on
        the backend the leg staged for this Ruby (its ABI's Magnus extension, else Fiddle), from
        the placed file with SHA256SUMS's hash; on Fiddle the probe forces the load and, where it
        can read the process's mappings (Linux, macOS), that file must be the one copy mapped. A
        leg staging no binary (the lint) stops at Bundler's path. With a ruby-wasm-<abi> archive
        staged, the smoke Gemfile takes <name>-wasm by the path suite-ruby-wasm.sh unpacks it at.
  ruby-wasm
        In suite-ruby-wasm.sh, between the smoke Gemfile's install and rbwasm (--dir: the smoke
        directory): `bundle info <name>-wasm --path` is the gem the pin packed, its lib/ files
        are the pin's, and it carries exactly the leg's ruby-wasm-<abi> archives, each inflating
        to SHA256SUMS's hash.
  php   The pinned PIE zip is the one SHA256SUMS lists, and the extracted extension is its
        entry. --installed, right before the suite: <NAME>_PHP_EXTENSION names that file (unset,
        the suite would load whatever this PHP or a sibling checkout has); `php
        companion_php.php <name>` without it loads no copy (an ini's or PIE's would win over the
        suite's -d flag); with `-d extension=` it loads the pin's version with no startup
        warning, and on Linux the only file of that name mapped is the pin's. --php names the
        PHP (else $HYPER_COMPANION_PHP, else php).
  java  The published jar is the one recorded, its native/ entries are the leg's libraries as
        SHA256SUMS lists them, and the init script and its companions.properties are the ones the
        wiring wrote. --installed, right before the suite (--dir: one Gradle build): `gradlew -q
        companionOrigin` there prints every copy of the module the suites load (each project's
        runtimeClasspath and testRuntimeClasspath, each Test and JavaExec task's classpath), and
        each must be the job-local jar with the recorded SHA-256, as canonical paths (a copy from
        Gradle's module cache is a remote repository's).
  csharp
        The feed's nupkg is the one recorded, its runtimes/ and staticlibs/ are the leg's files as
        SHA256SUMS lists them, nuget.config is the wiring's and the only one, and every
        requirement is `[<version>]`. --installed, after the suite's restore and right before it
        (--dir: one tree): each project.assets.json naming the package restored the pin's
        version with the recorded contentHash, and the global packages folder's copy has the
        feed as its .nupkg.metadata `source` and the packed bytes (NuGet never extracts an id
        and version twice, so a nuget.org copy already there is the one every build takes).
  swift Each swift-release-assets zip is the one SHA256SUMS lists, the pin's bundle and
        XCFramework hold exactly its files (and the record of them), every manifest naming the
        companion takes the pin by path, and `swift package show-dependencies --format json` in
        each such package gives the pin's directory (a remote requirement gives its URL and a
        .build/checkouts path). --installed, right before the suite (--dir: the package it builds;
        --build-system: the build system it builds with): the same graph, then `swift build -v
        --build-tests`, and every archive of the companion it links, by path or through a link
        line's -L directories, is one of the pin's extracted files, by path or by bytes.
  c     Each pinned C archive is the one SHA256SUMS lists, .companions/<name>-c/ holds exactly its
        files (and the record of them), and the consumer's c/smoke/<name>.sh, sourced by bash with
        <NAME>_PACKAGES at that directory, asks for the pin's version and reports the pin's
        unpacked archive for each platform. --installed, right before the smoke: <NAME>_PACKAGES,
        which the smoke inherits, names that directory (unset, the script downloads the GitHub
        Release's archive) and the script reports it so.
  r     The pin's DESCRIPTION carries `Hyper-Pin: <commit>`, every requirement on <name> in the
        tree's DESCRIPTION files is unversioned or `(>= <the pin's version>)`, and `Rscript
        companion_r.R <name>` loads the copy whose DESCRIPTION has that Hyper-Pin, the pin's
        version and no Repository field (CRAN's names CRAN; the GitHub Release tarball, installed
        by url::, has no Hyper-Pin). No hash: the package is built in the job. --installed, right
        before the suite (after the consumer's own vendor.sh): the same, and every vendor.tar.xz
        holding the companion's crate holds the pin's, file for file (crates.io's names its
        package in .cargo-checksum.json).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion_fetch  # noqa: E402
import companion_wire  # noqa: E402


class OriginError(Exception):
    """An override that did not take: the binding, the origin found, the origin expected."""
    def __init__(self, binding, found, expected):
        self.binding, self.found, self.expected = binding, found, expected
        super().__init__(f'{binding}: found {found}, expected {expected}')


def _tail(text, lines=3):
    return ' '.join(line.strip() for line in text.strip().splitlines()[-lines:])


_ANSI = re.compile(r'\x1b\[[0-9;]*m')


def _probe(run, argv, **kwargs):
    try:
        return run(argv, capture_output=True, text=True, **kwargs)
    except OSError as error:
        return subprocess.CompletedProcess(argv, 127, '', f'{argv[0]} could not run: {error}')


def origin_rust(root, env, sums, run=subprocess.run):
    name, pin = companion_wire.requirement(root, env, 'rust')
    root = Path(root).resolve()
    expected = f'{name} from {pin.as_posix()} (the pin, a path source)'
    workspaces = companion_wire.cargo_workspaces(root, name)
    if not workspaces:
        raise OriginError('Rust', f'no Cargo.lock under {root} locking {name}', expected)
    for workspace in workspaces:
        binding = f'Rust ({workspace.relative_to(root).as_posix()}/)'
        # `--color never`: dtolnay/rust-toolchain exports CARGO_TERM_COLOR=always, and a coloured
        # "warning" never matches. The ANSI strip and the pattern's case and optional period
        # still hold if it is lost, and on Cargo 1.85-1.90's "Patch ... graph." (fixtures/origin/).
        result = _probe(run, ['cargo', 'tree', '-i', name, '--locked', '--all-features', '--depth', '0',
                              '--prefix', 'none', '--color', 'never', '--manifest-path', str(workspace / 'Cargo.toml')])
        sys.stderr.write(result.stderr)
        unused = re.search(r'^warning: patch .* was not used in the crate graph\.?$',
                           _ANSI.sub('', result.stderr), re.I | re.M)
        if unused:
            raise OriginError(binding, f'Cargo\'s {unused.group(0)!r}', expected)
        if result.returncode != 0:
            raise OriginError(binding, f'cargo tree failed (exit {result.returncode}): {_tail(result.stderr)}', expected)
        lines = [line for line in result.stdout.splitlines() if line.startswith(f'{name} v')]
        match = re.fullmatch(rf'{re.escape(name)} v(\S+)(?: \((.+)\))?', lines[0]) if len(lines) == 1 else None
        if not match:
            raise OriginError(binding, f'cargo tree printed {result.stdout.strip()!r}', expected)
        version, source = match.groups()
        if source is None:
            raise OriginError(binding, f'{name} v{version} from a registry (crates.io)', expected)
        if Path(source).resolve() != pin:
            raise OriginError(binding, f'{name} v{version} from {source}', expected)
        print(f'{binding}: {lines[0]}')


def origin_go(root, env, sums, run=subprocess.run):
    _, pin = companion_wire.requirement(root, env, 'go')
    root = Path(root).resolve()
    module = companion_fetch._go_module(pin)
    expected = f'{module} replaced by {pin} (the pin)'
    modules = companion_wire.go_modules(root, module)
    if not modules:
        raise OriginError('Go', f'no go.mod under {root} requiring {module}', expected)
    for directory in modules:
        binding = f'Go ({directory.relative_to(root).as_posix()}/)'
        result = _probe(run, ['go', 'list', '-m', '-json', module], cwd=directory)
        if result.returncode != 0:
            raise OriginError(binding, f'no copy: go list failed (exit {result.returncode}): {_tail(result.stderr)}',
                              expected)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise OriginError(binding, f'go list printed {result.stdout.strip()[:200]!r}', expected) from None
        replace = data.get('Replace') or {}
        if not replace:
            raise OriginError(binding, f'{module} {data.get("Version")} from the module proxy, no replace '
                                       f'({data.get("Dir") or "not downloaded"})', expected)
        found = replace.get('Dir') or replace.get('Path') or ''
        if not found or Path(found).resolve() != pin.resolve() or not (pin / 'go.mod').is_file():
            raise OriginError(binding, f'{module} replaced by {found or replace}', expected)
        print(f'{binding}: {module} {data.get("Version")} => {found}')


def _python_requirements_pinned(root, name, version, binding):
    """Every requirement on the companion in the tree's pyproject.toml files says `==version`, or
    an OriginError naming the file: an unrewritten range is what only the index can meet."""
    import tomllib
    root = Path(root).resolve()
    manifests = companion_wire.python_manifests(root, name)
    expected = f"every requirement on {name} at '=={version}', the pin's version"
    if not manifests:
        raise OriginError(binding, f'no pyproject.toml under {root} requiring {name}', expected)
    for manifest in manifests:
        data = tomllib.loads(manifest.read_text(encoding='utf-8'))
        for _, _, value in companion_wire._python_requirements(data):
            match = companion_wire._REQUIREMENT.match(value)
            if match and companion_fetch._pep503(match.group(1)) == companion_fetch._pep503(name) \
                    and (match.group(3) != f'=={version}' or match.group(4)):
                raise OriginError(f'{binding} ({manifest.relative_to(root).as_posix()})',
                                  f'a requirement {value!r}', expected)


def _relative(root, path):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


def origin_python(root, env, sums, run=subprocess.run, python=None, environ=None):
    """`pip inspect` in the interpreter: the companion's PEP 610 direct_url.json names one of the
    pinned files, and its archive_info sha256 is the one SHA256SUMS lists for it."""
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname
    name, files = companion_wire.pinned_files(root, env, 'python')
    version = env.get(f'{name.upper()}_VERSION', '')
    root = Path(root).resolve()
    _python_requirements_pinned(root, name, version, 'Python')
    command = companion_wire.python_command(python, environ)
    if command is None:
        print(f'Python: every requirement on {name} is =={version}; {companion_wire.PYTHON_VARIABLE}=none, so each '
              'container and venv proves its own install')
        return
    binding = f'Python ({" ".join(command)})'
    expected = (f'{name} {version} installed from a pinned file ({", ".join(_relative(root, f) for f in files)}), '
                'its sha256 as SHA256SUMS lists it')
    result = _probe(run, command + ['-m', 'pip', '--disable-pip-version-check', 'inspect'])
    if result.returncode != 0:
        raise OriginError(binding, f'pip inspect failed (exit {result.returncode}): {_tail(result.stderr)}', expected)
    try:
        report = json.loads(result.stdout)
        installed = report.get('installed') or []
    except (ValueError, AttributeError):
        raise OriginError(binding, f'pip inspect printed {result.stdout.strip()[:200]!r}', expected) from None
    interpreter = (report.get('environment') or {}).get('python_full_version')
    if interpreter:
        binding = f'Python {interpreter} ({" ".join(command)})'
    entries = [e for e in installed
               if companion_fetch._pep503((e.get('metadata') or {}).get('name', '')) == companion_fetch._pep503(name)]
    if not entries:
        raise OriginError(binding, f'no {name} installed', expected)
    # Two copies (a stale egg-info on sys.path beside the dist-info, say): proving one says
    # nothing about which one imports.
    if len(entries) > 1:
        where = '; '.join(str(e.get('metadata_location') or '?') for e in entries)
        raise OriginError(binding, f'{len(entries)} copies of {name} installed ({where})', expected)
    entry = entries[0]
    found_version = (entry.get('metadata') or {}).get('version')
    direct = entry.get('direct_url') or {}
    if not direct:
        raise OriginError(binding, f'{name} {found_version} from an index (PyPI), with no direct_url.json', expected)
    url = direct.get('url', '')
    parsed = urlparse(url)
    path = Path(url2pathname(unquote(parsed.path))) if parsed.scheme == 'file' else None
    if path is None or path.resolve() not in {f.resolve() for f in files}:
        raise OriginError(binding, f'{name} {found_version} from {path if path is not None else url}', expected)
    archive = direct.get('archive_info') or {}
    digest = (archive.get('hashes') or {}).get('sha256') or (archive.get('hash') or '').removeprefix('sha256=')
    artifacts = root / env.get(f'{name.upper()}_ARTIFACTS', '')
    key = _relative(artifacts, path)
    shown = _relative(root, path)
    if key not in sums:
        raise OriginError(binding, f'{name} {found_version} from {shown}, which is not in SHA256SUMS', expected)
    if digest != sums[key]:
        raise OriginError(binding, f'{name} {found_version} from {shown} with sha256 {digest or "none recorded"}, '
                                   f'not SHA256SUMS\' {sums[key]}', expected)
    if found_version != version:
        raise OriginError(binding, f'{name} {found_version} from {shown}', expected)
    print(f'{binding}: {name} {found_version} from {shown}, sha256 {digest}')


def origin_pyodide(root, env, sums, run=subprocess.run):
    """What the host can prove for Pyodide: the requirement is the pin's version, and each pinned
    wheel's bytes are SHA256SUMS'. Inside the interpreter, pyodide-suite proves the rest (the
    micropip source and the in-interpreter hash, with dependency_sums)."""
    name, files = companion_wire.pinned_files(root, env, 'pyodide')
    version = env.get(f'{name.upper()}_VERSION', '')
    root = Path(root).resolve()
    _python_requirements_pinned(root, name, version, 'Pyodide')
    artifacts = root / env.get(f'{name.upper()}_ARTIFACTS', '')
    if not files:
        raise OriginError('Pyodide', 'no pinned Pyodide wheel staged', f'{name} {version} from the pin')
    for path in files:
        key, shown = _relative(artifacts, path), _relative(root, path)
        digest = companion_wire._sha256(path) if path.is_file() else 'missing'
        if sums.get(key) != digest:
            raise OriginError(f'Pyodide ({shown})', f'sha256 {digest}',
                              f'sha256 {sums.get(key) or "listed"} as SHA256SUMS lists it')
        print(f'Pyodide ({shown}): sha256 {digest}; pyodide-suite installs it before the consumer and proves it '
              'inside the interpreter')


def _npm_record(root, name, binding, expected):
    path = companion_wire.npm_dir(root, name) / companion_wire.NPM_RECORD
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise OriginError(binding, f'no wiring record ({_relative(root, path)})', expected) from None


def _tar_files(tarball):
    import tarfile
    try:
        with tarfile.open(tarball) as archive:
            return {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
    except (OSError, tarfile.TarError):
        return None


def _installed_is_the_tarball(binding, expected, directory, target, dep, version, members):
    """node_modules/<dep> is the tarball whole: its package.json names dep at the pin's version,
    and its files (a nested node_modules aside) are the tarball's package/ members, byte for byte,
    none missing and none added."""
    def shown(path):
        return path.relative_to(directory).as_posix()
    try:
        data = json.loads((target / 'package.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or (data.get('name'), data.get('version')) != (dep, version):
        found = data if isinstance(data, dict) else {}
        raise OriginError(binding, f'{found.get("name")} {found.get("version")} installed '
                                   f'({shown(target / "package.json")})', expected)
    installed = {p.relative_to(target).as_posix(): p for p in sorted(target.rglob('*'))
                 if p.is_file() and 'node_modules' not in p.relative_to(target).parts}
    packed = {m[len('package/'):]: data for m, data in members.items() if m.startswith('package/')}
    for relative, path in installed.items():
        if relative not in packed:
            raise OriginError(binding, f'{shown(path)}, which the tarball does not hold', expected)
        if path.read_bytes() != packed[relative]:
            raise OriginError(binding, f'{shown(path)} unlike the tarball\'s package/{relative}', expected)
    for relative in sorted(set(packed) - set(installed)):
        raise OriginError(binding, f'{shown(target)} without the tarball\'s package/{relative}', expected)


# Where Node finds a package from a directory: the name an argument (process.argv[1] under -p),
# never part of the code.
NODE_RESOLVE = "require.resolve(process.argv[1] + '/package.json')"


def origin_npm(root, env, sums, run=subprocess.run, installed=False, only=None):
    """Every consumer's lock resolves each companion package it requires from the tarball the
    wiring packed (`file:`), with that tarball's integrity, as `npm pack --json` reported it; the
    tarball holds the leg's payload, byte for byte as SHA256SUMS lists it (a tarball packed from
    the committed whitelist holds no addon). With `installed` (after the install, before the
    suite), Node resolves the package from that consumer's own node_modules, whose payload files
    are the pinned ones, whose package.json names the package at the pin's version, and whose
    files are the tarball's, none missing and none added. `only` limits the check to that consumer directory (the one a step
    installed and runs its suite in)."""
    import hashlib
    name, _, manifest = companion_wire.pinned_npm(root, env)
    root = Path(root).resolve()
    version = env.get(f'{name.upper()}_VERSION', '')
    record = _npm_record(root, name, 'npm', f'{manifest["name"]} {version} from the pin')
    packages = record.get('packages') or {}
    checked = 0
    for path, _, entries in companion_wire._npm_manifests(root, set(packages)):
        directory = path.parent
        if only is not None and directory != (root / only).resolve():
            continue
        binding = f'npm ({directory.relative_to(root).as_posix()}/)'
        for dep in sorted({dep for section, dep in entries if section != companion_wire.NPM_PEER}):
            wired = packages[dep]
            tarball = root / wired['tarball']
            expected = (f'{dep} {version} from file:{wired["tarball"]} (the pinned package packed with this leg\'s '
                        f'artifacts), integrity {wired["integrity"]}')
            entry = companion_wire._npm_lock_entry(directory, dep) if (directory / 'package-lock.json').is_file() else None
            if not entry:
                raise OriginError(binding, f'no node_modules/{dep} entry in package-lock.json', expected)
            resolved = str(entry.get('resolved') or '')
            if not resolved.startswith('file:'):
                raise OriginError(binding, f'{dep} {entry.get("version")} from {resolved or "nowhere"} (a registry)',
                                  expected)
            if (directory / resolved[len('file:'):]).resolve() != tarball.resolve():
                raise OriginError(binding, f'{dep} {entry.get("version")} from {resolved}', expected)
            actual = companion_wire.npm_integrity(tarball) if tarball.is_file() else 'missing'
            if not entry.get('integrity') == wired['integrity'] == actual:
                raise OriginError(binding, f'{dep} from {resolved} with integrity {entry.get("integrity")} in the lock '
                                           f'and {actual} on disk', expected)
            members = _tar_files(tarball) or {}
            for relative in wired['payload']:
                file = Path(relative).name
                if f'package/{file}' not in members:
                    raise OriginError(binding, f'{dep} from a tarball without {file} (packed without the job-local '
                                               'files list)', expected)
                digest = hashlib.sha256(members[f'package/{file}']).hexdigest()
                if digest != sums.get(relative):
                    raise OriginError(binding, f'{dep}\'s {file} with sha256 {digest}, not SHA256SUMS\' '
                                               f'{sums.get(relative) or "absent"}', expected)
            target = directory / 'node_modules' / dep
            if installed or target.exists():
                result = _probe(run, ['node', '-p', NODE_RESOLVE, dep], cwd=directory)
                if result.returncode != 0:
                    raise OriginError(binding, f'Node resolves no {dep}: {_tail(result.stderr)}', expected)
                where = Path(result.stdout.strip()).parent
                if where.resolve() != target.resolve():
                    raise OriginError(binding, f'Node resolves {dep} to {where.as_posix()}', expected)
                for relative in wired['payload']:
                    file = target / Path(relative).name
                    digest = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else 'missing'
                    if digest != sums.get(relative):
                        raise OriginError(binding, f'{file.relative_to(directory).as_posix()} with sha256 {digest}, '
                                                   f'not SHA256SUMS\' {sums.get(relative) or "absent"}', expected)
                _installed_is_the_tarball(binding, expected, directory, target, dep, version, members)
            print(f'{binding}: {dep} {entry.get("version")} from {resolved}, integrity {actual}'
                  + (f'; installed in node_modules with the pinned {", ".join(Path(p).name for p in wired["payload"])}'
                     if installed or target.exists() else ''))
            checked += 1
    if not checked:
        raise OriginError('npm', f'no package.json under {root / (only or "")} requiring a wired package',
                          f'{", ".join(sorted(packages))} from the pinned tarballs')


def origin_npm_installs(root, env, installs, expect=()):
    """The consumer tests' scenarios (each a directory under `installs` with its own lock), which
    install from the job's own registry the set the wiring packed: every companion package a
    lock resolves has the integrity of the pinned set's tarball. At least one must, and each
    directory named in `expect` (a scenario's consumer directory under `installs`) must hold a
    package-lock.json resolving one: a scenario that installed without a lock, or with another
    package manager, would otherwise go unchecked."""
    name, _, manifest = companion_wire.pinned_npm(root, env)
    root = Path(root).resolve()
    record = _npm_record(root, name, 'npm', 'the pinned set')
    registry = record.get('registry') or {}
    expected = f'{", ".join(sorted(registry)) or "the set"} as the wiring packed them for the job\'s registry'
    if not registry:
        raise OriginError('npm', 'no set packed for the registry (the leg stages fewer than every addon)', expected)
    checked = 0
    proven = set()
    for lock in sorted(Path(installs).rglob('package-lock.json')):
        if 'node_modules' in lock.parts:
            continue
        binding = f'npm ({_relative(root, lock.parent)}/)'
        packages = (json.loads(lock.read_text(encoding='utf-8')).get('packages') or {})
        for key, entry in sorted(packages.items()):
            dep = key.rsplit('node_modules/', 1)[-1]
            if not key or dep not in registry and not dep.startswith(manifest['name'] + '-'):
                continue
            wanted = (registry.get(dep) or {}).get('integrity')
            if entry.get('integrity') != wanted:
                raise OriginError(binding, f'{dep} {entry.get("version")} from {entry.get("resolved")} with integrity '
                                           f'{entry.get("integrity")}', expected)
            print(f'{binding}: {dep} {entry.get("version")} from {entry.get("resolved")}, the pinned set\'s integrity')
            checked += 1
            proven.add(lock.parent.resolve())
    if not checked:
        raise OriginError('npm', f'no lock under {_relative(root, installs)} resolving a companion package', expected)
    for scenario in expect:
        directory = (Path(installs) / scenario).resolve()
        if not any(p == directory or directory in p.parents for p in proven):
            raise OriginError(f'npm ({_relative(root, directory)}/)', 'no package-lock.json resolving a companion '
                              'package (a scenario that installed without a lock, or not at all)', expected)


def _ruby_requirements_exact(root, name, version, expected):
    """Every gemspec requirement on `name` is `= <version>`: what a rewrite leaves unchanged."""
    root = Path(root).resolve()
    for directory, files in companion_wire._walk_dirs(root):
        for file in sorted(f for f in files if f.endswith('.gemspec')):
            path = directory / file
            relative = path.relative_to(root).as_posix()
            text = path.read_text(encoding='utf-8')
            try:
                pinned, _ = companion_wire._pin_gemspec(text, relative, name, version)
            except ValueError as error:
                raise OriginError(f'Ruby ({relative})', str(error), expected) from None
            for before, after in zip(text.splitlines(), pinned.splitlines()):
                if before != after:
                    raise OriginError(f'Ruby ({relative})', f'the requirement {before.strip()!r}',
                                      f'{expected}, every requirement on it "= {version}"')


def _ruby_facts(text):
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def origin_ruby(root, env, sums, run=subprocess.run, installed=False, only=None):
    """Right after the wiring: each Gemfile taking the gem takes it by the pin's path, every
    gemspec requirement on it is `= <version>`, and each binary placed in the pin is the staged
    one SHA256SUMS lists. With `installed`, after the install and right before the suite, in each
    consumer (or `only`'s): `bundle info <name> --path` is the pin, and `bundle exec ruby
    companion_ruby.rb <name>` loads the pin's lib/<name>.rb at its version, on the backend the
    leg staged for this Ruby (its ABI's Magnus extension, else Fiddle, which <NAME>_PURE asks
    for), from the placed file, with SHA256SUMS's hash. On Fiddle the probe forces the load, and
    where it can read the process's mappings (Linux, macOS) the library mapped must be that one
    file and no other copy."""
    import shutil
    name, gem = companion_wire.requirement(root, env, 'ruby')
    version = env.get(f'{name.upper()}_VERSION', '')
    root = Path(root).resolve()
    expected = f'{name} {version} from {gem.as_posix()} (the pin, a path gem)'
    bundle = shutil.which('bundle') or 'bundle'
    resolved = {}
    if installed:
        # Bundler's resolution first, so an unwired leg is named by where the gem came from.
        for consumer in [(root / only).resolve()] if only is not None else _ruby_gemfile_dirs(root, name):
            resolved[consumer] = _bundler_path(run, bundle, root, consumer, name, gem, expected)
    _ruby_requirements_exact(root, name, version, expected)
    consumers = _ruby_consumers_simple(root, name, gem, expected)
    artifacts = root / env.get(f'{name.upper()}_ARTIFACTS', '')
    placed = {}
    try:
        placed = companion_wire.ruby_binaries(root, env, name, gem)
    except ValueError as error:
        raise OriginError('Ruby', str(error), expected) from None
    listed = {}
    for staged, target in placed.items():
        relative = staged.relative_to(artifacts).as_posix()
        digest = companion_wire._sha256(target) if target.is_file() else 'absent'
        if sums.get(relative) is None or digest != sums[relative]:
            raise OriginError('Ruby', f'{_relative(root, target)} (sha256 {digest}), not {relative} as SHA256SUMS lists '
                                      f'it ({sums.get(relative) or "absent"})', expected)
        listed[target.resolve()] = sums[relative]
    if any(companion_wire._RUBY_WASM.fullmatch(staged.parent.name) for staged in placed):
        smoke = _ruby_wasm_consumer(root, name, version)
        print(f'Ruby: {_relative(root, smoke)}/ takes {name}-wasm from {companion_wire.ruby_wasm_gem(root, name, version)}')
    if not installed:
        for target in placed.values():
            print(f'Ruby: {_relative(gem, target)}, the staged file')
        print(f'Ruby: {", ".join(_relative(root, c) + "/" for c in consumers)} take {name} from {gem}')
        return
    if only is not None:
        wanted = (root / only).resolve()
        if wanted not in consumers:
            raise OriginError(f'Ruby ({_relative(root, wanted)}/)', f'no Gemfile taking {name} by the pin\'s path',
                              expected)
        consumers = [wanted]
    probe = str(Path(__file__).resolve().parent / 'companion_ruby.rb')
    for consumer in consumers:
        binding = f'Ruby ({_relative(root, consumer)}/)'
        found = resolved.get(consumer) or _bundler_path(run, bundle, root, consumer, name, gem, expected)
        if not placed:
            # A leg that stages no binary (the lint) runs nothing that requires the gem, and
            # the pinned source alone may not load: Bundler's resolution is the proof.
            print(f'{binding}: Bundler resolves {name} from {found}; the leg stages no binary to load')
            continue
        result = _probe(run, [bundle, 'exec', 'ruby', probe, name], cwd=consumer)
        if result.returncode != 0:
            raise OriginError(binding, f'requiring {name} failed (exit {result.returncode}): {_tail(result.stderr)}',
                              expected)
        facts = _ruby_facts(result.stdout)
        binding = f'Ruby {facts.get("ruby", "?")} ({_relative(root, consumer)}/)'
        source = facts.get('source', '')
        if not source or Path(source).resolve() != gem / 'lib' / f'{name}.rb':
            raise OriginError(binding, f'{name} required from {source or "nowhere"}', expected)
        if facts.get('version') != version:
            raise OriginError(binding, f'{name} {facts.get("version")} from {source}', expected)
        abi, backend = facts.get('abi', ''), facts.get('backend', '')
        extensions = [t for t in placed.values() if t.parent.name == abi and t.parent.parent.name == name]
        if backend == 'native':
            loaded, digest = facts.get('extension', ''), facts.get('extension_sha256', '')
        elif backend == 'fiddle':
            if extensions and not facts.get('pure'):
                raise OriginError(binding, f'the Fiddle backend, though the leg staged {_relative(gem, extensions[0])} '
                                           f'for Ruby {abi} and {name.upper()}_PURE is unset', expected)
            loaded, digest = facts.get('library', ''), facts.get('library_sha256', '')
        else:
            raise OriginError(binding, f'backend {backend!r}', expected)
        if not loaded or Path(loaded).resolve() not in listed:
            raise OriginError(binding, f'the {backend} backend loading {loaded or "no file of the pin"}', expected)
        if digest != listed[Path(loaded).resolve()]:
            raise OriginError(binding, f'{loaded} with sha256 {digest}, not SHA256SUMS\'s '
                                       f'{listed[Path(loaded).resolve()]}', expected)
        if backend == 'fiddle' and facts.get('observed') == '1':
            # What the dlopen took, read from the process's own mappings: the one copy, the pin's.
            mapped = [m for m in facts.get('mapped', '').split(',') if m]
            if [Path(m).resolve() for m in mapped] != [Path(loaded).resolve()]:
                raise OriginError(binding, f'the Fiddle backend mapping {", ".join(mapped) or "no"} '
                                           f'{Path(loaded).name} after the load, though its search named {loaded}',
                                  expected)
        print(f'{binding}: {name} {version} from {source}, {backend} backend, {_relative(gem, loaded)} (sha256 {digest})')


def _ruby_wasm_consumer(root, name, version, only=None):
    """The smoke directory, whose Gemfile (the one reading WASM_GEM_DIR, or `only`'s) takes
    <name>-wasm by the path the pin's gem is unpacked at, once; anything else is an OriginError."""
    from companion_fetch import _ruby_statements
    wasm_name = f'{name}-wasm'
    wanted = companion_wire.ruby_wasm_gem(root, name, version)
    expected = f'{wasm_name} {version} from {wanted.as_posix()} (the pin\'s, a path gem)'
    if only is not None:
        directory = (Path(root) / only).resolve()
        gemfiles = [directory / f for f in ('Gemfile', 'gems.rb') if (directory / f).is_file()]
    else:
        gemfiles = companion_wire.ruby_wasm_gemfiles(root)
    if len(gemfiles) != 1:
        raise OriginError('Ruby wasm', f'{len(gemfiles)} smoke Gemfiles', expected)
    path = gemfiles[0]
    binding = f'Ruby wasm ({_relative(root, path)})'
    gem_call = re.compile(r'gem\s*\(?\s*["\']' + re.escape(wasm_name) + r'["\']')
    path_arg = re.compile(r'(?:\bpath:|:path\s*=>)\s*([\'"])((?:\\.|(?!\1)[^\\])*)\1')
    statements = [s for s in _ruby_statements(path.read_text(encoding='utf-8')) if gem_call.match(s)]
    if not statements:
        raise OriginError(binding, f'no gem {wasm_name!r} with the pin\'s path: (rubygems.org resolves it, through '
                                   f'the consumer\'s wasm gem)', expected)
    found = [_ruby_string(m.group(1), m.group(2)) for s in statements for m in [path_arg.search(s)] if m]
    if len(statements) != 1 or len(found) != 1 or Path(found[0]).resolve() != wanted:
        raise OriginError(binding, '; '.join(statements), expected)
    return path.parent


def origin_ruby_wasm(root, env, sums, run=subprocess.run, only=None):
    """build-ruby-wasm, in suite-ruby-wasm.sh between the smoke Gemfile's `bundle install` and
    `rbwasm build`, with that Gemfile's Bundler environment: `bundle info <name>-wasm --path` is
    the gem the pin packed (ruby_wasm_gem), every lib/ file it carries is the pin's own, and it
    carries exactly the leg's ruby-wasm-<abi> archives, each inflating to SHA256SUMS's hash for
    the staged one: the bytes rbwasm links into the interpreter."""
    import gzip
    import shutil
    name, gem = companion_wire.requirement(root, env, 'ruby')
    version = env.get(f'{name.upper()}_VERSION', '')
    root = Path(root).resolve()
    wasm_name = f'{name}-wasm'
    wanted = companion_wire.ruby_wasm_gem(root, name, version)
    expected = f'{wasm_name} {version} from {wanted.as_posix()} (the pin\'s, packed around the staged archive)'
    artifacts = root / env.get(f'{name.upper()}_ARTIFACTS', '')
    try:
        placed = companion_wire.ruby_binaries(root, env, name, gem)
    except ValueError as error:
        raise OriginError('Ruby wasm', str(error), expected) from None
    staged = {companion_wire._RUBY_WASM.fullmatch(s.parent.name)['abi']: s for s in placed
              if companion_wire._RUBY_WASM.fullmatch(s.parent.name)}
    if not staged:
        raise OriginError('Ruby wasm', 'no ruby-wasm-<abi> archive staged for this leg', expected)
    # Bundler's resolution first, so an unwired leg is named by where the gem came from.
    if only is not None:
        consumer = (Path(root) / only).resolve()
    else:
        smoke = companion_wire.ruby_wasm_gemfiles(root)
        if len(smoke) != 1:
            raise OriginError('Ruby wasm', f'{len(smoke)} smoke Gemfiles (reading WASM_GEM_DIR)', expected)
        consumer = smoke[0].parent
    binding = f'Ruby wasm ({_relative(root, consumer)}/)'
    bundle = shutil.which('bundle') or 'bundle'
    result = _probe(run, [bundle, 'info', wasm_name, '--path'], cwd=consumer)
    if result.returncode != 0:
        raise OriginError(binding, f'bundle info failed (exit {result.returncode}): {_tail(result.stderr)}', expected)
    lines = result.stdout.strip().splitlines()
    found = lines[-1].strip() if lines else ''
    if not found or Path(found).resolve() != wanted:
        raise OriginError(binding, f'{wasm_name} from {found or "nowhere"} (RubyGems, not the pin)', expected)
    _ruby_wasm_consumer(root, name, version, only)
    ext = wanted / 'ext' / f'{name}_native'
    carried = sorted(p.relative_to(wanted).as_posix() for p in ext.rglob('*.a.gz')) if ext.is_dir() else []
    for abi, archive in sorted(staged.items()):
        relative = archive.relative_to(artifacts).as_posix()
        gz = ext / abi / f'{name}_native.a.gz'
        try:
            digest = hashlib.sha256(gzip.decompress(gz.read_bytes())).hexdigest()
        except (OSError, EOFError) as error:
            raise OriginError(binding, f'{_relative(wanted, gz)} unreadable ({error})', expected) from None
        if sums.get(relative) is None or digest != sums[relative]:
            raise OriginError(binding, f'{_relative(wanted, gz)} inflating to sha256 {digest}, not {relative} as '
                                       f'SHA256SUMS lists it ({sums.get(relative) or "absent"})', expected)
        carried.remove(_relative(wanted, gz))
    if carried:
        raise OriginError(binding, f'{", ".join(carried)} beside the leg\'s archive: not the pin\'s staged build', expected)
    pin_lib, wasm_lib = gem / 'lib', wanted / 'lib'
    files = {p.relative_to(wasm_lib) for p in wasm_lib.rglob('*') if p.is_file()} if wasm_lib.is_dir() else set()
    for relative in sorted(files):
        source = pin_lib / relative
        if not source.is_file() or source.read_bytes() != (wasm_lib / relative).read_bytes():
            raise OriginError(binding, f'lib/{relative.as_posix()}, not the pin\'s', expected)
    if not (wasm_lib / f'{name}.rb').is_file():
        raise OriginError(binding, f'no lib/{name}.rb in {found}', expected)
    print(f'{binding}: {wasm_name} {version} from {found}, the pin\'s lib/ and '
          + ', '.join(f'ext/{name}_native/{abi}/{name}_native.a.gz (sha256 {sums[staged[abi].relative_to(artifacts).as_posix()]} '
                      'inflated)' for abi in sorted(staged)))


def _ruby_gemfile_dirs(root, name):
    """The directories holding a Gemfile beside a gemspec that requires `name`: the ones whose
    bundle must resolve it."""
    dependency = re.compile(companion_wire._GEMSPEC_DEPENDENCY + r'\s*\(?\s*["\']' + re.escape(name) + r'["\']')
    return [d for d, files in companion_wire._walk_dirs(Path(root).resolve())
            if files & {'Gemfile', 'gems.rb'} and any(dependency.search((d / f).read_text(encoding='utf-8'))
                                                     for f in files if f.endswith('.gemspec'))]


def _bundler_path(run, bundle, root, consumer, name, gem, expected):
    """`bundle info <name> --path` in `consumer`, which must be the pin."""
    binding = f'Ruby ({_relative(root, consumer)}/)'
    result = _probe(run, [bundle, 'info', name, '--path'], cwd=consumer)
    if result.returncode != 0:
        raise OriginError(binding, f'bundle info failed (exit {result.returncode}): {_tail(result.stderr)}', expected)
    lines = result.stdout.strip().splitlines()
    found = lines[-1].strip() if lines else ''
    if not found or Path(found).resolve() != gem:
        raise OriginError(binding, f'{name} from {found or "nowhere"} (RubyGems, not the pin)', expected)
    return found


def _ruby_string(quote, body):
    """A Ruby string literal's value, from its quote and the text between: single quotes escape
    only `\\` and `'`; double quotes, as companion_wire._ruby_quote writes them, `\\`, `"` and
    `#`. Any other escape is left as written, so a path it changes does not match the pin's."""
    escapes = "\\'" if quote == "'" else '\\"#'
    return re.sub(r'\\(.)', lambda m: m[1] if m[1] in escapes else m[0], body)


def _ruby_consumers_simple(root, name, gem, expected):
    """The directories whose Gemfile takes the gem by the pin's path; a Gemfile naming the gem
    otherwise, or one beside a gemspec requiring the gem without it, is an OriginError."""
    from companion_fetch import _ruby_statements
    gem_call = re.compile(r'gem\s*\(?\s*["\']' + re.escape(name) + r'["\']')
    path_arg = re.compile(r'(?:\bpath:|:path\s*=>)\s*([\'"])((?:\\.|(?!\1)[^\\])*)\1')
    dependency = re.compile(companion_wire._GEMSPEC_DEPENDENCY + r'\s*\(?\s*["\']' + re.escape(name) + r'["\']')
    consumers = []
    for directory, files in companion_wire._walk_dirs(root):
        requires = any(dependency.search((directory / f).read_text(encoding='utf-8'))
                       for f in files if f.endswith('.gemspec'))
        for file in sorted(files & {'Gemfile', 'gems.rb'}):
            path = directory / file
            binding = f'Ruby ({path.relative_to(root).as_posix()})'
            statements = [s for s in _ruby_statements(path.read_text(encoding='utf-8')) if gem_call.match(s)]
            if not statements:
                if requires:
                    raise OriginError(binding, f'no gem {name!r} with the pin\'s path: (rubygems.org resolves it)',
                                      expected)
                continue
            found = [_ruby_string(m.group(1), m.group(2)) for s in statements for m in [path_arg.search(s)] if m]
            if len(statements) != 1 or len(found) != 1 or Path(found[0]).resolve() != gem:
                raise OriginError(binding, '; '.join(statements), expected)
            consumers.append(directory)
    if not consumers:
        raise OriginError('Ruby', f'no Gemfile under {root} taking {name} by a path', expected)
    return consumers


def _php_probe(run, php, name, extension=None):
    probe = str(Path(__file__).resolve().parent / 'companion_php.php')
    flags = ['-d', f'extension={extension}'] if extension else []
    result = _probe(run, [php, *flags, '-d', 'display_startup_errors=1', '-d', 'display_errors=stderr', probe, name])
    try:
        facts = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else None
    except ValueError:
        facts = None
    return result, facts


def origin_php(root, env, sums, run=subprocess.run, installed=False, environ=None, php=None):
    """Right after the wiring: the pinned PIE zip is the one SHA256SUMS lists and the extracted
    extension is its entry, byte for byte. With `installed`, right before the suite:
    `<NAME>_PHP_EXTENSION` (which the suite loads) names that file; this PHP without it loads no
    copy of the extension (an ini's, or PIE's, would win over the suite's -d flag); with it, the
    extension loads, at the pin's version, with no startup warning, and on Linux the only file of
    its name mapped into the process is the pin's."""
    environ = os.environ if environ is None else environ
    name, zips = companion_wire.pinned_files(root, env, 'php')
    version = env.get(f'{name.upper()}_VERSION', '')
    root = Path(root).resolve()
    if len(zips) != 1:
        raise OriginError('PHP', f'{name.upper()}_PHP_REQ naming {len(zips)} PIE zips', f'{name} {version} from '
                          'the leg\'s one pinned PIE zip')
    try:
        entry, data = companion_wire.php_extension_entry(zips[0], name)
    except (ValueError, OSError) as error:
        raise OriginError('PHP', str(error), f'{name} {version} from a pinned PIE zip') from None
    extension = companion_wire.php_extension_dir(root, name) / entry
    expected = f'{name} {version} loaded from {extension.as_posix()} (the pin), and from nowhere else'
    artifacts = root / env.get(f'{name.upper()}_ARTIFACTS', '')
    relative = zips[0].relative_to(artifacts).as_posix() if zips[0].is_relative_to(artifacts) else str(zips[0])
    digest = companion_wire._sha256(zips[0])
    if sums.get(relative) != digest:
        raise OriginError('PHP', f'{relative} with sha256 {digest}, not the zip SHA256SUMS lists '
                                 f'({sums.get(relative) or "absent"})', expected)
    import hashlib
    wanted = hashlib.sha256(data).hexdigest()
    found = companion_wire._sha256(extension) if extension.is_file() else 'absent'
    if found != wanted:
        raise OriginError('PHP', f'{_relative(root, extension)} with sha256 {found}, not {entry} from {zips[0].name} '
                                 f'({wanted})', expected)
    if not installed:
        print(f'PHP: {_relative(root, extension)} is {entry} from {zips[0].name} (sha256 {wanted})')
        return
    php = php or environ.get('HYPER_COMPANION_PHP') or 'php'
    variable = f'{name.upper()}_PHP_EXTENSION'
    binding = f'PHP ({php})'
    named = environ.get(variable, '')
    if not named:
        raise OriginError(binding, f'{variable} unset: the suite would load whatever this PHP or a sibling '
                                   'checkout has, not the pin', expected)
    if Path(named).resolve() != extension.resolve():
        raise OriginError(binding, f'{variable}={named}', expected)
    result, bare = _php_probe(run, php, name)
    if bare is None:
        raise OriginError(binding, f'the probe failed (exit {result.returncode}): {_tail(result.stderr or result.stdout)}',
                          expected)
    binding = f'PHP {bare.get("php", "?")} ({php})'
    if bare.get('loaded'):
        raise OriginError(binding, f'{name} {bare.get("version")} loaded by this PHP without the pin (its ini, or '
                                   'PIE\'s)', expected)
    result, facts = _php_probe(run, php, name, named)
    if facts is None:
        raise OriginError(binding, f'the probe failed (exit {result.returncode}): {_tail(result.stderr or result.stdout)}',
                          expected)
    warnings = [line.strip() for line in (result.stderr or '').splitlines() if line.strip()]
    if warnings:
        raise OriginError(binding, f'PHP\'s startup said {warnings[0]!r}', expected)
    if not facts.get('loaded'):
        raise OriginError(binding, f'{name} not loaded with -d extension={named}', expected)
    if facts.get('version') != version:
        raise OriginError(binding, f'{name} {facts.get("version")} loaded', expected)
    others = [m for m in facts.get('mapped') or [] if Path(m).resolve() != extension.resolve()]
    if others:
        raise OriginError(binding, f'{", ".join(others)} mapped into the process beside the pin', expected)
    print(f'{binding}: {name} {facts.get("version")} from {named} (sha256 {wanted})'
          + (', the one file of its name mapped' if facts.get('mapped') else ''))


def _gradle_builds(root, only=None):
    """Every Gradle build under root (or `only`, relative to it), outside SKIP_DIRS: a directory
    with settings.gradle(.kts) that no build found above it already holds."""
    top = root / only if only is not None else root
    builds = []
    for directory, files in companion_wire._walk_dirs(top):
        if {'settings.gradle', 'settings.gradle.kts'} & files and not any(directory.is_relative_to(b) for b in builds):
            builds.append(directory)
    return builds


_GRADLE_CACHE = '/caches/modules-2/'


def origin_java(root, env, sums, run=subprocess.run, installed=False, only=None, environ=None, home=None):
    """Right after the wiring: the published jar is the one recorded, its native/ entries are the
    leg's staged libraries as SHA256SUMS lists them, and the init script in
    $GRADLE_USER_HOME/init.d/ (~/.gradle) is the one the wiring wrote, unchanged, with the data
    beside it in companions.properties. With `installed`, in the suite's own Gradle build, right
    before it (--dir: one build): `gradlew -q companionOrigin` names every copy of the module the
    suites load (each project's runtimeClasspath and testRuntimeClasspath selections, and every
    jar of the module's name on a Test or JavaExec task's classpath), and each must be the
    job-local repository's jar with the recorded SHA-256, compared as canonical paths (Gradle's
    module cache is where a registry copy comes from). The task fails the build on its own, so every Test and JavaExec
    task, which depends on it, proves the same in the suite's process."""
    name, pin = companion_wire.requirement(root, env, 'java')
    root = Path(root).resolve()
    version = env.get(f'{name.upper()}_VERSION', '')
    repository = companion_wire.java_repository(root)
    try:
        group, module = companion_wire.java_coordinate(pin)
        record = companion_wire.read_record(companion_wire.record_path(root, name, 'java'))
    except ValueError as error:
        raise OriginError('Java', str(error), f'{name} {version} published from the pinned source') from None
    expected = f'{group}:{module}:{version} from {repository.as_posix()} (the pin, sha256 {record.get("sha256")})'
    if (record.get('group'), record.get('module'), record.get('version')) != (group, module, version):
        raise OriginError('Java', f'a record of {record.get("group")}:{record.get("module")}:{record.get("version")}',
                          expected)
    jar = root / record.get('jar', '')
    found = companion_wire._sha256(jar) if jar.is_file() else 'absent'
    if found != record.get('sha256'):
        raise OriginError('Java', f'{_relative(root, jar)} with sha256 {found}', expected)
    wanted = {}
    for relative, digest in sums.items():
        directory, _, file = relative.partition('/')
        prefix = next((p for p in ('native-', 'jni-') if directory.startswith(p)), None)
        if prefix and file and '/' not in file:
            wanted[f'native/{directory[len(prefix):]}/{file}'] = digest
    difference = companion_wire.compare_payload(jar.name, companion_wire._jar_natives(jar), wanted)
    if difference:
        raise OriginError('Java', f'{difference} (SHA256SUMS)', expected)
    script = companion_wire.gradle_home(environ, home) / 'init.d' / companion_wire.JAVA_INIT_SCRIPT
    if not script.is_file():
        raise OriginError('Java', f'no Gradle init script at {script}: the build resolves {group}:{module} from the '
                                  'build\'s own repositories (Maven Central)', expected)
    if script.read_text(encoding='utf-8') != companion_wire.JAVA_INIT_TEMPLATE.read_text(encoding='utf-8'):
        raise OriginError('Java', f'{script}, which differs from the one the wiring wrote', expected)
    properties = script.parent / companion_wire.JAVA_INIT_PROPERTIES
    if not properties.is_file():
        raise OriginError('Java', f'no {properties} beside the init script: it has no pin to serve', expected)
    want = dict(zip(companion_wire.JAVA_PROPERTY_KEYS, (repository.as_posix(), group, module, version, record['sha256'])))
    try:
        held = companion_wire.read_java_properties(properties.read_text(encoding='latin-1'))
    except ValueError:
        held = None
    if held != want:
        raise OriginError('Java', f'{properties}, which differs from the one the wiring wrote', expected)
    if not installed:
        print(f'Java: {_relative(root, jar)} is {group}:{module}:{version} (sha256 {found}), with the leg\'s '
              f'{len(wanted)} native libraries; {script} serves it')
        return
    builds = _gradle_builds(root, only)
    if not builds:
        raise OriginError('Java', f'no Gradle build under {_relative(root, root / only) if only else "the checkout"}',
                          expected)
    gradlew = 'gradlew.bat' if os.name == 'nt' else 'gradlew'
    for build in builds:
        where = _relative(root, build).rstrip('/') + '/'
        # The suite's own step makes the wrapper executable (a checkout may not); so does this.
        if (build / gradlew).is_file():
            (build / gradlew).chmod((build / gradlew).stat().st_mode | 0o111)
        result = _probe(run, [str(build / gradlew), '-q', '--console=plain', 'companionOrigin'], cwd=build)
        lines = [line.split('\t') for line in (result.stdout or '').splitlines() if line.startswith('companion-origin\t')]
        for line in lines:
            if len(line) != 5:
                raise OriginError(f'Java ({where})', f'an unreadable companion-origin line {line!r}', expected)
            _, at, display, path, digest = line  # at: the project or the task that loads it
            if display.split(':')[:2] != [group, module]:
                continue
            posix = Path(path).as_posix()
            # Canonical paths: a symlinked checkout or temp root names the same jar another way.
            inside = Path(path).resolve().is_relative_to(repository.resolve())
            if inside and digest == record['sha256'] and display == f'{group}:{module}:{version}':
                continue
            if _GRADLE_CACHE in posix:
                what = "a remote repository's copy (Maven Central, from Gradle's module cache)"
            elif inside:
                what = 'the job-local repository\'s jar, changed after the publish'
            else:
                what = 'a copy from outside the job-local repository'
            raise OriginError(f'Java ({where}, {at})', f'{display} from {path} (sha256 {digest}), {what}', expected)
        if result.returncode != 0:
            text = result.stderr or result.stdout or ''
            if "Task 'companionOrigin' not found" in text:
                raise OriginError(f'Java ({where})', f'no companionOrigin task in the build: the init script is not '
                                                     f'installed, so Gradle resolves {group}:{module} from the '
                                                     'build\'s own repositories (Maven Central)', expected)
            raise OriginError(f'Java ({where})', f'gradlew companionOrigin failed (exit {result.returncode}): '
                                                 f'{_tail(_ANSI.sub("", text), 6)}', expected)
        if not lines:
            raise OriginError(f'Java ({where})', 'no companion-origin line: no project\'s classpath held '
                                                 f'{group}:{module}, so nothing proves which copy the suite takes',
                              expected)
        print(f'Java ({where}): {len(lines)} loaded copy(ies) of {group}:{module}:{version}, each from '
              f'{repository.as_posix()} (sha256 {record["sha256"]})')


def _csharp_requirements(root, package, prop, version, expected):
    """Every requirement on the package in the tree's MSBuild files is `[version]`."""
    count = 0
    for directory, files in companion_wire._walk_dirs(root):
        for file in sorted(files):
            if not file.endswith(companion_wire.MSBUILD_FILES):
                continue
            path = directory / file
            relative = _relative(root, path)
            try:
                _, found, values = companion_wire._rewrite_csharp(relative, path.read_text(encoding='utf-8'),
                                                                   package, prop, version)
            except ValueError as error:
                raise OriginError('C#', str(error), expected) from None
            count += found
            others = [v for v in values if v != f'[{version}]']
            if others:
                raise OriginError('C#', f'{relative} requiring {package} {", ".join(others)}', expected)
    if not count:
        raise OriginError('C#', f'no project requiring {package}', expected)


def origin_csharp(root, env, sums, installed=False, only=None):
    """Right after the wiring: the feed's package is the one recorded, its runtimes/ and
    staticlibs/ are the leg's staged files as SHA256SUMS lists them, nuget.config is the wiring's
    (the feed the package's only source) and every requirement is the pin's exact version. With
    `installed`, after the suite's own restore and right before it (--dir: one tree): each
    project.assets.json naming the package restored it at the pin's version with the recorded
    contentHash, and the global packages folder's copy came from the feed (.nupkg.metadata
    `source`; NuGet never extracts an id and version twice, so a copy already there from
    nuget.org is the one every build takes) with the packed bytes."""
    import base64
    name, source = companion_wire.requirement(root, env, 'csharp')
    root = Path(root).resolve()
    version = env.get(f'{name.upper()}_VERSION', '')
    feed = companion_wire.csharp_feed(root)
    try:
        cfg, package, _ = companion_wire.csharp_package(source)
        record = companion_wire.read_record(companion_wire.record_path(root, name, 'csharp'))
    except ValueError as error:
        raise OriginError('C#', str(error), f'{name} {version} packed from the pinned source') from None
    expected = f'{package} {version} from {feed.as_posix()} (the pin, contentHash {record.get("content_hash")})'
    if (record.get('package'), record.get('version')) != (package, version):
        raise OriginError('C#', f'a record of {record.get("package")} {record.get("version")}', expected)
    nupkg = root / record.get('nupkg', '')
    digest = hashlib.sha512(nupkg.read_bytes()).digest() if nupkg.is_file() else b''
    if digest.hex() != record.get('sha512') or base64.b64encode(digest).decode() != record.get('content_hash'):
        raise OriginError('C#', f'{_relative(root, nupkg)} with sha512 {digest.hex() or "absent"}', expected)
    wanted = {}
    for relative, value in sums.items():
        entry = companion_wire.csharp_entry(relative, cfg)
        if entry:
            wanted[entry] = value
    difference = companion_wire.compare_payload(nupkg.name, companion_wire.nupkg_payload(nupkg), wanted)
    if difference:
        raise OriginError('C#', f'{difference} (SHA256SUMS)', expected)
    config = root / companion_wire.NUGET_CONFIG
    if not config.is_file():
        raise OriginError('C#', f'no {companion_wire.NUGET_CONFIG} at the root: every source this machine knows '
                                f'serves {package}', expected)
    if config.read_text(encoding='utf-8') != companion_wire._nuget_config_text(feed.as_posix(), package):
        raise OriginError('C#', f'{companion_wire.NUGET_CONFIG}, which differs from the one the wiring wrote', expected)
    others = companion_wire._nuget_configs(root)
    if others:
        raise OriginError('C#', f'{", ".join(_relative(root, p) for p in others)} beside the wiring\'s '
                                f'{companion_wire.NUGET_CONFIG}', expected)
    _csharp_requirements(root, package, f'{cfg.project}Version', version, expected)
    if not installed:
        print(f'C#: {_relative(root, nupkg)} is {package} {version} (contentHash {record["content_hash"]}), with the '
              f'leg\'s {len(wanted)} native files; {companion_wire.NUGET_CONFIG} maps it to the feed alone')
        return
    top = root / only if only is not None else root
    restored = []
    for directory, files in companion_wire._walk_dirs(top):
        if 'project.assets.json' not in files:
            continue
        assets = directory / 'project.assets.json'
        try:
            data = json.loads(assets.read_text(encoding='utf-8'))
        except ValueError as error:
            raise OriginError('C#', f'{_relative(root, assets)} cannot be read: {error}', expected) from None
        libraries = {k: v for k, v in (data.get('libraries') or {}).items()
                     if k.split('/')[0].lower() == package.lower()}
        if libraries:
            restored.append((assets, data, libraries))
    if not restored:
        raise OriginError('C#', f'no project.assets.json under {_relative(root, top)} names {package}: nothing '
                                'restored it, so nothing proves which copy the build takes', expected)
    for assets, data, libraries in restored:
        project = assets.parent.parent if assets.parent.name == 'obj' else assets.parent
        binding = f'C# ({_relative(root, project).rstrip("/")}/)'
        for key, library in libraries.items():
            found = key.split('/', 1)[1]
            if found != version:
                raise OriginError(binding, f'{package} {found} (restored in {_relative(root, assets)})', expected)
            folders = [Path(f) for f in data.get('packageFolders') or {}]
            folder = next((f / library.get('path', '') for f in folders
                           if (f / library.get('path', '') / '.nupkg.metadata').is_file()), None)
            if folder is None:
                raise OriginError(binding, f'{package} {found} with no .nupkg.metadata in any package folder '
                                           f'({", ".join(map(str, folders))})', expected)
            metadata = json.loads((folder / '.nupkg.metadata').read_text(encoding='utf-8'))
            origin = str(metadata.get('source', ''))
            if origin.rstrip('/') != feed.as_posix() and Path(origin).resolve() != feed:
                from urllib.parse import urlparse
                host = (urlparse(origin).hostname or '').lower()
                what = "nuget.org's copy" if host == 'nuget.org' or host.endswith('.nuget.org') else 'another source\'s copy'
                raise OriginError(binding, f'{package} {found} at {folder}, {what} (source {origin})', expected)
            if metadata.get('contentHash') != record['content_hash']:
                raise OriginError(binding, f'{package} {found} at {folder} with contentHash '
                                           f'{metadata.get("contentHash")}', expected)
            if library.get('sha512') != record['content_hash']:
                raise OriginError(binding, f'{package} {found} restored with sha512 {library.get("sha512")} '
                                           f'({_relative(root, assets)})', expected)
            copy = folder / f'{package.lower()}.{found}.nupkg'
            held = hashlib.sha512(copy.read_bytes()).hexdigest() if copy.is_file() else 'absent'
            if held != record['sha512']:
                raise OriginError(binding, f'{copy} with sha512 {held}', expected)
            print(f'{binding}: {package} {found} from {origin} at {folder} (contentHash {record["content_hash"]})')


def _tree_matches(binding, base, held_root, expected, label, expected_origin):
    """Every file under each top directory `expected` names (relative to base) is there with its
    bytes, and none else: an OriginError naming the first that differs."""
    tops = sorted({'/'.join(r.split('/')[:held_root]) for r in expected})
    present = {p.relative_to(base).as_posix() for top in tops for p in (base / top).rglob('*') if p.is_file()}
    for relative in sorted(present | set(expected)):
        path = base / relative
        if relative not in expected:
            raise OriginError(binding, f'{label}/{relative}, a file the pinned archive does not hold', expected_origin)
        if not path.is_file():
            raise OriginError(binding, f'no {label}/{relative}, which the pinned archive holds', expected_origin)
        if path.read_bytes() != expected[relative]:
            raise OriginError(binding, f'{label}/{relative} with sha256 {companion_wire._sha256(path)}, not the pinned '
                                       f'archive\'s ({hashlib.sha256(expected[relative]).hexdigest()})', expected_origin)


def _archives_listed(binding, root, env, sums, paths, expected_origin):
    artifacts = root / env.get(next((k for k in env if k.endswith('_ARTIFACTS')), ''), '')
    for path in paths:
        relative = path.relative_to(artifacts).as_posix() if path.is_relative_to(artifacts) else str(path)
        digest = companion_wire._sha256(path)
        if sums.get(relative) != digest:
            raise OriginError(binding, f'{relative} with sha256 {digest}, not the one SHA256SUMS lists '
                                       f'({sums.get(relative) or "absent"})', expected_origin)


def _record_matches(binding, record, expected, expected_origin):
    wanted = ''.join(f'{hashlib.sha256(d).hexdigest()}  {r}\n' for r, d in sorted(expected.items()))
    held = record.read_text(encoding='utf-8') if record.is_file() else None
    if held != wanted:
        raise OriginError(binding, f'{record.name} {"absent" if held is None else "not the extracted files"}',
                          expected_origin)


def _swift_node(graph, name):
    """The `name` package in `swift package show-dependencies --format json`'s tree, or None."""
    stack = [graph]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if str(node.get('identity', '')).lower() == name:
                return node
            stack.extend(node.get('dependencies') or [])
    return None


_LINKED_ARCHIVE = r'(?:^|[\\/])(?:lib{0}\.a|{0}\.lib)$'


def _swift_linked(output, crate):
    """Every archive file of the crate a verbose SwiftPM build names: path tokens ending in
    lib<crate>.a (or <crate>.lib), and, where a link line takes it by `-l<crate>`, the first
    lib<crate>.a (or <crate>.lib) in that line's -L directories (the pre-6.4 build system copies
    the bundle's archive into .build and links it so)."""
    archive = re.compile(_LINKED_ARCHIVE.format(re.escape(crate)))
    found = []
    for line in output.splitlines():
        tokens = [t.strip('"\'') for t in line.split()]
        found += [t for t in tokens if archive.search(t) and (t.startswith('/') or re.match(r'[A-Za-z]:[\\/]', t))]
        if f'-l{crate}' in tokens:
            dirs = [tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t == '-L']
            dirs += [t[2:] for t in tokens if t.startswith('-L') and len(t) > 2]
            for directory in dirs:
                hit = next((Path(directory) / n for n in (f'lib{crate}.a', f'{crate}.lib')
                            if (Path(directory) / n).is_file()), None)
                if hit:
                    found.append(str(hit))
                    break
            else:
                found.append(f'-l{crate} (in none of its -L directories)')
    return list(dict.fromkeys(found))


_PROCESSED = re.compile(r'^ProcessXCFramework ((?:\\ |\S)+) ((?:\\ |\S)+)', re.M)


def _xcframework_linked(output, name, pin, tree, held, binding, expected_origin):
    """From an xcodebuild log (the Apple mobile legs build so): every ProcessXCFramework of the
    companion's XCFramework must read the pin's and write a copy with the pin's bytes, and every
    archive of the companion the link names (the copy, by -l through its -L directory) must hold
    them. Returns (the XCFramework processed, the archives linked)."""
    wanted = (pin / tree).resolve()
    processed = []
    for match in _PROCESSED.finditer(output):
        source, copy = (g.replace('\\ ', ' ') for g in match.groups())
        if Path(source).name != wanted.name:
            continue
        if Path(source).resolve() != wanted:
            raise OriginError(binding, f'{source} processed, not the pin\'s', expected_origin)
        if not Path(copy).is_file() or companion_wire._sha256(Path(copy)) not in held:
            raise OriginError(binding, f'{copy}, the copy of {source}, holding other bytes than the pin\'s slice',
                              expected_origin)
        processed.append(source)
    if not processed:
        raise OriginError(binding, f'no ProcessXCFramework of {wanted.name} in the log: nothing shows the build took '
                                   'the pin\'s XCFramework (an up-to-date build processes nothing)', expected_origin)
    linked = _swift_linked(output, name)
    if not linked:
        raise OriginError(binding, f'a log that links no lib{name}.a', expected_origin)
    for path in linked:
        if not Path(path).is_file() or companion_wire._sha256(Path(path)) not in held:
            raise OriginError(binding, f'{path} linked, holding other bytes than the pin\'s', expected_origin)
    return processed, linked


def origin_swift(root, env, sums, run=subprocess.run, installed=False, only=None, build_system=None,
                 xcodebuild_log=None):
    """Right after the wiring: each swift-release-assets zip is the one SHA256SUMS lists, the pin's
    bundle and XCFramework are those zips' files exactly (and the record of them), every manifest
    naming the companion takes the pin by path, and `swift package show-dependencies --format
    json` in each of those packages gives the pin's directory for it (a remote requirement gives
    its URL and a .build/checkouts path). With `installed`, right before the suite, in `only` (the
    package the suite builds): the same graph, then `swift build -v --build-tests` (with
    `--build-system`, when given): every archive of the companion the build links must be one of
    the pin's extracted files, by path or by bytes (a copy into .build). With `xcodebuild_log` (an
    Apple mobile leg's own xcodebuild output) in place of that build: the XCFramework it processed
    must be the pin's, and its copy and what the link takes the pin's bytes."""
    name, pin = companion_wire.requirement(root, env, 'swift')
    root = Path(root).resolve()
    expected_origin = f'{name} from {pin.as_posix()} (the pin, with the bundle and XCFramework from ' \
                      f'{companion_wire.SWIFT_ASSETS})'
    try:
        trees, expected, zips = companion_wire.swift_expected(root, env, name, pin)
    except (ValueError, OSError) as error:
        raise OriginError('Swift', str(error), expected_origin) from None
    _archives_listed('Swift', root, env, sums, zips, expected_origin)
    _tree_matches('Swift', pin, 2, expected, pin.relative_to(root).as_posix(), expected_origin)
    _record_matches('Swift', companion_wire.swift_record_path(root, name), expected, expected_origin)
    packages = []
    for manifest in companion_wire.swift_manifests(root):
        text = manifest.read_text(encoding='utf-8')
        found = companion_wire.swift_requirements(text, name)
        for _, _, kind, location in found:
            if kind != 'path' or (manifest.parent / location).resolve() != pin:
                raise OriginError(f'Swift ({manifest.relative_to(root).as_posix()})',
                                  f'{name} required from {location} ({"remote source control" if kind == "url" else kind})',
                                  expected_origin)
        if found:
            packages.append(manifest.parent)
    if not packages:
        raise OriginError('Swift', f'no Package.swift under {root} requiring {name}', expected_origin)
    if only is not None:
        packages = [(root / only).resolve()]
    held = {companion_wire._sha256(pin / r) for r in expected}
    for package in dict.fromkeys(packages):
        binding = f'Swift ({package.relative_to(root).as_posix()}/)' if package != root else 'Swift (./)'
        result = _probe(run, ['swift', 'package', 'show-dependencies', '--format', 'json'], cwd=package)
        try:
            graph = json.loads(result.stdout) if result.returncode == 0 else None
        except ValueError:
            graph = None
        if graph is None:
            raise OriginError(binding, f'show-dependencies failed (exit {result.returncode}): '
                                       f'{_tail(result.stderr or result.stdout)}', expected_origin)
        node = _swift_node(graph, name)
        if node is None:
            raise OriginError(binding, f'no {name} in the package graph', expected_origin)
        where = Path(str(node.get('path', ''))).resolve()
        if where != pin:
            url = str(node.get('url', ''))
            kind = 'remote source control, checked out at' if re.match(r'(https?|ssh|git)://|git@', url) else 'at'
            raise OriginError(binding, f'{name} {node.get("version")} from {url} ({kind} {node.get("path")})',
                              expected_origin)
        if not installed:
            print(f'{binding}: {name} from {node.get("path")}')
            continue
        if xcodebuild_log is not None:
            xcframework = next(t for t in trees.values() if t.endswith('.xcframework'))
            log = Path(xcodebuild_log).read_text(encoding='utf-8', errors='replace')
            _, linked = _xcframework_linked(log, name, pin, xcframework, held, binding, expected_origin)
            print(f'{binding}: {name} from {node.get("path")}, processing {(pin / xcframework).as_posix()}, linking '
                  f'{", ".join(linked)}')
            continue
        argv = ['swift', 'build', '-v', '--build-tests'] + (['--build-system', build_system] if build_system else [])
        result = _probe(run, argv, cwd=package)
        if result.returncode != 0:
            raise OriginError(binding, f'swift build failed (exit {result.returncode}): {_tail(result.stderr)}',
                              expected_origin)
        linked = _swift_linked(result.stdout + '\n' + result.stderr, name)
        if not linked:
            raise OriginError(binding, f'a verbose build that linked no lib{name}.a ({name}.lib on Windows): an '
                                       'up-to-date build links nothing, so the proof runs on a fresh one', expected_origin)
        for path in linked:
            file = Path(path)
            inside = file.is_file() and file.resolve().is_relative_to(pin) \
                and file.resolve().relative_to(pin).as_posix() in expected
            if not file.is_file() or not (inside or companion_wire._sha256(file) in held):
                what = 'the remote checkout\'s' if '/.build/checkouts/' in path.replace('\\', '/') else \
                    'a downloaded binary artifact\'s' if '/.build/artifacts/' in path.replace('\\', '/') else 'another'
                raise OriginError(binding, f'{path} linked, {what} archive'
                                           + ('' if file.is_file() else ' (not on disk)'), expected_origin)
        print(f'{binding}: {name} from {node.get("path")}, linking {", ".join(linked)}')


def _same_dir(reported, expected, nt=os.name == 'nt'):
    """Whether the directory a bash script printed is `expected`. On Windows, Git's bash prints
    /d/a/... (or, through cygpath -m, D:/a/...), which Python reads as D:\\d\\a\\...: the drive is
    translated, then both compared as Windows compares paths (case and separators aside)."""
    if not nt:
        return Path(reported).resolve() == Path(expected).resolve()
    import ntpath
    drive = re.match(r'/([A-Za-z])(/|$)', reported)
    if drive:
        reported = f'{drive.group(1)}:/{reported[drive.end():]}'

    def canonical(path):
        return ntpath.normcase(ntpath.normpath(path)).rstrip('\\')
    return canonical(reported) == canonical(expected)


def _find_bash(environ, expected='', nt=os.name == 'nt', isfile=os.path.isfile, which=None):
    """The bash the probe sources the consumer's script with (companion_fetch.find_bash), its
    absence the C origin's failure."""
    try:
        return companion_fetch.find_bash(environ, nt=nt, isfile=isfile, which=which)
    except ValueError as error:
        raise OriginError('C', str(error), expected) from None


# The consumer's own find_package, asked with the probe's data as cache variables (argv), never
# as code: which package it chooses and what that package imports. C is enabled, as the smoke's
# project enables it: the package's version file reads CMAKE_SIZEOF_VOID_P, and without a
# language it calls itself unsuitable.
_CMAKE_PROBE = """cmake_minimum_required(VERSION 3.16)
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
"""


def _cmake_chooses(run, environ, name, version, prefix, archive, held, binding, expected_origin):
    """What CMake's find_package(<name> <version> CONFIG), with `prefix` (the directory the
    consumer's script reported, which run.sh puts on CMAKE_PREFIX_PATH) as its prefix path,
    chooses: the package must be the pinned archive's, and every library it imports one of its
    files, with its bytes."""
    import shutil
    import tempfile
    cmake = shutil.which('cmake', path=environ.get('PATH'))
    if not cmake:
        raise OriginError(binding, 'no cmake on PATH to ask which package find_package chooses', expected_origin)
    with tempfile.TemporaryDirectory() as work:
        (Path(work) / 'CMakeLists.txt').write_text(_CMAKE_PROBE, encoding='utf-8')
        result = _probe(run, [cmake, '-S', work, '-B', str(Path(work) / 'build'), f'-DPROBE_NAME={name}',
                              f'-DPROBE_VERSION={version}', f'-DCMAKE_PREFIX_PATH={prefix}'], env=dict(environ))
    if result.returncode != 0:
        raise OriginError(binding, f'find_package({name} {version}) failing (exit {result.returncode}): '
                                   f'{_tail(result.stderr or result.stdout)}', expected_origin)
    lines = [line.split('companion-probe ', 1)[1] for line in result.stdout.splitlines() if 'companion-probe ' in line]
    chosen = next((line[len('dir='):] for line in lines if line.startswith('dir=')), '')
    archive = Path(archive).resolve()
    if not chosen or not Path(chosen).resolve().is_relative_to(archive):
        raise OriginError(binding, f'find_package({name} {version}) choosing '
                                   f'{Path(chosen).resolve().as_posix() if chosen else "nothing"}', expected_origin)
    imports = []
    for line in lines:
        if line.startswith('dir='):
            continue
        target, _, path = line.partition('=')
        file = Path(path)
        if not file.is_file() or not file.resolve().is_relative_to(archive) or companion_wire._sha256(file) not in held:
            raise OriginError(binding, f'{target} importing {path}', expected_origin)
        imports.append(file.resolve().as_posix())
    if not imports:
        raise OriginError(binding, f'find_package({name} {version}) importing no library', expected_origin)
    return Path(chosen).resolve().as_posix(), imports


def origin_c(root, env, sums, run=subprocess.run, installed=False, environ=None, rids=None):
    """Right after the wiring: each pinned C archive is the one SHA256SUMS lists, .companions/<name>-c/
    is their files exactly (and the record of them), and the consumer's own c/smoke/<name>.sh,
    sourced by bash with `<NAME>_PACKAGES` at that directory, asks for the pin's version (from
    rust/Cargo.toml) and reports, for each archive's platform, the pin's unpacked archive. With
    `installed`, right before the smoke: `<NAME>_PACKAGES` in this environment (which the smoke
    inherits) names that directory (unset, the script downloads the GitHub Release's archive), the
    script reports it so, and for each of `rids` (the archives the smoke builds against) CMake's
    find_package, with that directory on its prefix path as run.sh puts it, chooses that archive's
    package and imports its libraries."""
    environ = os.environ if environ is None else environ
    root = Path(root).resolve()
    name, _ = companion_wire.requirement(root, env, 'c')
    version = env.get(f'{name.upper()}_VERSION', '')
    packages = companion_wire.c_packages_dir(root, name)
    variable = companion_wire.C_VARIABLE.format(NAME=name.upper())
    script = root / 'c' / 'smoke' / f'{name}.sh'
    expected_origin = f'{name} {version} from {packages.as_posix()} (the pin\'s c-packages-ci, unpacked), as ' \
                      f'{script.relative_to(root).as_posix()} reports it'
    try:
        name, version, archives, expected = companion_wire.c_expected(root, env)
    except (ValueError, OSError) as error:
        raise OriginError('C', str(error), expected_origin) from None
    _archives_listed('C', root, env, sums, archives, expected_origin)
    _tree_matches('C', packages, 1, expected, packages.relative_to(root).as_posix(), expected_origin)
    _record_matches('C', companion_wire.c_record_path(root, name), expected, expected_origin)
    named = packages.as_posix()
    if installed:
        named = environ.get(variable, '')
        if not named:
            raise OriginError(f'C ({variable})', f'{variable} unset: {script.relative_to(root).as_posix()} downloads '
                                                 f'{name}\'s GitHub Release archive', expected_origin)
        if Path(named).resolve() != packages:
            raise OriginError(f'C ({variable})', f'{variable}={named}', expected_origin)
        if not rids:
            raise OriginError('C', 'no --rid: nothing names the archive the smoke builds against, so CMake cannot be '
                                   'asked which one find_package chooses', expected_origin)
    if not script.is_file():
        raise OriginError('C', f'no {script.relative_to(root).as_posix()}: the smoke takes {name} from nothing this '
                               'check can ask', expected_origin)
    bash = _find_bash(environ, expected_origin)
    # The script's own functions, named by argument ($5), never by code built from data; paths
    # with forward slashes, which Git's bash on Windows reads as well. Where cygpath is (Git's
    # bash), the directory is printed in its mixed form (D:/a/...), which CMake and Python read.
    probe = ('. "$1"; wanted="$("$5"_version "$2")"; printf "%s\\n" "$wanted"; '
             'reported="$("$5"_archive "$3" "$wanted" "$4")" || exit $?; '
             'if command -v cygpath >/dev/null 2>&1; then reported="$(cygpath -m "$reported")"; fi; '
             'printf "%s\\n" "$reported"')
    staged = sorted({r.split('/', 1)[0][len(f'{name}-{version}-'):] for r in expected})
    for rid in rids or ():
        if rid not in staged:
            raise OriginError(f'C (CMake, {rid})', f'no pinned {rid} archive (the staged c-packages-ci holds '
                                                   f'{", ".join(staged)})', expected_origin)
    import tempfile
    for rid in staged:
        binding = f'C ({script.relative_to(root).as_posix()}, {rid})'
        with tempfile.TemporaryDirectory() as work:
            result = _probe(run, [bash, '-c', probe, 'probe', script.as_posix(), root.as_posix(), rid,
                                  Path(work).as_posix(), name], env={**environ, variable: named})
        lines = result.stdout.strip().splitlines()
        if lines and lines[0] != version:
            raise OriginError(binding, f'a request for {name} {lines[0]} (rust/Cargo.toml)', expected_origin)
        if result.returncode != 0 or len(lines) != 2:
            raise OriginError(binding, f'the script failed (exit {result.returncode}): '
                                       f'{_tail(result.stderr or result.stdout)}', expected_origin)
        wanted, reported = lines
        archive = packages / f'{name}-{version}-{rid}'
        if not _same_dir(reported, str(archive.resolve())):
            raise OriginError(binding, f'{name} {wanted} from {reported}', expected_origin)
        print(f'{binding}: {name} {wanted} from {archive.as_posix()}')
        if installed and rid in rids:
            held = {hashlib.sha256(data).hexdigest() for relative, data in expected.items()
                    if relative.startswith(f'{name}-{version}-{rid}/')}
            chosen, imports = _cmake_chooses(run, {**environ, variable: named}, name, version, reported, archive, held,
                                             f'C (CMake, {rid})', expected_origin)
            print(f'CMake ({rid}): find_package({name} {version}) chose {chosen}, importing {", ".join(imports)}')


def _r_facts(stdout):
    facts = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition('\t')
        if sep:
            facts[key] = value.strip()
    return facts


# What `cargo package` writes beside the crate's own files: the normalized manifest, the original
# as Cargo.toml.orig (compared with the pin's), the VCS record, vendor.sh's checksum file, and the
# lock, which Cargo never reads for a dependency and re-resolves as it packages (the consumer's
# vendor.sh packages the pin with its own core patched in, and the lock gains a [[patch.unused]]
# entry; the companion's vendor.sh, run again for the floor R, takes it out).
_PACKAGED = ('Cargo.toml', 'Cargo.toml.orig', '.cargo_vcs_info.json', '.cargo-checksum.json', 'Cargo.lock')


def _r_requirers(root, name):
    """The Cargo manifests under root (outside .companions/) that depend on crate `name`: the
    Rust an R package vendors, which then needs the companion's crate in its vendor tarball."""
    import tomllib
    found = []
    for manifest in companion_fetch._walk(root, ['Cargo.toml']):
        try:
            data = tomllib.loads(manifest.read_text(encoding='utf-8'))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        if any(companion_wire._dependency_entries(data, name)):
            found.append(manifest)
    return found


def _r_vendored(root, name, crate, expected):
    """Every vendor.tar.xz under root holding vendor/<name>/ (or cargo vendor's versioned
    vendor/<name>-<version>/): each such crate is the one vendor.sh packages from the pin's rust/
    (the checksum file names no package, as a crates.io copy's does, and every file is the
    pin's, byte for byte). When a Cargo manifest in the tree depends on <name> and no tarball
    holds it, the Rust half is unproven, and that fails: an absent crate is never a pass.
    Returns the tarballs proven."""
    import tarfile
    directory = re.compile(rf'vendor/({re.escape(name)}(?:-\d+\.\d+\.\d+[^/]*)?)/(.+)')
    proven = []
    for tarball in companion_fetch._walk(root, ['vendor.tar.xz']):
        binding = f'R ({_relative(root, tarball)})'
        try:
            with tarfile.open(tarball, 'r:xz') as archive:
                crates = {}
                for member in archive.getmembers():
                    match = directory.fullmatch(member.name)
                    if member.isfile() and match:
                        crates.setdefault(match[1], {})[match[2]] = archive.extractfile(member).read()
        except (tarfile.TarError, OSError, EOFError) as error:
            raise OriginError(binding, f'a tarball that cannot be read: {error}', expected) from None
        for folder, files in sorted(crates.items()):
            _r_vendored_crate(root, name, crate, expected, binding, f'vendor/{folder}', files)
        if crates:
            proven.append(tarball)
    if not proven:
        requirers = _r_requirers(root, name)
        if requirers:
            needing = ', '.join(_relative(root, m) for m in requirers)
            raise OriginError('R (vendor.tar.xz)', f'no vendor.tar.xz holds {name} ({needing} requires it): '
                              'the R package\'s Rust half is unproven', expected)
    return proven


def _r_vendored_crate(root, name, crate, expected, binding, folder, files):
    """One vendored copy of the companion's crate, `files` keyed by path inside `folder`."""
    try:
        package = json.loads(files.get('.cargo-checksum.json', b'{}')).get('package')
    except ValueError:
        package = 'unreadable'
    if package is not None:
        raise OriginError(binding, f'a registry\'s {name} at {folder} (its .cargo-checksum.json names the package {package})',
                          expected)
    pinned = crate / 'Cargo.toml'
    if files.get('Cargo.toml.orig') != (pinned.read_bytes() if pinned.is_file() else None):
        raise OriginError(binding, f'a {name} whose Cargo.toml.orig differs from {_relative(root, pinned)}', expected)
    for relative, data in sorted(files.items()):
        if relative in _PACKAGED:
            continue
        held = crate / relative
        if not held.is_file():
            raise OriginError(binding, f'a {name} whose {relative} is not the pin\'s', expected)
        if held.read_bytes() != data:
            raise OriginError(binding, f'a {name} whose {relative} differs from {_relative(root, held)}', expected)
    print(f'{binding}: {name} from {_relative(root, crate)}, {len(files)} files, the pin\'s ({folder})')


def origin_r(root, env, sums, run=subprocess.run, installed=False):
    """The pin's DESCRIPTION carries `Hyper-Pin: <commit>`, every requirement on <name> in the
    tree's DESCRIPTION files is unversioned or `(>= <the pin's version>)`, and `Rscript
    companion_r.R <name>` (the step's R, its library paths) loads the pin's build: that copy's
    DESCRIPTION has the commit as its Hyper-Pin, the pin's version and no Repository (a CRAN copy
    names CRAN; the GitHub Release's tarball, installed by url::, has no Hyper-Pin). No hash: the
    package is built in the job. With `installed`, right before the suite (after the consumer's
    own vendor.sh), every vendor.tar.xz under root that holds the companion's crate holds the
    pin's: a consumer whose R package compiles its core vendors the companion's crate with it,
    from <NAME>_PATH, and without it from crates.io."""
    root = Path(root).resolve()
    try:
        name, source, version, commit = companion_wire.r_pin(root, env)
    except ValueError as error:
        raise OriginError('R', str(error), 'the pinned R package') from None
    expected = f'{name} {version} built from {source.as_posix()} (the pin, {companion_wire.R_PIN_FIELD}: {commit}), ' \
               'from no repository'
    description = source / 'DESCRIPTION'
    stamped = companion_wire.r_field(companion_wire._read_text(description), companion_wire.R_PIN_FIELD)
    if stamped != commit:
        raise OriginError('R', f'{_relative(root, description)} with '
                               f'{"no " + companion_wire.R_PIN_FIELD if stamped is None else stamped}', expected)
    for directory, files in companion_wire._walk_dirs(root):
        if 'DESCRIPTION' not in files:
            continue
        path = directory / 'DESCRIPTION'
        for field, constraint in companion_wire.r_requirements(companion_wire._read_text(path), name):
            if constraint not in (None, f'>= {version}'):
                raise OriginError(f'R ({_relative(root, path)})', f'a requirement {name} ({constraint}) in {field}',
                                  expected)
    binding = 'R (Rscript)'
    result = _probe(run, ['Rscript', str(Path(__file__).resolve().parent / 'companion_r.R'), name])
    facts = _r_facts(result.stdout or '')
    if 'error' in facts:
        raise OriginError(binding, f'{name} does not load: {facts["error"]} (libraries {facts.get("libpaths", "?")})',
                          expected)
    if result.returncode != 0 or 'path' not in facts:
        raise OriginError(binding, f'the probe failed (exit {result.returncode}): {_tail(result.stderr or result.stdout)}',
                          expected)
    where, loaded = facts['path'], f'{facts.get("Package", "?")} {facts.get("Version", "?")}'
    if 'Repository' in facts:
        raise OriginError(binding, f'{loaded} from {facts["Repository"]} ({where})', expected)
    pin = facts.get(companion_wire.R_PIN_FIELD)
    if pin is None:
        remote = f', RemoteType {facts["RemoteType"]}' if 'RemoteType' in facts else ''
        raise OriginError(binding, f'{loaded} with no {companion_wire.R_PIN_FIELD} field ({where}{remote})', expected)
    if facts.get('Package') != name or facts.get('Version') != version:
        raise OriginError(binding, f'{loaded} at {where}', expected)
    if pin != commit:
        raise OriginError(binding, f'{loaded} built from another pin ({pin}) at {where}', expected)
    print(f'{binding}: {loaded} from {where}, {companion_wire.R_PIN_FIELD} {pin}')
    if installed:
        _r_vendored(root, name, source.parent / 'rust', expected)


def read_sums(root, env):
    """The leg's SHA256SUMS, from `<NAME>_ARTIFACTS`; empty when it has no artifacts file."""
    folders = [v for k, v in env.items() if k.endswith('_ARTIFACTS')]
    sums = {}
    for folder in folders:
        path = Path(root) / folder / companion_fetch.SUMS
        if path.is_file():
            sums.update(companion_fetch._sums(path))
    return sums


def main(argv=None):
    origins = sorted(n[len('origin_'):].replace('_', '-') for n in globals() if n.startswith('origin_'))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ecosystem', choices=origins)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--python', help='python: the interpreter command to inspect (else '
                        f'${companion_wire.PYTHON_VARIABLE}, else this one)')
    parser.add_argument('--installed', action='store_true',
                        help='npm, ruby, php, java, csharp, swift, c, r: after the install (or restore) and right '
                             'before the suite, what the tool loads (or links) must be the pin\'s')
    parser.add_argument('--dir', type=Path, help='npm, ruby, ruby-wasm, java, csharp, swift: only the consumer in '
                        'this directory (relative to --root)')
    parser.add_argument('--build-system', help='swift, with --installed: the build system the suite builds with '
                        '(`native`, the pre-6.4 one)')
    parser.add_argument('--xcodebuild-log', type=Path, help='swift, with --installed: an Apple mobile leg\'s '
                        'xcodebuild output, read in place of a build: the XCFramework it processed and linked')
    parser.add_argument('--rid', action='append', default=[], help='c, with --installed (required there): the '
                        'archive the smoke builds against, whose package CMake must choose (repeat for several)')
    parser.add_argument('--php', help='php: the PHP command to probe (else $HYPER_COMPANION_PHP, else php)')
    parser.add_argument('--installs', type=Path,
                        help='npm: the consumer tests\' scenario directories, whose locks must hold the pinned set')
    parser.add_argument('--expect', default='',
                        help='npm, with --installs: comma-separated scenario directories under it, each of which '
                             'must hold a lock resolving a companion package')
    args = parser.parse_args(argv)
    extra = {}
    if args.python is not None:
        if args.ecosystem != 'python':
            parser.error('--python is for python')
        extra['python'] = args.python
    if args.installed and args.ecosystem not in ('npm', 'ruby', 'php', 'java', 'csharp', 'swift', 'c', 'r'):
        parser.error('--installed is for npm, ruby, php, java, csharp, swift, c and r (ruby-wasm is always after the '
                     'install)')
    if args.dir and args.ecosystem not in ('npm', 'ruby', 'ruby-wasm', 'java', 'csharp', 'swift'):
        parser.error('--dir is for npm, ruby, ruby-wasm, java, csharp and swift')
    if args.build_system is not None:
        if args.ecosystem != 'swift' or not args.installed:
            parser.error('--build-system is for swift, with --installed')
        extra['build_system'] = args.build_system
    if args.xcodebuild_log is not None:
        if args.ecosystem != 'swift' or not args.installed:
            parser.error('--xcodebuild-log is for swift, with --installed')
        extra['xcodebuild_log'] = args.xcodebuild_log
    if args.rid:
        if args.ecosystem != 'c' or not args.installed:
            parser.error('--rid is for c, with --installed')
        extra['rids'] = args.rid
    elif args.ecosystem == 'c' and args.installed:
        parser.error('c --installed needs --rid: the archive the smoke builds against')
    if (args.installs or args.expect) and args.ecosystem != 'npm':
        parser.error('--installs and --expect are for npm')
    if args.php is not None:
        if args.ecosystem != 'php':
            parser.error('--php is for php')
        extra['php'] = args.php
    if args.expect and args.installs is None:
        parser.error('--expect names scenarios under --installs')
    if args.installed:
        extra['installed'] = True
    if args.dir is not None:
        extra['only'] = args.dir
    try:
        env = companion_wire.read_env(args.root)
        if args.installs is not None:
            origin_npm_installs(args.root, env, args.installs,
                                expect=[s for s in args.expect.split(',') if s])
            return 0
        globals()[f'origin_{args.ecosystem.replace("-", "_")}'](args.root, env, read_sums(args.root, env), **extra)
    except OriginError as error:
        print(f'::error::{error}', file=sys.stderr)
        return 1
    except ValueError as error:
        print(f'::error::companion_origin.py {args.ecosystem}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
