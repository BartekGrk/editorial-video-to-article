# Editorial Video → Article Assistant

Aplikacja webowa, która **automatycznie, od nagrania do gotowego tekstu**, przygotowuje weryfikowalny tekst redakcyjny w stylu WP Finanse w dwóch trybach:

- **SHORT**: tekst pod wideo (2–3 tytuły i jeden akapit);
- **FULL**: pełny artykuł (lead, śródtytuły, cytaty z myślnikiem).

Wejście to plik wideo/audio, link albo gotowa transkrypcja. Każdy cytat jest sprawdzany względem transkrypcji, a tezy wymagające zewnętrznego potwierdzenia są oznaczane `requires_verification`. Tekst z niezweryfikowanym cytatem nie zostanie zatwierdzony ani wyeksportowany. Aplikacja nie publikuje sama: zatwierdzony tekst trafia do CMS przez eksport.

Specyfikacja: [`ideas/editorial-video-to-article.md`](https://github.com/BartekGrk/automation-ops-intelligence/blob/main/ideas/editorial-video-to-article.md) w `automation-ops-intelligence`. Decyzje i ograniczenia: [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md).

## Szybki start

```bash
sudo apt-get install ffmpeg          # transkrypcja nagrań
pip install -r requirements.txt
uvicorn app.main:app                 # http://127.0.0.1:8000
```

**Docker (zalecane na własny komputer/serwer):** `cp .env.example .env`, potem `docker compose up -d --build` (z kartą NVIDIA: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`). Pełna instrukcja dla domowego komputera z Windows, z dostępem przez Tailscale: [docs/INSTALACJA-WINDOWS.md](docs/INSTALACJA-WINDOWS.md).

Bez klucza API aplikacja działa w **trybie demonstracyjnym**: draft powstaje regułowo z dosłownych wypowiedzi, bez AI. Aby drafty pisał Claude:

```bash
export ANTHROPIC_API_KEY=...         # albo: ant auth login
uvicorn app.main:app
```

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `EDITORIAL_ENGINE` | `anthropic`, jeśli jest klucz, w przeciwnym razie `heuristic` | silnik generowania |
| `EDITORIAL_MODEL` | `claude-opus-5-5` | model Claude |
| `EDITORIAL_ASR_MODEL` | `medium` | model faster-whisper (`small` jest 2,5× szybszy, ale popełnia więcej błędów; z kartą graficzną `large-v3`) |
| `EDITORIAL_ASR_DEVICE` | `auto` | `auto` – karta NVIDIA, jeśli działa, inaczej CPU; `cuda`; `cpu` |
| `EDITORIAL_DB` | `data/editorial.db` | baza SQLite |
| `EDITORIAL_MEDIA_DIR` | `data/media` | przesłane i pobrane nagrania |
| `EDITORIAL_MAX_UPLOAD_MB` | `2048` | limit rozmiaru pliku |
| `EDITORIAL_ALLOW_PRIVATE_URLS` | – | `1` zezwala na linki do adresów wewnętrznych |

## Proces automatyczny (od nagrania do tekstu)

Na stronie głównej, w sekcji **„Automatycznie z nagrania”**, podajesz plik wideo/audio albo link (YouTube lub bezpośredni link do pliku). Wszystkie etapy przebiegają bez dalszych kliknięć:

1. pobranie nagrania (yt-dlp) i konwersja dźwięku (ffmpeg);
2. **transkrypcja lokalna** (faster-whisper): nagranie nie opuszcza serwera, a do modelu AI trafia najwyżej tekst;
3. **przypisanie mówców** przez model na podstawie treści (reporter, rozmówcy z nazwiskami, gdy padają w nagraniu);
4. **materiał dowodowy**: wątki, cytaty z timecode, tezy z `requires_verification`, luki;
5. **wybór trybu** SHORT/FULL według wartości informacyjnej materiału (można wymusić);
6. **draft w stylu WP Finanse** (wbudowany profil stylu);
7. **weryfikacja** cytatów (także akapitów dialogowych z myślnikiem) oraz automatyczna korekta timecode'ów; przy błędach albo wyraźnych odstępstwach od formatu model dostaje jedną rundę poprawki;
8. **zatwierdzenie**, jeśli weryfikacja nie wykryła błędów, a draft ma co najmniej jeden cytat i nie jest pustym szablonem (min. 15 słów). Wtedy draft jest gotowy do eksportu (Markdown/HTML) w CMS; w przeciwnym razie zostaje draftem z uwagą „Automat nie zatwierdził: …”.

Postęp etapów widać w aplikacji. Przy błędzie materiał ma status „Błąd” z opisem i przyciskiem „Spróbuj ponownie”. Dla materiału z gotową transkrypcją automat uruchamia przycisk **⚡ Automat**.

Z wiersza poleceń:

```bash
python scripts/auto.py nagranie.mp4 --out wyniki/          # kod wyjścia 0 = weryfikacja OK
```

Aplikacja nie publikuje sama, bo integracja z CMS wymaga zgody redakcji. Gotowy, zweryfikowany tekst kopiuje się z zakładki „Eksport”.

## Styl WP Finanse

Wbudowany profil `wp-finanse` (`app/styles/wp_finanse.json`) opisuje format tekstów wideo WP Finanse na podstawie analizy 24 publicznych tekstów z finanse.wp.pl/tag/wideo:

- **SHORT**: jeden akapit 35–80 słów pod wideo, bez śródtytułów;
- **FULL**: 280–850 słów, lead 30–50 słów, 2–5 śródtytułów, 1–5 cytatów jako akapit „- treść - mówi X, funkcja”;
- tytuł 6–12 słów: teza plus cytat w cudzysłowie.

Profil zawiera tylko opis formatu, bez fraz z artykułów. Odstępstwa od tych zakresów pokazują się jako nieblokujące ostrzeżenia „Format WP Finanse”.

## Praca ręczna

1. **Nowy materiał** („+ Z transkrypcji”): tytuł, kategoria, temat, kąt, link do wideo i transkrypcja `[hh:mm:ss] Mówca: tekst`.
2. **Materiał dowodowy** → **Draft** (tryb, profil, długość, wskazówki) → **edycja**. Każdy zapis uruchamia weryfikację ponownie.
3. **Zatwierdzenie i eksport.**

Kliknięcie timecode w transkrypcji przewija odtwarzacz (YouTube, plik z linku lub przesłany plik) do danego momentu.

## Architektura

```text
app/
  models.py          kontrakty danych (Pydantic) – także JSON Schema dla LLM
  transcript.py      parser transkrypcji
  verification.py    deterministyczna weryfikacja cytatów i tez
  pipeline.py        analyze → write (+ jedna poprawka), linki do wideo
  media.py           pobranie nagrania, ffmpeg, faster-whisper, sklejanie segmentów ASR
  auto.py            automat od nagrania do draftu (bez bazy – używany przez API i CLI)
  workflow.py, jobs.py  zadanie automatu w tle z postępem w bazie
  format_check.py    zgodność z formatem WP Finanse (ostrzeżenia)
  styles/            wbudowane profile stylu
  engines/           AnthropicEngine (Claude, structured output) i HeuristicEngine (demo/testy)
  store.py           SQLite (dokumenty JSON)
  render.py          eksport Markdown / HTML
  evaluation.py      metryki jakości na zestawie przypadków
  main.py            API FastAPI + serwowanie UI
  static/            interfejs (HTML/CSS/JS bez kroku budowania)
scripts/auto.py      automat z wiersza poleceń
scripts/evaluate.py  raport ewaluacji
examples/eval/       syntetyczne przypadki testowe
```

API: dokumentacja OpenAPI pod `/docs` (materiały `/api/materials…`, profile `/api/style-profiles…`, narzędzia bezstanowe `/api/tools/{parse,verify,render}`).

## Testy i ewaluacja

```bash
python -m pytest -q
EDITORIAL_ENGINE=heuristic python scripts/evaluate.py examples/eval
EDITORIAL_ENGINE=heuristic python scripts/evaluate.py examples/eval --from-audio   # od nagrania, z WER
python scripts/evaluate.py examples/eval --from-audio --out reports/eval.md     # z Claude
```

Przykłady w `examples/eval` są syntetyczne: transkrypcje napisane na potrzeby testów i nagrania wygenerowane syntezatorem mowy Piper. Raport podaje m.in. poprawność cytatów, WER transkrypcji, uwagi do formatu i tabelę do ręcznej oceny redaktora.

Testy działają offline: silnik Claude jest testowany z atrapą klienta.

## Zasady danych

Wyłącznie dane **syntetyczne lub publiczne, legalnie udostępnione**. Nie commitujemy niepublikowanych transkrypcji, materiałów redakcji ani sekretów. Baza (`data/`) jest poza repozytorium.
