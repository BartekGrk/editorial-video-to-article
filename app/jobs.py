"""Zadania w tle: transkrypcja i generowanie trwają minuty, więc nie blokują żądania HTTP.

Jeden wątek roboczy wystarcza prototypowi: ASR i tak zajmuje wszystkie rdzenie,
a kolejne zadania czekają w kolejce. W testach `InlineRunner` wykonuje zadanie od razu.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor


class ThreadRunner:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="editorial-job")

    def submit(self, job: Callable[[], None]) -> None:
        self._executor.submit(job)


class InlineRunner:
    def submit(self, job: Callable[[], None]) -> None:
        job()
