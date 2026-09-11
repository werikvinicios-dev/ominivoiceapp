#!/usr/bin/env python3
"""Preparação e inicialização do OmniVoice Studio no Google Colab.

O notebook chama só estas funções — a aplicação em si continua sendo a do
repositório, sem nenhuma cópia dentro do caderno.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_URL = "https://github.com/werikvinicios-dev/ominivoiceapp.git"
REPO_DIR = Path("/content/ominivoiceapp")
DRIVE_MOUNT = Path("/content/drive")
DRIVE_DATA = DRIVE_MOUNT / "MyDrive" / "OmniVoiceStudio"
CLOUDFLARED = Path("/usr/local/bin/cloudflared")
CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64"
)


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, **kwargs)


def _log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------


def detect_gpu() -> str | None:
    """Nome da GPU disponível, ou ``None`` quando não há."""
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass
    if shutil.which("nvidia-smi"):
        try:
            output = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            ).stdout.strip()
            if output:
                return output.splitlines()[0]
        except Exception:
            pass
    return None


def fetch_repo(branch: str = "") -> Path:
    """Clona (ou atualiza) o repositório do Studio.

    ``branch`` vazio usa a branch padrão do repositório — assim o notebook
    continua funcionando independentemente de como as branches evoluírem.
    """
    if REPO_DIR.exists():
        _log(f"Atualizando {REPO_DIR}…")
        target = branch or _default_branch()
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "fetch", "--depth", "1", "origin", target],
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "checkout", "-B", target,
             f"origin/{target}"],
            check=False,
        )
    else:
        _log(f"Clonando o OmniVoice Studio em {REPO_DIR}…")
        command = ["git", "clone", "--depth", "1"]
        if branch:
            command += ["--branch", branch]
        _run([*command, REPO_URL, str(REPO_DIR)])
    return REPO_DIR


def _default_branch() -> str:
    """Branch padrão do repositório remoto (o HEAD do origin)."""
    try:
        output = subprocess.run(
            ["git", "-C", str(REPO_DIR), "remote", "show", "origin"],
            capture_output=True, text=True, timeout=60, check=False,
        ).stdout
        for line in output.splitlines():
            if "HEAD branch:" in line:
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "main"


def install_studio() -> None:
    """Dependências do app web. Leves — nada de modelo aqui."""
    _log("Instalando as dependências do Studio…")
    _run([sys.executable, "-m", "pip", "install", "-q",
          "fastapi", "uvicorn[standard]", "jinja2", "python-multipart"])


def install_omnivoice() -> bool:
    """Instala o OmniVoice. Devolve ``False`` se não der (segue no simulador)."""
    try:
        import omnivoice  # noqa: F401

        _log("OmniVoice já está instalado.")
        return True
    except ImportError:
        pass
    _log("Instalando o OmniVoice (pode levar alguns minutos)…")
    try:
        _run([sys.executable, "-m", "pip", "install", "-q", "omnivoice"])
        return True
    except subprocess.CalledProcessError:
        _log("Não foi possível instalar o OmniVoice — seguindo no simulador.")
        return False


def install_cloudflared() -> bool:
    """Baixa o cloudflared, que cria o endereço HTTPS público."""
    if CLOUDFLARED.exists():
        return True
    _log("Baixando o cloudflared (túnel HTTPS)…")
    try:
        urllib.request.urlretrieve(CLOUDFLARED_URL, CLOUDFLARED)
        CLOUDFLARED.chmod(0o755)
        return True
    except Exception as error:
        _log(f"Falha ao baixar o cloudflared: {error}")
        return False


def mount_drive() -> Path | None:
    """Monta o Google Drive e devolve a pasta persistente do Studio."""
    try:
        from google.colab import drive
    except ImportError:
        _log("Fora do Colab: o Drive não será montado.")
        return None
    if not (DRIVE_MOUNT / "MyDrive").exists():
        drive.mount(str(DRIVE_MOUNT))
    DRIVE_DATA.mkdir(parents=True, exist_ok=True)
    _log(f"Drive pronto: {DRIVE_DATA}")
    return DRIVE_DATA


def setup(use_drive: bool = True, branch: str = "") -> dict:
    """Prepara tudo e devolve o resumo do ambiente."""
    gpu = detect_gpu()
    if gpu:
        _log(f"GPU detectada: {gpu}")
    else:
        _log(
            "Nenhuma GPU disponível.\n"
            "  No Colab: Ambiente de execução → Alterar o tipo de ambiente"
            " → GPU (T4) e execute tudo de novo.\n"
            "  Sem GPU o Studio abre no modo simulador: a interface toda"
            " funciona, mas o áudio não é fala de verdade."
        )

    repo = fetch_repo(branch)
    install_studio()
    has_model = install_omnivoice() if gpu else False
    tunnel = install_cloudflared()
    persist = mount_drive() if use_drive else None
    if use_drive and persist is None:
        _log("Sem Drive: os dados desta sessão são temporários.")

    return {
        "repo": str(repo),
        "gpu": gpu,
        "backend": "omnivoice" if (gpu and has_model) else "mock",
        "persist_dir": str(persist) if persist else "",
        "tunnel": tunnel,
    }


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def _wait_for_port(port: int, timeout: float = 900.0) -> bool:
    """Espera o servidor subir (o modelo pode demorar a carregar)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1.0)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(1.0)
    return False


_URL_RE = re.compile(r"https://[-\w.]+\.trycloudflare\.com")


def _tunnel_is_live(url: str, attempts: int = 6, delay: float = 5.0) -> bool:
    """Confirma que o endereço público chega mesmo ao servidor local.

    O cloudflared imprime a URL antes de registrar a conexão com a borda; sem
    esta checagem o notebook anunciaria um link que ainda responde 530.
    """
    for _ in range(attempts):
        time.sleep(delay)
        try:
            with urllib.request.urlopen(f"{url}/api/status", timeout=20) as response:
                if response.status == 200:
                    return True
        except Exception:
            continue
    return False


def _tunnel_log() -> Path:
    base = Path("/content") if Path("/content").is_dir() else Path.cwd()
    return base / "cloudflared.log"


def _start_cloudflared(port: int, protocol: str | None, timeout: float = 60.0):
    """Sobe o cloudflared e devolve (url, processo) assim que a URL aparece.

    A saída vai para um arquivo, nunca para um ``PIPE``: o cloudflared registra
    log continuamente e, se ninguém drenasse o pipe, ele travaria ao escrever
    assim que o buffer do sistema enchesse — e a Cloudflare derrubaria o túnel
    com erro 1033 no meio do uso.
    """
    command = [
        str(CLOUDFLARED), "tunnel", "--url", f"http://127.0.0.1:{port}",
        "--no-autoupdate",
    ]
    if protocol:
        command += ["--protocol", protocol]

    log_path = _tunnel_log()
    handle = log_path.open("w")
    process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
    process._omni_log = handle  # mantém o arquivo aberto enquanto o processo vive

    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return None, None
        found = _URL_RE.search(log_path.read_text(errors="replace"))
        if found:
            return found.group(0), process
        time.sleep(0.5)

    process.terminate()
    return None, None


def _colab_proxy_url(port: int) -> str | None:
    """Endereço HTTPS servido pelo próprio Colab.

    Não depende de nenhum serviço externo, mas só funciona no mesmo navegador
    que está com o notebook aberto — no celular, isso significa abrir o Colab
    pelo navegador (não pelo aplicativo).
    """
    try:
        from google.colab.output import eval_js

        url = eval_js(f"google.colab.kernel.proxyPort({port})")
        return url.rstrip("/") if url else None
    except Exception:
        return None


def _open_tunnel(port: int) -> tuple[str | None, object]:
    """Abre o endereço HTTPS público, testando os protocolos disponíveis.

    QUIC é o padrão do cloudflared, mas algumas redes bloqueiam UDP; nesse
    caso o HTTP/2 sobre TCP costuma passar.
    """
    if not CLOUDFLARED.exists():
        return None, None

    for protocol in (None, "http2"):
        url, process = _start_cloudflared(port, protocol)
        if not url:
            continue
        if _tunnel_is_live(url):
            return url, process
        _log(
            f"O túnel {url} não respondeu"
            f"{' (QUIC)' if protocol is None else ' (HTTP/2)'};"
            " tentando outro protocolo…"
        )
        process.terminate()

    return None, None


def launch(
    config: dict,
    port: int = 8080,
    preload: bool = True,
) -> dict:
    """Sobe o Studio e devolve os endereços de acesso.

    O HTTPS importa: sem ele o navegador do celular bloqueia o microfone.
    """
    repo = Path(config.get("repo") or REPO_DIR)
    env = os.environ.copy()
    env["OMNI_BACKEND"] = config.get("backend", "auto")
    env["OMNI_PORT"] = str(port)
    env["OMNI_PRELOAD"] = "1" if preload and config.get("backend") == "omnivoice" else "0"
    if config.get("persist_dir"):
        env["OMNI_PERSIST_DIR"] = config["persist_dir"]
    env.setdefault("HF_HOME", "/content/hf-cache")

    # Fora do Colab (testes, execução local) /content não existe.
    log_dir = Path("/content") if Path("/content").is_dir() else repo
    log_path = log_dir / "omnivoice-studio.log"
    _log("Iniciando o servidor…")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(repo),
        env=env,
        stdout=log_path.open("w"),
        stderr=subprocess.STDOUT,
    )

    if not _wait_for_port(port):
        _log(f"O servidor não respondeu. Veja o log em {log_path}.")
        server.terminate()
        return {"server": server, "url": None, "proxy": None,
                "port": port, "log": str(log_path)}

    _log("Servidor no ar. Abrindo os endereços HTTPS…")
    url, tunnel = _open_tunnel(port)
    proxy = _colab_proxy_url(port)

    _log("\n" + "=" * 60)
    _log("  OMNIVOICE STUDIO PRONTO")
    _log("=" * 60)

    if url:
        _log("\n  1) LINK PÚBLICO (qualquer aparelho):")
        _log(f"     {url}")
    if proxy:
        _log("\n  2) LINK DO COLAB (só neste navegador, mas não cai):")
        _log(f"     {proxy}")

    if not url and not proxy:
        _log(
            f"\n  Nenhum endereço HTTPS subiu. O servidor está na porta {port}."
            "\n  Rode esta célula novamente."
        )
    else:
        _log(
            "\n  ATENÇÃO: estes endereços mudam a cada execução."
            "\n  Um link de uma sessão anterior sempre dará erro 1033."
        )

    if config.get("backend") == "mock":
        _log("\n  Modo simulador: a interface funciona, mas o áudio")
        _log("  gerado não é fala de verdade.")
    if not config.get("persist_dir"):
        _log("\n  Sem Drive: os dados desta sessão são temporários.")

    _log("\n  Mantenha esta célula rodando enquanto usar o Studio.")
    _log("=" * 60 + "\n")

    return {
        "server": server,
        "tunnel": tunnel,
        "url": url,
        "proxy": proxy,
        "port": port,
        "log": str(log_path),
    }


def tunnel_alive(session: dict) -> bool:
    """``True`` se o túnel continua de pé e respondendo."""
    tunnel = session.get("tunnel")
    if tunnel is None or tunnel.poll() is not None:
        return False
    url = session.get("url")
    if not url or ".trycloudflare.com" not in url:
        return True  # proxy do Colab: não há processo para vigiar
    try:
        with urllib.request.urlopen(f"{url}/api/status", timeout=15) as response:
            return response.status == 200
    except Exception:
        return False


def keep_alive(session: dict, port: int = 8080, check_every: float = 30.0) -> None:
    """Mantém a célula viva e reabre o túnel se ele cair.

    Túneis gratuitos do trycloudflare caem sozinhos de vez em quando; sem isto
    o usuário veria um erro 1033 e teria de reiniciar tudo na mão.
    """
    server = session.get("server")
    failures = 0
    try:
        while server is not None and server.poll() is None:
            time.sleep(check_every)
            if session.get("url") is None or tunnel_alive(session):
                failures = 0
                continue

            failures += 1
            _log(f"O túnel caiu (verificação {failures}). Reabrindo…")
            old = session.get("tunnel")
            if old is not None and old.poll() is None:
                old.terminate()

            url, tunnel = _open_tunnel(port)
            if url:
                session["url"], session["tunnel"] = url, tunnel
                _log(f"\n  NOVO ENDEREÇO:  {url}\n")
                failures = 0
            elif failures >= 3:
                _log(
                    "Não consegui reabrir o túnel. Rode esta célula novamente,"
                    " ou use o link do Colab, que não depende de túnel."
                )
                return
    except KeyboardInterrupt:
        _log("Encerrando o Studio…")
        return
    finally:
        if server is not None and server.poll() is not None:
            _log(
                "\n  O SERVIDOR PAROU — é por isso que os links dão erro 1033."
                f"\n  Veja o motivo com:  !tail -40 {session.get('log')}"
            )
        for key in ("tunnel", "server"):
            process = session.get(key)
            if process is not None and process.poll() is None:
                process.terminate()


# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------


def _tail(path: Path, lines: int = 12) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except Exception as error:
        return f"(não consegui ler {path}: {error})"


def diagnose(session: dict | None = None) -> None:
    """Imprime o estado de cada peça — servidor, porta, túnel e endereços.

    Serve para saber onde o erro 1033 nasce: túnel caído, servidor caído ou
    link de uma sessão antiga.
    """
    session = session or {}
    port = session.get("port", 8080)

    print("=" * 60)
    print("  DIAGNÓSTICO DO OMNIVOICE STUDIO")
    print("=" * 60)

    server = session.get("server")
    if server is None:
        print("\n[servidor]  nenhum registrado nesta sessão")
    else:
        alive = server.poll() is None
        print(f"\n[servidor]  {'rodando' if alive else f'MORTO (código {server.poll()})'}")

    with socket.socket() as probe:
        probe.settimeout(2.0)
        listening = probe.connect_ex(("127.0.0.1", port)) == 0
    print(f"[porta {port}]  {'respondendo' if listening else 'SEM RESPOSTA'}")

    if listening:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=10
            ) as response:
                import json

                status = json.load(response)
            print(
                f"[app]       ok — motor: {status['engine']['name']}"
                f"{' (simulador)' if status['engine']['mock'] else ''}"
                f", persistente: {status['persistent']}"
            )
        except Exception as error:
            print(f"[app]       NÃO RESPONDEU: {error}")

    tunnel = session.get("tunnel")
    if tunnel is None:
        print("[túnel]     não está em uso")
    else:
        print(f"[túnel]     {'rodando' if tunnel.poll() is None else 'MORTO'}")

    for label, url in (("link público", session.get("url")),
                       ("link do Colab", session.get("proxy"))):
        if not url:
            continue
        try:
            with urllib.request.urlopen(f"{url}/api/status", timeout=20) as response:
                verdict = f"OK ({response.status})"
        except urllib.error.HTTPError as error:
            verdict = f"HTTP {error.code}" + (
                "  <- o túnel não está conectado à Cloudflare"
                if error.code in (502, 503, 530) else ""
            )
        except Exception as error:
            verdict = f"falhou: {type(error).__name__}"
        print(f"[{label}]  {url}\n              {verdict}")

    print("\n--- últimas linhas do servidor ---")
    print(_tail(Path(session.get("log", "/content/omnivoice-studio.log"))))
    print("\n--- últimas linhas do túnel ---")
    print(_tail(_tunnel_log()))
    print("=" * 60)
