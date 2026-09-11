"""Testes do motor de marcação e da montagem do áudio."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app import markup
from app.audio import concat_wavs, write_wav
from app.engines.base import GenerationParams
from app.engines.mock_engine import MockEngine
from app.markup import parse
from app.pipeline import merge_instruct, render, segment_params

SR = 24000


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_plain_text_stays_a_single_untouched_segment():
    result = parse("Olá, mundo. Tudo bem por aí?")
    assert len(result.segments) == 1
    assert result.segments[0].text == "Olá, mundo. Tudo bem por aí?"
    assert result.segments[0].style is None
    assert result.warnings == []


def test_unicode_and_ptbr_survive_parsing():
    text = "Ação, coração e não — reticências… “aspas” 中文 🎙️"
    result = parse(text)
    assert result.segments[0].text == text


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("[pause]", markup.DEFAULT_PAUSE),
        ("[pause=0.5]", 0.5),
        ("[pause=1.2]", 1.2),
        ("[pause=2]", 2.0),
        ("[pause=1,5]", 1.5),  # vírgula decimal, como se escreve em pt-BR
        ("[long-pause]", markup.DEFAULT_LONG_PAUSE),
        ("[long pause]", markup.DEFAULT_LONG_PAUSE),
    ],
)
def test_pause_tags_become_exact_durations(tag, expected):
    result = parse(f"Antes.{tag}Depois.")
    assert result.segments[0].pause_after == pytest.approx(expected)
    assert [s.text for s in result.segments] == ["Antes.", "Depois."]


def test_consecutive_pauses_add_up():
    result = parse("Um.[pause=0.5][pause=0.5][long-pause]Dois.")
    expected = 0.5 + 0.5 + markup.DEFAULT_LONG_PAUSE
    assert result.segments[0].pause_after == pytest.approx(expected)


def test_leading_and_trailing_pauses():
    result = parse("[pause=1.0]Meio.[pause=0.8]")
    assert result.segments[0].pause_before == pytest.approx(1.0)
    assert result.segments[0].pause_after == pytest.approx(0.8)
    assert result.segments[0].text == "Meio."


def test_custom_pause_defaults_are_honoured():
    result = parse("A.[pause]B.[long-pause]C.", pause=0.25, long_pause=4.0)
    assert result.segments[0].pause_after == pytest.approx(0.25)
    assert result.segments[1].pause_after == pytest.approx(4.0)


def test_absurd_pause_is_capped_with_a_warning():
    result = parse(f"A.[pause={markup.MAX_PAUSE_SECONDS + 100}]B.")
    assert result.segments[0].pause_after == markup.MAX_PAUSE_SECONDS
    assert any("máximo" in w.message for w in result.warnings)


def test_malformed_pause_value_warns_and_uses_the_default():
    result = parse("A.[pause=abc]B.")
    assert result.segments[0].pause_after == pytest.approx(markup.DEFAULT_PAUSE)
    assert any(w.level == "warning" for w in result.warnings)


def test_native_tags_reach_the_model_inline():
    result = parse("[sigh] — Por favor... [laughter] Sério?")
    segment = result.segments[0]
    assert "[sigh]" in segment.text
    assert "[laughter]" in segment.text
    assert set(segment.native_tags) == {"sigh", "laughter"}
    assert result.warnings == []


def test_pronunciation_override_passes_through():
    # Sintaxe do próprio OmniVoice: não pode ser tratada como tag desconhecida.
    result = parse("Diga [B EY1 S] agora.")
    assert "[B EY1 S]" in result.segments[0].text
    assert result.warnings == []


def test_unknown_tag_warns_and_is_never_spoken():
    result = parse("Texto [qualquer-coisa] aqui.")
    assert "qualquer-coisa" not in result.segments[0].text
    assert result.has_errors
    # O texto do usuário continua intacto no original e no recorte.
    assert "[qualquer-coisa]" in result.original
    assert "[qualquer-coisa]" in result.segments[0].source_text


def test_unknown_tag_suggests_a_close_match():
    result = parse("[wisper] Baixinho.")
    assert "whisper" in result.warnings[0].message


def test_empty_tag_warns_without_dropping_text():
    result = parse("Antes [] depois.")
    assert result.segments[0].text == "Antes depois."
    assert result.warnings[0].level == "warning"


def test_style_applies_only_to_its_own_segment():
    result = parse("[soft] Sussurrado.[pause=1]Normal de novo.")
    assert result.segments[0].style == "soft"
    assert result.segments[1].style is None


def test_style_switches_close_the_previous_segment():
    result = parse("[soft] Um. [tense] Dois. [urgent] Três.")
    assert [s.style for s in result.segments] == ["soft", "tense", "urgent"]
    assert [s.text for s in result.segments] == ["Um.", "Dois.", "Três."]


def test_whisper_is_the_only_style_backed_by_the_model():
    real = {key for key, style in markup.STYLES.items() if style.real}
    assert real == {"whisper"}
    assert markup.STYLES["whisper"].instruct == "whisper"


def test_style_tags_never_reach_the_tts_text():
    result = parse("[emphasis] Tarde demais.")
    assert "[emphasis]" not in result.segments[0].text
    assert result.segments[0].text == "Tarde demais."


def test_original_text_is_never_replaced():
    source = "[soft] Um.[pause=1.2][tense] Dois."
    result = parse(source)
    assert result.original == source
    assert "".join(s.source_text for s in result.segments) == source


def test_has_markup_and_strip_markup():
    assert markup.has_markup("Oi [pause] tchau")
    assert markup.has_markup("[sigh] oi")
    assert not markup.has_markup("Oi, tudo bem?")
    assert markup.strip_markup("[soft] Oi [pause=1] tchau.") == "Oi tchau."


def test_text_with_only_tags_has_no_speech():
    assert parse("[pause=2][long-pause]").speech_segments == []


# ---------------------------------------------------------------------------
# Parâmetros por segmento
# ---------------------------------------------------------------------------


def test_style_scales_speed_without_discarding_the_user_choice():
    base = GenerationParams(speed=1.2)
    segment = parse("[urgent] Corre!").segments[0]
    applied = segment_params(base, segment)
    assert applied.speed > base.speed
    assert applied.speed <= 1.5  # respeita o limite do modelo


def test_segment_without_style_reuses_the_base_params():
    base = GenerationParams(speed=0.8, num_step=12)
    segment = parse("Sem estilo.").segments[0]
    assert segment_params(base, segment) is base


def test_fixed_duration_is_dropped_on_styled_segments():
    base = GenerationParams(duration=10.0)
    segment = parse("[soft] Oi.").segments[0]
    assert segment_params(base, segment).duration is None


def test_merge_instruct_deduplicates():
    assert merge_instruct("female, whisper", "whisper") == "female, whisper"
    assert merge_instruct(None, "whisper") == "whisper"
    assert merge_instruct("male", None) == "male"
    assert merge_instruct(None, None) is None


# ---------------------------------------------------------------------------
# Montagem do áudio
# ---------------------------------------------------------------------------


def _samples(path: Path) -> tuple[list[int], int]:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
    return [
        int.from_bytes(raw[i : i + 2], "little", signed=True)
        for i in range(0, len(raw), 2)
    ], rate


def _longest_silence(path: Path) -> float:
    """Maior sequência contínua de silêncio do WAV, em segundos."""
    samples, rate = _samples(path)
    longest = current = 0
    for sample in samples:
        current = current + 1 if abs(sample) < 32 else 0
        longest = max(longest, current)
    return longest / rate


@pytest.fixture(scope="module")
def engine():
    instance = MockEngine()
    instance.load()
    return instance


def test_pause_becomes_real_silence_in_the_final_wav(engine, tmp_path):
    """O caso obrigatório: 1,5 s reais de silêncio entre as duas falas.

    Mede a região exata que o pipeline inseriu — não uma janela aproximada —
    e confirma que todas as amostras ali são zero absoluto.
    """
    parsed = parse("Olá.[pause=1.5]Mundo.")
    work = tmp_path / "segs"
    out = tmp_path / "final.wav"
    render(engine, parsed, out_path=out, work_dir=work)

    with wave.open(str(work / "seg_0000.wav"), "rb") as first:
        speech_end = first.getnframes()

    samples, rate = _samples(out)
    inserted = samples[speech_end : speech_end + round(1.5 * rate)]

    assert len(inserted) == round(1.5 * rate)
    assert set(inserted) == {0}, "o silêncio inserido não é digital"
    # E a fala recomeça logo depois, sem silêncio sobrando.
    assert _longest_silence(out) < 1.5 + 0.4


def test_total_duration_covers_speech_plus_pauses(engine, tmp_path):
    parsed = parse("Um.[pause=0.5]Dois.[pause=1.0]Três.")
    out = tmp_path / "final.wav"
    total = render(engine, parsed, out_path=out, work_dir=tmp_path / "segs")

    speech = 0.0
    for index in (0, 1, 2):
        with wave.open(str(tmp_path / "segs" / f"seg_{index:04d}.wav")) as handle:
            speech += handle.getnframes() / SR
    assert total == pytest.approx(speech + 1.5, abs=0.02)


def test_long_text_renders_every_segment(engine, tmp_path):
    text = "[pause=0.3]".join(f"Parágrafo número {n}." for n in range(12))
    parsed = parse(text)
    out = tmp_path / "longo.wav"
    render(engine, parsed, out_path=out, work_dir=tmp_path / "segs")

    assert len(list((tmp_path / "segs").glob("seg_*.wav"))) == 12
    assert out.stat().st_size > 44


def test_finished_segments_are_reused_on_a_second_run(engine, tmp_path):
    """Retomada: um segmento já pronto não é sintetizado de novo."""
    parsed = parse("Um.[pause=0.5]Dois.")
    work = tmp_path / "segs"
    render(engine, parsed, out_path=tmp_path / "a.wav", work_dir=work)

    # Marca o primeiro segmento com um áudio reconhecível.
    marker = [0.5] * SR
    write_wav(work / "seg_0000.wav", marker, SR)

    render(engine, parsed, out_path=tmp_path / "b.wav", work_dir=work)
    with wave.open(str(work / "seg_0000.wav"), "rb") as handle:
        assert handle.getnframes() == SR  # o checkpoint foi reaproveitado


def test_render_refuses_text_without_speech(engine, tmp_path):
    with pytest.raises(ValueError):
        render(
            engine,
            parse("[pause=1]"),
            out_path=tmp_path / "x.wav",
            work_dir=tmp_path / "segs",
        )


def test_concat_rejects_mismatched_sample_rates(tmp_path):
    path = tmp_path / "outra-taxa.wav"
    write_wav(path, [0.0] * 100, 16000)
    with pytest.raises(ValueError, match="mono"):
        concat_wavs([("audio", str(path))], tmp_path / "o.wav", SR)
