from app import pipeline
from app.engines import DraftRequest, HeuristicEngine, MaterialInput
from app.models import ArticleMode, Claim, EvidencePack, Quote


def _material(transcript):
    return MaterialInput(title="Sonda", transcript=transcript)


def _evidence(**overrides):
    data = dict(
        summary="s", threads=[], quotes=[], claims=[], suggested_mode=ArticleMode.SHORT, mode_rationale="r"
    )
    data.update(overrides)
    return EvidencePack(**data)


def test_sanitize_drops_fabricated_and_fixes_attribution(transcript):
    evidence = _evidence(
        quotes=[
            Quote(text="Kaucja to porażka", speaker="Przechodzień 2", timecode_seconds=31),
            Quote(text="Ja nie oddaję", speaker="Przechodzień 1", timecode_seconds=5),
        ]
    )
    clean = pipeline.sanitize_evidence(evidence, transcript)
    assert clean.quotes == [Quote(text="Ja nie oddaję", speaker="Przechodzień 2", timecode_seconds=31)]
    assert any("Kaucja to porażka" in gap for gap in clean.gaps)


def test_sanitize_snaps_claim_timecodes(transcript):
    evidence = _evidence(claims=[Claim(statement="x", evidence_timecodes=[47, 9999])])
    assert pipeline.sanitize_evidence(evidence, transcript).claims[0].evidence_timecodes == [44]


class _FabricatingEngine(HeuristicEngine):
    """Najpierw zmyśla cytat, po poprawce go usuwa – jak model, który się myli."""

    def __init__(self):
        self.repair_calls = 0

    def write_draft(self, request):
        draft = super().write_draft(request)
        draft.lead += " „To jest wymyślona wypowiedź rozmówcy”."
        return draft

    def repair_draft(self, request, draft, problems):
        self.repair_calls += 1
        assert any("wymyślona" in p for p in problems)
        draft.lead = draft.lead.replace(" „To jest wymyślona wypowiedź rozmówcy”.", "")
        return draft


def test_write_repairs_once_when_verification_fails(transcript):
    engine = _FabricatingEngine()
    material = _material(transcript)
    request = DraftRequest(material=material, evidence=pipeline.analyze(engine, material), mode=ArticleMode.SHORT)
    result = pipeline.write(engine, request)
    assert result.repaired and result.report.ok and engine.repair_calls == 1


def test_fix_quote_timecodes_moves_exact_quote(transcript, draft):
    draft.quotes[1] = draft.quotes[1].model_copy(update={"timecode_seconds": 5})
    fixed = pipeline.fix_quote_timecodes(draft, transcript)
    assert fixed.quotes[1].timecode_seconds == 31
