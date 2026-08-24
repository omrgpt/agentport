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
