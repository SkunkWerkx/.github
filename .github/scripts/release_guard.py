"""The release's A (build source) -> B (staged bytes) checks. No rebuilding.

Only the generated paths already staged by the consumer's stage-native-binaries workflow may
change between A and B, and, with `swift_remote`, the root Package.swift's three release lines
(swift_release.py), which must name the Swift zips the CI run built. Build provenance names A; packages assembled by release.yml name B.
Every path, name and target comes from the consumer's `.github/hyper.json` (`hyper_config`);
the one project-specific piece, the C program that proves the ABI of a linked payload, is the
consumer's `abi_probe` file, compiled here against the Swift bundle's header.

Modes, all but `staged` run from the consumer's checkout:
  record    at release entry: read B's trailers, check tag, versions, tree, CI run and CHANGELOG
  run       re-check a CI run id against A, waiting up to RUN_WAIT_SECONDS for it to complete
  staged    print B when master is already A's staging commit, else master must be A (API only)
  master    origin/master must still be A
  index     the staging index (A + copied payloads) is exactly the artifact bytes
  payloads  every artifact payload is present, exports the ABI and passes the probe
  compare   payloads, plus provenance and byte equality with the committed tree
  paths     print the committed payload paths, one per line (release_inventory --natives)
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hyper_config  # noqa: E402
import swift_release  # noqa: E402

FORGE = 'SkunkWerkx/.github'
LIBRARY_SUFFIXES = ('.a', '.lib', '.so', '.dll', '.dylib')


def command(args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def git(root, *args):
    return command(['git', *args], cwd=root)


def api(repo, endpoint):
    return json.loads(command(['gh', 'api', f'repos/{repo}/{endpoint}']))


def config(root):
    return hyper_config.load(root)


def payload_paths(cfg, artifacts):
    return cfg.payload_paths(artifacts)


def require_master(root, sha):
    if git(root, 'rev-parse', 'origin/master') != sha:
        raise ValueError(f'master advanced beyond preparation source {sha}; prepare again')


def validate_run(run, sha, run_id, repo, completed=True):
    expected = {'id': int(run_id), 'head_sha': sha, 'head_branch': 'master',
                'path': '.github/workflows/ci.yml'}
    if completed:
        expected.update(status='completed', conclusion='success')
    for key, value in expected.items():
        if run.get(key) != value:
            raise ValueError(f'CI run {run_id}: expected {key}={value}, got {run.get(key)}')
    if run.get('event') not in ('workflow_dispatch', 'schedule'):
        raise ValueError('CI run must use the full release-mode matrix')
    if run.get('repository', {}).get('full_name', '').lower() != repo.lower():
        raise ValueError('CI run belongs to a different repository')


def await_run(repo, sha, run_id, timeout=0, interval=20, sleep=time.sleep, clock=time.monotonic):
    """The run once it has completed, waiting up to `timeout` seconds; then the full check.

    ci.yml's request-staging job dispatches staging from inside the run, so the run is still
    in progress when staging starts. Everything but completion is checked on the first read,
    so a wrong run fails at once instead of after the wait."""
    deadline = clock() + timeout
    while True:
        run = api(repo, f'actions/runs/{run_id}')
        validate_run(run, sha, run_id, repo, completed=False)
        if run.get('status') == 'completed' or clock() >= deadline:
            break
        sleep(interval)
    validate_run(run, sha, run_id, repo)
    return run


def staged_as(repo, sha):
    """B when master is already preparation source A's staging commit, None while it is A.

    Staging can be asked twice for one A: by ci.yml's request-staging job and, when a human
    dispatched that run, by workflow_run as well, or by a second ci.yml run on A. The second
    request finds B (A's only child, naming A as its source) and has nothing to do. Anything
    else on master fails exactly as before."""
    head = api(repo, 'commits/master')
    if head['sha'] == sha:
        return None
    parents = [parent['sha'] for parent in head.get('parents', [])]
    sources = re.findall(r'^Release-Source-SHA: (.*)$', head['commit']['message'], re.M)
    if parents == [sha] and sources == [sha]:
        return head['sha']
    raise ValueError(f'master advanced beyond preparation source {sha}; prepare again')


def version_at(root, sha):
    return re.search(r'^version = "([^"]+)"', git(root, 'show', f'{sha}:rust/Cargo.toml'), re.M)[1]


def changelog_section(text, version):
    """The body of `## [version]` in a Keep a Changelog file, or None when there is none."""
    match = re.search(rf'^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)', text, re.M | re.S)
    return (match[1].strip() or None) if match else None


def require_changelog(root, commit, version):
    # The finalizer writes the GitHub Release from this section after publication, which
    # cannot be undone, so a tag with nothing to say fails before anything is uploaded.
    try:
        text = git(root, 'show', f'{commit}:CHANGELOG.md')
    except subprocess.CalledProcessError:
        raise ValueError('CHANGELOG.md is missing from the staging commit') from None
    section = changelog_section(text, version)
    if not section:
        raise ValueError(f'CHANGELOG.md has no "## [{version}]" section; rename [Unreleased] before preparing')
    if '\u27e8' in section:
        # A ⟨placeholder⟩ is a figure the owner still has to measure and fill in; the
        # finalizer would publish it verbatim into the Release notes.
        raise ValueError(f'CHANGELOG.md "## [{version}]" still holds a \u27e8placeholder\u27e9; fill it in before tagging')
    return section


def unversioned_swift_pins(text):
    """The `.package(...)` declarations in a manifest that pin a revision or a branch."""
    return re.findall(r'\.package\([^)]*?\b(?:revision|branch)\s*:[^)]*\)', text, re.S)


def require_versioned_swift_dependencies(root, commit):
    # SwiftPM refuses a dependency on a revision or a branch to any consumer resolving a
    # tagged version of this package, so a pin kept while an upstream was unreleased would
    # ship a Swift package nobody can depend on.
    manifests = [p for p in git(root, 'ls-tree', '-r', '--name-only', commit).splitlines()
                 if p.rsplit('/', 1)[-1] == 'Package.swift']
    pinned = [f'{path}: {pin}' for path in manifests
              for pin in unversioned_swift_pins(git(root, 'show', f'{commit}:{path}'))]
    if pinned:
        raise ValueError('Package.swift pins a revision or branch; depend on a released version '
                         'before tagging:\n  ' + '\n  '.join(pinned))


def release_record(root, cfg, commit, tag, override, repo):
    message = git(root, 'show', '-s', '--format=%B', commit)

    def trailer(key, pattern):
        values = re.findall(rf'^{key}: (.*)$', message, re.M)
        if len(values) != 1 or not re.fullmatch(pattern, values[0]):
            raise ValueError(f'staging commit requires exactly one {key} trailer')
        return values[0]
    sha = trailer('Release-Source-SHA', '[0-9a-f]{40}')
    run_id = trailer('Release-CI-Run-ID', '[1-9][0-9]*')
    if git(root, 'show', '-s', '--format=%P', commit) != sha:
        raise ValueError('staging commit B must have exactly one parent, preparation source A')
    if override and override != run_id:
        raise ValueError('ci_run_id must equal the run recorded by the staging commit')
    want = version_at(root, commit)
    if tag and tag != f'v{want}':
        raise ValueError(f'tag {tag} does not match version {want}')
    if version_at(root, sha) != want:
        raise ValueError('preparation and staging versions differ')
    if tag:
        require_changelog(root, commit, want)
        require_versioned_swift_dependencies(root, commit)
        if cfg.swift_remote:
            named = swift_release.read(git(root, 'show', f'{commit}:{swift_release.MANIFEST}'))
            if named['version'] != want:
                raise ValueError(f'{swift_release.MANIFEST} names Swift release {named["version"]!r}, '
                                 f'not {want}; staging writes it')
    validate_transition(root, cfg, sha, commit)
    validate_staged_tree(root, cfg, commit)
    validate_run(api(repo, f'actions/runs/{run_id}'), sha, run_id, repo)
    return sha, run_id


def expected_mode(path):
    return '100755' if path.startswith('php/') else '100644'


def validate_transition(root, cfg, sha, commit):
    allowed = payload_paths(cfg, Path('artifacts'))
    changed = git(root, 'diff', '--name-only', '--no-renames', sha, commit).splitlines()
    for path in changed:
        if path in cfg.staged_edits:
            before, after = (git(root, 'show', f'{rev}:{path}') for rev in (sha, commit))
            if not swift_release.only_release_lines_differ(before, after):
                raise ValueError(f'{path} changed beyond its release lines between A and B')
            continue
        if path not in allowed:
            raise ValueError(f'unrelated A -> B change: {path}')
        entry = git(root, 'ls-tree', commit, '--', path).split()
        if not entry:
            raise ValueError(f'staged payload was removed: {path}')
        if entry[0] != expected_mode(path):
            raise ValueError(f'invalid staged file mode for {path}: {entry[0]}')


def validate_staging_index(root, cfg, sha):
    if git(root, 'rev-parse', 'HEAD') != sha:
        raise ValueError('staging index is not based on preparation source A')
    tree = git(root, 'write-tree')
    validate_transition(root, cfg, sha, tree)
    validate_staged_tree(root, cfg, tree)
    compare_payloads(root, payload_paths(cfg, root / 'artifacts'), tree)
    if cfg.swift_remote:
        check_swift_assets(root, cfg, git(root, 'show', f'{tree}:{swift_release.MANIFEST}'))


def validate_staged_tree(root, cfg, commit):
    """Validate every shipped path, including files unchanged by staging."""
    expected = payload_paths(cfg, Path('artifacts'))
    entries = {}
    for line in git(root, 'ls-tree', '-r', commit, '--', *cfg.payload_trees).splitlines():
        metadata, path = line.split('\t', 1)
        entries[path] = metadata.split()[0]
    for path in expected:
        if path not in entries:
            raise ValueError(f'missing staged payload: {path}')
        if entries[path] != expected_mode(path):
            raise ValueError(f'invalid staged file mode for {path}: {entries[path]}')
    for path in entries:
        if path not in expected and path not in cfg.source_paths:
            raise ValueError(f'unexpected staged payload: {path}')


def verify_attestation(path, sha, repo):
    subprocess.run(['gh', 'attestation', 'verify', str(path), '--repo', repo,
                    '--signer-repo', FORGE, '--source-digest', sha], check=True)


def compare_payloads(root, paths, tree=None):
    for dest, src in paths.items():
        if src.is_symlink() or (root / dest).is_symlink():
            raise ValueError(f'staged payload must be a regular file: {dest}')
        shipped = (subprocess.check_output(['git', 'show', f'{tree}:{dest}'], cwd=root) if tree
                   else (root / dest).read_bytes())
        if src.read_bytes() != shipped:
            raise ValueError(f'staged bytes do not match the recorded CI run: {dest}')


def swift_assets_dir(root):
    return root / 'artifacts' / 'swift-release-assets'


def check_swift_assets(root, cfg, manifest, sha=None, repo=None):
    """The CI run's Swift zips are the ones `manifest` (Package.swift) names, for the crate's
    version; with `sha`, each also carries the Forge's build provenance at A."""
    assets = swift_assets_dir(root)
    if sha:
        for name in cfg.swift_assets:
            verify_attestation(assets / name, sha, repo)
    swift_release.check(cfg, manifest, swift_release.crate_version(root), assets)


def core_exports(root, cfg=None):
    return (cfg or config(root)).core_exports(root)


def check_symbols(path, expected, cfg):
    # LLVM understands ELF, Mach-O, COFF and WASM archives. GNU nm cannot read them all.
    if path.suffix == '.dll':
        output = command(['llvm-readobj', '--coff-exports', str(path)])
        symbols = re.findall(r'^  Name: (\S+)$', output, re.M)
    else:
        flags = ['--dynamic'] if path.suffix == '.so' else []
        output = command(['llvm-nm', *flags, '--defined-only', '--extern-only', '--format=posix', str(path)])
        symbols = [line.split()[0] for line in output.splitlines() if len(line.split()) >= 2]
    names = set()
    for symbol in symbols:
        name = symbol.removeprefix('_')  # Mach-O C symbol decoration
        if cfg.is_export_symbol(name):
            names.add(name)
    if names != expected:
        raise ValueError(f'{path}: missing exports {sorted(expected - names)}; '
                         f'stale exports {sorted(names - expected)}')


def packed_version(version):
    return sum(int(n) << shift for n, shift in zip(version.split('.'), (16, 8, 0)))


def check_runtime(root, cfg, payload, version):
    """Compile the consumer's ABI probe against the bundle's header and link one payload.

    Each process loads or links just one payload, avoiding dynamic loader caching. The
    probe's `main` returns non-zero on any mismatch; `EXPECTED_VERSION` is the packed
    `MAJOR << 16 | MINOR << 8 | PATCH` the `<crate>_version` export returns."""
    probe = root / cfg.abi_probe
    if not probe.is_file():
        raise ValueError(f'ABI probe {cfg.abi_probe} is missing')
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / 'abi'
        subprocess.run(['cc', f'-DEXPECTED_VERSION={packed_version(version)}',
                        '-I', str(root / cfg.bundle / 'include'), str(probe),
                        str(payload.resolve()), '-ldl', '-lpthread', '-lm', '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


def validate_payloads(root, cfg, paths, sha, repo, compare=False, attest=True):
    exports = cfg.core_exports(root)
    version = re.search(r'^version = "([^"]+)"', (root / 'rust/Cargo.toml').read_text(), re.M)[1]
    # Check the complete explicit platform set, including generated metadata, before copying.
    for dest, src in paths.items():
        if not src.is_file() or src.is_symlink():
            raise ValueError(f'missing artifact payload: {src}')
        if src.suffix in LIBRARY_SUFFIXES:
            if attest:
                verify_attestation(src, sha, repo)
            check_symbols(src, exports, cfg)
    for dest in cfg.probe_payloads:
        check_runtime(root, cfg, paths[dest], version)
    if cfg.cdylib_smoke:
        shared = paths[cfg.probe_payloads[0]]
        subprocess.run([sys.executable, '-I', str(root / cfg.cdylib_smoke), str(shared),
                        str(root / 'rust/Cargo.toml')], check=True)
    if compare:
        compare_payloads(root, paths)
        if cfg.swift_remote:
            check_swift_assets(root, cfg, (root / swift_release.MANIFEST).read_text(), sha, repo)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('record', 'run', 'staged', 'master', 'index', 'payloads', 'compare', 'paths'))
    args = parser.parse_args()
    if args.mode == 'staged':
        # Run before anything is checked out: it reads master through the API alone.
        staged = staged_as(os.environ['GITHUB_REPOSITORY'], os.environ['SOURCE_SHA'])
        if staged:
            print(staged)
        return
    root = Path.cwd()
    cfg = config(root)
    if args.mode == 'paths':
        print('\n'.join(payload_paths(cfg, Path('artifacts'))))
        return
    repo, sha = os.environ['GITHUB_REPOSITORY'], os.environ.get('SOURCE_SHA', '')
    run_id = os.environ.get('RUN_ID', '')
    if args.mode == 'record':
        ref = os.environ.get('GITHUB_REF', '')
        tag = ref.removeprefix('refs/tags/') if ref.startswith('refs/tags/') else ''
        if not tag and os.environ.get('GITHUB_REF_TYPE') == 'tag':
            tag = os.environ['GITHUB_REF_NAME']
        sha, run_id = release_record(root, cfg, os.environ['GITHUB_SHA'], tag,
                                     os.environ.get('CI_RUN_ID_OVERRIDE', ''), repo)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'source_sha={sha}\nrun_id={run_id}\n')
    elif args.mode == 'run':
        await_run(repo, sha, run_id, int(os.environ.get('RUN_WAIT_SECONDS') or 0))
    elif args.mode == 'master':
        require_master(root, sha)
    elif args.mode == 'index':
        validate_staging_index(root, cfg, sha)
    else:
        # Staging's verify-and-copy step checks provenance once, after these ABI checks. At
        # release entry, verify it here while comparing the shipped bytes.
        validate_payloads(root, cfg, payload_paths(cfg, root / 'artifacts'), sha, repo,
                          compare=args.mode == 'compare', attest=args.mode == 'compare')


if __name__ == '__main__':
    main()
