"""Znaczniki cytatów w tekście generowanym przez model.

Model nie przepisuje cytatów ani przypisań – wstawia znaczniki odwołujące się do
listy `quotes` (numeracja od 1):

- `[[cytat:N]]` – fraza w cudzysłowie wpleciona w zdanie: „treść”;
- akapit złożony wyłącznie z `[[wypowiedź:N]]` – akapit dialogowy w stylu WP:
  "- treść - mówi Jan Kowalski, rolnik."

Znaczniki rozwija pipeline (nie silnik), bo do tego potrzebna jest transkrypcja:
przypisanie w akapicie dialogowym bierzemy z etykiety segmentu, w którym cytat
faktycznie znaleziono, a nie z deklaracji modelu. Znacznik z błędnym numerem
albo nierozpoznanej postaci zostaje w tekście i blokuje eksport.
"""

import re

from app.models import ATTRIBUTION_VERBS, ArticleDraft, Quote, Section, Transcript

# Tolerancja na drobne odstępstwa zapisu modelu: wielkość liter, brak ogonków, spacje, "cytat 1".
_INLINE_MARKER = re.compile(r"\[\[\s*(?:cytat|wypowied[źz])\s*[:\s]\s*(\d+)\s*\]\]", re.IGNORECASE)
_PARAGRAPH_MARKER = re.compile(r"^\s*\[\[\s*wypowied[źz]\s*[:\s]\s*(\d+)\s*\]\]\s*$", re.IGNORECASE)
# Każda pozostałość w podwójnych nawiasach to nierozwinięty znacznik – nie może trafić do CMS.
ANY_MARKER = re.compile(r"\[\[[^\]\n]{0,40}\]{0,2}")
# Etykiety ogólne z transkrypcji (anonimowi rozmówcy) – w tekście opisujemy rolę, nie numer.
_GENERIC = re.compile(r"^(?:rozmówca|rozmówczyni|mówca|mówczyni)\b", re.IGNORECASE)
UNHEARD = "słyszymy w nagraniu"


def speaker_display(speaker: str | None) -> str:
    if not speaker or speaker.lower().startswith("mówca nieustalony"):
        return UNHEARD
    if _GENERIC.match(speaker):
        return "rozmówczyni" if speaker.lower().startswith("rozmówczyni") else "rozmówca"
    return clean_speaker_label(speaker)


def clean_speaker_label(label: str) -> str:
    """Etykieta bez dwukropka (parser "Mówca: treść") i bez myślników ("Jan Kowalski - prezes"),
    które parser akapitu dialogowego wziąłby za początek nowej wypowiedzi."""
    return " ".join(re.sub(r"\s+[-–—]\s+", ", ", label.replace(":", " ")).split())


def _transcript_speaker(quote: Quote, transcript: Transcript | None) -> str | None:
    """Mówca segmentu, w którym cytat występuje w transkrypcji (None, gdy nieznany)."""
    if transcript is None:
        return quote.speaker
    from app.verification import QuoteStatus, check_quote

    check = check_quote(quote, transcript)
    if check.status == QuoteStatus.NOT_FOUND or check.segment_index is None:
        return None
    return transcript.segments[check.segment_index].speaker


def dialogue_paragraph(quote: Quote, speaker: str | None) -> str:
    text = quote.text.strip().rstrip(".")
    who = speaker_display(speaker)
    if who == UNHEARD:
        return f"- {text} - {UNHEARD}."
    verb = quote.verb if quote.verb in ATTRIBUTION_VERBS else "mówi"
    return f"- {text} - {verb} {who}."


def _quote(quotes: list[Quote], number: str) -> Quote | None:
    index = int(number)
    return quotes[index - 1] if 1 <= index <= len(quotes) else None


def _expand_text(text: str, quotes: list[Quote]) -> str:
    def inline(match: re.Match) -> str:
        quote = _quote(quotes, match.group(1))
        return f"„{quote.text.strip().rstrip('.')}”" if quote else match.group(0)

    return _INLINE_MARKER.sub(inline, text)


def _expand_paragraph(paragraph: str, quotes: list[Quote], transcript: Transcript | None) -> str:
    match = _PARAGRAPH_MARKER.match(paragraph)
    if match:
        quote = _quote(quotes, match.group(1))
        return dialogue_paragraph(quote, _transcript_speaker(quote, transcript)) if quote else paragraph
    # [[wypowiedź:N]] wewnątrz zdania traktujemy jak [[cytat:N]].
    return _expand_text(paragraph, quotes)


def expand_markers(draft: ArticleDraft, transcript: Transcript | None = None) -> ArticleDraft:
    quotes = draft.quotes
    return draft.model_copy(
        update={
            "title_options": [_expand_text(t, quotes) for t in draft.title_options],
            "lead": _expand_paragraph(draft.lead, quotes, transcript),
            "sections": [
                Section(
                    heading=_expand_text(s.heading, quotes) if s.heading else s.heading,
                    paragraphs=[_expand_paragraph(p, quotes, transcript) for p in s.paragraphs],
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
