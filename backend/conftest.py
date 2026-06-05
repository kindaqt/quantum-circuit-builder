"""Test configuration: make `import main` work no matter where pytest is invoked.

pytest's default ("prepend") import mode already adds this conftest's directory to
sys.path, but we insert it explicitly so the suite runs the same from the repo root
(`pytest backend`) or from inside `backend/` (`pytest`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)
