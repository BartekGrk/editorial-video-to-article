# E1 – parser transkrypcji i weryfikacja cytatów

## Cel i kontrakt

Draft może zawierać cytat tylko wtedy, gdy ta wypowiedź istnieje w transkrypcji, u wskazanego mówcy i w okolicy wskazanego timecode. Inaczej `/drafts/render` zwraca 409 z raportem.

## Od czego zacząć czytanie

1. `app/models.py` – kształt danych: `Transcript`, `Quote`, `Claim`, `ArticleDraft`.
2. `app/transcript.py` – `parse_transcript`: tekst → segmenty.
3. `app/verification.py` – `check_quote` i `verify_draft`.
4. `tests/test_verification.py` – przypadki, które opisują zachowanie.

Przepływ: `POST /drafts/render` → `_parse` (parser) → `verify_draft` → dla każdego cytatu `check_quote` → jeśli `report.ok`, `render_markdown`.

## Najważniejsze decyzje

- `normalize` usuwa interpunkcję i wielkość liter, ale **nie** usuwa słów: dopisane „zawsze” daje `NOT_FOUND`.
- `_contains_in_order` dokleja spacje na brzegach, żeby „obok do” nie pasowało do „obok domu”.
- `_segment_at` szuka ostatniego segmentu, który zaczął się przed timecode — timecode cytatu nie musi trafić co do sekundy w początek segmentu.

## Dokumentacja

- Pydantic: [Models](https://docs.pydantic.dev/latest/concepts/models/), [Fields – constraints](https://docs.pydantic.dev/latest/concepts/fields/), [computed_field](https://docs.pydantic.dev/latest/concepts/fields/#the-computed_field-decorator)
- FastAPI: [Request Body](https://fastapi.tiangolo.com/tutorial/body/), [Handling Errors](https://fastapi.tiangolo.com/tutorial/handling-errors/), [Testing](https://fastapi.tiangolo.com/tutorial/testing/)
- Python: [re](https://docs.python.org/3/library/re.html)

## Sprawdzenie

```bash
python -m pytest -q
```

Oczekiwany wynik: `25 passed`.

## Ćwiczenie: przewidź → zmień → uruchom → wyjaśnij

Zmień `_MAX_FOLLOWING_SEGMENTS` w `app/verification.py` na `0`. Który test przestanie przechodzić i dlaczego? Jaki realny przypadek z transkrypcji ASR to pokazuje?

## Czego testy nie sprawdzają

- zgodności transkrypcji z nagraniem (błędy ASR);
- cytatów wplecionych w akapity, a niewymienionych w `quotes`;
- jakości redakcyjnej tekstu (tytuł, lead, styl).
