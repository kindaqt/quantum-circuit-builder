"""Test configuration: make `import main` (and `core`, `personas`, ...) work no
matter where pytest is invoked.

The tests live in `backend/tests/` but import the backend modules as top-level
names (`import core`), so we put the `backend/` directory (this file's parent's
parent) on sys.path explicitly. That keeps the suite running the same from the repo
root (`pytest backend`), from inside `backend/`, or from `backend/tests/`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)
