"""Kontrakty danych aplikacji.

Kolejność modeli odpowiada pipeline'owi ze specyfikacji:
transkrypcja → materiał dowodowy (cytaty, tezy) → profil stylu → draft SHORT/FULL.
Draft nie przechowuje "wolnego" tekstu cytatów: każdy cytat ma mówcę
i timecode, żeby dało się go sprawdzić względem transkrypcji.

Modele z opisami pól (`description`) trafiają do JSON Schema wysyłanej do
modelu językowego, więc opisy są jednocześnie instrukcją dla LLM.
"""

from enum import Enum

from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    start_seconds: int = Field(ge=0)
    speaker: str | None = None
    text: str


class Transcript(BaseModel):
    segments: list[TranscriptSegment]


class ArticleMode(str, Enum):
    # SHORT: tekst towarzyszący wideo; FULL: pełnoprawny artykuł.
    # Wybór należy do redaktora, nie zakładamy, że każdy materiał nadaje się na FULL.
    SHORT = "SHORT"
    FULL = "FULL"


class Quote(BaseModel):
    text: str = Field(description="Dosłowny fragment wypowiedzi skopiowany z transkrypcji, bez poprawek")
    speaker: str | None = Field(default=None, description="Mówca dokładnie jak w transkrypcji")
    timecode_seconds: int = Field(ge=0, description="Początek segmentu transkrypcji w sekundach")


class Claim(BaseModel):
    """Teza/fakt z materiału wraz ze wskazaniem fragmentów transkrypcji."""

    statement: str
    evidence_timecodes: list[int] = Field(
        default_factory=list, description="Sekundy początków segmentów, które potwierdzają tezę"
    )
    # Relacja rozmówcy to nie potwierdzony fakt: kwoty, daty i twierdzenia
    # wymagające zewnętrznego źródła oznaczamy do weryfikacji przez redaktora.
    requires_verification: bool = Field(
        default=False,
        description="true dla liczb, dat, nazwisk i twierdzeń, które wymagają sprawdzenia poza nagraniem",
    )
    note: str | None = None


class Section(BaseModel):
    heading: str | None = Field(default=None, description="Śródtytuł; null dla sekcji bez śródtytułu")
    paragraphs: list[str]


class ArticleDraft(BaseModel):
    mode: ArticleMode
    title_options: list[str] = Field(min_length=2, max_length=3, description="2–3 propozycje tytułu")
    lead: str
    sections: list[Section]
    quotes: list[Quote] = Field(
        default_factory=list, description="Każdy cytat użyty w tytule, leadzie lub treści"
    )
    claims: list[Claim] = Field(default_factory=list)
    editor_notes: list[str] = Field(
        default_factory=list, description="Luki, niepewności i miejsca potencjalnego błędu"
    )


class Thread(BaseModel):
    name: str
    summary: str
    timecodes: list[int] = Field(default_factory=list)


class EvidencePack(BaseModel):
    """Uporządkowany materiał dowodowy: wspólna warstwa dla trybów SHORT i FULL."""

    summary: str = Field(description="2–3 zdania: o czym jest materiał")
    threads: list[Thread] = Field(description="Główne wątki w kolejności ważności")
    quotes: list[Quote] = Field(description="Najmocniejsze dosłowne wypowiedzi kandydujące do cytowania")
    claims: list[Claim] = Field(description="Tezy i fakty padające w materiale")
    suggested_mode: ArticleMode = Field(description="SHORT, jeśli materiał nie uzasadnia pełnego artykułu")
    mode_rationale: str
    gaps: list[str] = Field(
        default_factory=list,
        description="Czego brakuje w transkrypcji (np. kontekst z obrazu, niejasni mówcy, możliwe błędy ASR)",
    )


class EditorialStyleProfile(BaseModel):
    """Jawny opis formatu na podstawie przykładowych artykułów (nie kopia ich treści)."""

    title_style: str = Field(description="Długość i konstrukcja tytułów, np. pytanie, cytat w tytule")
    lead_style: str = Field(description="Długość leadu i tempo wprowadzania meritum")
    paragraph_style: str = Field(description="Typowa długość akapitów i zdań")
    subheading_style: str = Field(description="Jak często i jakie śródtytuły")
    quote_style: str = Field(description="Zasady cytowania i przedstawiania rozmówców")
    tone: str = Field(description="Ton: neutralny, dynamiczny itp.")
    context_style: str = Field(description="Sposób podawania kontekstu i tła")
    multi_speaker_style: str = Field(description="Łączenie wypowiedzi wielu osób, zaznaczanie ich statusu")
    short_mode_notes: str = Field(description="Cechy krótkich tekstów towarzyszących wideo")
    full_mode_notes: str = Field(description="Cechy pełnych artykułów")
    typical_title_words: int = Field(ge=1)
    typical_lead_words: int = Field(ge=1)
