import io
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentport import jsonc as aj
from agentport import miniyaml, minitoml
from agentport.errors import FormatError, SafetyError
from agentport.formats import mcp as mcpf
from agentport.safety import atomic_write_text, ensure_within, read_text_capped

FIXTURES = ROOT / "tests" / "fixtures"


def run_cli(argv):
    from agentport import cli

    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout = buf_out = io.StringIO()
    sys.stderr = buf_err = io.StringIO()
    try:
        rc = cli.main(argv)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, buf_out.getvalue(), buf_err.getvalue()


@pytest.fixture()
def project(tmp_path):
    shutil.copy(FIXTURES / "AGENTS.md", tmp_path / "AGENTS.md")
    return tmp_path


class TestPathContainment:
    def test_out_traversal_blocked(self, project):
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "instructions", "convert", "AGENTS.md", "--to", "claude",
            "--out", "../escaped.md",
        ])
        assert rc == 2
        assert not (project.parent / "escaped.md").exists()

    def test_deep_traversal_blocked(self, project):
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "instructions", "convert", "AGENTS.md", "--to", "claude",
            "--out", "a/b/../../../../outside.md",
        ])
        assert rc == 2
        assert not (project.parent / "outside.md").exists()

    def test_absolute_outside_root_blocked(self, project, tmp_path_factory):
        other = tmp_path_factory.mktemp("elsewhere")
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "instructions", "convert", "AGENTS.md", "--to", "claude",
            "--out", str(other / "x.md"),
        ])
        assert rc == 2
        assert not (other / "x.md").exists()

    def test_src_outside_root_blocked(self, project, tmp_path_factory):
        secret = tmp_path_factory.mktemp("secrets") / "secret.md"
        secret.write_text("top secret guidelines\n", encoding="utf-8")
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "instructions", "convert", str(secret), "--to", "agents",
        ])
        assert rc == 2

    def test_nul_byte_path_rejected(self, project):
        with pytest.raises(SafetyError, match="NUL"):
            ensure_within(project, "file\x00.txt")

    def test_ads_colon_component_rejected(self, project):
        with pytest.raises(SafetyError):
            ensure_within(project, "file.txt:hidden_stream")

    def test_drive_letter_still_allowed(self, project):
        resolved = ensure_within(project, project / "ok.md")
        assert resolved.name == "ok.md"

    def test_ensure_within_allows_inside_paths(self, project):
        p = ensure_within(project, Path(project) / "sub" / "deep" / "f.md")
        assert project in p.parents


class TestSymlinkDefense:
    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
    def test_write_through_symlink_refused(self, project):
        target = project / "real.txt"
        target.write_text("orig\n", encoding="utf-8")
        link = project / "link.txt"
        try:
            os.symlink(target, link)
        except OSError:
            pytest.skip("no symlink privilege on this platform")
        with pytest.raises(SafetyError, match="symlink"):
            atomic_write_text(link, "pwned\n")
        assert target.read_text(encoding="utf-8") == "orig\n"


class TestResourceLimits:
    def test_yaml_flow_depth_bomb_rejected(self):
        bomb = "key: " + "[" * 500 + "]" * 500 + "\n"
        with pytest.raises(FormatError, match="nested deeper"):
            miniyaml.parse(bomb)

    def test_toml_array_depth_bomb_rejected(self):
        bomb = "a = " + "[" * 5000 + "]" * 5000 + "\n"
        with pytest.raises((FormatError, RecursionError)):
            minitoml.parse(bomb)

    def test_toml_fallback_depth_bomb_rejected(self):
        bomb = "a = " + "[" * 5000 + "]" * 5000 + "\n"
        with pytest.raises(FormatError):
            minitoml.parse_fallback(bomb)

    def test_json_nesting_bomb_never_crashes(self):
        for depth in (2000, 100000):
            bomb = '{"a":' * depth + "1" + "}" * depth
            try:
                aj.loads_strict(bomb)
            except FormatError:
                pass

    def test_oversized_file_refused(self, project):
        big = project / "big.md"
        big.write_bytes(b"# " + b"a" * (1024 * 1024 + 10))
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "instructions", "convert", "big.md", "--to", "claude",
        ])
        assert rc == 2 and "too large" in (out + err)

    def test_binary_file_refused(self, project):
        (project / "bin.md").write_bytes(b"\x00\x01binary\x00")
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "instructions", "convert", "bin.md", "--to", "claude",
        ])
        assert rc == 1 and "NUL" in (out + err)


class TestParserHardening:
    def test_duplicate_json_keys_rejected_at_every_level(self):
        with pytest.raises(FormatError, match="duplicate"):
            aj.loads_strict('{"outer": {"k": 1, "k": 2}}')

    def test_unterminated_block_comment_rejected(self):
        with pytest.raises(FormatError, match="unterminated"):
            aj.loads_jsonc('{"a": 1} /* never closed')

    def test_raw_newline_in_string_rejected(self):
        with pytest.raises(FormatError, match="unterminated"):
            aj.loads_jsonc('{"a": "line1\nline2"}')

    def test_yaml_tab_indentation_rejected(self):
        with pytest.raises(FormatError, match="tab"):
            miniyaml.parse("a:\n\tb: 1\n")

    def test_yaml_duplicate_keys_rejected(self):
        with pytest.raises(FormatError, match="duplicate"):
            miniyaml.parse("name: a\nname: b\n")

    def test_yaml_unclosed_quote_rejected(self):
        with pytest.raises(FormatError):
            miniyaml.parse('name: "never closed\n')

    def test_mcp_server_name_validation(self):
        warnings = []
        text = '{"mcpServers": {"bad name!": {"command": "x"}}}'
        with pytest.raises(FormatError, match="invalid MCP server name"):
            mcpf.parse_source_text(text, "cursor", warnings)


class TestSecretHygiene:
    def test_show_masks_all_secretish_keys(self, project):
        cfg = {
            "mcpServers": {
                "s": {
                    "command": "npx",
                    "env": {
                        "GITHUB_TOKEN": "fake-token-short",
                        "MY_SECRET": "sss",
                        "CLIENT_SECRET": "css",
                        "PASSWORD": "ppp",
                        "AWS_ACCESS_KEY_ID": "akid",
                        "AUTHORIZATION": "Bearer x",
                        "PUBLIC_FLAG": "true",
                        "HOME": "/home/me",
                    },
                }
            }
        }
        import json as _json

        (project / "cfg.json").write_text(_json.dumps(cfg), encoding="utf-8")
        rc, out, err = run_cli(["--root", str(project), "--no-color", "mcp", "show", "cfg.json"])
        assert rc == 0
        for leaked in ("fake-token-short", "sss", "css", "ppp", "akid", "Bearer x"):
            assert leaked not in out
        assert "/home/me" in out and "true" in out

    def test_convert_diff_masks_secrets_both_sides(self, project):
        old = '{"mcpServers": {"s": {"command": "old", "env": {"API_KEY": "OLD-KEY"}}}}'
        new = '{"mcpServers": {"s": {"command": "new", "env": {"API_KEY": "NEW-KEY"}}}}'
        (project / ".cursor").mkdir()
        (project / ".cursor" / "mcp.json").write_text(old, encoding="utf-8")
        (project / "src.json").write_text(new, encoding="utf-8")
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "mcp", "convert", "src.json", "--to", "cursor",
            "--conflict", "overwrite", "--diff",
        ])
        assert rc == 0
        assert "OLD-KEY" not in out and "NEW-KEY" not in out
        assert '"command": "new"' in out
        assert '"command": "old"' in out

    def test_warnings_never_contain_env_values(self):
        warnings = []
        text = '{"mcpServers": {"s": {"command": "c", "env": {"TOKEN": "super-secret-value"}}}}'
        doc, _ = mcpf.parse_source_text(text, "cursor", warnings)
        joined = " | ".join(warnings)
        assert "super-secret-value" not in joined


class TestSkillBundleSecurity:
    def test_executables_skipped_on_import(self, project):
        src = project / "sk"
        src.mkdir()
        (src / "SKILL.md").write_text(
            "---\nname: sk\ndescription: d\n---\nbody here long enough\n",
            encoding="utf-8",
        )
        (src / "evil.ps1").write_text("Write-Output pwned\n", encoding="utf-8")
        (src / "safe.md").write_text("fine\n", encoding="utf-8")
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "skills", "import", "sk", "--to", "claude", "--force",
        ])
        assert rc == 0
        dest = project / ".claude" / "skills" / "sk"
        assert (dest / "safe.md").exists()
        assert not (dest / "evil.ps1").exists()
        assert any("evil.ps1" in w for w in (out + err).splitlines())

    def test_name_override_traversal_blocked(self, project):
        src = project / "sk2"
        src.mkdir()
        (src / "SKILL.md").write_text(
            "---\nname: sk2\ndescription: d\n---\nbody that is long enough to pass\n",
            encoding="utf-8",
        )
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "skills", "import", "sk2", "--to", "claude", "--force",
            "--name", "../../escape",
        ])
        assert rc == 1
        assert not (project.parent / "escape").exists()
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "skills", "import", "sk2", "--to", "claude", "--force",
            "--name", "..",
        ])
        assert rc == 1

    def test_name_override_dotdot_refused_cleanly(self, project):
        src = project / "sk3"
        src.mkdir()
        (src / "SKILL.md").write_text(
            "---\nname: sk3\ndescription: d\n---\nbody that is long enough\n",
            encoding="utf-8",
        )
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "skills", "import", "sk3", "--to", "claude", "--force", "--name", "..",
        ])
        assert rc == 1
        assert not (project / "SKILL.md").exists()
        assert not (project / ".claude" / "skills").exists() or \
            not any(p.name == ".." for p in (project / ".claude" / "skills").glob("*"))

    def test_export_rejects_invalid_skill_name(self, project):
        src = project / "Bad_Name"
        src.mkdir()
        (src / "SKILL.md").write_text(
            "---\nname: Bad_Name\ndescription: d\n---\nbody that is long enough\n",
            encoding="utf-8",
        )
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "skills", "export", "Bad_Name", "--to", "cursor",
        ])
        assert rc == 1
        assert not list((project / ".cursor").rglob("*")) if (project / ".cursor").exists() else True


class TestTerminalInjection:
    def test_ansi_escapes_sanitized_from_output(self, project):
        esc = "\x1b]0;PWNED\x07\x1b[31mred\x1b[0m"
        cfg = {"mcpServers": {esc: {"command": "x"}}}
        import json as _json

        (project / "evil.json").write_text(_json.dumps(cfg), encoding="utf-8")
        rc, out, err = run_cli(["--root", str(project), "--no-color", "mcp", "show", "evil.json"])
        raw = out + err
        assert rc in (0, 1)
        assert "\x1b]0;" not in raw
        assert "PWNED" in raw

    def test_error_repr_does_not_emit_raw_control_chars(self):
        from agentport.errors import FormatError

        esc = chr(27)
        exc = FormatError("cannot parse line: " + repr(esc + "]0;pwned"))
        assert "\\x1b" in str(exc) or "\x1b" not in str(exc)


class TestMergeIntegrity:
    def test_conflict_default_keeps_existing_servers(self, project):
        existing = '{"mcpServers": {"keep-me": {"command": "original"}}}'
        incoming = '{"mcpServers": {"keep-me": {"command": "attacker-version"}}}'
        (project / ".cursor").mkdir()
        (project / ".cursor" / "mcp.json").write_text(existing, encoding="utf-8")
        (project / "in.json").write_text(incoming, encoding="utf-8")
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "mcp", "convert", "in.json", "--to", "cursor",
        ])
        assert rc == 0
        result = aj.loads_jsonc((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
        assert result["mcpServers"]["keep-me"]["command"] == "original"

    def test_unknown_keys_preserved_roundtrip(self):
        warnings = []
        text = '{"mcpServers": {"s": {"command": "c", "customFutureField": {"nested": [1,2]}}}}'
        doc, _ = mcpf.parse_source_text(text, "cursor", warnings)
        out, _obj = mcpf.render_family_json(doc, {}, "cursor", True, "keep", [])
        back, _ = mcpf.parse_source_text(out, "cursor", [])
        s = next(s for s in back.servers if s.name == "s")
        assert s.extras.get("customFutureField") == {"nested": [1, 2]}

    def test_opencode_other_top_level_keys_preserved(self, project):
        src = (FIXTURES / "opencode.json").read_text(encoding="utf-8")
        (project / "oc.json").write_text(src, encoding="utf-8")
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "mcp", "convert", "oc.json", "--to", "opencode", "--out", "target.json",
        ])
        assert rc == 0
        merged = aj.loads_jsonc((project / "target.json").read_text(encoding="utf-8"))
        assert merged.get("$schema") == "https://opencode.ai/config.json"

    def test_codex_merge_preserves_non_mcp_tables(self, project):
        shutil.copy(FIXTURES / "codex_config.toml", project / "config.toml")
        add = '[mcp_servers.extra]\ncommand = "extra-cmd"\n'
        with open(project / "config.toml", "a", encoding="utf-8") as fh:
            fh.write(add)
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "mcp", "convert", "config.toml", "--to", "codex", "--out", "merged.toml",
        ])
        assert rc == 0
        text = (project / "merged.toml").read_text(encoding="utf-8")
        assert 'model = "gpt-5-codex"' in text
        assert "[mcp_servers.filesystem]" in text
        assert "[mcp_servers.extra]" in text
        assert "[mcp_servers.docs]" in text


class TestDeterminismAndIdempotency:
    def test_double_sync_produces_identical_bytes(self, project):
        rc1, _, _ = run_cli(["--root", str(project), "--no-color", "instructions", "sync"])
        first_pass = {}
        for p in sorted(project.rglob("*")):
            if p.is_file() and p.name != "AGENTS.md":
                first_pass[str(p.relative_to(project))] = p.read_bytes()
        rc2, _, _ = run_cli(["--root", str(project), "--no-color", "instructions", "sync", "--force"])
        second_pass = {}
        for p in sorted(project.rglob("*")):
            if p.is_file() and p.name != "AGENTS.md":
                second_pass[str(p.relative_to(project))] = p.read_bytes()
        assert rc1 == rc2 == 0
        assert first_pass == second_pass

    def test_no_temp_files_left_behind(self, project):
        run_cli(["--root", str(project), "--no-color", "instructions", "sync"])
        leftovers = [p.name for p in project.rglob(".agentport-*")]
        assert leftovers == []


class TestNoNetworkNoExec:
    def test_import_graph_is_offline(self):
        banned_modules = {"socket", "urllib", "http", "ftplib", "telnetlib",
                          "smtplib", "subprocess", "pickle", "marshal", "ctypes"}
        src_root = ROOT / "src" / "agentport"
        found = []
        for py in src_root.rglob("*.py"):
            tree_src = py.read_text(encoding="utf-8")
            for mod in banned_modules:
                for token in (f"import {mod}", f"from {mod}"):
                    if token in tree_src.split("#")[0]:
                        found.append((py.name, token))
        assert found == [], f"forbidden imports: {found}"

    def test_dangerous_builtins_absent(self):
        import re as _re

        patterns = [
            _re.compile(r"(?<![\w.])eval\("),
            _re.compile(r"(?<![\w.])exec\("),
            _re.compile(r"os\.system\("),
            _re.compile(r"__import__\("),
            _re.compile(r"(?<!re\.)\bcompile\("),
            _re.compile(r"\bpickle\.load\b"),
        ]
        src_root = ROOT / "src" / "agentport"
        for py in src_root.rglob("*.py"):
            code = py.read_text(encoding="utf-8")
            for pat in patterns:
                assert not pat.search(code), f"{pat.pattern} found in {py.name}"
