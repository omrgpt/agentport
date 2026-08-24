import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agentport import jsonc as aj
from agentport.errors import FormatError
from agentport import miniyaml, minitoml
from agentport.frontmatter import render_frontmatter, split_frontmatter
from agentport.formats import instructions as instr
from agentport.formats import mcp as mcpf
from agentport.formats import skills as skillf
from agentport.safety import atomic_write_text, ensure_within, mask_mapping

FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures"

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("miniyaml roundtrip")
def _():
    src = {
        "name": "demo",
        "description": "has: colon and #hash",
        "tools": ["a", "b c", 'q"uote'],
        "nested": {"x": 1, "y": True, "z": None},
    }
    parsed = miniyaml.parse(miniyaml.dump(src))
    assert parsed == src, (parsed, src)


@check("miniyaml block list of maps")
def _():
    parsed = miniyaml.parse("items:\n  - k1: v1\n    k2: v2\n  - plain\nend: 1\n")
    assert parsed["items"] == [{"k1": "v1", "k2": "v2"}, "plain"], parsed


@check("miniyaml literal block scalar")
def _():
    parsed = miniyaml.parse("desc: |\n  one\n  two\nnext: v\n")
    assert parsed["desc"] == "one\ntwo\n" and parsed["next"] == "v", parsed


@check("miniyaml rejects duplicate keys")
def _():
    try:
        miniyaml.parse("a: 1\na: 2\n")
        raise AssertionError("expected FormatError")
    except Exception as exc:
        assert "duplicate" in str(exc).lower()


@check("jsonc comments and trailing commas")
def _():
    text = '{\n  // hi\n  "a": [1, 2,], /* block */\n  "b": "x, y ]}",\n}\n'
    obj = aj.loads_jsonc(text)
    assert obj == {"a": [1, 2], "b": "x, y ]}"}, obj


@check("jsonc duplicate key rejection")
def _():
    try:
        aj.loads_strict('{"a": 1, "a": 2}')
        raise AssertionError("expected FormatError")
    except Exception as exc:
        assert "duplicate" in str(exc).lower()


@check("frontmatter split/render")
def _():
    meta, body = split_frontmatter("---\nname: x\ndescription: y z\n---\n\nBody here.\n")
    assert meta == {"name": "x", "description": "y z"} and body.startswith("Body"), (meta, body)
    rendered = render_frontmatter({"name": "n", "globs": ["*.py"]}, "body line\n")
    m2, b2 = split_frontmatter(rendered)
    assert m2 == {"name": "n", "globs": ["*.py"]} and b2 == "body line\n"


@check("instructions plain conversion")
def _():
    src = FIXTURES / "AGENTS.md"
    out = instr.convert_instructions(src.read_text(encoding="utf-8"), "claude")
    assert "Conventional commits only." in out
    assert not out.startswith("---")


@check("cursor rule parse and emit")
def _():
    text = (FIXTURES / "rule.mdc").read_text(encoding="utf-8")
    meta, body = instr.parse_cursor_rule(text)
    assert meta["description"] == "Rules for TypeScript files"
    assert meta["alwaysApply"] is False
    emitted = instr.convert_instructions(text, "agents")
    assert not emitted.startswith("---") and "strict TypeScript" in emitted
    back = instr.convert_instructions(
        (FIXTURES / "AGENTS.md").read_text(encoding="utf-8"), "cursor"
    )
    m3, b3 = instr.parse_cursor_rule(back)
    assert m3["alwaysApply"] is True and m3["globs"] == []
    assert m3["description"].strip() != ""


@check("mcp claude -> vscode/opencode/codex roundtrip")
def _():
    warnings = []
    text = (FIXTURES / "claude_mcp.json").read_text(encoding="utf-8")
    doc, raw = mcpf.parse_source_text(text, "claude", warnings)
    names = {s.name for s in doc.servers}
    assert names == {"filesystem", "github", "remote-docs"}, names
    gh = next(s for s in doc.servers if s.name == "github")
    assert gh.env.get("GITHUB_TOKEN") == "fake-token-value-not-a-real-credential"

    vstext, vsobj = mcpf.render_vscode(doc, None, False, "keep", warnings)
    vsdoc, _ = mcpf.parse_source_text(vstext, "vscode", [])
    assert {s.name for s in vsdoc.servers} == names
    rd = next(s for s in vsdoc.servers if s.name == "remote-docs")
    assert rd.transport in ("http", "sse") and rd.url == "https://docs.example.com/mcp"

    octext, _ = mcpf.render_opencode(doc, None, False, "keep", warnings)
    ocdoc, _ = mcpf.parse_source_text(octext, "opencode", [])
    fs = next(s for s in ocdoc.servers if s.name == "filesystem")
    assert fs.command == "npx" and "-y" in fs.args

    tomltext, _ = mcpf.render_codex(doc, None, False, "keep", False, warnings)
    codoc, _ = mcpf.parse_source_text(tomltext, "codex", [])
    assert {s.name for s in codoc.servers} == names


@check("mcp opencode source with disabled + environment")
def _():
    warnings = []
    text = (FIXTURES / "opencode.json").read_text(encoding="utf-8")
    doc, raw = mcpf.parse_source_text(text, "opencode", warnings)
    assert raw.get("theme") == "opal"
    search = next(s for s in doc.servers if s.name == "search")
    assert search.disabled is True
    curtext, _ = mcpf.render_family_json(doc, {}, "cursor", True, "keep", warnings)
    curdoc, _ = mcpf.parse_source_text(curtext, "cursor", [])
    cs = next(s for s in curdoc.servers if s.name == "search")
    assert cs.url == "https://search.example.com/mcp"
    back, _ = mcpf.render_opencode(curdoc, None, True, "keep", warnings)
    assert '"enabled": false' in back


@check("mcp merge keeps existing on conflict")
def _():
    warnings = []
    base_text = '{"mcpServers": {"fs": {"command": "old-cmd"}}}'
    incoming_text = '{"mcpServers": {"fs": {"command": "new-cmd"}, "extra": {"command": "e"}}}'
    base_doc, base_raw = mcpf.parse_source_text(base_text, "cursor", warnings)
    inc_doc, _ = mcpf.parse_source_text(incoming_text, "cursor", warnings)
    merged = mcpf.merge_documents(base_doc, inc_doc, "keep", False, warnings)
    fs = next(s for s in merged.servers if s.name == "fs")
    assert fs.command == "old-cmd"
    assert any("conflict" in w for w in warnings)
    merged2 = mcpf.merge_documents(base_doc, inc_doc, "overwrite", False, [])
    fs2 = next(s for s in merged2.servers if s.name == "fs")
    assert fs2.command == "new-cmd"


@check("secret masking")
def _():
    masked = mask_mapping({"GITHUB_TOKEN": "abc", "HOME": "/h", "API_KEY": "zzz"})
    assert masked == {"GITHUB_TOKEN": "***", "HOME": "/h", "API_KEY": "***"}


@check("skill parse validate normalize")
def _():
    warnings = []
    doc = skillf.load_skill_dir(FIXTURES / "skill-demo", warnings)
    issues = skillf.validate_skill(doc, dir_name="skill-demo", warnings=[])
    assert not issues, issues
    assert doc.allowed_tools == ["Bash(git status)", "Read"]
    bad = skillf.SkillDoc(name="Bad Name!", description="d", body="body that is long enough here")
    issues_bad = skillf.validate_skill(bad)
    assert len(issues_bad) >= 1
    fixed_name = skillf.normalize_skill_name("Bad Name!")
    assert fixed_name == "bad-name"


@check("skills opencode render moves allowed-tools into metadata")
def _():
    warnings = []
    doc = skillf.load_skill_dir(FIXTURES / "skill-demo", warnings)
    oc = skillf.render_skill_md(doc, target="opencode")
    m, _ = split_frontmatter(oc)
    assert "allowed-tools" not in m
    assert m["metadata"]["allowed-tools"] == "Bash(git status), Read"
    cl = skillf.render_skill_md(doc, target="claude")
    mc, _ = split_frontmatter(cl)
    assert mc["allowed-tools"] == "Bash(git status), Read"


@check("toml writer/reader fallback")
def _():
    obj = {"top": "v", "mcp_servers": {"a b": {"command": "x", "args": ["1", "2"], "env": {"K": "#v"}}}}
    text = minitoml.dumps(obj)
    assert "[mcp_servers." in text or '["a b"' in text or '"a b"' in text
    has_tomllib = sys.version_info >= (3, 11)
    if has_tomllib:
        import io
        import tomllib
        back = tomllib.load(io.BytesIO(text.encode()))
        assert back["mcp_servers"]["a b"]["env"]["K"] == "#v"


@check("path containment blocks escape")
def _():
    import tempfile as tf
    root = Path(tf.mkdtemp())
    try:
        ensure_within(root, "sub/file.txt")
        try:
            ensure_within(root, "../outside.txt")
            raise AssertionError("expected SafetyError")
        except Exception as exc:
            assert "escapes" in str(exc) or "outside" in str(exc).lower()
    finally:
        shutil.rmtree(root, ignore_errors=True)


@check("atomic write is idempotent and newline-stable")
def _():
    root = Path(tempfile.mkdtemp())
    try:
        p = root / "a" / "b.txt"
        atomic_write_text(p, "line\n")
        atomic_write_text(p, "line2\n")
        assert p.read_text(encoding="utf-8") == "line2\n"
        assert not list(root.rglob(".agentport-*"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_cli(argv):
    from agentport import cli

    saved = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        rc = cli.main(argv)
    finally:
        sys.stdout = saved
    return rc, buf.getvalue()


import io  # noqa: E402


@check("cli e2e detect/sync/mcp/skills in temp project")
def _():
    tmp = Path(tempfile.mkdtemp(prefix="agentport-selftest-"))
    try:
        shutil.copy(FIXTURES / "AGENTS.md", tmp / "AGENTS.md")
        rc, out = run_cli(["--root", str(tmp), "--no-color", "detect"])
        assert rc == 0 and "AGENTS.md" in out, (rc, out)

        rc, out = run_cli(["--root", str(tmp), "--no-color", "instructions", "sync", "--force"])
        assert rc == 0, (rc, out)
        assert (tmp / "CLAUDE.md").exists()
        assert (tmp / ".cursor" / "rules" / "main.mdc").exists()
        assert (tmp / ".github" / "copilot-instructions.md").exists()

        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "instructions", "convert", str(tmp / "AGENTS.md"),
            "--to", "gemini", "--out", "GEMINI.md", "--force",
        ])
        assert rc == 0 and (tmp / "GEMINI.md").exists()

        shutil.copy(FIXTURES / "claude_mcp.json", tmp / "src_mcp.json")
        skill_src = tmp / "skill-demo"
        shutil.copytree(FIXTURES / "skill-demo", skill_src)
        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "mcp", "show", "src_mcp.json",
        ])
        assert rc == 0 and "***" in out and "fake-token-value-not-a-real-credential" not in out, (rc, out)

        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "mcp", "convert", "src_mcp.json", "--to", "vscode",
        ])
        assert rc == 0 and (tmp / ".vscode" / "mcp.json").exists(), (rc, out)

        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "mcp", "convert", ".vscode/mcp.json", "--to", "opencode", "--out", "opencode.json",
        ])
        assert rc == 0, (rc, out)
        oc = aj.loads_jsonc((tmp / "opencode.json").read_text(encoding="utf-8"))
        assert "filesystem" in oc["mcp"]
        assert oc["mcp"]["filesystem"]["type"] == "local"
        assert isinstance(oc["mcp"]["filesystem"]["command"], list)

        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "skills", "validate", "skill-demo",
        ])
        assert rc == 0 and "PASS" in out, (rc, out)

        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "skills", "import", "skill-demo", "--to", "claude",
        ])
        assert rc == 0, (rc, out)
        installed = tmp / ".claude" / "skills" / "demo-skill" / "SKILL.md"
        assert installed.exists(), out

        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "skills", "export", "skill-demo", "--to", "cursor",
        ])
        assert rc == 0, (rc, out)
        assert (tmp / ".cursor" / "rules" / "demo-skill.mdc").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("cli refuses path traversal via --out")
def _():
    tmp = Path(tempfile.mkdtemp(prefix="agentport-sec-"))
    try:
        shutil.copy(FIXTURES / "AGENTS.md", tmp / "AGENTS.md")
        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "instructions", "convert", "AGENTS.md", "--to", "claude",
            "--out", "../escaped.md",
        ])
        assert rc == 2, (rc, out)
        assert not (tmp.parent / "escaped.md").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("conflict exit code without --force")
def _():
    tmp = Path(tempfile.mkdtemp(prefix="agentport-conflict-"))
    try:
        shutil.copy(FIXTURES / "AGENTS.md", tmp / "AGENTS.md")
        shutil.copy(FIXTURES / "AGENTS.md", tmp / "CLAUDE.md")
        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "instructions", "convert", "AGENTS.md", "--to", "claude",
        ])
        assert rc == 3, (rc, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("security: skill --name traversal refused")
def _():
    tmp = Path(tempfile.mkdtemp(prefix="agentport-namefix-"))
    try:
        sk = tmp / "sk"
        sk.mkdir()
        (sk / "SKILL.md").write_text(
            "---\nname: sk\ndescription: d\n---\nbody long enough here\n",
            encoding="utf-8",
        )
        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "skills", "import", "sk", "--to", "claude", "--force", "--name", "../../evil",
        ])
        assert rc == 1, (rc, out)
        rc, out = run_cli([
            "--root", str(tmp), "--no-color",
            "skills", "import", "sk", "--to", "claude", "--force", "--name", "..",
        ])
        assert rc == 1, (rc, out)
        assert not (tmp.parent / "evil").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("security: mcp diff masks secrets")
def _():
    from agentport.safety import mask_tree

    masked = mask_tree({"mcpServers": {"s": {"env": {"API_KEY": "v"}}}})
    assert masked["mcpServers"]["s"]["env"]["API_KEY"] == "***"


@check("security: yaml tab indentation rejected")
def _():
    try:
        miniyaml.parse("a:\n\tb: 1\n")
        raise AssertionError("expected FormatError")
    except Exception as exc:
        assert isinstance(exc, FormatError) or "tab" in str(exc).lower()


def main():
    failed = 0
    for name, fn in CHECKS:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"FAIL  {name}")
            print(f"      {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {name}")
    print()
    total = len(CHECKS)
    if failed:
        print(f"{failed}/{total} checks FAILED")
        return 1
    print(f"all {total} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
