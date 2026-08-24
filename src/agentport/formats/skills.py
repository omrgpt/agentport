import re

from ..errors import FormatError
from ..frontmatter import render_frontmatter, split_frontmatter
from ..ir import SkillDoc
from ..safety import ensure_trailing_newline, normalize_newlines

NAME_MAX = 64
DESC_MAX = 1024
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
KNOWN_FIELDS = {"name", "description", "license", "allowed-tools", "compatibility", "metadata"}

SKILL_TARGET_DIRS = {
    "claude": ".claude/skills",
    "opencode": ".opencode/skill",
    "agents": ".agents/skills",
}

TOOL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+(\([^\n()]*\))?$")


def normalize_skill_name(raw):
    s = str(raw).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if len(s) > NAME_MAX:
        s = s[:NAME_MAX].rstrip("-")
    return s


def parse_allowed_tools(raw, warnings):
    if raw is None:
        return None
    items = []
    src = []
    if isinstance(raw, list):
        src = [str(x) for x in raw]
    elif isinstance(raw, str):
        src = raw.split(",")
    else:
        warnings.append("WARN allowed-tools must be a string or list; dropped")
        return None
    for token in src:
        token = token.strip()
        if not token:
            continue
        if not TOOL_TOKEN_RE.match(token):
            warnings.append(f"WARN unusual allowed-tools entry kept as-is: {token!r}")
        items.append(token)
    return items


def parse_skill_md(text, warnings):
    meta, body = split_frontmatter(text)
    if not isinstance(meta, dict):
        raise FormatError(
            "SKILL.md requires YAML frontmatter with 'name' and 'description'",
            hint="start the file with --- and define name/description fields",
        )
    name_raw = meta.get("name")
    desc_raw = meta.get("description")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise FormatError("SKILL.md frontmatter is missing a valid 'name' field")
    if not isinstance(desc_raw, str) or not desc_raw.strip():
        raise FormatError("SKILL.md frontmatter is missing a valid 'description' field")
    unknown = {
        k: v for k, v in meta.items()
        if k not in KNOWN_FIELDS
    }
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    doc = SkillDoc(
        name=name_raw.strip(),
        description=desc_raw.strip(),
        license=str(meta["license"]).strip() if isinstance(meta.get("license"), (str, int)) else None,
        allowed_tools=parse_allowed_tools(meta.get("allowed-tools"), warnings),
        compatibility=str(meta["compatibility"]).strip() if isinstance(meta.get("compatibility"), (str, int)) else None,
        metadata=dict(metadata),
        body=normalize_newlines(body),
        unknown_fields=unknown,
    )
    for k in sorted(unknown):
        warnings.append(f"WARN unknown frontmatter field '{k}' preserved as-is")
    return doc


def validate_skill(doc, dir_name=None, warnings=None):
    issues = []
    warn = warnings if warnings is not None else []
    if not NAME_RE.match(doc.name):
        issues.append(
            f"name '{doc.name}' violates the skill naming rule "
            "(lowercase letters, digits and hyphens; must start/end alphanumeric)"
        )
    if len(doc.name) > NAME_MAX:
        issues.append(f"name exceeds {NAME_MAX} characters ({len(doc.name)})")
    if len(doc.description) > DESC_MAX:
        issues.append(f"description exceeds {DESC_MAX} characters ({len(doc.description)})")
    elif len(doc.description) > 512:
        warn.append("WARN description longer than 512 chars; some clients may truncate it")
    body_clean = doc.body.strip()
    if not body_clean:
        issues.append("skill body is empty")
    elif len(body_clean) < 20:
        warn.append("WARN skill body is very short (< 20 chars)")
    if dir_name is not None and dir_name != doc.name:
        warn.append(
            f"WARN folder name '{dir_name}' != frontmatter name '{doc.name}'; "
            "use skills normalize to align them"
        )
    return issues


def load_skill_dir(dir_path, warnings):
    from pathlib import Path

    d = Path(dir_path)
    if d.is_file() and d.name.upper() == "SKILL.MD":
        d = d.parent
    skill_file = d / "SKILL.md"
    if not skill_file.exists():
        raise FormatError(
            f"no SKILL.md found under {dir_path}",
            hint="skills live in a folder containing SKILL.md",
        )
    from ..safety import read_text_capped

    text = read_text_capped(skill_file, what="SKILL.md")
    doc = parse_skill_md(text, warnings)
    doc.source_dir = str(d)
    validate_skill(doc, dir_name=d.name, warnings=warnings)
    return doc


def render_skill_md(doc, target="claude"):
    meta = {}
    meta["name"] = doc.name
    meta["description"] = doc.description
    if doc.license:
        meta["license"] = doc.license
    if target == "claude":
        if doc.allowed_tools:
            meta["allowed-tools"] = ", ".join(doc.allowed_tools)
    else:
        if doc.allowed_tools:
            meta.setdefault("metadata", {})["allowed-tools"] = ", ".join(doc.allowed_tools)
    if doc.compatibility:
        meta["compatibility"] = doc.compatibility
    if doc.metadata:
        merged = dict(doc.metadata)
        existing = meta.get("metadata")
        if isinstance(existing, dict):
            merged.update({k: v for k, v in existing.items()})
        meta["metadata"] = merged
    body = ensure_trailing_newline(doc.body)
    return render_frontmatter(meta, body)


def apply_normalizations(doc, warnings):
    fixed_name = normalize_skill_name(doc.name)
    if fixed_name != doc.name:
        if not fixed_name:
            raise FormatError(
                f"cannot derive a valid skill name from {doc.name!r}",
                hint="rename the folder/frontmatter manually",
            )
        warnings.append(f"WARN normalized name: '{doc.name}' -> '{fixed_name}'")
        doc.name = fixed_name
    trimmed = " ".join(doc.description.split())
    if len(trimmed) > DESC_MAX:
        cut = trimmed[:DESC_MAX].rsplit(" ", 1)[0].rstrip()
        warnings.append(f"WARN truncated description to {DESC_MAX} characters")
        trimmed = cut
    if trimmed != doc.description:
        warnings.append("WARN whitespace-normalized description")
        doc.description = trimmed
    if doc.body and not doc.body.endswith("\n"):
        warnings.append("WARN added missing trailing newline to body")
        doc.body += "\n"
    return doc
