# Kontekst projektu

**Źródło założeń:** [`automation-ops-intelligence/ideas/editorial-video-to-article.md`](https://github.com/BartekGrk/automation-ops-intelligence/blob/main/ideas/editorial-video-to-article.md) (zapis z 2026-10-18). Ten plik nie powiela specyfikacji. Zapisuje decyzje podjęte przy implementacji i stan prac.

## Stan (2026-10-19)

Zrealizowane: Faza 1 w całości, większość Fazy 2 (profil stylu, ewaluacja, śledzenie źródeł cytatów) oraz z sekcji „Później”: ASR z pliku wideo i pobieranie z linku. Na prośbę użytkownika proces działa **automatycznie od nagrania do zatwierdzonego tekstu** w stylu WP Finanse. Testy: `python -m pytest -q`.

**Sprawdzone na danych:** automat od dźwięku (syntetyczne nagrania Piper w `examples/eval`): WER transkrypcji 3,7–7,7% (`medium`), 15/15 cytatów poprawnych, wszystkie drafty przeszły weryfikację w trybie demonstracyjnym. Przepływ UI sprawdzony w przeglądarce (Playwright).

**Nie sprawdzono:**
- jakości tekstów Claude na realnych wywołaniach: w środowisku budowy nie było klucza API, a testy sprawdzają parametry wywołania i obsługę odpowiedzi na atrapie;
- prawdziwych nagrań WP Finanse. Wideo WP działa w ich własnym odtwarzaczu bez publicznego pliku, a pobieranie z YouTube zwróciło tu 403 (ochrona przed botami po stronie YouTube, której nie obchodzimy).

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
11. **Automat:** zadania w tle (jeden wątek) z etapami zapisywanymi w bazie. Przerwane restartem są oznaczane jako błąd, a ponowienie startuje od zapisanego pliku albo linku. Zatwierdzenie następuje tylko przy pozytywnej weryfikacji. O trybie decyduje analiza, a redaktor może go wymusić.
12. **ASR lokalnie** (faster-whisper `medium`, VAD): nagranie nie trafia do zewnętrznych usług. `medium` zamiast `small`, bo w procesie bez człowieka błąd ASR może trafić do cytatu (pomiar: `small` 1,5 min, `medium` 3,8 min na 4,4 min nagrania).
13. **Mówcy** przypisywani przez model na podstawie treści. Bez modelu (tryb demonstracyjny) transkrypcja jest bez mówców, a parser nie traktuje wtedy „Słowo: …” jako etykiety mówcy.
14. **Styl WP Finanse** z analizy 24 tekstów wideo. Profil opisuje format (długości, śródtytuły, akapity dialogowe), a progi służą też do nieblokujących ostrzeżeń o formacie. Model AI dostaje te ostrzeżenia do poprawki.
15. **Akapit z myślnikiem** („- treść - mówi X”) to zawsze cytat i jest sprawdzany bez minimalnej długości. Cytaty w cudzysłowie sprawdzamy od 4 słów (krótsze to zwykle nazwy własne).
16. **Odporność na odpowiedzi modelu:** luźny schemat draftu (liczba tytułów normalizowana po stronie aplikacji), jedno ponowienie przy błędzie walidacji, mechaniczna korekta timecode'u cytatu, którego tekst i mówca się zgadzają.
17. **Bezpieczeństwo:** limit rozmiaru pliku, blokada linków do adresów wewnętrznych (bez sprawdzania przekierowań – ograniczenie), pliki zapisywane pod nazwą nadaną przez serwer.
18. **Cytaty w tekście modelu przez znaczniki:** model nie przepisuje cytatów ani przypisań. Wstawia `[[cytat:N]]` i akapity `[[wypowiedź:N]]` odwołujące się do listy cytatów. Pipeline rozwija je z transkrypcją: treść pochodzi z listy (zweryfikowana dosłownie), a przypisanie w akapicie dialogowym z etykiety segmentu, w którym cytat znaleziono, nie z deklaracji modelu. Poprawka dostaje wersję ze znacznikami. Model pisze sam jedynie zdanie wokół `[[cytat:N]]`, więc dodatkowa kontrola ostrzega, gdy w zdaniu z cytatem pada nazwisko innego znanego mówcy, a automat wtedy nie zatwierdza. Nie wykryje to wymyślonej osoby spoza nagrania ani przypisania w transkrypcji bez mówców. Heurystyczny rozbiór akapitów z myślnikiem zostaje jako pomoc przy ręcznej edycji i przy niejasności blokuje.

## Znane ograniczenia

- Weryfikacja porównuje z transkrypcją, a ta pochodzi z ASR. Błąd rozpoznania mowy (np. „atak kredytu” zamiast „rata kredytu”) przejdzie do cytatu jako „zgodny z transkrypcją”. Zabezpieczeniem są `requires_verification`, uwagi o mówcach i odtwarzacz z przeskokiem do timecode.
- Przypisanie mówców przez model może się mylić; niepewne segmenty trafiają do uwag.
- Parafraza przypisana złej osobie („Jan Kowalski twierdzi, że…”) nie jest wykrywana – kontrola dotyczy cytatów.
- Nie wykrywamy parafrazy z błędnym sensem: sprawdzamy dosłowność cytatów i źródła tez, nie interpretację.
- Brak analizy obrazu (napisy, wykresy, dokumenty w kadrze) i integracji z CMS.
- Jeden użytkownik, bez logowania. Zadania wykonywane po kolei.

## Następny krok (jeden)

**Pilot z kluczem API na publicznych nagraniach:** uruchomić `python scripts/evaluate.py <katalog> --from-audio` z Claude na 5–10 publicznych nagraniach WP Finanse (pliki od redakcji lub z kanału, jeśli dostępne), porównać z opublikowanymi tekstami, uzupełnić ocenę redaktora w raporcie i dostroić prompty (`app/engines/prompts.py`) oraz profil (`app/styles/wp_finanse.json`).

Otwarte pytania do redakcji (pola CMS, przykładowe pary, polityka danych) znajdują się w sekcji 8 specyfikacji.
