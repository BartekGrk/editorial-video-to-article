"""Zapis materiałów i profili stylu w SQLite.

Rekord to dokument JSON (model Pydantic) w jednej kolumnie: przy prototypie
schemat danych zmienia się często, a zapytania ograniczają się do listy i
odczytu po id. Plik bazy domyślnie w `data/` (poza repozytorium).
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field

from app.models import ArticleDraft, ArticleMode, EditorialStyleProfile, EvidencePack
from app.verification import VerificationReport


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class MaterialStatus(str, Enum):
    PROCESSING = "PROCESSING"
    FAILED = "FAILED"
    NEW = "NEW"
    ANALYZED = "ANALYZED"
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"


class AutoSettings(BaseModel):
    """Ustawienia ostatniego uruchomienia automatu – ponowienie po błędzie ich używa."""

    mode: ArticleMode | None = None
    style_profile_id: str | None = None
    target_words: int | None = Field(default=None, ge=30, le=3000)
    auto_approve: bool = True


class Material(BaseModel):
    id: str = Field(default_factory=_new_id)
    title: str
    category: str = ""
    topic: str = ""
    angle: str = ""
    video_url: str = ""
    # Pusta, dopóki automat nie skończy transkrypcji nagrania.
    transcript_raw: str = ""
    # False dla transkrypcji ASR bez mówców: wtedy "Słowo: tekst" nie jest etykietą mówcy.
    transcript_has_speakers: bool = True
    source: str = ""
    media_file: str | None = None
    job_stage: str | None = None
    job_error: str | None = None
    # Tytuł nadany automatycznie (z nazwy pliku/nagrania) zastępujemy pierwszą propozycją z draftu.
    title_auto: bool = False
    # Zastrzeżenia z transkrypcji automatycznej (np. mówcy przypisani przez model) – trafiają
    # do luk materiału dowodowego także przy ponownym generowaniu.
    transcript_notes: list[str] = []
    auto_settings: AutoSettings | None = None
    # Zgoda na wysłanie treści do zewnętrznego API: materiał publiczny,
    # syntetyczny albo dopuszczony polityką redakcji.
    external_processing_allowed: bool = False
    style_profile_id: str | None = None
    status: MaterialStatus = MaterialStatus.NEW
    evidence: EvidencePack | None = None
    mode: ArticleMode | None = None
    draft: ArticleDraft | None = None
    report: VerificationReport | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    # Licznik zapisów – wersja dla zatwierdzania (updated_at ma dokładność sekundy).
    revision: int = 0


class StyleProfileRecord(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    description: str = ""
    profile: EditorialStyleProfile
    source_count: int = 0
    created_at: str = Field(default_factory=_now)


T = TypeVar("T", bound=BaseModel)

_TABLES = {Material: "materials", StyleProfileRecord: "style_profiles"}


class ConditionFailed(Exception):
    """Warunek atomowej aktualizacji niespełniony (stan zmienił się w międzyczasie)."""


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI wykonuje endpointy synchroniczne w puli wątków; jedno połączenie + blokada wystarcza.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            for table in _TABLES.values():
                self._conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )

    def save(self, record: BaseModel) -> None:
        if isinstance(record, Material):
            record.updated_at = _now()
            with self._lock:
                row = self._conn.execute("SELECT data FROM materials WHERE id = ?", (record.id,)).fetchone()
            record.revision = (Material.model_validate_json(row[0]).revision + 1) if row else max(record.revision, 1)
        table = _TABLES[type(record)]
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT OR REPLACE INTO {table} (id, data, updated_at) VALUES (?, ?, ?)",
                (record.id, record.model_dump_json(), _now()),
            )

    def get(self, model: type[T], record_id: str) -> T | None:
        with self._lock:
            row = self._conn.execute(f"SELECT data FROM {_TABLES[model]} WHERE id = ?", (record_id,)).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def list(self, model: type[T]) -> list[T]:
        with self._lock:
            rows = self._conn.execute(f"SELECT data FROM {_TABLES[model]} ORDER BY updated_at DESC").fetchall()
        return [model.model_validate_json(r[0]) for r in rows]

    def update(self, model: type[T], record_id: str, condition=None, **fields) -> T | None:
        """Atomowy odczyt i zapis zmienionych pól; None, gdy rekordu już nie ma.

        Osobne get + save pozwalałyby zadaniu w tle "wskrzesić" rekord usunięty
        przez redaktora między odczytem a zapisem. `condition(aktualny)` sprawdzany
        w tej samej transakcji – gdy zwróci False, rzucamy `ConditionFailed`.
        """
        table = _TABLES[model]
        with self._lock, self._conn:
            row = self._conn.execute(f"SELECT data FROM {table} WHERE id = ?", (record_id,)).fetchone()
            if row is None:
                return None
            current = model.model_validate_json(row[0])
            if condition is not None and not condition(current):
                raise ConditionFailed(record_id)
            record = current.model_copy(update=fields)
            if isinstance(record, Material):
                record.updated_at = _now()
                record.revision = current.revision + 1
            self._conn.execute(
                f"UPDATE {table} SET data = ?, updated_at = ? WHERE id = ?",
                (record.model_dump_json(), _now(), record_id),
            )
        return record

    def delete(self, model: type[BaseModel], record_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(f"DELETE FROM {_TABLES[model]} WHERE id = ?", (record_id,))
        return cursor.rowcount > 0


# Wbudowane profile stylu (opis formatu, bez treści artykułów źródłowych).
STYLES_DIR = Path(__file__).parent / "styles"
DEFAULT_STYLE_ID = "wp-finanse"


def seed_builtin_styles(store: Store) -> None:
    """Dodaje wbudowane profile, jeśli ich nie ma; nie nadpisuje zmian redaktora."""
    for path in sorted(STYLES_DIR.glob("*.json")):
        record_id = path.stem.replace("_", "-")
        if store.get(StyleProfileRecord, record_id) is None:
            data = json.loads(path.read_text(encoding="utf-8"))
            store.save(StyleProfileRecord(id=record_id, source_count=0, **data))
