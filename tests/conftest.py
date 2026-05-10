from pathlib import Path

import pytest


@pytest.fixture
def sample_claude_root() -> Path:
    return Path(__file__).parent / "fixtures" / ".claude"
