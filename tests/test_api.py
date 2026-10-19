def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_parse_endpoint(client, raw_transcript):
    response = client.post("/api/tools/parse", json={"text": raw_transcript})
    assert response.status_code == 200
    assert response.json()["segments"][1]["speaker"] == "Przechodzień 1"


def test_parse_endpoint_rejects_invalid_transcript(client):
    response = client.post("/api/tools/parse", json={"text": "bez znacznika"})
    assert response.status_code == 422


def test_verify_endpoint(client, raw_transcript, draft_data):
    payload = {"transcript": {"text": raw_transcript}, "draft": draft_data}
    response = client.post("/api/tools/verify", json=payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_render_returns_markdown_with_control_section(client, raw_transcript, draft_data):
    payload = {"transcript": {"text": raw_transcript}, "draft": draft_data}
    markdown = client.post("/api/tools/render", json=payload).json()["markdown"]
    assert "## Lead" in markdown
    assert "[00:00:12] Przechodzień 1: „Oddaję, bo automat jest w sklepie obok domu”" in markdown
    assert "**[requires_verification]**" in markdown


def test_render_refuses_draft_with_fabricated_quote(client, raw_transcript, draft_data):
    draft_data["quotes"].append(
        {"text": "System kaucyjny to porażka", "speaker": "Przechodzień 2", "timecode_seconds": 31}
    )
    payload = {"transcript": {"text": raw_transcript}, "draft": draft_data}
    response = client.post("/api/tools/render", json=payload)
    assert response.status_code == 409
    statuses = [q["status"] for q in response.json()["detail"]["quotes"]]
    assert statuses == ["VERIFIED", "VERIFIED", "NOT_FOUND"]


def test_draft_requires_two_or_three_titles(client, raw_transcript, draft_data):
    draft_data["title_options"] = ["Jeden tytuł"]
    payload = {"transcript": {"text": raw_transcript}, "draft": draft_data}
    assert client.post("/api/tools/verify", json=payload).status_code == 422
