"""Silnik Claude z atrapą klienta: sprawdzamy parametry wywołania i obsługę odpowiedzi, bez sieci."""

from types import SimpleNamespace

import pytest

from app.engines import DraftRequest, EngineError, MaterialInput
from app.engines.anthropic_engine import AnthropicEngine, DraftOutput
from app.models import ArticleMode, EvidencePack


class _FakeMessages:
    def __init__(self, output, stop_reason="end_turn"):
        self.output, self.stop_reason, self.calls = output, stop_reason, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        output = self.output.pop(0) if isinstance(self.output, list) else self.output
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=output)


def _draft_output(draft, **overrides):
    data = draft.model_dump(exclude={"mode"})
    data.update(overrides)
    return DraftOutput(**data)


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
    engine, messages = _engine(_draft_output(draft))
    request = DraftRequest(
        material=MaterialInput(title="Sonda", transcript=transcript),
        evidence=_evidence(),
        mode=ArticleMode.SHORT,
        target_words=150,
    )
    assert engine.write_draft(request).mode == ArticleMode.SHORT
    assert "około 150 słów" in messages.calls[0]["messages"][0]["content"]


def test_repair_prompt_lists_problems(transcript, draft):
    engine, messages = _engine(_draft_output(draft))
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


def _request(transcript, title="Sonda"):
    return DraftRequest(
        material=MaterialInput(title=title, transcript=transcript), evidence=_evidence(), mode=ArticleMode.FULL
    )


def test_title_count_is_normalized(transcript, draft):
    engine, _ = _engine(_draft_output(draft, title_options=["A", "B", "A", "C", "D"]))
    assert engine.write_draft(_request(transcript)).title_options == ["A", "B", "C"]
    engine, _ = _engine(_draft_output(draft, title_options=["Jedyny"]))
    assert engine.write_draft(_request(transcript)).title_options == ["Jedyny", "Sonda"]


def test_validation_error_is_retried_once(transcript):
    from pydantic import ValidationError

    try:
        EvidencePack.model_validate({})
    except ValidationError as error:
        invalid = error
    engine, messages = _engine([invalid, _evidence()])
    assert engine.extract_evidence(MaterialInput(title="S", transcript=transcript)).summary == "s"
    engine, messages = _engine([invalid, invalid])
    with pytest.raises(EngineError):
        engine.extract_evidence(MaterialInput(title="S", transcript=transcript))
    assert len(messages.calls) == 2


def test_engine_returns_markers_and_pipeline_expands_them(transcript, draft):
    from app import pipeline

    output = _draft_output(
        draft,
        lead="Rozmówcy: [[cytat:1]].",
        sections=[{"heading": None, "paragraphs": ["[[wypowiedź:2]]"]}],
        # model podaje złego mówcę – przypisanie i tak pochodzi z transkrypcji
        quotes=[draft.quotes[0].model_dump(), {**draft.quotes[1].model_dump(), "speaker": "Anna Nowak, minister"}],
    )
    engine, messages = _engine([output, output, output])
    assert engine.write_draft(_request(transcript)).lead == "Rozmówcy: [[cytat:1]]."
    result = pipeline.write(engine, _request(transcript))
    assert result.draft.lead == "Rozmówcy: „Oddaję, bo automat jest w sklepie obok domu”."
    assert result.draft.sections[0].paragraphs == ["- Za te trzydzieści groszy nie będę stać w kolejce - mówi Przechodzień 2."]
    assert not result.report.ok  # deklaracja "Anna Nowak" niezgodna z transkrypcją
    # poprawka dostała wersję ze znacznikami, nie rozwinięty tekst
    assert "[[wypowiedź:2]]" in messages.calls[2]["messages"][0]["content"]
