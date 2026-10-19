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


def test_dash_quotes_parsing():
    from app.verification import dash_quotes

    assert dash_quotes("- Ja nie oddaję - mówi rozmówca.") == ["Ja nie oddaję"]
    assert dash_quotes("– Jedno zdanie. - mówi X. - Drugie zdanie tutaj.") == ["Jedno zdanie.", "Drugie zdanie tutaj."]
    assert dash_quotes("- Cytat - mówiła jedna z osób. Narracja dalej - skandowali.") == ["Cytat"]
    assert dash_quotes("Zwykły akapit - z myślnikiem w środku.") == []


def test_dash_quote_paragraph_is_verified(draft, transcript):
    draft.sections[0].paragraphs.append("- Za te trzydzieści groszy nie będę stać w kolejce - mówi przechodzień.")
    assert verify_draft(draft, transcript).ok
    draft.sections[0].paragraphs.append("- Za te pieniądze nigdy nie będę stać w kolejce - dodaje.")
    report = verify_draft(draft, transcript)
    assert not report.ok
    assert [q.text for q in report.inline_quotes if not q.verified] == ["Za te pieniądze nigdy nie będę stać w kolejce"]


def test_format_warnings_do_not_block(draft, transcript):
    full = draft.model_copy(update={"mode": "FULL"})
    report = verify_draft(full, transcript)
    assert report.ok
    assert report.format_warnings  # krótki tekst jako FULL: za mało słów i śródtytułów


def test_format_warnings_for_long_short_text(draft):
    from app.format_check import format_warnings

    draft.lead = "słowo " * 150
    assert any(w.startswith("Tekst pod wideo ma 1") and "słów" in w for w in format_warnings(draft))
    full = draft.model_copy(update={"mode": "FULL"})
    warnings = format_warnings(full)
    assert any("śródtytułów" in w for w in warnings)
    assert any("myślnikiem" in w for w in warnings)


def test_segments_starting_in_same_second_are_all_cited():
    from app.models import Transcript, TranscriptSegment

    t = Transcript(
        segments=[
            TranscriptSegment(start_seconds=5, speaker="A", text="Pierwsze zdanie tutaj jest."),
            TranscriptSegment(start_seconds=5, speaker="B", text="Drugie zdanie od kogoś innego."),
        ]
    )
    first = check_quote(Quote(text="Pierwsze zdanie tutaj jest", speaker="A", timecode_seconds=5), t)
    second = check_quote(Quote(text="Drugie zdanie od kogoś", speaker="B", timecode_seconds=5), t)
    assert (first.status, first.segment_index) == (QuoteStatus.VERIFIED, 0)
    assert (second.status, second.segment_index) == (QuoteStatus.VERIFIED, 1)


def test_short_dash_quote_is_checked(draft, transcript):
    draft.sections[0].paragraphs.append("- Nigdy więcej - mówi rozmówca.")
    assert not verify_draft(draft, transcript).ok


def test_dash_quote_with_inner_number_range_is_fully_checked():
    from app.verification import dash_quotes

    assert dash_quotes("- Ceny wzrosły o 5 - 10 procent i będą rosły dalej - mówi Jan.") == [
        "Ceny wzrosły o 5 - 10 procent i będą rosły dalej"
    ]


def test_other_quote_marks_are_checked(draft, transcript):
    for text in ("Jak mówi „to jest zmyślony cytat ze studia“ ekspert.", "Słowa »to jest zmyślony cytat ze studia« padły."):
        broken = draft.model_copy(update={"lead": text})
        assert not verify_draft(broken, transcript).ok


def test_repair_that_breaks_quotes_keeps_original(transcript):
    from app import pipeline
    from app.engines import DraftRequest, HeuristicEngine, MaterialInput

    class FormatFixer(HeuristicEngine):
        external = True  # silnik "AI": poprawia też format

        def repair_draft(self, request, draft, problems):
            return draft.model_copy(update={"lead": draft.lead + " „Całkowicie zmyślona wypowiedź w poprawce”."})

    engine = FormatFixer()
    material = MaterialInput(title="Sonda", transcript=transcript)
    request = DraftRequest(material=material, evidence=pipeline.analyze(engine, material), mode="FULL")
    result = pipeline.write(engine, request)
    assert result.report.ok and not result.repaired
