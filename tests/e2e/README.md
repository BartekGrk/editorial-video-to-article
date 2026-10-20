# Testy przeglądarkowe (Playwright)

Uzupełniają testy `pytest`: przechodzą interfejs tak, jak redaktor.

```bash
# serwer na czystej bazie, tryb demonstracyjny, szybki model ASR
EDITORIAL_DB=/tmp/e2e.db EDITORIAL_MEDIA_DIR=/tmp/e2e-media EDITORIAL_ENGINE=heuristic \
  EDITORIAL_ASR_MODEL=small uvicorn app.main:app --port 8765 &

mkdir -p /tmp/shots
node tests/e2e/manual_flow.js /tmp/shots   # wymaga pustej bazy: przykład → analiza → draft → edycja → eksport → profile
node tests/e2e/auto_flow.js /tmp/shots     # wgranie nagrania → postęp → draft zatwierdzony → odtwarzacz → ⚡ Automat
node tests/e2e/unsaved_edits.js /tmp/shots # niezapisane edycje przy zmianie zakładki, blokada zatwierdzenia bez zapisu
```

Wynik `ERRORS: []` oznacza brak błędów JavaScript; zrzuty ekranu trafiają do podanego katalogu. Potrzebny jest pakiet `playwright` (Node) i przeglądarka Chromium.
