"""Just enough YAML for the Forge's own action.yml files, which have no PyYAML on the
validate runner by default: block mappings and sequences, folded (`>`) and literal (`|`)
scalars, flow scalars, comments. Not a general parser; the tests that use it fail loudly
on anything else, which is the point."""
import re


class Line:
    def __init__(self, indent, text):
        self.indent, self.text = indent, text


def _lines(text):
    out = []
    for raw in text.splitlines():
        stripped = raw.split(' #', 1)[0] if not raw.lstrip().startswith('#') else ''
        if raw.lstrip().startswith('#') or not stripped.strip():
            out.append(Line(len(raw) - len(raw.lstrip(' ')), ''))
            continue
        out.append(Line(len(raw) - len(raw.lstrip(' ')), raw))
    return out


def _scalar(value):
    value = value.strip()
    if value in ('true', 'false'):
        return value == 'true'
    if re.fullmatch(r'-?\d+', value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        inner = value[1:-1]
        return inner.replace('\\"', '"') if value[0] == '"' else inner.replace("''", "'")
    if value in ('~', 'null'):
        return None
    return value


def _block(raw_lines, start, indent):
    """Collect a block scalar's lines deeper than `indent`."""
    out = []
    i = start
    while i < len(raw_lines) and (raw_lines[i].strip() == '' or len(raw_lines[i]) - len(raw_lines[i].lstrip(' ')) > indent):
        out.append(raw_lines[i])
        i += 1
    while out and out[-1].strip() == '':
        out.pop()
    base = min((len(l) - len(l.lstrip(' ')) for l in out if l.strip()), default=0)
    return [l[base:] if l.strip() else '' for l in out], i


def load(text):
    raw = text.splitlines()
    pos = [0]

    def current():
        while pos[0] < len(raw):
            line = raw[pos[0]]
            if line.strip() == '' or line.lstrip().startswith('#'):
                pos[0] += 1
                continue
            return line
        return None

    def strip_comment(value):
        return re.sub(r'\s+#.*$', '', value) if not value.lstrip().startswith(('"', "'")) else value

    def parse_value(value, indent):
        value = strip_comment(value).strip()
        if value in ('|', '>', '|-', '>-'):
            lines, end = _block(raw, pos[0], indent)
            pos[0] = end
            joined = '\n'.join(lines) if value.startswith('|') else ' '.join(l if l else '\n' for l in lines).replace(' \n ', '\n\n')
            return joined.strip('\n') + ('' if value.endswith('-') else '\n')
        if value == '':
            line = current()
            if line is None:
                return None
            child = len(line) - len(line.lstrip(' '))
            if child <= indent and not line.lstrip().startswith('- '):
                return None
            return parse_node(child if child > indent else indent)
        if value.startswith('[') and value.endswith(']'):
            return [_scalar(v) for v in value[1:-1].split(',') if v.strip()]
        if value.startswith('{') and value.endswith('}'):
            out = {}
            for pair in re.findall(r'([\w-]+):\s*([^,}]+)', value):
                out[pair[0]] = _scalar(pair[1])
            return out
        return _scalar(value)

    def parse_node(indent):
        line = current()
        if line is None:
            return None
        if line.lstrip().startswith('- '):
            items = []
            while (line := current()) is not None:
                this = len(line) - len(line.lstrip(' '))
                if this != indent or not line.lstrip().startswith('- '):
                    break
                pos[0] += 1
                rest = line.lstrip()[2:]
                if re.match(r'^[\w.-]+:(\s|$)', rest) and not rest.startswith(('"', "'")):
                    # A mapping whose first key shares the dash's line.
                    raw[pos[0] - 1] = ' ' * (indent + 2) + rest
                    pos[0] -= 1
                    items.append(parse_node(indent + 2))
                else:
                    items.append(parse_value(rest, indent))
            return items
        mapping = {}
        while (line := current()) is not None:
            this = len(line) - len(line.lstrip(' '))
            if this < indent or line.lstrip().startswith('- '):
                break
            if this > indent:
                raise ValueError(f'unexpected indentation: {line!r}')
            match = re.match(r'^([\w.${}/ -]+?):(?:\s+(.*))?$', line.strip())
            if not match:
                raise ValueError(f'not a mapping entry: {line!r}')
            pos[0] += 1
            mapping[match[1]] = parse_value(match[2] or '', indent)
        return mapping

    return parse_node(0)
