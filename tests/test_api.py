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


def test_markdown_export_keeps_dialogue_paragraphs_out_of_lists(draft):
    from app.render import render_markdown

    draft.sections[0].paragraphs.append("- Za te trzydzieści groszy nie będę stać w kolejce - mówi Przechodzień 2.")
    markdown = render_markdown(draft, include_control=False)
    assert "\n\\- Za te trzydzieści groszy" in markdown


def test_markdown_and_html_line_handling(draft):
    from app.render import render_html, render_markdown

    draft.lead = "13. emerytura trafi do seniorów.\n  - wcięty\n**Czytaj także:** tekst"
    draft.sections[0].paragraphs = ["- Pierwsza - mówi A.\n- Druga - mówi B."]
    md = render_markdown(draft, include_control=False)
    assert "13\\. emerytura" in md and "  \\- wcięty" in md and "\n**Czytaj także:**" in md
    assert "\\- Pierwsza - mówi A.  \n\\- Druga - mówi B." in md
    assert "- Pierwsza - mówi A.<br>- Druga - mówi B." in render_html(draft)


def test_html_export_defaults_without_control_section(client, raw_transcript):
    mid = client.post("/api/materials", json={"title": "x", "transcript_raw": raw_transcript}).json()["id"]
    client.post(f"/api/materials/{mid}/draft", json={"mode": "SHORT"})
    assert "Materiał kontrolny" not in client.get(f"/api/materials/{mid}/export?format=html").text
    assert "Materiał kontrolny" in client.get(f"/api/materials/{mid}/export?format=md").text


def test_approve_requires_viewed_version(client, raw_transcript):
    mid = client.post("/api/materials", json={"title": "x", "transcript_raw": raw_transcript}).json()["id"]
    viewed = client.post(f"/api/materials/{mid}/draft", json={"mode": "SHORT"}).json()["material"]
    newer = {**viewed["draft"], "lead": viewed["draft"]["lead"] + " Zmiana z innej karty."}
    # zapis w tej samej sekundzie – wersja to licznik zapisów, nie znacznik czasu
    client.put(f"/api/materials/{mid}/draft", json=newer)
    assert client.post(f"/api/materials/{mid}/approve", params={"version": viewed["revision"]}).status_code == 409
    current = client.get(f"/api/materials/{mid}").json()
    assert current["revision"] > viewed["revision"]
    assert client.post(f"/api/materials/{mid}/approve", params={"version": current["revision"]}).status_code == 200


def test_settings_update_does_not_resurrect_deleted(client, raw_transcript):
    from app.store import Material

    store = client.app.state.store
    mid = client.post("/api/materials", json={"title": "x", "transcript_raw": raw_transcript}).json()["id"]
    original_update = store.update

    def delete_then_update(model, record_id, **fields):
        store.delete(Material, record_id)
        return original_update(model, record_id, **fields)

    store.update = delete_then_update
    response = client.put(f"/api/materials/{mid}", json={"title": "y", "transcript_raw": raw_transcript})
    store.update = original_update
    assert response.status_code == 404
    assert client.get(f"/api/materials/{mid}").status_code == 404
