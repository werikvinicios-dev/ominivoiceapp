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
    """Envia uma geração e devolve o id do cartão criado.

    Um cartão pode trazer avisos de markup; o que não pode aparecer é o
    alerta de erro, que vem sozinho e sem cartão nenhum.
    """
    response = client.post("/gerar", data=data, headers={"HX-Request": "true"})
    assert response.status_code == 200, response.text
    marker = 'id="gen-'
    assert marker in response.text, response.text
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


# ---------------------------------------------------------------------------
# Editor de audiobook: markup, prévia, projetos e preferências
# ---------------------------------------------------------------------------


def test_markup_validation_reports_segments_and_pauses(client):
    response = client.post(
        "/markup/validar",
        data={"text": "[soft] Um.[pause=1.2]Dois."},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "2</b> segmento" in response.text
    assert "1.2s" in response.text or "1,2" in response.text


def test_markup_validation_flags_unknown_tags(client):
    response = client.post(
        "/markup/validar",
        data={"text": "Texto [inexistente] aqui."},
        headers={"HX-Request": "true"},
    )
    assert "Tag desconhecida" in response.text


def test_generation_with_markup_inserts_real_silence(client):
    generation_id = _generate(
        client, mode="auto", text="Olá.[pause=1.5]Mundo.", num_step="8"
    )
    result = _wait_done(client, generation_id)
    assert result["status"] == "done"
    assert result["segments_total"] == 2
    assert result["segments_done"] == 2

    audio = client.get(f"/geracoes/{generation_id}/audio").content
    # Cabeçalho WAV de 44 bytes; procura 1,5 s de amostras zeradas.
    silence = b"\x00\x00" * int(1.5 * 24000)
    assert silence in audio[44:]


def test_unknown_tag_is_kept_in_history_but_never_spoken(client):
    generation_id = _generate(
        client, mode="auto", text="Antes [xyz-abc] depois.", num_step="8"
    )
    result = _wait_done(client, generation_id)
    assert result["status"] == "done"
    # O texto do usuário fica intacto no histórico...
    assert "[xyz-abc]" in result["text"]
    # ...e o aviso explica que a tag não foi falada.
    assert any("desconhecida" in w["message"] for w in result["warnings"])


def test_native_tags_do_not_warn(client):
    generation_id = _generate(
        client, mode="auto", text="[sigh] Enfim. [laughter] Pois é.", num_step="8"
    )
    result = _wait_done(client, generation_id)
    assert result["warnings"] == []
    assert result["status"] == "done"


def test_preview_generates_outside_the_history(client):
    before = client.get("/api/status").json()["generations"]
    response = client.post(
        "/testar-trecho",
        data={"mode": "auto", "text": "Só um trecho curto.", "num_step": "8"},
        headers={"HX-Request": "true"},
    )
    assert 'id="gen-' in response.text, response.text
    generation_id = response.text.split('id="gen-')[1].split('"')[0]

    result = _wait_done(client, generation_id)
    assert result["status"] == "done"
    assert result["is_preview"] is True
    assert client.get("/api/status").json()["generations"] == before
    assert "prévia" not in client.get("/historico").text or generation_id not in (
        client.get("/historico").text
    )


def test_preview_rejects_an_overlong_excerpt(client):
    response = client.post(
        "/testar-trecho",
        data={"mode": "auto", "text": "a" * 5000},
        headers={"HX-Request": "true"},
    )
    assert 'class="alert"' in response.text


def test_pause_preferences_change_the_generated_audio(client):
    saved = client.post(
        "/ajustes/pausas",
        data={"pause": "0.25", "long_pause": "3.0"},
        headers={"HX-Request": "true"},
    )
    assert saved.status_code == 200
    assert client.get("/api/status").json()["pauses"] == {
        "pause": 0.25,
        "long_pause": 3.0,
    }

    generation_id = _generate(client, mode="auto", text="A.[pause]B.", num_step="8")
    _wait_done(client, generation_id)
    audio = client.get(f"/geracoes/{generation_id}/audio").content
    assert b"\x00\x00" * int(0.25 * 24000) in audio[44:]

    client.post(
        "/ajustes/pausas",
        data={"pause": "0.6", "long_pause": "1.5"},
        headers={"HX-Request": "true"},
    )


def test_projects_roundtrip(client):
    created = client.post(
        "/projetos",
        data={"name": "Capítulo 1", "text": "[soft] Era uma vez.[pause=1]Fim."},
        headers={"HX-Request": "true"},
    )
    assert "Capítulo 1" in created.text

    listed = client.get("/projetos").text
    assert "Capítulo 1" in listed
    project_id = listed.split('data-load-project="')[1].split('"')[0]

    loaded = client.get(f"/projetos/{project_id}").json()
    assert loaded["text"] == "[soft] Era uma vez.[pause=1]Fim."

    client.post(
        "/projetos",
        data={"name": "Capítulo 1 revisado", "text": "Outro texto.", "project_id": project_id},
        headers={"HX-Request": "true"},
    )
    assert client.get(f"/projetos/{project_id}").json()["name"] == "Capítulo 1 revisado"

    removed = client.delete(f"/projetos/{project_id}", headers={"HX-Request": "true"})
    assert "Capítulo 1 revisado" not in removed.text


def test_project_requires_name_and_text(client):
    response = client.post(
        "/projetos", data={"name": "", "text": "oi"}, headers={"HX-Request": "true"}
    )
    assert 'class="alert"' in response.text


def test_tag_catalog_marks_what_is_real(client):
    catalog = client.get("/api/tags").json()
    assert "[sigh]" in [item["tag"] for item in catalog["native"]]
    assert all(item["real"] for item in catalog["native"])
    real_styles = [item["tag"] for item in catalog["style"] if item["real"]]
    assert real_styles == ["[whisper]"]


def test_text_without_tags_still_produces_one_segment(client):
    generation_id = _generate(
        client, mode="auto", text="Texto comum, sem nenhuma marcação.", num_step="8"
    )
    result = _wait_done(client, generation_id)
    assert result["status"] == "done"
    assert result["segments_total"] == 1
    assert result["warnings"] == []


def test_text_with_only_pauses_is_rejected(client):
    response = client.post(
        "/gerar",
        data={"mode": "auto", "text": "[pause=2][long-pause]"},
        headers={"HX-Request": "true"},
    )
    assert "nada para falar" in response.text


def test_design_instruct_only_uses_terms_the_model_accepts():
    """O instruct do OmniVoice é whitelist: nada fora dela pode escapar."""
    from app.presets import CATEGORIES, build_instruct

    assert build_instruct({"gender": "female", "age": "young adult"}) == (
        "female, young adult"
    )
    # Valores inventados são descartados em vez de chegarem ao modelo.
    assert build_instruct({"gender": "narrador calmo"}) is None
    assert build_instruct({"style": "sad"}) is None

    # Toda opção oferecida na interface existe na whitelist do OmniVoice.
    from omnivoice_whitelist import VALID  # type: ignore[import-not-found]

    for category in CATEGORIES:
        for option in category.options:
            assert option.value in VALID, option.value
