"""Write the GitHub Release body from the CHANGELOG and the release inventory.

The body is the CHANGELOG's `## [X.Y.Z]` section as written, followed by a generated
"Verifying" section naming the one-line command the release attestation answers to, the CI
run and source commit the build provenance names, and where the inventory is. The finalizer
runs this after publication, so release_guard.py `record` has already refused a tag whose
CHANGELOG lacks the section.

usage: release_notes.py --changelog CHANGELOG.md --inventory release-packages/inventory.json --output notes.md
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_guard import changelog_section  # noqa: E402
from release_inventory import FORGE, PREDICATE_TYPE  # noqa: E402


def verifying(inventory):
    repo, version = inventory['repository'], inventory['version']
    source, run_id = inventory['source_sha'], inventory['ci_run_id']
    files = inventory['files']
    natives = [e for e in files if e.get('location') == 'source' and 'signer_repo' in e]
    metadata = sum(e.get('location') == 'source' for e in files) - len(natives)
    # npm and CRAN ship only from consumers with those bindings; name what this release holds.
    groups = {e['path'].split('/')[0] for e in files if e.get('location') == 'packages'}
    registries = ['NuGet', 'RubyGems', 'PyPI', 'crates.io', 'Maven Central'] + ['npm'] * ('npm' in groups)
    packages = f"({', '.join(registries[:-1])} and {registries[-1]}), "
    if 'cran' in groups:
        packages += 'a CRAN source tarball the maintainer submits by hand, '
    # The napi-rs addons are built and attested by the Forge in CI like the native
    # libraries, and npm records its own provenance at publish time beside PyPI's PEP 740.
    npm = 'npm' in groups
    sdist = any(e['path'].startswith('pypi/') and e['path'].endswith('.tar.gz') for e in files)
    # A consumer with swift_remote attaches the Swift core to the Release as two zips, built
    # and attested by the Forge in CI, instead of committing it; Go commits no library at
    # all (its core is Go source), so such a release has no committed natives to name.
    assets = sorted(Path(e['path']).name for e in files if e['path'].startswith('github/'))
    staged = f', and the {metadata} metadata files staged beside\nthem' if metadata else ''
    attached = (f",\nand the Swift core's {' and '.join(f'`{name}`' for name in assets)}, attached below"
                if assets else '')
    # With c_assets, one C archive per platform, packed and signed at the tag like the gems.
    archives = sum(1 for e in files if e['path'].startswith('c/'))
    if archives:
        attached += f",\nand {archives} C archives, one per platform, attached below"
    # The PHP extension's PIE zips, one per platform and PHP minor, built and attested by the
    # Forge in CI like the native libraries; PIE downloads them from the Release.
    extensions = sum(1 for e in files if e['path'].startswith('php/'))
    if extensions:
        attached += f",\nand {extensions} PHP extension zips for PIE, one per platform and PHP minor, attached below"
    registry = (", and npm a provenance statement for every package, linking it to the\n"
                "publishing run of this repository's `release.yml`") if npm else ''
    named = f'every registry\npackage {packages}'
    if natives:
        named += f'the {len(natives)} native\nlibraries committed for Swift{staged}{attached}'
    elif attached:
        named = named.rstrip(', ') + ' ' + attached.lstrip(',\n')
    else:
        named = named.rstrip(', ')
    # What carries the Forge's build provenance from the CI run at A, in the order it is named
    # above: the committed libraries, the release assets, then the registry files.
    carriers = [*(['the native libraries'] if natives else []), *(['the Swift zips'] if assets else []),
                *(['the PHP extension zips'] if extensions else []),
                'the Python wheels' + (' and sdist' if sdist else ''),
                *(['the Node addons inside the npm platform packages'] if npm else [])]
    carried = ', '.join(carriers)
    carried = carried[0].upper() + carried[1:]
    return f'''## Verifying

Every file in this release verifies from the tag alone. One attestation, signed by this
repository's `release.yml` at `v{version}`, names all {len(files)} of them: {named}. Its predicate is the release inventory attached below.

```sh
gh attestation verify <file> --repo {repo} \\
  --source-ref refs/tags/v{version} --predicate-type {PREDICATE_TYPE}
```

{carried} and the archives inside the NuGet package and the
jar also carry build provenance from CI run [{run_id}](https://github.com/{repo}/actions/runs/{run_id})
at commit `{source[:7]}`, the parent of the tagged commit, signed by the Forge:

```sh
gh attestation verify <file> --repo {repo} --signer-repo {FORGE} --source-digest {source}
```

`inventory.json` lists every file with its size and SHA-256 and, for each file with build
provenance, the commit it was built from and the repository that signed it; it carries
build provenance of its own at the tagged commit. PyPI additionally holds a PEP 740 publish attestation for every wheel{' and the sdist' if sdist else ''}{registry}.
'''


def notes(changelog, inventory):
    version, repo = inventory['version'], inventory['repository']
    section = changelog_section(changelog, version)
    if not section:
        raise ValueError(f'CHANGELOG.md has no "## [{version}]" section')
    link = f'https://github.com/{repo}/blob/v{version}/CHANGELOG.md'
    return f'{section}\n\nThe full list is in [CHANGELOG.md]({link}).\n\n{verifying(inventory)}'


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--changelog', type=Path, default=Path('CHANGELOG.md'))
    parser.add_argument('--inventory', type=Path, default=Path('release-packages/inventory.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding='utf-8'))
    if inventory.get('schema', 1) < 2:
        raise ValueError('release notes need a schema 2 inventory')
    args.output.write_text(notes(args.changelog.read_text(encoding='utf-8'), inventory), encoding='utf-8')
    print(f'wrote {args.output}')


if __name__ == '__main__':
    main()
