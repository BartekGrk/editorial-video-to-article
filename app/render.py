"""Eksport draftu do Markdown dla redaktora.

Wynik ma dwie części: tekst do redakcji oraz materiał kontrolny (cytaty
z timecode, tezy z flagą weryfikacji, uwagi). Część kontrolna nie trafia
do CMS; służy sprawdzeniu draftu obok nagrania.
"""

from app.models import ArticleDraft
from app.transcript import format_timecode


def render_markdown(draft: ArticleDraft) -> str:
    lines = [f"<!-- tryb: {draft.mode.value} -->", "", "## Propozycje tytułu", ""]
    lines += [f"{i}. {title}" for i, title in enumerate(draft.title_options, start=1)]
    lines += ["", "## Lead", "", draft.lead, "", "## Treść", ""]

    for section in draft.sections:
        if section.heading:
            lines += [f"### {section.heading}", ""]
        for paragraph in section.paragraphs:
            lines += [paragraph, ""]

    lines += ["---", "", "## Materiał kontrolny (nie do publikacji)", ""]

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

    return "\n".join(lines).rstrip() + "\n"
