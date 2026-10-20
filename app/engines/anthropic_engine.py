"""Silnik oparty na Claude (Anthropic API).

Każdy krok to pojedyncze wywołanie ze structured output: odpowiedź modelu
jest walidowana do modelu Pydantic, więc pipeline dostaje typowane dane
zamiast tekstu do parsowania. Bez agentów i narzędzi – kolejność kroków
kontroluje kod aplikacji.
"""

import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.engines import prompts
from app.engines.base import DraftRequest, EngineError, MaterialInput
from app.models import ArticleDraft, ArticleMode, Claim, EditorialStyleProfile, EvidencePack, Quote, Section, SpeakerLabels

DEFAULT_MODEL = "claude-opus-5-5"
# Serwerowy fallback przy odmowie klasyfikatora bezpieczeństwa: zamiast pustej
# odpowiedzi API samo ponawia żądanie na innym modelu.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)
# Odpowiedź niezgodna ze schematem zdarza się rzadko i zwykle nie powtarza się przy ponowieniu.
_VALIDATION_ATTEMPTS = 2


class DraftOutput(BaseModel):
    """Schemat odpowiedzi modelu dla draftu – bez limitu liczby tytułów.

    API nie egzekwuje minItems/maxItems, więc przy ścisłym modelu `ArticleDraft`
    jeden nadmiarowy tytuł kończyłby cały automat błędem; liczbę tytułów
    normalizujemy po stronie aplikacji.
    """

    title_options: list[str]
    lead: str
    sections: list[Section]
    quotes: list[Quote]
    claims: list[Claim]
    editor_notes: list[str]

    def to_draft(self, mode: ArticleMode, fallback_title: str) -> ArticleDraft:
        titles = list(dict.fromkeys(t.strip() for t in self.title_options if t.strip()))[:3]
        # Tytuł roboczy tylko jako uzupełnienie – w automacie bywa nazwą pliku.
        for extra in (fallback_title, f"{fallback_title} – wideo"):
            if len(titles) < 2 and extra not in titles:
                titles.append(extra)
        return ArticleDraft(
            mode=mode,
            title_options=titles[:3],
            lead=self.lead,
            sections=[s for s in self.sections if s.paragraphs],
            quotes=self.quotes,
            claims=self.claims,
            editor_notes=self.editor_notes,
        )


class AnthropicEngine:
    name = "anthropic"
    external = True

    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None):
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("EDITORIAL_MODEL", DEFAULT_MODEL)

    def _call(self, system: str, prompt: str, output: type[T], effort: str) -> T:
        for attempt in range(1, _VALIDATION_ATTEMPTS + 1):
            try:
                return self._call_once(system, prompt, output, effort)
            except ValidationError as error:
                if attempt == _VALIDATION_ATTEMPTS:
                    raise EngineError(f"Odpowiedź modelu nie pasuje do schematu: {error}") from error
        raise AssertionError("unreachable")

    def _call_once(self, system: str, prompt: str, output: type[T], effort: str) -> T:
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=output,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                # Transkrypcja wraca w kilku krokach (analiza, draft, poprawka),
                # więc cache'ujemy prefiks żądania.
                cache_control={"type": "ephemeral"},
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as error:
            raise EngineError("Niepoprawny klucz API Anthropic") from error
        except anthropic.RateLimitError as error:
            raise EngineError("Przekroczony limit zapytań do API – spróbuj za chwilę") from error
        except anthropic.APIStatusError as error:
            raise EngineError(f"Błąd API ({error.status_code}): {error.message}") from error
        except anthropic.APIConnectionError as error:
            raise EngineError("Brak połączenia z API Anthropic") from error

        if response.stop_reason == "refusal":
            raise EngineError("Model odmówił przetworzenia materiału")
        if response.stop_reason == "max_tokens":
            raise EngineError("Odpowiedź modelu została ucięta (limit tokenów)")
        if response.parsed_output is None:
            raise EngineError("Model nie zwrócił danych w oczekiwanym formacie")
        return response.parsed_output

    def label_speakers(self, material: MaterialInput) -> SpeakerLabels:
        return self._call(prompts.SPEAKERS_SYSTEM, prompts.speakers_prompt(material), SpeakerLabels, "low")

    def extract_evidence(self, material: MaterialInput) -> EvidencePack:
        return self._call(prompts.EVIDENCE_SYSTEM, prompts.evidence_prompt(material), EvidencePack, "medium")

    def build_style_profile(self, articles: list[str]) -> EditorialStyleProfile:
        return self._call(prompts.STYLE_SYSTEM, prompts.style_prompt(articles), EditorialStyleProfile, "medium")

    def write_draft(self, request: DraftRequest) -> ArticleDraft:
        output = self._call(prompts.DRAFT_SYSTEM, prompts.draft_prompt(request), DraftOutput, "high")
        # Tryb wybiera redaktor (albo automat); model nie może go zmienić. Znaczniki cytatów
        # rozwija pipeline – potrzebna jest do tego transkrypcja.
        return output.to_draft(request.mode, request.material.title)

    def repair_draft(self, request: DraftRequest, draft: ArticleDraft, problems: list[str]) -> ArticleDraft:
        output = self._call(prompts.REPAIR_SYSTEM, prompts.repair_prompt(request, draft, problems), DraftOutput, "high")
        return output.to_draft(request.mode, request.material.title)
