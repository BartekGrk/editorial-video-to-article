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
