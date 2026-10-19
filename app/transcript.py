"""Parser transkrypcji z oznaczeniami czasu.

Obsługiwany format (jedna wypowiedź na linię):

    [00:01:23] Jan Kowalski: treść wypowiedzi
    [01:23] treść bez mówcy

Linie bez znacznika czasu są doklejane do poprzedniego segmentu, bo eksporty
ASR często łamią długie wypowiedzi na kilka linii.
"""

import re

from app.models import Transcript, TranscriptSegment

_LINE = re.compile(r"^\[(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<rest>.*)$")
# Mówca to krótki fragment przed dwukropkiem; limit długości chroni przed
# potraktowaniem zdania zawierającego dwukropek jako nazwy mówcy.
_SPEAKER = re.compile(r"^(?P<speaker>[^:]{1,60}):\s+(?P<text>.+)$")


class TranscriptParseError(ValueError):
    pass


def parse_timecode(value: str) -> int:
    parts = [int(p) for p in value.split(":")]
    if any(p >= 60 for p in parts[1:]):
        raise TranscriptParseError(f"Niepoprawny znacznik czasu: {value}")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def format_timecode(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_transcript(raw: str) -> Transcript:
    segments: list[TranscriptSegment] = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        match = _LINE.match(line)
        if match is None:
            if not segments:
                raise TranscriptParseError(
                    f"Linia {line_no}: transkrypcja musi zaczynać się od znacznika czasu"
                )
            segments[-1].text = f"{segments[-1].text} {line}"
            continue

        start = parse_timecode(match["time"])
        if segments and start < segments[-1].start_seconds:
            raise TranscriptParseError(f"Linia {line_no}: znaczniki czasu nie rosną")
        rest = match["rest"]
        speaker_match = _SPEAKER.match(rest)
        if speaker_match:
            speaker, text = speaker_match["speaker"].strip(), speaker_match["text"]
        else:
            speaker, text = None, rest
        segments.append(TranscriptSegment(start_seconds=start, speaker=speaker, text=text))

    if not segments:
        raise TranscriptParseError("Pusta transkrypcja")
    return Transcript(segments=segments)
