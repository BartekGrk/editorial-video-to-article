"""Automat od nagrania do draftu: plik lub link → transkrypcja → draft → weryfikacja → eksport.

Przykłady:
    python scripts/auto.py nagranie.mp4 --out wyniki/
    python scripts/auto.py "https://www.youtube.com/watch?v=..." --mode FULL --out wyniki/

Wyniki w katalogu --out: transcript.txt, evidence.json, draft.json, report.json,
article.md (z materiałem kontrolnym) i article.html (do wklejenia w CMS).
Kod wyjścia 0, gdy draft przeszedł weryfikację; 2, gdy wymaga poprawek.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auto  # noqa: E402
from app.engines import create_engine  # noqa: E402
from app.media import WhisperTranscriber  # noqa: E402
from app.models import ArticleMode  # noqa: E402
from app.render import render_html, render_markdown  # noqa: E402
from app.store import STYLES_DIR  # noqa: E402
from app.models import EditorialStyleProfile  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="ścieżka do pliku wideo/audio albo link")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--angle", default="")
    parser.add_argument("--mode", choices=["SHORT", "FULL"], help="domyślnie tryb sugerowany przez analizę")
    parser.add_argument("--style", type=Path, default=STYLES_DIR / "wp_finanse.json", help="plik profilu stylu (JSON)")
    args = parser.parse_args()

    style_data = json.loads(args.style.read_text(encoding="utf-8"))
    options = auto.AutoOptions(
        title=args.title,
        category=args.category,
        topic=args.topic,
        angle=args.angle,
        mode=ArticleMode(args.mode) if args.mode else None,
        style=EditorialStyleProfile.model_validate(style_data.get("profile", style_data)),
    )
    engine = create_engine()
    print(f"Silnik: {engine.name}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        result = auto.run(
            engine,
            WhisperTranscriber(),
            args.source,
            options,
            Path(tmp),
            on_stage=lambda stage: print(f"→ {stage.value}", file=sys.stderr),
        )

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    draft, report = result.draft_result.draft, result.draft_result.report
    (out / "transcript.txt").write_text(result.transcription.transcript_raw + "\n", encoding="utf-8")
    (out / "evidence.json").write_text(result.evidence.model_dump_json(indent=2), encoding="utf-8")
    (out / "draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    (out / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out / "article.md").write_text(render_markdown(draft), encoding="utf-8")
    (out / "article.html").write_text(render_html(draft), encoding="utf-8")

    print(f"Tytuł: {draft.title_options[0]} | tryb: {result.mode.value} | weryfikacja: {'OK' if report.ok else 'BŁĘDY'}")
    for problem in report.problems():
        print(f"  ! {problem}")
    print(f"Wyniki: {out}")
    return 0 if report.ok else 2


if __name__ == "__main__":
    sys.exit(main())
