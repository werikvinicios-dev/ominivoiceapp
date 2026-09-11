# OmniVoice Studio

Aplicativo web para o [OmniVoice](https://github.com/k2-fsa/OmniVoice) — modelo
de síntese de voz *zero-shot* multilíngue (600+ idiomas) da equipe Next-gen
Kaldi da Xiaomi.

A interface foi feita para **celular**: você roda o servidor no computador e
abre o endereço no navegador do telefone, na mesma rede Wi-Fi.

## O que dá para fazer

| Recurso | Descrição |
|---|---|
| **Voz automática** | Digite o texto e ouça; o modelo escolhe a voz. |
| **Clonar voz** | Grave 3–10 s pelo microfone do celular (ou envie um arquivo) e fale com aquela voz. |
| **Criar voz** | Monte uma voz por atributos: gênero, idade, tom, sussurro, sotaque, dialeto. |
| **Biblioteca de vozes** | Vozes salvas ficam prontas para reuso, com prévia, renomear e excluir. |
| **Histórico** | Toda geração fica guardada com player e download em WAV. |
| **Fila** | As gerações entram numa fila de um worker só — o modelo ocupa a GPU inteira. |
| **600+ idiomas** | Seletor completo, com os mais usados no topo. |
| **Instalável** | "Adicionar à tela de início" abre em tela cheia, como um app. |

Sem login e sem nuvem: tudo fica no computador que roda o servidor.

## Instalação rápida

```bash
git clone https://github.com/werikvinicios-dev/ominivoiceapp.git
cd ominivoiceapp

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

./run.sh
```

O `run.sh` imprime os endereços de acesso. Abra o `http://<ip-do-computador>:8080`
no navegador do celular.

Nesse ponto o app já funciona por completo, mas **em modo simulador**: sem o
modelo instalado ele gera um som sintético no lugar da fala, o que serve para
conhecer a interface. Um aviso laranja aparece no topo enquanto for esse o caso.

## Backend real (fala de verdade)

O OmniVoice roda no computador que serve o site, não no celular.

```bash
# 1. PyTorch — escolha conforme o seu hardware
pip install torch==2.8.0 torchaudio==2.8.0 --extra-index-url https://download.pytorch.org/whl/cu128   # NVIDIA
pip install torch==2.8.0 torchaudio==2.8.0                                                            # Apple Silicon / CPU

# 2. OmniVoice
pip install omnivoice

# 3. Suba o servidor com o motor real
OMNI_BACKEND=omnivoice ./run.sh
```

Na primeira execução o checkpoint (alguns GB) é baixado do HuggingFace. Se o
download estiver lento, use o espelho: `export HF_ENDPOINT="https://hf-mirror.com"`.

Sem o `OMNI_BACKEND`, o app detecta sozinho: usa o OmniVoice se estiver
instalado e cai para o simulador se não estiver.

### Microfone no celular

Navegadores só liberam o microfone em `https://` ou em `http://localhost`. Num
IP da rede local (`http://192.168.x.x`) o botão de gravar não funciona — nesse
caso **envie um arquivo de áudio** pelo seletor, que funciona sempre. Para
liberar a gravação, sirva o app por HTTPS (por exemplo com um túnel reverso ou
um proxy com certificado).

## Configuração

Tudo por variável de ambiente:

| Variável | Padrão | Para que serve |
|---|---|---|
| `OMNI_BACKEND` | `auto` | `auto`, `omnivoice` ou `mock`. |
| `OMNI_MODEL` | `k2-fsa/OmniVoice` | Checkpoint local ou repositório do HuggingFace. |
| `OMNI_DEVICE` | detecção automática | `cuda`, `mps`, `xpu`, `cpu`. |
| `OMNI_PRELOAD` | `0` | `1` carrega o modelo já na inicialização. |
| `OMNI_LOAD_ASR` | `1` | `0` desliga o Whisper (aí a transcrição do áudio de referência passa a ser obrigatória). |
| `OMNI_ASR_MODEL` | `openai/whisper-large-v3-turbo` | Modelo de transcrição. |
| `OMNI_HOST` / `OMNI_PORT` | `0.0.0.0` / `8080` | Endereço de escuta. |
| `OMNI_DATA_DIR` | `./data` | Onde ficam banco, áudios e vozes. |
| `OMNI_MAX_TEXT_CHARS` | `5000` | Limite de texto por geração. |
| `OMNI_MAX_UPLOAD_MB` | `25` | Limite do áudio de referência. |
| `OMNI_HISTORY_LIMIT` | `200` | Gerações mantidas; as mais antigas são apagadas. |

## Estrutura

```
app/
  main.py        rotas FastAPI e renderização das páginas
  jobs.py        fila de síntese (um worker, modelo serializado)
  db.py          SQLite: vozes e histórico
  config.py      configuração por variável de ambiente
  audio.py       leitura/escrita de WAV (só biblioteca padrão)
  languages.py   os 600+ idiomas, lidos do TSV do OmniVoice
  presets.py     atributos de "voice design"
  engines/
    base.py              contrato comum dos motores
    omnivoice_engine.py  motor real
    mock_engine.py       simulador, para rodar sem GPU
  templates/     Jinja2 + HTMX
  static/        CSS, JS, htmx (vendorizado), ícone, manifest
tests/           testes de fumaça sobre o simulador
```

## API

Além da interface, há endpoints JSON para automação:

- `GET /api/status` — motor, fila e contadores.
- `GET /api/geracoes/{id}` — estado de uma geração.
- `GET /geracoes/{id}/audio` — o WAV (`?download=true` força o download).

## Testes

```bash
pip install pytest httpx
python -m pytest tests/ -q
```

Rodam sobre o motor simulado, então não exigem GPU nem o checkpoint.

## Licença

O OmniVoice é distribuído sob Apache 2.0. Clonagem de voz exige consentimento
de quem está sendo clonado — use com responsabilidade.
