"""Pipeline: najpierw uporządkowany materiał dowodowy, dopiero potem pisanie.

1. `analyze` – silnik wyciąga wątki, cytaty i tezy; aplikacja sprawdza każdy
   cytat i odrzuca te, których nie ma w transkrypcji.
2. `write` – silnik pisze draft w trybie wybranym przez redaktora; aplikacja
   weryfikuje go i przy błędach prosi silnik o jedną poprawkę.

Model może się mylić, dlatego o tym, co jest cytatem, decyduje deterministyczna
weryfikacja, a nie deklaracja modelu.
"""

from dataclasses import dataclass

from app.engines.base import DraftRequest, Engine, MaterialInput
from app.models import ArticleDraft, EvidencePack, Quote, Transcript
from app.verification import QuoteStatus, VerificationReport, check_quote, segment_at, verify_draft


@dataclass
class DraftResult:
    draft: ArticleDraft
    report: VerificationReport
    repaired: bool


def sanitize_evidence(evidence: EvidencePack, transcript: Transcript) -> EvidencePack:
    """Zostawia tylko cytaty obecne w transkrypcji; mówcę i timecode bierze z transkrypcji."""
    quotes: list[Quote] = []
    rejected: list[str] = []
    seen: set[tuple[str, int]] = set()
    for quote in evidence.quotes:
        check = check_quote(quote, transcript)
        if check.status == QuoteStatus.NOT_FOUND:
            rejected.append(quote.text)
            continue
        # Tekst istnieje dosłownie, więc transkrypcja jest źródłem prawdy o tym, kto i kiedy to powiedział.
        segment = next(s for s in transcript.segments if s.start_seconds == check.found_at_seconds)
        fixed = quote.model_copy(update={"speaker": segment.speaker, "timecode_seconds": segment.start_seconds})
        key = (fixed.text, fixed.timecode_seconds)
        if key not in seen:
            seen.add(key)
            quotes.append(fixed)

    end = transcript.segments[-1].start_seconds
    claims = []
    for claim in evidence.claims:
        # Timecode tezy przyciągamy do początku segmentu, żeby link do wideo trafiał w wypowiedź.
        snapped = sorted(
            {
                transcript.segments[i].start_seconds
                for t in claim.evidence_timecodes
                if t <= end + 60 and (i := segment_at(transcript, t)) is not None
            }
        )
        claims.append(claim.model_copy(update={"evidence_timecodes": snapped}))

    gaps = list(evidence.gaps)
    gaps += [f"Odrzucono cytat, którego nie ma w transkrypcji: „{text}”" for text in rejected]
    return evidence.model_copy(update={"quotes": quotes, "claims": claims, "gaps": gaps})


def analyze(engine: Engine, material: MaterialInput) -> EvidencePack:
    return sanitize_evidence(engine.extract_evidence(material), material.transcript)


def write(engine: Engine, request: DraftRequest) -> DraftResult:
    transcript = request.material.transcript
    draft = engine.write_draft(request)
    report = verify_draft(draft, transcript)
    if report.ok:
        return DraftResult(draft=draft, report=report, repaired=False)

    # Jedna runda poprawki: więcej rund rzadko pomaga, a mnoży koszt i czas.
    draft = engine.repair_draft(request, draft, report.problems())
    return DraftResult(draft=draft, report=verify_draft(draft, transcript), repaired=True)


def video_link(video_url: str | None, seconds: int) -> str | None:
    """Link do momentu nagrania: YouTube (`t=`) lub fragment mediów (`#t=`)."""
    if not video_url:
        return None
    if "youtube.com" in video_url or "youtu.be" in video_url:
        separator = "&" if "?" in video_url else "?"
        return f"{video_url}{separator}t={seconds}"
    return f"{video_url.split('#')[0]}#t={seconds}"

