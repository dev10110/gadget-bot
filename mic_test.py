"""Live microphone check: shows a level meter while you talk, then plays the recording back.

Opens the mic the same way gadget_realtime.py does (default device, 24 kHz mono). Press Enter to
start each test, so you can check that the mic keeps working across several tests.

Usage: python mic_test.py            # keep one mic stream open, like the phone does
       python mic_test.py --reopen   # reopen the mic for every test
"""

import sys
import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 24_000
SECONDS = 5
METER_WIDTH = 40


class Recorder:
    def __init__(self):
        self.chunks: list[np.ndarray] | None = None  # collecting while not None
        self.lock = threading.Lock()

    def on_audio(self, indata, frames, time, status):
        with self.lock:
            if self.chunks is None:
                return
            self.chunks.append(indata[:, 0].copy())
        level = np.abs(indata).max()
        bar = "█" * int(min(level * 4, 1) * METER_WIDTH)  # 0.25 peak fills the bar
        print(f"\r  level |{bar:<{METER_WIDTH}}| {level:.3f}", end="", flush=True)

    def open_stream(self) -> sd.InputStream:
        return sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=SAMPLE_RATE // 10,
            callback=self.on_audio,
        )


def run_test(number: int, recorder: Recorder, reopen: bool):
    print(f"Test {number}: talk now for {SECONDS} seconds...")
    recorder.chunks = []
    if reopen:
        with recorder.open_stream():
            sd.sleep(SECONDS * 1000)
    else:
        sd.sleep(SECONDS * 1000)
    with recorder.lock:
        chunks, recorder.chunks = recorder.chunks, None

    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    peak = np.abs(audio).max()
    verdict = (
        "NO SIGNAL (exact silence)" if peak == 0
        else "very quiet" if peak < 0.02
        else "OK"
    )
    print(f"\n  peak {peak:.3f}  →  {verdict}")
    if peak > 0:
        print("  playing it back...")
        sd.play(audio / peak * 0.5, SAMPLE_RATE, blocking=True)


def main():
    reopen = "--reopen" in sys.argv[1:]
    print(f"Input device: {sd.query_devices(kind='input')['name']}")
    print("Mode:", "reopen the mic for every test" if reopen else "one mic stream kept open")
    recorder = Recorder()
    stream = None if reopen else recorder.open_stream()
    if stream:
        stream.start()
    number = 1
    while True:
        try:
            input("\nPress Enter to start a test (Ctrl+C to quit) ")
        except (KeyboardInterrupt, EOFError):
            print()
            return
        run_test(number, recorder, reopen)
        number += 1


if __name__ == "__main__":
    main()
