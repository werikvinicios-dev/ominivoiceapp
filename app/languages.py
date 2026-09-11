"""Lista de idiomas suportados pelo OmniVoice.

Lida do TSV que acompanha o projeto original, então o app conhece os 600+
idiomas mesmo sem o pacote ``omnivoice`` instalado.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

_TSV = Path(__file__).resolve().parent / "data" / "lang_id_name_map.tsv"

# Idiomas que aparecem no topo da lista, na ordem definida aqui.
FEATURED_CODES = [
    "pt",
    "en",
    "es",
    "fr",
    "de",
    "it",
    "zh",
    "ja",
    "ko",
    "ru",
    "arb",
    "yue",
    "hi",
]


class Language(NamedTuple):
    code: str
    name: str
    hours: float


@lru_cache(maxsize=1)
def all_languages() -> list[Language]:
    """Todos os idiomas, ordenados por nome."""
    languages: dict[str, Language] = {}
    if not _TSV.exists():  # pragma: no cover - o arquivo é versionado junto
        return []
    with _TSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            name = (row.get("language_name") or "").strip()
            code = (row.get("language_id") or "").strip()
            if not name or not code:
                continue
            try:
                hours = float(row.get("train_data_duration") or 0)
            except ValueError:
                hours = 0.0
            languages[code] = Language(code=code, name=name, hours=hours)
    return sorted(languages.values(), key=lambda lang: lang.name.lower())


@lru_cache(maxsize=1)
def featured_languages() -> list[Language]:
    """Os idiomas mais usados, para o topo do seletor."""
    by_code = {lang.code: lang for lang in all_languages()}
    return [by_code[code] for code in FEATURED_CODES if code in by_code]


@lru_cache(maxsize=1)
def _by_code() -> dict[str, Language]:
    return {lang.code: lang for lang in all_languages()}


def language_name(code: str | None) -> str:
    """Nome de exibição de um código de idioma."""
    if not code:
        return "Detectar automaticamente"
    found = _by_code().get(code)
    return found.name if found else code


def is_valid(code: str | None) -> bool:
    return bool(code) and code in _by_code()
