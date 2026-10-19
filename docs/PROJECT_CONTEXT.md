# Kontekst projektu

**Źródło założeń:** [`automation-ops-intelligence/ideas/editorial-video-to-article.md`](https://github.com/BartekGrk/automation-ops-intelligence/blob/main/ideas/editorial-video-to-article.md) (zapis z 2026-10-18). Ten plik nie powiela specyfikacji; zapisuje decyzje podjęte przy implementacji i stan prac.

## Kluczowe zasady ze specyfikacji

- Najpierw uporządkowany materiał dowodowy (cytaty, tezy z timecode), dopiero potem pisanie.
- Zerowa tolerancja na sfabrykowane cytaty; relacja rozmówcy ≠ potwierdzony fakt.
- Dwa tryby wyjścia (SHORT/FULL) na wspólnej warstwie ekstrakcji; tryb wybiera redaktor.
- Bez agentów, bazy wektorowej i fine-tuningu na start. Styl jako jawny `EditorialStyleProfile` (Faza 2).
- Tylko dane publiczne lub syntetyczne.

## Decyzje implementacyjne (E1, 2026-10-19)

1. **Stack jak w `automation-ops-intelligence`:** Python, FastAPI, Pydantic v2, pytest. Pydantic daje JSON Schema dla przyszłych wywołań LLM ze structured output.
2. **Weryfikacja cytatów jest deterministyczna, bez LLM.** Model może się mylić także przy sprawdzaniu, a porównanie tekstu jest tanie i powtarzalne. Normalizacja: wielkość liter, interpunkcja, białe znaki. Dopasowanie na granicach słów; skrót `[…]` dozwolony przy zachowaniu kolejności fragmentów; cytat może obejmować do 2 kolejnych segmentów tego samego mówcy.
3. **Statusy cytatu:** `VERIFIED`, `SPEAKER_MISMATCH`, `TIMECODE_MISMATCH` (z faktyczną pozycją), `NOT_FOUND`.
4. **Eksport blokowany przy błędach** (`/drafts/render` → 409). Markdown zawiera oddzielną sekcję kontrolną (cytaty, tezy, uwagi), która nie jest przeznaczona do CMS.
5. **Format transkrypcji:** `[hh:mm:ss] Mówca: tekst` lub `[mm:ss] tekst`; linie bez znacznika są kontynuacją poprzedniego segmentu.

## Znane ograniczenia

- Weryfikacja sprawdza zgodność z *transkrypcją*, nie z nagraniem. Błędy ASR (kwoty, nazwiska) przechodzą dalej — dlatego `requires_verification` i kontrola redaktora.
- Parafraza w treści artykułu (poza listą `quotes`) nie jest wykrywana; draft musi wymieniać każdy cytat w `quotes`.
- Brak sprawdzenia, czy cytaty w akapitach są tymi samymi co w `quotes`.

## Następny krok (jeden)

**E2 – ekstrakcja materiału dowodowego przez LLM:** transkrypcja → `EvidencePack` (wątki, cytaty z timecode, tezy z `requires_verification`) przez structured output zgodny z modelami Pydantic, z automatyczną weryfikacją `check_quote` na wyjściu modelu. Klucz API ze zmiennej środowiskowej; testy na atrapie klienta, bez wywołań sieciowych.

Otwarte pytania do redakcji (pola CMS, przykładowe pary wideo + artykuł, polityka danych) — sekcja 8 specyfikacji.
