"""API i serwowanie interfejsu.

Przepływ pracy redaktora:
  utwórz materiał (transkrypcja + metadane) → analiza (materiał dowodowy)
  → wybór trybu SHORT/FULL i draft → edycja z ponowną weryfikacją
  → zatwierdzenie → ręczny eksport (Markdown/HTML) do CMS.
Aplikacja niczego nie publikuje sama.
"""

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import pipeline
from app.engines import DraftRequest, Engine, EngineError, MaterialInput, create_engine
from app.models import ArticleDraft, ArticleMode, EditorialStyleProfile, Transcript
from app.render import render_html, render_markdown
from app.store import Material, MaterialStatus, Store, StyleProfileRecord
from app.transcript import TranscriptParseError, parse_transcript
from app.verification import VerificationReport, verify_draft

STATIC_DIR = Path(__file__).parent / "static"


# --- schematy żądań -------------------------------------------------------


class RawTranscript(BaseModel):
    text: str


class DraftWithTranscript(BaseModel):
    transcript: RawTranscript
    draft: ArticleDraft


class MarkdownResponse(BaseModel):
    markdown: str


class MaterialFields(BaseModel):
    title: str = Field(min_length=1)
    category: str = ""
    topic: str = ""
    angle: str = ""
    video_url: str = ""
    transcript_raw: str = Field(min_length=1)
    external_processing_allowed: bool = False
    style_profile_id: str | None = None


class MaterialView(Material):
    transcript: Transcript


class DraftOptions(BaseModel):
    mode: ArticleMode
    target_words: int | None = Field(default=None, ge=30, le=3000)
    extra_instructions: str = ""
    style_profile_id: str | None = None


class DraftResponse(BaseModel):
    material: MaterialView
    repaired: bool


class StyleProfileCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    articles: list[str] = Field(min_length=1, max_length=15)


class StyleProfileUpdate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    profile: EditorialStyleProfile


class AppConfig(BaseModel):
    engine: str
    external: bool
    model: str | None


# --- pomocnicze -----------------------------------------------------------


def _parse(raw: str) -> Transcript:
    try:
        return parse_transcript(raw)
    except TranscriptParseError as error:
        raise HTTPException(status_code=422, detail=f"Transkrypcja: {error}") from error


def _view(material: Material) -> MaterialView:
    return MaterialView(**material.model_dump(), transcript=_parse(material.transcript_raw))


def _material_input(material: Material) -> MaterialInput:
    return MaterialInput(
        title=material.title,
        transcript=_parse(material.transcript_raw),
        category=material.category,
        topic=material.topic,
        angle=material.angle,
    )


def _ensure_consent(engine: Engine, material: Material) -> None:
    # Specyfikacja: nieopublikowanych transkrypcji nie wysyłamy do zewnętrznego API
    # bez ustalonej polityki redakcji. Redaktor musi to jawnie potwierdzić.
    if engine.external and not material.external_processing_allowed:
        raise HTTPException(
            status_code=403,
            detail="Materiał nie ma zgody na przetwarzanie przez zewnętrzne API. "
            "Zaznacz ją w ustawieniach materiału, jeśli materiał jest publiczny lub dopuszczony przez redakcję.",
        )


def _engine_call(fn, *args):
    try:
        return fn(*args)
    except EngineError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


# --- aplikacja ------------------------------------------------------------


def create_app(store: Store | None = None, engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="Editorial Video → Article Assistant")
    app.state.store = store or Store(os.environ.get("EDITORIAL_DB", "data/editorial.db"))
    app.state.engine = engine or create_engine()

    def get_store(request: Request) -> Store:
        return request.app.state.store

    def get_engine(request: Request) -> Engine:
        return request.app.state.engine

    def get_material(material_id: str, store: Store = Depends(get_store)) -> Material:
        material = store.get(Material, material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Nie ma takiego materiału")
        return material

    def get_style(store: Store, profile_id: str | None) -> EditorialStyleProfile | None:
        if not profile_id:
            return None
        record = store.get(StyleProfileRecord, profile_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Nie ma takiego profilu stylu")
        return record.profile

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def config(engine: Engine = Depends(get_engine)) -> AppConfig:
        return AppConfig(engine=engine.name, external=engine.external, model=getattr(engine, "model", None))

    # Materiały ------------------------------------------------------------

    @app.get("/api/materials")
    def list_materials(store: Store = Depends(get_store)) -> list[Material]:
        return store.list(Material)

    @app.post("/api/materials", status_code=201)
    def create_material(fields: MaterialFields, store: Store = Depends(get_store)) -> MaterialView:
        _parse(fields.transcript_raw)
        get_style(store, fields.style_profile_id)
        material = Material(**fields.model_dump())
        store.save(material)
        return _view(material)

    @app.get("/api/materials/{material_id}")
    def read_material(material: Material = Depends(get_material)) -> MaterialView:
        return _view(material)

    @app.put("/api/materials/{material_id}")
    def update_material(
        fields: MaterialFields, material: Material = Depends(get_material), store: Store = Depends(get_store)
    ) -> MaterialView:
        _parse(fields.transcript_raw)
        get_style(store, fields.style_profile_id)
        transcript_changed = fields.transcript_raw != material.transcript_raw
        updated = material.model_copy(update=fields.model_dump())
        if transcript_changed:
            # Materiał dowodowy i draft odnoszą się do starej transkrypcji – nie można ich zachować.
            updated = updated.model_copy(
                update={"evidence": None, "draft": None, "report": None, "mode": None, "status": MaterialStatus.NEW}
            )
        store.save(updated)
        return _view(updated)

    @app.delete("/api/materials/{material_id}", status_code=204)
    def delete_material(material: Material = Depends(get_material), store: Store = Depends(get_store)) -> None:
        store.delete(Material, material.id)

    @app.post("/api/materials/{material_id}/analyze")
    def analyze_material(
        material: Material = Depends(get_material),
        store: Store = Depends(get_store),
        engine: Engine = Depends(get_engine),
    ) -> MaterialView:
        _ensure_consent(engine, material)
        evidence = _engine_call(pipeline.analyze, engine, _material_input(material))
        updated = material.model_copy(
            update={
                "evidence": evidence,
                "status": MaterialStatus.ANALYZED if material.draft is None else material.status,
            }
        )
        store.save(updated)
        return _view(updated)

    @app.post("/api/materials/{material_id}/draft")
    def draft_material(
        options: DraftOptions,
        material: Material = Depends(get_material),
        store: Store = Depends(get_store),
        engine: Engine = Depends(get_engine),
    ) -> DraftResponse:
        _ensure_consent(engine, material)
        material_input = _material_input(material)
        evidence = material.evidence or _engine_call(pipeline.analyze, engine, material_input)
        profile_id = options.style_profile_id or material.style_profile_id
        request = DraftRequest(
            material=material_input,
            evidence=evidence,
            mode=options.mode,
            style=get_style(store, profile_id),
            target_words=options.target_words,
            extra_instructions=options.extra_instructions,
        )
        result = _engine_call(pipeline.write, engine, request)
        updated = material.model_copy(
            update={
                "evidence": evidence,
                "mode": options.mode,
                "draft": result.draft,
                "report": result.report,
                "style_profile_id": profile_id,
                "status": MaterialStatus.DRAFTED,
            }
        )
        store.save(updated)
        return DraftResponse(material=_view(updated), repaired=result.repaired)

    @app.put("/api/materials/{material_id}/draft")
    def save_draft(
        draft: ArticleDraft, material: Material = Depends(get_material), store: Store = Depends(get_store)
    ) -> MaterialView:
        report = verify_draft(draft, _parse(material.transcript_raw))
        updated = material.model_copy(
            update={"draft": draft, "mode": draft.mode, "report": report, "status": MaterialStatus.DRAFTED}
        )
        store.save(updated)
        return _view(updated)

    @app.post("/api/materials/{material_id}/approve")
    def approve(material: Material = Depends(get_material), store: Store = Depends(get_store)) -> MaterialView:
        if material.draft is None or material.report is None or not material.report.ok:
            raise HTTPException(status_code=409, detail="Zatwierdzić można tylko draft, który przeszedł weryfikację")
        updated = material.model_copy(update={"status": MaterialStatus.APPROVED})
        store.save(updated)
        return _view(updated)

    @app.get("/api/materials/{material_id}/export")
    def export(
        format: str = "md", control: bool = True, title: int = 0, material: Material = Depends(get_material)
    ) -> PlainTextResponse:
        if material.draft is None:
            raise HTTPException(status_code=404, detail="Materiał nie ma jeszcze draftu")
        # Eksport tylko po pozytywnej weryfikacji: niezweryfikowany cytat
        # nie powinien trafić nawet do wersji roboczej w CMS.
        report = verify_draft(material.draft, _parse(material.transcript_raw))
        if not report.ok:
            raise HTTPException(status_code=409, detail=report.problems())
        if format == "md":
            return PlainTextResponse(render_markdown(material.draft, include_control=control), media_type="text/markdown")
        if format == "html":
            return PlainTextResponse(
                render_html(material.draft, title_index=title, include_control=control), media_type="text/html"
            )
        raise HTTPException(status_code=422, detail="Format: md lub html")

    # Profile stylu --------------------------------------------------------

    @app.get("/api/style-profiles")
    def list_profiles(store: Store = Depends(get_store)) -> list[StyleProfileRecord]:
        return store.list(StyleProfileRecord)

    @app.post("/api/style-profiles", status_code=201)
    def create_profile(
        payload: StyleProfileCreate, store: Store = Depends(get_store), engine: Engine = Depends(get_engine)
    ) -> StyleProfileRecord:
        articles = [a for a in payload.articles if a.strip()]
        if not articles:
            raise HTTPException(status_code=422, detail="Podaj co najmniej jeden artykuł")
        profile = _engine_call(engine.build_style_profile, articles)
        # Zapisujemy tylko profil; teksty artykułów referencyjnych nie są przechowywane.
        record = StyleProfileRecord(
            name=payload.name, description=payload.description, profile=profile, source_count=len(articles)
        )
        store.save(record)
        return record

    @app.put("/api/style-profiles/{profile_id}")
    def update_profile(
        profile_id: str, payload: StyleProfileUpdate, store: Store = Depends(get_store)
    ) -> StyleProfileRecord:
        record = store.get(StyleProfileRecord, profile_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Nie ma takiego profilu stylu")
        updated = record.model_copy(
            update={"name": payload.name, "description": payload.description, "profile": payload.profile}
        )
        store.save(updated)
        return updated

    @app.delete("/api/style-profiles/{profile_id}", status_code=204)
    def delete_profile(profile_id: str, store: Store = Depends(get_store)) -> None:
        if not store.delete(StyleProfileRecord, profile_id):
            raise HTTPException(status_code=404, detail="Nie ma takiego profilu stylu")

    # Narzędzia bezstanowe (np. do integracji lub skryptów) ----------------

    @app.post("/api/tools/parse")
    def parse(raw: RawTranscript) -> Transcript:
        return _parse(raw.text)

    @app.post("/api/tools/verify")
    def verify(payload: DraftWithTranscript) -> VerificationReport:
        return verify_draft(payload.draft, _parse(payload.transcript.text))

    @app.post("/api/tools/render")
    def render(payload: DraftWithTranscript) -> MarkdownResponse:
        report = verify_draft(payload.draft, _parse(payload.transcript.text))
        if not report.ok:
            raise HTTPException(status_code=409, detail=report.model_dump(mode="json"))
        return MarkdownResponse(markdown=render_markdown(payload.draft))

    # Interfejs ------------------------------------------------------------

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
