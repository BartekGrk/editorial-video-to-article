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


def test_dash_quotes_hardened():
    from app.verification import dash_quotes

    # druga wypowiedź od cyfry po zdaniu narracji
    assert dash_quotes("- Prawdziwy cytat tutaj - mówi Jan. - 500 złotych to za mało.") == [
        "Prawdziwy cytat tutaj",
        "500 złotych to za mało.",
    ]
    # fragment po myślniku bez czasownika mówienia należy do cytatu
    assert dash_quotes("- Prawdziwy cytat - a potem wymyślona reszta.") == ["Prawdziwy cytat - a potem wymyślona reszta."]
    assert dash_quotes("- Płacimy - dodatkowo za wszystko - mówi.") == ["Płacimy - dodatkowo za wszystko"]
    # myślnik w nowej linii akapitu i bez spacji
    assert dash_quotes("Narracja.\n- Wymyślony cytat - mówi Jan.") == ["Wymyślony cytat"]
    assert dash_quotes("-Bez spacji po myślniku - mówi Jan.") == ["Bez spacji po myślniku"]
    # narracja po przypisaniu z cytatem w cudzysłowie i dalszym myślnikiem
    assert dash_quotes("- Cytat - mówiła jedna z osób. „Hasło” - skandowali.") == ["Cytat"]


def test_quoted_heading_is_checked(draft, transcript):
    from app.models import Section

    draft.sections.append(Section(heading="„Rząd kłamie w sprawie podatków”", paragraphs=["Tekst."]))
    assert not verify_draft(draft, transcript).ok


def test_straight_quote_inside_polish_quote(draft, transcript):
    draft.lead = 'Tekst „Rząd nas okłamał, "Kowalski" mówił to wiele razy” koniec.'
    assert not verify_draft(draft, transcript).ok


def test_fragmented_ellipsis_is_rejected(transcript):
    q = Quote(text="Oddaję … automat … sklepie … domu", speaker="Przechodzień 1", timecode_seconds=12)
    check = check_quote(q, transcript)
    assert check.status == QuoteStatus.NOT_FOUND and "rozdrobniony" in check.message


def test_undeclared_inline_quote_gives_attribution_warning(draft, transcript):
    draft.sections[0].paragraphs.append("- Słyszałam, że w zeszłym miesiącu oddano miliard butelek - mówi rozmówczyni.")
    report = verify_draft(draft, transcript)
    assert report.ok and not report.strict_ok
    assert "Przechodzieńka 3" in report.attribution_warnings[0]
    draft.quotes.append(Quote(text="Słyszałam, że w zeszłym miesiącu oddano miliard butelek", speaker="Przechodzieńka 3", timecode_seconds=44))
    assert verify_draft(draft, transcript).strict_ok


def test_inline_quote_declared_with_wrong_speaker_warns(draft, transcript):
    draft.quotes[0] = draft.quotes[0].model_copy(update={"speaker": "Przechodzień 2"})
    report = verify_draft(draft, transcript)
    assert report.attribution_warnings


def test_third_review_regressions(draft, transcript):
    from app.verification import dash_quotes, extract_inline_quotes

    # akapit dialogowy od wielokropka lub cudzysłowu nadal jest cytatem
    assert dash_quotes("- …i wtedy rząd obniżył podatki - mówi Jan.") == ["…i wtedy rząd obniżył podatki"]
    assert dash_quotes("- “Rząd obniżył podatki” - mówi X.") == ["“Rząd obniżył podatki”"]
    # lista od małej litery w dalszych liniach to nie cytaty
    assert dash_quotes("Najważniejsze zmiany:\n- wyższy próg podatkowy\n- niższa stawka VAT") == []
    # przypisanie formą przeszłą i samym nazwiskiem
    assert dash_quotes("- Inflacja spada - podkreślał Jan Kowalski.") == ["Inflacja spada"]
    assert dash_quotes("- Inflacja spada - Jan Kowalski, ekonomista.", ["Jan Kowalski"]) == ["Inflacja spada"]
    # dwa cytaty w jednym akapicie nie sklejają się
    draft.lead = 'Jak mówi Jan, „inflacja spada powoli ale stale". Dodał też „ceny prądu wzrosną w przyszłym roku”.'
    quoted = [q for _, q, _, _ in extract_inline_quotes(draft) if "inflacja" in q or "ceny" in q]
    assert quoted == ["inflacja spada powoli ale stale", "ceny prądu wzrosną w przyszłym roku"]


def test_asr_pause_ellipsis_in_verbatim_quote():
    from app.models import Transcript, TranscriptSegment

    t = Transcript(segments=[TranscriptSegment(start_seconds=0, speaker="A", text="No cóż... trzeba po prostu płacić te rachunki co miesiąc.")])
    q = Quote(text="No cóż... trzeba po prostu płacić te rachunki co miesiąc", speaker="A", timecode_seconds=0)
    assert check_quote(q, t).status == QuoteStatus.VERIFIED


def test_repeated_words_use_declared_speaker():
    from app.models import ArticleDraft, Transcript, TranscriptSegment

    t = Transcript(
        segments=[
            TranscriptSegment(start_seconds=1, speaker="Prowadzący", text="Czy ceny prądu wzrosną w przyszłym roku znacząco?"),
            TranscriptSegment(start_seconds=10, speaker="Jan Kowalski", text="Ceny prądu wzrosną w przyszłym roku znacząco."),
        ]
    )
    d = ArticleDraft(
        mode="SHORT",
        title_options=["Ceny prądu", "Prąd podrożeje"],
        lead="Ekonomista ostrzega: „ceny prądu wzrosną w przyszłym roku znacząco”.",
        sections=[],
        quotes=[Quote(text="Ceny prądu wzrosną w przyszłym roku znacząco", speaker="Jan Kowalski", timecode_seconds=10)],
    )
    report = verify_draft(d, t)
    assert report.strict_ok
    assert report.inline_quotes[0].speaker == "Jan Kowalski"


def test_fourth_review_dash_bypasses():
    from app.models import ArticleDraft, Transcript, TranscriptSegment
    from app.verification import dash_quotes

    t = Transcript(segments=[TranscriptSegment(start_seconds=0, speaker="Jan", text="Ceny są za wysokie.")])
    for paragraph in (
        "Jan mówi.\n- rząd ukradł nam miliony złotych - mówi Jan.",
        "- Ceny są za wysokie - mówiąc szczerze, rząd nas oszukał i ukradł miliony.",
        "- Ceny są za wysokie - Donald Tusk, premier, ukradł nam miliony złotych.",
    ):
        d = ArticleDraft(mode="SHORT", title_options=["A b c d", "E f g h"], lead=paragraph, sections=[])
        assert not verify_draft(d, t).ok, paragraph
    assert dash_quotes("- Ceny są za wysokie - Jan Kowalski, ekonomista z Warszawy.", ["Jan Kowalski, ekonomista"]) == [
        "Ceny są za wysokie"
    ]


def test_unlabelled_and_labelled_repeat_can_be_declared():
    from app.models import ArticleDraft, Transcript, TranscriptSegment

    text = "Za te trzydzieści groszy nie będę stać."
    t = Transcript(
        segments=[TranscriptSegment(start_seconds=0, text=text), TranscriptSegment(start_seconds=10, speaker="Jan", text=text)]
    )
    d = ArticleDraft(mode="SHORT", title_options=["A b c d", "E f g h"], lead=f"Słyszymy: „{text}”", sections=[])
    report = verify_draft(d, t)
    assert "Jan" in report.attribution_warnings[0]
    d.quotes.append(Quote(text=text, speaker="Jan", timecode_seconds=10))
    assert verify_draft(d, t).strict_ok


def test_fifth_review_dash_rules():
    from app.models import ArticleDraft, Transcript, TranscriptSegment
    from app.verification import dash_quotes

    t = Transcript(segments=[TranscriptSegment(start_seconds=0, speaker="Jan Kowalski", text="Ceny są za wysokie.")])
    declared = [Quote(text="Ceny są za wysokie", speaker="Jan Kowalski", timecode_seconds=0)]
    fabricated = (
        "- Ceny są za wysokie - mówi Jan Kowalski i dodaje: - Rząd ukradł nam miliony złotych.",
        "- Ceny są za wysokie - mówi Jan Kowalski - Rząd ukradł nam miliony złotych.",
        "- Ceny są za wysokie - mówi Jan Kowalski. I dodaje - Rząd ukradł nam miliony złotych.",
        "Narracja.\n- rząd ukradł nam miliony złotych - Jan Kowalski, rolnik.",
        "- Ceny są za wysokie - Panie Premierze, oddaj nam nasze miliony!",
    )
    for paragraph in fabricated:
        d = ArticleDraft(mode="SHORT", title_options=["A b c d", "E f g h"], lead=paragraph, sections=[], quotes=declared)
        assert not verify_draft(d, t).ok, paragraph
    # długie funkcje znanego mówcy nie są doklejane do cytatu
    for role in ("ekspert ds. energetyki.", "ekonomista Banku Pekao S.A.", "główny ekonomista banku w Warszawie."):
        d = ArticleDraft(
            mode="SHORT",
            title_options=["A b c d", "E f g h"],
            lead=f"- Ceny są za wysokie - Jan Kowalski, {role}",
            sections=[],
            quotes=declared,
        )
        assert verify_draft(d, t).strict_ok, role
    assert dash_quotes("- A b c - mówi X i dodaje: - D e f.") == ["A b c", "D e f."]


def test_wrong_speaker_not_excused_by_labelled_repeat_only():
    from app.models import ArticleDraft, Transcript, TranscriptSegment

    text = "Za te trzydzieści groszy nie będę stać."
    t = Transcript(
        segments=[
            TranscriptSegment(start_seconds=0, speaker="Jan", text=text),
            TranscriptSegment(start_seconds=10, speaker="Prowadzący", text=text),
        ]
    )
    d = ArticleDraft(
        mode="SHORT",
        title_options=["A b c d", "E f g h"],
        lead=f"- {text} - mówi Anna.",
        sections=[],
        quotes=[Quote(text=text, speaker="Anna", timecode_seconds=0)],
    )
    assert not verify_draft(d, t).strict_ok


def test_ambiguous_unlabelled_original_and_labelled_repeat_is_blocked():
    # Te same słowa w segmencie bez mówcy i u prowadzącego: nie da się potwierdzić autora –
    # blokujemy (w automacie wszystkie segmenty mają etykiety, więc dotyczy to ręcznych transkrypcji).
    from app.models import ArticleDraft
    from app.transcript import parse_transcript

    t = parse_transcript(
        "[00:00] Nie stać nas na nawozy w tej cenie, mówię to otwarcie.\n"
        "[00:08] Prowadzący: Nie stać nas na nawozy w tej cenie – tak pan mówi. Dlaczego?"
    )
    d = ArticleDraft(
        mode="SHORT",
        title_options=["A b c d", "E f g h"],
        lead="- Nie stać nas na nawozy w tej cenie - mówi Jan Kowalski.",
        sections=[],
        quotes=[Quote(text="Nie stać nas na nawozy w tej cenie", speaker="Jan Kowalski", timecode_seconds=0)],
    )
    assert not verify_draft(d, t).strict_ok


def test_sixth_review_cases():
    from app.models import ArticleDraft, Transcript, TranscriptSegment
    from app.transcript import parse_transcript

    labelled = Transcript(segments=[TranscriptSegment(start_seconds=5, speaker="dr Jan Kowalski, rolnik", text="Nie stać nas na nawozy w tej cenie.")])
    declared = [Quote(text="Nie stać nas na nawozy w tej cenie", speaker="dr Jan Kowalski, rolnik", timecode_seconds=5)]
    ok_paragraphs = (
        "- Nie stać nas na nawozy w tej cenie - mówi Jan Kowalski, który – jak sam przyznaje – od 30 lat prowadzi gospodarstwo.",
        "- Nie stać nas na nawozy w tej cenie - mówi Jan Kowalski. W jego gminie ceny wzrosły o 20 - 30 procent.",
        "- Nie stać nas na nawozy w tej cenie - Jan Kowalski, rolnik.",
    )
    for paragraph in ok_paragraphs:
        d = ArticleDraft(mode="SHORT", title_options=["A b c d", "E f g h"], lead=paragraph, sections=[], quotes=declared)
        assert verify_draft(d, labelled).strict_ok, paragraph
    # transkrypcja ASR bez mówców: samo nazwisko bez czasownika nie jest rozpoznawane jako przypisanie
    unlabelled = parse_transcript("[00:05] Nie stać nas na nawozy w tej cenie.", with_speakers=False)
    d = ArticleDraft(
        mode="SHORT",
        title_options=["A b c d", "E f g h"],
        lead="- Nie stać nas na nawozy w tej cenie - Jan Kowalski, rolnik.",
        sections=[],
        quotes=[Quote(text="Nie stać nas na nawozy w tej cenie", timecode_seconds=5)],
    )
    assert not verify_draft(d, unlabelled).ok
    d.lead = "- Nie stać nas na nawozy w tej cenie - mówi Jan Kowalski, rolnik."
    assert verify_draft(d, unlabelled).ok
    # lista z myślnikami dostaje podpowiedź
    d = ArticleDraft(mode="SHORT", title_options=["A b c d", "E f g h"], lead="Zmiany:\n- Kwota wolna wzrośnie", sections=[])
    assert any("punktorów" in p for p in verify_draft(d, labelled).problems())


def test_quote_markers_expand_to_verified_text_and_attribution(transcript, draft):
    from app.quote_markers import expand_markers

    marked = draft.model_copy(
        update={
            "lead": "Rozmówcy są podzieleni: [[cytat:1]].",
            "sections": [draft.sections[0].model_copy(update={"paragraphs": ["[[wypowiedź:2]]", "[[wypowiedź:9]]"]})],
        }
    )
    expanded = expand_markers(marked)
    assert expanded.lead == "Rozmówcy są podzieleni: „Oddaję, bo automat jest w sklepie obok domu”."
    assert expanded.sections[0].paragraphs[0] == "- Za te trzydzieści groszy nie będę stać w kolejce - mówi Przechodzień 2."
    report = verify_draft(expanded, transcript)
    assert not report.ok and report.unresolved_markers == ["[[wypowiedź:9]]"]
    expanded.sections[0].paragraphs.pop()
    assert verify_draft(expanded, transcript).strict_ok


def test_other_known_speaker_named_next_to_quote():
    from app.models import ArticleDraft, Transcript, TranscriptSegment

    t = Transcript(
        segments=[
            TranscriptSegment(start_seconds=5, speaker="Jan Kowalski, rolnik", text="Za tonę pszenicy dostajemy dziś mniej niż rok temu."),
            TranscriptSegment(start_seconds=12, speaker="Anna Nowak, minister", text="Pracujemy nad dopłatami."),
        ]
    )
    quote = Quote(text="Za tonę pszenicy dostajemy dziś mniej niż rok temu", speaker="Jan Kowalski, rolnik", timecode_seconds=5)

    def draft(lead):
        return ArticleDraft(mode="SHORT", title_options=["A b c d", "E f g h"], lead=lead, sections=[], quotes=[quote])

    bad = (
        "Minister Anna Nowak mówi wprost: „Za tonę pszenicy dostajemy dziś mniej niż rok temu”.",
        "- Za tonę pszenicy dostajemy dziś mniej niż rok temu - mówi Anna Nowak, minister.",
    )
    for lead in bad:
        report = verify_draft(draft(lead), t)
        assert not report.strict_ok and "Anna Nowak" in report.attribution_warnings[0], lead
    good = (
        "Jan Kowalski mówi wprost: „Za tonę pszenicy dostajemy dziś mniej niż rok temu”. Anna Nowak zapowiada dopłaty.",
        "- Za tonę pszenicy dostajemy dziś mniej niż rok temu - mówi Jan Kowalski, rolnik.",
    )
    for lead in good:
        assert verify_draft(draft(lead), t).strict_ok, lead


def test_marker_variants_and_inline_dialogue_marker(transcript, draft):
    from app.quote_markers import expand_markers

    marked = draft.model_copy(
        update={"lead": "Słyszymy: [[Cytat 1]] oraz [[wypowiedz:2]].", "sections": [draft.sections[0].model_copy(update={"paragraphs": ["[[ wypowiedz : 2 ]]"]})]}
    )
    expanded = expand_markers(marked, transcript)
    assert "[[" not in expanded.lead and "„Za te trzydzieści groszy" in expanded.lead
    assert expanded.sections[0].paragraphs[0].startswith("- Za te trzydzieści groszy")
    leftover = draft.model_copy(update={"lead": "Tekst [[cytaty]]."})
    assert verify_draft(leftover, transcript).unresolved_markers == ["[[cytaty]]"]


def test_dialogue_attribution_comes_from_transcript_not_model(transcript, draft):
    from app.quote_markers import expand_markers

    wrong = draft.quotes[1].model_copy(update={"speaker": "Anna Nowak, minister"})
    marked = draft.model_copy(update={"quotes": [draft.quotes[0], wrong], "lead": "[[wypowiedź:2]]", "sections": []})
    assert expand_markers(marked, transcript).lead == "- Za te trzydzieści groszy nie będę stać w kolejce - mówi Przechodzień 2."
