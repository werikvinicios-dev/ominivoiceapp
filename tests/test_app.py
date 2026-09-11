"""Testes de fumaça do OmniVoice Studio (rodam sobre o motor simulado)."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("OMNI_BACKEND", "mock")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Sobe o app com uma pasta de dados isolada."""
    data_dir = tmp_path_factory.mktemp("studio-data")
    os.environ["OMNI_DATA_DIR"] = str(data_dir)
    os.environ["OMNI_BACKEND"] = "mock"

    from fastapi.testclient import TestClient

    from app import config

    config.settings.data_dir = data_dir
    config.settings.backend = "mock"

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _wait_done(client, generation_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/geracoes/{generation_id}").json()
        if payload["status"] in {"done", "error"}:
            return payload
        time.sleep(0.2)
    raise AssertionError("geração não terminou a tempo")


def _generate(client, **data) -> str:
    response = client.post("/gerar", data=data, headers={"HX-Request": "true"})
    assert response.status_code == 200, response.text
    assert "alert" not in response.text, response.text
    marker = 'id="gen-'
    start = response.text.index(marker) + len(marker)
    return response.text[start : response.text.index('"', start)]


@pytest.mark.parametrize("path", ["/", "/vozes", "/historico", "/ajustes"])
def test_pages_render(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "OmniVoice Studio" in response.text


def test_status_reports_mock_engine(client):
    payload = client.get("/api/status").json()
    assert payload["engine"]["mock"] is True
    assert payload["engine"]["ready"] is True


def test_auto_generation_produces_wav(client):
    generation_id = _generate(
        client, mode="auto", text="Olá, mundo.", language="pt", num_step="8"
    )
    result = _wait_done(client, generation_id)
    assert result["status"] == "done", result["error"]
    assert result["duration"] > 0

    audio = client.get(f"/geracoes/{generation_id}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF"


def test_design_mode_builds_instruct(client):
    generation_id = _generate(
        client,
        mode="design",
        text="Bom dia.",
        gender="female",
        age="young adult",
        num_step="8",
    )
    result = _wait_done(client, generation_id)
    assert result["status"] == "done"
    assert result["instruct"] == "female, young adult"


def test_voice_roundtrip_and_clone(client, tmp_path):
    # Um WAV qualquer serve como referência para o simulador.
    generation_id = _generate(client, mode="auto", text="Amostra.", num_step="8")
    _wait_done(client, generation_id)
    sample = client.get(f"/geracoes/{generation_id}/audio").content
    reference = tmp_path / "ref.wav"
    reference.write_bytes(sample)

    created = client.post(
        "/vozes",
        data={"name": "Voz teste", "ref_text": "Amostra.", "language": "pt"},
        files={"audio": ("ref.wav", reference.read_bytes(), "audio/wav")},
        headers={"HX-Request": "true"},
    )
    assert created.status_code == 200
    assert "Voz teste" in created.text

    voice_id = created.text.split('id="voice-')[1].split('"')[0]

    cloned = _generate(
        client,
        mode="clone",
        text="Texto clonado.",
        voice_id=voice_id,
        num_step="8",
    )
    result = _wait_done(client, cloned)
    assert result["status"] == "done"
    assert result["voice_name"] == "Voz teste"

    renamed = client.post(
        f"/vozes/{voice_id}/renomear",
        data={"name": "Outro nome"},
        headers={"HX-Request": "true"},
    )
    assert "Outro nome" in renamed.text

    removed = client.delete(f"/vozes/{voice_id}", headers={"HX-Request": "true"})
    assert "Outro nome" not in removed.text


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"mode": "auto"}, "texto não pode ficar vazio"),
        ({"mode": "clone", "text": "oi"}, "Escolha uma voz"),
        ({"mode": "design", "text": "oi"}, "ao menos um atributo"),
        ({"mode": "auto", "text": "oi", "language": "zzz"}, "Idioma desconhecido"),
        ({"mode": "inexistente", "text": "oi"}, "Modo de geração inválido"),
    ],
)
def test_invalid_requests_return_inline_alert(client, data, expected):
    response = client.post("/gerar", data=data, headers={"HX-Request": "true"})
    assert response.status_code == 200  # HTMX só troca conteúdo em respostas 2xx
    assert 'class="alert"' in response.text
    assert expected in response.text


def test_rejects_non_audio_upload(client):
    response = client.post(
        "/vozes",
        data={"name": "X", "ref_text": "oi"},
        files={"audio": ("nota.txt", b"nao sou audio", "text/plain")},
        headers={"HX-Request": "true"},
    )
    assert "Formato de áudio não suportado" in response.text


def test_generation_can_be_deleted(client):
    generation_id = _generate(client, mode="auto", text="Apagar.", num_step="8")
    assert _wait_done(client, generation_id)["status"] == "done"

    from app import config

    audio_file = config.settings.audio_dir / f"{generation_id}.wav"
    assert audio_file.exists()

    assert client.delete(f"/geracoes/{generation_id}").status_code == 200
    assert client.get(f"/api/geracoes/{generation_id}").status_code == 404
    assert not audio_file.exists()  # o WAV sai do disco junto com o registro
