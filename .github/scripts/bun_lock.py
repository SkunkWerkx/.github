"""Bun's text lockfile, read: JSON with trailing commas, its `packages` a map of

    "<key>": ["<name>@<version>", "<registry or empty>", {<metadata>}, "<integrity>"]    registry
    "<key>": ["<name>@<path>", {<metadata>}, "<integrity>"]                              tarball

where <key> is the package's name, or `<parent>/<name>` for a nested copy. `entry` gives one
package in the shape an npm lock has it, so a proof reads either the same way.
"""
import json
from pathlib import Path
import re

LOCK = 'bun.lock'
_PATH = re.compile(r'^(file:|link:|\.{0,2}/|[A-Za-z]:[\\/])')


def present(directory):
    return (Path(directory) / LOCK).is_file()


def parse(text):
    """The lock's JSON: every comma before a closing bracket dropped, outside strings."""
    out = []
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in '}]':
            while out and out[-1].isspace():
                out.pop()
            if out and out[-1] == ',':
                out.pop()
        out.append(char)
    return json.loads(''.join(out))


def read(directory):
    return parse((Path(directory) / LOCK).read_text(encoding='utf-8'))


def describe(value):
    """(name, version or None, resolved, integrity or None) of a `packages` value, resolved a
    registry's URL (empty for the default) or `file:<path>` for a tarball."""
    head = value[0]
    at = head.index('@', 1)
    name, spec = head[:at], head[at + 1:]
    integrity = next((v for v in value[1:] if isinstance(v, str) and v.startswith('sha')), None)
    if _PATH.match(spec):
        path = spec[len('file:'):] if spec.startswith('file:') else spec
        return name, None, f'link:{path[len("link:"):]}' if spec.startswith('link:') else f'file:{path}', integrity
    registry = value[1] if len(value) > 1 and isinstance(value[1], str) else ''
    return name, spec, registry, integrity


def entries(lock):
    """Every locked package: (key, name, version, resolved, integrity)."""
    for key, value in sorted((lock.get('packages') or {}).items()):
        yield (key, *describe(value))


def entry(directory, package):
    """The top-level copy of `package`, as npm's lock would hold it, or None."""
    value = (read(directory).get('packages') or {}).get(package)
    if value is None:
        return None
    _, version, resolved, integrity = describe(value)
    return {'version': version, 'resolved': resolved, 'integrity': integrity}


def version(directory, package):
    """The exact version the lock resolved `package` to, or None."""
    found = entry(directory, package)
    return found['version'] if found else None
