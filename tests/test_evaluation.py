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
    assert "Warunek pilota: zero sfabrykowanych cytatów w wersji końcowej — spełniony." in report
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
