import json
import os
from pathlib import Path

# Import app.main tworzy aplikację domyślną; w testach bez pliku bazy i bez API.
os.environ.setdefault("EDITORIAL_DB", ":memory:")
os.environ["EDITORIAL_ENGINE"] = "heuristic"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.engines import HeuristicEngine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import ArticleDraft  # noqa: E402
from app.store import Store  # noqa: E402
from app.transcript import parse_transcript  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw_transcript() -> str:
    return (FIXTURES / "sonda_kaucja.txt").read_text(encoding="utf-8")


@pytest.fixture
def transcript(raw_transcript):
    return parse_transcript(raw_transcript)


@pytest.fixture
def draft_data() -> dict:
    return json.loads((FIXTURES / "sonda_kaucja_draft.json").read_text(encoding="utf-8"))


@pytest.fixture
def draft(draft_data) -> ArticleDraft:
    return ArticleDraft.model_validate(draft_data)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Store(":memory:"), HeuristicEngine()))
