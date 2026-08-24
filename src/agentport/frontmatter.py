from . import miniyaml
from .errors import FormatError
from .safety import ensure_trailing_newline, normalize_newlines

MAX_FRONTMATTER_LINES = 500


def split_frontmatter(text):
    text = normalize_newlines(text)
    lines = text.split("\n")
    first = lines[0].strip() if lines else ""
    if first != "---":
        return None, text
    end_idx = None
    limit = min(len(lines), MAX_FRONTMATTER_LINES + 1)
    for idx in range(1, limit):
        if lines[idx].strip() in ("---", "..."):
            end_idx = idx
            break
    if end_idx is None:
        raise FormatError("frontmatter opened with --- but never closed")
    meta_text = "\n".join(lines[1:end_idx])
    meta = miniyaml.parse(meta_text)
    body = "\n".join(lines[end_idx + 1:])
    return meta, body.lstrip("\n")


def render_frontmatter(meta, body):
    if not isinstance(meta, dict):
        raise FormatError("frontmatter must be a mapping")
    fm_text = miniyaml.dump(meta).rstrip("\n")
    return f"---\n{fm_text}\n---\n\n{ensure_trailing_newline(body)}"
