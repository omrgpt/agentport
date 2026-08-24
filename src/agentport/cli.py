import argparse
import json
import os
import re
import sys

from . import __version__
from . import ops
from .errors import AgentPortError, ConflictError, FormatError, SafetyError, UsageError

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize(text):
    return _CTRL_RE.sub("?", str(text))


class _Fmt:
    def __init__(self, enabled):
        self.enabled = enabled

    def _c(self, code, text):
        text = sanitize(text)
        if not self.enabled:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def ok(self, text):
        return self._c("32", text)

    def warn(self, text):
        return self._c("33", text)

    def err(self, text):
        return self._c("31", text)

    def dim(self, text):
        return self._c("2", text)


def _make_fmt(no_color):
    if no_color or os.environ.get("NO_COLOR"):
        return _Fmt(False)
    try:
        return _Fmt(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return _Fmt(False)


def _fix_windows_stdout():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _emit_warnings(warnings, fmt, strict):
    for w in warnings:
        print(fmt.warn(w))
    if strict and warnings:
        raise UsageError(
            "strict mode: warnings were treated as errors",
            hint="resolve the warnings above or drop --strict",
        )


def _table(rows, headers):
    headers = [sanitize(h) for h in headers]
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        cells = [sanitize(c) for c in row]
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
        str_rows.append(cells)
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for cells in str_rows:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)))
    return "\n".join(lines)


def cmd_detect(args, fmt):
    found = ops.detect(args.root)
    if not found:
        print(fmt.dim("no agent configuration files detected"))
        print()
        print("Suggested starting point: create an AGENTS.md, then run:")
        print("  agentport instructions sync --from agents")
        return 0
    rows = [(f["tool"], f["kind"], f["path"], fmt.ok("ok")) for f in found]
    print(_table(rows, ["tool", "kind", "file", "status"]))
    tools = sorted({f["tool"] for f in found})
    has_agents = any(f["path"].endswith("AGENTS.md") for f in found)
    print()
    print(f"Detected: {', '.join(tools)}")
    if has_agents:
        print("Propagate AGENTS.md everywhere:      agentport instructions sync --from agents")
    else:
        print("Convert one file to another target:   agentport instructions convert <FILE> --to <target>")
    mcp_files = [f for f in found if f["kind"] == "mcp"]
    if mcp_files:
        src = mcp_files[0]["path"]
        print(f"Sync MCP servers from {src}:          agentport mcp convert \"{src}\" --to <family>")
    return 0


def cmd_instructions_convert(args, fmt):
    warnings = []
    result = ops.convert_instructions(
        args.root, args.src, args.to, out_path=args.out,
        description=args.description, globs=args.globs,
        always_apply=args.always_apply,
        dry_run=args.dry_run, force=args.force, diff=args.diff,
        warnings=warnings,
    )
    _emit_warnings(warnings, fmt, args.strict)
    label = "planned" if args.dry_run else "wrote"
    print(f"{fmt.ok(label)}: {result['dest']} ({result['bytes']} bytes)")
    if args.diff and "diff" in result:
        print(sanitize(result["diff"]))
    return 0


def cmd_instructions_sync(args, fmt):
    only = None
    if args.only:
        only = [t.strip() for t in args.only.split(",") if t.strip()]
        from .formats.instructions import validate_target

        for t in only:
            validate_target(t)
    warnings = []
    results = ops.sync_instructions(
        args.root, from_key=args.from_key, only=only,
        dry_run=args.dry_run, force=args.force, warnings=warnings,
    )
    _emit_warnings(warnings, fmt, args.strict)
    rows = []
    written = skipped = 0
    for r in results:
        status = r["status"]
        if status == "written":
            icon = fmt.ok("written")
            written += 1
        elif status == "planned":
            icon = fmt.ok("planned")
            written += 1
        elif status == "skipped-existing":
            icon = fmt.warn("exists")
            skipped += 1
        else:
            icon = fmt.err(status)
        rows.append((r["target"], r.get("dest", "-"), icon))
    print(_table(rows, ["target", "file", "status"]))
    print()
    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {written}, skipped {skipped} existing (use --force to overwrite)")
    return 0


def cmd_mcp_convert(args, fmt):
    warnings = []
    result = ops.mcp_convert(
        args.root, args.src, args.to, out_path=args.out,
        from_family=args.from_key, replace=args.replace,
        conflict_policy=args.conflict, prune_unused=args.prune,
        dry_run=args.dry_run, diff=args.diff, warnings=warnings,
    )
    _emit_warnings(warnings, fmt, args.strict)
    verb = "planned" if args.dry_run else "wrote"
    print(
        f"{fmt.ok(verb)}: {result['dest']} "
        f"({result['server_count']} source -> {result['merged_count']} merged servers)"
    )
    if args.diff and "diff" in result:
        print(sanitize(result["diff"]))
    return 0


def cmd_mcp_show(args, fmt):
    warnings = []
    preview = ops.mcp_show(args.root, args.src, warnings=warnings, from_family=args.from_key)
    _emit_warnings(warnings, fmt, args.strict)
    print(json.dumps(preview, indent=2, ensure_ascii=False))
    return 0


def cmd_skills_validate(args, fmt):
    warnings = []
    result = ops.skills_validate(args.root, args.path, warnings=warnings)
    _emit_warnings(warnings, fmt, args.strict)
    if result["ok"]:
        print(f"{fmt.ok('PASS')}: skill '{sanitize(result['name'])}'")
        print(fmt.dim(f"  description: {result['description_preview']}"))
        return 0
    print(f"{fmt.err('FAIL')}: skill '{sanitize(result['name'])}'")
    for issue in result["issues"]:
        print(f"  - {sanitize(issue)}")
    return 1


def cmd_skills_normalize(args, fmt):
    warnings = []
    result = ops.skills_normalize(
        args.root, args.path, dry_run=args.dry_run, force=args.force, warnings=warnings,
    )
    _emit_warnings(warnings, fmt, args.strict)
    if not result["changed"]:
        print(f"{fmt.ok('already clean')}: {result['path']}")
        return 0
    if args.dry_run:
        print(f"{fmt.ok('planned changes')} for {result['path']}")
    elif result.get("needs_force"):
        print(f"{fmt.warn('review needed')} for {result['path']} (re-run with --force to apply):")
    else:
        print(f"{fmt.ok('normalized')}: {result['path']}")
        if "renamed_to" in result:
            print(f"  folder renamed to: {result['renamed_to']}")
    if "diff" in result:
        print(sanitize(result["diff"]))
    return 0


def cmd_skills_import(args, fmt):
    warnings = []
    result = ops.skills_import(
        args.root, args.path, args.to, name_override=args.name,
        dry_run=args.dry_run, force=args.force, warnings=warnings,
    )
    _emit_warnings(warnings, fmt, args.strict)
    verb = "planned" if args.dry_run else "installed"
    print(f"{fmt.ok(verb)} skill at: {result['dest']}")
    print(fmt.dim(f"  {len(result['files'])} auxiliary file(s) copied"))
    return 0


def cmd_skills_export(args, fmt):
    warnings = []
    result = ops.skills_export(
        args.root, args.path, args.to, out_path=args.out,
        dry_run=args.dry_run, force=args.force, description=args.description,
        warnings=warnings,
    )
    _emit_warnings(warnings, fmt, args.strict)
    if "stdout" in result:
        sys.stdout.write(sanitize(result["stdout"]))
        return 0
    verb = "planned" if args.dry_run else "wrote"
    print(f"{fmt.ok(verb)}: {result['dest']} ({result['bytes']} bytes)")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="agentport",
        description="Lightweight, security-first converter between coding-agent config formats.",
    )
    parser.add_argument("--version", action="version", version=f"agentport {__version__}")
    parser.add_argument("--root", default=".", help="project root directory (default: cwd)")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    parser.add_argument("--debug", action="store_true", help="show full tracebacks on unexpected errors")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_detect = sub.add_parser("detect", help="detect agent config files under the project root")
    p_detect.set_defaults(func=cmd_detect)

    p_instr = sub.add_parser("instructions", help="convert instruction files between tools")
    instr_sub = p_instr.add_subparsers(dest="subcommand", required=True)

    p_ic = instr_sub.add_parser("convert", help="convert one instruction file to another tool's format/location")
    p_ic.add_argument("src", help="source markdown file")
    p_ic.add_argument("--to", required=True,
                      help="target: agents|claude|gemini|amp|zed|windsurf|cline|aider|copilot|cursor|cursor-legacy")
    p_ic.add_argument("--out", default=None, help="explicit output path (default: canonical location)")
    p_ic.add_argument("--description", default=None, help="description for cursor rule frontmatter")
    p_ic.add_argument("--globs", default=None, help="comma-separated globs for cursor rule frontmatter")
    p_ic.add_argument("--always-apply", dest="always_apply",
                      type=lambda v: v.strip().lower() in ("1", "true", "yes"),
                      default=None, help="alwaysApply value for cursor rules (true/false)")
    p_ic.add_argument("--dry-run", action="store_true")
    p_ic.add_argument("--force", action="store_true")
    p_ic.add_argument("--diff", action="store_true")
    p_ic.set_defaults(func=cmd_instructions_convert)

    p_is = instr_sub.add_parser("sync", help="propagate a source instruction file to every other tool")
    p_is.add_argument("--from", dest="from_key", default="agents", help="source key (default: agents)")
    p_is.add_argument("--only", default=None, help="comma-separated subset of targets")
    p_is.add_argument("--dry-run", action="store_true")
    p_is.add_argument("--force", action="store_true")
    p_is.set_defaults(func=cmd_instructions_sync)

    p_mcp = sub.add_parser("mcp", help="convert MCP server configs between clients")
    mcp_sub = p_mcp.add_subparsers(dest="subcommand", required=True)

    p_mc = mcp_sub.add_parser("convert", help="merge/convert an MCP config into another client's format")
    p_mc.add_argument("src", help="source MCP config file")
    p_mc.add_argument("--to", required=True,
                      help="target family: claude|cursor|vscode|opencode|codex|windsurf|gemini")
    p_mc.add_argument("--out", default=None, help="output path (required for global targets like claude/codex)")
    p_mc.add_argument("--from", dest="from_key", default=None, help="override source family detection")
    p_mc.add_argument("--replace", action="store_true",
                      help="discard existing target file content instead of merging")
    p_mc.add_argument("--conflict", choices=["keep", "overwrite"], default="keep",
                      help="what wins when the same server name differs (default: keep)")
    p_mc.add_argument("--prune", action="store_true",
                      help="remove servers in the target absent from the source")
    p_mc.add_argument("--dry-run", action="store_true")
    p_mc.add_argument("--diff", action="store_true")
    p_mc.set_defaults(func=cmd_mcp_convert)

    p_ms = mcp_sub.add_parser("show", help="print a normalized, secret-masked view of an MCP config")
    p_ms.add_argument("src", help="MCP config file")
    p_ms.add_argument("--from", dest="from_key", default=None, help="override family detection")
    p_ms.set_defaults(func=cmd_mcp_show)

    p_sk = sub.add_parser("skills", help="validate, normalize, install and export SKILL.md folders")
    sk_sub = p_sk.add_subparsers(dest="subcommand", required=True)

    p_sv = sk_sub.add_parser("validate", help="validate a skill folder against the spec")
    p_sv.add_argument("path", help="skill folder (or its SKILL.md)")
    p_sv.set_defaults(func=cmd_skills_validate)

    p_sn = sk_sub.add_parser("normalize", help="auto-fix common frontmatter problems")
    p_sn.add_argument("path", help="skill folder (or its SKILL.md)")
    p_sn.add_argument("--dry-run", action="store_true")
    p_sn.add_argument("--force", action="store_true")
    p_sn.set_defaults(func=cmd_skills_normalize)

    p_si = sk_sub.add_parser("import", help="install a skill folder into a client's skills directory")
    p_si.add_argument("path", help="skill folder to install")
    p_si.add_argument("--to", required=True, help="target: claude|opencode|agents")
    p_si.add_argument("--name", default=None, help="override installed folder name")
    p_si.add_argument("--dry-run", action="store_true")
    p_si.add_argument("--force", action="store_true")
    p_si.set_defaults(func=cmd_skills_import)

    p_se = sk_sub.add_parser("export", help="export a skill as a cursor rule or markdown snippet")
    p_se.add_argument("path", help="skill folder to export")
    p_se.add_argument("--to", required=True, help="target: cursor|markdown")
    p_se.add_argument("--out", default=None, help="output path (default: .cursor/rules/<name>.mdc or stdout)")
    p_se.add_argument("--description", default=None, help="override description")
    p_se.add_argument("--dry-run", action="store_true")
    p_se.add_argument("--force", action="store_true")
    p_se.set_defaults(func=cmd_skills_export)

    return parser


def main(argv=None):
    _fix_windows_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 0
    fmt = _make_fmt(getattr(args, "no_color", False))
    try:
        return args.func(args, fmt)
    except UsageError as exc:
        print(fmt.err(f"error: {exc.message}"), file=sys.stderr)
        if exc.hint:
            print(fmt.dim(f"hint: {exc.hint}"), file=sys.stderr)
        return exc.exit_code
    except FormatError as exc:
        print(fmt.err(f"format error: {exc.message}"), file=sys.stderr)
        if exc.hint:
            print(fmt.dim(f"hint: {exc.hint}"), file=sys.stderr)
        return exc.exit_code
    except SafetyError as exc:
        print(fmt.err(f"safety error: {exc.message}"), file=sys.stderr)
        if exc.hint:
            print(fmt.dim(f"hint: {exc.hint}"), file=sys.stderr)
        return exc.exit_code
    except ConflictError as exc:
        print(fmt.err(f"conflict: {exc.message}"), file=sys.stderr)
        if exc.hint:
            print(fmt.dim(f"hint: {exc.hint}"), file=sys.stderr)
        return exc.exit_code
    except AgentPortError as exc:
        print(fmt.err(f"error: {exc.message}"), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        if getattr(args, "debug", False):
            raise
        print(fmt.err(f"unexpected error: {type(exc).__name__}: {exc}"), file=sys.stderr)
        print(fmt.dim("re-run with --debug for a traceback; please report this"), file=sys.stderr)
        return 70


if __name__ == "__main__":
    sys.exit(main())
