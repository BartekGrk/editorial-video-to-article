"""Zgodność draftu z formatem tekstów wideo WP Finanse (ostrzeżenia, nie blokady).

Progi z analizy 24 publicznych tekstów finanse.wp.pl/tag/wideo (2026):
- SHORT: zwykle jeden akapit 35–80 słów, bez śródtytułów;
- FULL: 280–850 słów, lead 30–50 słów, 2–5 śródtytułów, 1–5 cytatów z myślnikiem;
- tytuły 6–12 słów.
Zakresy poniżej są nieco szersze, żeby ostrzegać tylko przy wyraźnym odstępstwie.
"""

from app.models import ArticleDraft, ArticleMode
from app.verification import dash_quotes, normalize

SHORT_WORDS = (30, 110)
FULL_WORDS = (250, 1000)
FULL_LEAD_WORDS = (20, 70)
FULL_HEADINGS = (2, 6)
TITLE_WORDS = (4, 14)


def _words(text: str) -> int:
    return len(normalize(text).split())


def format_warnings(draft: ArticleDraft) -> list[str]:
    warnings = []
    body = [p for s in draft.sections for p in s.paragraphs]
    total = _words(draft.lead) + sum(_words(p) for p in body)
    headings = sum(1 for s in draft.sections if s.heading)

    for number, title in enumerate(draft.title_options, start=1):
        if not TITLE_WORDS[0] <= _words(title) <= TITLE_WORDS[1]:
            warnings.append(f"Tytuł {number} ma {_words(title)} słów (zwykle {TITLE_WORDS[0]}–{TITLE_WORDS[1]}).")

    if draft.mode == ArticleMode.SHORT:
        if not SHORT_WORDS[0] <= total <= SHORT_WORDS[1]:
            warnings.append(f"Tekst pod wideo ma {total} słów (zwykle {SHORT_WORDS[0]}–{SHORT_WORDS[1]}).")
        if headings:
            warnings.append("Tekst pod wideo zwykle nie ma śródtytułów.")
        if len(body) > 2:
            warnings.append(f"Tekst pod wideo ma {len(body) + 1} akapitów – zwykle jeden.")
    else:
        if not FULL_WORDS[0] <= total <= FULL_WORDS[1]:
            warnings.append(f"Artykuł ma {total} słów (zwykle {FULL_WORDS[0]}–{FULL_WORDS[1]}).")
        lead = _words(draft.lead)
        if not FULL_LEAD_WORDS[0] <= lead <= FULL_LEAD_WORDS[1]:
            warnings.append(f"Lead ma {lead} słów (zwykle {FULL_LEAD_WORDS[0]}–{FULL_LEAD_WORDS[1]}).")
        if not FULL_HEADINGS[0] <= headings <= FULL_HEADINGS[1]:
            warnings.append(f"Artykuł ma {headings} śródtytułów (zwykle {FULL_HEADINGS[0]}–{FULL_HEADINGS[1]}).")
        if not any(dash_quotes(p) for p in body):
            warnings.append("Brak cytatu w formie akapitu z myślnikiem („- … - mówi X”).")
    return warnings
