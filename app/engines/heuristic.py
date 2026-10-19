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
_NUMERIC = re.compile(
    r"\d|\b(?:tysiąc|tysięcy|milion|miliard|procent|złotych|zł|groszy|euro|dolarów)\w*", re.IGNORECASE
)
# Powyżej tej liczby słów rozmówców materiał zwykle wystarcza na pełny artykuł.
_FULL_MODE_MIN_WORDS = 600
_MIN_QUOTE_WORDS = 5
_DEMO_NOTE = "Draft z trybu demonstracyjnego (bez AI): szablon z dosłownych wypowiedzi do dalszej redakcji."


def _is_host(segment: TranscriptSegment) -> bool:
    return segment.speaker is not None and bool(_HOST.search(segment.speaker))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


def _words(text: str) -> int:
    return len(normalize(text).split())


class HeuristicEngine:
    name = "heuristic"
    external = False

    def extract_evidence(self, material: MaterialInput) -> EvidencePack:
        segments = material.transcript.segments
        guests = [s for s in segments if not _is_host(s)]

        by_speaker: OrderedDict[str, list[TranscriptSegment]] = OrderedDict()
        for segment in guests:
            by_speaker.setdefault(segment.speaker or "Mówca nieznany", []).append(segment)

        threads = [
            Thread(
                name=speaker,
                summary=_sentences(items[0].text)[0],
                timecodes=[s.start_seconds for s in items],
            )
            for speaker, items in by_speaker.items()
        ]

        quotes, claims = [], []
        for segment in guests:
            sentences = _sentences(segment.text)
            quotable = [s for s in sentences if _words(s) >= _MIN_QUOTE_WORDS]
            if quotable:
                quotes.append(
                    Quote(text=quotable[0].rstrip("."), speaker=segment.speaker, timecode_seconds=segment.start_seconds)
                )
            for sentence in sentences:
                if _NUMERIC.search(sentence):
                    claims.append(
                        Claim(
                            statement=sentence,
                            evidence_timecodes=[segment.start_seconds],
                            requires_verification=True,
                            note="Liczba lub kwota w wypowiedzi rozmówcy – sprawdzić w źródle",
                        )
                    )

        guest_words = sum(_words(s.text) for s in guests)
        mode = ArticleMode.FULL if guest_words >= _FULL_MODE_MIN_WORDS else ArticleMode.SHORT
        speakers = len(by_speaker)
        gaps = ["Tryb demonstracyjny: analiza regułowa, bez rozumienia treści."]
        if any(s.speaker is None for s in segments):
            gaps.append("Część segmentów nie ma przypisanego mówcy.")

        return EvidencePack(
            summary=(
                # Bez cudzysłowu: weryfikator traktuje tekst w „…” jako cytat.
                f"{material.title.rstrip('.?!:')}: {speakers} rozmówców, {len(segments)} segmentów, "
                f"około {guest_words} słów wypowiedzi."
            ),
            threads=threads,
            quotes=quotes,
            claims=claims,
            suggested_mode=mode,
            mode_rationale=f"{guest_words} słów wypowiedzi rozmówców (próg FULL: {_FULL_MODE_MIN_WORDS}).",
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
        limit = 3 if request.mode == ArticleMode.SHORT else 8
        quotes = evidence.quotes[:limit]
        topic = material.topic or material.title

        titles = [material.title, f"{topic}: co mówią rozmówcy"]
        if quotes:
            titles.append(f"„{quotes[0].text}”")

        def quote_paragraph(quote: Quote) -> str:
            who = quote.speaker or "Rozmówca"
            return f"{who} mówi: „{quote.text}”."

        if request.mode == ArticleMode.SHORT:
            sections = [Section(paragraphs=[quote_paragraph(q) for q in quotes] or [evidence.summary])]
        else:
            sections = []
            for thread in evidence.threads:
                thread_quotes = [q for q in quotes if q.speaker == thread.name]
                paragraphs = [quote_paragraph(q) for q in thread_quotes] or [thread.summary]
                sections.append(Section(heading=thread.name, paragraphs=paragraphs))

        return ArticleDraft(
            mode=request.mode,
            title_options=titles[:3],
            lead=evidence.summary,
            sections=sections,
            quotes=quotes,
            claims=evidence.claims,
            editor_notes=[_DEMO_NOTE, *evidence.gaps],
        )

    def repair_draft(self, request: DraftRequest, draft: ArticleDraft, problems: list[str]) -> ArticleDraft:
        # Bez modelu nie przeredagujemy tekstu; zostawiamy draft i opisujemy problemy redaktorowi.
        return draft.model_copy(update={"editor_notes": [*draft.editor_notes, *problems]})
