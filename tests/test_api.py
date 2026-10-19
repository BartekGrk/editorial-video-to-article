from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_parse_endpoint(raw_transcript):
    response = client.post("/transcripts/parse", json={"text": raw_transcript})
    assert response.status_code == 200
    assert response.json()["segments"][1]["speaker"] == "Przechodzień 1"


def test_parse_endpoint_rejects_invalid_transcript():
    response = client.post("/transcripts/parse", json={"text": "bez znacznika"})
    assert response.status_code == 422


def test_verify_endpoint(raw_transcript, draft_data):
    payload = {"transcript": {"text": raw_transcript}, "draft": draft_data}
    response = client.post("/drafts/verify", json=payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_render_returns_markdown_with_control_section(raw_transcript, draft_data):
    payload = {"transcript": {"text": raw_transcript}, "draft": draft_data}
    markdown = client.post("/drafts/render", json=payload).json()["markdown"]
    assert "## Lead" in markdown
    assert "[00:00:12] Przechodzień 1: „Oddaję, bo automat jest w sklepie obok domu”" in markdown
    assert "**[requires_verification]**" in markdown


def test_render_refuses_draft_with_fabricated_quote(raw_transcript, draft_data):
    draft_data["quotes"].append(
        {"text": "System kaucyjny to porażka", "speaker": "Przechodzień 2", "timecode_seconds": 31}
    )
    payload = {"transcript": {"text": raw_transcript}, "draft": draft_data}
    response = client.post("/drafts/render", json=payload)
    assert response.status_code == 409
    statuses = [q["status"] for q in response.json()["detail"]["quotes"]]
    assert statuses == ["VERIFIED", "VERIFIED", "NOT_FOUND"]


def test_draft_requires_two_or_three_titles(raw_transcript, draft_data):
    draft_data["title_options"] = ["Jeden tytuł"]
    payload = {"transcript": {"text": raw_transcript}, "draft": draft_data}
    assert client.post("/drafts/verify", json=payload).status_code == 422
