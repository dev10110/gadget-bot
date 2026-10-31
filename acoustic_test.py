"""Plays the ring and each caller through the speaker, records them with the mic, and compares levels.

Use it to check that the callers and the ring come out about equally loud, after changing a voice,
`gain_db`, `TONE_VOLUME`, or the speaker. The mic should be near the speaker, as in the costume.

The callers' opening lines are fetched from the API once and cached in `acoustic_test_clips/`
before any effects, so later runs are free and pick up changes to the effects. The recording is
saved there too, for listening back.

Usage: python acoustic_test.py            # uses the cached lines
       python acoustic_test.py --refetch  # fetches new lines, e.g. after changing a voice or prompt
"""

import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from openai import OpenAI

from gadget_realtime import CALLERS, RING, SAMPLE_RATE, VoiceEffect, load_secrets
from voice_sampler import speak

CLIPS_DIR = Path(__file__).with_name("acoustic_test_clips")
BANDS = [125, 250, 500, 1000, 2000, 4000, 8000]  # octave band centres, Hz
FRAME_SECONDS = 0.05


def read_wav(path: Path) -> bytes:
    with wave.open(str(path)) as wav:
        return wav.readframes(wav.getnframes())


def write_wav(path: Path, pcm: bytes):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


def to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16) / 32768


def caller_line(client: OpenAI | None, caller) -> np.ndarray:
    """The caller's opening line with their effects applied, fetching it if it isn't cached."""
    path = CLIPS_DIR / (caller.name.replace(" ", "_").replace(".", "") + ".wav")
    if client:
        prompt = "The call just connected. Say your opening line."
        write_wav(path, speak(client, caller.voice, caller.speed, caller.persona, prompt, caller.model))
    return to_float(VoiceEffect(caller)(read_wav(path)))


def a_weighting(freqs: np.ndarray) -> np.ndarray:
    """How loud each frequency sounds to people, relative to 1 kHz (IEC 61672 A-weighting)."""
    f2 = np.maximum(freqs, 1e-6) ** 2
    gain = (12194**2 * f2**2) / (
        (f2 + 20.6**2) * np.sqrt((f2 + 107.7**2) * (f2 + 737.9**2)) * (f2 + 12194**2)
    )
    return gain / 0.7943


def filtered(x: np.ndarray, gain_at) -> np.ndarray:
    spectrum = np.fft.rfft(x) * gain_at(np.fft.rfftfreq(len(x), 1 / SAMPLE_RATE))
    return np.fft.irfft(spectrum, len(x))


def frame_power(x: np.ndarray) -> np.ndarray:
    size = int(SAMPLE_RATE * FRAME_SECONDS)
    return (x[: len(x) // size * size].reshape(-1, size) ** 2).mean(axis=1)


def levels(x: np.ndarray, sounding: np.ndarray) -> list[float]:
    """Overall, A-weighted, and per-octave-band levels in dB, over the frames where it's sounding."""
    shapes = [lambda f: 1.0, a_weighting]
    shapes += [lambda f, c=c: ((f >= c / 2**0.5) & (f < c * 2**0.5)).astype(float) for c in BANDS]
    return [10 * np.log10(max(frame_power(filtered(x, s))[sounding].mean(), 1e-12)) for s in shapes]


def print_table(title: str, rows: list[tuple[str, list[float]]]):
    bands = "".join(f"{b if b < 1000 else f'{b // 1000}k':>6}" for b in BANDS)
    print(f"{title}\n{'':14s}{'RMS':>7s}{'A-wtd':>7s} |{bands}")
    for name, values in rows:
        print(f"{name:14s}{values[0]:7.1f}{values[1]:7.1f} |" + "".join(f"{v:6.1f}" for v in values[2:]))
    print()


def main():
    CLIPS_DIR.mkdir(exist_ok=True)
    refetch = "--refetch" in sys.argv or not any(CLIPS_DIR.glob("*.wav"))
    client = None
    if refetch:
        load_secrets()
        client = OpenAI()
        print("Fetching each caller's opening line...", flush=True)
    sounds = [("Ring", to_float(RING))] + [(c.name, caller_line(client, c)) for c in CALLERS]

    # 2 s of quiet first to measure the room's noise, then each sound with a 1 s gap.
    gap = np.zeros(SAMPLE_RATE)
    pieces, spans = [np.zeros(2 * SAMPLE_RATE)], []
    position = len(pieces[0])
    for name, sound in sounds:
        spans.append((name, position, position + len(sound)))
        pieces += [sound, gap]
        position += len(sound) + len(gap)
    played = np.concatenate(pieces).astype(np.float32)

    print(f"Playing {len(played) / SAMPLE_RATE:.0f} s through the speaker and recording...", flush=True)
    chunks = []
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=lambda indata, *_: chunks.append(indata[:, 0].copy()),
    ):
        time.sleep(0.5)
        sd.play(played, SAMPLE_RATE, blocking=True)
        time.sleep(1.0)  # catch the tail, since the recording lags the playback
    recorded = np.concatenate(chunks).astype(float)
    write_wav(CLIPS_DIR / "recording.wav", (np.clip(recorded, -1, 1) * 32767).astype(np.int16).tobytes())

    # Line the recording up with what was played, to find the playback delay.
    n = 1 << int(np.ceil(np.log2(len(recorded) + len(played))))
    correlation = np.fft.irfft(np.fft.rfft(recorded, n) * np.conj(np.fft.rfft(played, n)), n)
    lag = int(np.argmax(correlation[: len(recorded)]))
    noise = recorded[lag + SAMPLE_RATE // 5 : lag + 9 * SAMPLE_RATE // 5]
    peak = 20 * np.log10(np.abs(recorded).max())
    print(f"Delay {lag / SAMPLE_RATE * 1000:.0f} ms, room noise {10 * np.log10((noise**2).mean()):.1f} dB, "
          f"mic peak {peak:.1f} dBFS{' (clipping: turn the volume down)' if peak > -0.5 else ''}\n")

    sent, heard = [], []
    for name, start, end in spans:
        source = played[start:end].astype(float)
        power = frame_power(source)
        sounding = power > power.max() / 1000  # within 30 dB of its loudest moment
        sent.append((name, levels(source, sounding)))
        heard.append((name, levels(recorded[start + lag : end + lag], sounding)))
    print_table("Sent by the program (dB)", sent)
    print_table("Heard by the mic (dB): compare the A-wtd column", heard)


if __name__ == "__main__":
    main()
