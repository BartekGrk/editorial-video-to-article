import os

from app.engines.base import DraftRequest, Engine, EngineError, MaterialInput
from app.engines.heuristic import HeuristicEngine

__all__ = ["DraftRequest", "Engine", "EngineError", "MaterialInput", "HeuristicEngine", "create_engine"]


def create_engine() -> Engine:
    """Wybór silnika: EDITORIAL_ENGINE=anthropic|heuristic; domyślnie Claude, jeśli jest klucz API."""
    choice = os.environ.get("EDITORIAL_ENGINE")
    if choice is None:
        choice = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "heuristic"
    if choice == "anthropic":
        # Import leniwy: tryb demonstracyjny nie wymaga zainstalowanego SDK.
        from app.engines.anthropic_engine import AnthropicEngine

        return AnthropicEngine()
    if choice == "heuristic":
        return HeuristicEngine()
    raise ValueError(f"Nieznany silnik: {choice}")
