"""Preferências editáveis pela tela de Ajustes.

Ficam no banco (e não em variável de ambiente) porque o usuário as muda pelo
celular, e precisam sobreviver ao reinício do servidor.
"""

from __future__ import annotations

from . import db, markup

#: Chave -> (valor padrão, mínimo, máximo).
NUMERIC_PREFS: dict[str, tuple[float, float, float]] = {
    "pause": (markup.DEFAULT_PAUSE, 0.05, markup.MAX_PAUSE_SECONDS),
    "long_pause": (markup.DEFAULT_LONG_PAUSE, 0.05, markup.MAX_PAUSE_SECONDS),
}


def pauses() -> dict[str, float]:
    """Durações atuais de ``[pause]`` e ``[long-pause]``, em segundos."""
    stored = db.get_prefs()
    result = {}
    for key, (default, low, high) in NUMERIC_PREFS.items():
        try:
            value = float(stored.get(key, default))
        except (TypeError, ValueError):
            value = default
        result[key] = max(low, min(high, value))
    return result


def set_pauses(pause: float, long_pause: float) -> dict[str, float]:
    for key, value in (("pause", pause), ("long_pause", long_pause)):
        _default, low, high = NUMERIC_PREFS[key]
        db.set_pref(key, str(max(low, min(high, float(value)))))
    return pauses()
