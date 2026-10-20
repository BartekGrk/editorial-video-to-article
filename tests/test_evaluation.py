from pathlib import Path

from app.engines import HeuristicEngine
from app.evaluation import report_markdown, run_directory

EXAMPLES = Path(__file__).parent.parent / "examples" / "eval"


def test_evaluation_runs_on_examples():
    results = {r.name: r for r in run_directory(HeuristicEngine(), EXAMPLES)}
    assert set(results) == {"sonda-kaucja", "wywiad-budzet-domowy"}
    assert results["sonda-kaucja"].mode.value == "SHORT"
    assert results["wywiad-budzet-domowy"].mode.value == "FULL"
    assert results["wywiad-budzet-domowy"].reference_words > 100
    assert all(r.final_ok for r in results.values())

    report = report_markdown(list(results.values()), "heuristic")
    assert "Warunek pilota: zero sfabrykowanych lub źle przypisanych cytatów w wersji końcowej — spełniony." in report
    assert "## Ocena redaktora" in report


def test_draft_words_include_lead(draft):
    from app.evaluation import draft_words

    draft.sections = []
    assert draft_words(draft) > 0


def test_word_error_rate():
    from app.evaluation import word_error_rate

    assert word_error_rate("Ala ma kota.", "ala ma kota") == 0
    assert word_error_rate("ala ma kota", "ala ma psa") == 1 / 3
    assert word_error_rate("ala ma kota", "ala kota") == 1 / 3


def test_demo_drafts_fit_wp_title_format_and_headings():
    """Tryb demonstracyjny nie może łamać własnych reguł formatu ani dawać śródtytułu spoza sekcji."""
    import json

    from app import pipeline
    from app.engines import DraftRequest, MaterialInput
    from app.evaluation import default_style
    from app.media import title_from_filename
    from app.transcript import parse_transcript
    from app.verification import normalize

    engine = HeuristicEngine()
    for path in sorted(EXAMPLES.glob("*.txt")):
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        material = MaterialInput(
            title=title_from_filename(path.name),
            transcript=parse_transcript(path.read_text(encoding="utf-8")),
            topic=meta.get("topic", ""),
        )
        evidence = pipeline.analyze(engine, material)
        request = DraftRequest(material=material, evidence=evidence, mode=evidence.suggested_mode, style=default_style())
        result = pipeline.write(engine, request)
        draft = result.draft
        assert result.report.strict_ok
        assert not [w for w in result.report.format_warnings if w.startswith("Tytuł")], result.report.format_warnings
        assert all(t[:1].isupper() for t in draft.title_options)
        for section in draft.sections:
            if section.heading and section.heading.endswith("…"):
                body = normalize(" ".join(section.paragraphs))
                assert normalize(section.heading.rstrip("…")) in body, section.heading
