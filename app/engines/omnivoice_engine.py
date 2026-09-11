"""Motor real: envolve o pacote ``omnivoice`` (Xiaomi / Next-gen Kaldi)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

from .base import EngineError, GenerationParams, TTSEngine, VoicePrompt

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """``True`` se ``omnivoice`` e ``torch`` estiverem instalados."""
    import importlib.util

    return all(
        importlib.util.find_spec(module) is not None
        for module in ("omnivoice", "torch")
    )


class OmniVoiceEngine(TTSEngine):
    name = "omnivoice"
    is_mock = False

    def __init__(
        self,
        model: str,
        device: str | None = None,
        load_asr: bool = True,
        asr_model: str = "openai/whisper-large-v3-turbo",
    ) -> None:
        self.model_ref = model
        self.device = device
        self.load_asr = load_asr
        self.asr_model = asr_model
        self._model: Any = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                import torch
                from omnivoice import OmniVoice
                from omnivoice.utils.common import get_best_device
            except ImportError as exc:  # pragma: no cover - depende do ambiente
                raise EngineError(
                    "Pacote 'omnivoice' não está instalado. Veja o README"
                    " (seção 'Backend real')."
                ) from exc

            device = self.device or get_best_device()
            logger.info("Carregando OmniVoice de %s em %s…", self.model_ref, device)
            # float16 na GPU; na CPU o float16 é lento e instável.
            dtype = torch.float32 if str(device).startswith("cpu") else torch.float16
            try:
                self._model = OmniVoice.from_pretrained(
                    self.model_ref,
                    device_map=device,
                    dtype=dtype,
                    load_asr=self.load_asr,
                    asr_model_name=self.asr_model,
                )
            except Exception as exc:
                raise EngineError(f"Falha ao carregar o modelo: {exc}") from exc
            self.sampling_rate = int(self._model.sampling_rate or 24000)
            logger.info("OmniVoice pronto (%d Hz).", self.sampling_rate)

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def supports_asr(self) -> bool:
        return self.load_asr

    def _require_model(self):
        if self._model is None:
            self.load()
        return self._model

    # ------------------------------------------------------------------
    # Vozes
    # ------------------------------------------------------------------

    def create_voice_prompt(
        self,
        audio_path: str,
        ref_text: str | None = None,
        preprocess: bool = True,
    ) -> VoicePrompt:
        model = self._require_model()
        if not ref_text and not self.load_asr:
            raise EngineError(
                "Informe a transcrição do áudio de referência: o modelo de"
                " transcrição automática não foi carregado (OMNI_LOAD_ASR=0)."
            )
        with self._lock:
            try:
                handle = model.create_voice_clone_prompt(
                    ref_audio=audio_path,
                    ref_text=ref_text or None,
                    preprocess_prompt=preprocess,
                )
            except Exception as exc:
                raise EngineError(
                    f"Não foi possível processar o áudio: {exc}."
                    " Formatos comprimidos (M4A, MP3, WebM) precisam do ffmpeg"
                    " instalado no servidor; um arquivo WAV sempre funciona."
                ) from exc
        return VoicePrompt(handle=handle, ref_text=getattr(handle, "ref_text", ref_text))

    def save_voice_prompt(self, prompt: VoicePrompt, path: str) -> None:
        prompt.handle.save(path)

    def load_voice_prompt(self, path: str, ref_text: str | None = None) -> VoicePrompt:
        from omnivoice import VoiceClonePrompt

        try:
            handle = VoiceClonePrompt.load(path)
        except Exception as exc:
            raise EngineError(f"Voz salva não pôde ser carregada: {exc}") from exc
        return VoicePrompt(handle=handle, ref_text=getattr(handle, "ref_text", ref_text))

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
        from omnivoice import OmniVoiceGenerationConfig

        model = self._require_model()
        params = (params or GenerationParams()).clamped()

        config = OmniVoiceGenerationConfig(
            num_step=params.num_step,
            guidance_scale=params.guidance_scale,
            denoise=params.denoise,
            preprocess_prompt=params.preprocess_prompt,
            postprocess_output=params.postprocess_output,
        )
        kwargs: dict[str, Any] = {
            "text": text,
            "language": language or None,
            "generation_config": config,
            "normalize_text": params.normalize_text,
        }
        # duration tem precedência sobre speed no OmniVoice; só enviamos um.
        if params.duration:
            kwargs["duration"] = params.duration
        elif params.speed != 1.0:
            kwargs["speed"] = params.speed
        if voice is not None:
            kwargs["voice_clone_prompt"] = voice.handle
        if instruct:
            kwargs["instruct"] = instruct

        # Uma geração por vez: o modelo ocupa a GPU inteira.
        with self._lock:
            try:
                audios = model.generate(**kwargs)
            except Exception as exc:
                raise EngineError(f"{type(exc).__name__}: {exc}") from exc
        if not audios:
            raise EngineError("O modelo não devolveu áudio.")
        return audios[0]
