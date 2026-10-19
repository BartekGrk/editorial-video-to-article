# Editorial Video → Article Assistant

Aplikacja webowa, która z **transkrypcji wideo** przygotowuje **weryfikowalny draft** tekstu redakcyjnego w dwóch trybach:

- **SHORT**: tekst towarzyszący wideo (2–3 tytuły, krótki lead, zwięzła treść);
- **FULL**: pełnoprawny artykuł (tytuły, lead, śródtytuły, wybrane cytaty).

Człowiek pozostaje redaktorem: aplikacja niczego nie publikuje. Każdy cytat jest sprawdzany względem transkrypcji, a tezy wymagające zewnętrznego potwierdzenia są oznaczane `requires_verification`. Draft z niezweryfikowanym cytatem nie da się zatwierdzić ani wyeksportować.

Specyfikacja: [`ideas/editorial-video-to-article.md`](https://github.com/BartekGrk/automation-ops-intelligence/blob/main/ideas/editorial-video-to-article.md) w `automation-ops-intelligence`. Decyzje i ograniczenia: [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md).

## Szybki start

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload        # http://127.0.0.1:8000
```

Bez klucza API aplikacja działa w **trybie demonstracyjnym** (reguły zamiast AI), co pozwala przejść cały proces. Aby generować drafty modelem Claude:

```bash
export ANTHROPIC_API_KEY=...         # albo: ant auth login
uvicorn app.main:app
```

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `EDITORIAL_ENGINE` | `anthropic`, jeśli jest klucz, w przeciwnym razie `heuristic` | wybór silnika |
| `EDITORIAL_MODEL` | `claude-opus-5-5` | model Claude |
| `EDITORIAL_DB` | `data/editorial.db` | plik bazy SQLite |

## Przepływ pracy

1. **Nowy materiał:** tytuł, kategoria, temat, kąt, link do wideo (YouTube lub plik) i transkrypcja `[hh:mm:ss] Mówca: tekst`. Zgoda na przetwarzanie przez zewnętrzne API jest wymagana dla każdego materiału osobno.
2. **Materiał dowodowy:** wątki, cytaty, tezy, luki i sugerowany tryb. Cytaty, których nie ma w transkrypcji, są odrzucane, a mówca i timecode poprawiane według transkrypcji.
3. **Draft:** redaktor wybiera SHORT/FULL, opcjonalnie profil stylu, długość i wskazówki. Draft jest weryfikowany, a przy błędach model dostaje jedną rundę poprawki.
4. **Edycja:** tytuły, lead, sekcje i uwagi. Każdy zapis uruchamia weryfikację ponownie, również dla cytatów w cudzysłowie dopisanych ręcznie.
5. **Zatwierdzenie i eksport:** Markdown lub HTML z wybranym tytułem, z materiałem kontrolnym (cytaty z timecode, tezy, uwagi) albo bez niego.

Transkrypcja wyświetla się obok draftu. Kliknięcie timecode przewija odtwarzacz (YouTube lub plik wideo) do danego momentu.

**Profile stylu:** z 5–10 publicznych artykułów powstaje jawny opis formatu (tytuły, lead, śródtytuły, cytowanie, ton), który można edytować. Teksty źródłowe nie są zapisywane.

## Architektura

```text
app/
  models.py          kontrakty danych (Pydantic) – także JSON Schema dla LLM
  transcript.py      parser transkrypcji
  verification.py    deterministyczna weryfikacja cytatów i tez
  pipeline.py        analyze → write (+ jedna poprawka), linki do wideo
  engines/           AnthropicEngine (Claude, structured output) i HeuristicEngine (demo/testy)
  store.py           SQLite (dokumenty JSON)
  render.py          eksport Markdown / HTML
  evaluation.py      metryki jakości na zestawie przypadków
  main.py            API FastAPI + serwowanie UI
  static/            interfejs (HTML/CSS/JS bez kroku budowania)
scripts/evaluate.py  raport ewaluacji
examples/eval/       syntetyczne przypadki testowe
```

API: dokumentacja OpenAPI pod `/docs` (materiały `/api/materials…`, profile `/api/style-profiles…`, narzędzia bezstanowe `/api/tools/{parse,verify,render}`).

## Testy i ewaluacja

```bash
python -m pytest -q
EDITORIAL_ENGINE=heuristic python scripts/evaluate.py examples/eval
python scripts/evaluate.py examples/eval --out reports/eval.md   # z Claude
```

Testy działają offline: silnik Claude jest testowany z atrapą klienta.

## Zasady danych

Wyłącznie dane **syntetyczne lub publiczne, legalnie udostępnione**. Nie commitujemy niepublikowanych transkrypcji, materiałów redakcji ani sekretów. Baza (`data/`) jest poza repozytorium.
