"""Nagranie → transkrypcja z oznaczeniami czasu.

1. `fetch_media` – plik lokalny albo link (YouTube i inne serwisy obsługiwane
   przez yt-dlp, bezpośredni link do pliku wideo/audio).
2. `WhisperTranscriber` – lokalne rozpoznawanie mowy (faster-whisper), więc
   nagranie nie opuszcza serwera; tylko tekst może trafić do modelu językowego.
3. `merge_asr_segments` – sklejenie krótkich odcinków ASR w zdania.

Zależności (yt-dlp, faster-whisper, ffmpeg) importujemy leniwie: aplikacja
działa bez nich, dopóki nikt nie uruchomi przetwarzania nagrania.
"""

import ipaddress
import os
import re
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from app.models import TranscriptSegment


class MediaError(RuntimeError):
    pass


@dataclass
class AsrSegment:
    start: float
    end: float
    text: str


@dataclass
class FetchedMedia:
    path: Path
    title: str | None = None
    # Link do odtwarzania w interfejsie (np. YouTube); None dla przesłanych plików.
    public_url: str | None = None


# Szum w nazwach plików: prefiks aparatu z numerami na początku, wersja lub kopia na końcu.
# Liczby w środku i na końcu nazwy zostają ("800-plus", "budzet-2025").
_CAMERA_PREFIX = re.compile(r"img|vid|mov|mvi|dsc[nf]?|pxl|gopr\d*|screen ?recording|nagranie ekranu", re.I)
_YEAR = re.compile(r"(?:19|20)\d\d")
_TRAILING_NOISE = re.compile(r"final|v\d+|copy|kopia|edit|\(\d+\)|\d{6,}", re.I)


def title_from_filename(name: str) -> str:
    """Tytuł roboczy z nazwy pliku: "IMG_0412_sonda-kaucja_FINAL.mp4" → "Sonda kaucja"."""
    stem = re.sub(r"\(\d+\)$", "", Path(name).stem)
    words = [w for w in re.split(r"[\s_\-.]+", stem) if w]
    for size in (2, 1):
        if len(words) >= size and _CAMERA_PREFIX.fullmatch(" ".join(words[:size])):
            del words[:size]
            # Po prefiksie aparatu zwykle data i godzina ("2025-01-15 at 14.30.22").
            while words and (words[0].isdigit() or words[0].lower() in ("at", "o")):
                words.pop(0)
            break
    # Data na początku nazwy ("2025-01-15 sonda"); sam rok przed treścią zostaje ("2025 budżet").
    if len(words) >= 3 and _YEAR.fullmatch(words[0]) and words[1].isdigit() and len(words[1]) <= 2:
        del words[:3 if words[2].isdigit() and len(words[2]) <= 2 else 2]
    while words and _TRAILING_NOISE.fullmatch(words[-1]):
        words.pop()
    if not words:
        return "Nagranie"
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def is_url(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


def ensure_public_host(url: str) -> None:
    """Blokuje linki do adresów wewnętrznych (localhost, sieci prywatne).

    Serwer pobiera nagranie w imieniu użytkownika, więc bez tej kontroli link
    mógłby posłużyć do odpytywania usług w sieci wewnętrznej. Przekierowania
    obsługiwane przez yt-dlp nie są tu sprawdzane – to ograniczenie prototypu.
    Wyłączenie (np. dla nagrań z wewnętrznego serwera): EDITORIAL_ALLOW_PRIVATE_URLS=1.
    """
    if os.environ.get("EDITORIAL_ALLOW_PRIVATE_URLS") == "1":
        return
    host = urlparse(url).hostname
    if not host:
        raise MediaError("Nieprawidłowy link")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as error:
        raise MediaError(f"Nie można odnaleźć serwera: {host}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise MediaError(f"Link wskazuje adres wewnętrzny ({host}) – pobieranie zablokowane")


def fetch_media(source: str, workdir: Path) -> FetchedMedia:
    workdir.mkdir(parents=True, exist_ok=True)
    if not is_url(source):
        path = Path(source)
        if not path.is_file():
            raise MediaError(f"Nie ma pliku: {source}")
        return FetchedMedia(path=path, title=title_from_filename(path.name))

    ensure_public_host(source)
    try:
        import yt_dlp
    except ImportError as error:  # pragma: no cover - zależność opcjonalna
        raise MediaError("Pobieranie z linku wymaga pakietu yt-dlp") from error

    options = {
        # Do transkrypcji wystarczy ścieżka dźwiękowa; mniejszy plik szybciej się pobiera.
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(source, download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception as error:  # yt-dlp zgłasza wiele typów wyjątków
        raise MediaError(f"Nie udało się pobrać nagrania: {error}") from error
    if not path.is_file():
        raise MediaError("Pobieranie zakończyło się bez pliku")
    return FetchedMedia(path=path, title=info.get("title"), public_url=source)


def to_wav(path: Path, workdir: Path) -> Path:
    """16 kHz mono – format, którego oczekuje model rozpoznawania mowy."""
    if shutil.which("ffmpeg") is None:
        raise MediaError("Brak programu ffmpeg")
    target = workdir / "audio.wav"
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MediaError(f"ffmpeg: {result.stderr.strip()[:300] or 'nieznany błąd'}")
    return target


def read_wav(path: Path):
    """Próbki float32 z pliku WAV 16 kHz mono przygotowanego przez `to_wav`.

    Przekazujemy tablicę zamiast ścieżki, bo dekodowanie pliku w faster-whisper
    zależy od wersji biblioteki `av`, a ffmpeg i tak już przygotował dźwięk.
    """
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as reader:
        if reader.getsampwidth() != 2 or reader.getnchannels() != 1:
            raise MediaError("Oczekiwano 16-bitowego dźwięku mono")
        data = reader.readframes(reader.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


class Transcriber(Protocol):
    def transcribe(self, audio: Path) -> list[AsrSegment]: ...


class WhisperTranscriber:
    """faster-whisper na CPU. Model wybierany zmienną EDITORIAL_ASR_MODEL (domyślnie `medium`).

    Pomiar na 4 rdzeniach (nagranie 4,4 min): `small` 1,5 min, `medium` 3,8 min.
    `medium` popełnia wyraźnie mniej błędów w liczbach i odmianie, a w procesie
    bez człowieka każdy błąd ASR może trafić do cytatu – stąd taki domyślny wybór.
    """

    _models: dict[str, object] = {}
    _lock = threading.Lock()

    def __init__(self, model_size: str | None = None, language: str = "pl"):
        self.model_size = model_size or os.environ.get("EDITORIAL_ASR_MODEL", "medium")
        self.language = language

    def _model(self):
        with self._lock:
            if self.model_size not in self._models:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as error:  # pragma: no cover - zależność opcjonalna
                    raise MediaError("Transkrypcja wymaga pakietu faster-whisper") from error
                self._models[self.model_size] = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            return self._models[self.model_size]

    def transcribe(self, audio: Path) -> list[AsrSegment]:
        segments, _info = self._model().transcribe(
            read_wav(audio),
            language=self.language,
            # VAD pomija ciszę i muzykę, co ogranicza "halucynacje" ASR na pustych fragmentach.
            vad_filter=True,
            beam_size=5,
        )
        return [AsrSegment(start=s.start, end=s.end, text=s.text.strip()) for s in segments if s.text.strip()]


_SENTENCE_END = (".", "!", "?", "…")
# Przerwa dłuższa niż ta zwykle oznacza zmianę mówcy albo nową myśl.
_MAX_MERGE_GAP = 1.0
_MAX_MERGED_WORDS = 60


def merge_asr_segments(segments: list[AsrSegment]) -> list[TranscriptSegment]:
    """Skleja odcinki ASR urwane w środku zdania; czas segmentu to początek pierwszego odcinka."""
    merged: list[TranscriptSegment] = []
    last_end = None
    for segment in segments:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and not previous.text.endswith(_SENTENCE_END)
            and last_end is not None
            and segment.start - last_end <= _MAX_MERGE_GAP
            and len(previous.text.split()) + len(segment.text.split()) <= _MAX_MERGED_WORDS
        ):
            previous.text = f"{previous.text} {segment.text}"
        else:
            start = int(segment.start)
            # Parser wymaga niemalejących znaczników czasu.
            if merged and start < merged[-1].start_seconds:
                start = merged[-1].start_seconds
            merged.append(TranscriptSegment(start_seconds=start, text=segment.text))
        last_end = segment.end
    return merged
