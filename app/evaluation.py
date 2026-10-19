"""Ewaluacja pipeline'u na zestawie przypadków (sekcja 7 specyfikacji).

Przypadek to katalog-niezależna trójka plików o wspólnej nazwie:
- `<nazwa>.txt`  – transkrypcja (wymagana),
- `<nazwa>.md`   – opublikowany artykuł referencyjny (opcjonalny),
- `<nazwa>.json` – metadane: title, category, topic, angle, mode (opcjonalne).

Metryki automatyczne mierzą to, co da się policzyć bez człowieka: cytaty
przed i po poprawce, tezy bez źródła, tezy do weryfikacji, czas i długość.
Ocena redaktora (tytuł, lead, styl, gotowość) pozostaje do uzupełnienia
w raporcie – automatyczne metryki jej nie zastępują.
"""

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app import auto, pipeline
from app.media import Transcriber
from app.store import STYLES_DIR
from app.engines import DraftRequest, Engine, MaterialInput
from app.models import ArticleDraft, ArticleMode, EditorialStyleProfile
from app.transcript import parse_transcript
from app.verification import QuoteStatus, VerificationReport, normalize


@dataclass
class CaseResult:
    name: str
    mode: ArticleMode
    seconds: float
    quotes_total: int
    quotes_verified_first: int
    inline_failed_first: int
    repaired: bool
    final_ok: bool
    claims_total: int
    claims_flagged: int
    claims_without_source: int
    draft_words: int
    reference_words: int | None
    title_words: int
    lead_words: int
    format_warnings: int
    asr_wer: float | None = None


def _words(text: str) -> int:
    return len(normalize(text).split())


def draft_words(draft: ArticleDraft) -> int:
    """Lead + treść – w trybie SHORT cały tekst jest w leadzie."""
    return _words(draft.lead) + sum(_words(p) for s in draft.sections for p in s.paragraphs)


def _verified(report: VerificationReport) -> int:
    return sum(q.status == QuoteStatus.VERIFIED for q in report.quotes)


MEDIA_SUFFIXES = (".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".webm")


def word_error_rate(reference: str, hypothesis: str) -> float:
    """WER na słowach po normalizacji (bez wielkości liter i interpunkcji)."""
    ref, hyp = normalize(reference).split(), normalize(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_word in enumerate(hyp, start=1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref_word != hyp_word))
        previous = current
    return previous[-1] / len(ref)


def _media_for(transcript_path: Path) -> Path | None:
    return next((p for p in (transcript_path.with_suffix(s) for s in MEDIA_SUFFIXES) if p.exists()), None)


def default_style() -> EditorialStyleProfile:
    data = json.loads((STYLES_DIR / "wp_finanse.json").read_text(encoding="utf-8"))
    return EditorialStyleProfile.model_validate(data["profile"])


def run_case(
    engine: Engine,
    transcript_path: Path,
    style: EditorialStyleProfile | None = None,
    transcriber: Transcriber | None = None,
) -> CaseResult:
    """Ocena jednego przypadku; z `transcriber` i plikiem nagrania – od dźwięku (z ASR)."""
    meta_path, ref_path = transcript_path.with_suffix(".json"), transcript_path.with_suffix(".md")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    title = meta.get("title", transcript_path.stem)
    reference_raw = transcript_path.read_text(encoding="utf-8")

    started = time.perf_counter()
    wer = None
    media = _media_for(transcript_path) if transcriber else None
    if media is not None:
        with tempfile.TemporaryDirectory() as tmp:
            transcription = auto.transcribe_and_label(engine, transcriber, str(media), title, Path(tmp))
        transcript = parse_transcript(transcription.transcript_raw, with_speakers=transcription.has_speakers)
        reference = parse_transcript(reference_raw)
        wer = word_error_rate(
            " ".join(s.text for s in reference.segments), " ".join(s.text for s in transcript.segments)
        )
    else:
        transcript = parse_transcript(reference_raw)

    material = MaterialInput(
        title=title,
        transcript=transcript,
        category=meta.get("category", ""),
        topic=meta.get("topic", ""),
        angle=meta.get("angle", ""),
    )
    evidence = pipeline.analyze(engine, material)
    mode = ArticleMode(meta["mode"]) if "mode" in meta else evidence.suggested_mode
    request = DraftRequest(material=material, evidence=evidence, mode=mode, style=style or default_style())
    result = pipeline.write(engine, request)
    elapsed = time.perf_counter() - started

    first, draft = result.first_report, result.draft
    return CaseResult(
        name=transcript_path.stem,
        mode=mode,
        seconds=round(elapsed, 2),
        quotes_total=len(first.quotes),
        quotes_verified_first=_verified(first),
        inline_failed_first=sum(not q.verified for q in first.inline_quotes),
        repaired=result.repaired,
        final_ok=result.report.ok,
        claims_total=len(draft.claims),
        claims_flagged=sum(c.requires_verification for c in draft.claims),
        claims_without_source=len(result.report.claims),
        draft_words=draft_words(draft),
        reference_words=_words(ref_path.read_text(encoding="utf-8")) if ref_path.exists() else None,
        title_words=_words(draft.title_options[0]),
        lead_words=_words(draft.lead),
        format_warnings=len(result.report.format_warnings),
        asr_wer=None if wer is None else round(wer, 3),
    )


def run_directory(engine: Engine, directory: Path, transcriber: Transcriber | None = None) -> list[CaseResult]:
    return [run_case(engine, path, transcriber=transcriber) for path in sorted(directory.glob("*.txt"))]


def report_markdown(results: list[CaseResult], engine_name: str) -> str:
    lines = [
        f"# Raport ewaluacji ({engine_name})",
        "",
        "| Przypadek | Tryb | Czas [s] | Cytaty OK (1. wersja) | Cytaty w tekście błędne (1. wersja) | Poprawka | Wynik końcowy | "
        "Tezy / do weryf. / bez źródła | Słowa draft / ref. | Uwagi do formatu | WER ASR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        ref = r.reference_words if r.reference_words is not None else "–"
        lines.append(
            f"| {r.name} | {r.mode.value} | {r.seconds} | {r.quotes_verified_first}/{r.quotes_total} | "
            f"{r.inline_failed_first} | {'tak' if r.repaired else 'nie'} | {'OK' if r.final_ok else 'BŁĘDY'} | "
            f"{r.claims_total} / {r.claims_flagged} / {r.claims_without_source} | {r.draft_words} / {ref} | "
            f"{r.format_warnings} | {'–' if r.asr_wer is None else f'{r.asr_wer:.1%}'} |"
        )

    if results:
        total_quotes = sum(r.quotes_total for r in results)
        verified = sum(r.quotes_verified_first for r in results)
        rate = f"{verified / total_quotes:.0%}" if total_quotes else "–"
        lines += [
            "",
            "## Podsumowanie",
            "",
            f"- Przypadków: {len(results)}; końcowo bez błędów weryfikacji: {sum(r.final_ok for r in results)}",
            f"- Cytaty poprawne w pierwszej wersji: {verified}/{total_quotes} ({rate})",
            f"- Wymagały poprawki: {sum(r.repaired for r in results)}",
            "- Warunek pilota: zero sfabrykowanych cytatów w wersji końcowej "
            + ("— spełniony." if all(r.final_ok for r in results) else "— NIESPEŁNIONY."),
        ]

    lines += [
        "",
        "## Ocena redaktora (do uzupełnienia ręcznie)",
        "",
        "| Przypadek | Tytuł 1–5 | Lead 1–5 | Struktura 1–5 | Styl 1–5 | Gotowość do redakcji 1–5 | Czas korekty [min] | Uwagi |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {r.name} |  |  |  |  |  |  |  |" for r in results]
    return "\n".join(lines) + "\n"
