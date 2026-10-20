"""Zadanie automatu zapisujące postęp w bazie (używane przez API).

Kolejne etapy aktualizują `job_stage` materiału, więc interfejs może pokazywać
postęp, a błąd w dowolnym kroku kończy się statusem FAILED z opisem przyczyny
zamiast zawieszonego zadania.
"""

import logging
import shutil
from pathlib import Path

from app import auto
from app.engines.base import Engine, MaterialInput
from app.format_check import approval_blockers
from app.media import Transcriber
from app.store import Material, MaterialStatus, Store
from app.transcript import parse_transcript

log = logging.getLogger(__name__)


class MaterialDeleted(Exception):
    """Materiał usunięty w trakcie przetwarzania (osobny typ, żeby nie łapać innych KeyError/IndexError)."""


def _update(store: Store, material_id: str, **fields) -> Material:
    updated = store.update(Material, material_id, **fields)
    if updated is None:
        raise MaterialDeleted(material_id)
    return updated


def run_auto_job(
    store: Store,
    engine: Engine,
    transcriber: Transcriber | None,
    material_id: str,
    options: auto.AutoOptions,
    auto_approve: bool,
    media_dir: Path,
    source: str | None = None,
) -> None:
    """Pełny automat dla materiału; `source` (plik/link) uruchamia też transkrypcję."""

    def stage(value: auto.Stage) -> None:
        _update(store, material_id, job_stage=value.value)

    try:
        if source is not None:
            if transcriber is None:
                raise RuntimeError("Transkrypcja nagrań jest niedostępna")
            workdir = media_dir / material_id
            transcription = auto.transcribe_and_label(engine, transcriber, source, options.title, workdir, stage)
            media = transcription.media
            # Plik WAV dla ASR jest pośredni; zostawiamy tylko oryginał do odtwarzania.
            (workdir / "audio.wav").unlink(missing_ok=True)
            material = store.get(Material, material_id)
            fields = {
                "transcript_raw": transcription.transcript_raw,
                "transcript_has_speakers": transcription.has_speakers,
                "transcript_notes": transcription.notes,
                "video_url": media.public_url or "",
                "media_file": str(media.path.relative_to(media_dir)) if media.path.is_relative_to(media_dir) else None,
            }
            # Tytuł z serwisu (np. YouTube) jest lepszy niż "Nagranie z linku"; nazwa pliku roboczego – nie.
            if material is not None and material.title_auto and media.public_url and media.title:
                fields["title"] = media.title
            _update(store, material_id, **fields)

        material = store.get(Material, material_id)
        if material is None:
            raise MaterialDeleted(material_id)
        material_input = MaterialInput(
            title=material.title,
            transcript=parse_transcript(material.transcript_raw, with_speakers=material.transcript_has_speakers),
            category=material.category,
            topic=material.topic,
            angle=material.angle,
        )
        evidence, mode, result = auto.write_from_transcript(
            engine, material_input, options, material.transcript_notes, stage
        )

        # Automat zatwierdza tylko draft bez żadnych wątpliwości co do cytatów i ich mówców
        # i z treścią nadającą się do publikacji.
        draft = result.draft
        blockers = approval_blockers(draft)
        approved = auto_approve and result.report.strict_ok and not blockers
        if auto_approve and blockers:
            draft = draft.model_copy(
                update={"editor_notes": [*draft.editor_notes, *(f"Automat nie zatwierdził: {b}" for b in blockers)]}
            )
        fields = {
            "evidence": evidence,
            "mode": mode,
            "draft": draft,
            "report": result.report,
            "status": MaterialStatus.APPROVED if approved else MaterialStatus.DRAFTED,
            "job_stage": auto.Stage.DONE.value,
            "job_error": None,
        }
        if material.title_auto:
            fields["title"] = result.draft.title_options[0]
            fields["title_auto"] = False
        _update(store, material_id, **fields)
    except MaterialDeleted:
        log.info("Materiał %s usunięty w trakcie przetwarzania", material_id)
        # Usunięcie mogło nastąpić przed zapisem pobranego nagrania – sprzątamy po zadaniu.
        shutil.rmtree(media_dir / material_id, ignore_errors=True)
    except Exception as error:  # zadanie w tle: każdy błąd musi trafić do materiału
        log.exception("Automat dla %s zakończył się błędem", material_id)
        try:
            _update(
                store,
                material_id,
                status=MaterialStatus.FAILED,
                # job_stage zostaje na etapie, który się nie powiódł – UI pokazuje, gdzie był błąd.
                job_error=str(error) or error.__class__.__name__,
            )
        except MaterialDeleted:
            pass
