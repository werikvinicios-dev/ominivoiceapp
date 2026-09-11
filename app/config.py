"""Configuração do aplicativo, lida de variáveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """Parâmetros ajustáveis por variável de ambiente (prefixo ``OMNI_``)."""

    # Onde ficam banco, áudios gerados, vozes salvas e uploads.
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("OMNI_DATA_DIR", PROJECT_DIR / "data")
        ).resolve()
    )

    # "auto" tenta o OmniVoice real e cai para o simulador se indisponível.
    # Valores aceitos: auto | omnivoice | mock
    backend: str = field(
        default_factory=lambda: os.environ.get("OMNI_BACKEND", "auto").strip().lower()
    )

    # Checkpoint ou repo do HuggingFace usado pelo backend real.
    model: str = field(
        default_factory=lambda: os.environ.get("OMNI_MODEL", "k2-fsa/OmniVoice")
    )

    # None deixa o OmniVoice escolher (cuda / mps / xpu / cpu).
    device: str | None = field(
        default_factory=lambda: os.environ.get("OMNI_DEVICE") or None
    )

    # Carrega o Whisper para transcrever automaticamente o áudio de referência.
    load_asr: bool = field(default_factory=lambda: _env_bool("OMNI_LOAD_ASR", True))
    asr_model: str = field(
        default_factory=lambda: os.environ.get(
            "OMNI_ASR_MODEL", "openai/whisper-large-v3-turbo"
        )
    )

    # Carrega o modelo já na inicialização em vez de na primeira geração.
    preload: bool = field(default_factory=lambda: _env_bool("OMNI_PRELOAD", False))

    # Limites de entrada.
    max_text_chars: int = field(
        default_factory=lambda: _env_int("OMNI_MAX_TEXT_CHARS", 5000)
    )
    max_upload_bytes: int = field(
        default_factory=lambda: _env_int("OMNI_MAX_UPLOAD_MB", 25) * 1024 * 1024
    )

    # Quantas gerações manter no histórico (as mais antigas são apagadas).
    history_limit: int = field(
        default_factory=lambda: _env_int("OMNI_HISTORY_LIMIT", 200)
    )

    # Pasta persistente (ex.: Google Drive no Colab). Recebe banco, vozes e
    # áudios finais; o resto continua no runtime, que é bem mais rápido.
    # Vazio = tudo junto em data_dir.
    persist_dir_raw: str = field(
        default_factory=lambda: os.environ.get("OMNI_PERSIST_DIR", "").strip()
    )

    @property
    def persist_dir(self) -> Path:
        return Path(self.persist_dir_raw).resolve() if self.persist_dir_raw else self.data_dir

    @property
    def is_persistent(self) -> bool:
        """``True`` quando os dados sobrevivem ao fim da sessão."""
        return bool(self.persist_dir_raw)

    @property
    def audio_dir(self) -> Path:
        return self.persist_dir / "audio"

    @property
    def voices_dir(self) -> Path:
        return self.persist_dir / "voices"

    @property
    def db_path(self) -> Path:
        return self.persist_dir / "studio.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def temp_dir(self) -> Path:
        """Áudio descartável: prévias e checkpoints de segmentos."""
        return self.data_dir / "temp"

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.persist_dir,
            self.audio_dir,
            self.voices_dir,
            self.uploads_dir,
            self.temp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
