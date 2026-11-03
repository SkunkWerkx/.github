"""The companion gate: a release resolves each companion from the registries, so before any
build, the version the consumer's manifests require must be live on every one of them but CRAN.

A release-mode run of hyper-companions.yml runs this (the companion-pins spec, `companion-gate`).
It reads the lower bound every manifest puts on each companion; they must agree. It then probes:

  crates.io       the sparse index line, not yanked
  NuGet           the flat container's version list
  Maven Central   maven-metadata.xml
  PyPI            the JSON API for the version, not yanked
  RubyGems        the versions API: the source gem and every platform gem
  npm             the root package and every platform package, the WASI one included
  Go              the module proxy's go/vX.Y.Z
  Packagist       the package's versions
  GitHub Release  the PIE zips, the two Swift zips, the C archives and the R tarball

CRAN is never queried: its review takes up to ten business days, and the consumer's own CRAN
submission follows the companion's by hand. The gate fails listing every missing item; a probe
that errors, or answers 200 with a body it can't read, is listed too, never passed.

The GitHub Release probe assumes a public companion: `github.token` can't see another private
repository, so a private companion's release would read as missing.

  companion_gate.py --root <consumer>                        gate every companion in the block
  companion_gate.py --crate X --version V [--repo O/N]       probe one version
  companion_gate.py --record --crate X --version V [--repo]  print a fixture of the responses
  companion_gate.py --root <consumer> --r-package            the R jobs' url:: reference to the
                                                             companion's GitHub Release tarball
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import sys
import types
import urllib.error
import urllib.request
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c_package  # noqa: E402
import hyper_config  # noqa: E402
import php_ext_package  # noqa: E402

OWNER = 'SkunkWerkx'
NPM_ACCEPT = 'application/vnd.npm.install-v1+json'
GITHUB_API = 'https://api.github.com'
ABSENT = (404, 410)


def fetch(url):
    """(status, body) of a GET; an HTTP error is a status, a network error status 0."""
    headers = {'User-Agent': 'HyperForge-companion-gate'}
    token = None
    if url.startswith('https://registry.npmjs.org/'):
        headers['Accept'] = NPM_ACCEPT
    if url.startswith(GITHUB_API + '/'):
        headers['Accept'] = 'application/vnd.github+json'
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    request = urllib.request.Request(url, headers=headers)
    if token:
        # Unredirected: urllib copies ordinary headers to a redirect's target, wherever it is.
        request.add_unredirected_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode('utf-8')
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode('utf-8', 'replace')
    except (urllib.error.URLError, OSError) as error:  # TimeoutError and a reset read included
        return 0, str(error)


class Probe(NamedTuple):
    where: str
    url: str
    # What a recorded fixture keeps of a 200 body: only what judge reads.
    trim: Callable[[str], str]
    # The missing items, given a 200 body, or given None when the registry answered 404/410.
    judge: Callable[[str | None], list[str]]


def sparse_index_path(crate):
    if len(crate) <= 2:
        return f'{len(crate)}/{crate}'
    if len(crate) == 3:
        return f'3/{crate[0]}/{crate}'
    return f'{crate[:2]}/{crate[2:4]}/{crate}'


def go_proxy_path(module):
    """The module proxy's case encoding: each capital becomes ! and its lowercase."""
    return re.sub('[A-Z]', lambda m: '!' + m.group(0).lower(), module)


def _json(trim):
    return lambda body: json.dumps(trim(json.loads(body)), separators=(',', ':'))


def release_assets(crate, project, version):
    """Every asset a companion's GitHub Release must carry for a consumer's release."""
    return (php_ext_package.release_asset_names(f'v{version}', crate)
            + [f'{project}Core.artifactbundle.zip', f'{project}CoreApple.xcframework.zip']
            + list(c_package.asset_names(types.SimpleNamespace(crate=crate), version))
            + [f'{crate}_{version}.tar.gz'])


def probes(crate, version, repo):
    project = repo.split('/', 1)[1]
    scope = OWNER.lower()

    def crates(body):
        if body is None:
            return [f'crates.io: {crate} {version}']
        lines = [json.loads(line) for line in body.splitlines() if line.strip()]
        found = [line for line in lines if line['vers'] == version]
        if not found:
            return [f'crates.io: {crate} {version}']
        return [f'crates.io: {crate} {version} is yanked'] if all(line['yanked'] for line in found) else []

    def nuget(body):
        live = body is not None and version in json.loads(body)['versions']
        return [] if live else [f'NuGet: {project} {version}']

    def maven(body):
        versions = re.findall(r'<version>([^<]+)</version>', body or '')
        return [] if version in versions else [f'Maven Central: io.github.{scope}:{crate}:{version}']

    def pypi(body):
        if body is None or json.loads(body)['info']['version'] != version:
            return [f'PyPI: {crate} {version}']
        return [f'PyPI: {crate} {version} is yanked'] if json.loads(body)['info'].get('yanked') else []

    def rubygems(body):
        have = {(v['number'], v['platform']) for v in json.loads(body)} if body is not None else set()
        platforms = ['ruby'] + list(hyper_config.RUBY_PLATFORMS.values())
        return [f'RubyGems: {crate} {version} ({p})' for p in platforms if (version, p) not in have]

    def npm(name):
        def judge(body):
            live = body is not None and version in json.loads(body).get('versions', {})
            return [] if live else [f'npm: @{scope}/{name}@{version}']
        return judge

    module = f'github.com/{repo}/go'

    def go(body):
        live = body is not None and json.loads(body).get('Version') == f'v{version}'
        return [] if live else [f'Go: {module}@v{version}']

    package = f'{scope}/{crate}'

    def packagist(body):
        entries = json.loads(body)['packages'].get(package, []) if body is not None else []
        live = any(entry.get('version', '').lstrip('v') == version for entry in entries)
        return [] if live else [f'Packagist: {package} v{version}']

    def release(body):
        if body is None:
            return [f'GitHub Release: {repo} has no release v{version} (PIE zips, Swift zips, C archives, R tarball)']
        have = {asset['name'] for asset in json.loads(body)['assets']}
        return [f'GitHub Release: {repo} v{version} lacks {name}' for name in release_assets(crate, project, version)
                if name not in have]

    npm_names = [crate] + [f'{crate}-{p}' for p in hyper_config.NODE_PLATFORMS.values()] + \
        [f'{crate}-{hyper_config.NODE_WASI_PLATFORM}']
    return [
        Probe('crates.io', f'https://index.crates.io/{sparse_index_path(crate)}',
              lambda body: '\n'.join(json.dumps({k: json.loads(line)[k] for k in ('name', 'vers', 'yanked')},
                                                separators=(',', ':'))
                                     for line in body.splitlines() if line.strip()), crates),
        Probe('NuGet', f'https://api.nuget.org/v3-flatcontainer/{crate}/index.json',
              _json(lambda d: {'versions': d['versions']}), nuget),
        Probe('Maven Central', f'https://repo1.maven.org/maven2/io/github/{scope}/{crate}/maven-metadata.xml',
              lambda body: '<metadata><versioning><versions>' + ''.join(
                  f'<version>{v}</version>' for v in re.findall(r'<version>([^<]+)</version>', body))
              + '</versions></versioning></metadata>', maven),
        Probe('PyPI', f'https://pypi.org/pypi/{crate}/{version}/json',
              _json(lambda d: {'info': {'version': d['info']['version'], 'yanked': d['info'].get('yanked', False)}}),
              pypi),
        Probe('RubyGems', f'https://rubygems.org/api/v1/versions/{crate}.json',
              _json(lambda d: [{'number': v['number'], 'platform': v['platform']} for v in d]), rubygems),
        *[Probe('npm', f'https://registry.npmjs.org/@{scope}%2f{name}',
                _json(lambda d: {'name': d['name'], 'versions': {v: {} for v in d.get('versions', {})}}), npm(name))
          for name in npm_names],
        Probe('Go', f'https://proxy.golang.org/{go_proxy_path(module)}/@v/v{version}.info',
              _json(lambda d: {'Version': d['Version']}), go),
        Probe('Packagist', f'https://repo.packagist.org/p2/{package}.json',
              _json(lambda d: {'packages': {k: [{'version': e['version']} for e in v]
                                            for k, v in d['packages'].items()}}), packagist),
        Probe('GitHub Release', f'{GITHUB_API}/repos/{repo}/releases/tags/v{version}',
              _json(lambda d: {'tag_name': d['tag_name'], 'assets': [{'name': a['name']} for a in d['assets']]}),
              release),
    ]


def repo_lookup_url(crate):
    return f'{GITHUB_API}/repos/{OWNER}/{crate}'


def resolve_repo(crate, http):
    """The owner's repository for a crate, in GitHub's own casing (the Go proxy is case-sensitive)."""
    url = repo_lookup_url(crate)
    status, body = http(url)
    if status in ABSENT:
        raise ValueError(f'no repository {OWNER}/{crate} (HTTP {status} from {url}): pass --repo')
    if status == 0:
        raise ValueError(f'looking up {OWNER}/{crate} failed (network: {body}) from {url}; retry, or pass --repo')
    if status != 200:
        raise ValueError(f'looking up {OWNER}/{crate} failed, HTTP {status} from {url}; retry, or pass --repo')
    try:
        return json.loads(body)['full_name']
    except (ValueError, KeyError, TypeError):
        raise ValueError(f'looking up {OWNER}/{crate} failed, unreadable 200 from {url}; pass --repo') from None


def check(crate, version, http=fetch, repo=None):
    """Every item of `crate` `version` that is not live, on every registry but CRAN; empty when
    the release may resolve it. `http(url) -> (status, body)` is injected for the tests."""
    repo = repo or resolve_repo(crate, http)
    missing = []
    for probe in probes(crate, version, repo):
        status, body = http(probe.url)
        if status == 200:
            try:
                missing += probe.judge(body)
            except (ValueError, KeyError, TypeError, AttributeError):  # an HTML page, a changed shape
                missing.append(f'{probe.where}: probe failed, unreadable 200 from {probe.url}')
        elif status in ABSENT:
            missing += probe.judge(None)
        elif status == 0:
            missing.append(f'{probe.where}: probe failed (network: {body}) from {probe.url}')
        else:
            missing.append(f'{probe.where}: probe failed, HTTP {status} from {probe.url}')
    return missing


def record(crate, version, http=fetch, repo=None):
    """A fixture: each probe's response, a 200 trimmed to what the probe reads."""
    responses = {}
    if repo is None:
        status, body = http(repo_lookup_url(crate))
        responses[repo_lookup_url(crate)] = {'status': status, 'body': _json(lambda d: {'full_name': d['full_name']})(body)
                                             if status == 200 else ''}
        repo = json.loads(body)['full_name']
    for probe in probes(crate, version, repo):
        status, body = http(probe.url)
        responses[probe.url] = {'status': status, 'body': probe.trim(body) if status == 200 else ''}
    return {'crate': crate, 'version': version, 'repo': repo,
            'recorded': datetime.date.today().isoformat(), 'responses': responses}


# The consumer manifests a companion requirement lives in (the companion-pins plan, Task 7).
MANIFESTS = ('rust/Cargo.toml', 'go/go.mod', 'python/pyproject.toml', 'typescript/package.json',
             'ruby/*.gemspec', 'java/build.gradle.kts', 'csharp/Directory.Build.props', 'composer.json',
             'Package.swift', 'r/DESCRIPTION')
VERSION = r'v?(\d+(?:\.\d+){0,2})'


def lower_bound(spec):
    """The X.Y.Z lower bound of one requirement: `~> 1.1`, `>= 1.1.0`, `^1.1.0`, `=1.1.0`, a bare
    version (Cargo's caret) and comma-joined upper bounds beside them. One lower bound, or several
    that are all `>=` or `~>` (Ruby's `"~> 1.1", ">= 1.1.2"`), which give the highest."""
    bounds = []
    for part in (p.strip() for p in str(spec).split(',')):
        if not part:
            continue
        lower = re.fullmatch(rf'(>=|===?|~>|~=|\^|~|=)?\s*{VERSION}', part)
        if lower:
            numbers = lower.group(2).split('.')
            bounds.append((lower.group(1), tuple(int(n) for n in numbers + ['0'] * (3 - len(numbers)))))
        elif not re.fullmatch(rf'(?:<|<=|!=)\s*{VERSION}', part):
            raise ValueError(f'{spec!r}: {part!r} is not a version requirement the gate reads')
    if not bounds:
        raise ValueError(f'{spec!r} has no lower bound')
    if len(bounds) > 1 and any(op not in ('>=', '~>') for op, _ in bounds):
        raise ValueError(f'{spec!r} has {len(bounds)} lower bounds: use one, or only >= and ~> ones')
    return '.'.join(str(n) for n in max(version for _, version in bounds))


def _specs(path, relative, crate, repo):
    """The requirement strings one manifest puts on the companion."""
    owner, project = repo.split('/', 1)
    scope = owner.lower()
    text = path.read_text(encoding='utf-8')
    if relative == 'rust/Cargo.toml':
        return [a or b for a, b in re.findall(
            rf'^\s*{re.escape(crate)}\s*=\s*(?:"([^"]+)"|\{{[^}}\n]*\bversion\s*=\s*"([^"]+)")', text, re.M)]
    if relative == 'go/go.mod':
        return re.findall(rf'(?<![\w./-]){re.escape(f"github.com/{repo}/go")}\s+(v\S+)', text)
    if relative == 'python/pyproject.toml':
        return re.findall(rf'["\']{re.escape(crate)}(?:\[[^\]]*\])?\s*([<>=!~^][^"\';]*)', text)
    if relative == 'typescript/package.json':
        data = json.loads(text)
        return [data[s][f'@{scope}/{crate}'] for s in ('dependencies', 'peerDependencies', 'devDependencies',
                                                        'optionalDependencies')
                if f'@{scope}/{crate}' in data.get(s, {})]
    if relative.endswith('.gemspec'):
        return [', '.join(re.findall(r'["\']([^"\']+)["\']', rest)) for rest in re.findall(
            rf'add_(?:runtime_|development_)?dependency\s*\(?\s*["\']{re.escape(crate)}["\']((?:\s*,\s*["\'][^"\']+["\'])+)',
            text)]
    if relative == 'java/build.gradle.kts':
        return re.findall(rf'"io\.github\.{re.escape(scope)}:{re.escape(crate)}:([^"]+)"', text)
    if relative.startswith('csharp/'):
        return (re.findall(rf'<{re.escape(project)}Version>\s*([^<\s]+)\s*</{re.escape(project)}Version>', text)
                + re.findall(rf'<PackageReference\s+Include="{re.escape(project)}"\s+Version="([^"$]+)"', text))
    if relative == 'composer.json':
        data = json.loads(text)
        return [data[s][k] for s in ('require', 'require-dev') for k in (f'ext-{crate}', f'{scope}/{crate}')
                if k in data.get(s, {})]
    if relative == 'Package.swift':
        return re.findall(rf'\.package\(\s*url:\s*"https://github\.com/{re.escape(repo)}(?:\.git)?"\s*,\s*'
                          rf'(?:from:\s*|exact:\s*|\.upToNextM(?:ajor|inor)\(\s*from:\s*)"([^"]+)"', text)
    if relative == 'r/DESCRIPTION':
        return re.findall(rf'(?<![\w.]){re.escape(crate)}\s*\(\s*([<>=]=?\s*[^)]+?)\s*\)', text)
    raise AssertionError(relative)


def requirements(root, crate, repo):
    """(manifest, requirement) for every requirement the consumer's manifests put on the
    companion. An absent manifest is skipped; a present one that names none is an error."""
    root = Path(root)
    found = []
    for pattern in MANIFESTS:
        paths = sorted(root.glob(pattern))
        if pattern == 'csharp/Directory.Build.props' and paths:
            # The version may also be a literal PackageReference in a project.
            paths += sorted(root.glob('csharp/**/*.csproj'))
        specs = [(p.relative_to(root).as_posix(), s) for p in paths
                 for s in _specs(p, p.relative_to(root).as_posix(), crate, repo)]
        if paths and not specs:
            raise ValueError(f'{paths[0].relative_to(root).as_posix()} names no requirement on {crate}')
        found += specs
    return found


def companions(root):
    block = hyper_config.load_companions(root)
    if not block:
        raise ValueError(f"no companions block in {hyper_config.CONFIG_PATH}: a repository without companions "
                         "doesn't call hyper-companions.yml")
    return block


def required_version(root, name=None):
    """The one version every manifest's requirement on the companion starts at."""
    block = companions(root)
    if name is None:
        if len(block) != 1:
            raise ValueError(f'{len(block)} companions: name the one to read')
        name = next(iter(block))
    reqs = requirements(root, name, block[name].repo)
    if not reqs:
        raise ValueError(f'no manifest requires {name}')
    bounds = []
    for path, spec in reqs:
        try:
            bounds.append((path, lower_bound(spec)))
        except ValueError as error:
            raise ValueError(f'{path}: {error}') from None
    if len({version for _, version in bounds}) != 1:
        raise ValueError(f'the manifests require different {name} versions: '
                         + ', '.join(f'{path} {version}' for path, version in bounds))
    return bounds[0][1]


def r_release_package(root, name=None):
    """R in release mode: the companion's GitHub Release tarball (the R source package
    finalize-release attaches, which release_assets requires) at the version the manifests
    require, as the url:: reference the R jobs add to setup-r-dependencies. CRAN is never asked."""
    block = companions(root)
    if name is None:
        if len(block) != 1:
            raise ValueError(f'{len(block)} companions: name the one to read')
        name = next(iter(block))
    version = required_version(root, name)
    return f'url::https://github.com/{block[name].repo}/releases/download/v{version}/{name}_{version}.tar.gz'


def report(crate, version, missing):
    if missing:
        print(f'::error::{crate} {version} is not live everywhere a release resolves it (CRAN is not checked); '
              f'{len(missing)} missing:')
        for item in missing:
            print(f'  {item}')
    else:
        print(f'{crate} {version} is live on every registry but CRAN, which is not checked')


def main(argv=None, http=fetch):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--crate')
    parser.add_argument('--version')
    parser.add_argument('--repo')
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--r-package', action='store_true',
                        help='with --root: print `package=<url:: reference>`, the step output the R jobs add to '
                             'setup-r-dependencies in release mode')
    args = parser.parse_args(argv)
    if args.r_package:
        if args.root is None:
            parser.error('--r-package reads the manifests: give --root')
        print(f'package={r_release_package(args.root)}')
        return 0
    if args.root is None:
        if not args.crate or not args.version:
            parser.error('--root, or --crate and --version')
        if args.record:
            print(json.dumps(record(args.crate, args.version, http, args.repo), indent=2))
            return 0
        missing = check(args.crate, args.version, http, args.repo)
        report(args.crate, args.version, missing)
        return 1 if missing else 0
    failed = False
    for name, companion in companions(args.root).items():
        version = required_version(args.root, name)
        missing = check(name, version, http, companion.repo)
        report(name, version, missing)
        failed = failed or bool(missing)
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ValueError as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
