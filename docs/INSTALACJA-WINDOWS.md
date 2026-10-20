# Instalacja na domowym komputerze z Windows 11

Aplikacja działa w kontenerze Docker na Twoim komputerze. Z innych urządzeń (laptop, telefon)
łączysz się przez Tailscale, więc nic nie jest wystawione do internetu.

```
laptop / telefon ──(Tailscale, HTTPS)──► komputer z Windows
                                          └─ Docker: aplikacja + ffmpeg + Whisper (karta NVIDIA)
                                                     └─ tylko tekst ──► Claude (API)
```

Instrukcja zakłada komputer z kartą NVIDIA (np. RTX 5070 Ti) i aktualnym sterownikiem.
Bez karty wszystko działa tak samo, tylko transkrypcja jest wolniejsza (krok 5, wariant CPU).

## 1. WSL2 i Docker Desktop

1. PowerShell **jako administrator**:
   ```powershell
   wsl --install
   ```
   Uruchom komputer ponownie, jeśli Windows o to poprosi.
2. Zainstaluj [Docker Desktop](https://www.docker.com/products/docker-desktop/). W ustawieniach:
   - *General* → **Use the WSL 2 based engine** (włączone),
   - *General* → **Start Docker Desktop when you sign in** (włączone).
3. Sprawdź, czy kontenery widzą kartę graficzną:
   ```powershell
   docker run --rm --gpus all nvcr.io/nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
   ```
   Powinna się wyświetlić tabelka z nazwą karty. Jeśli nie – zaktualizuj sterownik NVIDIA i Docker Desktop.

## 2. Pobranie aplikacji

Zainstaluj [Git for Windows](https://git-scm.com/download/win), potem w PowerShell:

```powershell
cd C:\
git clone https://github.com/BartekGrk/editorial-video-to-article.git editorial
cd C:\editorial
git checkout claude/elegant-feynman-xp0n78
copy .env.example .env
notepad .env
```

W pliku `.env` wpisz klucz API (`ANTHROPIC_API_KEY=...`). Bez klucza aplikacja uruchomi się w trybie
demonstracyjnym, bez AI – to dobry pierwszy test.

## 3. Uruchomienie

```powershell
cd C:\editorial
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Pierwsze budowanie trwa kilka minut. Potem otwórz <http://localhost:8000>.

Przy pierwszej transkrypcji aplikacja pobierze model Whisper (ok. 3 GB) do `C:\editorial\models`.
Kolejne transkrypcje używają już pobranego modelu.

## 4. Czy transkrypcja działa na karcie graficznej?

Po pierwszym przetworzonym nagraniu:

```powershell
docker compose logs app | findstr ASR
```

- `ASR: model large-v3 na GPU (cuda)` – wszystko w porządku.
- `ASR: GPU niedostępne (...) – transkrypcja na CPU` – aplikacja działa, ale na procesorze.
  Zdarza się przy najnowszych kartach (seria RTX 50), zanim biblioteka transkrypcji dostanie
  ich obsługę. Ustaw wtedy w `.env` `EDITORIAL_ASR_MODEL=large-v3-turbo` (szybszy na CPU),
  uruchom ponownie (krok 3) i przekaż komunikat błędu – poprawimy obraz.

## 5. Wariant bez karty graficznej

```powershell
docker compose up -d --build
```

i w `.env`: `EDITORIAL_ASR_MODEL=large-v3-turbo`, `EDITORIAL_ASR_DEVICE=cpu`.

## 6. Dostęp z innych urządzeń: Tailscale

1. Załóż konto na [tailscale.com](https://tailscale.com) i zainstaluj Tailscale na komputerze
   oraz na laptopie/telefonie, z którego będzie korzystać redaktorka (to samo konto lub zaproszenie).
2. W panelu Tailscale (*DNS*) włącz **MagicDNS** i **HTTPS Certificates**.
3. Na komputerze z aplikacją (PowerShell):
   ```powershell
   tailscale serve --bg 8000
   ```
   Polecenie wypisze adres w rodzaju `https://nazwa-komputera.twoja-siec.ts.net` – pod nim
   aplikacja działa na każdym urządzeniu w Twojej sieci Tailscale.

Aplikacja nasłuchuje tylko na `127.0.0.1`, więc inne urządzenia w sieci domowej nie mają do niej
dostępu poza Tailscale.

## 7. Żeby działało na co dzień

- *Ustawienia → System → Zasilanie*: uśpienie przy zasilaniu sieciowym – **Nigdy**.
- Po restarcie komputera zaloguj się do Windows – Docker Desktop i aplikacja uruchomią się same.
- Komputer musi być włączony, gdy ktoś korzysta z aplikacji.

## 8. Aktualizacja

```powershell
cd C:\editorial
git pull
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Baza i nagrania są w `C:\editorial\data` i przetrwają aktualizację.

## 9. Kopia zapasowa

Bezpieczna kopia bazy w trakcie pracy aplikacji:

```powershell
docker compose exec app python -c "import sqlite3; sqlite3.connect('/app/data/editorial.db').backup(sqlite3.connect('/app/data/kopia.db'))"
```

Plik `C:\editorial\data\kopia.db` skopiuj na inny dysk lub do chmury (np. raz w tygodniu).
Nagrania w `data\media` można odtworzyć z oryginałów, więc ich kopia nie jest konieczna.

## Najczęstsze problemy

| Objaw | Co zrobić |
| --- | --- |
| `localhost:8000` się nie otwiera | `docker compose ps` – czy kontener ma status *healthy*; `docker compose logs app` |
| Błąd przy `--gpus all` | zaktualizuj sterownik NVIDIA i Docker Desktop; sprawdź, czy włączony jest silnik WSL 2 |
| Automat: „Transkrypcja wymaga…” / błąd pobierania modelu | sprawdź połączenie z internetem przy pierwszym uruchomieniu (pobieranie modelu) |
| Tryb demonstracyjny mimo klucza | sprawdź `ANTHROPIC_API_KEY` w `.env`, potem krok 3 |
