from app.models import Claim, Quote
from app.verification import QuoteStatus, check_quote, verify_draft


def test_sample_draft_passes_verification(draft, transcript):
    assert verify_draft(draft, transcript).ok


def test_quote_ignores_case_and_punctuation(transcript):
    quote = Quote(text="oddaję — bo automat jest w sklepie obok domu!", speaker="przechodzień 1", timecode_seconds=12)
    assert check_quote(quote, transcript).status == QuoteStatus.VERIFIED


def test_embellished_quote_is_rejected(transcript):
    # Dopisane słowo zmienia sens wypowiedzi: to już nie jest cytat.
    quote = Quote(text="Oddaję zawsze, bo automat jest w sklepie obok domu", speaker="Przechodzień 1", timecode_seconds=12)
    assert check_quote(quote, transcript).status == QuoteStatus.NOT_FOUND


def test_partial_word_does_not_match(transcript):
    quote = Quote(text="obok do", speaker="Przechodzień 1", timecode_seconds=12)
    assert check_quote(quote, transcript).status == QuoteStatus.NOT_FOUND


def test_ellipsis_allows_fragments_in_order(transcript):
    quote = Quote(text="Czasem nie działa, […] trwa to minutę", speaker="Przechodzień 1", timecode_seconds=18)
    assert check_quote(quote, transcript).status == QuoteStatus.VERIFIED


def test_ellipsis_fragments_in_wrong_order_are_rejected(transcript):
    quote = Quote(text="trwa to minutę... Czasem nie działa", speaker="Przechodzień 1", timecode_seconds=18)
    assert check_quote(quote, transcript).status == QuoteStatus.NOT_FOUND


def test_quote_may_continue_into_next_segment_of_same_speaker(transcript):
    quote = Quote(text="obok domu. Czasem nie działa", speaker="Przechodzień 1", timecode_seconds=12)
    assert check_quote(quote, transcript).status == QuoteStatus.VERIFIED


def test_wrong_speaker_is_reported(transcript):
    quote = Quote(text="Ja nie oddaję", speaker="Przechodzień 1", timecode_seconds=31)
    assert check_quote(quote, transcript).status == QuoteStatus.SPEAKER_MISMATCH


def test_wrong_timecode_reports_actual_position(transcript):
    check = check_quote(Quote(text="Ja nie oddaję", speaker="Przechodzień 2", timecode_seconds=5), transcript)
    assert check.status == QuoteStatus.TIMECODE_MISMATCH
    assert check.found_at_seconds == 31


def test_claim_without_evidence_fails(draft, transcript):
    draft.claims.append(Claim(statement="Kaucja wynosi 50 gr"))
    report = verify_draft(draft, transcript)
    assert not report.ok
    assert report.claims[0].claim_index == 1


def test_claim_with_timecode_outside_transcript_fails(draft, transcript):
    draft.claims[0].evidence_timecodes = [3600]
    assert not verify_draft(draft, transcript).ok


def test_inline_quote_in_paragraph_is_checked(draft, transcript):
    draft.sections[0].paragraphs.append("Ktoś dodał: „Kaucja to najgorszy pomysł tego rządu”.")
    report = verify_draft(draft, transcript)
    assert not report.ok
    assert [q.text for q in report.inline_quotes if not q.verified] == ["Kaucja to najgorszy pomysł tego rządu"]


def test_inline_quote_in_title_must_be_exact(draft, transcript):
    draft.title_options[1] = "„Za trzydzieści groszy nie będę stać w kolejce”. Sonda"
    assert not verify_draft(draft, transcript).ok


def test_short_quoted_names_are_not_treated_as_quotes(draft, transcript):
    draft.lead = "Program „Czyste Powietrze” nie dotyczy kaucji."
    assert verify_draft(draft, transcript).ok


def test_problems_lists_all_failures(draft, transcript):
    draft.quotes.append(Quote(text="Nie ma tego", speaker="X", timecode_seconds=5))
    draft.claims.append(Claim(statement="Bez źródła"))
    problems = verify_draft(draft, transcript).problems()
    assert len(problems) == 2
