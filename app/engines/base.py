"""Interface comum entre o motor real (OmniVoice) e o simulador."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class GenerationParams:
    """Parâmetros de geração expostos na interface.

    Espelham ``OmniVoiceGenerationConfig`` e os argumentos de
    ``OmniVoice.generate()``.
    """

    num_step: int = 32
    guidance_scale: float = 2.0
    speed: float = 1.0
    duration: float | None = None
    denoise: bool = True
    preprocess_prompt: bool = True
    postprocess_output: bool = True
    normalize_text: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GenerationParams:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def clamped(self) -> GenerationParams:
        """Mantém os valores dentro das faixas aceitas pelo modelo."""

        def clamp(value: float, low: float, high: float) -> float:
            return max(low, min(high, value))

        duration = self.duration
        if duration is not None:
            duration = clamp(float(duration), 0.5, 600.0)
        return GenerationParams(
            num_step=int(clamp(int(self.num_step), 4, 64)),
            guidance_scale=round(clamp(float(self.guidance_scale), 0.0, 4.0), 2),
            speed=round(clamp(float(self.speed), 0.5, 1.5), 2),
            duration=duration,
            denoise=bool(self.denoise),
            preprocess_prompt=bool(self.preprocess_prompt),
            postprocess_output=bool(self.postprocess_output),
            normalize_text=bool(self.normalize_text),
        )


@dataclass
class VoicePrompt:
    """Referência de voz pronta para reutilização.

    ``handle`` guarda o objeto nativo do motor (``VoiceClonePrompt`` no caso
    do OmniVoice); ``ref_text`` é a transcrição resolvida do áudio.
    """

    handle: Any
    ref_text: str | None = None


class EngineError(RuntimeError):
    """Falha esperada do motor, com mensagem pronta para o usuário."""


class TTSEngine:
    """Contrato implementado pelos motores de síntese."""

    name = "base"
    #: ``True`` quando a saída é sintética (nenhum modelo real envolvido).
    is_mock = False
    sampling_rate = 24000

    def load(self) -> None:
        """Carrega o modelo. Pode demorar; chamada uma única vez."""
        raise NotImplementedError

    @property
    def ready(self) -> bool:
        raise NotImplementedError

    @property
    def supports_asr(self) -> bool:
        """``True`` se consegue transcrever o áudio de referência sozinho."""
        return False

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ready": self.ready,
            "mock": self.is_mock,
            "sampling_rate": self.sampling_rate,
            "asr": self.supports_asr,
        }

    def create_voice_prompt(
        self,
        audio_path: str,
        ref_text: str | None = None,
        preprocess: bool = True,
    ) -> VoicePrompt:
        raise NotImplementedError

    def save_voice_prompt(self, prompt: VoicePrompt, path: str) -> None:
        raise NotImplementedError

    def load_voice_prompt(self, path: str, ref_text: str | None = None) -> VoicePrompt:
        raise NotImplementedError

    def synthesize(
        self,
        text: str,
        language: str | None = None,
        voice: VoicePrompt | None = None,
        instruct: str | None = None,
        params: GenerationParams | None = None,
    ) -> Sequence[float]:
        """Devolve a forma de onda mono em floats [-1, 1]."""
        raise NotImplementedError
