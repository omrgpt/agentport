import re

from ..errors import FormatError, UsageError
from ..frontmatter import render_frontmatter, split_frontmatter
from ..safety import ensure_trailing_newline, normalize_newlines

INSTRUCTION_TARGETS = {
    "agents": "AGENTS.md",
    "claude": "CLAUDE.md",
    "gemini": "GEMINI.md",
    "amp": "AGENT.md",
    "zed": ".rules",
    "windsurf": ".windsurfrules",
    "cline": ".clinerules",
    "aider": "CONVENTIONS.md",
    "copilot": ".github/copilot-instructions.md",
    "cursor": ".cursor/rules/main.mdc",
    "cursor-legacy": ".cursorrules",
}

MDC_TARGETS = {"cursor", "cursor-legacy"}

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_MARKDOWN_NOISE_RE = re.compile(r"[`*_\[\]()>#-]")


def derive_description(body, limit=80):
    m = _HEADING_RE.search(body)
    if m:
        text = m.group(1)
    else:
        text = ""
        for line in body.split("\n"):
            line = line.strip()
            if not line or line.startswith(("#", "---", "```")):
                continue
            text = line
            break
    text = _MARKDOWN_NOISE_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut.strip() + "..."
    return text


def parse_instructions(text):
    meta, body = split_frontmatter(text)
    return body


def parse_cursor_rule(text):
    meta, body = split_frontmatter(text)
    if not isinstance(meta, dict):
        raise FormatError(
            "cursor rule (.mdc) requires YAML frontmatter with at least a description"
        )
    return meta, body


def normalize_body(body):
    return ensure_trailing_newline(normalize_newlines(body)).rstrip("\n") + "\n"


def render_cursor_rule(body, description=None, globs=None, always_apply=True,
                       source_meta=None):
    desc = description
    if not desc and source_meta and isinstance(source_meta.get("description"), str):
        desc = source_meta["description"]
    if not desc:
        desc = derive_description(body) or "Coding guidelines"
    meta = {}
    meta["description"] = desc
    if globs is None:
        if source_meta and isinstance(source_meta.get("globs"), (list, str)):
            globs = source_meta["globs"]
        else:
            globs = []
    if isinstance(globs, str):
        globs = [g.strip() for g in globs.split(",") if g.strip()]
    meta["globs"] = list(globs or [])
    if always_apply is None:
        if source_meta and isinstance(source_meta.get("alwaysApply"), bool):
            always_apply = source_meta["alwaysApply"]
        else:
            always_apply = True
    meta["alwaysApply"] = bool(always_apply)
    return render_frontmatter(meta, normalize_body(body))


def convert_instructions(src_text, target_key, *, description=None, globs=None,
                         always_apply=None, warnings=None):
    body = parse_instructions(src_text)
    if target_key in MDC_TARGETS:
        src_is_mdc = False
        try:
            meta_probe, _ = split_frontmatter(src_text)
            if isinstance(meta_probe, dict):
                src_is_mdc = True
        except FormatError:
            pass
        source_meta = None
        if src_is_mdc:
            source_meta, body = split_frontmatter(src_text)
        out_text = render_cursor_rule(
            body, description=description, globs=globs,
            always_apply=always_apply, source_meta=source_meta,
        )
        return out_text
    if description or globs is not None or always_apply is not None:
        warnings.append(
            f"WARN --description/--globs/--always-apply only apply to cursor targets; ignored for {target_key}"
        )
    return normalize_body(body)


def validate_target(target_key):
    if target_key not in INSTRUCTION_TARGETS:
        known = ", ".join(sorted(INSTRUCTION_TARGETS))
        raise UsageError(f"unknown instructions target: {target_key}", hint=f"known targets: {known}")
