"""What the fetch-companions action does in a consumer leg (the companion-pins spec,
`fetch-companions`), one subcommand per step:

  companion_fetch.py plan --root . --leg L [--ecosystems "a b"]
      has_block, and with a block the companion's name, repo and commit and the ecosystems the
      leg wires (companion_map.ecosystems_for, or the override), as step outputs. Without a
      block it reads nothing else: the leg is not even parsed.
  companion_fetch.py guard --root .
      Release mode: every companion override or .companions/ found (release_guard), each an
      ::error::, and exit 1 if there is any.
  companion_fetch.py confirm --root . --dir <mirror> --outcome <download step outcome>
      The mirror's pin.json, written by the companions job after it checked the pin, must
      equal the block.
  companion_fetch.py stage --root . --leg L --dir <mirror>
      Copies only the leg's artifacts (companion_map.artifacts_for) into
      .companions/<name>-artifacts/, verifies every byte against the mirror's SHA256SUMS, and
      writes the leg's lines of it there as SHA256SUMS.
  companion_fetch.py env --root . --ecosystems "a b"
      Writes .companions/env (write_env) from the staged artifacts and the source checkout.
  companion_fetch.py wire --root . --ecosystems "a b"
      For each ecosystem, companion_wire.py <eco> --root, then companion_origin.py <eco>
      --root. An ecosystem without both is an error, never a silent pass.

The block holds one companion (companion_mirror refuses more); everything here is keyed by
its name all the same.
"""
import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

# Only the standard library and hyper_config load at import, so a block-less leg's `plan` runs
# on any python3 hyper_config runs on; tomllib (3.11) and companion_map load where used.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bun_lock  # noqa: E402
import hyper_config  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
SUMS = 'SHA256SUMS'
PIN = 'pin.json'
WORKDIR = '.companions'
ENV = 'env'
SKIP_DIRS = {'.git', 'node_modules', WORKDIR, '.hyperforge', 'target', '.build', '.venv', 'vendor'}


def the_companion(root):
    """The one companion in the block, or None without a block."""
    block = hyper_config.load_companions(root)
    if not block:
        return None
    if len(block) != 1:
        raise ValueError(f'fetch-companions takes one companion; {hyper_config.CONFIG_PATH} pins '
                         f'{len(block)} ({", ".join(sorted(block))})')
    return next(iter(block.values()))


def _walk(root, patterns):
    """Every file under root whose name matches one of `patterns` (case-insensitively),
    outside SKIP_DIRS."""
    wanted = [p.lower() for p in patterns]
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if any(fnmatch.fnmatchcase(name.lower(), p) for p in wanted):
                yield Path(directory) / name


def _rel(root, path):
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def _pep503(name):
    """A Python project name as PEP 503 normalizes it (companion_wire and companion_origin too)."""
    return re.sub(r'[-_.]+', '-', name).lower()


def _go_module(pin):
    """The module line of the pinned source's go.mod (companion_wire and companion_origin)."""
    path = Path(pin) / 'go.mod'
    match = re.search(r'(?m)^module\s+(\S+)', path.read_text(encoding='utf-8')) if path.is_file() else None
    if not match:
        raise ValueError(f'the pinned source has no module line in {path}')
    return match.group(1).strip('"')


def find_bash(environ, nt=os.name == 'nt', isfile=os.path.isfile, which=None):
    """The bash a script is sourced or run with, whatever shell the step runs: on Windows Git's own
    (%ProgramFiles%\\Git\\bin\\bash.exe, which puts its cygpath on PATH), else one on PATH outside
    %SystemRoot% (System32's is WSL's, which reads no Windows path); elsewhere PATH's. ValueError,
    saying what was found, when there is none (companion_origin's C probe, companion_wire's R)."""
    which = which or shutil.which
    if not nt:
        bash = which('bash', path=environ.get('PATH'))
        if not bash:
            raise ValueError('no bash on PATH')
        return bash
    import ntpath
    git = ntpath.join(environ.get('ProgramFiles', r'C:\Program Files'), 'Git', 'bin', 'bash.exe')
    if isfile(git):
        return git
    bash = which('bash', path=environ.get('PATH'))
    system = ntpath.normcase(environ.get('SystemRoot', r'C:\Windows')).rstrip('\\') + '\\'
    if bash and not ntpath.normcase(bash).startswith(system):
        return bash
    raise ValueError(f'{bash} (WSL\'s bash, which reads no Windows path) and no Git bash at {git}' if bash
                     else f'no bash: neither Git\'s at {git} nor one on PATH')


def _interpreters():
    found = []
    for name in ('python3', 'python'):
        path = shutil.which(name)
        if path and 'WindowsApps' not in path and os.path.realpath(path) not in {os.path.realpath(p) for p in found}:
            found.append(path)
    return found


def pip_installed():
    """`pip inspect`'s installed entries from every interpreter on the path. An interpreter
    without pip has installed nothing with it; any other failure is raised, never passed."""
    entries = []
    # `-m` puts the working directory first on sys.path: run from an empty one, so a pip/ or
    # pip.py in the checkout can't stand in for pip inside the guard meant to catch tampering.
    with tempfile.TemporaryDirectory() as empty:
        for python in _interpreters():
            entries += _inspect(python, empty)
    return entries


def _inspect(python, cwd):
    result = subprocess.run([python, '-m', 'pip', '--disable-pip-version-check', 'inspect'],
                            capture_output=True, text=True, timeout=300, cwd=cwd)
    if result.returncode != 0:
        if 'No module named pip' in result.stderr:
            return []
        raise ValueError(f'pip inspect with {python} failed: {result.stderr.strip()[-500:]}')
    return json.loads(result.stdout).get('installed') or []


def _toml(path):
    """The parsed TOML file, or a finding if it can't be read as TOML: an unreadable file could
    hide an override, so it is never skipped."""
    import tomllib
    try:
        return tomllib.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return f'{path} cannot be parsed as TOML ({error}), so it could hide an override'


def _dirs(root, name):
    """Every directory under root named `name`, outside SKIP_DIRS."""
    for directory, dirs, _ in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if name in dirs:
            yield Path(directory) / name


def _cargo_configs(root, env, home):
    """Every Cargo config a build in the checkout can read: each .cargo/ in the checkout (each
    workspace keeps its own, java/jni/ two levels down), each ancestor's, $CARGO_HOME's and
    ~/.cargo's (an ancestor of the runner's workspace even when CARGO_HOME points elsewhere)."""
    dirs = list(_dirs(root, '.cargo')) + [p / '.cargo' for p in root.parents]
    dirs += [Path(env['CARGO_HOME'])] if env.get('CARGO_HOME') else []
    dirs += [home / '.cargo']
    paths = [d / n for d in dirs for n in ('config.toml', 'config')]
    return list(dict.fromkeys(p.resolve() for p in paths if p.is_file()))


def _ruby_statements(text):
    """Gemfile statements, comments dropped, a call continued over lines that end in a comma or
    a backslash, or inside an open parenthesis, joined into one."""
    statements, current, depth = [], '', 0
    for line in text.splitlines():
        line = re.sub(r'(^|\s)#.*$', '', line).strip()
        if not line:
            continue
        current = f'{current} {line}'.strip()
        depth += line.count('(') - line.count(')')
        if depth > 0 or line.endswith((',', '\\')):
            continue
        statements.append(current)
        current, depth = '', 0
    if current:
        statements.append(current)
    return statements


def _gemfile_paths(text, name):
    """Whether the Gemfile takes `name` from a path: `path:` (or `:path =>`) on its gem call,
    or the gem inside a `path ... do` (or `{ }`) block."""
    gem = re.compile(r'''\bgem\s*\(?\s*["']''' + re.escape(name) + r'''["']''')
    blocks = []  # one entry per open do-block: whether it is a path block
    for statement in _ruby_statements(text):
        is_path = bool(re.match(r'path\b', statement))
        if gem.match(statement) and (re.search(r'(\bpath:|:path\s*=>)', statement) or any(blocks)):
            return True
        if is_path and '{' in statement and gem.search(statement):
            return True
        if re.search(r'\bdo(\s*\|[^|]*\|)?$', statement):
            blocks.append(is_path)
        elif re.fullmatch(r'end\b.*', statement) and blocks:
            blocks.pop()
    return False


def _lock_paths(text, name):
    """Whether a Bundler lockfile's PATH section resolves `name`: a four-space spec line (a
    six-space line is another spec's dependency)."""
    section = None
    for line in text.splitlines():
        if line and not line[0].isspace():
            section = line.strip()
        elif section == 'PATH' and re.match(r'^    ' + re.escape(name) + r' \(', line):
            return True
    return False


# The one Gemfile that may evaluate another: the development peers hook, whose file is HyperForge's
# workspace/Gemfile.peers (`gem "<companion>", path:` for each sibling checkout), named by a path
# relative to the Gemfile and evaluated only when it is there. CI checks out one repository, so the
# file is never there, and this guard fails a leg where it is. Exactly these two statements: any
# other eval_gemfile is Ruby this guard cannot read, and is a finding too.
GEMFILE_PEERS = 'Gemfile.peers'
_PEERS_PATH = re.compile(r'peers\s*=\s*File\.expand_path\(\s*"((?:\.\./)+' + re.escape(GEMFILE_PEERS)
                         + r')"\s*,\s*__dir__\s*\)')
_PEERS_HOOK = 'eval_gemfile peers if File.exist?(peers) && ENV["CI"].nil?'


def gemfile_evals(path, text):
    """What a Gemfile evaluates that a CI leg must not: the peers file, when the hook's path
    reaches one, or any eval_gemfile but the hook. One phrase each, naming the file."""
    statements = _ruby_statements(text)
    found = []
    targets = [m.group(1) for s in statements if (m := _PEERS_PATH.fullmatch(s))]
    for statement in statements:
        if not re.search(r'\beval_gemfile\b', statement):
            continue
        if statement != _PEERS_HOOK or len(targets) != 1:
            found.append(f'evaluates another Gemfile ({statement}), which this guard cannot read')
            continue
        peers = (Path(path).parent / targets[0]).resolve()
        if peers.exists():
            found.append(f'evaluates {peers}, the development peers file, which takes companions from a path:')
    return found


def peer_findings(root, block, env, home):
    """The development peer layout (HyperForge's workspace/, each repository built against the
    working trees beside it), which no CI leg may sit in. Each development switch in a repository
    takes a companion only from its checkout beside this one: both Package.swift, Java's
    settings.gradle.kts, Python's tests/conftest.py, R's tools/vendor.sh, PHP's tools/extension.php,
    and the Ruby hook's Gemfile.peers (gemfile_evals). Cargo, Go, Bun and MSBuild take one through
    a file above the checkout: a `paths` override (the Cargo configs), a go.work, a package.json
    with workspaces, a Directory.Build.props or .targets (which MSBuild imports from any parent
    directory). A CI checkout has none of them, which makes every switch inert; this is what proves
    it, rather than the switches' own CI checks."""
    root = Path(root).resolve()
    found = []
    for path in _cargo_configs(root, env, home):
        data = _toml(path)
        if isinstance(data, dict) and 'paths' in data:
            found.append(f'{_rel(root, path)} has a paths override: Cargo builds here against the crates it names')
    for companion in block.values():
        beside = root.parent / companion.repo.split('/', 1)[1]
        if beside.is_dir():
            found.append(f'{beside} is a companion checkout beside this one, which development switches build against')
    for parent in root.parents:
        if (parent / 'go.work').is_file():
            found.append(f'{parent / "go.work"} is above the checkout: Go builds here against the modules it uses')
        for name in ('Directory.Build.props', 'Directory.Build.targets'):
            if (parent / name).is_file():
                found.append(f'{parent / name} is above the checkout: MSBuild imports it into every project here')
        manifest = parent / 'package.json'
        if manifest.is_file():
            try:
                data = json.loads(_read(manifest))
            except ValueError:
                data = {'workspaces': 'unreadable'}  # it could hide one
            if isinstance(data, dict) and 'workspaces' in data:
                found.append(f'{manifest} is above the checkout with workspaces: Bun and npm link its members here')
    return found


# What npm reads as a path: file: and link: specs, and bare relative, absolute or home paths.
_NPM_PATH = re.compile(r'^(file:|link:|\.{1,2}/|/|~/)')


def _npm_overrides(tree, package, parent=None):
    """(dependency, spec) for every path spec on the package anywhere in an overrides tree,
    where a key may carry a version (`name@1`) and `.` is the parent's own spec."""
    for key, value in (tree or {}).items():
        dep = parent if key == '.' else re.sub(r'(?<=.)@[^/@]*$', '', key)
        if isinstance(value, str):
            if dep and package.fullmatch(dep) and _NPM_PATH.match(value):
                yield dep, value
        elif isinstance(value, dict):
            yield from _npm_overrides(value, package, dep)


def _go_findings(root, companion):
    module = re.escape(f'github.com/{companion.repo}/go')
    project = companion.repo.split('/', 1)[1].lower()
    for relative in ('go/go.mod', 'go/go.work', 'go.work'):
        text = re.sub(r'//.*', '', _read(root / relative))
        if re.search(r'^\s*(?:replace\s+)?' + module + r'(?:\s+v\S+)?\s*=>', text, re.M):
            yield f'{relative} has a replace for github.com/{companion.repo}/go'
        if relative.endswith('go.work'):
            uses = re.findall(r'^\s*use\s+([^\s(]+)', text, re.M)
            for block in re.findall(r'^\s*use\s*\(([^)]*)\)', text, re.M):
                uses += block.split()
            for used in uses:
                if project in used.lower() or WORKDIR in used:
                    yield f'{relative} uses {used}'


def _npm_findings(root, scope, name):
    package = re.compile(re.escape(f'@{scope}/{name}') + r'(?:-[a-z0-9-]+)?')
    for path in _walk(root, ['package.json']):
        try:
            data = json.loads(_read(path))
        except ValueError:
            yield f'{_rel(root, path)} cannot be parsed as JSON, so it could hide an override'
            continue
        for section in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
            for dep, spec in (data.get(section) or {}).items():
                if package.fullmatch(dep) and isinstance(spec, str) and _NPM_PATH.match(spec):
                    yield f'{_rel(root, path)} has a file: {section} {dep} ({spec})'
        for dep, spec in _npm_overrides(data.get('overrides'), package):
            yield f'{_rel(root, path)} has a file: override {dep} ({spec})'
    for path in _walk(root, ['package-lock.json', 'npm-shrinkwrap.json']):
        try:
            packages = json.loads(_read(path)).get('packages') or {}
        except ValueError:
            yield f'{_rel(root, path)} cannot be parsed as JSON, so it could hide an override'
            continue
        for key, entry in packages.items():
            dep = key.rsplit('node_modules/', 1)[-1]
            if package.fullmatch(dep) and isinstance(entry, dict) and (
                    entry.get('link') is True or str(entry.get('resolved', '')).startswith('file:')):
                yield f'{_rel(root, path)} resolves {dep} from a path (file:)'
    yield from _bun_findings(root, package)


def _bun_findings(root, package):
    for path in _walk(root, [bun_lock.LOCK]):
        try:
            entries = list(bun_lock.entries(bun_lock.read(path.parent)))
        except (ValueError, TypeError, IndexError):
            yield f'{_rel(root, path)} cannot be parsed as a Bun lock, so it could hide an override'
            continue
        for _, dep, _, resolved, _ in entries:
            if package.fullmatch(dep) and resolved.startswith(('file:', 'link:')):
                yield f'{_rel(root, path)} resolves {dep} from a path ({resolved.split(":", 1)[0]}:)'


def _python_findings(root, name):
    direct = re.compile(r'(?<![\w.-])' + re.escape(name).replace('_', '[-_.]') + r'(?:\[[^\]]*\])?\s*@\s*\S', re.I)
    for path in _walk(root, ['pyproject.toml', 'requirements*.txt', 'constraints*.txt']):
        text = '\n'.join(line.split('#', 1)[0] for line in _read(path).splitlines())
        if direct.search(text):
            yield f'{_rel(root, path)} requires {name} by a direct reference (@)'
        if path.name != 'pyproject.toml':
            continue
        data = _toml(path)
        if isinstance(data, str):
            yield data
            continue
        sources = ((data.get('tool') or {}).get('uv') or {}).get('sources') or {}
        for key, value in sources.items():
            entries = value if isinstance(value, list) else [value]
            if _pep503(key) == _pep503(name) and any(isinstance(e, dict) and {'path', 'url', 'git'} & set(e)
                                                     for e in entries):
                yield f'{_rel(root, path)} has a [tool.uv.sources] {key} path, url or git source'


def release_guard(root, env=None, home=None, inspect=None):
    """Every companion override, or .companions/, in the checkout at root and on this machine:
    what a release-mode leg must never see. Each finding names the file or variable."""
    root = Path(root).resolve()
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    inspect = pip_installed if inspect is None else inspect
    block = hyper_config.load_companions(root)
    found = []
    if (root / WORKDIR).exists():
        found.append(f'{WORKDIR}/ is in the checkout')
    # Cargo: any patch of crates-io, in any TOML form, in any config a build here reads.
    for path in _cargo_configs(root, env, home):
        data = _toml(path)
        if isinstance(data, str):
            found.append(data)
        elif isinstance(data.get('patch'), dict) and 'crates-io' in data['patch']:
            found.append(f'{_rel(root, path)} has a [patch.crates-io] table')
    found += peer_findings(root, block, env, home)
    # Ruby: a Gemfile evaluating the development peers file, or any other Gemfile.
    for path in _walk(root, ['Gemfile', 'gems.rb']):
        found += [f'{_rel(root, path)} {finding}' for finding in gemfile_evals(path, _read(path))]
    for name, companion in sorted(block.items()):
        project = companion.repo.split('/', 1)[1]
        scope = companion.repo.split('/', 1)[0].lower()
        prefix = name.upper()
        # Go: a replace of the companion's module in go.mod or a go.work, or a go.work use of it.
        found += _go_findings(root, companion)
        # npm: a path spec on the companion's root or platform packages, in a manifest or its
        # overrides, or a lock that resolved one from a path.
        found += _npm_findings(root, scope, name)
        # Ruby: a path: gem, a path block holding it, or a lock resolving it from a PATH.
        for path in _walk(root, ['Gemfile', 'gems.rb']):
            if _gemfile_paths(_read(path), name):
                found.append(f'{_rel(root, path)} takes {name} from a path:')
        for path in _walk(root, ['Gemfile.lock', 'gems.locked']):
            if _lock_paths(_read(path), name):
                found.append(f'{_rel(root, path)} resolves {name} from a PATH source (path:)')
        # NuGet: a source named companions, in the checkout or a user config (Unix and Windows).
        source = re.compile(r'''<add\b[^>]*\bkey\s*=\s*["']companions["']''', re.I)
        nugets = list(_walk(root, ['nuget.config']))
        nugets += [home / '.nuget' / 'NuGet' / 'NuGet.Config', home / '.config' / 'NuGet' / 'NuGet.Config']
        if env.get('APPDATA'):
            nugets.append(Path(env['APPDATA']) / 'NuGet' / 'NuGet.Config')
        for path in dict.fromkeys(p.resolve() for p in nugets if p.is_file()):
            if source.search(_read(path)):
                found.append(f'{_rel(root, path)} has a package source named companions')
        # Python: a direct reference or a uv source in a manifest, or an installed copy with
        # direct_url.json.
        found += _python_findings(root, name)
        try:
            installed = inspect()
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            found.append(f'the installed packages could not be inspected: {error}')
            installed = []
        for entry in installed:
            if _pep503((entry.get('metadata') or {}).get('name', '')) == _pep503(name) and entry.get('direct_url'):
                url = (entry['direct_url'] or {}).get('url', '')
                found.append(f'{name} is installed from a direct_url.json ({url}), not an index')
        # Swift: an edited dependency, or a path package for the companion.
        for path in (root / 'swift' / '.build' / 'workspace-state.json', root / '.build' / 'workspace-state.json'):
            try:
                deps = (json.loads(_read(path)).get('object') or {}).get('dependencies') or []
            except ValueError:
                continue
            for dep in deps:
                ref = dep.get('packageRef') or {}
                if project.lower() in (str(ref.get('identity', '')).lower(), str(ref.get('name', '')).lower()) \
                        and (dep.get('state') or {}).get('name') == 'edited':
                    found.append(f'{_rel(root, path)} lists {project} in edited state')
        swift = re.compile(r'\.package\(\s*(?:name:\s*"([^"]*)"\s*,\s*)?path:\s*"([^"]*)"')
        for path in _walk(root, ['Package.swift']):
            for named, where in swift.findall(_read(path)):
                if named.lower() == project.lower() or project.lower() in where.lower() or WORKDIR in where:
                    found.append(f'{_rel(root, path)} has a .package(path: "{where}") for {project}')
        # The variables the C smoke, the PHP extension loader, the npm consumer tests and the R
        # package's vendor.sh read.
        for variable in (f'{prefix}_PACKAGES', f'{prefix}_PHP_EXTENSION', f'{prefix}_TARBALLS', f'{prefix}_PATH'):
            if variable in env:
                found.append(f'{variable} is set in the environment ({env[variable]})')
    # Gradle: an init script named for the companions, in $GRADLE_USER_HOME and in ~/.gradle,
    # where companion_wire.py java writes it (init.d/companions.gradle).
    gradle_homes = ([Path(env['GRADLE_USER_HOME'])] if env.get('GRADLE_USER_HOME') else []) + [home / '.gradle']
    for directory in dict.fromkeys(g.resolve() / 'init.d' for g in gradle_homes):
        for path in sorted(directory.glob('companions*')):
            found.append(f'the Gradle init script {path} is installed')
    return found


def _sums(sums_path):
    entries = {}
    for number, line in enumerate(Path(sums_path).read_text(encoding='utf-8').splitlines(), 1):
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise ValueError(f'{sums_path}:{number} is not a sha256sum line')
        relative = match.group(2)
        parts = relative.split('/')
        if relative.startswith('/') or '\\' in relative or ':' in relative or any(p in ('', '.', '..') for p in parts):
            raise ValueError(f'{sums_path}:{number}: {relative} is not a plain relative path')
        entries[relative] = match.group(1)
    return entries


def verify(directory, sums_path):
    """Every file under directory (but its own top-level SHA256SUMS and pin.json) must be in
    sums_path with its hash, and every file sums_path lists under an artifact directory that
    is present must be there. Raises ValueError naming the file."""
    directory = Path(directory)
    sums = _sums(sums_path)
    present = set()
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or (path.parent == directory and path.name in (SUMS, PIN)):
            continue
        relative = path.relative_to(directory).as_posix()
        present.add(relative)
        if relative not in sums:
            raise ValueError(f'{relative} is not in the companions job\'s {SUMS}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != sums[relative]:
            raise ValueError(f'{relative} differs from the companions job\'s {SUMS}')
    for relative in sorted(sums):
        if (directory / relative.split('/', 1)[0]).is_dir() and relative not in present:
            raise ValueError(f'{relative} is in the companions job\'s {SUMS} but missing')


def plan(root, leg, ecosystems=''):
    companion = the_companion(root)
    if companion is None:
        return {'has_block': 'false'}
    import companion_map
    parsed = companion_map.parse_leg(leg)
    wanted = ecosystems.split() or companion_map.ecosystems_for(parsed)
    for eco in wanted:
        if eco not in companion_map.ECOSYSTEMS:
            raise ValueError(f'{eco!r} is not an ecosystem ({" ".join(companion_map.ECOSYSTEMS)})')
    companion_map.artifacts_for(parsed, companion.name, hyper_config.HyperConfig.load(root))
    return {'has_block': 'true', 'name': companion.name, 'repo': companion.repo, 'commit': companion.commit,
            'ecosystems': ' '.join(wanted)}


def confirm(root, directory, outcome):
    companion = the_companion(root)
    directory = Path(directory)
    if outcome != 'success' or not (directory / PIN).is_file():
        raise ValueError(f'this run has no companion-{companion.name} artifact ({outcome or "not downloaded"}): '
                         "this repo's `ci.yml` must call `hyper-companions.yml` and need it")
    recorded = json.loads((directory / PIN).read_text(encoding='utf-8'))
    pinned = {'repo': companion.repo, 'commit': companion.commit, 'run': companion.run}
    if json.dumps(recorded, sort_keys=True) != json.dumps(pinned, sort_keys=True):
        raise ValueError(f'companion-{companion.name} records {json.dumps(recorded, sort_keys=True)}, but '
                         f'{hyper_config.CONFIG_PATH} pins {json.dumps(pinned, sort_keys=True)}: the companions job '
                         'checked another pin; re-run the whole board')


def artifacts_dir(root, name):
    return Path(root) / WORKDIR / f'{name}-artifacts'


def stage(root, leg, directory):
    """Copy the leg's artifacts from the mirror into .companions/<name>-artifacts/ and verify them."""
    import companion_map
    companion = the_companion(root)
    directory = Path(directory)
    names = companion_map.artifacts_for(companion_map.parse_leg(leg), companion.name,
                                        hyper_config.HyperConfig.load(root))
    absent = [n for n in names if not (directory / n).is_dir() or not any(p.is_file() for p in (directory / n).rglob('*'))]
    if absent:
        raise ValueError(f'the leg {leg} takes {", ".join(absent)}, absent from companion-{companion.name}')
    out = artifacts_dir(root, companion.name)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in names:
        shutil.copytree(directory / name, out / name)
    verify(out, directory / SUMS)
    kept = {n + '/' for n in names}
    lines = [f'{digest}  {relative}\n' for relative, digest in sorted(_sums(directory / SUMS).items())
             if any(relative.startswith(k) for k in kept)]
    (out / SUMS).write_text(''.join(lines), encoding='utf-8')
    return out


def _version(source):
    import tomllib
    path = source / 'rust' / 'Cargo.toml'
    try:
        version = tomllib.loads(path.read_text(encoding='utf-8'))['package']['version']
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f'{path} gives no [package] version: {error}') from None
    if not isinstance(version, str):
        raise ValueError(f'{path}: [package] version is not a literal')
    return version


def write_env(root, companion, leg_files, ecosystems):
    """.companions/env: `<NAME>_SOURCE`, `_ARTIFACTS`, `_COMMIT`, `_VERSION`, and one
    `<NAME>_<ECOSYSTEM>_REQ` per ecosystem, the reference that ecosystem's wiring points at.
    Every path is relative to root, with `/`, so a container mounting the checkout anywhere
    resolves it; a list is comma-separated. Every line this call doesn't write is kept: any other
    companion's, and this one's requirements from an earlier fetch in the same job (a job that
    wires its ecosystems in two passes, each once its toolchain is on PATH)."""
    import companion_map
    root = Path(root)
    source = Path(WORKDIR) / companion.name
    artifacts = Path(WORKDIR) / f'{companion.name}-artifacts'
    version = _version(root / source)
    files = sorted({(Path(f).resolve().relative_to(root.resolve()) if Path(f).is_absolute() else Path(f)).as_posix()
                    for f in leg_files})
    files = [f for f in files if f.startswith(artifacts.as_posix() + '/') and not f.endswith('/' + SUMS)]

    def under(*prefixes, exclude=()):
        chosen = [f for f in files if f.split('/')[2].startswith(prefixes) and f.split('/')[2] not in exclude]
        return ','.join(chosen)

    src = source.as_posix()
    reqs = {
        'rust': f'{src}/rust',
        'go': f'{src}/go',
        'python': under('pypi-wheels-', exclude=('pypi-wheels-pyodide',)),
        'pyodide': under('pypi-wheels-pyodide'),
        'npm': f'{src}/typescript',
        'ruby': f'{src}/ruby',
        'java': f'{src}/java',
        'csharp': f'{src}/csharp',
        'php': under('php-ext-'),
        'swift': src,
        'r': f'{src}/r',
        'c': f'{artifacts.as_posix()}/{companion_map.C_PACKAGES}',
    }
    prefix = companion.name.upper()
    lines = {f'{prefix}_SOURCE': src, f'{prefix}_ARTIFACTS': artifacts.as_posix(),
             f'{prefix}_COMMIT': companion.commit, f'{prefix}_VERSION': version}
    for eco in ecosystems:
        if eco not in reqs:
            raise ValueError(f'{eco!r} is not an ecosystem ({" ".join(companion_map.ECOSYSTEMS)})')
        lines[f'{prefix}_{eco.upper()}_REQ'] = reqs[eco]
    for key, value in lines.items():
        if re.search(r'\s', value):
            raise ValueError(f'{key}={value!r}: a value in {WORKDIR}/{ENV} carries no whitespace')
    path = root / WORKDIR / ENV
    kept = []
    if path.is_file():
        kept = [line for line in path.read_text(encoding='utf-8').splitlines() if line.split('=', 1)[0] not in lines]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(line + '\n' for line in kept + [f'{k}={v}' for k, v in lines.items()]), encoding='utf-8')
    return path


def _module(name):
    path = SCRIPTS / f'{name}.py'
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def available(eco):
    """Whether companion_wire.wire_<eco> and companion_origin.origin_<eco> both exist."""
    wire_module, origin_module = _module('companion_wire'), _module('companion_origin')
    return bool(wire_module and origin_module and hasattr(wire_module, f'wire_{eco}')
                and hasattr(origin_module, f'origin_{eco}'))


def wire(root, ecosystems):
    # The pin is what this leg tests: a development peer layout around the checkout would take
    # the companion from a working tree instead, past the wiring.
    # (Without a hyper.json, the scripts below fail on it themselves; the files above still count.)
    config = Path(root) / hyper_config.CONFIG_PATH
    block = hyper_config.load_companions(root) if config.is_file() else {}
    found = peer_findings(root, block, os.environ, Path.home())
    if found:
        raise ValueError('companion mode tests the pin, but ' + '; '.join(found))
    for eco in ecosystems:
        if not available(eco):
            raise ValueError(f'no companion_wire.wire_{eco} or companion_origin.origin_{eco}: this leg would test '
                             'whatever resolves, unproven')
        for script in ('companion_wire.py', 'companion_origin.py'):
            print(f'{script} {eco}', flush=True)
            result = subprocess.run([sys.executable, str(SCRIPTS / script), eco, '--root', str(root)])
            if result.returncode != 0:
                raise ValueError(f'{script} {eco} failed (exit {result.returncode})')


def _output(values):
    lines = ''.join(f'{key}={value}\n' for key, value in values.items())
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write(lines)
    print(lines, end='')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('plan', 'guard', 'confirm', 'stage', 'env', 'wire'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--leg', default='')
    parser.add_argument('--ecosystems', default='')
    parser.add_argument('--dir', type=Path)
    parser.add_argument('--outcome', default='')
    args = parser.parse_args(argv)
    if args.mode == 'plan':
        _output(plan(args.root, args.leg, args.ecosystems))
    elif args.mode == 'guard':
        found = release_guard(args.root)
        for item in found:
            print(f'::error::release mode resolves every companion from the registries, but {item}')
        if found:
            return 1
        print('no companion override and no .companions/: the registries decide')
    elif args.mode == 'confirm':
        confirm(args.root, args.dir, args.outcome)
        print(f'the mirror records the pin {hyper_config.CONFIG_PATH} names')
    elif args.mode == 'stage':
        out = stage(args.root, args.leg, args.dir)
        print(f'staged and verified {len(_sums(out / SUMS))} file(s) in {_rel(Path(args.root), out)}')
    elif args.mode == 'env':
        companion = the_companion(args.root)
        files = [p for p in artifacts_dir(args.root, companion.name).rglob('*') if p.is_file()]
        path = write_env(args.root, companion, files, args.ecosystems.split())
        print(path.read_text(encoding='utf-8'), end='')
    elif args.mode == 'wire':
        wire(args.root, args.ecosystems.split())
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ValueError as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
