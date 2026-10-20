"""Znaczniki cytatów w tekście generowanym przez model.

Model nie przepisuje cytatów ani przypisań – wstawia znaczniki odwołujące się do
listy `quotes` (numeracja od 1):

- `[[cytat:N]]` – fraza w cudzysłowie wpleciona w zdanie: „treść”;
- akapit złożony wyłącznie z `[[wypowiedź:N]]` – akapit dialogowy w stylu WP:
  "- treść - mówi Jan Kowalski, rolnik."

Aplikacja podstawia treść zweryfikowaną w transkrypcji i przypisanie zbudowane z
etykiety mówcy, więc w automacie zmyślony cytat albo zła osoba w przypisaniu są
niemożliwe z konstrukcji. Znacznik z błędnym numerem zostaje w tekście i blokuje
eksport (weryfikacja zgłasza go jako błąd).
"""

import re

from app.models import ATTRIBUTION_VERBS, ArticleDraft, Quote, Section

_INLINE_MARKER = re.compile(r"\[\[cytat:(\d+)\]\]")
_PARAGRAPH_MARKER = re.compile(r"^\s*\[\[wypowiedź:(\d+)\]\]\s*$")
ANY_MARKER = re.compile(r"\[\[(?:cytat|wypowiedź):[^\]]*\]\]")
# Etykiety ogólne z transkrypcji (anonimowi rozmówcy) – w tekście opisujemy rolę, nie numer.
_GENERIC = re.compile(r"^(?:rozmówca|rozmówczyni|mówca|mówczyni)\b", re.IGNORECASE)


def speaker_display(speaker: str | None) -> str:
    if not speaker or speaker.lower().startswith("mówca nieustalony"):
        return "słyszymy w nagraniu"
    if _GENERIC.match(speaker):
        return "rozmówczyni" if speaker.lower().startswith("rozmówczyni") else "rozmówca"
    return speaker


def dialogue_paragraph(quote: Quote) -> str:
    text = quote.text.strip().rstrip(".")
    who = speaker_display(quote.speaker)
    if who == "słyszymy w nagraniu":
        return f"- {text} - słyszymy w nagraniu."
    verb = quote.verb if quote.verb in ATTRIBUTION_VERBS else "mówi"
    return f"- {text} - {verb} {who}."


def _expand_text(text: str, quotes: list[Quote]) -> str:
    def inline(match: re.Match) -> str:
        number = int(match.group(1))
        if 1 <= number <= len(quotes):
            return f"„{quotes[number - 1].text.strip().rstrip('.')}”"
        return match.group(0)

    return _INLINE_MARKER.sub(inline, text)


def _expand_paragraph(paragraph: str, quotes: list[Quote]) -> str:
    match = _PARAGRAPH_MARKER.match(paragraph)
    if match:
        number = int(match.group(1))
        if 1 <= number <= len(quotes):
            return dialogue_paragraph(quotes[number - 1])
        return paragraph
    return _expand_text(paragraph, quotes)


def expand_markers(draft: ArticleDraft) -> ArticleDraft:
    quotes = draft.quotes
    return draft.model_copy(
        update={
            "title_options": [_expand_text(t, quotes) for t in draft.title_options],
            "lead": _expand_paragraph(draft.lead, quotes),
            "sections": [
                Section(
                    heading=_expand_text(s.heading, quotes) if s.heading else s.heading,
                    paragraphs=[_expand_paragraph(p, quotes) for p in s.paragraphs],
                )
                for s in draft.sections
            ],
        }
    )


def leftover_markers(draft: ArticleDraft) -> list[str]:
    texts = [*draft.title_options, draft.lead]
    for section in draft.sections:
        texts += [section.heading or "", *section.paragraphs]
    return [m.group(0) for t in texts for m in ANY_MARKER.finditer(t)]
