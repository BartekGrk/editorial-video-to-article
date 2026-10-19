# Kontekst projektu

**Źródło założeń:** [`automation-ops-intelligence/ideas/editorial-video-to-article.md`](https://github.com/BartekGrk/automation-ops-intelligence/blob/main/ideas/editorial-video-to-article.md) (zapis z 2026-10-18). Ten plik nie powiela specyfikacji. Zapisuje decyzje podjęte przy implementacji i stan prac.

## Stan (2026-10-19)

Zrealizowane: Faza 1 (prototyp) w całości oraz część Fazy 2 (profil stylu, ewaluacja, śledzenie źródeł cytatów w UI). Działa w trybie demonstracyjnym i z Claude. Testy: `python -m pytest -q`.

**Nie sprawdzono na prawdziwym API:** prompty i jakość draftów Claude nie zostały ocenione na realnych wywołaniach, bo w środowisku budowy nie było klucza. Testy sprawdzają parametry wywołania i obsługę odpowiedzi na atrapie klienta.

## Kluczowe zasady ze specyfikacji

- Najpierw uporządkowany materiał dowodowy, potem pisanie.
- Zerowa tolerancja na sfabrykowane cytaty; relacja rozmówcy ≠ potwierdzony fakt.
- SHORT/FULL na wspólnej warstwie ekstrakcji; tryb wybiera redaktor.
- Bez agentów, bazy wektorowej i fine-tuningu. Styl jako jawny `EditorialStyleProfile`.
- Tylko dane publiczne lub syntetyczne. Brak automatycznej publikacji.

## Decyzje implementacyjne

1. **Stack** taki sam jak w `automation-ops-intelligence`: Python, FastAPI, Pydantic v2, pytest. Dodatkowo SQLite (stdlib) oraz frontend w czystym HTML/JS, bez frameworka i kroku budowania.
2. **LLM:** Claude (`claude-opus-5-5`) przez `client.beta.messages.parse` ze structured output (modele Pydantic), adaptacyjnym myśleniem (effort `medium` dla analizy i profilu, `high` dla draftu), cache'owaniem promptu i serwerowym fallbackiem przy odmowie. Każdy krok to jedno wywołanie, a kolejność kroków kontroluje kod.
3. **Tryb demonstracyjny** (`HeuristicEngine`) ma ten sam interfejs co silnik Claude. Pozwala używać aplikacji bez klucza i testować offline.
4. **Weryfikacja jest deterministyczna, bez LLM:** normalizacja wielkości liter, interpunkcji i białych znaków, dopasowanie na granicach słów, dozwolony skrót `[…]` przy zachowaniu kolejności fragmentów, cytat może obejmować do 3 kolejnych segmentów tego samego mówcy. Statusy: `VERIFIED`, `SPEAKER_MISMATCH`, `TIMECODE_MISMATCH`, `NOT_FOUND`.
5. **Cytaty w tekście:** wszystko w „…”, “…” lub "…" od 4 słów w tytułach, leadzie i akapitach musi występować w transkrypcji. Krótsze frazy traktujemy jak nazwy własne.
6. **Sanityzacja materiału dowodowego:** cytaty nieobecne w transkrypcji są odrzucane i trafiają do luk. Dla obecnych mówcę i timecode bierzemy z transkrypcji.
7. **Jedna runda poprawki** draftu przy błędach weryfikacji. Jeśli to nie pomoże, redaktor widzi listę problemów, a zatwierdzenie i eksport pozostają zablokowane.
8. **Zgoda na zewnętrzne API** zapisywana jest dla każdego materiału. Silnik zewnętrzny bez zgody zwraca 403.
9. **Profile stylu** przechowują tylko opis formatu, nie teksty artykułów.
10. **Zmiana transkrypcji** kasuje analizę i draft, bo odnosiły się do starego tekstu.

## Znane ograniczenia

- Weryfikacja porównuje z transkrypcją, nie z nagraniem. Błędy ASR (kwoty, nazwiska) przechodzą dalej. Zabezpieczeniem są `requires_verification` i kontrola redaktora.
- Parafraza z błędnym sensem nie jest wykrywana; sprawdzamy dosłowność cytatów i obecność źródeł tez, nie poprawność interpretacji.
- Cytat krótszy niż 4 słowa w cudzysłowie nie jest sprawdzany.
- Brak ASR z pliku wideo, analizy kadrów i integracji z CMS. To elementy z sekcji „Później, opcjonalnie” specyfikacji.
- Prototyp dla jednego użytkownika: bez logowania i bez kontroli współbieżnej edycji.

## Następny krok (jeden)

**Pilot na realnym API:** uruchomić `scripts/evaluate.py` z Claude na 5–10 publicznych parach wideo + artykuł (sekcja 7 specyfikacji), wypełnić ręczną ocenę redaktora w raporcie i na tej podstawie dostroić prompty w `app/engines/prompts.py`.

Otwarte pytania do redakcji (pola CMS, przykładowe pary, polityka danych) znajdują się w sekcji 8 specyfikacji.
