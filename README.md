# Editorial Video → Article Assistant

Prototyp narzędzia, które z **transkrypcji wideo** przygotowuje **weryfikowalny draft** tekstu redakcyjnego w dwóch trybach:

- **SHORT** – tekst towarzyszący wideo (2–3 tytuły, krótki lead, zwięzła treść);
- **FULL** – pełnoprawny artykuł (tytuły, lead, śródtytuły, selektywnie dobrane cytaty).

Człowiek pozostaje redaktorem: narzędzie nie publikuje niczego samo. Każdy cytat ma mówcę i timecode i jest sprawdzany względem transkrypcji; tezy wymagające zewnętrznego potwierdzenia są oznaczane `requires_verification`.

Pełna specyfikacja pomysłu: [`ideas/editorial-video-to-article.md`](https://github.com/BartekGrk/automation-ops-intelligence/blob/main/ideas/editorial-video-to-article.md) w repozytorium `automation-ops-intelligence`. Decyzje i stan prac: [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md).

## Stan

Faza 1, etap E1 – fundament bez LLM:

| Element | Plik | Status |
| --- | --- | --- |
| Kontrakty danych (transkrypcja, cytat, teza, draft SHORT/FULL) | `app/models.py` | gotowe, testy |
| Parser transkrypcji `[hh:mm:ss] Mówca: tekst` | `app/transcript.py` | gotowe, testy |
| Deterministyczna weryfikacja cytatów i tez | `app/verification.py` | gotowe, testy |
| Eksport Markdown z materiałem kontrolnym | `app/render.py` | gotowe, testy |
| API (FastAPI) | `app/main.py` | gotowe, testy |
| Ekstrakcja faktów/cytatów i generowanie draftu przez LLM | – | następny krok |

## Uruchomienie

```bash
pip install -r requirements.txt
python -m pytest -q          # testy
uvicorn app.main:app --reload  # API na http://127.0.0.1:8000/docs
```

Endpointy:

- `GET /health`
- `POST /transcripts/parse` – `{"text": "..."}` → segmenty z czasem i mówcą;
- `POST /drafts/verify` – transkrypcja + draft → raport cytatów i tez (`ok`);
- `POST /drafts/render` – Markdown dla redaktora; **409**, jeśli weryfikacja nie przeszła.

Przykładowe dane (syntetyczne): `tests/fixtures/`.

## Zasady danych

Wyłącznie dane **syntetyczne lub publiczne, legalnie udostępnione**. Nie commitujemy niepublikowanych transkrypcji, materiałów wewnętrznych redakcji ani sekretów (klucze API tylko w zmiennych środowiskowych).
