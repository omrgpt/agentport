# Changelog

## 0.1.1 (unreleased)

### Security hardening (2026-08-24 adversarial campaign)

- Secret masking now recognizes `PAT` and `BEARER` as standalone key tokens
  (`GITHUB_PAT`, `AUTH_BEARER_X`, ...) in `mcp show` output.
- Skill bundle collection skips NTFS junctions / reparse-point directories
  with a warning, preventing files outside the skill folder from being copied
  into installed skills.
- Deeply nested YAML frontmatter (> 64 block levels) is refused with a clean
  format error instead of crashing with RecursionError.
- Non-object JSON/TOML config roots (e.g. a top-level JSON array) are refused
  with a clean format error instead of an internal AttributeError.
- The MCP `disabled` flag survives claude -> vscode -> claude round-trips
  (kept as a well-known extra for VS Code, which has no native flag); targets
  without any disable concept now emit a warning naming the affected servers
  instead of silently dropping the state.
- Cursor `.mdc` frontmatter: YAML 1.1 booleans (`yes/no/on/off/y/n`) are
  parsed as booleans and quoted when written as strings, so a rule authored
  with `alwaysApply: no` no longer round-trips into `alwaysApply: true`.
- Paths containing control characters are refused with a safety error
  instead of surfacing an OS error as an unexpected crash.
- Terminal hygiene: hostile content in skill names/descriptions/bodies can no
  longer inject ANSI escape sequences through the `skills validate` issue
  list or the `skills export --to markdown` stdout path.

### Fixed

- `skills import` no longer crashes with an internal NameError when reporting
  an invalid skill name; it prints the intended validation message.

## 0.1.2 - 2026-08-24

Deep-test campaign: ~7,600 adversarial/property/matrix scenarios. Found and
fixed 8 defects; added permanent regression suite (`tests/test_campaign_regressions.py`).

### Fixed
- YAML dump dropped trailing whitespace inside strings that qualified as
  "plain-safe"; such strings are now force-quoted (same for keys).
- YAML list-items whose first key mapped to a collection rendered as `- - x`,
  which re-parsed as a nested list instead of a map; collections now nest
  under the key with deeper indent.
- Empty list inside a list-item crashed the dumper (`IndexError`) and an
  empty-collection item emitted `- []` which the parser rejected after the
  branch reorder; both directions verified by round-trip.
- Parser branch order treated `- inner:` as a key named "- inner" instead of
  a nested sequence element; nested-list detection now takes priority,
  matching the YAML spec rule that plain scalars cannot start with "- ".
- `--prune` filtered only the parsed model, not the raw base document the
  renderer reads from, so pruned servers silently survived in output files.
- `--out <existing-directory>` returned exit code 3 (conflict) instead of
  exit code 2 (safety); directory/symlink check now runs before conflict
  checks on every write path.
- Skill install names matching Windows reserved device names (con, nul,
  com1-9, lpt1-9, ...) are rejected before any path is constructed; also
  enforced on normalize-rename and cursor export.
- JSON `\uD800`-style lone surrogates crashed downstream UTF-8 writes;
  all three parsers now reject unpaired surrogates with a clean format
  error via a stack-based (recursion-proof) validator.

### Hardened
- `ensure_encodable` iterative walker shared by miniyaml/minitoml/jsonc.
- Parent-directory creation failures during atomic writes surface as exit
  code 2 with actionable hints.

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
