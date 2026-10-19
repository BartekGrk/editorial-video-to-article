"""Silnik Claude z atrapą klienta: sprawdzamy parametry wywołania i obsługę odpowiedzi, bez sieci."""

from types import SimpleNamespace

import pytest

from app.engines import DraftRequest, EngineError, MaterialInput
from app.engines.anthropic_engine import AnthropicEngine
from app.models import ArticleMode, EvidencePack


class _FakeMessages:
    def __init__(self, output, stop_reason="end_turn"):
        self.output, self.stop_reason, self.calls = output, stop_reason, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.output)


def _engine(output, stop_reason="end_turn"):
    messages = _FakeMessages(output, stop_reason)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return AnthropicEngine(client=client, model="claude-opus-5-5"), messages


def _evidence():
    return EvidencePack(summary="s", threads=[], quotes=[], claims=[], suggested_mode="SHORT", mode_rationale="r")


def test_extract_sends_transcript_with_seconds_and_schema(transcript):
    engine, messages = _engine(_evidence())
    result = engine.extract_evidence(MaterialInput(title="Sonda", transcript=transcript, angle="koszty"))
    assert result.summary == "s"
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is EvidencePack
    assert call["thinking"] == {"type": "adaptive"}
    assert call["fallbacks"] == "default"
    prompt = call["messages"][0]["content"]
    assert "[00:00:12 t=12] Przechodzień 1: Oddaję" in prompt
    assert "Kąt redakcyjny wskazany przez redaktora: koszty" in prompt


def test_draft_keeps_editor_mode(transcript, draft):
    engine, messages = _engine(draft.model_copy(update={"mode": ArticleMode.FULL}))
    request = DraftRequest(
        material=MaterialInput(title="Sonda", transcript=transcript),
        evidence=_evidence(),
        mode=ArticleMode.SHORT,
        target_words=150,
    )
    assert engine.write_draft(request).mode == ArticleMode.SHORT
    assert "około 150 słów" in messages.calls[0]["messages"][0]["content"]


def test_repair_prompt_lists_problems(transcript, draft):
    engine, messages = _engine(draft)
    request = DraftRequest(material=MaterialInput(title="S", transcript=transcript), evidence=_evidence(), mode=ArticleMode.SHORT)
    engine.repair_draft(request, draft, ["Cytat „x” (NOT_FOUND)"])
    assert "- Cytat „x” (NOT_FOUND)" in messages.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_response_raises_engine_error(transcript, stop_reason):
    engine, _ = _engine(_evidence(), stop_reason=stop_reason)
    with pytest.raises(EngineError):
        engine.extract_evidence(MaterialInput(title="S", transcript=transcript))


def test_missing_parsed_output_raises(transcript):
    engine, _ = _engine(None)
    with pytest.raises(EngineError):
        engine.extract_evidence(MaterialInput(title="S", transcript=transcript))
