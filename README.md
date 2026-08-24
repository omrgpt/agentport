# AgentPort

**One config, every coding agent.** A lightweight, security-first converter
between the config formats of AI coding assistants.

You write your guidelines once. AgentPort ports them everywhere.

```
                ┌──────────────┐
   AGENTS.md ──▶│              ├──▶ CLAUDE.md            (Claude Code)
   SKILL.md  ──▶│  agentport   ├──▶ .cursor/rules/*.mdc  (Cursor)
   mcp.json  ──▶│              ├──▶ GEMINI.md / .rules / .clinerules / ...
                └──────────────┼──▶ .github/copilot-instructions.md
                               ├──▶ opencode.json / codex config.toml
                               └──▶ .claude/skills / .opencode/skill / ...
```

## Why

Every coding assistant invented its own instruction format: `AGENTS.md`,
`CLAUDE.md`, `.cursorrules`, `copilot-instructions.md`, `.windsurfrules`,
`.clinerules`, `GEMINI.md`, ... — plus per-tool MCP server configs and skill
formats. Keeping them in sync by hand is tedious and error-prone. Existing
tools solve only one slice (usually rules propagation) and tend to pull in a
dependency tree.

AgentPort is the missing **converter**: any format → any other format, for
instructions *and* MCP configs *and* skills.

- **Zero dependencies.** Pure Python stdlib (3.10+). One `pip install`, no
  supply-chain surface.
- **Security-first.** No network. No eval. Path containment, size caps,
  symlink refusal, duplicate-key rejection, atomic writes, secret masking.
  See [SECURITY.md](SECURITY.md).
- **Lossless where possible, honest where not.** When a target format lacks a
  feature (e.g. Codex has no "disabled" flag for servers), AgentPort warns
  instead of silently dropping data.
- **Deterministic output.** Same input + same flags = byte-identical files.

## Install

```
pip install .
```

or run straight from a checkout:

```
python -m agentport --help        # from repo root (uses src/)
python selftest.py                # dependency-free verification
```

## Quick start

```
# What does my project already have?
agentport detect

# Write AGENTS.md once, propagate to all other tools
agentport instructions sync --from agents          # skips existing files
agentport instructions sync --force                # overwrite all targets

# Single conversion, with preview
agentport instructions convert AGENTS.md --to cursor --dry-run --diff
agentport instructions convert AGENTS.md --to copilot --force

# Port your Claude Desktop MCP servers into VS Code + OpenCode + Codex
agentport mcp convert claude_desktop_config.json --to vscode
agentport mcp convert claude_desktop_config.json --to opencode --out opencode.json
agentport mcp convert claude_desktop_config.json --to codex   --out ~/.codex/config.toml

# Inspect an MCP config without leaking secrets
agentport mcp show .cursor/mcp.json

# Skills: validate against the spec, then install everywhere
agentport skills validate ./my-skill
agentport skills import ./my-skill --to claude     # -> .claude/skills/my-skill
agentport skills import ./my-skill --to opencode   # -> .opencode/skill/my-skill
agentport skills export  ./my-skill --to cursor    # -> .cursor/rules/my-skill.mdc
```

## Supported formats

### Instructions (`instructions convert|sync`)

| Key             | File                              | Tool                 |
|-----------------|-----------------------------------|----------------------|
| `agents`        | `AGENTS.md`                       | Codex, OpenCode, many |
| `claude`        | `CLAUDE.md`                       | Claude Code          |
| `gemini`        | `GEMINI.md`                       | Gemini CLI           |
| `amp`           | `AGENT.md`                        | Amp                  |
| `zed`           | `.rules`                          | Zed                  |
| `windsurf`      | `.windsurfrules`                  | Windsurf             |
| `cline`         | `.clinerules`                     | Cline                |
| `aider`         | `CONVENTIONS.md`                  | Aider                |
| `copilot`       | `.github/copilot-instructions.md` | GitHub Copilot       |
| `cursor`        | `.cursor/rules/*.mdc`             | Cursor (frontmatter) |
| `cursor-legacy` | `.cursorrules`                    | Cursor (legacy)      |

Cursor `.mdc` files get synthesized frontmatter (`description`, `globs`,
`alwaysApply`); converting *from* `.mdc` strips it back off. Override with
`--description/--globs/--always-apply`.

### MCP server configs (`mcp convert|show`)

| Family    | Typical file                          | stdio | http/sse | disabled flag |
|-----------|---------------------------------------|-------|----------|---------------|
| `claude`  | `claude_desktop_config.json`          | yes   | warn     | no            |
| `cursor`  | `.cursor/mcp.json`                    | yes   | yes      | via extra key |
| `vscode`  | `.vscode/mcp.json`                    | yes   | yes      | no            |
| `opencode`| `opencode.json(c)` (`"mcp"` section)  | yes   | yes      | `enabled`     |
| `codex`   | `~/.codex/config.toml`                | yes   | recent   | no            |
| `windsurf`| `~/.codeium/windsurf/mcp_config.json` | yes   | warn     | no            |
| `gemini`  | `~/.gemini/settings.json`             | yes   | warn     | no            |

Semantics:

- Default is a **merge**: existing servers are kept; name collisions keep the
  existing entry unless `--conflict overwrite`; `--replace` discards the old
  file content; `--prune` drops servers absent from the source.
- Project-scoped targets (cursor/vscode/opencode) merge in place at their
  canonical path. Machine-global targets (claude/codex/windsurf/gemini)
  require explicit `--out` so AgentPort never touches your home directory on
  its own.
- Unknown keys are preserved per-server so round-trips don't shred exotic
  options. Existing top-level keys (`$schema`, `theme`, `inputs`, ...) are
  preserved too.
- OpenCode's `"command": [cmd, arg...]` array form and `environment` map are
  translated automatically; `enabled: false` maps to the disabled flag.

### Skills (`skills validate|normalize|import|export`)

Validates the common SKILL.md spec (lowercase-hyphen `name` ≤ 64 chars,
`description` ≤ 1024 chars, body required). `normalize` auto-fixes casing,
whitespace and over-long descriptions. Import copies auxiliary files under
strict caps (200 files / 2 MiB) while skipping symlinks and executables.
Claude-only `allowed-tools` is moved under `metadata` for non-Claude targets.

## Safety & exit codes

| Code | Meaning                                                        |
|------|----------------------------------------------------------------|
| 0    | success                                                        |
| 1    | usage/format errors (bad target, invalid frontmatter, ...)     |
| 2    | safety refusal (path escape, oversized/binary input, symlink)  |
| 3    | conflict (existing file; re-run with `--force`)                |
| 70   | unexpected bug (please report)                                 |

Add `--strict` to turn warnings into failures, `--dry-run`/`--diff` to
preview, `--root <dir>` to operate on another project.

## Use it from your agent

A ready-made skill is included so Claude Code / OpenCode / agents-spec
clients can drive AgentPort themselves:

```
agentport skills import integration/skills/agent-port --to claude
```

## Comparison

| | AgentPort | Ruler | ad-hoc scripts |
|---|---|---|---|
| Instructions propagation | yes | yes | partial |
| Bidirectional conversion | yes | no | no |
| MCP config conversion | yes (7 clients) | no | rare |
| Skills handling | yes | no | no |
| Dependencies | **0** | Node.js tree | varies |

## Development

```
pip install -e .[dev]
python selftest.py && pytest tests -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for design rules and how to add a new
target format (it's a registry entry plus round-trip tests).

## License

MIT — see [LICENSE](LICENSE).
