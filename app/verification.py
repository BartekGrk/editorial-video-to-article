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
    r"„([^”“„]+)[”“]|„([^”“„\"]+)\"|“([^”]+)”|\"([^\"]+)\"|»([^«]+)«|«([^»]+)»"
)
# Krótkie frazy w cudzysłowie to zwykle nazwy własne (programy, ustawy),
# a nie cytaty; sprawdzamy tylko wypowiedzi od 4 słów.
_MIN_INLINE_QUOTE_WORDS = 4
# Konwencja WP Finanse: wypowiedź jako akapit z myślnikiem,
# np. "- Treść wypowiedzi - mówi Jan Kowalski, rolnik."
_DASH_LINE = re.compile(r"^\s*[-–—]\s*(?=[^\s\-–—])")
# Dalsze linie akapitu z myślnikiem traktujemy jako wypowiedź, gdy zaczynają się jak zdanie
# (wielka litera, cyfra, wielokropek, cudzysłów) – linie listy od małej litery to nie cytaty.
_SENTENCE_START = re.compile(r"^[\W_]*(?:[A-ZĄĆĘŁŃÓŚŹŻ0-9]|…|\.\.\.)")
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
podkreślał podkreślała podkreślali dodawał dodawała dodawali zaznaczał zaznaczała zaznaczają
wyjaśniał wyjaśniała wyjaśniają zauważał zauważała zauważają wskazał wskazała wskazują
opowiedział opowiedziała opowiadają oceniał oceniała stwierdzał stwierdzała przyznawał przyznawała
zapewniał zapewniała zapewniają ostrzegał ostrzegała ostrzegają przypominał przypominała przypominają
wspomniał wspomniała wspominają deklarują zapowiadał zapowiadała zapowiadają podsumowywał podsumowywała
kwitował kwitowała argumentują dopowiadał dopowiadała wtrącał wtrącała zastrzegał zastrzegała zastrzegają
apelują alarmują narzekają skarżą komentują relacjonują przekonywali
""".split()
# Po myślniku przypisanie zaczyna się czasownikiem mówienia ("mówi", "dodaje"…); jawna lista form,
# bo prefiks ("doda") łapałby też słowa z wypowiedzi ("dodatkowo"). Każdy inny fragment po
# myślniku traktujemy jako dalszy ciąg wypowiedzi i sprawdzamy.
_VERB_GROUP = "(?:" + "|".join(_ATTRIBUTION_VERBS) + ")"
_ATTRIBUTION = re.compile(r"^" + _VERB_GROUP + r"\b")
# Linia zawierająca gdziekolwiek "- mówi …" to wypowiedź z przypisaniem (także od małej litery).
_HAS_ATTRIBUTION = re.compile(r"\s[-–—]\s+" + _VERB_GROUP + r"\b")
# Przypisanie samym nazwiskiem i krótką funkcją na końcu linii: "- Cytat - Jan Kowalski, ekonomista."
# Funkcja bez przecinka i najwyżej 6 słów – inaczej ("Jan Nowak, premier, ukradł…") to dalszy ciąg cytatu.
_NAME_ATTRIBUTION = re.compile(
    r"^[A-ZĄĆĘŁŃÓŚŹŻ][\w-]+(?: [A-ZĄĆĘŁŃÓŚŹŻ][\w-]+)+, (?:[^\s,.!?]+ ?){1,6}[.!?]?$"
)
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


def _fragment_options(quote_text: str) -> tuple[list[list[str]], bool]:
    """Warianty dopasowania cytatu i informacja, czy odrzucono zbyt rozdrobniony skrót.

    Najpierw cały tekst (ASR zapisuje pauzy jako "..." – dosłowny cytat z pauzą musi
    przejść), potem fragmenty między wielokropkami, jeśli każdy ma co najmniej 3 słowa.
    """
    full = normalize(quote_text)
    if not full:
        return [], False
    options = [[full]]
    parts = [f for f in (normalize(p) for p in _ELLIPSIS.split(quote_text)) if f]
    fragmented = len(parts) > 1 and any(len(p.split()) < _MIN_FRAGMENT_WORDS for p in parts)
    if len(parts) > 1 and not fragmented:
        options.append(parts)
    return options, fragmented


def _matches(options: list[list[str]], window: str) -> bool:
    return any(_contains_in_order(window, fragments) for fragments in options)


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


def _speaker_agrees(declared: str | None, segment: str | None) -> bool:
    """Mówca z listy cytatów zgadza się z segmentem. Segment bez mówcy niczego nie wyklucza
    (te słowa mógł powiedzieć ktokolwiek), więc pasuje do każdej deklaracji."""
    return segment is None or _same_speaker(declared, segment)


def _same_speaker(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and normalize(a) == normalize(b)


def check_quote(quote: Quote, transcript: Transcript) -> QuoteCheck:
    options, fragmented = _fragment_options(quote.text)
    if not options:
        return QuoteCheck(quote=quote, status=QuoteStatus.NOT_FOUND, message="Pusty cytat")

    segments = transcript.segments
    cited = segment_at(transcript, quote.timecode_seconds)
    # Kilka segmentów ASR może zaczynać się w tej samej sekundzie – wszystkie są "wskazane".
    candidates = (
        [i for i, s in enumerate(segments) if s.start_seconds == segments[cited].start_seconds]
        if cited is not None
        else []
    )
    windows = _windows(transcript)
    matches = [i for i in candidates if _matches(options, windows[i])]
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
        if i not in candidates and _matches(options, windows[i]):
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

    message = (
        "Skrót cytatu zbyt rozdrobniony: między wielokropkami potrzeba co najmniej 3 słów"
        if fragmented
        else "Brak takiej wypowiedzi w transkrypcji: cytat nie może zostać opublikowany"
    )
    return QuoteCheck(quote=quote, status=QuoteStatus.NOT_FOUND, message=message)


def find_quotes(text: str, transcript: Transcript) -> list[int]:
    """Indeksy wszystkich segmentów, od których zaczyna się cytat (bez względu na mówcę)."""
    options, _ = _fragment_options(text)
    if not options:
        return []
    return [i for i, window in enumerate(_windows(transcript)) if _matches(options, window)]


def dash_quotes(paragraph: str) -> list[str]:
    """Wypowiedzi z akapitu dialogowego "- cytat - mówi X. - Dalszy cytat."

    Każda linia akapitu zaczynająca się od myślnika to wypowiedź. Fragment po
    myślniku jest przypisaniem tylko wtedy, gdy zaczyna się czasownikiem mówienia;
    inaczej (np. "5 - 10 procent", "- a potem…") należy do cytatu i jest sprawdzany.
    Po przypisaniu i zakończonym zdaniu narracji kolejny fragment to nowa wypowiedź.
    """
    quotes: list[str] = []
    for number, line in enumerate(paragraph.splitlines()):
        if not _DASH_LINE.match(line):
            continue
        content = _DASH_LINE.sub("", line, count=1)
        if number > 0 and not (_SENTENCE_START.match(content) or _HAS_ATTRIBUTION.search(content)):
            continue
        parts = _DASH_SPLIT.split(content)
        current: str | None = parts[0]
        narration = ""
        for part in parts[1:]:
            if current is not None:
                if _ATTRIBUTION.match(part) or _NAME_ATTRIBUTION.match(part):
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
        matches = find_quotes(text, transcript)
        options, _ = _fragment_options(text)
        normalized = normalize(text)
        candidates = [q for n, q in declared if n and (_matches(options, n) or n in normalized)]
        index, is_declared = (matches[0] if matches else None), True
        labelled = [i for i in matches if transcript.segments[i].speaker is not None]
        if labelled:
            # Te same słowa mogą paść kilka razy (np. prowadzący powtarza słowa gościa):
            # wystarczy, że deklarowany mówca jest jednym z mówiących je w nagraniu.
            agreed = [
                i for i in matches if any(_speaker_agrees(q.speaker, transcript.segments[i].speaker) for q in candidates)
            ]
            is_declared = bool(agreed)
            # W ostrzeżeniu podajemy rozpoznanego mówcę – "None" nie dałoby się zadeklarować.
            index = agreed[0] if agreed else labelled[0]
        speaker = transcript.segments[index].speaker if index is not None else None
        checks.append(
            InlineQuoteCheck(
                location=location,
                text=text,
                verified=bool(matches),
                found_at_seconds=transcript.segments[index].start_seconds if index is not None else None,
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
