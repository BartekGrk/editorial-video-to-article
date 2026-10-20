"""Konflikt nazwisk przy cytacie – przypadki z przeglądów (zdania pisane przez model wokół [[cytat:N]])."""

import pytest

from app.models import ArticleDraft, Quote, Transcript, TranscriptSegment
from app.quote_markers import expand_markers
from app.verification import verify_draft


def _transcript(jan="Jan Kowalski, rolnik", anna="Anna Nowak, minister", jan_text="To jest skandal. Za tonę pszenicy dostajemy mniej."):
    return Transcript(
        segments=[
            TranscriptSegment(start_seconds=5, speaker=jan, text=jan_text),
            TranscriptSegment(start_seconds=12, speaker=anna, text="Pracujemy nad dopłatami."),
        ]
    )


def _report(lead, quote_text="To jest skandal", transcript=None, sections=()):
    t = transcript or _transcript()
    speaker = t.segments[0].speaker
    draft = ArticleDraft(
        mode="SHORT",
        title_options=["A b c d", "E f g h"],
        lead=lead,
        sections=list(sections),
        quotes=[Quote(text=quote_text, speaker=speaker, timecode_seconds=5)],
    )
    return verify_draft(expand_markers(draft, t), t)


@pytest.mark.parametrize(
    "lead",
    [
        "Minister Anna Nowak mówi krótko: [[cytat:1]].",  # krótki cytat
        "Nowak mówi wprost: [[cytat:1]].",  # samo nazwisko
        "Zdaniem Anny Nowak [[cytat:1]].",  # odmiana
        "Minister Anna Nowak nie ma złudzeń. [[cytat:1]] – stwierdza.",  # poprzednie zdanie
        "Anna Nowak, minister ds. rolnictwa, mówi: [[cytat:1]].",  # skrót "ds."
        "[[cytat:1]]\n- mówi Anna Nowak.",  # przypisanie w następnej linii
    ],
)
def test_conflicting_name_blocks_auto_approval(lead):
    report = _report(lead)
    assert report.ok and not report.strict_ok
    assert "Anna Nowak" in report.attribution_warnings[0]


@pytest.mark.parametrize(
    "lead",
    [
        "Jan Kowalski mówi krótko: [[cytat:1]]. Anna Nowak zapowiada dopłaty.",
        "Rolnik nie ma złudzeń: [[cytat:1]].",
        "Kowalski dodaje: [[cytat:1]].",
    ],
)
def test_correct_attribution_passes(lead):
    assert _report(lead).strict_ok


def test_label_formats_are_recognized():
    for anna in ("Anna Nowak (minister)", "Anna Nowak - minister", "Minister Anna Nowak"):
        report = _report("Anna Nowak mówi wprost: [[cytat:1]].", transcript=_transcript(anna=anna))
        assert not report.strict_ok, anna


def test_similar_surnames_are_distinct():
    t = _transcript(jan="Adam Nowak", anna="Adam Nowakowski")
    report = _report("Adam Nowakowski mówi wprost: [[cytat:1]].", transcript=t)
    assert not report.strict_ok
    assert _report("Adam Nowak mówi wprost: [[cytat:1]].", transcript=t).strict_ok


def test_names_inside_quote_and_generic_speaker_do_not_conflict():
    text = "Wójt Jan Kowalski obiecał nam drogę i nic nie zrobił."
    t = Transcript(
        segments=[
            TranscriptSegment(start_seconds=5, speaker="Rozmówca 1", text=text),
            TranscriptSegment(start_seconds=12, speaker="Jan Kowalski, wójt", text="Droga będzie w przyszłym roku."),
        ]
    )
    quote = "Wójt Jan Kowalski obiecał nam drogę i nic nie zrobił"
    assert _report("[[wypowiedź:1]]", quote_text=quote, transcript=t).strict_ok
    assert _report("Jeden z mieszkańców mówi: [[cytat:1]].", quote_text=quote, transcript=t).strict_ok


def test_curly_quotes_inside_quote_still_checked():
    t = _transcript(jan_text="Mówią nam “to nie przejdzie” i koniec rozmowy.")
    report = _report("Anna Nowak mówi: [[cytat:1]].", quote_text="Mówią nam “to nie przejdzie” i koniec rozmowy", transcript=t)
    assert not report.strict_ok


def test_malformed_marker_blocks_export():
    report = _report("Jan Kowalski mówi: [[cytat:1].")
    assert not report.ok and report.unresolved_markers


def test_next_dialogue_paragraph_is_not_attribution_and_prefix_quote():
    t = Transcript(
        segments=[
            TranscriptSegment(start_seconds=5, speaker="Jan Kowalski, rolnik", text="To jest wielki problem dla nas wszystkich."),
            TranscriptSegment(start_seconds=12, speaker="Anna Nowak, minister", text="że ceny wzrosną o dwadzieścia procent."),
        ]
    )
    draft = ArticleDraft(
        mode="FULL",
        title_options=["A b c d", "E f g h"],
        lead="Lead.",
        sections=[{"heading": None, "paragraphs": ["[[wypowiedź:1]]", "[[wypowiedź:2]]"]}],
        quotes=[
            Quote(text="To jest wielki problem dla nas wszystkich", speaker="Jan Kowalski, rolnik", timecode_seconds=5),
            Quote(text="że ceny wzrosną o dwadzieścia procent", speaker="Anna Nowak, minister", timecode_seconds=12),
        ],
    )
    assert verify_draft(expand_markers(draft, t), t).strict_ok


def test_fix_quote_timecodes_normalizes_speaker(transcript, draft):
    from app import pipeline

    draft.quotes[1] = draft.quotes[1].model_copy(update={"speaker": "przechodzień 2", "timecode_seconds": 5})
    assert pipeline.fix_quote_timecodes(draft, transcript).quotes[1].timecode_seconds == 31
