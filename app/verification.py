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

# Cudzysłowy: „…” i „…“ (polskie), “…” (angielskie), "…" (proste), »…« i «…».
# Prosty " zamyka „ tylko, gdy w tekście nie ma ” ani “ – inaczej wtrącenie w "…" ucinałoby cytat.
_INLINE_QUOTE = re.compile(
    r"„([^”“]+)[”“]|„([^”“\"]+)\"|“([^”]+)”|\"([^\"]+)\"|»([^«]+)«|«([^»]+)»"
)
# Krótkie frazy w cudzysłowie to zwykle nazwy własne (programy, ustawy),
# a nie cytaty; sprawdzamy tylko wypowiedzi od 4 słów.
_MIN_INLINE_QUOTE_WORDS = 4
# Konwencja WP Finanse: wypowiedź jako akapit z myślnikiem,
# np. "- Treść wypowiedzi - mówi Jan Kowalski, rolnik."
_DASH_LINE = re.compile(r"^\s*[-–—]\s*(?=\w|„|\")")
_DASH_SPLIT = re.compile(r"\s+[-–—]\s+")
# Po myślniku przypisanie zaczyna się czasownikiem mówienia ("mówi", "dodaje"…);
# każdy inny fragment po myślniku traktujemy jako dalszy ciąg wypowiedzi i sprawdzamy.
_ATTRIBUTION_VERBS = """
mówi mówią mówił mówiła mówili mówiły powiedział powiedziała powiedzieli
dodaje dodają dodał dodała dodali podkreśla podkreślają podkreślił podkreśliła
ocenia oceniają ocenił oceniła wylicza wyliczał wyliczała podsumowuje podsumował podsumowała
przyznaje przyznają przyznał przyznała tłumaczy tłumaczą tłumaczył tłumaczyła
zaznacza zaznaczył zaznaczyła wyjaśnia wyjaśnił wyjaśniła przekonuje przekonują przekonywał przekonywała
twierdzi twierdzą twierdził twierdziła stwierdza stwierdził stwierdziła zauważa zauważył zauważyła
odpowiada odpowiadają odpowiedział odpowiedziała odparł odparła komentuje komentował komentowała
opowiada opowiadał opowiadała wspomina wspominał wspominała zapewnia zapewnił zapewniła
apeluje apelował apelowała pyta pytają pytał pytała ostrzega ostrzegł ostrzegła
przypomina przypomniał przypomniała skandują skandowali skandowały krzyczy krzyczą krzyczał krzyczała
kwituje skwitował skwitowała argumentuje argumentował argumentowała uważa uważają uważał uważała
relacjonuje relacjonował relacjonowała słyszymy usłyszeliśmy słyszę wtrąca wtrącił wtrąciła
zastrzega zastrzegł zastrzegła żartuje żartował żartowała deklaruje deklarował deklarowała
zapowiada zapowiedział zapowiedziała wskazuje wskazywał wskazywała narzeka narzekał narzekała
skarży skarżył skarżyła alarmuje alarmował alarmowała dopowiada dopowiedział dopowiedziała
""".split()
# Po myślniku przypisanie zaczyna się czasownikiem mówienia ("mówi", "dodaje"…); jawna lista form,
# bo prefiks ("doda") łapałby też słowa z wypowiedzi ("dodatkowo"). Każdy inny fragment po
# myślniku traktujemy jako dalszy ciąg wypowiedzi i sprawdzamy.
_ATTRIBUTION = re.compile(r"^(?:" + "|".join(_ATTRIBUTION_VERBS) + r")\b")
# Skrót cytatu wielokropkiem jest dozwolony, ale z samych pojedynczych słów
# dałoby się skleić zdanie o odwrotnym sensie.
_MIN_FRAGMENT_WORDS = 3

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
    segment_index: int | None = None
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
    # Kto mówi te słowa w nagraniu i czy cytat jest na liście `quotes` z tym mówcą.
    speaker: str | None = None
    declared: bool = True


class VerificationReport(BaseModel):
    quotes: list[QuoteCheck]
    claims: list[ClaimCheck]
    inline_quotes: list[InlineQuoteCheck] = []
    # Odstępstwa od formatu redakcji – informacja dla redaktora, nie blokują eksportu.
    format_warnings: list[str] = []
    # Cytat z tekstu bez pozycji na liście cytatów albo przypisany na niej innemu mówcy.
    # Przy ręcznej edycji to ostrzeżenie; automat traktuje je jak błąd (`strict_ok`).
    attribution_warnings: list[str] = []

    @computed_field
    @property
    def ok(self) -> bool:
        return (
            all(q.status == QuoteStatus.VERIFIED for q in self.quotes)
            and not self.claims
            and all(q.verified for q in self.inline_quotes)
        )

    @computed_field
    @property
    def strict_ok(self) -> bool:
        return self.ok and not self.attribution_warnings

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


def word_count(text: str) -> int:
    return len(normalize(text).split())


def normalize(text: str) -> str:
    return " ".join(_NON_WORD.sub(" ", text.casefold()).split())


def _fragments(quote_text: str) -> list[str]:
    """Fragmenty cytatu między wielokropkami; [] gdy skrót jest zbyt rozdrobniony."""
    fragments = [f for f in (normalize(p) for p in _ELLIPSIS.split(quote_text)) if f]
    if len(fragments) > 1 and any(len(f.split()) < _MIN_FRAGMENT_WORDS for f in fragments):
        return []
    return fragments


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


def _windows(transcript: Transcript) -> list[str]:
    """Okna dla wszystkich segmentów, liczone raz na transkrypcję (przy długich nagraniach
    weryfikacja sprawdza dziesiątki cytatów na każdym zapisie)."""
    if transcript._windows is None or len(transcript._windows) != len(transcript.segments):
        transcript._windows = [_window_text(transcript.segments, i) for i in range(len(transcript.segments))]
    return transcript._windows


def _same_speaker(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and normalize(a) == normalize(b)


def check_quote(quote: Quote, transcript: Transcript) -> QuoteCheck:
    fragments = _fragments(quote.text)
    if not fragments:
        message = (
            "Skrót cytatu zbyt rozdrobniony: między wielokropkami potrzeba co najmniej 3 słów"
            if normalize(quote.text)
            else "Pusty cytat"
        )
        return QuoteCheck(quote=quote, status=QuoteStatus.NOT_FOUND, message=message)

    segments = transcript.segments
    cited = segment_at(transcript, quote.timecode_seconds)
    # Kilka segmentów ASR może zaczynać się w tej samej sekundzie – wszystkie są "wskazane".
    candidates = (
        [i for i, s in enumerate(segments) if s.start_seconds == segments[cited].start_seconds]
        if cited is not None
        else []
    )
    windows = _windows(transcript)
    matches = [i for i in candidates if _contains_in_order(windows[i], fragments)]
    if matches:
        verified = [i for i in matches if segments[i].speaker is None or _same_speaker(quote.speaker, segments[i].speaker)]
        if verified:
            return QuoteCheck(
                quote=quote,
                status=QuoteStatus.VERIFIED,
                found_at_seconds=segments[verified[0]].start_seconds,
                segment_index=verified[0],
                message="Cytat zgodny z transkrypcją",
            )
        segment = segments[matches[0]]
        return QuoteCheck(
            quote=quote,
            status=QuoteStatus.SPEAKER_MISMATCH,
            found_at_seconds=segment.start_seconds,
            segment_index=matches[0],
            message=f"W transkrypcji mówi: {segment.speaker}, w drafcie: {quote.speaker or 'brak'}",
        )

    for i, segment in enumerate(segments):
        if i not in candidates and _contains_in_order(windows[i], fragments):
            return QuoteCheck(
                quote=quote,
                status=QuoteStatus.TIMECODE_MISMATCH,
                found_at_seconds=segment.start_seconds,
                segment_index=i,
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
        if _contains_in_order(_windows(transcript)[i], fragments):
            return i
    return None


def dash_quotes(paragraph: str) -> list[str]:
    """Wypowiedzi z akapitu dialogowego "- cytat - mówi X. - Dalszy cytat."

    Każda linia akapitu zaczynająca się od myślnika to wypowiedź. Fragment po
    myślniku jest przypisaniem tylko wtedy, gdy zaczyna się czasownikiem mówienia;
    inaczej (np. "5 - 10 procent", "- a potem…") należy do cytatu i jest sprawdzany.
    Po przypisaniu i zakończonym zdaniu narracji kolejny fragment to nowa wypowiedź.
    """
    quotes: list[str] = []
    for line in paragraph.splitlines():
        if not _DASH_LINE.match(line):
            continue
        parts = _DASH_SPLIT.split(_DASH_LINE.sub("", line, count=1))
        current: str | None = parts[0]
        narration = ""
        for part in parts[1:]:
            if current is not None:
                if _ATTRIBUTION.match(part):
                    quotes.append(current)
                    current, narration = None, part
                else:
                    current = f"{current} - {part}"
            elif narration.rstrip().endswith((".", "!", "?", "…")):
                current = part
            else:
                narration = part
        if current is not None:
            quotes.append(current)
    return [q.strip() for q in quotes if q.strip()]


def extract_inline_quotes(draft: ArticleDraft) -> list[tuple[str, str]]:
    """Pary (miejsce, cytat) dla wypowiedzi w cudzysłowie lub po myślniku w tekście draftu."""
    places = [(f"tytuł {i}", t) for i, t in enumerate(draft.title_options, start=1)]
    places.append(("lead", draft.lead))
    for s_no, section in enumerate(draft.sections, start=1):
        if section.heading:
            places.append((f"śródtytuł {s_no}", section.heading))
        places += [(f"sekcja {s_no}, akapit {p_no}", p) for p_no, p in enumerate(section.paragraphs, start=1)]
    found = []
    for location, text in places:
        for match in _INLINE_QUOTE.finditer(text):
            quoted = next(g for g in match.groups() if g is not None).strip()
            if len(normalize(quoted).split()) >= _MIN_INLINE_QUOTE_WORDS:
                found.append((location, quoted))
        for quoted in dash_quotes(text):
            # Akapit z myślnikiem to zawsze wypowiedź (nie nazwa własna), więc sprawdzamy
            # też najkrótsze; może zawierać wtrącenia w cudzysłowie – sprawdzamy całość.
            if normalize(quoted):
                found.append((location, quoted))
    return found


def check_inline_quotes(draft: ArticleDraft, transcript: Transcript) -> list[InlineQuoteCheck]:
    declared = [(normalize(q.text), q) for q in draft.quotes]
    checks = []
    for location, text in extract_inline_quotes(draft):
        index = find_quote(text, transcript)
        speaker = None if index is None else transcript.segments[index].speaker
        is_declared = True
        if index is not None and speaker is not None:
            fragments = _fragments(text)
            matching = [q for n, q in declared if _contains_in_order(n, fragments) or (n and n in normalize(text))]
            is_declared = any(_same_speaker(q.speaker, speaker) for q in matching)
        checks.append(
            InlineQuoteCheck(
                location=location,
                text=text,
                verified=index is not None,
                found_at_seconds=None if index is None else transcript.segments[index].start_seconds,
                speaker=speaker,
                declared=is_declared,
            )
        )
    return checks


def attribution_warnings(checks: list[InlineQuoteCheck]) -> list[str]:
    return [
        f"Cytat „{c.text}” ({c.location}) nie ma na liście cytatów pozycji z mówcą – w nagraniu mówi: {c.speaker}. "
        "Sprawdź przypisanie w tekście."
        for c in checks
        if c.verified and not c.declared
    ]


def _format_warnings(draft: ArticleDraft) -> list[str]:
    # Import w funkcji: format_check korzysta z dash_quotes z tego modułu.
    from app.format_check import format_warnings

    return format_warnings(draft)


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

    inline = check_inline_quotes(draft, transcript)
    return VerificationReport(
        quotes=[check_quote(q, transcript) for q in draft.quotes],
        claims=claim_checks,
        inline_quotes=inline,
        attribution_warnings=attribution_warnings(inline),
        format_warnings=_format_warnings(draft),
    )
