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

# Cudzysłowy w tekście: polskie „…”, angielskie “…” i proste "…".
_INLINE_QUOTE = re.compile(r"„([^”\"]+)[”\"]|“([^”]+)”|\"([^\"]+)\"")
# Krótkie frazy w cudzysłowie to zwykle nazwy własne (programy, ustawy),
# a nie cytaty; sprawdzamy tylko wypowiedzi od 4 słów.
_MIN_INLINE_QUOTE_WORDS = 4
# Konwencja WP Finanse: wypowiedź jako akapit z myślnikiem,
# np. "- Treść wypowiedzi - mówi Jan Kowalski, rolnik."
_DASH_PARAGRAPH = re.compile(r"^\s*[-–—]\s+")
_DASH_SPLIT = re.compile(r"\s+[-–—]\s+")

# Cytat może przechodzić na kolejne segmenty tej samej wypowiedzi (ASR tnie
# mowę na odcinki po kilka sekund); szersze okno zwiększałoby ryzyko "sklejenia" cytatu z odległych zdań.
_MAX_FOLLOWING_SEGMENTS = 3


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


class InlineQuoteCheck(BaseModel):
    """Cytat w cudzysłowie wpleciony w tytuł, lead lub akapit."""

    location: str
    text: str
    found_at_seconds: int | None = None
    verified: bool


class VerificationReport(BaseModel):
    quotes: list[QuoteCheck]
    claims: list[ClaimCheck]
    inline_quotes: list[InlineQuoteCheck] = []

    @computed_field
    @property
    def ok(self) -> bool:
        return (
            all(q.status == QuoteStatus.VERIFIED for q in self.quotes)
            and not self.claims
            and all(q.verified for q in self.inline_quotes)
        )

    def problems(self) -> list[str]:
        """Czytelna lista błędów, np. do poprawki draftu przez model."""
        lines = [
            f"Cytat „{q.quote.text}” ({q.status.value}): {q.message}"
            for q in self.quotes
            if q.status != QuoteStatus.VERIFIED
        ]
        lines += [f"Teza „{c.statement}”: {'; '.join(c.problems)}" for c in self.claims]
        lines += [
            f"Cytat w tekście ({q.location}) „{q.text}” nie występuje w transkrypcji"
            for q in self.inline_quotes
            if not q.verified
        ]
        return lines


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


def segment_at(transcript: Transcript, seconds: int) -> int | None:
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
    cited = segment_at(transcript, quote.timecode_seconds)
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


def find_quote(text: str, transcript: Transcript) -> int | None:
    """Indeks pierwszego segmentu, od którego zaczyna się cytat (bez względu na mówcę)."""
    fragments = _fragments(text)
    if not fragments:
        return None
    for i in range(len(transcript.segments)):
        if _contains_in_order(_window_text(transcript.segments, i), fragments):
            return i
    return None


def dash_quotes(paragraph: str) -> list[str]:
    """Wypowiedzi z akapitu dialogowego "- cytat - mówi X. - Dalszy cytat."

    Pierwszy fragment po myślniku to cytat. Kolejne fragmenty zaczynające się
    małą literą to przypisanie ("mówi", "dodaje") i narracja; fragment od
    wielkiej litery po zakończonym zdaniu to następna wypowiedź.
    """
    if not _DASH_PARAGRAPH.match(paragraph):
        return []
    parts = _DASH_SPLIT.split(_DASH_PARAGRAPH.sub("", paragraph, count=1))
    quotes = [parts[0]]
    for previous, part in zip(parts, parts[1:]):
        if part[:1].isupper() and previous.rstrip().endswith((".", "!", "?", "…")):
            quotes.append(part)
    return [q.strip() for q in quotes if q.strip()]


def extract_inline_quotes(draft: ArticleDraft) -> list[tuple[str, str]]:
    """Pary (miejsce, cytat) dla wypowiedzi w cudzysłowie lub po myślniku w tekście draftu."""
    places = [(f"tytuł {i}", t) for i, t in enumerate(draft.title_options, start=1)]
    places.append(("lead", draft.lead))
    for s_no, section in enumerate(draft.sections, start=1):
        places += [(f"sekcja {s_no}, akapit {p_no}", p) for p_no, p in enumerate(section.paragraphs, start=1)]
    found = []
    for location, text in places:
        for match in _INLINE_QUOTE.finditer(text):
            quoted = next(g for g in match.groups() if g is not None).strip()
            if len(normalize(quoted).split()) >= _MIN_INLINE_QUOTE_WORDS:
                found.append((location, quoted))
        for quoted in dash_quotes(text):
            # Cytat z myślnikiem może zawierać wtrącenia w cudzysłowie – sprawdzamy całość.
            if len(normalize(quoted).split()) >= _MIN_INLINE_QUOTE_WORDS:
                found.append((location, quoted))
    return found


def check_inline_quotes(draft: ArticleDraft, transcript: Transcript) -> list[InlineQuoteCheck]:
    checks = []
    for location, text in extract_inline_quotes(draft):
        index = find_quote(text, transcript)
        checks.append(
            InlineQuoteCheck(
                location=location,
                text=text,
                verified=index is not None,
                found_at_seconds=None if index is None else transcript.segments[index].start_seconds,
            )
        )
    return checks


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
            if segment_at(transcript, t) is None or t > end + 60
        ]
        if problems:
            claim_checks.append(
                ClaimCheck(claim_index=index, statement=claim.statement, problems=problems)
            )

    return VerificationReport(
        quotes=[check_quote(q, transcript) for q in draft.quotes],
        claims=claim_checks,
        inline_quotes=check_inline_quotes(draft, transcript),
    )
