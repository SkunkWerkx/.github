"""A run's companion mode, from the event that triggered it.

A consumer's ci.yml is a caller, so a reusable workflow sees an empty `inputs.purpose`; the
dispatch input is read from the event instead. A dispatch with `purpose=release` is release
mode: companions resolve from their registries at the versions the manifests require. Every
other event (and `purpose=board`) is companion mode: the pinned commit and its run. A dispatch
whose purpose is missing or unknown is refused, never guessed.
"""
import json
import os
import sys

PURPOSES = {'board': 'companion', 'release': 'release'}
# The same rule as a workflow expression, for the structural tests that pin the YAML to it.
MODE_RELEASE = "github.event_name == 'workflow_dispatch' && github.event.inputs.purpose == 'release'"


def companion_mode(event_name, event):
    if event_name != 'workflow_dispatch':
        return 'companion'
    purpose = (event.get('inputs') or {}).get('purpose')
    if purpose not in PURPOSES:
        raise ValueError(f"workflow_dispatch needs inputs.purpose 'board' or 'release', got {purpose!r}")
    return PURPOSES[purpose]


def main():
    with open(os.environ['GITHUB_EVENT_PATH']) as handle:
        event = json.load(handle)
    value = companion_mode(os.environ['GITHUB_EVENT_NAME'], event)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'companion_mode={value}\n')
    print(f'companion_mode={value}')


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
