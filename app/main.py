"""API prototypu. Generowanie draftu przez LLM nie jest jeszcze podłączone:
na tym etapie draft dostarcza klient (np. ręcznie lub z zewnętrznego modelu),
a aplikacja parsuje transkrypcję, weryfikuje draft i eksportuje go do Markdown.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.models import ArticleDraft, Transcript
from app.render import render_markdown
from app.transcript import TranscriptParseError, parse_transcript
from app.verification import VerificationReport, verify_draft

app = FastAPI(title="Editorial Video → Article Assistant")


class RawTranscript(BaseModel):
    text: str


class DraftWithTranscript(BaseModel):
    transcript: RawTranscript
    draft: ArticleDraft


class MarkdownResponse(BaseModel):
    markdown: str


def _parse(raw: RawTranscript) -> Transcript:
    try:
        return parse_transcript(raw.text)
    except TranscriptParseError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/transcripts/parse")
def parse(raw: RawTranscript) -> Transcript:
    return _parse(raw)


@app.post("/drafts/verify")
def verify(payload: DraftWithTranscript) -> VerificationReport:
    return verify_draft(payload.draft, _parse(payload.transcript))


@app.post("/drafts/render")
def render(payload: DraftWithTranscript) -> MarkdownResponse:
    # Eksport tylko po pozytywnej weryfikacji: niezweryfikowany cytat
    # nie powinien trafić nawet do wersji roboczej w CMS.
    report = verify_draft(payload.draft, _parse(payload.transcript))
    if not report.ok:
        raise HTTPException(status_code=409, detail=report.model_dump(mode="json"))
    return MarkdownResponse(markdown=render_markdown(payload.draft))
