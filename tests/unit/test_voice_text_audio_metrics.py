import pytest

from laptop_server.voice.audio import AudioClip, concat
from laptop_server.voice.metrics import cer, normalize, wer
from laptop_server.voice.text import SentenceStream, ScriptRun, dominant_language, split_by_script, split_sentences


# ------------------------------------------------------------------ sentences
def test_split_sentences_basic_and_tamil():
    text = "Hey! Good morning. Your CPU is at 3.5 GHz, e.g. fast. வணக்கம்! எப்படி இருக்கீங்க?"
    assert split_sentences(text) == [
        "Hey!", "Good morning.", "Your CPU is at 3.5 GHz, e.g. fast.", "வணக்கம்!", "எப்படி இருக்கீங்க?",
    ]


def test_split_sentences_newlines_and_no_terminator():
    assert split_sentences("line one\nline two") == ["line one", "line two"]
    assert split_sentences("") == []


def test_sentence_stream_emits_only_complete_sentences():
    s = SentenceStream()
    out = []
    for chunk in ["Hel", "lo there", ". How a", "re you", "? I'm AIRA"]:
        out += list(s.feed(chunk))
    assert out == ["Hello there.", "How are you?"]
    assert list(s.flush()) == ["I'm AIRA"]
    assert list(s.flush()) == []


def test_sentence_stream_waits_for_space_after_period():
    s = SentenceStream()
    assert list(s.feed("Version 3.")) == []          # might be "3.5"
    assert list(s.feed("5 is out. Next")) == ["Version 3.5 is out."]


# --------------------------------------------------------------------- script
def test_split_by_script_tanglish():
    runs = split_by_script("சரி, உங்க Python error-ஐ பார்க்கலாம்!")
    assert runs == [ScriptRun("ta", "சரி, உங்க"), ScriptRun("en", "Python"), ScriptRun("ta", "error-ஐ பார்க்கலாம்!")]


def test_split_by_script_neutral_tokens():
    assert split_by_script("7 PM-க்கு reminder 100%") == [
        ScriptRun("ta", "7 PM-க்கு"), ScriptRun("en", "reminder 100%")]
    assert split_by_script("123 !") == [ScriptRun("ta", "123 !")]
    assert split_by_script("") == []


def test_dominant_language():
    assert dominant_language("Hello there") == "en"
    assert dominant_language("Hello வணக்கம்") == "ta"


# ---------------------------------------------------------------------- audio
def test_audio_roundtrip_concat_and_silence():
    a = AudioClip.silence(0.5, 16000)
    assert a.duration_s == pytest.approx(0.5)
    b = AudioClip.from_wav_bytes(a.to_wav_bytes())
    assert b.sample_rate == 16000 and b.pcm16 == a.pcm16
    joined = concat([a, b], gap_s=0.25)
    assert joined.duration_s == pytest.approx(1.25)
    with pytest.raises(ValueError):
        concat([a, AudioClip.silence(0.1, 24000)])


def test_audio_from_float_clips():
    clip = AudioClip.from_float([0.0, 1.0, -1.0, 2.0], 8000)
    assert len(clip.pcm16) == 8
    assert clip.duration_s == pytest.approx(4 / 8000)


# -------------------------------------------------------------------- metrics
def test_normalize_keeps_tamil_signs_drops_punctuation():
    assert normalize("  Hello, WORLD!! ") == "hello world"
    assert normalize("இருக்கீங்க?") == "இருக்கீங்க"   # vowel signs and virama survive


def test_cer_wer():
    assert cer("hello world", "hello world") == 0.0
    assert wer("open visual studio code", "open visual studio") == pytest.approx(0.25)
    assert cer("abcd", "abxd") == pytest.approx(0.25)
    assert cer("", "") == 0.0 and cer("", "x") == 1.0
    # Tamil: one wrong suffix fails the word but costs little in CER
    ref, hyp = "நான் வரேன்", "நான் வரென்"
    assert wer(ref, hyp) == pytest.approx(0.5)
    assert cer(ref, hyp) < 0.2
