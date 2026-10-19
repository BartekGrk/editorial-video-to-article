"""Silnik regułowy bez AI.

Służy do pracy bez klucza API (tryb demonstracyjny) i do testów offline.
Nie pisze dobrego tekstu – składa szablon z dosłownych wypowiedzi, żeby dało się
przejść cały proces w aplikacji: analiza → draft → weryfikacja → edycja → eksport.
"""

import re
from collections import OrderedDict

from app.engines.base import DraftRequest, MaterialInput
from app.models import (
    ArticleDraft,
    ArticleMode,
    Claim,
    EditorialStyleProfile,
    EvidencePack,
    Quote,
    Section,
    Thread,
    TranscriptSegment,
)
from app.verification import normalize

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Prowadzący zadaje pytania; jego słowa nie są wypowiedziami rozmówców do cytowania.
_HOST = re.compile(r"reporter|redaktor|prowadząc|dziennikar|lektor", re.IGNORECASE)
# Formułki prowadzącego (powitania, przedstawianie gościa) – nie są wypowiedziami do cytowania.
_HOST_PHRASES = re.compile(
    r"\b(?:moim gościem|naszym gościem|dzień dobry|dziękuję za (?:rozmowę|zaproszenie)|witam|zapraszam)\b",
    re.IGNORECASE,
)
_NUMERIC = re.compile(
    r"\d|\b(?:tysiąc|tysięcy|milion|miliard|procent|złotych|zł|groszy|euro|dolarów)\w*", re.IGNORECASE
)
# Powyżej tej liczby słów rozmówców materiał zwykle wystarcza na pełny artykuł.
_FULL_MODE_MIN_WORDS = 400
_MIN_QUOTE_WORDS = 5
_DEMO_NOTE = "Draft z trybu demonstracyjnego (bez AI): szablon z dosłownych wypowiedzi do dalszej redakcji."


_MAX_TITLE_QUOTE_WORDS = 8
_SHORT_MAX_WORDS = 90
_MAX_THREADS = 4


def _subject(material: MaterialInput) -> str:
    return (material.title or material.topic or "Materiał wideo").rstrip(" .?!:")


def _who(quote: Quote, capital: bool = False) -> str:
    who = quote.speaker or "rozmówca"
    return who[:1].upper() + who[1:] if capital else who


def _attribution(quote: Quote) -> str:
    return f"mówi {quote.speaker}" if quote.speaker else "słyszymy w nagraniu"


def _threads(guests: list[TranscriptSegment], quotes: list[Quote]) -> list[Thread]:
    """Wątki: osobno dla każdego rozpoznanego rozmówcy albo kolejne części nagrania."""
    named = OrderedDict()
    for segment in guests:
        if segment.speaker:
            named.setdefault(segment.speaker, []).append(segment)
    if len(named) >= 2:
        groups = list(named.items())
    else:
        # Bez mówców dzielimy nagranie na zbliżone objętościowo części w kolejności czasu.
        total = sum(_words(s.text) for s in guests) or 1
        size = total / min(_MAX_THREADS, max(1, len(quotes) // 2))
        chunks, current, count = [], [], 0
        for segment in guests:
            current.append(segment)
            count += _words(segment.text)
            if count >= size * (len(chunks) + 1):
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)
        groups = [(None, chunk) for chunk in chunks]

    threads = []
    for name, items in groups:
        times = [s.start_seconds for s in items]
        first = next((q for q in quotes if q.timecode_seconds in times), None)
        if first is None:
            continue
        # Śródtytuł z pierwszych słów wypowiedzi – szablon do zmiany przez redaktora.
        heading = name or " ".join(first.text.split()[:6]).rstrip(",;:") + "…"
        threads.append(Thread(name=heading, summary=first.text, timecodes=times))
    return threads


def _is_host(segment: TranscriptSegment) -> bool:
    return segment.speaker is not None and bool(_HOST.search(segment.speaker))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


def _words(text: str) -> int:
    return len(normalize(text).split())


class HeuristicEngine:
    name = "heuristic"
    external = False

    def label_speakers(self, material: MaterialInput) -> None:
        # Bez modelu językowego nie odróżnimy mówców w surowej transkrypcji ASR.
        return None

    def extract_evidence(self, material: MaterialInput) -> EvidencePack:
        segments = material.transcript.segments
        guests = [s for s in segments if not _is_host(s)]

        quotes, claims = [], []
        for segment in guests:
            sentences = _sentences(segment.text)
            # Pytania to zwykle słowa prowadzącego (w ASR bez mówców nie da się go odfiltrować inaczej).
            quotable = [
                s
                for s in sentences
                if _words(s) >= _MIN_QUOTE_WORDS and not s.endswith("?") and not _HOST_PHRASES.search(s)
            ]
            if quotable:
                quotes.append(
                    Quote(text=quotable[0].rstrip("."), speaker=segment.speaker, timecode_seconds=segment.start_seconds)
                )
            for sentence in sentences:
                if _NUMERIC.search(sentence) and not sentence.endswith("?"):
                    claims.append(
                        Claim(
                            statement=sentence,
                            evidence_timecodes=[segment.start_seconds],
                            requires_verification=True,
                            note="Liczba lub kwota w wypowiedzi rozmówcy – sprawdzić w źródle",
                        )
                    )

        threads = _threads(guests, quotes)
        guest_words = sum(_words(s.text) for s in guests)
        mode = ArticleMode.FULL if guest_words >= _FULL_MODE_MIN_WORDS and len(quotes) >= 4 else ArticleMode.SHORT
        named = {s.speaker for s in guests if s.speaker}
        gaps = ["Tryb demonstracyjny: analiza regułowa, bez rozumienia treści."]
        if any(s.speaker is None for s in segments):
            gaps.append("Część segmentów nie ma przypisanego mówcy.")

        voices = f"{len(named)} rozmówców" if named else "rozmówcy bez rozpoznanych nazwisk"
        return EvidencePack(
            # Bez cudzysłowu: weryfikator traktuje tekst w „…” jako cytat.
            summary=f"{_subject(material)}. W nagraniu: {voices}, około {guest_words} słów wypowiedzi.",
            threads=threads,
            quotes=quotes,
            claims=claims,
            suggested_mode=mode,
            mode_rationale=(
                f"{guest_words} słów wypowiedzi i {len(quotes)} cytatów "
                f"(próg FULL: {_FULL_MODE_MIN_WORDS} słów i 4 cytaty)."
            ),
            gaps=gaps,
        )

    def build_style_profile(self, articles: list[str]) -> EditorialStyleProfile:
        titles, leads, paragraphs, subheadings = [], [], [], 0
        for article in articles:
            blocks = [b.strip() for b in re.split(r"\n\s*\n", article.strip()) if b.strip()]
            if not blocks:
                continue
            titles.append(_words(blocks[0].lstrip("# ")))
            if len(blocks) > 1:
                leads.append(_words(blocks[1]))
            for block in blocks[2:]:
                # Krótki blok bez kropki na końcu traktujemy jako śródtytuł.
                if block.startswith("#") or (_words(block) <= 8 and not block.endswith((".", "!", "?", "”"))):
                    subheadings += 1
                else:
                    paragraphs.append(_words(block))

        def avg(values: list[int], default: int) -> int:
            return round(sum(values) / len(values)) if values else default

        title_words, lead_words, paragraph_words = avg(titles, 8), avg(leads, 35), avg(paragraphs, 50)
        per_article = subheadings / max(len(articles), 1)
        return EditorialStyleProfile(
            title_style=f"Średnio {title_words} słów w tytule.",
            lead_style=f"Lead średnio {lead_words} słów.",
            paragraph_style=f"Akapity średnio {paragraph_words} słów.",
            subheading_style=f"Średnio {per_article:.1f} śródtytułu na artykuł.",
            quote_style="Nieustalone automatycznie – uzupełnij ręcznie.",
            tone="Nieustalone automatycznie – uzupełnij ręcznie.",
            context_style="Nieustalone automatycznie – uzupełnij ręcznie.",
            multi_speaker_style="Nieustalone automatycznie – uzupełnij ręcznie.",
            short_mode_notes="Profil wyliczony statystycznie w trybie demonstracyjnym.",
            full_mode_notes="Profil wyliczony statystycznie w trybie demonstracyjnym.",
            typical_title_words=title_words,
            typical_lead_words=lead_words,
        )

    def write_draft(self, request: DraftRequest) -> ArticleDraft:
        evidence, material = request.evidence, request.material
        quotes = evidence.quotes
        subject = _subject(material)

        short_quotes = [q for q in quotes if _words(q.text) <= _MAX_TITLE_QUOTE_WORDS]
        titles = [subject]
        if short_quotes:
            titles.append(f"{subject}. „{short_quotes[0].text}”")
        titles.append(f"{material.topic or subject}: wideo")
        titles = list(dict.fromkeys(titles))
        if len(titles) < 2:
            titles.append(f"{subject} – relacja wideo")

        lead = f"{subject} – materiał wideo."
        if quotes:
            lead += f" {_who(quotes[0], capital=True)} w nagraniu: „{quotes[0].text}”."

        if request.mode == ArticleMode.SHORT:
            if len(quotes) > 1 and _words(lead) + _words(quotes[1].text) <= _SHORT_MAX_WORDS:
                lead += f" {_who(quotes[1], capital=True)}: „{quotes[1].text}”."
            sections: list[Section] = []
            used = quotes[:2]
        else:
            sections = []
            used = quotes[:1]
            for thread in evidence.threads:
                thread_quotes = [q for q in quotes if q.timecode_seconds in thread.timecodes and q not in used][:3]
                if not thread_quotes:
                    continue
                used += thread_quotes
                sections.append(
                    Section(heading=thread.name, paragraphs=[f"- {q.text} - {_attribution(q)}." for q in thread_quotes])
                )

        return ArticleDraft(
            mode=request.mode,
            title_options=titles[:3],
            lead=lead,
            sections=sections,
            quotes=used,
            claims=evidence.claims,
            editor_notes=[_DEMO_NOTE, *evidence.gaps],
        )

    def repair_draft(self, request: DraftRequest, draft: ArticleDraft, problems: list[str]) -> ArticleDraft:
        # Bez modelu nie przeredagujemy tekstu; zostawiamy draft i opisujemy problemy redaktorowi.
        return draft.model_copy(update={"editor_notes": [*draft.editor_notes, *problems]})
