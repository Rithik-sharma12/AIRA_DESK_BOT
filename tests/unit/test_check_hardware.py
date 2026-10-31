import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "check_hardware", Path(__file__).resolve().parents[2] / "raspberry_pi" / "check_hardware.py")
ch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ch)

APLAY = """**** List of PLAYBACK Hardware Devices ****
card 0: vc4hdmi [vc4-hdmi], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
card 1: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: HifiBerry DAC HiFi pcm5102a-hifi-0 []"""


def fake(out, code=0):
    return lambda cmd, timeout=15: (code, out)


def test_find_card_skips_hdmi_and_prefers_i2s(monkeypatch):
    monkeypatch.setattr(ch, "run", fake(APLAY))
    num, line = ch.find_card("aplay")
    assert num == "1" and "hifiberry" in line


def test_find_card_only_hdmi_or_missing(monkeypatch):
    monkeypatch.setattr(ch, "run", fake("card 0: vc4hdmi [vc4-hdmi], device 0: MAI PCM [MAI]"))
    assert ch.find_card("aplay") is None
    monkeypatch.setattr(ch, "run", fake("aplay not installed", 127))
    assert ch.find_card("aplay") is None
    monkeypatch.setattr(ch, "run", fake("card 2: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]"))
    assert ch.find_card("arecord")[0] == "2"


def test_audio_check_reports_missing_dac(monkeypatch):
    ch.results.clear()
    monkeypatch.setattr(ch, "run", fake("card 0: vc4hdmi [vc4-hdmi], device 0: x [x]"))
    ch.check_audio()
    assert ch.results[0][0] == "FAIL" and "dtparam=i2s=on" in ch.results[0][2]


def test_wifi_parsing(monkeypatch):
    ch.results.clear()
    out = "Connected to aa:bb\n\tSSID: Redmi Note 12 5G\n\tfreq: 2437.0\n\tsignal: -78 dBm"
    monkeypatch.setattr(ch, "run", fake(out))
    ch.check_wifi()
    status, name, detail = ch.results[-1]
    assert status == "WARN" and "2437 MHz" in detail


def test_find_card_ignores_legacy_bcm2835_outputs(monkeypatch):
    # With vc4-kms-v3d commented out (as on AIRA), built-in audio appears under bcm2835 names.
    out = ("card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]\n"
           "card 1: b1 [bcm2835 HDMI 1], device 0: bcm2835 HDMI 1 [bcm2835 HDMI 1]")
    monkeypatch.setattr(ch, "run", fake(out))
    assert ch.find_card("aplay") is None
    monkeypatch.setattr(ch, "run", fake(out + "\ncard 2: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: x []"))
    assert ch.find_card("aplay")[0] == "2"
