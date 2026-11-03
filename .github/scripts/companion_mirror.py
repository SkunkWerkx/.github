"""What hyper-companions.yml mirrors from a pinned companion run, and the seal it puts on it.

  companion_mirror.py plan --root <consumer>
      The companion, its pin, the repository name the token is minted for, and the
      download-artifact pattern matching every artifact any leg takes
      (companion_map.all_artifacts), as step outputs.
  companion_mirror.py seal --root <consumer> --dir <mirror> --outcome <download step outcome>
      Every artifact must have arrived with files in it; then SHA256SUMS (sha256sum's format,
      paths relative to the mirror) and pin.json ({"repo", "commit", "run"}) are written beside
      them. A failed download or an absent artifact names the pinned run and what to do.

One companion: the download is one actions/download-artifact step, and a step can't loop. A
second companion needs a second set of steps in the workflow, and this refuses until then.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion_map  # noqa: E402
import hyper_config  # noqa: E402

SUMS = 'SHA256SUMS'
PIN = 'pin.json'


def companion(root):
    block = hyper_config.load_companions(root)
    if not block:
        raise ValueError(f"no companions block: a repository without companions doesn't call hyper-companions.yml "
                         f"({hyper_config.CONFIG_PATH})")
    if len(block) != 1:
        raise ValueError(f'hyper-companions.yml mirrors one companion; {hyper_config.CONFIG_PATH} pins '
                         f'{len(block)} ({", ".join(sorted(block))})')
    return next(iter(block.values()))


def expected(root):
    return sorted(companion_map.all_artifacts(hyper_config.HyperConfig.load(root)))


def pattern(names):
    """A minimatch pattern matching exactly these names (a one-name brace set is literal)."""
    names = sorted(names)
    return names[0] if len(names) == 1 else '{' + ','.join(names) + '}'


def plan(root):
    pin = companion(root)
    return {'name': pin.name, 'repo': pin.repo, 'commit': pin.commit, 'run': pin.run,
            'repositories': pin.repo.split('/', 1)[1], 'pattern': pattern(expected(root))}


def rerun(pin):
    return f're-run `{pin.repo}` CI at `{pin.commit}` with `purpose=board` and move the pin'


def seal(root, directory, outcome):
    pin = companion(root)
    directory = Path(directory)
    if outcome != 'success':
        raise ValueError(f'downloading the artifacts of {pin.repo} run {pin.run} failed ({outcome}): '
                         f'they expired or the run lacks them; {rerun(pin)}')
    absent = [name for name in expected(root)
              if not (directory / name).is_dir() or not any(p.is_file() for p in (directory / name).rglob('*'))]
    if absent:
        raise ValueError(f'{pin.repo} run {pin.run} lacks {len(absent)} artifact(s) a leg takes: '
                         f'{", ".join(absent)}; {rerun(pin)}')
    files = sorted((p for p in directory.rglob('*')
                    if p.is_file() and not (p.parent == directory and p.name in (SUMS, PIN))),
                   key=lambda p: p.relative_to(directory).as_posix())
    lines = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(directory).as_posix()}' for p in files]
    (directory / SUMS).write_text(''.join(line + '\n' for line in lines), encoding='utf-8')
    (directory / PIN).write_text(json.dumps({'repo': pin.repo, 'commit': pin.commit, 'run': pin.run}) + '\n',
                                 encoding='utf-8')
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('plan', 'seal'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--dir', type=Path)
    parser.add_argument('--outcome', default='')
    args = parser.parse_args(argv)
    if args.mode == 'plan':
        lines = ''.join(f'{key}={value}\n' for key, value in plan(args.root).items())
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write(lines)
        print(lines, end='')
        return 0
    if args.dir is None:
        parser.error('seal needs --dir')
    print(f'sealed {len(seal(args.root, args.dir, args.outcome))} file(s) in {args.dir}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ValueError as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
