"""A volume control page for the Gadget Phone, to open in a phone's browser.

Serves a slider for the speaker volume and a button that plays the ring, so the volume can be set
by ear. Runs as its own service, apart from gadget_realtime.py, and uses only the standard library.

Usage: python3 volume_server.py    # then open http://<pi address>:8080 on the phone
"""

import io
import json
import math
import re
import subprocess
import tempfile
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
SINK = "@DEFAULT_AUDIO_SINK@"  # the Voice HAT speaker
SAMPLE_RATE = 24_000
RING_VOLUME = 0.4  # keep in step with TONE_VOLUME in gadget_realtime.py

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gadget Phone Volume</title>
<style>
  :root { color-scheme: light dark; --accent: #e0a100; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 32px 20px; text-align: center; }
  h1 { font-size: 1.3rem; font-weight: 600; margin: 0 0 32px; }
  #level { font-size: 4rem; font-weight: 700; font-variant-numeric: tabular-nums; }
  input[type=range] { width: 100%; max-width: 420px; height: 48px; accent-color: var(--accent); }
  .steps { display: flex; gap: 12px; justify-content: center; margin: 16px 0 32px; }
  button { font: inherit; font-size: 1.1rem; padding: 14px 22px; border-radius: 12px;
           border: 1px solid #8884; background: #8881; color: inherit; }
  button:active { background: #8883; }
  #ring { background: var(--accent); color: #000; border: none; font-weight: 600; }
  #status { min-height: 1.5em; margin-top: 20px; opacity: 0.7; }
</style>
</head>
<body>
<h1>Gadget Phone volume</h1>
<div id="level">--</div>
<input id="slider" type="range" min="0" max="100" step="1" value="0">
<div class="steps">
  <button data-step="-5">&minus;5</button>
  <button data-step="5">+5</button>
</div>
<button id="ring">Play the ring</button>
<div id="status"></div>
<script>
const slider = document.getElementById("slider");
const level = document.getElementById("level");
const status = document.getElementById("status");

function show(volume) {
  slider.value = volume;
  level.textContent = volume + "%";
}

async function call(method, path, body) {
  try {
    const response = await fetch(path, { method, body: body && JSON.stringify(body) });
    if (!response.ok) throw new Error(await response.text());
    status.textContent = "";
    return await response.json();
  } catch (error) {
    status.textContent = "Couldn't reach the phone: " + error.message;
  }
}

async function setVolume(volume) {
  volume = Math.max(0, Math.min(100, volume));
  show(volume);
  const result = await call("POST", "/volume", { volume });
  if (result) show(result.volume);
}

slider.addEventListener("input", () => { level.textContent = slider.value + "%"; });
slider.addEventListener("change", () => setVolume(Number(slider.value)));
document.querySelectorAll("[data-step]").forEach(button =>
  button.addEventListener("click", () => setVolume(Number(slider.value) + Number(button.dataset.step))));
document.getElementById("ring").addEventListener("click", () => call("POST", "/ring"));

call("GET", "/volume").then(result => result && show(result.volume));
</script>
</body>
</html>
"""


def get_volume() -> int:
    """The speaker volume as a percentage, on the same scale as wpctl."""
    output = subprocess.run(["wpctl", "get-volume", SINK], capture_output=True, text=True, check=True)
    return round(float(re.search(r"Volume: ([\d.]+)", output.stdout).group(1)) * 100)


def set_volume(percent: int):
    # Above 100% the Pi clips the audio, so cap it there.
    subprocess.run(["wpctl", "set-volume", "--limit", "1.0", SINK, f"{percent}%"], check=True)


def ring_wav() -> bytes:
    """One ring at the same pitch and loudness as gadget_realtime.py's RING."""
    samples = bytearray()
    for i in range(SAMPLE_RATE):
        t = i / SAMPLE_RATE
        value = (math.sin(2 * math.pi * 440 * t) + math.sin(2 * math.pi * 480 * t)) / 2
        samples += int(value * RING_VOLUME * 32767).to_bytes(2, "little", signed=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(samples))
    return buffer.getvalue()


RING_WAV = ring_wav()


def play_ring():
    with tempfile.NamedTemporaryFile(suffix=".wav") as file:
        file.write(RING_WAV)
        file.flush()
        subprocess.run(["pw-play", file.name], check=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/volume":
            self.reply_json({"volume": get_volume()})
        else:
            self.reply(404, b"Not found", "text/plain")

    def do_POST(self):
        if self.path == "/volume":
            length = int(self.headers.get("Content-Length", 0))
            percent = int(json.loads(self.rfile.read(length))["volume"])
            set_volume(max(0, min(100, percent)))
            self.reply_json({"volume": get_volume()})
        elif self.path == "/ring":
            play_ring()
            self.reply_json({})
        else:
            self.reply(404, b"Not found", "text/plain")

    def reply_json(self, data: dict):
        self.reply(200, json.dumps(data).encode(), "application/json")

    def reply(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # one line per slider move would flood the journal


if __name__ == "__main__":
    print(f"Volume control on port {PORT}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
