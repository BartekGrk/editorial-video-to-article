"""API i serwowanie interfejsu.

Dwa sposoby pracy:
- automatyczny: nagranie lub link → transkrypcja → analiza → draft w trybie
  sugerowanym przez analizę → weryfikacja → zatwierdzenie, gdy weryfikacja przeszła;
- ręczny: transkrypcja + metadane → analiza → wybór trybu i draft → edycja
  z ponowną weryfikacją → zatwierdzenie.
W obu przypadkach eksport do CMS (Markdown/HTML) jest możliwy tylko dla
draftu bez błędów weryfikacji.
"""

import os
import shutil
from importlib.util import find_spec
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auto, pipeline
from app.jobs import ThreadRunner
from app.media import Transcriber, WhisperTranscriber, title_from_filename
from app.workflow import run_auto_job
from app.engines import DraftRequest, Engine, EngineError, MaterialInput, create_engine
from app.models import ArticleDraft, ArticleMode, EditorialStyleProfile, Transcript
from app.render import render_html, render_markdown
from app.store import DEFAULT_STYLE_ID, DELETED_BUILTINS_KEY, AutoSettings, ConditionFailed, Material, builtin_style_ids, MaterialStatus, Store, StyleProfileRecord, seed_builtin_styles
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
    transcript_has_speakers: bool = True
    external_processing_allowed: bool = False
    style_profile_id: str | None = None


class MaterialSummary(BaseModel):
    id: str
    title: str
    category: str
    topic: str
    status: MaterialStatus
    mode: ArticleMode | None
    job_stage: str | None
    updated_at: str
    revision: int


class MaterialView(Material):
    transcript: Transcript | None


AutoRunOptions = AutoSettings


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
    asr: bool


# --- pomocnicze -----------------------------------------------------------


def _parse(raw: str, with_speakers: bool = True) -> Transcript:
    try:
        return parse_transcript(raw, with_speakers=with_speakers)
    except TranscriptParseError as error:
        raise HTTPException(status_code=422, detail=f"Transkrypcja: {error}") from error


def _transcript(material: Material) -> Transcript:
    return _parse(material.transcript_raw, material.transcript_has_speakers)


def _view(material: Material) -> MaterialView:
    transcript = _transcript(material) if material.transcript_raw.strip() else None
    return MaterialView(**material.model_dump(), transcript=transcript)


def _ensure_idle(material: Material) -> None:
    if material.status == MaterialStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Materiał jest w trakcie automatycznego przetwarzania")


def _material_input(material: Material) -> MaterialInput:
    return MaterialInput(
        title=material.title,
        transcript=_transcript(material),
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


NO_PROFILE = "__none__"


def _form_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("true", "1", "on", "yes"):
        return True
    if normalized in ("false", "0", "off", "no", ""):
        return False
    raise HTTPException(status_code=422, detail=f"{name}: oczekiwano true/false")


def _asr_available() -> bool:
    return find_spec("faster_whisper") is not None and shutil.which("ffmpeg") is not None


def _recover_interrupted_jobs(store: Store) -> None:
    """Zadania przerwane restartem serwera nie wrócą same – oznaczamy je jako nieudane."""
    for material in store.list(Material):
        if material.status == MaterialStatus.PROCESSING:
            store.save(
                material.model_copy(
                    update={
                        "status": MaterialStatus.FAILED,
                        "job_error": "Przetwarzanie przerwane restartem serwera – uruchom ponownie",
                    }
                )
            )


def _engine_call(fn, *args):
    try:
        return fn(*args)
    except EngineError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


# --- aplikacja ------------------------------------------------------------


def create_app(
    store: Store | None = None,
    engine: Engine | None = None,
    transcriber: Transcriber | None = None,
    runner=None,
    media_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Editorial Video → Article Assistant")
    app.state.store = store or Store(os.environ.get("EDITORIAL_DB", "data/editorial.db"))
    app.state.engine = engine or create_engine()
    app.state.transcriber = transcriber or WhisperTranscriber()
    app.state.runner = runner or ThreadRunner()
    app.state.media_dir = (media_dir or Path(os.environ.get("EDITORIAL_MEDIA_DIR", "data/media"))).resolve()
    seed_builtin_styles(app.state.store)
    _recover_interrupted_jobs(app.state.store)

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

    def commit(store: Store, material: Material, fields, condition=None) -> Material:
        """Zapis wyniku operacji na materiale – tylko zmienionych pól i tylko na aktualnym stanie.

        Analiza i draft trwają długo; zapis całej kopii sprzed wywołania cofałby zmiany
        zrobione w międzyczasie (edycje redaktora, usunięcie, uruchomiony automat).
        Warunki sprawdzamy w tej samej transakcji co zapis (`Store.update(condition=…)`).
        `fields` to słownik albo funkcja aktualnego materiału zwracająca słownik.
        """
        same_transcript = (material.transcript_raw, material.transcript_has_speakers)

        def unchanged(current: Material) -> bool:
            return (
                current.status != MaterialStatus.PROCESSING
                and (current.transcript_raw, current.transcript_has_speakers) == same_transcript
                and (condition is None or condition(current))
            )

        try:
            updated = store.update(
                Material,
                material.id,
                condition=unchanged,
                compute=fields if callable(fields) else None,
                **({} if callable(fields) else fields),
            )
        except ConditionFailed as error:
            raise HTTPException(
                status_code=409,
                detail="Materiał zmienił się w trakcie operacji (automat, transkrypcja albo nowszy draft) – odśwież",
            ) from error
        if updated is None:
            raise HTTPException(status_code=404, detail="Materiał został usunięty w trakcie operacji")
        return updated

    def resolve_style_id(store: Store, explicit: str | None, material_profile: str | None) -> str | None:
        """Profil dla draftu.

        `explicit`: wybór redaktora w tym żądaniu – "" oznacza świadomie "bez profilu",
        None – brak wyboru. Ta sama konwencja dotyczy profilu zapisanego w materiale. Wtedy profil materiału (jeśli nadal istnieje), a w ostateczności
        wbudowany profil WP Finanse. Usunięty profil (np. zapamiętany do ponowienia) nie blokuje pracy.
        """
        if explicit == "":
            return None
        if explicit and store.get(StyleProfileRecord, explicit):
            return explicit
        if material_profile == "":
            return None
        if material_profile and store.get(StyleProfileRecord, material_profile):
            return material_profile
        return DEFAULT_STYLE_ID if store.get(StyleProfileRecord, DEFAULT_STYLE_ID) else None

    def ensure_style_exists(store: Store, explicit: str | None) -> None:
        """Profil wskazany wprost w żądaniu musi istnieć (literówka nie może po cichu dać innego stylu)."""
        if explicit:
            get_style(store, explicit)

    def start_auto_job(
        request: Request, material: Material, options: AutoRunOptions, source: str | None = None
    ) -> Material:
        store: Store = request.app.state.store
        engine: Engine = request.app.state.engine
        profile_id = resolve_style_id(store, options.style_profile_id, material.style_profile_id)
        auto_options = auto.AutoOptions(
            title=material.title,
            category=material.category,
            topic=material.topic,
            angle=material.angle,
            mode=options.mode,
            style=get_style(store, profile_id),
            target_words=options.target_words,
        )
        try:
            queued = store.update(
                Material,
                material.id,
                # Sprawdzane atomowo: dwa równoczesne uruchomienia nie mogą zakolejkować dwóch zadań.
                condition=lambda current: current.status != MaterialStatus.PROCESSING,
                status=MaterialStatus.PROCESSING,
                job_stage=auto.Stage.QUEUED.value,
                job_error=None,
                # "" zapamiętuje świadomy brak profilu (None oznacza profil domyślny).
                style_profile_id=profile_id or "",
                auto_settings=options.model_copy(update={"style_profile_id": profile_id or ""}),
            )
        except ConditionFailed as error:
            raise HTTPException(status_code=409, detail="Automat już przetwarza ten materiał") from error
        if queued is None:
            raise HTTPException(status_code=404, detail="Nie ma takiego materiału")
        request.app.state.runner.submit(
            lambda: run_auto_job(
                store,
                engine,
                request.app.state.transcriber,
                material.id,
                auto_options,
                options.auto_approve,
                request.app.state.media_dir,
                source,
            )
        )
        return store.get(Material, material.id) or queued

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def config(engine: Engine = Depends(get_engine)) -> AppConfig:
        return AppConfig(
            engine=engine.name, external=engine.external, model=getattr(engine, "model", None), asr=_asr_available()
        )

    # Materiały ------------------------------------------------------------

    @app.get("/api/materials")
    def list_materials(store: Store = Depends(get_store)) -> list[MaterialSummary]:
        # Lista bez transkrypcji i draftów – odświeżana co kilka sekund w trakcie automatu.
        return [MaterialSummary.model_validate(m.model_dump()) for m in store.list(Material)]

    @app.post("/api/materials", status_code=201)
    def create_material(fields: MaterialFields, store: Store = Depends(get_store)) -> MaterialView:
        _parse(fields.transcript_raw, fields.transcript_has_speakers)
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
        _ensure_idle(material)
        _parse(fields.transcript_raw, fields.transcript_has_speakers)
        get_style(store, fields.style_profile_id)
        # Zapis przez store.update: nie wskrzesza usuniętego materiału i nie nadpisuje pól
        # (draft, status) zapisanych w międzyczasie przez inną operację.
        transcript_changed = (
            fields.transcript_raw != material.transcript_raw
            or fields.transcript_has_speakers != material.transcript_has_speakers
        )
        changes = fields.model_dump()
        if fields.title != material.title:
            # Tytuł nadany przez redaktora nie może zostać nadpisany przez automat.
            changes["title_auto"] = False
        if transcript_changed:
            # Materiał dowodowy i draft odnoszą się do starej transkrypcji – nie można ich zachować.
            changes.update(
                evidence=None,
                draft=None,
                report=None,
                mode=None,
                status=MaterialStatus.NEW,
                job_stage=None,
                job_error=None,
                # Zastrzeżenia ASR dotyczyły poprzedniej wersji transkrypcji.
                transcript_notes=[],
            )
        updated = store.update(Material, material.id, **changes)
        if updated is None:
            raise HTTPException(status_code=404, detail="Nie ma takiego materiału")
        return _view(updated)

    @app.delete("/api/materials/{material_id}", status_code=204)
    def delete_material(
        request: Request, material: Material = Depends(get_material), store: Store = Depends(get_store)
    ) -> None:
        store.delete(Material, material.id)
        media = request.app.state.media_dir / material.id
        if media.is_dir() and media.parent == request.app.state.media_dir:
            shutil.rmtree(media, ignore_errors=True)

    @app.post("/api/materials/{material_id}/analyze")
    def analyze_material(
        material: Material = Depends(get_material),
        store: Store = Depends(get_store),
        engine: Engine = Depends(get_engine),
    ) -> MaterialView:
        _ensure_idle(material)
        _ensure_consent(engine, material)
        evidence = _engine_call(pipeline.analyze, engine, _material_input(material))
        updated = commit(
            store,
            material,
            lambda current: {
                "evidence": evidence,
                "status": MaterialStatus.ANALYZED if current.draft is None else current.status,
            },
        )
        return _view(updated)

    @app.post("/api/materials/{material_id}/draft")
    def draft_material(
        options: DraftOptions,
        material: Material = Depends(get_material),
        store: Store = Depends(get_store),
        engine: Engine = Depends(get_engine),
    ) -> DraftResponse:
        _ensure_idle(material)
        _ensure_consent(engine, material)
        material_input = _material_input(material)
        evidence = material.evidence or _engine_call(pipeline.analyze, engine, material_input)
        ensure_style_exists(store, options.style_profile_id)
        profile_id = resolve_style_id(store, options.style_profile_id, material.style_profile_id)
        request = DraftRequest(
            material=material_input,
            evidence=evidence,
            mode=options.mode,
            style=get_style(store, profile_id),
            target_words=options.target_words,
            extra_instructions=options.extra_instructions,
        )
        result = _engine_call(pipeline.write, engine, request)
        updated = commit(
            store,
            material,
            {
                "evidence": evidence,
                "mode": options.mode,
                "draft": result.draft,
                "report": result.report,
                "style_profile_id": profile_id or "",
                "status": MaterialStatus.DRAFTED,
            },
        )
        return DraftResponse(material=_view(updated), repaired=result.repaired)

    @app.put("/api/materials/{material_id}/draft")
    def save_draft(
        draft: ArticleDraft, material: Material = Depends(get_material), store: Store = Depends(get_store)
    ) -> MaterialView:
        _ensure_idle(material)
        report = verify_draft(draft, _transcript(material))
        updated = commit(
            store, material, {"draft": draft, "mode": draft.mode, "report": report, "status": MaterialStatus.DRAFTED}
        )
        return _view(updated)

    @app.post("/api/materials/{material_id}/approve")
    def approve(
        version: int | None = Query(None, description="revision materiału oglądanego przez redaktora"),
        material: Material = Depends(get_material),
        store: Store = Depends(get_store),
    ) -> MaterialView:
        _ensure_idle(material)
        if version is not None and version != material.revision:
            raise HTTPException(status_code=409, detail="Draft zmienił się od wyświetlenia – odśwież i sprawdź go ponownie")
        if material.draft is None or material.report is None or not material.report.ok:
            raise HTTPException(status_code=409, detail="Zatwierdzić można tylko draft, który przeszedł weryfikację")
        updated = commit(
            store,
            material,
            {"status": MaterialStatus.APPROVED},
            # Zatwierdzamy tylko wersję, którą oglądał redaktor, i tylko poprawnie zweryfikowaną.
            condition=lambda current: current.revision == material.revision
            and current.report is not None
            and current.report.ok,
        )
        return _view(updated)

    @app.get("/api/materials/{material_id}/export")
    def export(
        format: str = "md",
        # Domyślnie: materiał kontrolny w Markdown (do pracy redakcyjnej), bez niego w HTML (do CMS).
        control: bool | None = None,
        title: int = Query(0, ge=0, le=2),
        material: Material = Depends(get_material),
    ) -> PlainTextResponse:
        if material.draft is None:
            raise HTTPException(status_code=404, detail="Materiał nie ma jeszcze draftu")
        # Eksport tylko po pozytywnej weryfikacji: niezweryfikowany cytat
        # nie powinien trafić nawet do wersji roboczej w CMS.
        report = verify_draft(material.draft, _transcript(material))
        if not report.ok:
            raise HTTPException(status_code=409, detail=report.problems())
        if format == "md":
            include = True if control is None else control
            return PlainTextResponse(render_markdown(material.draft, include_control=include), media_type="text/markdown")
        if format == "html":
            include = False if control is None else control
            return PlainTextResponse(
                render_html(material.draft, title_index=title, include_control=include), media_type="text/html"
            )
        raise HTTPException(status_code=422, detail="Format: md lub html")

    # Automat -------------------------------------------------------------

    @app.post("/api/auto", status_code=202)
    async def auto_from_media(
        request: Request,
        url: str = Form(""),
        file: UploadFile | None = File(None),
        title: str = Form(""),
        category: str = Form(""),
        topic: str = Form(""),
        angle: str = Form(""),
        mode: str = Form(""),
        style_profile_id: str = Form(""),
        external_processing_allowed: str = Form("false"),
        auto_approve: str = Form("true"),
    ) -> MaterialView:
        """Nagranie (plik) albo link → cały proces w tle; postęp w `job_stage` materiału."""
        # Pola logiczne jako tekst: FastAPI traktuje brakujące pole bool w formularzu jak
        # niezaznaczony checkbox (False), co po cichu wyłączałoby domyślne zatwierdzanie.
        consent = _form_bool(external_processing_allowed, "external_processing_allowed")
        approve_when_ok = _form_bool(auto_approve, "auto_approve")
        store: Store = request.app.state.store
        engine: Engine = request.app.state.engine
        url = url.strip()
        has_file = file is not None and bool(file.filename)
        if has_file == bool(url):
            raise HTTPException(status_code=422, detail="Podaj plik nagrania albo link – dokładnie jedno z nich")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="Link musi zaczynać się od http:// lub https://")
        if engine.external and not consent:
            raise HTTPException(
                status_code=403,
                detail="Potwierdź zgodę na przetwarzanie transkrypcji przez zewnętrzne API (materiał publiczny "
                "lub dopuszczony przez redakcję).",
            )
        try:
            run_mode = ArticleMode(mode) if mode else None
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Tryb: SHORT, FULL albo pusty (automatyczny)") from error
        # Formularz nie przenosi null: puste pole = profil domyślny, NO_PROFILE = świadomie bez profilu.
        chosen_style = "" if style_profile_id == NO_PROFILE else style_profile_id or None
        get_style(store, chosen_style)

        material = Material(
            title=title.strip() or (title_from_filename(file.filename) if has_file else "Nagranie z linku"),
            title_auto=not title.strip(),
            category=category,
            topic=topic,
            angle=angle,
            source=url or (file.filename if has_file else ""),
            external_processing_allowed=consent,
            style_profile_id=chosen_style,
        )
        source = url
        if has_file:
            workdir = request.app.state.media_dir / material.id
            workdir.mkdir(parents=True, exist_ok=True)
            # Nazwa pliku od użytkownika służy tylko do rozszerzenia – nie do ścieżki.
            suffix = "".join(c for c in Path(file.filename).suffix.lower() if c.isalnum() or c == ".")[:10]
            target = workdir / f"upload{suffix}"
            limit = int(os.environ.get("EDITORIAL_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
            written = 0
            with target.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        handle.close()
                        shutil.rmtree(workdir, ignore_errors=True)
                        raise HTTPException(status_code=413, detail=f"Plik większy niż {limit // 1024 // 1024} MB")
                    handle.write(chunk)
            source = str(target)
            # Zapisany od razu: odtwarzanie w UI i ponowienie automatu po błędzie.
            material.media_file = str(target.relative_to(request.app.state.media_dir))
        store.save(material)
        options = AutoRunOptions(mode=run_mode, style_profile_id=chosen_style, auto_approve=approve_when_ok)
        return _view(start_auto_job(request, material, options, source))

    @app.post("/api/materials/{material_id}/auto", status_code=202)
    def auto_from_transcript(
        request: Request, options: AutoRunOptions, material: Material = Depends(get_material)
    ) -> MaterialView:
        """Automat dla materiału z gotową transkrypcją: analiza, tryb, draft, weryfikacja."""
        _ensure_idle(material)
        _ensure_consent(request.app.state.engine, material)
        if not material.transcript_raw.strip():
            raise HTTPException(status_code=409, detail="Materiał nie ma transkrypcji")
        _transcript(material)
        ensure_style_exists(request.app.state.store, options.style_profile_id)
        return _view(start_auto_job(request, material, options))

    @app.post("/api/materials/{material_id}/retry", status_code=202)
    def retry_auto(request: Request, material: Material = Depends(get_material)) -> MaterialView:
        """Ponowienie automatu po błędzie: od transkrypcji, jeśli jej jeszcze nie ma."""
        if material.status != MaterialStatus.FAILED:
            raise HTTPException(status_code=409, detail="Ponowić można tylko automat zakończony błędem")
        _ensure_consent(request.app.state.engine, material)
        # Profil z materiału: redaktor mógł go zmienić w ustawieniach po błędzie. Gdy materiał
        # nie ma zapisanego wyboru (None), obowiązuje ten z ostatniego uruchomienia ("" = bez profilu).
        options = material.auto_settings or AutoRunOptions()
        if material.style_profile_id is not None:
            options = options.model_copy(update={"style_profile_id": material.style_profile_id})
        if material.transcript_raw.strip():
            return _view(start_auto_job(request, material, options))
        media = request.app.state.media_dir / material.media_file if material.media_file else None
        if media is not None and media.is_file():
            source = str(media)
        elif material.source.startswith(("http://", "https://")):
            source = material.source
        else:
            raise HTTPException(status_code=409, detail="Brak nagrania do ponownego przetworzenia")
        return _view(start_auto_job(request, material, options, source))

    @app.get("/media/{material_id}", include_in_schema=False)
    def media(request: Request, material: Material = Depends(get_material)) -> FileResponse:
        if not material.media_file:
            raise HTTPException(status_code=404, detail="Materiał nie ma pliku nagrania")
        root = request.app.state.media_dir
        path = (root / material.media_file).resolve()
        if not path.is_file() or not path.is_relative_to(root):
            raise HTTPException(status_code=404, detail="Brak pliku nagrania")
        return FileResponse(path)

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
        if profile_id in builtin_style_ids():
            # Zapamiętujemy, żeby wbudowany profil nie wrócił po restarcie serwera.
            deleted = set(store.get_setting(DELETED_BUILTINS_KEY, []))
            store.set_setting(DELETED_BUILTINS_KEY, sorted(deleted | {profile_id}))

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
