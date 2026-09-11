# OmniVoice Studio

Aplicativo web para o [OmniVoice](https://github.com/k2-fsa/OmniVoice) — modelo
de síntese de voz *zero-shot* multilíngue (600+ idiomas) da equipe Next-gen
Kaldi da Xiaomi.

A interface foi feita para **celular**. O caminho principal é o Google Colab
gratuito: abrir o notebook, executar tudo e usar o link HTTPS no telefone —
sem computador e sem terminal. Também roda localmente.

## O que dá para fazer

| Recurso | Descrição |
|---|---|
| **Editor de audiobook** | Pausas exatas, emoções e sons não-verbais por tags no texto. |
| **Testar trecho** | Gera só a frase selecionada, para acertar voz e ritmo antes do capítulo inteiro. |
| **Textos salvos** | Capítulos guardados para retomar em outra sessão. |
| **Voz automática** | Digite o texto e ouça; o modelo escolhe a voz. |
| **Clonar voz** | Grave 3–10 s pelo microfone do celular (ou envie um arquivo) e fale com aquela voz. |
| **Criar voz** | Monte uma voz por atributos: gênero, idade, tom, sussurro, sotaque, dialeto. |
| **Biblioteca de vozes** | Vozes salvas ficam prontas para reuso, com prévia, renomear e excluir. |
| **Histórico** | Toda geração fica guardada com player e download em WAV. |
| **Fila** | As gerações entram numa fila de um worker só — o modelo ocupa a GPU inteira. |
| **600+ idiomas** | Seletor completo, com os mais usados no topo. |
| **Instalável** | "Adicionar à tela de início" abre em tela cheia, como um app. |

Sem login: os dados ficam na máquina que roda o servidor (ou no seu Drive).

---

## Caminho 1 — Google Colab (recomendado, só com o celular)

1. Abra **[`OmniVoice_Studio_Colab.ipynb`](OmniVoice_Studio_Colab.ipynb)** no
   [Google Colab](https://colab.research.google.com/github/werikvinicios-dev/ominivoiceapp/blob/claude/compassionate-clarke-eftt77/OmniVoice_Studio_Colab.ipynb)
2. **Ambiente de execução → Alterar o tipo de ambiente → GPU (T4)**
3. **Ambiente de execução → Executar tudo**
4. Autorize o Google Drive quando ele pedir
5. A última célula imprime **dois** endereços — use qualquer um:
   - **link público** (`trycloudflare.com`): funciona em qualquer aparelho
   - **link do Colab** (`googleusercontent.com`): só no navegador que está com
     o notebook aberto, mas não depende de serviço externo e não cai

> Os endereços **mudam a cada execução**. Um link guardado de uma sessão
> anterior sempre responde erro 1033 — pegue o link atual na saída da célula.

A última célula precisa continuar rodando enquanto você usa o Studio. Sem GPU
o notebook avisa e abre em modo simulador.

### Onde ficam os dados

Com o Drive ligado, tudo que importa vai para **`Meu Drive/OmniVoiceStudio`**:

| Caminho | Conteúdo |
|---|---|
| `studio.db` | vozes, textos salvos, histórico e preferências |
| `voices/` | os prompts de clonagem (`VoiceClonePrompt`) |
| `audio/` | os WAV finais |

Prévias e checkpoints de segmentos ficam no runtime do Colab, que é rápido e
descartável — o Drive não é usado para dados regeneráveis.

Se a sessão do Colab cair no meio de um capítulo longo, a geração volta para a
fila na próxima inicialização e reaproveita os segmentos já sintetizados.

---

## Caminho 2 — instalação local

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

Para persistir os dados fora da pasta do projeto, aponte `OMNI_PERSIST_DIR`
para onde quiser (é o que o notebook do Colab faz com o Drive).

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

## Editor de audiobook

Escreva tags no texto para controlar pausas, sons e interpretação:

```
[soft] A segunda luz não deveria existir.
[pause=1.2]
[tense] Alguém a acendera.
[sigh] — Por favor...
[long-pause]
[emphasis] Tarde demais.
```

Uma tag de emoção vale até a próxima tag, pausa ou parágrafo — nunca contamina
o resto do capítulo. Texto sem tags funciona exatamente como antes.

### Pausas — exatas

O silêncio é inserido no WAV **depois** da síntese, então a duração não depende
do modelo.

| Tag | Efeito |
|---|---|
| `[pause]` | duração padrão (ajustável em Ajustes) |
| `[pause=1.2]` | exatamente 1,2 s |
| `[long-pause]` | pausa longa (ajustável em Ajustes) |

### Sons não-verbais — nativos do modelo

Vão inline até o OmniVoice, que produz o som de verdade:

`[laughter]` `[sigh]` `[confirmation-en]` `[question-en]` `[question-ah]`
`[question-oh]` `[question-ei]` `[question-yi]` `[surprise-ah]`
`[surprise-oh]` `[surprise-wa]` `[surprise-yo]` `[dissatisfaction-hnn]`

A correção de pronúncia do OmniVoice (`[B EY1 S]`) também passa direto.

### Interpretação — uma real, o resto aproximado

O OmniVoice **não tem controle de emoção**: seu `instruct` é uma lista fechada
de atributos (gênero, idade, tom, sotaque, dialeto e `whisper`) que rejeita
qualquer outro termo. Só o sussurro é real; as demais tags são aproximações
feitas pelo Studio com ritmo e intensidade.

| Tag | Suporte |
|---|---|
| `[whisper]` | **real** — atributo do modelo |
| `[soft]` `[tense]` `[sad]` `[hopeful]` `[urgent]` `[emphasis]` | aproximado por velocidade e CFG |

Uma tag desconhecida nunca é falada: o editor avisa e a ignora na geração, mas
o seu texto continua intacto.

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
| `OMNI_DATA_DIR` | `./data` | Runtime: uploads, prévias e checkpoints. |
| `OMNI_PERSIST_DIR` | igual ao `OMNI_DATA_DIR` | Pasta persistente (ex.: Drive): banco, vozes e áudios finais. |
| `OMNI_MAX_TEXT_CHARS` | `5000` | Limite de texto por geração. |
| `OMNI_MAX_UPLOAD_MB` | `25` | Limite do áudio de referência. |
| `OMNI_HISTORY_LIMIT` | `200` | Gerações mantidas; as mais antigas são apagadas. |

## Estrutura

```
OmniVoice_Studio_Colab.ipynb   notebook de uso pelo celular
scripts/colab.py               preparação e inicialização no Colab
app/
  main.py        rotas FastAPI e renderização das páginas
  markup.py      parser de tags: pausas, emoções e sons nativos
  pipeline.py    segmentos → TTS → silêncios reais → WAV final
  prefs.py       preferências editáveis (durações de pausa)
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

- `GET /api/status` — motor, fila, contadores e durações de pausa.
- `GET /api/tags` — catálogo de tags, marcando o que é real no backend.
- `GET /api/geracoes/{id}` — estado, progresso por segmento e avisos.
- `GET /geracoes/{id}/audio` — o WAV (`?download=true` força o download).

## Testes

```bash
pip install pytest httpx
python -m pytest tests/ -q
```

Rodam sobre o motor simulado, então não exigem GPU nem o checkpoint. Cobrem o
parser, as pausas (inclusive a verificação de que `[pause=1.5]` rende 1,5 s de
silêncio digital real), a montagem do WAV, a retomada e a persistência.

## Limitações conhecidas

- **Emoções não são nativas.** Só `[whisper]` é um recurso real do modelo; as
  outras tags de interpretação aproximam o efeito com ritmo e intensidade.
- **Microfone exige HTTPS.** No Colab o link é HTTPS e a gravação funciona.
  Localmente, num IP de rede (`http://192.168.x.x`), o navegador bloqueia o
  microfone — use o envio de arquivo.
- **A sessão do Colab é temporária.** Sem o Drive montado, tudo se perde ao
  fim da sessão.
- **O túnel gratuito do trycloudflare cai de vez em quando** (erro 1033 na
  página). A célula detecta a queda em até 30 s, reabre o túnel e imprime o
  novo endereço; o link do Colab continua valendo enquanto isso. A célula de
  diagnóstico (`colab.diagnose(sessao)`) mostra qual peça falhou.

## Licença

O OmniVoice é distribuído sob Apache 2.0. Clonagem de voz exige consentimento
de quem está sendo clonado — use com responsabilidade.
