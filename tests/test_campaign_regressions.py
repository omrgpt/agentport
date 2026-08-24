import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentport import miniyaml, minitoml
from agentport import jsonc as aj
from agentport.errors import FormatError


def run_cli(argv):
    from agentport import cli

    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout = buf_o = io.StringIO()
    sys.stderr = buf_e = io.StringIO()
    try:
        rc = cli.main(argv)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, buf_o.getvalue(), buf_e.getvalue()


class TestYamlFidelity:
    def test_trailing_space_string_roundtrip(self):
        obj = {"k": "trailing ", "lead": " leading", "both": " x "}
        assert miniyaml.parse(miniyaml.dump(obj)) == obj

    def test_key_with_edge_whitespace(self):
        obj = {"spaced key ": "v"}
        assert miniyaml.parse(miniyaml.dump(obj)) == obj

    def test_quoted_value_with_trailing_space_in_block(self):
        text = 'a: "keep me "\nb: plain\n'
        assert miniyaml.parse(text) == {"a": "keep me ", "b": "plain"}

    def test_nested_list_of_lists(self):
        obj = {"m": [[1, 2], [3], []]}
        assert miniyaml.parse(miniyaml.dump(obj)) == obj

    def test_dict_with_collection_first_value_inside_list(self):
        obj = {"items": [{"tags": ["a", "b"]}, "tail", {"n": None}]}
        assert miniyaml.parse(miniyaml.dump(obj)) == obj

    def test_empty_collections_inside_list_items(self):
        obj = {"xs": [{}, [], {"k": ""}]}
        assert miniyaml.parse(miniyaml.dump(obj)) == obj

    def test_deep_mixed_nesting(self):
        obj = {
            "outer": [
                {"meta": {"v": 1}, "tags": ["x"], "none": None},
                [{"inner": [True, False]}, []],
            ]
        }
        assert miniyaml.parse(miniyaml.dump(obj)) == obj


class TestSurrogateRejection:
    def test_json_lone_surrogate_rejected(self):
        with pytest.raises(FormatError, match="surrogat"):
            aj.loads_strict('{"k": "\\ud800"}')

    def test_yaml_surrogate_escape_rejected(self):
        with pytest.raises(FormatError):
            miniyaml.parse('k: "\\ud800"\n')

    def test_valid_unicode_still_accepted(self):
        assert aj.loads_strict('{"k": "\\u00e9\\u4e2d"}') == {"k": "\u00e9\u4e2d"}


class TestReservedNames:
    def _skill(self, project, name="ok-skill"):
        d = project / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\nbody long enough\n",
            encoding="utf-8",
        )
        return d

    def test_import_reserved_windows_name_blocked(self, project, tmp_path):
        self._skill(project)

        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "skills", "import", "ok-skill", "--to", "claude", "--force",
            "--name", "con",
        ])
        assert rc == 1 and "reserved" in (out + err).lower()

    def test_export_reserved_name_blocked(self, project):
        d = project / "aux"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: aux\ndescription: d\n---\nbody long enough\n",
            encoding="utf-8",
        )

        rc, out, err = run_cli([
            "--root", str(project), "--no-color", "skills", "export", "aux", "--to", "cursor",
        ])
        assert rc == 1


class TestOutIsDirectory:
    def test_instructions_out_dir_exit_2(self, project):

        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "instructions", "convert", "AGENTS.md", "--to", "claude",
            "--out", ".",
        ])
        assert rc == 2 and "directory" in (out + err)

    def test_mcp_out_dir_exit_2(self, project):
        (project / "s.json").write_text('{"mcpServers": {}}', encoding="utf-8")
        (project / ".cursor").mkdir()
        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "mcp", "convert", "s.json", "--to", "cursor", "--out", ".cursor",
        ])
        assert rc == 2


class TestPruneRawBase:
    def test_prune_removes_servers_from_merged_file(self, project):
        import json as _json

        (project / ".cursor").mkdir()
        existing = {"mcpServers": {"old": {"command": "o"}, "keep": {"command": "k"}}}
        (project / ".cursor" / "mcp.json").write_text(_json.dumps(existing), encoding="utf-8")
        incoming = '{"mcpServers": {"new": {"command": "n"}, "keep": {"command": "k"}}}'
        (project / "in.json").write_text(incoming, encoding="utf-8")

        rc, out, err = run_cli([
            "--root", str(project), "--no-color",
            "mcp", "convert", "in.json", "--to", "cursor", "--prune",
        ])
        assert rc == 0
        merged = _json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
        assert set(merged["mcpServers"]) == {"keep", "new"}


class TestTomlEdgeValues:
    def test_env_values_with_special_chars_roundtrip(self):
        obj = {"mcp_servers": {"s": {"command": "c", "env": {
            "HASH": "# not comment", "QUOTE": 'say "hi"', "NEWLINE": "l1\nl2"}}}}
        text = minitoml.dumps(obj)
        back = minitoml.parse(text)
        assert back["mcp_servers"]["s"]["env"]["NEWLINE"] == "l1\nl2"
        assert back["mcp_servers"]["s"]["env"]["HASH"] == "# not comment"

    def test_server_name_with_space_roundtrip(self):
        obj = {"mcp_servers": {"my server": {"command": "c"}}}
        back = minitoml.parse(minitoml.dumps(obj))
        assert back["mcp_servers"]["my server"]["command"] == "c"
