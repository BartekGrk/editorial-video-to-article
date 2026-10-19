"""Uruchamia ewaluację na katalogu przypadków i zapisuje raport Markdown.

Przykład (tryb demonstracyjny, bez API):
    EDITORIAL_ENGINE=heuristic python scripts/evaluate.py examples/eval
Z modelem Claude (wymaga ANTHROPIC_API_KEY; dane tylko publiczne/syntetyczne):
    python scripts/evaluate.py examples/eval --out reports/eval.md
Od nagrania (ASR + cały automat, z miarą WER transkrypcji):
    python scripts/evaluate.py examples/eval --from-audio
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines import create_engine  # noqa: E402
from app.evaluation import report_markdown, run_directory  # noqa: E402
from app.media import WhisperTranscriber  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path, help="katalog z plikami <nazwa>.txt [.md, .json]")
    parser.add_argument("--out", type=Path, help="plik raportu (domyślnie wypisz na ekran)")
    parser.add_argument(
        "--from-audio",
        action="store_true",
        help="dla przypadków z plikiem nagrania (<nazwa>.wav/.mp3/.m4a/.mp4) zacznij od transkrypcji ASR i policz WER",
    )
    args = parser.parse_args()

    engine = create_engine()
    transcriber = WhisperTranscriber() if args.from_audio else None
    report = report_markdown(run_directory(engine, args.directory, transcriber), engine.name)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"Zapisano raport: {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
