# Gadget Phone

An Inspector Gadget costume prop. Press the button, the phone rings, and Chief Quimby, Penny, or
Dr. Claw is on the line, each with their own voice. Party guests talk to them hands-free until the
caller hangs up (Quimby's message self-destructs).

Mic → OpenAI Realtime API (speech-to-speech, streaming) → speaker. Built to run on a Raspberry Pi 4
with a USB mic and speaker; see [docs/raspi-build-plan.md](docs/raspi-build-plan.md).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "OPENAI_API_KEY=sk-..." > SECRETS && chmod 600 SECRETS
python gadget_realtime.py
```

API keys live in `SECRETS` (one `KEY=value` per line), which is gitignored and loaded at startup.
A variable already exported in your shell wins over the file.

On the Raspberry Pi, create the venv with `python3 -m venv --system-site-packages .venv` instead,
so it can use the system's GPIO libraries (`gpiozero`, `lgpio`), which don't build from pip.

Press the button (or Enter) to make the phone ring, and again to hang up early. On the Pi the
button is the AIY Voice HAT's button connector (GPIO 23); its light is solid when ready, blinks
fast while ringing, and blinks slowly during a call. On macOS, allow microphone access
for your terminal in System Settings → Privacy & Security → Microphone.

Check the mic with `python mic_test.py`: a live level meter, then playback.

## Running on the Pi

[gadget-phone.service](gadget-phone.service) starts the phone at boot and restarts it if it
crashes (install steps are at the top of the file). Useful commands:

```bash
journalctl --user -u gadget-phone -f     # watch the calls and errors live
systemctl --user restart gadget-phone    # after a git pull
systemctl --user stop gadget-phone       # before running it by hand or running mic_test.py
```

| Button light | Meaning |
|---|---|
| Solid | Ready: press to ring |
| Fast blink | Ringing |
| Slow blink | On a call |
| Three quick blinks, pause, repeat | No internet: check the phone hotspot |
| Off | Not running: check the logs |

## Picking voices

```bash
python voice_sampler.py                          # one line in every built-in voice
python voice_sampler.py --callers                # each caller as configured, with effects
python voice_sampler.py --caller claw ash cedar  # one caller tried in several voices
```

## Tweaking

Everything lives at the top of `gadget_realtime.py`:

- `CALLERS`: each caller's voice, prompt, speed, and effects (`pitch_semitones`, `grit`, `nasal`).
- `VAD_THRESHOLD`: raise if callers reply to party noise, lower if they miss the guest.
- `RINGS_BEFORE_ANSWER`, `RING_SECONDS`, `RING_GAP_SECONDS`: the ring.
- `EARLIER_CALLS_TO_REMEMBER`: how many recent call transcripts each new caller is told about, so
  they can bring up an earlier agent's code name or mission. Memory resets when the program restarts.
- `self_destruct.wav`: drop a 16-bit WAV next to the script to replace the synthesized explosion.
