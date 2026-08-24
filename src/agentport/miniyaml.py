import json
import re

from .errors import FormatError

MAX_YAML_LINES = 4000
MAX_FLOW_DEPTH = 32

_RESERVED_WORDS = {"true", "false", "null", "~"}
_NUMERIC_START_RE = re.compile(r"^[-+0-9.]")
_SAFE_PLAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _./()-]*$")
_KEY_LINE_RE = re.compile(r"""^("[^"]+"|'[^']+'|[^:#\[\]{}'"|\s][^:]*?)\s*:( |$)""")


class _Parser:
    def __init__(self, lines):
        self.lines = lines
        self.i = 0

    def cur(self):
        if self.i < len(self.lines):
            return self.lines[self.i]
        return None

    def advance(self):
        line = self.lines[self.i]
        self.i += 1
        return line

    def parse_root(self):
        first = self.cur()
        if first is None:
            return {}
        if first[1].startswith("- ") or first[1] == "-":
            return self.parse_list(first[0])
        return self.parse_map(first[0])

    def parse_node(self, indent):
        _, content = self.cur()
        if content.startswith("- ") or content == "-":
            return self.parse_list(indent)
        return self.parse_map(indent)

    def parse_block_after_key(self, key_indent):
        nxt = self.cur()
        if nxt is None:
            return None
        nindent, ncontent = nxt
        if nindent > key_indent:
            return self.parse_node(nindent)
        if nindent == key_indent and (ncontent.startswith("- ") or ncontent == "-"):
            return self.parse_list(nindent)
        return None

    def parse_map(self, indent):
        result = {}
        while True:
            entry = self.cur()
            if entry is None:
                break
            eindent, econtent = entry
            if eindent < indent:
                break
            if eindent > indent:
                raise FormatError(f"mini-yaml: unexpected indentation at {econtent!r}")
            m = _KEY_LINE_RE.match(econtent)
            if not m:
                raise FormatError(f"mini-yaml: cannot parse line: {econtent!r}")
            raw_key = m.group(1).strip()
            key = unquote_key(raw_key)
            rest = econtent[m.end(1):]
            if not rest.startswith(":"):
                raise FormatError(f"mini-yaml: malformed mapping line: {econtent!r}")
            value_part = rest[1:].strip()
            if key in result:
                raise FormatError(f"mini-yaml: duplicate key: {key}")
            self.advance()
            if value_part == "":
                result[key] = self.parse_block_after_key(indent)
            elif value_part[0] in "|>":
                if any(ch not in "|>+-" for ch in value_part):
                    result[key] = parse_scalar(value_part)
                else:
                    result[key] = self.parse_block_scalar(indent, value_part)
            else:
                result[key] = parse_scalar(value_part)
        return result

    def parse_block_scalar(self, key_indent, marker):
        style = marker[0]
        chomp = marker[1:]
        collected = []
        base_indent = None
        while True:
            entry = self.cur()
            if entry is None:
                break
            eindent, econtent = entry
            if eindent <= key_indent:
                break
            if base_indent is None:
                base_indent = eindent
            rel = eindent - base_indent
            collected.append((" " * rel + econtent) if rel > 0 else econtent)
            self.advance()
        dedented = collected
        if style == "|":
            body = "\n".join(dedented)
        else:
            paragraphs = []
            buf = []
            for ln in dedented:
                if ln == "":
                    if buf:
                        paragraphs.append(" ".join(buf))
                        buf = []
                    paragraphs.append("")
                else:
                    buf.append(ln.strip())
            if buf:
                paragraphs.append(" ".join(buf))
            body = "\n".join(paragraphs)
        if body:
            body += "\n"
        if chomp.strip() == "-":
            body = body.rstrip("\n")
        return body

    def parse_list(self, indent):
        items = []
        while True:
            entry = self.cur()
            if entry is None:
                break
            eindent, econtent = entry
            is_item = econtent.startswith("- ") or econtent == "-"
            if eindent != indent or not is_item:
                break
            rest = econtent[2:].strip() if econtent != "-" else ""
            if rest == "":
                self.advance()
                nxt = self.cur()
                if nxt is not None and nxt[0] > indent:
                    items.append(self.parse_node(nxt[0]))
                else:
                    items.append(None)
                continue
            if rest.startswith("- ") or rest == "-":
                self.lines[self.i] = (indent + 2, rest)
                items.append(self.parse_list(indent + 2))
            elif _KEY_LINE_RE.match(rest):
                virtual = indent + 2
                self.lines[self.i] = (virtual, rest)
                items.append(self.parse_map(virtual))
            else:
                self.advance()
                items.append(parse_scalar(rest))
        return items


def unquote_key(raw):
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return unescape_double(raw[1:-1])
    return raw


def strip_inline_comment(value):
    out = []
    quote = None
    i = 0
    while i < len(value):
        c = value[i]
        if quote:
            out.append(c)
            if quote == "'" and c == "'":
                if i + 1 < len(value) and value[i + 1] == "'":
                    out.append("'")
                    i += 1
                else:
                    quote = None
            elif quote == '"':
                if c == "\\":
                    if i + 1 < len(value):
                        out.append(value[i + 1])
                        i += 1
                elif c == '"':
                    quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            out.append(c)
        elif c == "#" and (not out or out[-1] in (" ", "\t")):
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).rstrip()


def split_flow(value):
    parts = []
    depth = 0
    quote = None
    buf = []
    i = 0
    while i < len(value):
        c = value[i]
        if quote:
            buf.append(c)
            if quote == '"' and c == "\\" and i + 1 < len(value):
                buf.append(value[i + 1])
                i += 1
            elif c == quote:
                if quote == "'" and i + 1 < len(value) and value[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    quote = None
        elif c in ("'", '"'):
            quote = c
            buf.append(c)
        elif c in "[{":
            depth += 1
            buf.append(c)
        elif c in "]}":
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    last = "".join(buf).strip()
    if last or parts:
        parts.append(last)
    return parts


def parse_flow_map(value, depth=0):
    if depth > MAX_FLOW_DEPTH:
        raise FormatError(f"mini-yaml: flow collections nested deeper than {MAX_FLOW_DEPTH}")
    inner = value[1:-1].strip()
    if inner == "":
        return {}
    result = {}
    for part in split_flow(inner):
        if part == "":
            continue
        if ":" not in part:
            raise FormatError(f"mini-yaml: bad flow map entry: {part!r}")
        k, v = part.split(":", 1)
        result[unquote_key(k.strip())] = parse_scalar(v.strip(), depth + 1)
    return result


def unescape_double(body):
    out = []
    i = 0
    simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
              "n": "\n", "r": "\r", "t": "\t"}
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(body):
            raise FormatError("mini-yaml: dangling escape in string")
        nxt = body[i + 1]
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
        elif nxt == "u":
            hexpart = body[i + 2:i + 6]
            if len(hexpart) != 4:
                raise FormatError("mini-yaml: bad \\u escape")
            try:
                out.append(chr(int(hexpart, 16)))
            except ValueError:
                raise FormatError(f"mini-yaml: bad \\u escape: \\u{hexpart}")
            i += 6
        else:
            raise FormatError(f"mini-yaml: unknown escape: \\{nxt}")
    return "".join(out)


def parse_quoted(value):
    quote = value[0]
    if quote == "'":
        i = 1
        buf = []
        while i < len(value):
            c = value[i]
            if c == "'":
                if i + 1 < len(value) and value[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                tail = value[i + 1:]
                if tail.strip():
                    raise FormatError(f"mini-yaml: trailing junk after string: {value!r}")
                return "".join(buf)
            buf.append(c)
            i += 1
        raise FormatError("mini-yaml: unterminated single-quoted string")
    if quote == '"':
        end = None
        i = 1
        while i < len(value):
            c = value[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                end = i
                break
            i += 1
        if end is None:
            raise FormatError("mini-yaml: unterminated double-quoted string")
        tail = value[end + 1:]
        if tail.strip():
            raise FormatError(f"mini-yaml: trailing junk after string: {value!r}")
        return unescape_double(value[1:end])
    raise FormatError(f"mini-yaml: expected quoted string: {value!r}")


_INT_RE = re.compile(r"^[-+]?[0-9]+$")
_FLOAT_RE = re.compile(r"^[-+]?([0-9]+\.[0-9]*|[0-9]*\.[0-9]+)([eE][-+]?[0-9]+)?$")
_EXP_RE = re.compile(r"^[-+]?[0-9]+[eE][-+]?[0-9]+$")


def parse_scalar(raw, depth=0):
    if depth > MAX_FLOW_DEPTH:
        raise FormatError(f"mini-yaml: flow collections nested deeper than {MAX_FLOW_DEPTH}")
    value = raw.strip()
    if value == "":
        return None
    lowered = value.lower()
    if lowered in ("null", "~"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if _INT_RE.match(value):
        return int(value)
    if _FLOAT_RE.match(value) or _EXP_RE.match(value):
        return float(value)
    if value[0] in ("'", '"'):
        return parse_quoted(value)
    if value.startswith("["):
        if not value.endswith("]"):
            raise FormatError(f"mini-yaml: unterminated flow sequence: {value!r}")
        inner = value[1:-1].strip()
        if inner == "":
            return []
        return [parse_scalar(p, depth + 1) for p in split_flow(inner)]
    if value.startswith("{"):
        if not value.endswith("}"):
            raise FormatError(f"mini-yaml: unterminated flow map: {value!r}")
        return parse_flow_map(value, depth)
    return strip_inline_comment(value)


def parse(text):
    from .safety import ensure_encodable

    raw_lines = text.split("\n")
    if len(raw_lines) > MAX_YAML_LINES:
        raise FormatError(f"mini-yaml: more than {MAX_YAML_LINES} lines")
    lines = []
    for ln in raw_lines:
        leading_ws = ln[:len(ln) - len(ln.lstrip())]
        if "\t" in leading_ws:
            raise FormatError("mini-yaml: tab characters are not allowed in indentation")
        expanded = ln.expandtabs(2)
        stripped = expanded.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        indent = len(expanded) - len(expanded.lstrip(" "))
        lines.append((indent, stripped))
    if not lines:
        return {}
    parser = _Parser(lines)
    result = parser.parse_root()
    if parser.i < len(lines):
        _, leftover = lines[parser.i]
        raise FormatError(f"mini-yaml: could not parse line: {leftover!r}")
    ensure_encodable(result)
    return result


def format_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        if (_SAFE_PLAIN_RE.match(value)
                and value == value.strip()
                and value.lower() not in _RESERVED_WORDS
                and not _NUMERIC_START_RE.match(value)):
            return value
        return json.dumps(value, ensure_ascii=False)
    raise FormatError(f"mini-yaml: cannot serialize {type(value).__name__} as scalar")


def _safe_key(key):
    key_str = str(key)
    if (key_str
            and key_str == key_str.strip()
            and _SAFE_PLAIN_RE.match(key_str)
            and not _NUMERIC_START_RE.match(key_str)):
        return key_str
    return json.dumps(key_str, ensure_ascii=False)


def _pairs_lines(mapping, indent):
    lines = []
    pad = " " * indent
    for key, value in mapping.items():
        sk = _safe_key(key)
        if isinstance(value, dict):
            if value:
                lines.append(f"{pad}{sk}:")
                lines.extend(_pairs_lines(value, indent + 2))
            else:
                lines.append(f"{pad}{sk}: {{}}")
        elif isinstance(value, list):
            if value:
                lines.append(f"{pad}{sk}:")
                lines.extend(_list_lines(value, indent + 2))
            else:
                lines.append(f"{pad}{sk}: []")
        else:
            lines.append(f"{pad}{sk}: {format_scalar(value)}")
    return lines


def _dict_item_lines(mapping, indent):
    pad = " " * indent
    keys = list(mapping.keys())
    if not keys:
        return [f"{pad}- {{}}"]
    first_key = keys[0]
    fk = _safe_key(first_key)
    first_value = mapping[first_key]
    lines = []
    if isinstance(first_value, dict):
        if first_value:
            lines.append(f"{pad}- {fk}:")
            lines.extend(_pairs_lines(first_value, indent + 4))
        else:
            lines.append(f"{pad}- {fk}: {{}}")
    elif isinstance(first_value, list):
        if first_value:
            lines.append(f"{pad}- {fk}:")
            lines.extend(_list_lines(first_value, indent + 4))
        else:
            lines.append(f"{pad}- {fk}: []")
    else:
        lines.append(f"{pad}- {fk}: {format_scalar(first_value)}")
    rest = {k: mapping[k] for k in keys[1:]}
    lines.extend(_pairs_lines(rest, indent + 2))
    return lines


def _list_lines(items, indent):
    lines = []
    pad = " " * indent
    for item in items:
        if isinstance(item, dict):
            lines.extend(_dict_item_lines(item, indent))
        elif isinstance(item, list):
            if not item:
                lines.append(f"{pad}- []")
                continue
            sub = _list_lines(item, indent + 2)
            lines.append(f"{pad}- {sub[0].strip()}")
            lines.extend(sub[1:])
        else:
            lines.append(f"{pad}- {format_scalar(item)}")
    return lines


def dump(obj):
    if isinstance(obj, dict):
        if not obj:
            return "{}\n"
        return "\n".join(_pairs_lines(obj, 0)) + "\n"
    if isinstance(obj, list):
        if not obj:
            return "[]\n"
        return "\n".join(_list_lines(obj, 0)) + "\n"
    return format_scalar(obj) + "\n"
