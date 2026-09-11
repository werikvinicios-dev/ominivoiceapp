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


def concat_wavs(
    items: Sequence[tuple[str, object]],
    out_path: str | Path,
    sample_rate: int,
) -> float:
    """Monta um WAV a partir de trechos de áudio e silêncios reais.

    ``items`` é uma sequência de ``("audio", caminho)`` ou
    ``("silence", segundos)``. Os quadros são copiados em blocos, então um
    audiobook longo não precisa caber na memória.

    O silêncio é gravado aqui, depois da síntese — é por isso que
    ``[pause=1.2]`` rende exatamente 1,2 s, qualquer que seja o modelo.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames = 0

    with contextlib.closing(wave.open(str(out_path), "wb")) as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(sample_rate))

        for kind, value in items:
            if kind == "silence":
                count = round(float(value) * sample_rate)
                if count <= 0:
                    continue
                out.writeframes(b"\x00\x00" * count)
                frames += count
                continue

            with contextlib.closing(wave.open(str(value), "rb")) as source:
                if source.getframerate() != sample_rate or source.getnchannels() != 1:
                    raise ValueError(
                        f"{value}: esperado WAV mono a {sample_rate} Hz."
                    )
                remaining = source.getnframes()
                frames += remaining
                while remaining > 0:
                    block = source.readframes(min(remaining, 65536))
                    if not block:
                        break
                    out.writeframes(block)
                    remaining -= len(block) // 2

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
