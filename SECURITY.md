# Security Policy

AgentPort treats configuration files as untrusted input. This document describes
the threat model, the guarantees the tool tries to provide, and how to report
vulnerabilities.

## Threat model

AgentPort reads and writes local config files (markdown, JSON/JSONC, TOML).
We assume these files can be attacker-controlled (for example a malicious
repository you cloned). The tool must never become an escalation path.

## Hardening rules enforced by the code

1. No network access. AgentPort performs zero HTTP/DNS operations; the import
   graph is asserted free of network/exec/deserialization modules in CI
   (`tests/test_security_adversarial.py::TestNoNetworkNoExec`).
2. No code execution from file contents. Parsers are hand-written and
   non-evaluating: YAML/TOML/JSON values are parsed into plain Python data
   only. There is no `eval`, `exec`, `pickle`, `yaml.load` (unsafe), or shell
   interpolation anywhere in the codebase.
3. Path containment. Every read/write target must resolve inside the working
   root (`--root`). Escapes like `../` or absolute paths outside the root are
   rejected with exit code 2. NUL bytes and NTFS alternate-data-stream style
   (`file.txt:stream`) path components are rejected outright. Skill install
   names (frontmatter name and `--name` override) must match the strict skill
   naming rule before any path is constructed from them.
4. Symlink discipline. Writes refuse to go through symlinks; skill bundle
   copying skips symlinks entirely. Known limitation: like any local CLI there
   is a theoretical TOCTOU window between the symlink check and the final
   rename; threat model assumes a single local user owns the working tree, so
   an attacker who can win that race already controls the project.
5. Size caps. Documents > 1 MiB and configs > 5 MiB are refused; individual
   skill files are capped at 512 KiB and bundles at 2 MiB / 200 files.
6. Binary rejection. Files containing NUL bytes or invalid UTF-8 are refused
   instead of being partially processed.
7. Duplicate-key rejection in JSON/JSONC to avoid silent override ambiguity.
8. Secret masking. `mcp show`, MCP diffs, and previews mask values whose keys
   look secret-bearing (`token`, `secret`, `key`, `password`,
   `authorization`, ...). Masking is applied key-aware recursively on both
   sides of every diff. Values are still written verbatim when converting,
   because that is what a converter is for; review diffs before committing
   generated global configs.
9. Executable deny-list. Skill bundles containing `.exe/.bat/.ps1/.sh/...`
   files skip those files with a warning on import.
10. Atomic writes. Output goes through a temp file + atomic rename so a crash
    can never leave half-written config behind; temp files are cleaned up on
    failure and never left behind on success.
11. Conservative defaults. Existing files are never overwritten without
    `--force`; MCP merges keep existing entries on conflict unless
    `--conflict overwrite`.
12. Terminal-output hygiene. All CLI output (warnings, errors, tables,
    diffs) passes through a control-character sanitizer so hostile content
    inside config files cannot inject ANSI terminal escape sequences.
13. Parser resource limits. YAML flow collections and TOML/JSON values have
    explicit nesting-depth limits that raise clean format errors instead of
    RecursionError; tab indentation in frontmatter is rejected before
    whitespace expansion can hide it. Block-style YAML nesting is likewise
    depth-capped, and non-object JSON/TOML document roots are refused with a
    clean format error.
14. Junction discipline. On Windows, skill bundle collection skips NTFS
    junctions / reparse-point directories (not just symlinks), so a hostile
    skill folder cannot pull in files from elsewhere on the machine.
15. Terminal-output hygiene applies to every emission path, including raw
    stdout writes (`skills export --to markdown`) and validation issue lists,
    so hostile skill names/descriptions/bodies cannot inject ANSI escapes.

## Known limitations

- Converting MCP configs necessarily copies environment variables (including
  secrets) between files. Review diffs before committing results.
- The bundled mini-YAML parser covers the subset used by SKILL.md / .mdc
  frontmatter (scalars, lists, maps, flow collections, block scalars). It
  intentionally rejects exotic YAML rather than approximating it.

## Reporting

Open a private security advisory via GitHub's "Report a vulnerability" button,
or contact the maintainers directly. Please do not open public issues for
security reports.
