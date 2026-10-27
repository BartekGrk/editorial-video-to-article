# Roadmapa: od prototypu do narzędzia do codziennej pracy

**Cel:** aplikacja, z którą jedna dziennikarka pracuje na co dzień. Wgrywa nagranie, dostaje
transkrypcję, którą sprawdza tylko w kilku wskazanych miejscach, i draft w swoim stylu, który
poprawia coraz mniej. Działa na domowym komputerze (Windows 11, Docker, karta NVIDIA), dostęp
przez Tailscale. Później ewentualnie przeniesienie na AWS.

**Miara sukcesu:** czas od wgrania nagrania do tekstu gotowego do CMS oraz odsetek tekstu, który
redaktorka zmienia w drafcie (maleje z kolejnymi tekstami).

Zadania oznaczone **[człowiek]** wymagają Bartka lub redaktorki (sprzęt, klucz API, ocena tekstu).
Pozostałe może wykonać agent – zasady na końcu dokumentu.

---

## M0. Uruchomienie u Bartka [człowiek]

- [ ] **M0.1** Instalacja według [INSTALACJA-WINDOWS.md](INSTALACJA-WINDOWS.md), kroki 1–4.
  Gotowe, gdy `docker compose logs app | findstr ASR` pokazuje `na GPU (cuda)` albo wiadomo,
  że karta nie działa (wtedy zadanie M1.7).
- [ ] **M0.2** Klucz API w osobnym workspace Anthropic z limitem wydatków; wpis w `.env`.
- [ ] **M0.3** Tailscale na komputerze i urządzeniach redaktorki (krok 6).
- [ ] **M0.4** Pierwsze 3 prawdziwe nagrania przez automat. Zapisać w `docs/PILOT.md`:
  czas transkrypcji, błędy transkrypcji, co redaktorka zmieniła w drafcie.

## M1. Gotowość do codziennej pracy

- [ ] **M1.1 Wersja z AI obok wersji po redakcji.** Przy każdym drafcie z modelu zapisać
  `ai_draft` (niezmieniany przy edycjach) i przy zatwierdzeniu `final_draft`. Policzyć
  `edit_ratio` (odsetek słów zmienionych, np. `difflib` na słowach) i czas od wygenerowania do
  zatwierdzenia. Pokazać w zakładce Eksport.
  *Gotowe gdy:* test API: wygeneruj → edytuj → zatwierdź → `ai_draft` bez zmian, `edit_ratio` > 0.
- [ ] **M1.2 Koszt każdego materiału.** Zapisywać `usage` z odpowiedzi Claude (tokeny wejścia,
  wyjścia, cache) na materiale; w liście materiałów suma za bieżący miesiąc w USD wg cennika
  modelu w `app/engines/pricing.py`.
  *Gotowe gdy:* test silnika na atrapie odpowiedzi z `usage` zapisuje koszt; UI pokazuje sumę.
- [ ] **M1.3 Automatyczna kopia bazy.** Codziennie (wątek w aplikacji, pierwsze uruchomienie po
  starcie) `sqlite3.backup` do `data/backups/editorial-RRRR-MM-DD.db`, przechowywać 14 ostatnich.
  *Gotowe gdy:* test z podmienionym zegarem tworzy kopię i usuwa najstarszą.
- [ ] **M1.4 Porządek z nagraniami.** Pliki nagrań starsze niż `EDITORIAL_MEDIA_RETENTION_DAYS`
  (domyślnie 30) usuwane; transkrypcja i draft zostają, odtwarzacz pokazuje „nagranie usunięte”.
- [ ] **M1.5 Ponowienie przy przeciążeniu API.** Błędy 429/529/5xx: do 3 prób z rosnącą przerwą
  (poza wbudowanymi w SDK), potem czytelny błąd i przycisk „Spróbuj ponownie”.
  *Gotowe gdy:* test z atrapą klienta zwracającą 529 dwa razy kończy się sukcesem.
- [ ] **M1.6 Powiadomienie o gotowym drafcie.** Powiadomienie przeglądarki (Notification API,
  po zgodzie) i licznik w tytule karty, gdy automat skończy materiał.
- [ ] **M1.7 Karta RTX 50 w kontenerze** (tylko jeśli M0.1 pokazało przejście na CPU): dobrać
  wersję `ctranslate2`/obrazu CUDA obsługującą architekturę Blackwell; sprawdzić u Bartka. [człowiek do weryfikacji]

## M2. Transkrypcja, której nie trzeba odsłuchiwać od nowa

- [ ] **M2.1 Słownik nazw.** Ustawienia → słownik (nazwiska, instytucje, skróty: ZUS, NBP,
  „800 plus”). Do Whispera jako `initial_prompt`/`hotwords` razem z nazwami z tytułu i tematu
  materiału.
  *Gotowe gdy:* test sprawdza, że transcriber dostaje słownik; WER na `examples/eval` nie rośnie.
- [ ] **M2.2 Niepewne słowa.** `word_timestamps=True`; zapisać słowa z prawdopodobieństwem
  < 0,6 (próg w ustawieniach) przy segmencie. W panelu transkrypcji podświetlić je;
  kliknięcie odtwarza fragment od 2 s przed słowem. Licznik „do sprawdzenia: N”.
- [ ] **M2.3 Poprawianie pojedynczego segmentu.** Edycja tekstu segmentu w panelu bez kasowania
  draftu: po zapisie tylko ponowna weryfikacja draftu (cytaty, które przestały pasować,
  blokują zatwierdzenie i są wskazane). Zmiana mówcy segmentu – tak samo.
  *Gotowe gdy:* test: zmiana słowa w segmencie, z którego jest cytat → raport pokazuje NOT_FOUND
  dla tego cytatu, pozostałe bez zmian, draft zachowany.
- [ ] **M2.4 Popraw cytat jednym kliknięciem.** Gdy cytat po poprawce segmentu nie pasuje,
  przycisk „Wstaw poprawione brzmienie” podmienia tekst cytatu na fragment z segmentu.
- [ ] **M2.5 Mówcy po głosie (opcjonalnie).** `pyannote.audio` na GPU, włączane zmienną
  `EDITORIAL_DIARIZATION=1` i tokenem Hugging Face; wynik jako podpowiedź dla etykiet z modelu.
  [człowiek: token HF, test na GPU]
- [ ] **M2.6 Pomiar na jej nagraniach.** 3 nagrania z ręcznie poprawioną transkrypcją wzorcową
  w `data/eval/` (poza repo); `scripts/evaluate.py --from-audio` podaje WER. [człowiek: wzorce]

## M3. Jej styl

- [ ] **M3.1 Korpus artykułów.** Zakładka „Mój styl”: dodawanie artykułów (wklejony tekst lub
  plik .txt/.md; tytuł, lead, treść, tryb SHORT/FULL). Tabela `style_examples` w SQLite.
  Teksty tylko lokalnie – nie do repozytorium; w testach syntetyczne.
- [ ] **M3.2 Cechy stylu z korpusu.** Deterministyczne statystyki (`app/style_stats.py`):
  długości tytułu, leadu, akapitów i zdań; typ otwarcia (liczba / cytat / scena / pytanie);
  liczba i długość cytatów; czasowniki przypisania; śródtytuły. Widok z rozkładami.
- [ ] **M3.3 Profil z korpusu.** Przycisk „Zaproponuj profil”: Claude dostaje statystyki i
  próbkę tekstów, zwraca `EditorialStyleProfile`; redaktorka edytuje i zapisuje.
  Profil ma pole `rules: list[str]` (reguły w punktach) używane w prompcie draftu.
- [ ] **M3.4 Przykłady w prompcie.** Do draftu 2–3 artykuły z korpusu: ten sam tryb,
  największe podobieństwo słów (TF-IDF w czystym Pythonie, bez bazy wektorowej), losowanie
  spośród 5 najbliższych. Oznaczone w prompcie jako wzór budowy i tonu, nie źródło faktów.
  *Gotowe gdy:* test wyboru przykładów (tryb, podobieństwo, różne zestawy przy kolejnych wywołaniach).
- [ ] **M3.5 Kontrola powtórzeń.** Fragment 8+ kolejnych słów wspólny z korpusem → ostrzeżenie
  przy drafcie i brak automatycznego zatwierdzenia (`approval_blockers`).
- [ ] **M3.6 Fakty spoza nagrania.** Liczby (z jednostkami) i nazwy własne w drafcie, których nie
  ma w transkrypcji ani w materiale dowodowym → ostrzeżenie „sprawdź źródło”, blokada
  automatycznego zatwierdzenia.
- [ ] **M3.7 Ocena redaktorki.** 5 materiałów: drafty z profilem WP Finanse i z jej profilem,
  ocena na ślepo w `docs/PILOT.md`. [człowiek]

## M4. Pętla uczenia

- [ ] **M4.1 Porównanie wersji.** Widok „Co zmieniłam”: różnice `ai_draft` → `final_draft`
  (dodane/usunięte fragmenty), dla każdego zatwierdzonego materiału.
- [ ] **M4.2 Propozycje reguł.** Przycisk „Czego się nauczyć?” (aktywny od 5 nowych par):
  Claude dostaje pary przed→po i obecne reguły, zwraca listę propozycji (reguła + przykład z
  par). Kolejka propozycji: Przyjmij / Odrzuć / Edytuj; przyjęte trafiają do `rules` profilu.
  Odrzucone zapamiętane, by nie wracały.
- [ ] **M4.3 Pary jako przykłady.** Najbardziej poprawiane fragmenty (przed→po, do 3) w prompcie
  draftu jako „tak redaktorka poprawia” – obok przykładów z M3.4.
- [ ] **M4.4 Postęp.** Wykres `edit_ratio` i czasu redakcji w kolejnych tekstach (dane z M1.1).

## M5. Później

- [ ] **M5.1** Eksport w formacie edytora CMS WP (po ustaleniu pól z redakcją). [człowiek: pola]
- [ ] **M5.2** Analiza kadrów: napisy i wykresy z wideo jako dodatkowy materiał (opcjonalnie).
- [ ] **M5.3** Instrukcja przeniesienia na AWS (EC2 z GPU na żądanie albo CPU, S3, Claude przez AWS).
- [ ] **M5.4** Więcej użytkowników: logowanie, PostgreSQL – dopiero gdy będzie potrzeba.

---

## Zasady pracy agentów

**Kolejność:** pierwsze nieodhaczone zadanie z najniższego niezamkniętego kamienia milowego,
pomijając **[człowiek]**. M2 może iść równolegle z M1. M3.4+ wymaga M3.1; M4 wymaga M1.1.

**Jedno zadanie = jeden commit** (lub kilka małych) na gałęzi roboczej, z odhaczeniem pola
w tym pliku i wpisem decyzji w `docs/PROJECT_CONTEXT.md`, jeśli zapadła.

**Zanim commit:**
1. `python -m pytest -q` – wszystko przechodzi; nowe zachowanie ma test.
2. Zmiana w `app/static/` – testy przeglądarkowe z `tests/e2e/README.md` bez błędów.
3. Zmiana w `Dockerfile`/`requirements.txt` – `docker build` przechodzi.

**Nie wolno:**
- wpisywać do repozytorium kluczy, prawdziwych transkrypcji, nagrań ani artykułów redaktorki
  (wszystko z `data/` zostaje lokalnie; w testach dane syntetyczne);
- publikować czegokolwiek automatycznie ani wysyłać nagrań do usług zewnętrznych bez ustawienia
  włączanego przez użytkownika;
- osłabiać weryfikacji cytatów (każda zmiana w `app/verification.py` z testem, że błędny cytat
  nadal jest wykrywany);
- wywoływać prawdziwego API w testach (atrapa klienta jak w `tests/test_anthropic_engine.py`).

**Gdy decyzja należy do użytkownika** (wygląd, progi, zakres) – przyjąć rozsądne założenie,
zapisać je w sekcji „Pytania” poniżej i iść dalej; nie blokować pętli.

**Po każdym kamieniu milowym:** przegląd kodu (`/code-review high`), poprawki, aktualizacja
README i tego pliku.

### Prompt do pętli

```
/loop Pracuj nad editorial-video-to-article według docs/ROADMAP.md: weź pierwsze nieodhaczone
zadanie bez [człowiek] (z zachowaniem zależności), zaimplementuj z testami, sprawdź według
„Zanim commit”, odhacz, zapisz decyzje, commit i push. Po zamknięciu kamienia milowego zrób
przegląd kodu i popraw błędy. Zatrzymaj pętlę, gdy zostaną tylko zadania [człowiek] albo
pytania wymagające decyzji użytkownika.
```

## Pytania

_(agenci dopisują tu założenia wymagające potwierdzenia)_
