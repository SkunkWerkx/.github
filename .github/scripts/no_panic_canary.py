"""The no-panic proof's negative control: a build with a function that can panic must fail the
proof, naming that function and nothing else.

  no_panic_canary.py --name <canary> [--root <dir>] -- <command...>

Runs <command> in <root> (default `.`) with RUSTFLAGS="--cfg <canary>", the cfg that compiles
the crate's canary in, and judges its output. A proof that has never rejected anything has not
been shown to check anything: the normal no-panic build passing proves nothing on its own if the
functions it should check were never linked, or the check stopped running. So this build must
fail (a pass means the proof checks nothing), its output must name the canary (a failure that
doesn't is a broken build, not a rejection), and it must name only the canary (any other name
is a real panic path the normal build should have caught).

RUSTFLAGS replaces the rustflags the crate's .cargo/config.toml sets for the target, so the
canary build runs with only the cfg.
"""
import argparse
import os
import re
import subprocess
import sys

DETECTED = re.compile(r'detected panic in function `([^`]+)`')
TAIL = 20


def judge(returncode, log, name):
    """(passed, message) for one canary build: its exit code and its merged output."""
    if returncode == 0:
        return False, f'a build with a panicking canary ({name}) passed the proof: the proof checks nothing'
    named = DETECTED.findall(log)
    others = sorted(set(n for n in named if n != name))
    if others:
        return False, f'the proof rejected other functions beside the canary: {", ".join(others)}'
    if name not in named:
        tail = '\n'.join(log.strip().splitlines()[-TAIL:])
        return False, f'the canary build failed for some other reason:\n{tail}'
    return True, f'ok: the proof rejected the canary ({name}), by name, and nothing else'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--name', required=True, help='the canary function, and the cfg that compiles it in')
    parser.add_argument('--root', default='.', help='the directory the command runs in')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='the no-panic build, after --')
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('no command after --')
    env = os.environ | {'RUSTFLAGS': f'--cfg {args.name}'}
    run = subprocess.run(command, cwd=args.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors='replace')
    ok, message = judge(run.returncode, run.stdout, args.name)
    if ok:
        print(message)
        return 0
    print(f'::error::no_panic_canary.py: {message}', file=sys.stderr)
    if run.returncode == 0 or DETECTED.search(run.stdout):
        print('\n'.join(run.stdout.strip().splitlines()[-TAIL:]), file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
