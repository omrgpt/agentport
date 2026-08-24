# Contributing

## Setup

```
pip install -e .[dev]
python selftest.py
pytest tests -q
```

Both `selftest.py` (no dependencies required) and the pytest suite must pass.
CI runs both on Linux, Windows and macOS across Python 3.10-3.13.

## Design rules

- Zero runtime dependencies. Stdlib only. If you need a library, you are
  probably solving the wrong problem.
- Security first. Any new file-parsing or file-writing code must respect the
  rules in SECURITY.md (containment, caps, atomic writes, no eval).
- Deterministic output. Same input + same flags = byte-identical output.
  Never emit timestamps or absolute paths into generated files.
- Warnings over silence. When a conversion loses information (a feature the
  target format lacks), emit a `WARN` line; never drop data quietly.
- Every new target format needs: a registry entry, round-trip tests in both
  directions, and a fixture file.

## Adding a new instructions target

1. Add it to `INSTRUCTION_TARGETS` in `src/agentport/formats/instructions.py`.
2. If it needs frontmatter, add a renderer branch next to the cursor logic.
3. Add fixtures + tests.

## Adding a new MCP family

1. Add a parser in `src/agentport/formats/mcp.py` (map its schema onto the
   `ServerDef` IR) and a renderer.
2. Register the family in `MCP_FAMILIES`, `SERVER_KEY_BY_FAMILY` and the
   default-path tables.
3. Round-trip test against every other family.
