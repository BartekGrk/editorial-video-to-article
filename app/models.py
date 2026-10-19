"""Kontrakty danych prototypu (Faza 1).

Kolejność modeli odpowiada pipeline'owi ze specyfikacji:
transkrypcja → materiał dowodowy (cytaty, tezy) → draft SHORT/FULL.
Draft nie przechowuje "wolnego" tekstu cytatów: każdy cytat ma mówcę
i timecode, żeby dało się go sprawdzić względem transkrypcji.
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
    text: str
    speaker: str | None = None
    timecode_seconds: int = Field(ge=0)


class Claim(BaseModel):
    """Teza/fakt z materiału wraz ze wskazaniem fragmentów transkrypcji."""

    statement: str
    evidence_timecodes: list[int] = Field(default_factory=list)
    # Relacja rozmówcy to nie potwierdzony fakt: kwoty, daty i twierdzenia
    # wymagające zewnętrznego źródła oznaczamy do weryfikacji przez redaktora.
    requires_verification: bool = False
    note: str | None = None


class Section(BaseModel):
    heading: str | None = None
    paragraphs: list[str]


class ArticleDraft(BaseModel):
    mode: ArticleMode
    title_options: list[str] = Field(min_length=2, max_length=3)
    lead: str
    sections: list[Section]
    quotes: list[Quote] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    editor_notes: list[str] = Field(default_factory=list)
