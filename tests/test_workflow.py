"""Pełny przepływ redaktora przez API na silniku demonstracyjnym."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store


@pytest.fixture
def material(client, raw_transcript):
    response = client.post(
        "/api/materials",
        json={
            "title": "Sonda: system kaucyjny",
            "category": "sonda uliczna",
            "video_url": "https://www.youtube.com/watch?v=abc",
            "transcript_raw": raw_transcript,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_material_returns_parsed_transcript(material):
    assert material["status"] == "NEW"
    assert len(material["transcript"]["segments"]) == 5


def test_create_material_rejects_invalid_transcript(client):
    response = client.post("/api/materials", json={"title": "x", "transcript_raw": "bez czasu"})
    assert response.status_code == 422


def test_full_flow_analyze_draft_edit_approve_export(client, material):
    mid = material["id"]

    analyzed = client.post(f"/api/materials/{mid}/analyze").json()
    assert analyzed["status"] == "ANALYZED"
    assert analyzed["evidence"]["suggested_mode"] == "SHORT"
    assert analyzed["evidence"]["quotes"]

    response = client.post(f"/api/materials/{mid}/draft", json={"mode": "SHORT"})
    assert response.status_code == 200
    drafted = response.json()["material"]
    assert drafted["status"] == "DRAFTED"
    assert drafted["report"]["ok"] is True

    # Redaktor dopisuje zmyślony cytat: weryfikacja blokuje zatwierdzenie i eksport.
    draft = drafted["draft"]
    draft["sections"][0]["paragraphs"].append("Inny rozmówca: „Kaucja to najgorszy pomysł tego rządu”.")
    edited = client.put(f"/api/materials/{mid}/draft", json=draft).json()
    assert edited["report"]["ok"] is False
    assert client.post(f"/api/materials/{mid}/approve").status_code == 409
    assert client.get(f"/api/materials/{mid}/export").status_code == 409

    draft["sections"][0]["paragraphs"].pop()
    assert client.put(f"/api/materials/{mid}/draft", json=draft).json()["report"]["ok"] is True
    assert client.post(f"/api/materials/{mid}/approve").json()["status"] == "APPROVED"

    markdown = client.get(f"/api/materials/{mid}/export?format=md").text
    assert "## Lead" in markdown and "Materiał kontrolny" in markdown
    html = client.get(f"/api/materials/{mid}/export?format=html&control=false&title=1").text
    assert html.startswith(f"<h1>{draft['title_options'][1]}</h1>")
    assert "Materiał kontrolny" not in html


def test_full_mode_has_subheadings(client, material):
    drafted = client.post(f"/api/materials/{material['id']}/draft", json={"mode": "FULL"}).json()["material"]
    assert drafted["mode"] == "FULL"
    assert all(s["heading"] for s in drafted["draft"]["sections"])


def test_changing_transcript_resets_analysis(client, material, raw_transcript):
    mid = material["id"]
    client.post(f"/api/materials/{mid}/draft", json={"mode": "SHORT"})
    fields = {"title": material["title"], "transcript_raw": raw_transcript + "\n[00:01:00] Przechodzień 4: Nowa wypowiedź."}
    updated = client.put(f"/api/materials/{mid}", json=fields).json()
    assert updated["status"] == "NEW" and updated["draft"] is None and updated["evidence"] is None


def test_external_engine_requires_consent(raw_transcript):
    class ExternalEngine:
        name, external = "fake-external", True

    client = TestClient(create_app(Store(":memory:"), ExternalEngine()))
    mid = client.post("/api/materials", json={"title": "x", "transcript_raw": raw_transcript}).json()["id"]
    assert client.post(f"/api/materials/{mid}/analyze").status_code == 403


def test_style_profile_crud_and_use_in_draft(client, material):
    articles = ["Tytuł artykułu numer jeden\n\nLead z kilkoma słowami.\n\nAkapit treści, który ma zdanie."] * 3
    profile = client.post("/api/style-profiles", json={"name": "Wideo – finanse", "articles": articles})
    assert profile.status_code == 201
    pid = profile.json()["id"]
    assert profile.json()["profile"]["typical_title_words"] == 4

    body = profile.json()
    body["profile"]["tone"] = "neutralny"
    assert client.put(f"/api/style-profiles/{pid}", json=body).json()["profile"]["tone"] == "neutralny"

    drafted = client.post(
        f"/api/materials/{material['id']}/draft", json={"mode": "SHORT", "style_profile_id": pid}
    ).json()["material"]
    assert drafted["style_profile_id"] == pid

    assert client.delete(f"/api/style-profiles/{pid}").status_code == 204
    assert client.get("/api/style-profiles").json() == []


def test_list_and_delete_material(client, material):
    assert [m["id"] for m in client.get("/api/materials").json()] == [material["id"]]
    assert client.delete(f"/api/materials/{material['id']}").status_code == 204
    assert client.get(f"/api/materials/{material['id']}").status_code == 404


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_config_reports_engine(client):
    assert client.get("/api/config").json()["engine"] == "heuristic"


def test_demo_draft_passes_verification_for_long_title(client, raw_transcript):
    # Regresja: tytuł materiału w cudzysłowie w leadzie był traktowany jak zmyślony cytat.
    mid = client.post(
        "/api/materials", json={"title": "Sonda: czy oddajemy butelki w systemie kaucyjnym?", "transcript_raw": raw_transcript}
    ).json()["id"]
    for mode in ("SHORT", "FULL"):
        assert client.post(f"/api/materials/{mid}/draft", json={"mode": mode}).json()["material"]["report"]["ok"]
