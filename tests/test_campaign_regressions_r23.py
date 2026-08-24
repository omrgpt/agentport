"""Regression tests for defects found in the 2026-08-24 adversarial campaigns.

Each test maps to a finding ID:
  F1  secret masking: PAT/bearer-style keys
  F2  junction/reparse-point traversal in skill bundles
  F3/F4 deep YAML block nesting -> clean FormatError, not RecursionError
  F5  non-object JSON/TOML config root -> clean FormatError, not AttributeError
  F6  disabled flag preserved (vscode) / loudly warned (claude family)
  F7  YAML-1.1 booleans (yes/no/on/off) parse as bools; writer quotes them
  F8  control characters in paths -> SafetyError, not OSError/exit 70
  F9  skills_import error path references the right variable name
"""
import io
import json
import os
import subprocess
import sys

import pytest

from agentport import errors
from agentport import jsonc as aj
from agentport import miniyaml
from agentport.formats import mcp as mcpf
from agentport.formats import skills as skillf
from agentport.safety import (
    SECRET_KEY_RE,
    collect_bundle_files,
    ensure_within,
    is_reparse_point,
    mask_mapping,
)
from agentport.frontmatter import split_frontmatter

IS_NT = os.name == "nt"
needs_junction = pytest.mark.skipif(
    not IS_NT, reason="NTFS junction required (Windows only)"
)


# ---------------------------------------------------------------- F1
class TestF1MaskingGap:
    @pytest.mark.parametrize("key", [
        "GITHUB_PAT", "GH_PAT", "PAT", "MY_PAT_2", "pat", "AUTH_BEARER_X",
    ])
    def test_pat_style_keys_are_secret(self, key):
        assert SECRET_KEY_RE.search(key), f"{key} should be treated as secret"

    def test_pat_value_masked_in_show(self, tmp_path):
        cfg = tmp_path / "cfg.json"
        payload = {"mcpServers": {"a": {"command": "x",
                                        "env": {"GITHUB_PAT": "leak-me-not"}}}}
        cfg.write_text(json.dumps(payload))
        warnings = []
        doc, _raw = mcpf.parse_source_text(cfg.read_text(), "cursor", warnings)
        preview = mcpf.document_to_masked_preview(doc, "cursor")
        env = preview["servers"]["a"]["env"]
        assert env["GITHUB_PAT"] == "***"

    def test_plain_words_not_overmasked(self):
        for key in ("HOME", "PATH", "USERPROFILE", "SHELL", "EDITOR"):
            assert mask_mapping({key: "/bin/x"})[key] == "/bin/x"


# ---------------------------------------------------------------- F2
@needs_junction
class TestF2JunctionTraversal:
    def _mk_junction(self, link, target):
        p = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True)
        return p.returncode == 0

    def _rm_junction(self, link):
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)

    def test_is_reparse_point_detects_junction(self, tmp_path):
        target = tmp_path / "t"
        target.mkdir()
        link = tmp_path / "l"
        if not self._mk_junction(link, target):
            pytest.skip("mklink unavailable")
        try:
            assert is_reparse_point(link)
            assert not is_reparse_point(target)
        finally:
            self._rm_junction(link)

    def test_bundle_collection_skips_junction_dir(self, tmp_path, mocker=None):
        src = tmp_path / "sk"
        (src / "real").mkdir(parents=True)
        (src / "SKILL.md").write_text("x", encoding="utf-8")
        (src / "real" / "ok.txt").write_text("ok", encoding="utf-8")
        payload = tmp_path / "payload"
        payload.mkdir()
        (payload / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
        link = src / "sub"
        if not self._mk_junction(link, payload):
            pytest.skip("mklink unavailable")
        try:
            warnings = []
            bundle = collect_bundle_files(src, warnings)
            rels = [r for r, _ in bundle]
            assert all(not r.startswith("sub/") for r in rels), rels
            assert any("junction" in w.lower() or "reparse" in w.lower()
                       for w in warnings)
        finally:
            self._rm_junction(link)


# ------------------------------------------------------- F3 / F4
def _deep_frontmatter(depth):
    lines = []
    for i in range(depth):
        lines.append("  " * i + f"k{i}:")
    lines.append("  " * depth + "leaf: 1")
    return "---\n" + "\n".join(lines) + "\n---\n\nbody\n"


class TestDeepNestingCleanErrors:
    def test_deep_block_map_raises_formaterror(self):
        text = _deep_frontmatter(450)
        with pytest.raises(errors.FormatError) as ei:
            split_frontmatter(text)
        assert isinstance(ei.value, errors.FormatError)

    def test_moderate_nesting_still_parses(self):
        meta, body = split_frontmatter(_deep_frontmatter(20))
        assert isinstance(meta, dict) and "body" in body

    def test_deep_list_nesting_raises_formaterror(self):
        text = "---\nk: " + "[\n" * 300 + "]" * 300 + "\n---\nbody\n"
        with pytest.raises((errors.FormatError,)):
            split_frontmatter(text)


# ---------------------------------------------------------------- F5
class TestNonObjectRootRefusedCleanly:
    def test_top_level_array_refused(self):
        with pytest.raises(errors.FormatError):
            mcpf.parse_family_json([{"mcpServers": {}}], "claude", [])

    def test_all_families_guard_non_dict_root(self):
        for fam in ("claude", "cursor", "windsurf", "gemini"):
            with pytest.raises(errors.FormatError):
                mcpf.parse_family_json([1, 2], fam, [])
        for fn in (mcpf.parse_vscode, mcpf.parse_opencode, mcpf.parse_codex):
            with pytest.raises(errors.FormatError):
                fn([1, 2], [])

    def test_scalar_root_refused(self):
        with pytest.raises(errors.FormatError):
            mcpf.parse_codex("scalar", [])


# ---------------------------------------------------------------- F6
class TestDisabledFlagSemantics:
    def _doc_with_disabled(self):
        warnings = []
        doc, raw = mcpf.parse_source_text(
            '{"mcpServers": {"d": {"command": "old", "disabled": true}}}',
            "claude", warnings)
        return doc, raw

    def test_vscode_render_preserves_disabled_as_extra(self):
        doc, _ = self._doc_with_disabled()
        text, obj = mcpf.render_vscode(doc, None, False, "keep", [])
        entry = obj["servers"]["d"]
        assert entry.get("disabled") is True

    def test_claude_render_warns_when_disabled_dropped(self):
        doc, _ = self._doc_with_disabled()
        warns = []
        text, obj = mcpf.render_family_json(doc, None, "claude", False,
                                            "keep", warns)
        assert any("disabled" in w for w in warns)
        # cursor keeps native flag
        cur_warns = []
        ctext, cobj = mcpf.render_family_json(doc, None, "cursor", False,
                                              "keep", cur_warns)
        assert cobj["mcpServers"]["d"]["disabled"] is True

    def test_roundtrip_claude_to_vscode_keeps_state(self):
        doc, raw = self._doc_with_disabled()
        vtext, vobj = mcpf.render_vscode(doc, None, False, "keep", [])
        doc2, _ = mcpf.parse_source_text(vtext, "vscode", [])
        srv = [s for s in doc2.servers if s.name == "d"][0]
        assert srv.disabled is True


# ---------------------------------------------------------------- F7
class TestYaml11Booleans:
    @pytest.mark.parametrize("raw,expected", [
        ("yes", True), ("no", False), ("on", True), ("off", False),
        ("Yes", True), ("NO", False), ("On", True), ("OFF", False),
        ("y", True), ("n", False),
        ("true", True), ("false", False), ("null", None), ("~", None),
    ])
    def test_parse_bools(self, raw, expected):
        value = miniyaml.parse(f"k: {raw}\n")["k"]
        assert value is expected, f"{raw!r} -> {value!r}"

    def test_writer_quotes_reserved_words(self):
        text = miniyaml.dump({"mode": "on", "answer": "no", "keep": "yes"})
        parsed = miniyaml.parse(text)
        assert parsed["mode"] == "on"
        assert parsed["answer"] == "no"
        assert parsed["keep"] == "yes"
        # they must be quoted so they stay strings
        assert "'on'" in text or '"on"' in text

    def test_always_apply_no_stays_false_through_cursor_semantics(self):
        """The security-relevant case from the campaign."""
        meta, _body = split_frontmatter(
            '---\ndescription: d\nalwaysApply: no\n---\n\nBody.\n')
        assert meta["alwaysApply"] is False
        # render via instructions renderer: must NOT flip to true
        from agentport.formats.instructions import render_cursor_rule
        out = render_cursor_rule("Body.", source_meta=meta)
        fm = out.split("---")[1]
        assert "alwaysApply: false" in fm


# ---------------------------------------------------------------- F8
class TestControlCharsInPaths:
    def test_newline_in_path_refused_safely(self, tmp_path):
        with pytest.raises(errors.SafetyError):
            ensure_within(tmp_path, "weird\nname.md")

    def test_tab_and_esc_refused(self, tmp_path):
        with pytest.raises(errors.SafetyError):
            ensure_within(tmp_path, "a\tb.md")
        with pytest.raises(errors.SafetyError):
            ensure_within(tmp_path, "a\x1b[31mb.md")


# ---------------------------------------------------------------- F9
class TestImportErrorMessagePath:
    def test_invalid_skill_name_gives_format_error_not_crash(self, tmp_path):
        sk = tmp_path / "sk"
        sk.mkdir()
        (sk / "SKILL.md").write_text(
            "---\nname: Bad Name!\ndescription: probe.\n---\n\n"
            "Body long enough.\n", encoding="utf-8")
        warnings = []
        doc = skillf.load_skill_dir(sk, warnings)
        fatal = skillf.validate_skill(doc, warnings=warnings)
        assert fatal, "expected validation failure for bad name"
        # The old code crashed building this exact hint string.
        from agentport.ops import skills_import
        with pytest.raises(errors.FormatError) as ei:
            skills_import(str(tmp_path), sk, "claude")
        assert "skills normalize" in ei.value.hint


# ---------------------------------------------------------------- integration
class TestCampaignIntegration:
    def test_full_cli_flow_after_fixes(self, tmp_path):
        """End-to-end smoke: sync + mcp convert + skills import still work."""
        (tmp_path / "AGENTS.md").write_text("# G\n\nrule one\n", encoding="utf-8")
        (tmp_path / "mcp.json").write_text(json.dumps({
            "mcpServers": {"fs": {"command": "npx", "args": ["-y", "p"],
                                  "env": {"GITHUB_PAT": "v"}},
                           "off": {"command": "x", "disabled": True}}}),
            encoding="utf-8")
        sk = tmp_path / "demo-skill"
        sk.mkdir()
        (sk / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: smoke probe skill.\n"
            "---\n\nBody long enough for validation checks.\n", encoding="utf-8")

        from agentport.ops import (convert_instructions, mcp_convert,
                                   skills_import)

        warnings = []
        r1 = convert_instructions(tmp_path, tmp_path / "AGENTS.md", "claude",
                                  force=True, warnings=warnings)
        assert (tmp_path / "CLAUDE.md").exists()

        r2 = mcp_convert(tmp_path, tmp_path / "mcp.json", "vscode",
                         out_path=tmp_path / "out.json", warnings=warnings)
        obj = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        assert obj["servers"]["off"]["disabled"] is True

        r3 = skills_import(tmp_path, sk, "claude", warnings=warnings)
        assert (tmp_path / ".claude/skills/demo-skill/SKILL.md").exists()
