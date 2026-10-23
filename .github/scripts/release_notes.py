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
    natives = [e for e in files if e.get('location') == 'source']
    return f'''## Verifying

Every file in this release verifies from the tag alone. One attestation, signed by this
repository's `release.yml` at `v{version}`, names all {len(files)} of them: every registry
package (NuGet, RubyGems, PyPI, crates.io and Maven Central) and the {len(natives)} native
libraries committed for Go, Swift and PHP. Its predicate is the release inventory attached
below.

```sh
gh attestation verify <file> --repo {repo} \\
  --source-ref refs/tags/v{version} --predicate-type {PREDICATE_TYPE}
```

The native libraries, the Python wheels and the archives inside the NuGet package and the
jar also carry build provenance from CI run [{run_id}](https://github.com/{repo}/actions/runs/{run_id})
at commit `{source[:7]}`, the parent of the tagged commit, signed by the Forge:

```sh
gh attestation verify <file> --repo {repo} --signer-repo {FORGE} --source-digest {source}
```

`inventory.json` lists every file with its size and SHA-256, the commit it was built from and
the repository that signed its build provenance; it carries build provenance of its own at
the tagged commit. PyPI additionally holds a PEP 740 publish attestation for every wheel.
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
