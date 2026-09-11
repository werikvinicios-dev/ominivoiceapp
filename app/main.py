"""OmniVoice Studio — servidor FastAPI com interface HTMX."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, db, languages, presets
from .audio import format_duration, wav_duration
from .config import BASE_DIR, settings
from .engines import EngineError, GenerationParams, get_engine
from .jobs import Job, JobQueue

logger = logging.getLogger(__name__)

ALLOWED_AUDIO_SUFFIXES = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".ogg",
    ".oga",
    ".opus",
    ".webm",
    ".flac",
    ".aac",
    ".3gp",
    ".amr",
}

MODES = {"clone", "design", "auto"}

job_queue: JobQueue | None = None


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global job_queue
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings.ensure_dirs()
    db.init_db()

    engine = get_engine()
    if settings.preload:
        try:
            engine.load()
        except EngineError as exc:
            logger.error("Falha ao pré-carregar o modelo: %s", exc)

    job_queue = JobQueue(engine)
    job_queue.start()
    app.state.engine = engine
    app.state.queue = job_queue
    if engine.is_mock:
        logger.warning(
            "Rodando com o SIMULADOR: o áudio gerado não é fala real."
            " Instale o OmniVoice para usar o modelo de verdade."
        )
    yield
    job_queue.stop()


app = FastAPI(title="OmniVoice Studio", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["duration"] = format_duration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _queue() -> JobQueue:
    if job_queue is None:  # pragma: no cover - só ocorre fora do lifespan
        raise HTTPException(503, "Servidor ainda inicializando.")
    return job_queue


def render(request: Request, template: str, **context: Any) -> HTMLResponse:
    engine = request.app.state.engine
    return templates.TemplateResponse(
        request,
        template,
        {
            "engine": engine.status(),
            "version": __version__,
            **context,
        },
    )


def _safe_suffix(filename: str | None, content_type: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in ALLOWED_AUDIO_SUFFIXES:
        return suffix
    guessed = mimetypes.guess_extension(content_type or "") or ""
    if guessed.lower() in ALLOWED_AUDIO_SUFFIXES:
        return guessed.lower()
    if (content_type or "").startswith("audio/"):
        # Gravações do navegador chegam como audio/webm;codecs=opus.
        return ".webm"
    raise HTTPException(400, "Formato de áudio não suportado.")


async def _store_upload(upload: UploadFile, destination_dir: Path, stem: str) -> Path:
    suffix = _safe_suffix(upload.filename, upload.content_type)
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"{stem}{suffix}"
    size = 0
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                handle.close()
                target.unlink(missing_ok=True)
                limit = settings.max_upload_bytes // (1024 * 1024)
                raise HTTPException(413, f"Áudio maior que o limite de {limit} MB.")
            handle.write(chunk)
    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "O arquivo de áudio está vazio.")
    return target


def _clean_text(raw: str | None, field: str, limit: int) -> str:
    text = (raw or "").strip()
    if not text:
        raise HTTPException(400, f"{field} não pode ficar vazio.")
    if len(text) > limit:
        raise HTTPException(400, f"{field} excede {limit} caracteres.")
    return text


def _parse_language(raw: str | None) -> str | None:
    code = (raw or "").strip()
    if not code or code == "auto":
        return None
    if not languages.is_valid(code):
        raise HTTPException(400, "Idioma desconhecido.")
    return code


def _parse_float(raw: str | None, default: float | None = None) -> float | None:
    text = (raw or "").strip().replace(",", ".")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _title_from_text(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed[:60] + ("…" if len(collapsed) > 60 else "")


def _generation_view(row) -> dict[str, Any]:
    """Linha de ``generations`` no formato usado pelos templates."""
    return {
        "id": row["id"],
        "status": row["status"],
        "mode": row["mode"],
        "text": row["text"],
        "title": _title_from_text(row["text"]),
        "language": row["language"],
        "language_name": languages.language_name(row["language"]),
        "instruct": row["instruct"],
        "voice_id": row["voice_id"],
        "voice_name": row["voice_name"],
        "params": json.loads(row["params"] or "{}"),
        "duration": row["duration"] or 0,
        "error": row["error"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "elapsed": (row["finished_at"] or time.time()) - (row["created_at"] or 0),
        "has_audio": bool(row["audio_path"]),
    }


def _voice_view(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "ref_text": row["ref_text"],
        "language": row["language"],
        "language_name": languages.language_name(row["language"]),
        "duration": row["duration"] or 0,
        "created_at": row["created_at"],
        "has_sample": bool(row["sample_path"]),
    }


def _mode_labels() -> dict[str, str]:
    return {"clone": "Clonagem", "design": "Voz criada", "auto": "Voz automática"}


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def page_studio(request: Request) -> HTMLResponse:
    return render(
        request,
        "studio.html",
        active="studio",
        voices=[_voice_view(row) for row in db.list_voices()],
        featured_languages=languages.featured_languages(),
        all_languages=languages.all_languages(),
        categories=presets.CATEGORIES,
        defaults=GenerationParams(),
        max_text_chars=settings.max_text_chars,
    )


@app.get("/vozes", response_class=HTMLResponse)
def page_voices(request: Request) -> HTMLResponse:
    return render(
        request,
        "voices.html",
        active="voices",
        voices=[_voice_view(row) for row in db.list_voices()],
        featured_languages=languages.featured_languages(),
        all_languages=languages.all_languages(),
        engine_has_asr=request.app.state.engine.supports_asr,
    )


@app.get("/historico", response_class=HTMLResponse)
def page_history(request: Request) -> HTMLResponse:
    rows = db.list_generations(limit=settings.history_limit)
    return render(
        request,
        "history.html",
        active="history",
        generations=[_generation_view(row) for row in rows],
        mode_labels=_mode_labels(),
        total=db.count_generations(),
    )


@app.get("/ajustes", response_class=HTMLResponse)
def page_settings(request: Request) -> HTMLResponse:
    engine = request.app.state.engine
    return render(
        request,
        "settings.html",
        active="settings",
        info={
            "Motor": "Simulador (sem modelo)" if engine.is_mock else "OmniVoice",
            "Modelo": settings.model if not engine.is_mock else "—",
            "Dispositivo": settings.device or "detecção automática",
            "Modelo carregado": "sim" if engine.ready else "sob demanda",
            "Transcrição automática": "sim" if engine.supports_asr else "não",
            "Taxa de amostragem": f"{engine.sampling_rate} Hz",
            "Pasta de dados": str(settings.data_dir),
            "Limite do histórico": f"{settings.history_limit} gerações",
        },
        counts={
            "vozes": len(db.list_voices()),
            "gerações": db.count_generations(),
        },
    )


# ---------------------------------------------------------------------------
# Geração
# ---------------------------------------------------------------------------


@app.post("/gerar", response_class=HTMLResponse)
async def create_generation(
    request: Request,
    mode: str = Form("auto"),
    text: str = Form(""),
    language: str = Form(""),
    voice_id: str = Form(""),
    instruct_extra: str = Form(""),
    gender: str = Form(""),
    age: str = Form(""),
    pitch: str = Form(""),
    style: str = Form(""),
    accent: str = Form(""),
    dialect: str = Form(""),
    num_step: int = Form(32),
    guidance_scale: str = Form("2.0"),
    speed: str = Form("1.0"),
    duration: str = Form(""),
    denoise: bool = Form(False),
    preprocess_prompt: bool = Form(False),
    postprocess_output: bool = Form(False),
    normalize_text: bool = Form(False),
) -> HTMLResponse:
    if mode not in MODES:
        raise HTTPException(400, "Modo de geração inválido.")

    clean_text = _clean_text(text, "O texto", settings.max_text_chars)
    lang = _parse_language(language)

    voice_name = None
    resolved_voice_id = None
    if mode == "clone":
        if not voice_id:
            raise HTTPException(400, "Escolha uma voz da biblioteca para clonar.")
        voice = db.get_voice(voice_id)
        if voice is None:
            raise HTTPException(404, "Voz não encontrada.")
        resolved_voice_id = voice["id"]
        voice_name = voice["name"]

    instruct = None
    if mode == "design":
        instruct = presets.build_instruct(
            {
                "gender": gender,
                "age": age,
                "pitch": pitch,
                "style": style,
                "accent": accent,
                "dialect": dialect,
            },
            instruct_extra,
        )
        if not instruct:
            raise HTTPException(
                400, "Escolha ao menos um atributo para desenhar a voz."
            )

    params = GenerationParams(
        num_step=num_step,
        guidance_scale=_parse_float(guidance_scale, 2.0) or 2.0,
        speed=_parse_float(speed, 1.0) or 1.0,
        duration=_parse_float(duration, None),
        denoise=denoise,
        preprocess_prompt=preprocess_prompt,
        postprocess_output=postprocess_output,
        normalize_text=normalize_text,
    ).clamped()

    generation_id = db.create_generation(
        mode=mode,
        text=clean_text,
        language=lang,
        instruct=instruct,
        voice_id=resolved_voice_id,
        voice_name=voice_name,
        params=params.to_dict(),
    )
    _queue().submit(
        Job(
            generation_id=generation_id,
            text=clean_text,
            language=lang,
            instruct=instruct,
            voice_id=resolved_voice_id,
            params=params,
        )
    )

    return render(
        request,
        "partials/generation_card.html",
        generation=_generation_view(db.get_generation(generation_id)),
        mode_labels=_mode_labels(),
        autoplay=True,
    )


@app.get("/geracoes/{generation_id}/cartao", response_class=HTMLResponse)
def generation_card(
    request: Request, generation_id: str, autoplay: bool = False
) -> HTMLResponse:
    row = db.get_generation(generation_id)
    if row is None:
        raise HTTPException(404, "Geração não encontrada.")
    return render(
        request,
        "partials/generation_card.html",
        generation=_generation_view(row),
        mode_labels=_mode_labels(),
        autoplay=autoplay,
    )


@app.get("/geracoes/{generation_id}/audio")
def generation_audio(generation_id: str, download: bool = False) -> FileResponse:
    row = db.get_generation(generation_id)
    if row is None or not row["audio_path"]:
        raise HTTPException(404, "Áudio não encontrado.")
    path = Path(row["audio_path"])
    if not path.exists():
        raise HTTPException(404, "Arquivo de áudio ausente no disco.")
    filename = f"omnivoice-{generation_id}.wav"
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=filename if download else None,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.delete("/geracoes/{generation_id}", response_class=HTMLResponse)
def remove_generation(generation_id: str) -> HTMLResponse:
    db.delete_generation(generation_id)
    return HTMLResponse("")


@app.post("/historico/limpar")
def clear_history() -> RedirectResponse:
    db.clear_history()
    return RedirectResponse("/historico", status_code=303)


@app.get("/fila", response_class=HTMLResponse)
def queue_badge(request: Request) -> HTMLResponse:
    rows = db.active_generations()
    return render(
        request,
        "partials/queue_badge.html",
        pending=len(rows),
        current=_queue().current,
    )


# ---------------------------------------------------------------------------
# Vozes
# ---------------------------------------------------------------------------


@app.post("/vozes", response_class=HTMLResponse)
async def add_voice(
    request: Request,
    name: str = Form(""),
    ref_text: str = Form(""),
    language: str = Form(""),
    audio: UploadFile = File(...),
) -> HTMLResponse:
    clean_name = _clean_text(name, "O nome da voz", 80)
    lang = _parse_language(language)
    engine = request.app.state.engine

    transcript = (ref_text or "").strip() or None
    if transcript is None and not engine.supports_asr:
        raise HTTPException(
            400,
            "Informe a transcrição do áudio: a transcrição automática está"
            " desativada neste servidor.",
        )

    voice_stem = db.new_id()
    sample = await _store_upload(audio, settings.uploads_dir, voice_stem)

    try:
        prompt = engine.create_voice_prompt(str(sample), transcript)
    except EngineError as exc:
        sample.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc

    prompt_path = settings.voices_dir / f"{voice_stem}.prompt"
    try:
        engine.save_voice_prompt(prompt, str(prompt_path))
    except Exception:
        logger.exception("Não foi possível salvar o prompt da voz %s", voice_stem)
        prompt_path = None

    voice_id = db.create_voice(
        name=clean_name,
        ref_text=prompt.ref_text or transcript,
        language=lang,
        prompt_path=str(prompt_path) if prompt_path else None,
        sample_path=str(sample),
        duration=wav_duration(sample),
    )
    _queue().cache_voice(voice_id, prompt)

    return render(
        request,
        "partials/voice_list.html",
        voices=[_voice_view(row) for row in db.list_voices()],
        created_id=voice_id,
    )


@app.get("/vozes/lista", response_class=HTMLResponse)
def voice_list(request: Request) -> HTMLResponse:
    return render(
        request,
        "partials/voice_list.html",
        voices=[_voice_view(row) for row in db.list_voices()],
        created_id=None,
    )


@app.get("/vozes/opcoes", response_class=HTMLResponse)
def voice_options(request: Request) -> HTMLResponse:
    return render(
        request,
        "partials/voice_options.html",
        voices=[_voice_view(row) for row in db.list_voices()],
    )


@app.get("/vozes/{voice_id}/amostra")
def voice_sample(voice_id: str) -> FileResponse:
    voice = db.get_voice(voice_id)
    if voice is None or not voice["sample_path"]:
        raise HTTPException(404, "Amostra não encontrada.")
    path = Path(voice["sample_path"])
    if not path.exists():
        raise HTTPException(404, "Arquivo de amostra ausente no disco.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.post("/vozes/{voice_id}/renomear", response_class=HTMLResponse)
def rename_voice(
    request: Request, voice_id: str, name: str = Form("")
) -> HTMLResponse:
    if db.get_voice(voice_id) is None:
        raise HTTPException(404, "Voz não encontrada.")
    db.rename_voice(voice_id, _clean_text(name, "O nome da voz", 80))
    return render(
        request,
        "partials/voice_list.html",
        voices=[_voice_view(row) for row in db.list_voices()],
        created_id=None,
    )


@app.delete("/vozes/{voice_id}", response_class=HTMLResponse)
def remove_voice(request: Request, voice_id: str) -> HTMLResponse:
    db.delete_voice(voice_id)
    _queue().invalidate_voice(voice_id)
    return render(
        request,
        "partials/voice_list.html",
        voices=[_voice_view(row) for row in db.list_voices()],
        created_id=None,
    )


# ---------------------------------------------------------------------------
# API JSON (para automações)
# ---------------------------------------------------------------------------


@app.get("/api/status")
def api_status(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "version": __version__,
            "engine": request.app.state.engine.status(),
            "queue": {"pending": _queue().pending, "current": _queue().current},
            "voices": len(db.list_voices()),
            "generations": db.count_generations(),
        }
    )


@app.get("/api/geracoes/{generation_id}")
def api_generation(generation_id: str) -> JSONResponse:
    row = db.get_generation(generation_id)
    if row is None:
        raise HTTPException(404, "Geração não encontrada.")
    return JSONResponse(_generation_view(row))


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Erros em requisições HTMX viram um alerta renderizado no lugar."""
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "partials/error.html",
            {"message": exc.detail},
            status_code=200,  # HTMX só faz swap de respostas 2xx por padrão
        )
    accepts = request.headers.get("accept", "")
    if "text/html" in accepts:
        return templates.TemplateResponse(
            request,
            "error_page.html",
            {"message": exc.detail, "status": exc.status_code, "engine": None},
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Campos faltando ou malformados viram a mesma mensagem amigável."""
    missing = [
        str(error["loc"][-1])
        for error in exc.errors()
        if error.get("type") == "missing"
    ]
    detail = (
        f"Preencha os campos obrigatórios: {', '.join(missing)}."
        if missing
        else "Alguns campos do formulário são inválidos."
    )
    return await http_exception_handler(request, HTTPException(400, detail))
