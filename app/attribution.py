"""Nazwiska mówców w otoczeniu cytatu (pomocnicze dla weryfikacji).

Model pisze sam zdanie wokół `[[cytat:N]]` (np. „Minister Anna Nowak mówi: …”),
a redaktor może dopisać przypisanie ręcznie. Dla każdego cytatu znalezionego
w tekście sprawdzamy, czy w jego kontekście pada nazwisko znanego mówcy
(także w odmianie), który tych słów nie powiedział.

Kontekst: zdanie z cytatem (z obsługą skrótów), poprzednie zdanie, gdy cytat
otwiera zdanie, oraz następna linia „- mówi X”, gdy linia zawiera sam cytat.
Dla akapitu dialogowego – cała linia. Nazwisko liczy się tylko wtedy, gdy pada
poza samym cytatem. To heurystyka: nie wykryje osób spoza nagrania ani opisów
bez nazwiska („minister rolnictwa”).
"""

import re
from dataclasses import dataclass

TITLES = re.compile(r"^(?:(?:dr|prof|mgr|inż|ks|hab)\.?\s+)+", re.IGNORECASE)
_LABEL_SPLIT = re.compile(r"\s*(?:,|\(|\s[-–—]\s)")
_CAPITALIZED = re.compile(r"[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż-]+")
# Koniec zdania, ale nie po typowych skrótach ("ds.", "m.in.", "ok.", "tys.").
_ABBREVIATIONS = (
    r"(?<!\bds)(?<!\bok)(?<!\bin)(?<!\btzw)(?<!\bnp)(?<!\bproc)(?<!\btys)(?<!\bmln)(?<!\bmld)"
    r"(?<!\bul)(?<!\bprof)(?<!\bdr)(?<!\bo\.o)(?<!\bSp)"
)
_SENTENCE_END = re.compile(_ABBREVIATIONS + r"[.!?…](?=\s+[A-ZĄĆĘŁŃÓŚŹŻ„\"-])")
_DASH_ATTRIBUTION_LINE = re.compile(r"^\s*[-–—]\s*[a-ząćęłńóśźż]")


@dataclass(frozen=True)
class Person:
    label: str
    surname: str

    @property
    def pattern(self) -> re.Pattern:
        # Odmiana: "Nowak" → "Nowaka", "Kowalski" → "Kowalskiego"; granica słowa i limit
        # końcówki, żeby "Nowakowski" nie był brany za "Nowak".
        stem = self.surname[: max(4, len(self.surname) - 2)]
        return re.compile(rf"\b{re.escape(stem)}[\wąćęłńóśźż]{{0,4}}\b")


def person(label: str | None) -> Person | None:
    """Osoba z etykiety mówcy ("dr Jan Kowalski, rolnik", "Minister Anna Nowak (PSL)")."""
    if not label:
        return None
    head = TITLES.sub("", _LABEL_SPLIT.split(label, maxsplit=1)[0].strip())
    words = _CAPITALIZED.findall(head)
    # Co najmniej imię i nazwisko – etykiety ogólne ("Reporter", "Rozmówca 1") pomijamy.
    if len(words) < 2:
        return None
    return Person(label=label, surname=words[-1])


def people(labels) -> list[Person]:
    found = {p.label: p for p in (person(label) for label in labels) if p}
    return list(found.values())


def quote_context(line: str, start: int, end: int, next_line: str | None) -> str:
    """Zdanie z cytatem `line[start:end]` (wraz z cudzysłowem) i ewentualne przypisanie obok."""
    bounds = [0, *[m.end() for m in _SENTENCE_END.finditer(line)], len(line)]
    sentence_start = max(b for b in bounds if b <= start)
    sentence_end = min((b for b in bounds if b >= end), default=len(line))
    context = line[sentence_start:sentence_end]
    if not line[sentence_start:start].strip(" „\"“«»-–—:"):
        # Cytat otwiera zdanie – przypisanie bywa w zdaniu poprzednim.
        previous = [b for b in bounds if b < sentence_start]
        context = line[previous[-1] if previous else 0 : sentence_start] + " " + context
    if next_line is not None and not line[:start].strip(" „\"“«»") and not line[end:].strip(" .”\"“«»"):
        # Linia z samym cytatem i przypisanie w następnej linii ("- mówi X").
        if _DASH_ATTRIBUTION_LINE.match(next_line):
            context += " " + next_line
    return context


def conflicting_names(context: str, quote: str, everyone: list[Person], speaker: str | None) -> list[str]:
    """Znani mówcy wymienieni w kontekście poza samym cytatem, inni niż autor cytatu."""
    own = person(speaker)
    named = []
    for candidate in everyone:
        if own and candidate.surname == own.surname:
            continue
        outside = len(candidate.pattern.findall(context)) - len(candidate.pattern.findall(quote))
        if outside > 0:
            named.append(candidate.label)
    return sorted(set(named))
