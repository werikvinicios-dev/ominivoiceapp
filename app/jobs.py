"""Fila de síntese com um único worker.

O modelo ocupa a GPU inteira, então as gerações são processadas em série.
A fila vive na memória; o estado de cada geração é persistido no SQLite,
de modo que a UI pode consultá-lo por polling.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from . import db
from .audio import write_wav
from .config import settings
from .engines import EngineError, GenerationParams, TTSEngine, VoicePrompt

logger = logging.getLogger(__name__)


@dataclass
class Job:
    generation_id: str
    text: str
    language: str | None
    instruct: str | None
    voice_id: str | None
    params: GenerationParams


class JobQueue:
    """Worker único que consome a fila e grava o resultado em disco."""

    def __init__(self, engine: TTSEngine) -> None:
        self.engine = engine
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._prompt_cache: dict[str, VoicePrompt] = {}
        self._current: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="tts-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------

    def submit(self, job: Job) -> None:
        self._queue.put(job)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._current

    def invalidate_voice(self, voice_id: str) -> None:
        self._prompt_cache.pop(voice_id, None)

    def cache_voice(self, voice_id: str, prompt: VoicePrompt) -> None:
        self._prompt_cache[voice_id] = prompt

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------

    def _resolve_voice(self, voice_id: str | None) -> VoicePrompt | None:
        if not voice_id:
            return None
        cached = self._prompt_cache.get(voice_id)
        if cached is not None:
            return cached

        voice = db.get_voice(voice_id)
        if voice is None:
            raise EngineError("A voz selecionada não existe mais.")
        prompt_path = voice["prompt_path"]
        if prompt_path and Path(prompt_path).exists():
            prompt = self.engine.load_voice_prompt(prompt_path, voice["ref_text"])
        elif voice["sample_path"] and Path(voice["sample_path"]).exists():
            # Sem prompt salvo (ex.: trocou-se de motor), reprocessa o áudio.
            prompt = self.engine.create_voice_prompt(
                voice["sample_path"], voice["ref_text"]
            )
        else:
            raise EngineError(
                "A voz selecionada não tem áudio de referência disponível."
            )
        self._prompt_cache[voice_id] = prompt
        return prompt

    def _process(self, job: Job) -> None:
        db.mark_running(job.generation_id)
        voice = self._resolve_voice(job.voice_id)
        waveform = self.engine.synthesize(
            text=job.text,
            language=job.language,
            voice=voice,
            instruct=job.instruct,
            params=job.params,
        )
        output = settings.audio_dir / f"{job.generation_id}.wav"
        duration = write_wav(output, waveform, self.engine.sampling_rate)
        db.mark_done(job.generation_id, str(output), duration)
        db.prune_history(settings.history_limit)

    def _run(self) -> None:
        logger.info("Worker de síntese iniciado (motor: %s).", self.engine.name)
        while not self._stop.is_set():
            job = self._queue.get()
            if job is None:
                break
            with self._lock:
                self._current = job.generation_id
            try:
                self._process(job)
            except EngineError as exc:
                logger.warning("Geração %s falhou: %s", job.generation_id, exc)
                db.mark_error(job.generation_id, str(exc))
            except Exception as exc:  # pragma: no cover - rede de segurança
                logger.exception("Erro inesperado na geração %s", job.generation_id)
                db.mark_error(job.generation_id, f"{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._current = None
                self._queue.task_done()
        logger.info("Worker de síntese encerrado.")


def job_from_row(row) -> Job:
    """Reconstrói um Job a partir da linha de ``generations``."""
    return Job(
        generation_id=row["id"],
        text=row["text"],
        language=row["language"],
        instruct=row["instruct"],
        voice_id=row["voice_id"],
        params=GenerationParams.from_dict(json.loads(row["params"] or "{}")),
    )
