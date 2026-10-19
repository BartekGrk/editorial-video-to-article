import json
from pathlib import Path

import pytest

from app.models import ArticleDraft
from app.transcript import parse_transcript

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
