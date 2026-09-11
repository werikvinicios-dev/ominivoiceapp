#!/usr/bin/env bash
# Inicia o OmniVoice Studio acessível na rede local (para abrir no celular).
set -euo pipefail

cd "$(dirname "$0")"

HOST="${OMNI_HOST:-0.0.0.0}"
PORT="${OMNI_PORT:-8080}"

PYTHON="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

if ! "$PYTHON" -c "import fastapi" >/dev/null 2>&1; then
  echo "Dependências ausentes. Rode:  pip install -r requirements.txt" >&2
  exit 1
fi

# Mostra os endereços da máquina para digitar no navegador do celular.
echo "OmniVoice Studio — abra no celular (mesma rede Wi-Fi):"
if command -v hostname >/dev/null 2>&1; then
  for ip in $(hostname -I 2>/dev/null || true); do
    echo "    http://${ip}:${PORT}"
  done
fi
echo "    http://localhost:${PORT}   (nesta máquina)"
echo

exec "$PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
