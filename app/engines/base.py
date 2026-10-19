"""Wspólny interfejs silników generujących materiał dowodowy, profil stylu i draft.

Dwie implementacje:
- `AnthropicEngine` – model Claude przez structured output (docelowa jakość);
- `HeuristicEngine` – reguły bez AI: tryb demonstracyjny i testy offline.

Pipeline (`app/pipeline.py`) nie zależy od tego, który silnik działa,
a weryfikacja cytatów zawsze jest deterministyczna i po stronie aplikacji.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.models import ArticleDraft, ArticleMode, EditorialStyleProfile, EvidencePack, SpeakerLabels, Transcript


class EngineError(RuntimeError):
    pass


@dataclass
class MaterialInput:
    title: str
    transcript: Transcript
    category: str = ""
    topic: str = ""
    angle: str = ""


@dataclass
class DraftRequest:
    material: MaterialInput
    evidence: EvidencePack
    mode: ArticleMode
    style: EditorialStyleProfile | None = None
    target_words: int | None = None
    extra_instructions: str = ""
    problems: list[str] = field(default_factory=list)


class Engine(Protocol):
    name: str
    # Czy silnik wysyła treść do zewnętrznego API (wymaga zgody na przetwarzanie).
    external: bool

    def label_speakers(self, material: MaterialInput) -> SpeakerLabels | None:
        """Mówcy dla transkrypcji bez etykiet; None, gdy silnik tego nie potrafi."""
        ...

    def extract_evidence(self, material: MaterialInput) -> EvidencePack: ...

    def build_style_profile(self, articles: list[str]) -> EditorialStyleProfile: ...

    def write_draft(self, request: DraftRequest) -> ArticleDraft: ...

    def repair_draft(self, request: DraftRequest, draft: ArticleDraft, problems: list[str]) -> ArticleDraft: ...
