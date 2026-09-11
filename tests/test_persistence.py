"""Persistência separada (Drive) e retomada de gerações interrompidas."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest


@pytest.fixture
def persistent_app(tmp_path, monkeypatch):
    """Sobe o app com pasta persistente separada da pasta de runtime."""
    runtime = tmp_path / "runtime"
    persist = tmp_path / "drive"
    runtime.mkdir()
    persist.mkdir()

    from fastapi.testclient import TestClient

    from app import config

    monkeypatch.setattr(config.settings, "data_dir", runtime)
    monkeypatch.setattr(config.settings, "persist_dir_raw", str(persist))
    monkeypatch.setattr(config.settings, "backend", "mock")
    monkeypatch.setenv("OMNI_BACKEND", "mock")

    # Conexões novas: cada thread abre a sua para o banco recém-criado.
    from app import db

    monkeypatch.setattr(db, "_local", threading.local())

    from app.main import app

    with TestClient(app) as client:
        yield client, runtime, persist


def _wait_done(client, generation_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/geracoes/{generation_id}").json()
        if payload["status"] in {"done", "error"}:
            return payload
        time.sleep(0.2)
    raise AssertionError("geração não terminou a tempo")


def _generate(client, **data):
    response = client.post("/gerar", data=data, headers={"HX-Request": "true"})
    assert 'id="gen-' in response.text, response.text
    return response.text.split('id="gen-')[1].split('"')[0]


def test_database_and_audio_land_in_the_persistent_folder(persistent_app):
    client, runtime, persist = persistent_app

    assert (persist / "studio.db").exists()
    assert client.get("/api/status").json()["persistent"] is True

    generation_id = _generate(client, mode="auto", text="Guardar isto.", num_step="8")
    _wait_done(client, generation_id)

    assert (persist / "audio" / f"{generation_id}.wav").exists()
    assert not (runtime / "audio").exists()


def test_previews_stay_out_of_the_persistent_folder(persistent_app):
    """Evita escrever no Drive a cada teste de trecho."""
    client, runtime, persist = persistent_app

    response = client.post(
        "/testar-trecho",
        data={"mode": "auto", "text": "Trecho de teste.", "num_step": "8"},
        headers={"HX-Request": "true"},
    )
    generation_id = response.text.split('id="gen-')[1].split('"')[0]
    _wait_done(client, generation_id)

    assert (runtime / "temp" / f"{generation_id}.wav").exists()
    assert not (persist / "audio" / f"{generation_id}.wav").exists()


def test_voices_and_projects_are_persisted(persistent_app, tmp_path):
    client, _runtime, persist = persistent_app

    generation_id = _generate(client, mode="auto", text="Amostra.", num_step="8")
    _wait_done(client, generation_id)
    sample = client.get(f"/geracoes/{generation_id}/audio").content

    client.post(
        "/vozes",
        data={"name": "Voz salva", "ref_text": "Amostra."},
        files={"audio": ("ref.wav", sample, "audio/wav")},
        headers={"HX-Request": "true"},
    )
    client.post(
        "/projetos",
        data={"name": "Capítulo", "text": "Texto do capítulo."},
        headers={"HX-Request": "true"},
    )

    assert list((persist / "voices").glob("*.prompt"))
    # O banco no Drive guarda voz e projeto — é o que permite retomar depois.
    assert client.get("/api/status").json()["voices"] == 1
    assert "Capítulo" in client.get("/projetos").text


def test_interrupted_generation_is_requeued_and_reuses_its_segments(
    tmp_path, monkeypatch
):
    """Uma sessão que cai no meio de um capítulo retoma de onde parou."""
    from fastapi.testclient import TestClient

    from app import config, db

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config.settings, "data_dir", data_dir)
    monkeypatch.setattr(config.settings, "persist_dir_raw", "")
    monkeypatch.setattr(config.settings, "backend", "mock")
    monkeypatch.setenv("OMNI_BACKEND", "mock")
    monkeypatch.setattr(db, "_local", threading.local())

    from app.main import app

    # Primeira "sessão": registra a geração e simula a queda no meio dela.
    with TestClient(app) as client:
        generation_id = _generate(
            client,
            mode="auto",
            text="Um.[pause=0.5]Dois.[pause=0.5]Três.",
            num_step="8",
        )
        _wait_done(client, generation_id)

    work = data_dir / "temp" / f"{generation_id}.segments"
    work.mkdir(parents=True, exist_ok=True)

    # Volta o registro para "running" e devolve um segmento ao checkpoint.
    from app.audio import write_wav

    write_wav(work / "seg_0000.wav", [0.25] * 24000, 24000)
    db.connect().execute(
        "UPDATE generations SET status = 'running', audio_path = NULL WHERE id = ?",
        (generation_id,),
    )
    db.connect().commit()

    # Segunda "sessão": o app deve recolocar a geração na fila sozinho.
    monkeypatch.setattr(db, "_local", threading.local())
    with TestClient(app) as client:
        result = _wait_done(client, generation_id)
        assert result["status"] == "done"
        audio = client.get(f"/geracoes/{generation_id}/audio").content

    # O segmento do checkpoint (1 s de valor constante) foi reaproveitado.
    assert len(audio) > 44 + 2 * 24000


def test_pause_durations_survive_a_resume(tmp_path):
    """A retomada reproduz as mesmas pausas da geração original."""
    from app.engines.base import GenerationParams
    from app.jobs import job_from_row

    row = {
        "id": "abc",
        "text": "A.[pause]B.",
        "language": None,
        "instruct": None,
        "voice_id": None,
        "params": json.dumps({**GenerationParams().to_dict(), "pause": 0.25, "long_pause": 4.0}),
        "is_preview": 0,
    }
    job = job_from_row(row)
    assert job.pause == 0.25
    assert job.long_pause == 4.0
    assert job.is_preview is False


def test_data_dir_defaults_to_everything_together(monkeypatch, tmp_path):
    from app import config

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "persist_dir_raw", "")
    assert config.settings.persist_dir == tmp_path
    assert config.settings.is_persistent is False
    assert config.settings.audio_dir.is_relative_to(tmp_path)


def test_colab_launcher_reports_a_missing_gpu_without_crashing():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import colab

    # Sem GPU aqui: a função tem de devolver None em vez de estourar.
    assert colab.detect_gpu() is None or isinstance(colab.detect_gpu(), str)
    # Sem cloudflared instalado, o túnel degrada em silêncio.
    colab.CLOUDFLARED = colab.Path("/nao-existe")
    assert colab._open_tunnel(9) == (None, None)


def test_cloudflared_output_never_goes_to_an_unread_pipe():
    """Um PIPE não drenado trava o cloudflared e derruba o túnel (erro 1033)."""
    import inspect
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import colab

    source = inspect.getsource(colab._start_cloudflared)
    assert "subprocess.PIPE" not in source
    assert "log_path.open" in source


def test_keep_alive_stops_when_the_server_dies():
    """A célula do Colab não pode ficar presa depois que o servidor cai."""
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import colab

    class DeadProcess:
        def poll(self):
            return 1

        def terminate(self):
            pass

    session = {"server": DeadProcess(), "tunnel": None, "url": None}
    colab.keep_alive(session, check_every=0.01)  # retorna em vez de travar


def test_tunnel_alive_detects_a_dead_process():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import colab

    class DeadProcess:
        def poll(self):
            return 1

    assert colab.tunnel_alive({"tunnel": None, "url": "x"}) is False
    assert colab.tunnel_alive({"tunnel": DeadProcess(), "url": "x"}) is False
    # O proxy do Colab não tem processo próprio para vigiar.
    assert colab.tunnel_alive(
        {"tunnel": DeadProcess.__new__(DeadProcess), "url": "https://colab.internal"}
    ) is not None


def _colab():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import colab

    return colab


def test_launcher_never_reuses_a_busy_port():
    """No Colab a 8080 já está ocupada; insistir nela matava o servidor."""
    import http.server
    import threading

    colab = _colab()

    class Intruder(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Intruder)
    busy = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert colab._port_is_free(busy) is False
        assert colab.pick_port(busy) != busy
        # E a porta escolhida está mesmo livre.
        assert colab._port_is_free(colab.pick_port(busy))
    finally:
        server.shutdown()


def test_launcher_rejects_a_stranger_on_the_port():
    """Outro serviço na porta não pode ser confundido com o Studio."""
    import http.server
    import threading

    colab = _colab()

    class Stranger(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"algo": "outro servico"}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Stranger)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert colab._studio_responds(port) is False
        # E a espera desiste em vez de anunciar um endereço enganoso.
        assert colab._wait_for_port(port, timeout=2.0) is False
    finally:
        server.shutdown()


def test_wait_for_port_gives_up_when_the_process_dies():
    colab = _colab()

    class DeadProcess:
        def poll(self):
            return 3

    assert colab._wait_for_port(1, DeadProcess(), timeout=30.0) is False
