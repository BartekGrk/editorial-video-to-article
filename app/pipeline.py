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
    # Raport pierwszej wersji (przed poprawką) – potrzebny do ewaluacji jakości modelu.
    first_report: VerificationReport | None = None


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
        segment = transcript.segments[check.segment_index]
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


def fix_quote_timecodes(draft: ArticleDraft, transcript: Transcript) -> ArticleDraft:
    """Poprawia timecode cytatów, których tekst występuje dosłownie gdzie indziej.

    To bezpieczna, mechaniczna korekta (tekst i mówca się zgadzają). Błędny mówca
    wymaga poprawy treści ("mówi X"), więc ten przypadek zostaje do poprawki przez model.
    """
    quotes = []
    for quote in draft.quotes:
        check = check_quote(quote, transcript)
        if check.status == QuoteStatus.TIMECODE_MISMATCH:
            segment = transcript.segments[check.segment_index]
            if segment.speaker is None or quote.speaker == segment.speaker:
                quote = quote.model_copy(update={"timecode_seconds": segment.start_seconds})
        quotes.append(quote)
    return draft.model_copy(update={"quotes": quotes})


def analyze(engine: Engine, material: MaterialInput) -> EvidencePack:
    return sanitize_evidence(engine.extract_evidence(material), material.transcript)


def write(engine: Engine, request: DraftRequest) -> DraftResult:
    transcript = request.material.transcript
    draft = fix_quote_timecodes(engine.write_draft(request), transcript)
    report = verify_draft(draft, transcript)
    # Model językowy poprawia też wyraźne odstępstwa od formatu; regułowy silnik
    # nie umie przeredagować tekstu, więc dla niego poprawka dotyczy tylko błędów.
    problems = report.problems() + report.attribution_warnings + (report.format_warnings if engine.external else [])
    if not problems:
        return DraftResult(draft=draft, report=report, repaired=False, first_report=report)

    # Jedna runda poprawki: więcej rund rzadko pomaga, a mnoży koszt i czas.
    repaired = fix_quote_timecodes(engine.repair_draft(request, draft, problems), transcript)
    repaired_report = verify_draft(repaired, transcript)
    # Poprawka samego formatu nie może zepsuć poprawnych cytatów – wtedy zostaje pierwsza wersja.
    if (report.ok and not repaired_report.ok) or (report.strict_ok and not repaired_report.strict_ok):
        return DraftResult(draft=draft, report=report, repaired=False, first_report=report)
    return DraftResult(draft=repaired, report=repaired_report, repaired=True, first_report=report)


def video_link(video_url: str | None, seconds: int) -> str | None:
    """Link do momentu nagrania: YouTube (`t=`) lub fragment mediów (`#t=`)."""
    if not video_url:
        return None
    if "youtube.com" in video_url or "youtu.be" in video_url:
        separator = "&" if "?" in video_url else "?"
        return f"{video_url}{separator}t={seconds}"
    return f"{video_url.split('#')[0]}#t={seconds}"

