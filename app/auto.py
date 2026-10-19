"""Proces automatyczny od nagrania do draftu gotowego do eksportu.

nagranie/link → pobranie → transkrypcja ASR → przypisanie mówców → materiał
dowodowy → wybór trybu (sugestia modelu, chyba że wskazano inny) → draft
w profilu stylu → weryfikacja i poprawka.

Moduł nie zależy od bazy ani API: korzystają z niego zarówno zadania w tle
aplikacji (`app/jobs.py`), jak i skrypt `scripts/auto.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app import pipeline
from app.engines.base import DraftRequest, Engine, MaterialInput
from app.media import FetchedMedia, MediaError, Transcriber, fetch_media, merge_asr_segments, to_wav
from app.models import ArticleMode, EditorialStyleProfile, EvidencePack, Transcript, TranscriptSegment
from app.transcript import build_transcript_raw, format_timecode, parse_transcript


class Stage(str, Enum):
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    TRANSCRIBING = "TRANSCRIBING"
    SPEAKERS = "SPEAKERS"
    ANALYZING = "ANALYZING"
    WRITING = "WRITING"
    DONE = "DONE"
    FAILED = "FAILED"


StageCallback = Callable[[Stage], None]

# Etykieta musi przejść przez parser `Mówca: treść`, więc nie może zawierać dwukropka.
_MAX_SPEAKER_LENGTH = 60
UNKNOWN_SPEAKER = "Mówca nieustalony"
# Poniżej tego pokrycia etykiety są zbyt dziurawe, żeby na nich polegać.
_MIN_LABEL_COVERAGE = 0.8


@dataclass
class AutoOptions:
    title: str = ""
    category: str = ""
    topic: str = ""
    angle: str = ""
    mode: ArticleMode | None = None
    style: EditorialStyleProfile | None = None
    target_words: int | None = None
    extra_instructions: str = ""


@dataclass
class TranscriptionResult:
    media: FetchedMedia
    transcript_raw: str
    has_speakers: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class AutoResult:
    title: str
    transcription: TranscriptionResult | None
    evidence: EvidencePack
    mode: ArticleMode
    draft_result: pipeline.DraftResult


def _noop(_stage: Stage) -> None:
    pass


def transcribe(source: str, workdir: Path, transcriber: Transcriber, on_stage: StageCallback = _noop) -> tuple[FetchedMedia, list[TranscriptSegment]]:
    on_stage(Stage.DOWNLOADING)
    media = fetch_media(source, workdir)
    on_stage(Stage.TRANSCRIBING)
    segments = merge_asr_segments(transcriber.transcribe(to_wav(media.path, workdir)))
    if not segments:
        raise MediaError("W nagraniu nie rozpoznano mowy")
    return media, segments


def label_speakers(engine: Engine, title: str, segments: list[TranscriptSegment]) -> tuple[list[TranscriptSegment], bool, list[str]]:
    """Przypisuje mówców przez silnik; zwraca segmenty, czy są mówcy, i uwagi dla redaktora."""
    material = MaterialInput(title=title, transcript=Transcript(segments=segments))
    labels = engine.label_speakers(material)
    if labels is None:
        return segments, False, ["Transkrypcja automatyczna bez rozróżnienia mówców – przypisz wypowiedzi ręcznie."]

    by_index = {}
    for label in labels.labels:
        name = " ".join(label.speaker.replace(":", " ").split())[:_MAX_SPEAKER_LENGTH]
        if 0 <= label.index < len(segments) and name:
            by_index[label.index] = name
    if len(by_index) < _MIN_LABEL_COVERAGE * len(segments):
        return segments, False, ["Model nie przypisał mówców do większości wypowiedzi – sprawdź je ręcznie."]

    # Każdy segment musi mieć etykietę: w transkrypcji z mówcami linia bez etykiety
    # z dwukropkiem w treści ("Pytanie brzmi: …") zostałaby odczytana jako mówca.
    labelled = [s.model_copy(update={"speaker": by_index.get(i, UNKNOWN_SPEAKER)}) for i, s in enumerate(segments)]
    notes = ["Mówcy przypisani automatycznie na podstawie treści – sprawdź nazwiska i funkcje z nagraniem."]
    uncertain = sorted({i for i in labels.uncertain_indices if 0 <= i < len(segments)})
    if uncertain:
        times = ", ".join(format_timecode(segments[i].start_seconds) for i in uncertain[:10])
        notes.append(f"Niepewne przypisanie mówcy: {times}")
    return labelled, True, notes


def transcribe_and_label(
    engine: Engine, transcriber: Transcriber, source: str, title: str, workdir: Path, on_stage: StageCallback = _noop
) -> TranscriptionResult:
    media, segments = transcribe(source, workdir, transcriber, on_stage)
    on_stage(Stage.SPEAKERS)
    labelled, has_speakers, notes = label_speakers(engine, title or media.title or "Nagranie", segments)
    return TranscriptionResult(
        media=media, transcript_raw=build_transcript_raw(labelled), has_speakers=has_speakers, notes=notes
    )


def write_from_transcript(
    engine: Engine,
    material: MaterialInput,
    options: AutoOptions,
    extra_gaps: list[str] | None = None,
    on_stage: StageCallback = _noop,
) -> tuple[EvidencePack, ArticleMode, pipeline.DraftResult]:
    on_stage(Stage.ANALYZING)
    evidence = pipeline.analyze(engine, material)
    if extra_gaps:
        evidence = evidence.model_copy(update={"gaps": [*extra_gaps, *evidence.gaps]})
    mode = options.mode or evidence.suggested_mode

    on_stage(Stage.WRITING)
    request = DraftRequest(
        material=material,
        evidence=evidence,
        mode=mode,
        style=options.style,
        target_words=options.target_words,
        extra_instructions=options.extra_instructions,
    )
    return evidence, mode, pipeline.write(engine, request)


def run(
    engine: Engine,
    transcriber: Transcriber,
    source: str,
    options: AutoOptions,
    workdir: Path,
    on_stage: StageCallback = _noop,
) -> AutoResult:
    transcription = transcribe_and_label(engine, transcriber, source, options.title, workdir, on_stage)
    title = options.title or transcription.media.title or "Nagranie"
    material = MaterialInput(
        title=title,
        transcript=parse_transcript(transcription.transcript_raw, with_speakers=transcription.has_speakers),
        category=options.category,
        topic=options.topic,
        angle=options.angle,
    )
    evidence, mode, result = write_from_transcript(engine, material, options, transcription.notes, on_stage)
    on_stage(Stage.DONE)
    return AutoResult(title=title, transcription=transcription, evidence=evidence, mode=mode, draft_result=result)
