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
import time
from dataclasses import dataclass
from pathlib import Path

from app import pipeline
from app.engines import DraftRequest, Engine, MaterialInput
from app.models import ArticleDraft, ArticleMode
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


def _words(text: str) -> int:
    return len(normalize(text).split())


def draft_words(draft: ArticleDraft) -> int:
    return sum(_words(p) for s in draft.sections for p in s.paragraphs)


def _verified(report: VerificationReport) -> int:
    return sum(q.status == QuoteStatus.VERIFIED for q in report.quotes)


def run_case(engine: Engine, transcript_path: Path) -> CaseResult:
    meta_path, ref_path = transcript_path.with_suffix(".json"), transcript_path.with_suffix(".md")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    material = MaterialInput(
        title=meta.get("title", transcript_path.stem),
        transcript=parse_transcript(transcript_path.read_text(encoding="utf-8")),
        category=meta.get("category", ""),
        topic=meta.get("topic", ""),
        angle=meta.get("angle", ""),
    )

    started = time.perf_counter()
    evidence = pipeline.analyze(engine, material)
    mode = ArticleMode(meta["mode"]) if "mode" in meta else evidence.suggested_mode
    result = pipeline.write(engine, DraftRequest(material=material, evidence=evidence, mode=mode))
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
    )


def run_directory(engine: Engine, directory: Path) -> list[CaseResult]:
    return [run_case(engine, path) for path in sorted(directory.glob("*.txt"))]


def report_markdown(results: list[CaseResult], engine_name: str) -> str:
    lines = [
        f"# Raport ewaluacji ({engine_name})",
        "",
        "| Przypadek | Tryb | Czas [s] | Cytaty OK (1. wersja) | Cytaty w tekście błędne (1. wersja) | Poprawka | Wynik końcowy | "
        "Tezy / do weryf. / bez źródła | Słowa draft / ref. |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        ref = r.reference_words if r.reference_words is not None else "–"
        lines.append(
            f"| {r.name} | {r.mode.value} | {r.seconds} | {r.quotes_verified_first}/{r.quotes_total} | "
            f"{r.inline_failed_first} | {'tak' if r.repaired else 'nie'} | {'OK' if r.final_ok else 'BŁĘDY'} | "
            f"{r.claims_total} / {r.claims_flagged} / {r.claims_without_source} | {r.draft_words} / {ref} |"
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
