import re

from .errors import FormatError

try:
    import tomllib as _toml_reader
except ImportError:
    _toml_reader = None

MAX_TOML_LINES = 8000
MAX_VALUE_DEPTH = 32


def escape_toml_string(value):
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def toml_key(key):
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return escape_toml_string(key)


def _format_inline(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return escape_toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_inline(v) for v in value) + "]"
    if isinstance(value, dict):
        parts = [f"{toml_key(str(k))} = {_format_inline(v)}" for k, v in value.items()]
        return "{ " + ", ".join(parts) + " }"
    raise FormatError(f"minitoml: cannot serialize {type(value).__name__}")


def dumps(obj):
    lines = []
    scalars = []
    tables = []
    arrays_of_tables = []
    for key, value in obj.items():
        if isinstance(value, dict):
            tables.append((key, value))
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            arrays_of_tables.append((key, value))
        else:
            scalars.append((key, value))
    for key, value in scalars:
        lines.append(f"{toml_key(str(key))} = {_format_inline(value)}")

    def emit_table(path, mapping):
        if path:
            header = ".".join(toml_key(p) for p in path)
            sub_scalars = []
            sub_tables = []
            sub_aots = []
            for k, v in mapping.items():
                if isinstance(v, dict):
                    sub_tables.append((k, v))
                elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    sub_aots.append((k, v))
                else:
                    sub_scalars.append((k, v))
            if sub_scalars or not mapping:
                lines.append(f"\n[{header}]")
            for k, v in sub_scalars:
                lines.append(f"{toml_key(str(k))} = {_format_inline(v)}")
            for k, v in sub_tables:
                emit_table(path + [str(k)], v)
            for k, v in sub_aots:
                emit_aot(path + [str(k)], v)
        else:
            for k, v in mapping.items():
                if isinstance(v, dict):
                    emit_table([str(k)], v)
                elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    emit_aot([str(k)], v)

    def emit_aot(path, items):
        header = ".".join(toml_key(p) for p in path)
        for item in items:
            lines.append(f"\n[[{header}]]")
            for k, v in item.items():
                if isinstance(v, dict):
                    emit_table_inner(header, str(k), v)
                else:
                    lines.append(f"{toml_key(str(k))} = {_format_inline(v)}")

    def emit_table_inner(parent_header, key, mapping):
        full = f"{parent_header}.{toml_key(key)}"
        sub_scalars = [(k, v) for k, v in mapping.items() if not isinstance(v, (dict, list))]
        if sub_scalars or not mapping:
            lines.append(f"\n[{full}]")
        for k, v in sub_scalars:
            lines.append(f"{toml_key(str(k))} = {_format_inline(v)}")
        for k, v in mapping.items():
            if isinstance(v, dict):
                emit_table_inner(full, str(k), v)

    for key, value in tables:
        emit_table(None, {key: value})
    for key, value in arrays_of_tables:
        emit_aot([key], value)
    text = "\n".join(lines)
    if text:
        text += "\n"
    return text


def _split_top_level(s, sep=","):
    parts = []
    depth = 0
    quote = None
    buf = []
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < len(s):
                buf.append(s[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in ('"', "'"):
            quote = c
            buf.append(c)
        elif c in "[{":
            depth += 1
            buf.append(c)
        elif c in "]}":
            depth -= 1
            buf.append(c)
        elif c == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    last = "".join(buf).strip()
    if last:
        parts.append(last)
    return parts


def _parse_value(raw, depth=0):
    if depth > MAX_VALUE_DEPTH:
        raise FormatError(f'minitoml: values nested deeper than {MAX_VALUE_DEPTH}')
    raw = raw.strip()
    if raw.startswith('"'):
        return _parse_basic_string(raw)[0]
    if raw.startswith("'"):
        end = raw.find("'", 1)
        if end < 0:
            raise FormatError("minitoml: unterminated literal string")
        return raw[1:end]
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise FormatError("minitoml: unterminated array")
        inner = raw[1:-1].strip()
        if inner == "":
            return []
        return [_parse_value(p, depth + 1) for p in _split_top_level(inner)]
    if raw.startswith("{"):
        if not raw.endswith("}"):
            raise FormatError("minitoml: unterminated inline table")
        inner = raw[1:-1].strip()
        result = {}
        if inner == "":
            return result
        for part in _split_top_level(inner):
            if "=" not in part:
                raise FormatError(f"minitoml: bad inline table entry: {part!r}")
            k, v = part.split("=", 1)
            kk = k.strip()
            if kk.startswith('"') or kk.startswith("'"):
                kk = _parse_value(kk, depth + 1)
            result[kk] = _parse_value(v.strip(), depth + 1)
        return result
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        f = float(raw)
        if f != f or f in (float("inf"), float("-inf")):
            raise ValueError
        return f
    except ValueError:
        pass
    return raw


def _parse_basic_string(raw):
    out = []
    i = 1
    simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
              "n": "\n", "r": "\r", "t": "\t"}
    while i < len(raw):
        c = raw[i]
        if c == '"':
            return "".join(out), raw[i + 1:]
        if c == "\\":
            nxt = raw[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
            elif nxt in ("u", "U"):
                width = 4 if nxt == "u" else 8
                hexpart = raw[i + 2:i + 2 + width]
                out.append(chr(int(hexpart, 16)))
                i += 2 + width
            else:
                raise FormatError(f"minitoml: unknown escape \\{nxt}")
        else:
            out.append(c)
            i += 1
    raise FormatError("minitoml: unterminated basic string")


def parse_fallback(text):
    try:
        return _parse_fallback_impl(text)
    except RecursionError:
        raise FormatError("minitoml: values nested too deep")


def _parse_fallback_impl(text):
    lines_in = text.split("\n")
    if len(lines_in) > MAX_TOML_LINES:
        raise FormatError(f"TOML file too large (> {MAX_TOML_LINES} lines)")
    root = {}
    current = root
    current_path = []
    i = 0
    while i < len(lines_in):
        raw_line = lines_in[i]
        line = raw_line.strip()
        i += 1
        if line == "" or line.startswith("#"):
            continue
        if line.startswith("[[") and line.endswith("]]"):
            path = _split_dotted(line[2:-2])
            tbl = root
            for part in path[:-1]:
                tbl = tbl.setdefault(part, {})
            arr = tbl.setdefault(path[-1], [])
            if not isinstance(arr, list):
                raise FormatError(f"minitoml: conflicting types at {'.'.join(path)}")
            new_tbl = {}
            arr.append(new_tbl)
            current = new_tbl
            current_path = path
            continue
        if line.startswith("[") and line.endswith("]"):
            path = _split_dotted(line[1:-1])
            tbl = root
            for part in path[:-1]:
                nxt = tbl.setdefault(part, {})
                if not isinstance(nxt, dict):
                    raise FormatError(f"minitoml: conflicting types at {'.'.join(path)}")
                tbl = nxt
            leaf = tbl.setdefault(path[-1], {})
            if not isinstance(leaf, dict):
                raise FormatError(f"minitoml: conflicting types at {'.'.join(path)}")
            current = leaf
            current_path = path
            continue
        if "=" not in line:
            raise FormatError(f"minitoml: cannot parse line: {line!r}")
        key_part, _, val_part = line.partition("=")
        key = key_part.strip()
        if key.startswith('"') or key.startswith("'"):
            key = _parse_value(key)
        val_part = val_part.strip()
        while _needs_more(val_part):
            if i >= len(lines_in):
                raise FormatError("minitoml: unexpected end of file in multiline value")
            cont = lines_in[i].strip()
            i += 1
            if cont and not cont.startswith("#"):
                val_part += " " + cont
        stripped_comment = _strip_toml_comment(val_part)
        current[key] = _parse_value(stripped_comment)
    return root


def _needs_more(val_part):
    depth = 0
    quote = None
    i = 0
    while i < len(val_part):
        c = val_part[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < len(val_part):
                i += 1
            elif c == quote:
                quote = None
        elif c in ('"', "'"):
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        i += 1
    return depth > 0 or quote is not None


def _strip_toml_comment(val_part):
    quote = None
    i = 0
    while i < len(val_part):
        c = val_part[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < len(val_part):
                i += 1
            elif c == quote:
                quote = None
        elif c in ('"', "'"):
            quote = c
        elif c == "#":
            return val_part[:i].rstrip()
        i += 1
    return val_part


def _split_dotted(header):
    parts = []
    quote = None
    buf = []
    i = 0
    while i < len(header):
        c = header[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(header):
                buf.append(header[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in ('"', "'"):
            quote = c
        elif c == ".":
            parts.append("".join(buf).strip().strip('"').strip("'"))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf).strip().strip('"').strip("'"))
    return [p for p in parts if p]


def parse(text):
    if _toml_reader is not None:
        import io
        data = text.encode("utf-8", "strict")
        try:
            result = _toml_reader.load(io.BytesIO(data))
        except UnicodeEncodeError as exc:
            raise FormatError(f"invalid TOML encoding: {exc}")
        except Exception as exc:
            raise FormatError(f"invalid TOML: {exc}")
        from .safety import ensure_encodable
        ensure_encodable(result)
        return result
    result = parse_fallback(text)
    from .safety import ensure_encodable
    ensure_encodable(result)
    return result
