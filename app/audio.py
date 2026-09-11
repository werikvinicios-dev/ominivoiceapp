"""Escrita e leitura de WAV usando apenas a biblioteca padrão.

Evita depender de ``soundfile``/``numpy`` no servidor web: o backend real
devolve um array de floats em [-1, 1] e aqui ele vira um WAV PCM 16 bits.
"""

from __future__ import annotations

import array
import contextlib
import wave
from collections.abc import Iterable, Sequence
from pathlib import Path


def _to_int16(samples: Iterable[float]) -> array.array:
    out = array.array("h")
    for value in samples:
        scaled = int(float(value) * 32767.0)
        if scaled > 32767:
            scaled = 32767
        elif scaled < -32768:
            scaled = -32768
        out.append(scaled)
    return out


def write_wav(path: str | Path, samples: Sequence[float], sample_rate: int) -> float:
    """Grava ``samples`` (floats em [-1, 1]) como WAV mono e devolve a duração."""
    try:  # numpy chega junto com o backend real; usá-lo é bem mais rápido
        import numpy as np

        arr = np.asarray(samples, dtype="float32").reshape(-1)
        pcm = np.clip(arr, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2").tobytes()
        frames = len(arr)
    except Exception:
        ints = _to_int16(samples)
        pcm = ints.tobytes()
        frames = len(ints)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(wave.open(str(path), "wb")) as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm)

    return frames / float(sample_rate) if sample_rate else 0.0


def wav_duration(path: str | Path) -> float:
    """Duração em segundos de um WAV, ou 0.0 se não for legível."""
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as handle:
            rate = handle.getframerate()
            return handle.getnframes() / float(rate) if rate else 0.0
    except Exception:
        return 0.0


def format_duration(seconds: float | None) -> str:
    """Formata segundos como ``m:ss`` (ou ``0:00`` quando desconhecido)."""
    total = round(seconds or 0)
    return f"{total // 60}:{total % 60:02d}"
