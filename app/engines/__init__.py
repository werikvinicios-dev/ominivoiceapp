"""Seleção do motor de síntese."""

from __future__ import annotations

import logging

from ..config import settings
from .base import EngineError, GenerationParams, TTSEngine, VoicePrompt
from .mock_engine import MockEngine
from .omnivoice_engine import OmniVoiceEngine, is_available

logger = logging.getLogger(__name__)

_engine: TTSEngine | None = None


def build_engine() -> TTSEngine:
    """Instancia o motor conforme ``OMNI_BACKEND`` (auto | omnivoice | mock)."""
    backend = settings.backend
    if backend == "mock":
        logger.info("Backend: simulador (forçado por OMNI_BACKEND=mock).")
        return MockEngine()

    if backend in {"auto", "omnivoice"}:
        if is_available():
            return OmniVoiceEngine(
                model=settings.model,
                device=settings.device,
                load_asr=settings.load_asr,
                asr_model=settings.asr_model,
            )
        if backend == "omnivoice":
            raise EngineError(
                "OMNI_BACKEND=omnivoice, mas o pacote 'omnivoice' (ou o torch)"
                " não está instalado."
            )
        logger.warning(
            "Pacote 'omnivoice' não encontrado — usando o simulador."
            " Instale o OmniVoice para gerar fala de verdade."
        )
        return MockEngine()

    raise EngineError(f"OMNI_BACKEND inválido: {backend!r}")


def get_engine() -> TTSEngine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


__all__ = [
    "EngineError",
    "GenerationParams",
    "TTSEngine",
    "VoicePrompt",
    "build_engine",
    "get_engine",
]
