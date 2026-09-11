"""Motor simulado: gera fala sintética sem carregar nenhum modelo.

Serve para desenvolver e testar toda a interface (fila, player, biblioteca de
vozes, histórico) em máquinas sem GPU ou sem o checkpoint baixado. O áudio é
um zumbido com formantes e envelope silábico — reconhecível como "voz", mas
não é fala de verdade.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import time
from collections.abc import Sequence
from pathlib import Path

from .base import GenerationParams, TTSEngine, VoicePrompt

_SAMPLE_RATE = 24000

# Palavras-chave de "voice design" que deslocam o tom base.
_PITCH_KEYWORDS = {
    "female": 1.55,
    "mulher": 1.55,
    "male": 0.8,
    "homem": 0.8,
    "child": 2.0,
    "criança": 2.0,
    "teenager": 1.4,
    "elderly": 0.85,
    "idoso": 0.85,
    "very low pitch": 0.6,
    "low pitch": 0.78,
    "high pitch": 1.5,
    "very high pitch": 1.9,
    "whisper": 1.0,
}


def _seed_of(*parts: str | None) -> int:
    payload = "|".join(part or "" for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _syllables(text: str) -> list[tuple[str, float]]:
    """Divide o texto em pedaços pronunciáveis e pausas (em segundos)."""
    chunks: list[tuple[str, float]] = []
    for token in re.findall(r"[^\W_]+|[.,!?;:…]+|\s+", text, flags=re.UNICODE):
        if token.isspace():
            chunks.append(("", 0.06))
        elif re.match(r"[.,!?;:…]", token):
            chunks.append(("", 0.28 if token[0] in ".!?…" else 0.14))
        else:
            # Aproxima sílabas por grupos de vogais.
            groups = re.findall(r"[aeiouyàáâãäéêëíóôõöúüAEIOUY]+", token) or [token]
            for group in groups:
                chunks.append((group, 0.0))
    return chunks


class MockEngine(TTSEngine):
    name = "mock"
    is_mock = True
    sampling_rate = _SAMPLE_RATE

    def __init__(self) -> None:
        # Não há modelo para carregar: o simulador já nasce pronto.
        self._ready = True

    def load(self) -> None:
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def supports_asr(self) -> bool:
        # Devolve um texto de placeholder no lugar da transcrição real.
        return True

    # ------------------------------------------------------------------
    # Vozes
    # ------------------------------------------------------------------

    def create_voice_prompt(
        self,
        audio_path: str,
        ref_text: str | None = None,
        preprocess: bool = True,
    ) -> VoicePrompt:
        # O "timbre" é só a assinatura do arquivo de referência.
        digest = hashlib.sha256(Path(audio_path).read_bytes()[:262144]).hexdigest()
        return VoicePrompt(
            handle={"signature": digest},
            ref_text=ref_text or "(transcrição simulada do áudio de referência)",
        )

    def save_voice_prompt(self, prompt: VoicePrompt, path: str) -> None:
        Path(path).write_text(
            f"{prompt.handle['signature']}\n{prompt.ref_text or ''}",
            encoding="utf-8",
        )

    def load_voice_prompt(self, path: str, ref_text: str | None = None) -> VoicePrompt:
        raw = Path(path).read_text(encoding="utf-8").split("\n", 1)
        signature = raw[0].strip()
        stored_text = raw[1].strip() if len(raw) > 1 else None
        return VoicePrompt(
            handle={"signature": signature}, ref_text=ref_text or stored_text
        )

    # ------------------------------------------------------------------
    # Síntese
    # ------------------------------------------------------------------

    def synthesize(
        self,
        text: str,
        language: str | None = None,
        voice: VoicePrompt | None = None,
        instruct: str | None = None,
        params: GenerationParams | None = None,
    ) -> Sequence[float]:
        params = (params or GenerationParams()).clamped()

        signature = (voice.handle or {}).get("signature") if voice else None
        rng = random.Random(_seed_of(signature, instruct, language))

        base_f0 = 110.0 + rng.random() * 70.0
        lowered = (instruct or "").lower()
        for keyword, factor in _PITCH_KEYWORDS.items():
            if keyword in lowered:
                base_f0 *= factor
                break
        breathy = "whisper" in lowered or "耳语" in (instruct or "")

        chunks = _syllables(text)
        speed = params.speed or 1.0
        syllable_seconds = 0.19 / speed

        samples: list[float] = []
        phase = 0.0
        for chunk, pause in chunks:
            if not chunk:
                samples.extend([0.0] * int(pause / speed * _SAMPLE_RATE))
                continue
            length = int(syllable_seconds * (0.75 + rng.random() * 0.6) * _SAMPLE_RATE)
            f0 = base_f0 * (0.9 + rng.random() * 0.25)
            # Leve contorno descendente dentro da sílaba.
            for index in range(length):
                position = index / max(length, 1)
                envelope = math.sin(math.pi * position) ** 0.6
                phase += 2 * math.pi * (f0 * (1.0 - 0.12 * position)) / _SAMPLE_RATE
                value = (
                    math.sin(phase)
                    + 0.45 * math.sin(2 * phase)
                    + 0.22 * math.sin(3 * phase)
                    + 0.10 * math.sin(5 * phase)
                )
                if breathy:
                    value = 0.25 * value + 0.75 * (rng.random() * 2 - 1)
                samples.append(0.28 * envelope * value)

        if params.duration:
            target = int(params.duration * _SAMPLE_RATE)
            if target > len(samples):
                samples.extend([0.0] * (target - len(samples)))
            else:
                samples = samples[:target]

        # Simula o custo de inferência para a fila ficar observável na UI.
        time.sleep(min(2.0, 0.15 + len(samples) / _SAMPLE_RATE * 0.05))
        return samples or [0.0] * _SAMPLE_RATE
