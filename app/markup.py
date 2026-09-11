"""Motor de marcação de audiobook.

Camada independente do backend de síntese: recebe o texto com marcações e
devolve segmentos prontos para qualquer motor de TTS.

    TEXTO ORIGINAL → PARSER → SEGMENTOS → TTS → PROCESSAMENTO → WAV

As três famílias de tags têm garantias bem diferentes, e a distinção é
deliberada — o usuário precisa saber no que confiar:

* **nativas** — o OmniVoice as reconhece dentro do texto e produz o som
  correspondente. Seguem inline até o modelo.
* **controle** — pausas. Viram silêncio digital inserido *depois* da
  síntese, então a duração é exata e não depende do modelo.
* **interpretação** — emoções. O OmniVoice **não** tem controle real de
  emoção: seu ``instruct`` é uma lista fechada (gênero, idade, tom, sotaque,
  dialeto e ``whisper``) que rejeita qualquer outro termo. Só ``[whisper]``
  mapeia para um recurso de verdade; as demais são aproximações por
  velocidade e aderência (CFG) e estão marcadas como experimentais.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Tags nativas do OmniVoice
# ---------------------------------------------------------------------------

#: Símbolos não-verbais aceitos pelo modelo (``_NONVERBAL_PATTERN`` em
#: ``omnivoice/models/omnivoice.py``). Vão inline no texto enviado ao TTS.
NATIVE_TAGS: tuple[str, ...] = (
    "laughter",
    "sigh",
    "confirmation-en",
    "question-en",
    "question-ah",
    "question-oh",
    "question-ei",
    "question-yi",
    "surprise-ah",
    "surprise-oh",
    "surprise-wa",
    "surprise-yo",
    "dissatisfaction-hnn",
)

_NATIVE_SET = frozenset(NATIVE_TAGS)

#: Correção de pronúncia do OmniVoice: ``[B EY1 S]`` (fonemas CMU em
#: maiúsculas). Passa direto para o modelo, como as tags nativas.
_PRONUNCIATION_RE = re.compile(r"^[A-Z][A-Z0-9]*(?: [A-Z0-9]+)*$")


# ---------------------------------------------------------------------------
# Estilos de interpretação
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Style:
    """Uma tag de interpretação e como ela vira parâmetros reais.

    ``speed`` e ``guidance_scale`` multiplicam/substituem os valores da
    geração; ``instruct`` só é usado quando o termo existe de verdade na
    whitelist do OmniVoice.
    """

    key: str
    label: str
    #: ``True`` quando o backend controla isso de verdade.
    real: bool
    speed: float = 1.0
    guidance_scale: float | None = None
    instruct: str | None = None
    pause_before: float = 0.0
    pause_after: float = 0.0
    description: str = ""


STYLES: dict[str, Style] = {
    "whisper": Style(
        key="whisper",
        label="Sussurro",
        real=True,
        instruct="whisper",  # único estilo com suporte real no instruct
        speed=0.96,
        description="Sussurro de verdade — o modelo tem esse atributo.",
    ),
    "soft": Style(
        key="soft",
        label="Suave",
        real=False,
        speed=0.94,
        guidance_scale=1.6,
        description="Fala mais lenta e menos marcada.",
    ),
    "tense": Style(
        key="tense",
        label="Tenso",
        real=False,
        speed=1.08,
        guidance_scale=2.7,
        description="Fala mais rápida e mais marcada.",
    ),
    "sad": Style(
        key="sad",
        label="Triste",
        real=False,
        speed=0.88,
        guidance_scale=1.7,
        pause_after=0.25,
        description="Ritmo arrastado, com uma respiração no fim.",
    ),
    "hopeful": Style(
        key="hopeful",
        label="Esperançoso",
        real=False,
        speed=1.02,
        guidance_scale=2.1,
        description="Ritmo levemente acima do normal.",
    ),
    "urgent": Style(
        key="urgent",
        label="Urgente",
        real=False,
        speed=1.18,
        guidance_scale=2.9,
        description="Fala acelerada e bem marcada.",
    ),
    "emphasis": Style(
        key="emphasis",
        label="Ênfase",
        real=False,
        speed=0.9,
        guidance_scale=2.6,
        pause_before=0.18,
        pause_after=0.18,
        description="Desacelera e isola o trecho entre pequenas pausas.",
    ),
}


# ---------------------------------------------------------------------------
# Pausas
# ---------------------------------------------------------------------------

DEFAULT_PAUSE = 0.6
DEFAULT_LONG_PAUSE = 1.5

#: Teto de segurança: evita que um `[pause=9999]` gere um WAV gigante.
MAX_PAUSE_SECONDS = 30.0

_PAUSE_RE = re.compile(r"^pause(?:\s*=\s*(.+))?$", re.IGNORECASE)
_LONG_PAUSE_RE = re.compile(r"^long[-_ ]?pause$", re.IGNORECASE)

#: Linha em branco: separa parágrafos e encerra o estilo em vigor.
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")

#: Qualquer `[...]` que não contenha `[` ou `]` aninhados.
_TAG_RE = re.compile(r"\[([^\[\]]*)\]")


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------

WarningLevel = Literal["error", "warning"]


@dataclass
class MarkupWarning:
    """Problema encontrado no markup, com posição no texto original."""

    level: WarningLevel
    tag: str
    message: str
    position: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "tag": self.tag,
            "message": self.message,
            "position": self.position,
        }


@dataclass
class Segment:
    """Um trecho contíguo com um único estilo.

    ``text`` é o que vai para o TTS (tags nativas preservadas, tags do Studio
    já removidas). ``source_text`` guarda o recorte original com as marcações
    — o texto do usuário nunca é substituído pelo processado.
    """

    text: str
    source_text: str = ""
    style: str | None = None
    native_tags: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    pause_before: float = 0.0
    pause_after: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_silence(self) -> bool:
        """Segmento sem fala: só contribui com silêncio."""
        return not self.text.strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source_text": self.source_text,
            "style": self.style,
            "native_tags": list(self.native_tags),
            "parameters": dict(self.parameters),
            "pause_before": self.pause_before,
            "pause_after": self.pause_after,
            "metadata": dict(self.metadata),
        }


@dataclass
class ParseResult:
    """Resultado da análise: o original intacto, os segmentos e os avisos."""

    original: str
    segments: list[Segment]
    warnings: list[MarkupWarning] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(w.level == "error" for w in self.warnings)

    @property
    def speech_segments(self) -> list[Segment]:
        return [s for s in self.segments if not s.is_silence]

    @property
    def total_pause(self) -> float:
        return sum(s.pause_before + s.pause_after for s in self.segments)

    def as_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "segments": [s.as_dict() for s in self.segments],
            "warnings": [w.as_dict() for w in self.warnings],
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parse_pause_value(raw: str | None, default: float) -> tuple[float, str | None]:
    """Interpreta o valor de ``[pause=X]``. Devolve (segundos, erro)."""
    if raw is None or not raw.strip():
        return default, None
    text = raw.strip().replace(",", ".").rstrip("s").strip()
    try:
        value = float(text)
    except ValueError:
        return default, f"'{raw.strip()}' não é um número de segundos."
    if value < 0:
        return default, "A pausa não pode ser negativa."
    if value > MAX_PAUSE_SECONDS:
        return MAX_PAUSE_SECONDS, (
            f"Pausa de {value:g}s reduzida ao máximo de {MAX_PAUSE_SECONDS:g}s."
        )
    return value, None


class _Builder:
    """Acumula texto até fechar um segmento (numa pausa ou troca de estilo)."""

    def __init__(self) -> None:
        self.segments: list[Segment] = []
        self._chunks: list[str] = []
        self._source: list[str] = []
        self._native: list[str] = []
        self._style: str | None = None
        self._pending_pause = 0.0
        self._start = 0

    def add_text(self, text: str, source: str) -> None:
        """Acrescenta texto, fechando o segmento em cada quebra de parágrafo.

        Uma linha em branco separa parágrafos, e é onde o estilo corrente
        deixa de valer — assim uma emoção não contamina o capítulo inteiro.
        """
        if self._style is None or not _PARAGRAPH_RE.search(text):
            self._chunks.append(text)
            self._source.append(source)
            return

        head, tail = _PARAGRAPH_RE.split(text, maxsplit=1)
        self._chunks.append(head)
        self._source.append(head)
        self.flush(0)
        self._style = None
        # O resto do parágrafo segue sem estilo (e pode conter outra quebra).
        self.add_text(tail, source[len(head) :])

    def add_native(self, tag: str, source: str) -> None:
        self._chunks.append(f"[{tag}]")
        self._source.append(source)
        self._native.append(tag)

    def add_passthrough(self, body: str, source: str) -> None:
        """Correção de pronúncia: segue inteira para o modelo."""
        self._chunks.append(f"[{body}]")
        self._source.append(source)

    def drop(self, source: str) -> None:
        """Tag removida do TTS mas mantida no original (não é pronunciada)."""
        self._source.append(source)

    def flush(self, position: int) -> None:
        """Fecha o segmento atual, se houver conteúdo."""
        text = _tidy("".join(self._chunks))
        source = "".join(self._source)
        if not text and not source.strip():
            self._chunks.clear()
            self._source.clear()
            self._native.clear()
            return

        style = STYLES.get(self._style or "")
        parameters: dict[str, Any] = {}
        pause_before = self._pending_pause
        pause_after = 0.0
        if style is not None:
            if style.speed != 1.0:
                parameters["speed_factor"] = style.speed
            if style.guidance_scale is not None:
                parameters["guidance_scale"] = style.guidance_scale
            if style.instruct:
                parameters["instruct"] = style.instruct
            pause_before += style.pause_before
            pause_after += style.pause_after

        self.segments.append(
            Segment(
                text=text,
                source_text=source,
                style=self._style,
                native_tags=tuple(self._native),
                parameters=parameters,
                pause_before=pause_before,
                pause_after=pause_after,
                metadata={"index": len(self.segments), "offset": self._start},
            )
        )
        self._chunks.clear()
        self._source.clear()
        self._native.clear()
        self._pending_pause = 0.0
        self._start = position

    def add_pause(self, seconds: float, position: int, source: str) -> None:
        """Fecha o segmento corrente e pendura o silêncio no próximo.

        A pausa também encerra o estilo em vigor: uma tag de emoção vale até
        a próxima pausa, parágrafo ou tag — nunca até o fim do capítulo.
        """
        self.flush(position)
        self._style = None
        if self.segments:
            self.segments[-1].pause_after += seconds
            self.segments[-1].source_text += source
        else:
            # Pausa antes de qualquer fala: vira silêncio de abertura.
            self._pending_pause += seconds
            self._source.append(source)

    def set_style(self, style: str | None, position: int) -> None:
        if style == self._style:
            return
        self.flush(position)
        self._style = style


def _tidy(text: str) -> str:
    """Normaliza os espaços deixados pela remoção das tags."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse(
    text: str,
    *,
    pause: float = DEFAULT_PAUSE,
    long_pause: float = DEFAULT_LONG_PAUSE,
) -> ParseResult:
    """Analisa o texto com marcações e devolve os segmentos.

    Texto sem nenhuma tag produz exatamente um segmento com o texto intacto,
    para que o comportamento anterior do Studio não mude.

    Args:
        text: Texto original, com ou sem marcações.
        pause: Duração de ``[pause]`` sem valor.
        long_pause: Duração de ``[long-pause]``.
    """
    builder = _Builder()
    warnings: list[MarkupWarning] = []
    cursor = 0

    for match in _TAG_RE.finditer(text):
        builder.add_text(text[cursor : match.start()], text[cursor : match.start()])
        cursor = match.end()

        body = match.group(1).strip()
        raw = match.group(0)
        position = match.start()
        lowered = body.lower()

        if not body:
            warnings.append(
                MarkupWarning(
                    "warning", raw, "Tag vazia: será ignorada.", position
                )
            )
            builder.drop(raw)
            continue

        if lowered in _NATIVE_SET:
            builder.add_native(lowered, raw)
            continue

        if _LONG_PAUSE_RE.match(lowered):
            builder.add_pause(long_pause, position, raw)
            continue

        pause_match = _PAUSE_RE.match(lowered)
        if pause_match:
            seconds, problem = _parse_pause_value(pause_match.group(1), pause)
            if problem:
                warnings.append(MarkupWarning("warning", raw, problem, position))
            builder.add_pause(seconds, position, raw)
            continue

        if lowered in STYLES:
            builder.set_style(lowered, position)
            builder.drop(raw)
            continue

        if _PRONUNCIATION_RE.match(body):
            # Correção de pronúncia do próprio OmniVoice: passa direto.
            builder.add_passthrough(body, raw)
            continue

        # Desconhecida: avisa, preserva no original e não deixa pronunciar.
        warnings.append(
            MarkupWarning(
                "error",
                raw,
                f"Tag desconhecida “{raw}”. Ela não será falada."
                f" {_suggest(lowered)}".rstrip(),
                position,
            )
        )
        builder.drop(raw)

    builder.add_text(text[cursor:], text[cursor:])
    builder.flush(len(text))

    if not builder.segments:
        builder.segments.append(
            Segment(text="", source_text=text, metadata={"index": 0, "offset": 0})
        )

    return ParseResult(original=text, segments=builder.segments, warnings=warnings)


def _suggest(name: str) -> str:
    """Sugere a tag conhecida mais próxima, quando houver uma."""
    import difflib

    known = list(STYLES) + list(NATIVE_TAGS) + ["pause", "long-pause"]
    close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    return f"Você quis dizer [{close[0]}]?" if close else ""


def has_markup(text: str) -> bool:
    """``True`` se o texto contém alguma tag reconhecida pelo Studio."""
    for match in _TAG_RE.finditer(text):
        body = match.group(1).strip().lower()
        if (
            body in _NATIVE_SET
            or body in STYLES
            or _PAUSE_RE.match(body)
            or _LONG_PAUSE_RE.match(body)
        ):
            return True
    return False


def strip_markup(text: str) -> str:
    """Texto limpo, sem nenhuma tag — útil para prévias e resumos."""
    return _tidy(_TAG_RE.sub("", text))


# ---------------------------------------------------------------------------
# Catálogo para a interface
# ---------------------------------------------------------------------------


def tag_catalog() -> dict[str, list[dict[str, Any]]]:
    """Tags agrupadas por família, para a ajuda e a barra do editor."""
    return {
        "native": [
            {
                "tag": f"[{tag}]",
                "label": tag,
                "real": True,
            }
            for tag in NATIVE_TAGS
        ],
        "control": [
            {"tag": "[pause]", "label": "Pausa padrão", "real": True},
            {"tag": "[pause=1.2]", "label": "Pausa de 1,2 s", "real": True},
            {"tag": "[long-pause]", "label": "Pausa longa", "real": True},
        ],
        "style": [
            {
                "tag": f"[{style.key}]",
                "label": style.label,
                "real": style.real,
                "description": style.description,
            }
            for style in STYLES.values()
        ],
    }
