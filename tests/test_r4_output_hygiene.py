"""R4 regression tests: output hygiene on raw paths + perf smoke."""
import re
import sys

from io import StringIO

import pytest

from agentport import cli

CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def run_cli(root, argv):
    buf_out, buf_err = StringIO(), StringIO()
    saved = (sys.stdout, sys.stderr)
    code = 0
    try:
        sys.stdout, sys.stderr = buf_out, buf_err
        try:
            code = cli.main(["--root", str(root), "--no-color"]
                            + [str(a) for a in argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.stdout, sys.stderr = saved
    return code, buf_out.getvalue(), buf_err.getvalue()


def _make_skill(root, name_raw, desc, body):
    sk = root / "sk"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        "---\n"
        f"name: \"{name_raw}\"\n"
        f"description: \"{desc}\"\n"
        "---\n"
        f"\n{body}\n",
        encoding="utf-8")
    return sk


class TestOutputHygiene:
    def test_validate_fail_path_sanitizes_name(self, tmp_path):
        _make_skill(tmp_path,
                    "a\x1b]0;pwned\x07b",
                    "probe.", "Body long enough for checks.")
        code, out, err = run_cli(tmp_path, ["skills", "validate", "sk"])
        assert code == 1
        assert not CTRL_RE.search(out + err), repr((out + err)[:200])

    def test_validate_pass_path_sanitizes_description(self, tmp_path):
        _make_skill(tmp_path,
                    "ok-skill",
                    "fine\x1b[31mtint\x1b[0m rest of a long enough "
                    "description to be sensible.",
                    "Body long enough for the validation checks here.")
        code, out, err = run_cli(tmp_path, ["skills", "validate", "sk"])
        assert code == 0
        assert "PASS" in out
        assert not CTRL_RE.search(out + err), repr((out + err)[:200])

    def test_export_markdown_stdout_sanitized(self, tmp_path):
        _make_skill(tmp_path,
                    "evil-export",
                    "export hygiene probe.",
                    "\x1b]0;OWNED\x07Body with \x1b[2Jclear escape. "
                    "Also long enough body text.")
        code, out, err = run_cli(tmp_path, ["skills", "export", "sk",
                                            "--to", "markdown"])
        assert code == 0
        assert not CTRL_RE.search(out), repr(out[:200])


class TestPerfNearCaps:
    def test_sync_large_instructions_fast(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text(
            "# Big\n\n" + ("- bullet point with plenty of words here\n" * 17000),
            encoding="utf-8")
        code, out, err = run_cli(tmp_path, ["instructions", "sync", "--force"])
        assert code == 0
