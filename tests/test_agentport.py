import io
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture()
def project(tmp_path):
    shutil.copy(FIXTURES / "AGENTS.md", tmp_path / "AGENTS.md")
    return tmp_path


def run_cli(argv):
    from agentport import cli

    saved = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        rc = cli.main(argv)
    finally:
        sys.stdout = saved
    return rc, buf.getvalue()


def test_miniyaml_roundtrip():
    from agentport import miniyaml

    src = {
        "name": "demo",
        "description": "has: colon",
        "tools": ["a", "b c"],
        "nested": {"x": 1, "y": True, "z": None},
    }
    assert miniyaml.parse(miniyaml.dump(src)) == src


def test_jsonc_features():
    from agentport import jsonc as aj

    obj = aj.loads_jsonc('{\n // c\n "a": [1,], /* b */ "s": "]}," \n}')
    assert obj == {"a": [1], "s": "]},"
    }
    with pytest.raises(Exception, match="duplicate"):
        aj.loads_strict('{"k": 1, "k": 2}')


def test_instructions_sync_writes_all_targets(project):
    rc, out = run_cli(["--root", str(project), "--no-color", "instructions", "sync"])
    assert rc == 0
    assert (project / "CLAUDE.md").exists()
    assert (project / "GEMINI.md").exists()
    assert (project / ".cursor" / "rules" / "main.mdc").exists()
    assert (project / ".github" / "copilot-instructions.md").exists()
    assert (project / ".cursorrules").exists()
    mdc = (project / ".cursor" / "rules" / "main.mdc").read_text(encoding="utf-8")
    assert mdc.startswith("---")
    assert "alwaysApply: true" in mdc


def test_mcp_conversion_chain_masks_secrets(project):
    shutil.copy(FIXTURES / "claude_mcp.json", project / "mcp.json")
    rc, out = run_cli(["--root", str(project), "--no-color", "mcp", "show", "mcp.json"])
    assert rc == 0 and "***" in out and "fake-token-value-not-a-real-credential" not in out

    rc, out = run_cli([
        "--root", str(project), "--no-color",
        "mcp", "convert", "mcp.json", "--to", "opencode", "--out", "opencode.json",
    ])
    assert rc == 0
    text = (project / "opencode.json").read_text(encoding="utf-8")
    fs = __import__("json").loads(text)["mcp"]["filesystem"]
    assert fs["type"] == "local" and fs["command"][0] == "npx"

    rc, out = run_cli([
        "--root", str(project), "--no-color",
        "mcp", "convert", "opencode.json", "--to", "codex", "--out", "codex.toml",
    ])
    assert rc == 0
    toml_text = (project / "codex.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.filesystem]" in toml_text


def test_codex_toml_source_parses(project):
    shutil.copy(FIXTURES / "codex_config.toml", project / "config.toml")
    rc, out = run_cli([
        "--root", str(project), "--no-color",
        "mcp", "convert", "config.toml", "--to", "vscode", "--out", ".vscode/mcp.json",
    ])
    assert rc == 0
    data = __import__("json").loads(
        (project / ".vscode" / "mcp.json").read_text(encoding="utf-8")
    )
    assert set(data["servers"].keys()) >= {"filesystem", "docs"}
    assert data["servers"]["docs"]["type"] == "http"


def test_skills_lifecycle(project):
    shutil.copytree(FIXTURES / "skill-demo", project / "skill-demo")
    rc, out = run_cli(["--root", str(project), "--no-color", "skills", "validate", "skill-demo"])
    assert rc == 0 and "PASS" in out

    rc, out = run_cli([
        "--root", str(project), "--no-color", "skills", "import", "skill-demo", "--to", "claude",
    ])
    assert rc == 0
    installed = project / ".claude" / "skills" / "demo-skill" / "SKILL.md"
    assert installed.exists()

    rc, out = run_cli([
        "--root", str(project), "--no-color",
        "skills", "import", "skill-demo", "--to", "opencode",
    ])
    assert rc == 0
    oc_skill = project / ".opencode" / "skill" / "demo-skill" / "SKILL.md"
    oc_meta = oc_skill.read_text(encoding="utf-8")
    assert "allowed-tools" not in oc_meta.split("metadata:")[0]


def test_traversal_blocked(project):
    rc, out = run_cli([
        "--root", str(project), "--no-color",
        "instructions", "convert", "AGENTS.md", "--to", "claude",
        "--out", "../escape.md",
    ])
    assert rc == 2


def test_conflict_exit_code(project):
    shutil.copy(FIXTURES / "AGENTS.md", project / "CLAUDE.md")
    rc, out = run_cli([
        "--root", str(project), "--no-color",
        "instructions", "convert", "AGENTS.md", "--to", "claude",
    ])
    assert rc == 3
