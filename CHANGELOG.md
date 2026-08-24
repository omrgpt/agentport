# Changelog

## 0.1.1 - 2026-08-24

Security review pass (adversarial suite added; workspace audit passed 0 FAIL / 0 WARN).

### Fixed
- MCP `--diff` could print raw env-secret values on the "old" side:
  `mask_tree` recursed but never masked leaves. It is now key-aware and
  recursive (`safety.py`), so every diff side is masked.
- `skills import --name` bypassed skill-name validation, allowing contained
  path confusion (`--name ..`, `../../x`). Names are now validated against
  the strict naming rule before any path is constructed.
- Converting an MCP config into a brand-new file dropped doc-level context
  (`$schema`, `theme`, Codex `model`/non-MCP tables). New-file conversions now
  seed from the source's non-server keys.
- YAML tab-indented frontmatter slipped past validation because tabs were
  expanded before the check. The check now runs on raw leading whitespace.
- TOML fallback parser could die with RecursionError on deep arrays; it now
  raises a clean format error.

### Hardened
- Nesting-depth limits for YAML flow collections and TOML values.
- NUL-byte and NTFS alternate-data-stream (`file.txt:stream`) path components
  rejected in all read/write targets.
- All CLI output sanitized against terminal escape-sequence injection from
  hostile file content.
- `--out` pointing at an existing directory fails fast with exit code 2.
- Adversarial test suite: 30+ red-team cases (traversal, symlink write,
  depth bombs, duplicate keys, secret leakage probes, ANSI injection,
  merge-integrity, determinism). CI asserts the import graph stays free of
  network/exec/deserialization modules.
- Dependabot enabled for GitHub Actions and pip.

## 0.1.0 - 2026-08-24

Initial release.

- `instructions convert|sync`: bidirectional conversion between AGENTS.md,
  CLAUDE.md, GEMINI.md, AGENT.md (Amp), `.rules` (Zed), `.windsurfrules`,
  `.clinerules`, CONVENTIONS.md (Aider), `.github/copilot-instructions.md`,
  `.cursor/rules/*.mdc` and legacy `.cursorrules`.
- `mcp convert|show`: parse/emit MCP server configs for Claude Desktop,
  Cursor, VS Code, OpenCode, Codex (TOML), Windsurf and Gemini CLI with
  merge semantics, conflict policy, pruning, dry-run and masked previews.
- `skills validate|normalize|import|export`: spec-compliant SKILL.md handling
  with install targets for Claude (`.claude/skills`), OpenCode
  (`.opencode/skill`) and the agents spec (`.agents/skills`), plus export to
  Cursor rules or markdown snippets.
- `detect`: inventory of agent config files under a project root.
- Zero runtime dependencies; security-hardened parsing and writing.
