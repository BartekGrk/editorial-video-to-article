"""Automat od nagrania do draftu: ASR podmieniony atrapą, reszta procesu prawdziwa."""

import io
import shutil
import wave

import pytest
from fastapi.testclient import TestClient

from app import auto
from app.engines import HeuristicEngine
from app.jobs import InlineRunner
from app.main import create_app
from app.media import AsrSegment, merge_asr_segments
from app.models import SpeakerLabel, SpeakerLabels, TranscriptSegment
from app.store import Material, MaterialStatus, Store

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="wymaga ffmpeg")

ASR = [
    AsrSegment(0.0, 3.0, "Pytamy przechodniów, czy oddają butelki."),
    AsrSegment(3.2, 6.0, "Oddaję, bo automat jest w sklepie"),
    AsrSegment(6.1, 8.0, "obok domu."),
    AsrSegment(12.0, 16.0, "Ja nie oddaję. Za te trzydzieści groszy nie będę stać w kolejce."),
    AsrSegment(17.0, 21.0, "Słyszałam, że w zeszłym miesiącu oddano miliard butelek."),
]


class FakeTranscriber:
    def __init__(self, segments=ASR, error=None):
        self.segments, self.error = segments, error

    def transcribe(self, audio):
        if self.error:
            raise self.error
        return self.segments


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


@pytest.fixture
def auto_client(tmp_path):
    def make(transcriber=None, engine=None):
        app = create_app(
            Store(":memory:"), engine or HeuristicEngine(), transcriber or FakeTranscriber(), InlineRunner(), tmp_path
        )
        return TestClient(app)

    return make


def test_merge_asr_segments_joins_broken_sentences():
    merged = merge_asr_segments(ASR)
    assert [s.text for s in merged][1] == "Oddaję, bo automat jest w sklepie obok domu."
    assert [s.start_seconds for s in merged] == [0, 3, 12, 17]


def test_merge_keeps_timecodes_non_decreasing():
    merged = merge_asr_segments([AsrSegment(5.9, 6.0, "A."), AsrSegment(5.2, 7.0, "B.")])
    assert [s.start_seconds for s in merged] == [5, 5]


class LabellingEngine(HeuristicEngine):
    def __init__(self, labels):
        self.labels = labels

    def label_speakers(self, material):
        return self.labels


def _segments():
    return [TranscriptSegment(start_seconds=i * 5, text=f"Zdanie numer {i}.") for i in range(4)]


def test_label_speakers_applies_and_sanitizes_labels():
    labels = SpeakerLabels(
        labels=[SpeakerLabel(index=i, speaker=n) for i, n in enumerate(["Reporterka", "Jan: rolnik", "Jan: rolnik", "Reporterka"])],
        uncertain_indices=[2, 99],
    )
    segments, has_speakers, notes = auto.label_speakers(LabellingEngine(labels), "T", _segments())
    assert has_speakers
    assert [s.speaker for s in segments] == ["Reporterka", "Jan rolnik", "Jan rolnik", "Reporterka"]
    assert any("00:00:10" in n for n in notes)


def test_label_speakers_rejects_sparse_labels():
    labels = SpeakerLabels(labels=[SpeakerLabel(index=0, speaker="Reporter")])
    segments, has_speakers, _ = auto.label_speakers(LabellingEngine(labels), "T", _segments())
    assert not has_speakers and all(s.speaker is None for s in segments)


@needs_ffmpeg
def test_auto_from_uploaded_file_end_to_end(auto_client):
    client = auto_client()
    response = client.post(
        "/api/auto", data={"category": "sonda"}, files={"file": ("sonda_kaucja.wav", _wav_bytes(), "audio/wav")}
    )
    assert response.status_code == 202
    material = client.get(f"/api/materials/{response.json()['id']}").json()
    assert material["status"] == "APPROVED", material["job_error"]
    assert material["job_stage"] == "DONE"
    assert material["transcript_has_speakers"] is False
    assert material["report"]["ok"] is True
    assert material["draft"]["mode"] == material["evidence"]["suggested_mode"]
    # Tytuł roboczy z nazwy pliku zastąpiony propozycją z draftu.
    assert material["title"] == material["draft"]["title_options"][0]
    assert client.get(f"/media/{material['id']}").content == _wav_bytes()
    assert client.get(f"/api/materials/{material['id']}/export?format=html").status_code == 200


@needs_ffmpeg
def test_auto_without_approval_leaves_draft(auto_client):
    client = auto_client()
    response = client.post(
        "/api/auto", data={"auto_approve": "false", "mode": "FULL"}, files={"file": ("a.wav", _wav_bytes(), "audio/wav")}
    )
    material = client.get(f"/api/materials/{response.json()['id']}").json()
    assert material["status"] == "DRAFTED" and material["mode"] == "FULL"


@needs_ffmpeg
def test_auto_failure_is_reported(auto_client):
    client = auto_client(FakeTranscriber(error=RuntimeError("model ASR niedostępny")))
    mid = client.post("/api/auto", files={"file": ("a.wav", _wav_bytes(), "audio/wav")}).json()["id"]
    material = client.get(f"/api/materials/{mid}").json()
    assert material["status"] == "FAILED"
    assert material["job_error"] == "model ASR niedostępny"


@needs_ffmpeg
def test_auto_rejects_empty_recognition(auto_client):
    client = auto_client(FakeTranscriber(segments=[]))
    mid = client.post("/api/auto", files={"file": ("a.wav", _wav_bytes(), "audio/wav")}).json()["id"]
    assert "nie rozpoznano mowy" in client.get(f"/api/materials/{mid}").json()["job_error"]


def test_auto_validation(auto_client):
    client = auto_client()
    assert client.post("/api/auto", data={}).status_code == 422
    assert (
        client.post(
            "/api/auto", data={"url": "https://x.example/v.mp4"}, files={"file": ("a.wav", b"x", "audio/wav")}
        ).status_code
        == 422
    )
    assert client.post("/api/auto", data={"url": "file:///etc/passwd"}).status_code == 422
    assert client.post("/api/auto", data={"url": "https://x.example/v.mp4", "mode": "XL"}).status_code == 422


def test_auto_requires_consent_for_external_engine(auto_client):
    class External(HeuristicEngine):
        name, external = "external", True

    client = auto_client(engine=External())
    assert client.post("/api/auto", data={"url": "https://x.example/v.mp4"}).status_code == 403


def test_auto_on_existing_transcript(auto_client, raw_transcript):
    client = auto_client()
    mid = client.post("/api/materials", json={"title": "Sonda", "transcript_raw": raw_transcript}).json()["id"]
    response = client.post(f"/api/materials/{mid}/auto", json={})
    assert response.status_code == 202
    material = client.get(f"/api/materials/{mid}").json()
    assert material["status"] == "APPROVED" and material["style_profile_id"] == "wp-finanse"


def test_processing_material_is_locked(auto_client, raw_transcript):
    store = Store(":memory:")
    client = TestClient(create_app(store, HeuristicEngine(), FakeTranscriber(), InlineRunner()))
    material = Material(title="x", transcript_raw=raw_transcript, status=MaterialStatus.PROCESSING)
    store.save(material)
    assert client.post(f"/api/materials/{material.id}/analyze").status_code == 409
    assert client.post(f"/api/materials/{material.id}/auto", json={}).status_code == 409


def test_interrupted_jobs_are_marked_failed(raw_transcript):
    store = Store(":memory:")
    store.save(Material(title="x", transcript_raw=raw_transcript, status=MaterialStatus.PROCESSING))
    create_app(store, HeuristicEngine(), FakeTranscriber(), InlineRunner())
    assert store.list(Material)[0].status == MaterialStatus.FAILED


def test_private_urls_are_blocked(monkeypatch, tmp_path):
    from app.media import MediaError, fetch_media

    monkeypatch.delenv("EDITORIAL_ALLOW_PRIVATE_URLS", raising=False)
    for url in ("http://127.0.0.1:8000/a.mp4", "http://localhost/a.mp4", "http://10.0.0.5/a.mp4"):
        with pytest.raises(MediaError, match="wewnętrzny"):
            fetch_media(url, tmp_path)


def test_upload_size_limit(auto_client, monkeypatch):
    monkeypatch.setenv("EDITORIAL_MAX_UPLOAD_MB", "0")
    client = auto_client()
    response = client.post("/api/auto", files={"file": ("a.wav", _wav_bytes(), "audio/wav")})
    assert response.status_code == 413
    assert client.get("/api/materials").json() == []


def test_export_rejects_negative_title(client, raw_transcript):
    mid = client.post("/api/materials", json={"title": "x", "transcript_raw": raw_transcript}).json()["id"]
    client.post(f"/api/materials/{mid}/draft", json={"mode": "SHORT"})
    assert client.get(f"/api/materials/{mid}/export?format=html&title=-1").status_code == 422
