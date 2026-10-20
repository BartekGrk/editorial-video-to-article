"""Kontrola, czy przy cytacie nie pada nazwisko innego mówcy z nagrania.

Model pisze sam zdanie wokół `[[cytat:N]]` (np. „Minister Anna Nowak mówi: …”),
więc po rozwinięciu znaczników sprawdzamy każde dosłowne wystąpienie
zweryfikowanego cytatu: jeśli w jego kontekście pada nazwisko znanego mówcy
(także w odmianie), a cytat należy do kogoś innego – zgłaszamy konflikt.

Kontekst: zdanie z cytatem, poprzednie zdanie (gdy cytat otwiera zdanie) oraz
następna linia, gdy jest przypisaniem z myślnikiem („- mówi X”). Słowa wewnątrz
samego cytatu pomijamy. To heurystyka – nie wykryje osób spoza nagrania ani
opisów bez nazwiska („minister rolnictwa”).
"""

import re
from dataclasses import dataclass

from app.models import ArticleDraft, Transcript
from app.verification import QuoteStatus, check_quote

_TITLES = re.compile(r"^(?:(?:dr|prof|mgr|inż|ks|hab)\.?\s+)+", re.IGNORECASE)
_LABEL_SPLIT = re.compile(r"\s*(?:,|\(|\s[-–—]\s)")
_CAPITALIZED = re.compile(r"[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż-]+")
# Koniec zdania, ale nie po typowych skrótach ("ds.", "m.in.", "ok.", "tys.").
_ABBREVIATIONS = r"(?<!\bds)(?<!\bok)(?<!\bin)(?<!\btzw)(?<!\bnp)(?<!\bproc)(?<!\btys)(?<!\bmln)(?<!\bmld)(?<!\bul)(?<!\bprof)(?<!\bdr)(?<!\bo\.o)(?<!\bSp)"
_SENTENCE_END = re.compile(_ABBREVIATIONS + r"[.!?…](?=\s+[A-ZĄĆĘŁŃÓŚŹŻ„\"-])")
_DASH_ATTRIBUTION_LINE = re.compile(r"^\s*[-–—]\s*[a-ząćęłńóśźż]")


@dataclass
class _Person:
    label: str
    surname: str

    @property
    def pattern(self) -> re.Pattern:
        # Odmiana: "Nowak" → "Nowaka", "Kowalski" → "Kowalskiego"; granica słowa i limit
        # końcówki, żeby "Nowakowski" nie był brany za "Nowak".
        stem = self.surname[: max(4, len(self.surname) - 2)]
        return re.compile(rf"\b{re.escape(stem)}[\wąćęłńóśźż]{{0,4}}\b")


def _person(label: str | None) -> _Person | None:
    if not label:
        return None
    head = _TITLES.sub("", _LABEL_SPLIT.split(label, maxsplit=1)[0].strip())
    words = _CAPITALIZED.findall(head)
    # Co najmniej imię i nazwisko – etykiety ogólne ("Reporter", "Rozmówca 1") pomijamy.
    if len(words) < 2:
        return None
    return _Person(label=label, surname=words[-1])


def _contexts(text: str, core: str) -> list[str]:
    contexts = []
    lines = text.split("\n")
    for line_no, line in enumerate(lines):
        start = 0
        while (position := line.find(core, start)) >= 0:
            start = position + len(core)
            bounds = [0, *[m.end() for m in _SENTENCE_END.finditer(line)], len(line)]
            sentence_start = max(b for b in bounds if b <= position)
            sentence_end = min((b for b in bounds if b >= position + len(core)), default=len(line))
            context = line[sentence_start:position] + " " + line[position + len(core) : sentence_end]
            if not line[sentence_start:position].strip(" „\"“-–—:"):
                # Cytat otwiera zdanie – przypisanie bywa w zdaniu poprzednim.
                previous = [b for b in bounds if b < sentence_start]
                context = line[previous[-1] if previous else 0 : sentence_start] + " " + context
            if line_no + 1 < len(lines) and _DASH_ATTRIBUTION_LINE.match(lines[line_no + 1]):
                context += " " + lines[line_no + 1]
            contexts.append(context)
    return contexts


def attribution_conflicts(draft: ArticleDraft, transcript: Transcript) -> list[str]:
    people = {p.label: p for p in (_person(s.speaker) for s in transcript.segments) if p}
    if not people:
        return []
    texts = [*draft.title_options, draft.lead]
    for section in draft.sections:
        texts += [section.heading or "", "\n".join(section.paragraphs)]

    warnings = []
    for quote in draft.quotes:
        check = check_quote(quote, transcript)
        if check.status != QuoteStatus.VERIFIED:
            continue  # błędny cytat zgłasza już weryfikacja
        speaker = transcript.segments[check.segment_index].speaker
        own = _person(speaker)
        core = quote.text.strip().rstrip(".")
        if len(core) < 3:
            continue
        for text in texts:
            for context in _contexts(text, core):
                named = sorted(
                    {
                        p.label
                        for p in people.values()
                        if p.pattern.search(context) and not (own and p.surname == own.surname)
                    }
                )
                if named:
                    warnings.append(
                        f"Przy cytacie „{core}” pada nazwisko: {', '.join(named)}, "
                        f"a w nagraniu te słowa mówi: {speaker or 'mówca nieustalony'}."
                    )
    return list(dict.fromkeys(warnings))
