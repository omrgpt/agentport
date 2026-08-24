import os
import re
from pathlib import Path

from .errors import ConflictError, FormatError, SafetyError, UsageError
from .formats import instructions as instr
from .formats import mcp as mcpf
from .formats import skills as skillf
from .safety import (
    atomic_write_text,
    collect_bundle_files,
    copy_bundle_file,
    ensure_writable_file,
    ensure_within,
    read_text_capped,
)
from . import jsonc

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv",
    ".next", "coverage", ".idea", ".cache", "target",
}

INSTRUCTION_FILES = {
    "CLAUDE.md": ("claude", "instructions"),
    "AGENTS.md": ("agents", "instructions"),
    "GEMINI.md": ("gemini", "instructions"),
    "AGENT.md": ("amp", "instructions"),
    ".rules": ("zed", "instructions"),
    ".windsurfrules": ("windsurf", "instructions"),
    ".clinerules": ("cline", "instructions"),
    "CONVENTIONS.md": ("aider", "instructions"),
    ".cursorrules": ("cursor-legacy", "instructions"),
}

SKILL_DIR_HINTS = {".claude/skills", ".opencode/skill", ".opencode/skills", ".agents/skills"}


def detect(root):
    root_p = Path(root)
    if not root_p.is_dir():
        raise UsageError(f"not a directory: {root}")
    found = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root_p):
        rel_dir = Path(dirpath).relative_to(root_p)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        scanned += 1
        if scanned > 20000:
            break
        rel_names = set(filenames)
        for fname in filenames:
            rel = (rel_dir / fname).as_posix()
            hit = INSTRUCTION_FILES.get(fname)
            if hit and fname in rel_names:
                found.append({"tool": hit[0], "kind": hit[1], "path": rel})
            elif fname.lower() == "skill.md":
                parent = rel_dir.as_posix()
                is_skill = any(parent == h or parent.startswith(h + "/") for h in SKILL_DIR_HINTS)
                if is_skill:
                    found.append({
                        "tool": _tool_for_skill_dir(parent),
                        "kind": "skill",
                        "path": rel,
                    })
            elif fname == "main.mdc" or fname.endswith(".mdc"):
                if ".cursor/rules" in rel.replace("\\", "/"):
                    found.append({"tool": "cursor", "kind": "cursor-rule", "path": rel})
        for special, tool, kind in (
            ("copilot-instructions.md", "copilot", "instructions"),
            ("mcp.json", None, "mcp"),
            ("mcp_config.json", "windsurf", "mcp"),
            ("settings.json", "gemini", "config"),
            ("claude_desktop_config.json", "claude", "mcp"),
            ("config.toml", None, "config"),
            ("opencode.json", "opencode", "config"),
            ("opencode.jsonc", "opencode", "config"),
        ):
            if special in filenames:
                rel = (rel_dir / special).as_posix()
                posix = rel.replace("\\", "/")
                if special == "copilot-instructions.md":
                    if ".github" not in posix:
                        continue
                    found.append({"tool": tool, "kind": kind, "path": rel})
                elif special == "mcp.json":
                    if posix.endswith(".cursor/mcp.json"):
                        found.append({"tool": "cursor", "kind": "mcp", "path": rel})
                    elif posix.endswith(".vscode/mcp.json"):
                        found.append({"tool": "vscode", "kind": "mcp", "path": rel})
                elif special == "mcp_config.json":
                    if "codeium" in posix or "windsurf" in posix:
                        found.append({"tool": "windsurf", "kind": "mcp", "path": rel})
                elif special == "settings.json":
                    if ".gemini" in posix.split("/")[:-1] or posix.endswith(".gemini/settings.json") or "/.gemini/" in "/" + posix:
                        found.append({"tool": "gemini", "kind": kind, "path": rel})
                elif special == "config.toml":
                    if posix.endswith(".codex/config.toml"):
                        found.append({"tool": "codex", "kind": kind, "path": rel})
                else:
                    found.append({"tool": tool, "kind": kind, "path": posix if posix != rel else rel})
    seen = set()
    unique = []
    for f in sorted(found, key=lambda x: x["path"]):
        key = (f["tool"], f["kind"], f["path"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _tool_for_skill_dir(parent_posix):
    if parent_posix.startswith(".claude/"):
        return "claude"
    if parent_posix.startswith(".opencode/"):
        return "opencode"
    if parent_posix.startswith(".agents/"):
        return "agents-spec"
    return "unknown"


def resolve_source_file(root, from_key):
    filename = instr.INSTRUCTION_TARGETS[from_key]
    path = Path(root) / filename
    if not path.exists():
        raise FormatError(
            f"source file not found: {filename} under {root}",
            hint="run 'agentport detect' to see which instruction files exist, "
                 "or pass --src with an explicit file",
        )
    return path


def convert_instructions(root, src_path, target_key, out_path=None, *, description=None,
                         globs=None, always_apply=None, dry_run=False, force=False,
                         diff=False, warnings=None):
    warnings = [] if warnings is None else warnings
    instr.validate_target(target_key)
    root_p = Path(root)
    src_resolved = ensure_within(root_p, src_path)
    text = read_text_capped(src_resolved, what="source instructions")
    out_text = instr.convert_instructions(
        text, target_key, description=description, globs=globs,
        always_apply=always_apply, warnings=warnings,
    )
    if out_path is not None:
        dest = ensure_within(root_p, out_path)
    else:
        dest = root_p / instr.INSTRUCTION_TARGETS[target_key]
        ensure_within(root_p, dest)
    ensure_writable_file(dest)
    exists = dest.exists()
    if exists and not force and not dry_run:
        raise ConflictError(
            f"target already exists: {dest.relative_to(root_p)}",
            hint="use --force to overwrite, --out to choose another path, "
                 "or --dry-run to preview",
        )
    result = {"dest": str(dest), "bytes": len(out_text.encode("utf-8")), "changed": True}
    if diff and exists:
        old_text = read_text_capped(dest, what="existing target")
        result["diff"] = _unified_diff(old_text, out_text, str(dest))
    if not dry_run:
        atomic_write_text(dest, out_text)
    return result


def sync_instructions(root, from_key="agents", only=None, *, dry_run=False, force=False, warnings=None):
    warnings = [] if warnings is None else warnings
    root_p = Path(root)
    src_file = resolve_source_file(root_p, from_key)
    text = read_text_capped(src_file, what=f"{from_key} instructions")
    targets = only if only else [k for k in instr.INSTRUCTION_TARGETS if k != from_key]
    results = []
    for target_key in targets:
        try:
            r = convert_instructions(
                root_p, src_file, target_key, dry_run=dry_run, force=force,
                warnings=warnings,
            )
            r["target"] = target_key
            r["status"] = "written" if not dry_run else "planned"
            results.append(r)
        except ConflictError as exc:
            results.append({
                "target": target_key,
                "status": "skipped-existing",
                "dest": exc.message.split(": ", 1)[-1],
                "changed": False,
            })
        except FormatError as exc:
            warnings.append(f"WARN {target_key}: {exc.message}")
            results.append({"target": target_key, "status": "error", "changed": False})
    return results


def _unified_diff(old_text, new_text, label):
    import difflib

    diff_lines = list(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    ))
    return "".join(diff_lines[:400])


def mcp_convert(root, src_path, target_family, out_path=None, *, from_family=None,
                replace=False, conflict_policy="keep", prune_unused=False,
                dry_run=False, diff=False, warnings=None):
    warnings = [] if warnings is None else warnings
    mcpf.validate_target(target_family)
    root_p = Path(root)
    src_resolved = ensure_within(root_p, src_path)
    text = read_text_capped(src_resolved, what="MCP source config", max_bytes=5 * 1024 * 1024)
    source_family = from_family or mcpf.sniff_family(src_resolved.name, text)
    if source_family is None:
        raise FormatError(
            f"cannot detect MCP config family of {src_resolved.name}",
            hint="pass --from with one of: " + ", ".join(mcpf.MCP_FAMILIES),
        )
    doc, raw_obj = mcpf.parse_source_text(text, source_family, warnings)
    if not doc.servers:
        warnings.append("WARN no servers parsed from source; nothing to merge")
    if target_family == source_family and out_path is None:
        warnings.append("WARN source and target family are identical")

    if out_path is not None:
        dest = ensure_within(root_p, out_path)
    else:
        default_rel = mcpf.PROJECT_DEFAULT_PATHS.get(target_family)
        if default_rel is None:
            hints = "\n  ".join(mcpf.GLOBAL_TARGET_HINTS[target_family])
            raise UsageError(
                f"'{target_family}' lives outside the project; pass --out explicitly",
                hint=f"typical locations:\n  {hints}\n"
                     f"or write a local copy: --out agentport-out/{target_family}-mcp."
                     + ("toml" if target_family == "codex" else "json"),
            )
        if target_family == "opencode":
            jsonc_variant = root_p / "opencode.jsonc"
            dest = jsonc_variant if jsonc_variant.exists() else root_p / default_rel
        else:
            dest = root_p / default_rel
        ensure_within(root_p, dest)

    ensure_writable_file(dest)
    existing_text = None
    had_comments = False
    base_doc = None
    base_raw = None
    if dest.exists():
        existing_text = read_text_capped(dest, what="existing target config", max_bytes=5 * 1024 * 1024)
        if target_family == "codex":
            had_comments = any(line.strip().startswith("#") for line in existing_text.splitlines())
        try:
            base_doc, base_raw = mcpf.parse_source_text(existing_text, target_family, warnings)
        except FormatError as exc:
            if replace:
                warnings.append(f"WARN could not parse existing file ({exc.message}); replacing it")
                base_doc, base_raw = None, None
            else:
                raise
        if prune_unused and base_doc is not None:
            keep = {s.name for s in doc.servers}
            pruned_names = []
            for srv in list(base_doc.servers):
                if srv.name not in keep:
                    pruned_names.append(srv.name)
            if pruned_names:
                warnings.append("WARN pruning servers absent from source: " + ", ".join(sorted(pruned_names)))
                base_doc.servers = [s for s in base_doc.servers if s.name in keep]
                if base_raw is not None:
                    for section_key in ("mcpServers", "servers", "mcp", "mcp_servers"):
                        section = base_raw.get(section_key)
                        if isinstance(section, dict):
                            for pruned_name in pruned_names:
                                section.pop(pruned_name, None)
    else:
        base_doc, base_raw = None, None
        if doc.extras and not replace:
            base_raw = dict(doc.extras)

    if replace:
        effective_base = None
    else:
        effective_base = base_doc

    merged_doc = mcpf.merge_documents(
        effective_base if effective_base is not None else type(doc)(),
        doc,
        conflict_policy,
        False,
        warnings,
    ) if effective_base is not None else doc

    if target_family == "codex":
        out_text, out_obj = mcpf.render_codex(
            merged_doc, base_raw, replace, conflict_policy, had_comments, warnings,
        )
    elif target_family == "vscode":
        out_text, out_obj = mcpf.render_vscode(merged_doc, base_raw, replace, conflict_policy, warnings)
    elif target_family == "opencode":
        out_text, out_obj = mcpf.render_opencode(merged_doc, base_raw, replace, conflict_policy, warnings)
    else:
        out_text, out_obj = mcpf.render_family_json(merged_doc, base_raw, target_family, replace, conflict_policy, warnings)

    result = {
        "dest": str(dest),
        "source_family": source_family,
        "server_count": len(doc.servers),
        "merged_count": len(merged_doc.servers),
        "bytes": len(out_text.encode("utf-8")),
    }
    if diff and existing_text is not None:
        from .safety import mask_tree

        try:
            old_masked = mask_tree(jsonc.loads_jsonc(existing_text)) if target_family != "codex" else None
        except FormatError:
            old_masked = None
        if target_family != "codex" and old_masked is not None:
            result["diff"] = _unified_diff(
                jsonc.dumps_pretty(old_masked), jsonc.dumps_pretty(mask_tree(out_obj)), str(dest),
            )
        else:
            result["diff"] = _unified_diff(existing_text, out_text, str(dest))
    if not dry_run:
        atomic_write_text(dest, out_text)
    return result


def mcp_show(root, src_path, warnings=None, from_family=None):
    warnings = [] if warnings is None else warnings
    root_p = Path(root)
    src_resolved = ensure_within(root_p, src_path)
    text = read_text_capped(src_resolved, what="MCP config", max_bytes=5 * 1024 * 1024)
    family = from_family or mcpf.sniff_family(src_resolved.name, text)
    if family is None:
        raise FormatError(
            f"cannot detect MCP config family of {src_resolved.name}",
            hint="pass --from with one of: " + ", ".join(mcpf.MCP_FAMILIES),
        )
    doc, _raw = mcpf.parse_source_text(text, family, warnings)
    return mcpf.document_to_masked_preview(doc, family)


def skills_validate(root, skill_path, warnings=None):
    warnings = [] if warnings is None else warnings
    root_p = Path(root)
    p = ensure_within(root_p, skill_path)
    doc = skillf.load_skill_dir(p, warnings)
    issues = skillf.validate_skill(doc, warnings=warnings)
    return {
        "name": doc.name,
        "description_preview": doc.description[:120] + ("..." if len(doc.description) > 120 else ""),
        "issues": issues,
        "ok": not issues,
    }


def skills_normalize(root, skill_path, dry_run=False, force=False, warnings=None):
    warnings = [] if warnings is None else warnings
    root_p = Path(root)
    p = ensure_within(root_p, skill_path)
    doc = skillf.load_skill_dir(p, warnings)
    skillf.apply_normalizations(doc, warnings)
    new_text = skillf.render_skill_md(doc, target="claude")
    skill_file = p / "SKILL.md"
    old_text = read_text_capped(skill_file, what="SKILL.md")
    changed = old_text != new_text
    result = {"path": str(skill_file), "changed": changed}
    if changed and not dry_run:
        if not force:
            import difflib

            result["diff"] = "".join(list(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile="a/SKILL.md",
                tofile="b/SKILL.md",
            ))[:200])
            result["needs_force"] = True
        else:
            atomic_write_text(skill_file, new_text)
            folder = p.name
            if folder != doc.name:
                try:
                    skillf.validate_install_name(doc.name)
                except FormatError as name_exc:
                    warnings.append(f"WARN cannot rename folder: {name_exc.message}")
                else:
                    new_dir = ensure_within(root_p, p.parent / doc.name)
                    if new_dir.exists():
                        warnings.append(f"WARN cannot rename folder: {new_dir} already exists")
                    else:
                        p.rename(new_dir)
                        result["renamed_to"] = str(new_dir)
    return result


def skills_import(root, src_dir, target, name_override=None, *, dry_run=False, force=False, warnings=None):
    warnings = [] if warnings is None else warnings
    if target not in skillf.SKILL_TARGET_DIRS:
        raise UsageError(
            f"unknown skill target: {target}",
            hint="known targets: " + ", ".join(skillf.SKILL_TARGET_DIRS),
        )
    root_p = Path(root)
    p = ensure_within(root_p, src_dir)
    doc = skillf.load_skill_dir(p, warnings)
    fatal = skillf.validate_skill(doc, warnings=warnings)
    if fatal:
        raise FormatError(
            "skill failed validation; fix these issues first:",
            hint="; ".join(fatal) + " | run: agentport skills normalize " + str(skill_path),
        )
    skillf.apply_normalizations(doc, warnings)
    final_name = name_override or doc.name
    skillf.validate_install_name(final_name)
    bundle = collect_bundle_files(p, warnings)
    dest_root = ensure_within(root_p, root_p / skillf.SKILL_TARGET_DIRS[target])
    dest_dir = ensure_within(root_p, dest_root / final_name)
    result = {"dest": str(dest_dir), "files": [], "changed": False}
    if dest_dir.exists() and not force and not dry_run:
        raise ConflictError(
            f"destination already exists: {dest_dir}",
            hint="use --force to merge/overwrite into it",
        )
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for rel_posix, full_src in bundle:
            if rel_posix.upper() == "SKILL.MD":
                continue
            copy_bundle_file(full_src, dest_dir, rel_posix)
            result["files"].append(rel_posix)
        rendered = skillf.render_skill_md(doc, target=target)
        atomic_write_text(dest_dir / "SKILL.md", rendered)
        result["changed"] = True
    else:
        result["files"] = [r for r, _ in bundle]
    return result


def skills_export(root, src_dir, target, out_path=None, *, dry_run=False, force=False,
                  description=None, warnings=None):
    warnings = [] if warnings is None else warnings
    root_p = Path(root)
    p = ensure_within(root_p, src_dir)
    doc = skillf.load_skill_dir(p, warnings)
    body = doc.body
    if target == "cursor":
        try:
            skillf.validate_install_name(doc.name)
        except FormatError as name_exc:
            raise FormatError(
                f"cannot export: {name_exc.message}",
                hint="run 'agentport skills normalize' first to fix the name",
            )
        desc = description or doc.description
        out_text = instr.render_cursor_rule(body, description=desc)
        if out_path is not None:
            dest = ensure_within(root_p, out_path)
        else:
            dest = ensure_within(root_p, root_p / ".cursor/rules" / f"{doc.name}.mdc")
        ensure_writable_file(dest)
        if dest.exists() and not force and not dry_run:
            raise ConflictError(f"target already exists: {dest}", hint="use --force or --out")
        result = {"dest": str(dest), "bytes": len(out_text.encode()), "changed": True}
        if not dry_run:
            atomic_write_text(dest, out_text)
        return result
    if target == "markdown":
        out_text = "# " + doc.name + "\n\n" + body.strip() + "\n"
        if out_path is not None:
            dest = ensure_within(root_p, out_path)
            if not dry_run:
                atomic_write_text(dest, out_text)
            return {"dest": str(dest), "bytes": len(out_text.encode()), "changed": True}
        return {"stdout": out_text}
    raise UsageError("export target must be 'cursor' or 'markdown'")
