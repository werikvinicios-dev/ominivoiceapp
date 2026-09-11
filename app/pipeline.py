"""Montagem do áudio final a partir dos segmentos do markup.

Fica entre o motor de marcação e o backend de TTS, sem conhecer nenhum dos
dois em detalhe: recebe segmentos e um objeto que obedece a ``TTSEngine``.

    SEGMENTOS → TTS (um por vez) → SILÊNCIOS REAIS → CONCATENAÇÃO → WAV
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .audio import concat_wavs, write_wav
from .engines import GenerationParams, TTSEngine, VoicePrompt
from .markup import ParseResult, Segment

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


def segment_params(base: GenerationParams, segment: Segment) -> GenerationParams:
    """Aplica ao segmento os ajustes que o estilo pediu.

    ``speed_factor`` multiplica a velocidade da geração (não a substitui),
    para que o ritmo escolhido pelo usuário continue valendo como base.
    """
    overrides = segment.parameters
    if not overrides:
        return base

    speed = base.speed * float(overrides.get("speed_factor", 1.0))
    guidance = overrides.get("guidance_scale", base.guidance_scale)
    # `duration` fixa o tamanho do áudio inteiro; num texto segmentado ela não
    # faz sentido e atropelaria a velocidade do estilo.
    return replace(
        base, speed=speed, guidance_scale=float(guidance), duration=None
    ).clamped()


def merge_instruct(base: str | None, extra: str | None) -> str | None:
    """Junta o instruct da geração com o pedido pelo estilo, sem repetir.

    O ``instruct`` do OmniVoice é uma lista fechada de atributos; só entram
    aqui termos que o modelo realmente aceita.
    """
    items: list[str] = []
    for source in (base, extra):
        if not source:
            continue
        for item in source.split(","):
            cleaned = item.strip()
            if cleaned and cleaned.lower() not in {i.lower() for i in items}:
                items.append(cleaned)
    return ", ".join(items) if items else None


def render(
    engine: TTSEngine,
    parsed: ParseResult,
    *,
    out_path: str | Path,
    work_dir: str | Path,
    language: str | None = None,
    voice: VoicePrompt | None = None,
    instruct: str | None = None,
    params: GenerationParams | None = None,
    progress: ProgressCallback | None = None,
) -> float:
    """Sintetiza todos os segmentos e monta o WAV final. Devolve a duração.

    Cada segmento é gravado em ``work_dir`` assim que fica pronto. Numa
    retomada, os que já existem são reaproveitados — é o que permite
    sobreviver ao fim de uma sessão do Colab no meio de um capítulo longo.
    """
    base = (params or GenerationParams()).clamped()
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    speech = [(i, s) for i, s in enumerate(parsed.segments) if not s.is_silence]
    if not speech:
        raise ValueError("Nada para sintetizar: o texto não tem fala.")
    total = len(speech)
    items: list[tuple[str, object]] = []
    rendered: dict[int, Path] = {}

    for done, (index, segment) in enumerate(speech):
        target = work_dir / f"seg_{index:04d}.wav"
        if target.exists() and target.stat().st_size > 44:
            logger.info("Segmento %d reaproveitado do checkpoint.", index)
        else:
            waveform = engine.synthesize(
                text=segment.text,
                language=language,
                voice=voice,
                instruct=merge_instruct(instruct, segment.parameters.get("instruct")),
                params=segment_params(base, segment),
            )
            # Grava num temporário e só então renomeia: um checkpoint só
            # existe quando está completo.
            staging = target.with_suffix(".part")
            write_wav(staging, waveform, engine.sampling_rate)
            staging.replace(target)
        rendered[index] = target
        if progress is not None:
            progress(done + 1, total)

    for index, segment in enumerate(parsed.segments):
        if segment.pause_before:
            items.append(("silence", segment.pause_before))
        if index in rendered:
            items.append(("audio", str(rendered[index])))
        if segment.pause_after:
            items.append(("silence", segment.pause_after))

    return concat_wavs(items, out_path, engine.sampling_rate)
