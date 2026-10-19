import pytest

from app.transcript import TranscriptParseError, format_timecode, parse_transcript


def test_parses_segments_with_speakers_and_timecodes(transcript):
    first = transcript.segments[0]
    assert first.start_seconds == 5
    assert first.speaker == "Reporterka"
    assert len(transcript.segments) == 5


def test_line_without_timecode_is_appended_to_previous_segment(transcript):
    assert transcript.segments[2].text.endswith("trwa to minutę, może dwie.")


def test_segment_without_speaker():
    transcript = parse_transcript("[01:02] sam tekst bez mówcy")
    assert transcript.segments[0].speaker is None
    assert transcript.segments[0].start_seconds == 62


def test_rejects_text_before_first_timecode():
    with pytest.raises(TranscriptParseError):
        parse_transcript("wstęp\n[00:01] tekst")


def test_rejects_decreasing_timecodes():
    with pytest.raises(TranscriptParseError):
        parse_transcript("[00:10] a\n[00:05] b")


def test_rejects_invalid_minutes():
    with pytest.raises(TranscriptParseError):
        parse_transcript("[00:75] a")


def test_format_timecode():
    assert format_timecode(3723) == "01:02:03"


def test_without_speakers_colon_stays_in_text():
    transcript = parse_transcript("[00:05] Uwaga: to ważne", with_speakers=False)
    assert transcript.segments[0].speaker is None
    assert transcript.segments[0].text == "Uwaga: to ważne"


def test_build_transcript_raw_roundtrip(transcript, raw_transcript):
    from app.transcript import build_transcript_raw

    assert parse_transcript(build_transcript_raw(transcript.segments)) == transcript
