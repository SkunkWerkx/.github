#!/usr/bin/env python3
"""Settle the release headings at the version, in the prepare workflow, before the push.

`## [Unreleased]` in CHANGELOG.md becomes `## [X.Y.Z] — <date>` and r/NEWS.md's heading becomes
`# <crate> X.Y.Z`; each is a no-op when the owner already renamed it by hand. The section must
then exist and hold no ⟨placeholder⟩ (the finalizer publishes it verbatim into the GitHub
Release), so a dispatch with nothing to say fails here, with nothing built and nothing pushed,
rather than at the tag. Nothing is written when the check refuses.

usage: release_headings.py --version X.Y.Z [--date YYYY-MM-DD] [--root .]
"""
import argparse
import datetime
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hyper_config  # noqa: E402
from release_guard import changelog_section  # noqa: E402

CHANGELOG = 'CHANGELOG.md'
NEWS = 'r/NEWS.md'
PLACEHOLDER = '⟨'


def rename_unreleased(text, version, date):
    """`## [Unreleased]` as `## [version] — date`; unchanged when the version already heads a section."""
    if re.search(rf'^## \[{re.escape(version)}\]', text, re.M):
        return text
    renamed, count = re.subn(r'^## \[Unreleased\][^\n]*$', f'## [{version}] — {date}', text, count=1, flags=re.M)
    if not count:
        raise ValueError(f'{CHANGELOG} has no [Unreleased] or [{version}] section')
    return renamed


def rename_news(text, crate, version):
    """`# <crate> (development version)` as `# <crate> <version>`; unchanged when the top heading
    already reads so. A top heading naming another release is refused, never relabelled: it is
    the last release's, and its entries would become this one's in the CRAN tarball."""
    heading = re.search(rf'^# {re.escape(crate)} (\S.*)$', text, re.M)
    if not heading:
        raise ValueError(f'{NEWS} has no "# {crate} (development version)" heading')
    if heading[1] == version:
        return text
    if heading[1] != '(development version)':
        raise ValueError(f'{NEWS} names {heading[1]}, not {version}; add a "# {crate} (development version)" '
                         f'heading with this release\'s entries above it')
    return text[:heading.start()] + f'# {crate} {version}' + text[heading.end():]


def settle(root, crate, version, date):
    """Rewrite both files at the version and check the section; the paths that changed."""
    root = Path(root)
    changelog = (root / CHANGELOG).read_text(encoding='utf-8')
    settled = {CHANGELOG: rename_unreleased(changelog, version, date)}
    if (root / NEWS).is_file():
        settled[NEWS] = rename_news((root / NEWS).read_text(encoding='utf-8'), crate, version)
    section = changelog_section(settled[CHANGELOG], version)
    if not section:
        raise ValueError(f'{CHANGELOG} "## [{version}]" section is empty; write the bullets before preparing')
    if PLACEHOLDER in section:
        raise ValueError(f'{CHANGELOG} "## [{version}]" still holds a ⟨placeholder⟩; fill it in before preparing')
    changed = []
    for relative, text in settled.items():
        if (root / relative).read_text(encoding='utf-8') != text:
            (root / relative).write_text(text, encoding='utf-8')
            changed.append(relative)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', required=True)
    parser.add_argument('--date', default=datetime.date.today().isoformat())
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', args.version):
        raise ValueError(f'{args.version!r} is not a bare X.Y.Z version')
    changed = settle(args.root, hyper_config.load(args.root).crate, args.version, args.date)
    print('\n'.join(f'settled {path}' for path in changed) or f'headings already at {args.version}')


if __name__ == '__main__':
    main()
