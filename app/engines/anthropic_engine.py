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
from app.models import ArticleDraft, EditorialStyleProfile, EvidencePack, SpeakerLabels

DEFAULT_MODEL = "claude-opus-5-5"
# Serwerowy fallback przy odmowie klasyfikatora bezpieczeństwa: zamiast pustej
# odpowiedzi API samo ponawia żądanie na innym modelu.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)


class AnthropicEngine:
    name = "anthropic"
    external = True

    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None):
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("EDITORIAL_MODEL", DEFAULT_MODEL)

    def _call(self, system: str, prompt: str, output: type[T], effort: str) -> T:
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
        except ValidationError as error:
            raise EngineError(f"Odpowiedź modelu nie pasuje do schematu: {error}") from error

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
        draft = self._call(prompts.DRAFT_SYSTEM, prompts.draft_prompt(request), ArticleDraft, "high")
        # Tryb wybiera redaktor; nie pozwalamy modelowi go zmienić.
        return draft.model_copy(update={"mode": request.mode})

    def repair_draft(self, request: DraftRequest, draft: ArticleDraft, problems: list[str]) -> ArticleDraft:
        repaired = self._call(
            prompts.REPAIR_SYSTEM, prompts.repair_prompt(request, draft, problems), ArticleDraft, "high"
        )
        return repaired.model_copy(update={"mode": request.mode})
