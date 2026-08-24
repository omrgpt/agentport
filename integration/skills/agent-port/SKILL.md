---
name: agent-port
description: Convert, sync and validate AI coding-agent configuration files with the agentport CLI - instructions (AGENTS.md/CLAUDE.md/cursor rules/copilot), MCP server configs across Claude/Cursor/VS Code/OpenCode/Codex/Windsurf/Gemini, and SKILL.md skill folders. Use when the user wants to port guidelines or MCP servers from one assistant to another, detect which agent configs a project has, or keep multiple assistants' configs in sync.
license: MIT
metadata:
  tool: agentport
  version: 0.1.0
---

# AgentPort

Convert coding-agent config formats into each other. Zero dependencies,
offline, security-hardened.

## Core rules

1. Run `agentport --root <project> detect` first to inventory what exists.
   Global flags (--root, --no-color, --strict, --debug) go BEFORE the
   subcommand; subcommand flags go after it.
2. Never use `--force` unless the user explicitly asked to overwrite.
3. Always prefer `--dry-run` plus `--diff` first; show the user the diff.
4. Global MCP targets (claude, codex, windsurf, gemini) require `--out`;
   suggest the canonical path but let the user confirm.
5. `mcp show` output masks secrets. `mcp convert` copies them - warn the user
   before writing files containing env secrets.

## Instructions

Targets: agents, claude, gemini, amp, zed, windsurf, cline, aider, copilot,
cursor, cursor-legacy.

```bash
agentport instructions sync --from agents --dry-run
agentport instructions sync --from agents            # skips existing targets
agentport instructions convert AGENTS.md --to cursor --dry-run --diff
```

Cursor rules get frontmatter (description/globs/alwaysApply). Derive a good
one-line description from the document before converting to cursor.

## MCP

Families: claude, cursor, vscode, opencode, codex, windsurf, gemini.

```bash
agentport mcp show .cursor/mcp.json                  # masked preview
agentport mcp convert src.json --to vscode --dry-run --diff
agentport mcp convert src.json --to vscode           # merge is the default
agentport mcp convert src.json --to opencode --out opencode.json
agentport mcp convert src.json --conflict overwrite  # incoming wins on clash
agentport mcp convert src.json --prune               # drop servers missing in source
```

OpenCode local servers use `"command": [cmd, ...args]` and `"environment"`;
agentport translates both directions automatically.

## Skills

```bash
agentport skills validate ./my-skill
agentport skills normalize ./my-skill --force        # fix name/description issues
agentport skills import ./my-skill --to claude       # .claude/skills/<name>
agentport skills import ./my-skill --to opencode     # .opencode/skill/<name>
agentport skills import ./my-skill --to agents       # .agents/skills/<name>
agentport skills export ./my-skill --to cursor       # .cursor/rules/<name>.mdc
```

Validation failures block import on purpose: fix them with normalize first.

## Exit codes

0 ok | 1 usage/format | 2 safety refusal | 3 conflict (needs --force) |
70 bug (report it).
