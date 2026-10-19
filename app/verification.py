"""Weryfikacja draftu względem transkrypcji.

Cel pilota to zerowa tolerancja na sfabrykowane cytaty, dlatego sprawdzenie
jest deterministyczne (bez LLM): cytat musi występować w transkrypcji
dosłownie, z dokładnością do wielkości liter, interpunkcji i białych znaków.
Skrót oznaczony "..." / "…" / "[...]" jest dozwolony, o ile fragmenty
występują w tej samej kolejności.
"""

import re
from enum import Enum

from pydantic import BaseModel, computed_field

from app.models import ArticleDraft, Quote, Transcript, TranscriptSegment
from app.transcript import format_timecode

_ELLIPSIS = re.compile(r"\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)

# Cytat może przechodzić na kolejną linię tej samej wypowiedzi;
# szersze okno zwiększałoby ryzyko "sklejenia" cytatu z odległych zdań.
_MAX_FOLLOWING_SEGMENTS = 2


class QuoteStatus(str, Enum):
    VERIFIED = "VERIFIED"
    SPEAKER_MISMATCH = "SPEAKER_MISMATCH"
    TIMECODE_MISMATCH = "TIMECODE_MISMATCH"
    NOT_FOUND = "NOT_FOUND"


class QuoteCheck(BaseModel):
    quote: Quote
    status: QuoteStatus
    found_at_seconds: int | None = None
    message: str


class ClaimCheck(BaseModel):
    claim_index: int
    statement: str
    problems: list[str]


class VerificationReport(BaseModel):
    quotes: list[QuoteCheck]
    claims: list[ClaimCheck]

    @computed_field
    @property
    def ok(self) -> bool:
        return all(q.status == QuoteStatus.VERIFIED for q in self.quotes) and not self.claims


def normalize(text: str) -> str:
    return " ".join(_NON_WORD.sub(" ", text.casefold()).split())


def _fragments(quote_text: str) -> list[str]:
    return [f for f in (normalize(p) for p in _ELLIPSIS.split(quote_text)) if f]


def _contains_in_order(haystack: str, fragments: list[str]) -> bool:
    # Dopasowanie na granicach słów: "dom" nie może pasować do "domu".
    padded = f" {haystack} "
    position = 0
    for fragment in fragments:
        found = padded.find(f" {fragment} ", position)
        if found < 0:
            return False
        position = found + len(fragment) + 1
    return True


def _segment_at(transcript: Transcript, seconds: int) -> int | None:
    """Indeks segmentu obejmującego dany moment (ostatni z początkiem <= seconds)."""
    index = None
    for i, segment in enumerate(transcript.segments):
        if segment.start_seconds <= seconds:
            index = i
        else:
            break
    return index


def _window_text(segments: list[TranscriptSegment], start: int) -> str:
    speaker = segments[start].speaker
    parts = [segments[start].text]
    for segment in segments[start + 1 : start + 1 + _MAX_FOLLOWING_SEGMENTS]:
        if segment.speaker != speaker:
            break
        parts.append(segment.text)
    return normalize(" ".join(parts))


def _same_speaker(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and normalize(a) == normalize(b)


def check_quote(quote: Quote, transcript: Transcript) -> QuoteCheck:
    fragments = _fragments(quote.text)
    if not fragments:
        return QuoteCheck(quote=quote, status=QuoteStatus.NOT_FOUND, message="Pusty cytat")

    segments = transcript.segments
    cited = _segment_at(transcript, quote.timecode_seconds)
    if cited is not None and _contains_in_order(_window_text(segments, cited), fragments):
        segment = segments[cited]
        if segment.speaker is None or _same_speaker(quote.speaker, segment.speaker):
            return QuoteCheck(
                quote=quote,
                status=QuoteStatus.VERIFIED,
                found_at_seconds=segment.start_seconds,
                message="Cytat zgodny z transkrypcją",
            )
        return QuoteCheck(
            quote=quote,
            status=QuoteStatus.SPEAKER_MISMATCH,
            found_at_seconds=segment.start_seconds,
            message=f"W transkrypcji mówi: {segment.speaker}, w drafcie: {quote.speaker or 'brak'}",
        )

    for i, segment in enumerate(segments):
        if i != cited and _contains_in_order(_window_text(segments, i), fragments):
            return QuoteCheck(
                quote=quote,
                status=QuoteStatus.TIMECODE_MISMATCH,
                found_at_seconds=segment.start_seconds,
                message=(
                    f"Cytat występuje w {format_timecode(segment.start_seconds)}, "
                    f"a nie w {format_timecode(quote.timecode_seconds)}"
                ),
            )

    return QuoteCheck(
        quote=quote,
        status=QuoteStatus.NOT_FOUND,
        message="Brak takiej wypowiedzi w transkrypcji: cytat nie może zostać opublikowany",
    )


def verify_draft(draft: ArticleDraft, transcript: Transcript) -> VerificationReport:
    end = transcript.segments[-1].start_seconds
    claim_checks: list[ClaimCheck] = []
    for index, claim in enumerate(draft.claims):
        problems = []
        if not claim.evidence_timecodes:
            problems.append("Brak wskazania fragmentu transkrypcji")
        # Transkrypcja nie podaje końca ostatniej wypowiedzi, więc dopuszczamy
        # 60 s zapasu; dalszy timecode niczego nie wskazuje.
        problems += [
            f"Timecode {format_timecode(t)} poza zakresem transkrypcji"
            for t in claim.evidence_timecodes
            if _segment_at(transcript, t) is None or t > end + 60
        ]
        if problems:
            claim_checks.append(
                ClaimCheck(claim_index=index, statement=claim.statement, problems=problems)
            )

    return VerificationReport(
        quotes=[check_quote(q, transcript) for q in draft.quotes],
        claims=claim_checks,
    )
