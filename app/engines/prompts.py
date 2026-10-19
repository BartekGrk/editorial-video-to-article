"""Prompty dla modelu językowego.

Instrukcje systemowe są stałe (sprzyja to cache'owaniu promptu); zmienne dane
(transkrypcja, materiał dowodowy, wybór redaktora) idą w wiadomości użytkownika.
"""

import json

from app.engines.base import DraftRequest, MaterialInput
from app.models import ArticleDraft, ArticleMode
from app.transcript import format_timecode

EVIDENCE_SYSTEM = """Jesteś asystentem redakcji serwisu informacyjnego. Porządkujesz materiał \
dowodowy z transkrypcji nagrania wideo, zanim ktokolwiek zacznie pisać tekst.

Zasady:
- Cytaty kopiuj z transkrypcji dosłownie, znak w znak. Możesz wybrać fragment wypowiedzi, \
ale nie poprawiaj gramatyki, nie łącz zdań różnych osób i nie dopisuj słów.
- Mówcę i `timecode_seconds` podawaj dokładnie według znacznika `t=` segmentu, z którego pochodzi cytat.
- Relacja rozmówcy to nie fakt. Liczby, kwoty, daty, nazwiska i twierdzenia, których nie da się \
potwierdzić samym nagraniem, oznacz `requires_verification: true`.
- W `gaps` wypisz, czego transkrypcja nie mówi: kontekst widoczny tylko w obrazie, niejasnych mówców, \
fragmenty wyglądające na błąd rozpoznawania mowy.
- Zaproponuj tryb: SHORT (krótki tekst towarzyszący wideo) albo FULL (pełny artykuł). FULL tylko wtedy, \
gdy materiał ma dość treści i wartości informacyjnej; nie rozbudowuj sztucznie.
- Pisz po polsku."""

STYLE_SYSTEM = """Analizujesz przykładowe artykuły redakcji, aby opisać ich format jako jawny profil stylu. \
Opisz cechy: długości, konstrukcję tytułów i leadów, śródtytuły, sposób cytowania, ton i podawanie kontekstu. \
Nie przepisuj zdań ani charakterystycznych fraz z przykładów: profil ma służyć spójności formatu, \
nie kopiowaniu treści ani naśladowaniu konkretnego autora. Pisz po polsku, zwięźle."""

DRAFT_SYSTEM = """Jesteś asystentem redakcji. Przygotowujesz draft tekstu na podstawie materiału dowodowego \
z nagrania wideo. Draft zredaguje i zatwierdzi człowiek.

Twarde zasady:
1. Opieraj się wyłącznie na transkrypcji i materiale dowodowym. Nie dodawaj faktów spoza nagrania. \
Jeśli tekst potrzebuje tła, wpisz to jako uwagę w `editor_notes`, zamiast je wymyślać.
2. Cytat w cudzysłowie „…” musi być dosłownym fragmentem transkrypcji. Każdy taki cytat \
(także w tytule i leadzie) dodaj do `quotes` z mówcą i `timecode_seconds`. Parafrazy pisz bez cudzysłowu \
i z przypisaniem („mówi”, „twierdzi”).
3. Relację rozmówcy przedstawiaj jako jego słowa, nie jako potwierdzony fakt. Tezy z liczbami, datami \
i nazwiskami umieść w `claims` z `requires_verification: true` i wskazaniem `evidence_timecodes`.
4. Tryb SHORT: tekst towarzyszący wideo, 2–3 tytuły, krótki lead, zwięzła treść (zwykle 1 sekcja bez \
śródtytułu). Nie rozbudowuj przekazu ponad to, co jest w nagraniu.
   Tryb FULL: 2–3 tytuły, lead, rozwinięcie ze śródtytułami, selektywnie dobrane cytaty. Długość \
wynika z wartości informacyjnej materiału.
5. Profil stylu opisuje format redakcji. Stosuj go, ale nie podszywaj się pod konkretnego autora.
6. Pisz poprawną polszczyzną dziennikarską."""

REPAIR_SYSTEM = DRAFT_SYSTEM + """

Otrzymujesz draft, który nie przeszedł automatycznej weryfikacji. Popraw wyłącznie wskazane problemy: \
zamień błędny cytat na dosłowny fragment transkrypcji albo przeredaguj go na parafrazę bez cudzysłowu; \
popraw mówcę lub timecode; dodaj brakujące wskazania źródeł. Resztę tekstu zostaw bez zmian."""


def format_transcript(material: MaterialInput) -> str:
    lines = []
    for segment in material.transcript.segments:
        speaker = f"{segment.speaker}: " if segment.speaker else ""
        lines.append(f"[{format_timecode(segment.start_seconds)} t={segment.start_seconds}] {speaker}{segment.text}")
    return "\n".join(lines)


def _material_header(material: MaterialInput) -> str:
    parts = [f"Tytuł roboczy: {material.title}"]
    if material.category:
        parts.append(f"Kategoria materiału: {material.category}")
    if material.topic:
        parts.append(f"Temat: {material.topic}")
    if material.angle:
        parts.append(f"Kąt redakcyjny wskazany przez redaktora: {material.angle}")
    return "\n".join(parts)


def evidence_prompt(material: MaterialInput) -> str:
    return f"{_material_header(material)}\n\n<transkrypcja>\n{format_transcript(material)}\n</transkrypcja>"


def style_prompt(articles: list[str]) -> str:
    joined = "\n\n".join(f"<artykul nr=\"{i}\">\n{a.strip()}\n</artykul>" for i, a in enumerate(articles, start=1))
    return f"Opisz profil stylu na podstawie {len(articles)} artykułów:\n\n{joined}"


def draft_prompt(request: DraftRequest) -> str:
    material = request.material
    parts = [
        _material_header(material),
        f"Tryb: {request.mode.value}"
        + (" (tekst towarzyszący wideo)" if request.mode == ArticleMode.SHORT else " (pełny artykuł)"),
    ]
    if request.target_words:
        parts.append(f"Docelowa długość treści: około {request.target_words} słów (orientacyjnie).")
    if request.extra_instructions:
        parts.append(f"Wskazówki redaktora: {request.extra_instructions}")
    if request.style:
        parts.append("<profil_stylu>\n" + request.style.model_dump_json(indent=2) + "\n</profil_stylu>")
    parts.append("<material_dowodowy>\n" + request.evidence.model_dump_json(indent=2) + "\n</material_dowodowy>")
    parts.append(f"<transkrypcja>\n{format_transcript(material)}\n</transkrypcja>")
    return "\n\n".join(parts)


def repair_prompt(request: DraftRequest, draft: ArticleDraft, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        f"{draft_prompt(request)}\n\n<draft>\n{json.dumps(draft.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n</draft>"
        f"\n\nProblemy wykryte przez weryfikację:\n{listed}"
    )
