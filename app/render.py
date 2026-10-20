"""Eksport draftu do Markdown i HTML.

Wynik ma dwie części: tekst do redakcji oraz materiał kontrolny (cytaty
z timecode, tezy z flagą weryfikacji, uwagi). Część kontrolna nie trafia
do CMS; służy sprawdzeniu draftu obok nagrania. Do HTML domyślnie jej nie
dołączamy, bo ten format służy do wklejenia w edytor CMS.
"""

from html import escape

from app.models import ArticleDraft
from app.transcript import format_timecode


def _control_lines(draft: ArticleDraft) -> list[str]:
    lines = ["---", "", "## Materiał kontrolny (nie do publikacji)", ""]
    if draft.quotes:
        lines += ["### Cytaty", ""]
        for quote in draft.quotes:
            speaker = quote.speaker or "mówca nieznany"
            lines.append(f"- [{format_timecode(quote.timecode_seconds)}] {speaker}: „{quote.text}”")
        lines.append("")
    if draft.claims:
        lines += ["### Tezy i fakty", ""]
        for claim in draft.claims:
            evidence = ", ".join(format_timecode(t) for t in claim.evidence_timecodes) or "brak"
            flag = " **[requires_verification]**" if claim.requires_verification else ""
            note = f" — {claim.note}" if claim.note else ""
            lines.append(f"- {claim.statement} (źródło: {evidence}){flag}{note}")
        lines.append("")
    if draft.editor_notes:
        lines += ["### Uwagi dla redaktora", ""]
        lines += [f"- {note}" for note in draft.editor_notes]
        lines.append("")
    return lines


def _md_paragraph(text: str) -> str:
    """Akapit dialogowy "- cytat - mówi X." bez escapowania stałby się w Markdown listą."""
    return "\n".join(("\\" + line) if line.lstrip()[:1] in "-+*" and line.strip() else line for line in text.split("\n"))


def render_markdown(draft: ArticleDraft, include_control: bool = True) -> str:
    lines = [f"<!-- tryb: {draft.mode.value} -->", "", "## Propozycje tytułu", ""]
    lines += [f"{i}. {title}" for i, title in enumerate(draft.title_options, start=1)]
    lines += ["", "## Lead", "", _md_paragraph(draft.lead), "", "## Treść", ""]

    for section in draft.sections:
        if section.heading:
            lines += [f"### {section.heading}", ""]
        for paragraph in section.paragraphs:
            lines += [_md_paragraph(paragraph), ""]

    if include_control:
        lines += _control_lines(draft)
    return "\n".join(lines).rstrip() + "\n"


def render_html(draft: ArticleDraft, title_index: int = 0, include_control: bool = False) -> str:
    """Fragment HTML artykułu z wybranym tytułem, gotowy do wklejenia w edytor."""
    title = draft.title_options[min(title_index, len(draft.title_options) - 1)]
    parts = [f"<h1>{escape(title)}</h1>", f'<p class="lead"><strong>{escape(draft.lead)}</strong></p>']
    for section in draft.sections:
        if section.heading:
            parts.append(f"<h2>{escape(section.heading)}</h2>")
        parts += [f"<p>{escape(p)}</p>" for p in section.paragraphs]

    if include_control:
        parts.append("<hr><h2>Materiał kontrolny (nie do publikacji)</h2><ul>")
        for quote in draft.quotes:
            parts.append(
                f"<li>[{format_timecode(quote.timecode_seconds)}] {escape(quote.speaker or 'mówca nieznany')}: "
                f"„{escape(quote.text)}”</li>"
            )
        for claim in draft.claims:
            flag = " <strong>[requires_verification]</strong>" if claim.requires_verification else ""
            parts.append(f"<li>{escape(claim.statement)}{flag}</li>")
        parts += [f"<li>{escape(note)}</li>" for note in draft.editor_notes]
        parts.append("</ul>")
    return "\n".join(parts) + "\n"
