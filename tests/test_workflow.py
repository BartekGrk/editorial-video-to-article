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
    original_lead = draft["lead"]
    draft["lead"] += " Inny rozmówca: „Kaucja to najgorszy pomysł tego rządu”."
    edited = client.put(f"/api/materials/{mid}/draft", json=draft).json()
    assert edited["report"]["ok"] is False
    assert client.post(f"/api/materials/{mid}/approve").status_code == 409
    assert client.get(f"/api/materials/{mid}/export").status_code == 409

    draft["lead"] = original_lead
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
    assert [p["id"] for p in client.get("/api/style-profiles").json()] == ["wp-finanse"]


def test_builtin_wp_style_is_default(client, material):
    drafted = client.post(f"/api/materials/{material['id']}/draft", json={"mode": "SHORT"}).json()["material"]
    assert drafted["style_profile_id"] == "wp-finanse"


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


def test_heuristic_title_quote_is_declared(client):
    raw = "\n".join(
        [
            "[00:00:01] Anna: Uważam, że ceny w sklepach rosną zdecydowanie za szybko w tym roku, naprawdę bardzo szybko.",
            "[00:00:10] Piotr: Moim zdaniem rząd powinien zrobić coś z cenami żywności i energii już teraz.",
            "[00:00:20] Ewa: Trzeba wreszcie obniżyć wszystkie podatki dochodowe.",
        ]
    )
    mid = client.post("/api/materials", json={"title": "Sonda o cenach", "transcript_raw": raw}).json()["id"]
    report = client.post(f"/api/materials/{mid}/draft", json={"mode": "SHORT"}).json()["material"]["report"]
    assert report["strict_ok"] is True


def test_transcript_edit_clears_asr_notes(client, raw_transcript):
    store = client.app.state.store
    from app.store import Material

    material = Material(title="x", transcript_raw=raw_transcript, transcript_notes=["Mówcy przypisani automatycznie"])
    store.save(material)
    client.put(f"/api/materials/{material.id}", json={"title": "x", "transcript_raw": raw_transcript + "\n[00:02:00] Ala: Nowe."})
    assert client.get(f"/api/materials/{material.id}").json()["transcript_notes"] == []


def test_long_operation_does_not_overwrite_newer_state(raw_transcript):
    """Wynik analizy zapisany po edycji redaktora nie cofa jego zmian; po usunięciu – nie wskrzesza."""
    from fastapi.testclient import TestClient

    from app.engines import HeuristicEngine
    from app.main import create_app
    from app.store import Store

    class EditsDuringAnalysis(HeuristicEngine):
        hook = None

        def extract_evidence(self, material):
            if self.hook:
                self.hook()
            return super().extract_evidence(material)

    engine = EditsDuringAnalysis()
    client = TestClient(create_app(Store(":memory:"), engine))
    mid = client.post("/api/materials", json={"title": "x", "transcript_raw": raw_transcript}).json()["id"]
    draft = client.post(f"/api/materials/{mid}/draft", json={"mode": "SHORT"}).json()["material"]["draft"]

    edited = {**draft, "lead": draft["lead"] + " Dopisek redaktora."}
    engine.hook = lambda: client.app.state.store.update(Material, mid, draft=ArticleDraft.model_validate(edited))
    client.post(f"/api/materials/{mid}/analyze")
    assert client.get(f"/api/materials/{mid}").json()["draft"]["lead"].endswith("Dopisek redaktora.")

    engine.hook = lambda: client.app.state.store.delete(Material, mid)
    assert client.post(f"/api/materials/{mid}/analyze").status_code == 404
    assert client.get(f"/api/materials/{mid}").status_code == 404


from app.models import ArticleDraft  # noqa: E402
from app.store import Material  # noqa: E402


def test_list_is_slim_and_retry_only_after_failure(client, raw_transcript):
    mid = client.post("/api/materials", json={"title": "x", "transcript_raw": raw_transcript}).json()["id"]
    listed = client.get("/api/materials").json()[0]
    assert "transcript_raw" not in listed and listed["revision"] >= 1
    assert client.post(f"/api/materials/{mid}/retry").status_code == 409


def test_deleted_builtin_profile_stays_deleted_after_restart():
    from app.engines import HeuristicEngine
    from app.main import create_app
    from app.store import Store

    store = Store(":memory:")
    client = TestClient(create_app(store, HeuristicEngine()))
    assert client.delete("/api/style-profiles/wp-finanse").status_code == 204
    create_app(store, HeuristicEngine())  # "restart" na tej samej bazie
    assert client.get("/api/style-profiles").json() == []


def test_revision_increments_on_every_write(raw_transcript):
    from app.store import Store

    store = Store(":memory:")
    material = Material(title="x", transcript_raw=raw_transcript)
    store.save(material)
    first = store.get(Material, material.id).revision
    store.save(store.get(Material, material.id))
    store.update(Material, material.id, title="y")
    assert store.get(Material, material.id).revision == first + 2
