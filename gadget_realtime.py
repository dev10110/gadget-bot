"""The Gadget Phone: a ringing speech-to-speech prop using the OpenAI Realtime API.

The wearer presses the button and the phone rings. After a couple of rings a random caller
(Chief Quimby, Penny, or Dr. Claw) picks up and speaks first in their own voice, then talks
hands-free with whoever is holding the phone until they hang up. Pressing the button during
a call hangs up early.

Mic audio streams straight to the model and its spoken reply streams straight back,
so there is no separate speech-to-text or text-to-speech step.
"""

import base64
import os
import queue
import socket
import wave
from pathlib import Path
import random
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

from button import Button, ButtonLight

if __name__ == "__main__":
    # The imports below take about 10 seconds on a Pi at boot, so pulse the light meanwhile.
    light = ButtonLight()
    light.loading()

import numpy as np
import sounddevice as sd
from openai import OpenAI
from scipy import signal

# KEY=value lines, gitignored. The Pi's systemd service reads the same file as its EnvironmentFile.
SECRETS_FILE = Path(__file__).with_name("SECRETS")

SAMPLE_RATE = 24_000  # the Realtime API's PCM format is fixed at 24 kHz, 16-bit mono
CHUNK_SECONDS = 0.05
# The full model follows vocal-delivery directions much better than gpt-realtime-mini.
MODEL = "gpt-realtime"
SPEECH_SPEED = 1.15  # 0.25 to 1.5; 1.0 is the model's normal pace

RINGS_BEFORE_ANSWER = 2
RING_SECONDS = 1.0  # a real phone rings for 2s with 4s gaps, which drags at a party
RING_GAP_SECONDS = 0.8
# Voice detection: raise toward 0.8 if party noise makes callers reply to nobody,
# lower toward 0.4 if they don't hear the guest.
VAD_THRESHOLD = 0.6
SILENCE_TO_END_TURN_MS = 600
# The mic stays muted while the caller talks, plus this long after, so the phone doesn't hear
# its own speaker and answer itself.
ECHO_GUARD_SECONDS = 0.5
NUDGE_AFTER_SILENCE_MS = 8_000  # caller asks "Hello? Still there?" after this much quiet
IDLE_HANGUP_SECONDS = 30  # hang up if the guest says nothing for this long
# Each caller is told what happened on this many earlier calls, so they can bring it up.
EARLIER_CALLS_TO_REMEMBER = 3
# While waiting for a press, check the internet this often; the light shows an error when it's down.
ONLINE_CHECK_SECONDS = 10
# Drop a WAV file here to use your own explosion instead of the synthesized one.
SELF_DESTRUCT_WAV = Path(__file__).with_name("self_destruct.wav")

SETTING = """You are a character in an Inspector Gadget themed Halloween party game. You are calling
Inspector Gadget on the Gadget Phone built into his hand. Whoever answers is a party guest: treat them
as one of Gadget's trusted agents standing in for him, since the Inspector is busy bumbling around.

# The call
Speak first as soon as the call connects. Keep every reply to one or two short sentences: your words
play through a small speaker in a noisy room. If you hear something garbled or unrelated, it is
probably party noise: ignore it or ask the guest to speak up. If the guest goes quiet, prompt them
once in character. After three or four exchanges, or as soon as the guest says goodbye or stays
silent, say a short in-character goodbye and then call the hang_up tool.

# Be sharp
- Really listen. React to the specifics of what the guest just said, never with a generic line,
  and pick up their name, costume, and jokes to use again later in the call.
- When the guest asks a question, answer it directly before moving on. Invent confident, funny
  details rather than dodging or changing the subject.
- Be quick-witted: play along with jokes, top them, and improvise when the guest goes off script,
  while still steering toward your goal for the call.
- Never repeat a line or a joke you've already used.
- Read the guest: simpler and gentler with kids, drier and cheekier with adults.
- Stay in character no matter what, and keep it party-friendly.

Act out sighs, laughs, gasps, and growls with your voice. Never say stage directions or sound
descriptions out loud as words."""

# Explains the earlier-call transcripts added to each caller's instructions.
EARLIER_CALLS_INTRO = """# Earlier calls on the Gadget Phone tonight
Every character knows what happened on these calls: Penny monitors the line with her computer book,
the Chief gets briefed by headquarters, and Dr. Claw has bugged the phone. The phone gets passed
around the party, so you are probably talking to someone new: ask rather than assume it's the same
agent. Bring up at most one thing from these calls, like an agent's code name, a mission, or a
suspect, and only where it fits naturally. Don't recap them."""


@dataclass
class Caller:
    name: str
    voice: str  # alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar
    persona: str
    model: str = MODEL
    self_destructs: bool = False  # play an explosion instead of the busy signal when they hang up
    speed: float = SPEECH_SPEED
    # Post-processing for voices none of the built-ins can do. Pitching down also slows speech,
    # so raise `speed` to compensate.
    pitch_semitones: float = 0.0
    grit: float = 0.0  # 0 is clean; 2-4 adds a raspy, distorted edge
    nasal: float = 0.0  # dB boost around 1.2 kHz with thinned-out bass; 6-12 sounds honky


CALLERS = [
    Caller(
        name="Chief Quimby",
        voice="marin",
        persona="""You are Chief Quimby, Gadget's long-suffering boss.

# Voice and delivery
- A flustered, pompous, stuffy older police chief. Nasal and honking, never smooth or polished.
- Wildly expressive and theatrical, like a cartoon character: big swings in pitch and volume,
  rising to an indignant squawk when exasperated, dropping to a hushed, urgent whisper for secrets.
- Uneven, hurried pacing: rush through some words, then stop dead for a dramatic pause.
- Sputter, stammer, and trip over your words. Harrumph, groan, and let out long, put-upon sighs.
- Never angry or tough, just perpetually frazzled.

# Sample phrases (for style only, don't repeat them verbatim)
- "Er, ahem, now see here, Gadget, this is, ah, highly classified!"
- "Ohhh, for heaven's sake... hhhhh... just listen, will you?"
- "Shh! I'm disguised as the punch bowl. Don't look at me!"

# The call
You are always hiding in a ridiculous disguise to deliver messages in secret, so mention it in
passing, like being disguised as a pumpkin, a coat rack, or a bowl of punch. Open with "Gadget, this
is Chief Quimby." Hand out one silly top-secret Halloween mission, such as finding who ate all the
candy corn or tailing a suspicious vampire near the snack table. Answer the guest's questions about
the mission. Always end the call with exactly "This message will self-destruct." and then hang up.
""",
        self_destructs=True,
        pitch_semitones=-2,
        grit=2.0,
        nasal=11,
        speed=1.25,
    ),
    Caller(
        name="Penny",
        voice="coral",
        persona="""You are Penny, Inspector Gadget's ten-year-old niece. Your uncle gets all the credit, but
you and your dog Brain secretly solve every case using your computer book and computer watch.

# Voice and delivery
- A bright, clear, youthful girl's voice: eager, quick, and sincere.
- Animated and expressive: excited gasps when something is interesting, a little giggle when the
  guest is funny, a fond, exasperated "Oh, Uncle Gadget..." when he comes up.
- Talk faster as you get excited, then slow down and lower your voice to share something secret.
- Polite, warm, and brave, with the confidence of a kid who is usually the smartest one in the room.
  Never bratty or sarcastic.

# Sample phrases (for style only, don't repeat them verbatim)
- "Oh! You must be one of Uncle Gadget's agents. Thank goodness, I need your help!"
- "Hold on, my computer book is analyzing... beep, beep... aha!"
- "Uncle Gadget means well, but... well, you know how he is."

# The call
A short, playful back-and-forth of about four exchanges. Ask one question at a time and end each
reply with it, react to what the guest actually says, and treat whatever they tell you as true:
there are no real clues at the party, so the guest's own answers become the clues.
1. Call for Uncle Gadget, realize you've reached one of his agents, and ask who they are tonight
   (their costume) so your computer book can verify they're not a M.A.D. agent in disguise.
2. "Analyze" the costume out loud, give them a fun secret agent code name based on it, then
   whisper that Dr. Claw has a M.A.D. agent hiding at this very party. Ask what the most
   suspicious costume they've seen tonight is.
3. Run their answer through your computer book and make a silly, excited deduction about it.
   Then give them a tiny, harmless secret mission they can actually do at the party, like telling
   the next person in a cape the password "wowsers," or keeping an eye on the snack table. Keep
   it kind: the "suspect" is a fun mystery, never someone to tease. Ask if they accept the mission.
4. Thank them with their code name, remind them not to tell Uncle Gadget you helped, and say
   goodbye.
If the guest asks their own questions or goes off on a tangent, play along in character, then
steer back to the next step.
""",
        # Pitching up sounds younger but also speeds speech up, hence the slower base speed.
        pitch_semitones=3,
        speed=1.0,
    ),
    Caller(
        name="Dr. Claw",
        voice="cedar",
        persona="""You are Dr. Claw, the unseen villain who runs M.A.D. Your voice is a deep, gravelly,
raspy growl: low, slow, and menacing, with a sneer on every word and the occasional sinister
chuckle. Speak as if leaning close to the microphone in a dark lair. Open by announcing you've
hacked the Gadget Phone. Taunt the guest about your evil Halloween scheme, like replacing all the
candy with raisins, and try to recruit them to M.A.D. Never reveal your face.
Say goodbye with "I'll get you next time, Gadget... next time!"
""",
        speed=1.4,
        pitch_semitones=-4,
        grit=3.0,
    ),
]

HANG_UP_TOOL = {
    "type": "function",
    "name": "hang_up",
    "description": "End the phone call. Call this right after saying goodbye.",
    "parameters": {"type": "object", "properties": {}},
}


def tone(freqs: list[float], seconds: float, volume: float = 0.2) -> bytes:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    wave = sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)
    return (wave * volume * 32767).astype(np.int16).tobytes()


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


# Classic North American phone sounds.
RING = tone([440, 480], RING_SECONDS)
RINGING = (RING + silence(RING_GAP_SECONDS)) * (RINGS_BEFORE_ANSWER - 1) + RING + silence(0.3)
HANG_UP_BEEPS = (tone([480, 620], 0.5) + silence(0.5)) * 3


def lowpass(signal: np.ndarray, cutoff_hz: float) -> np.ndarray:
    spectrum = np.fft.rfft(signal)
    spectrum[np.fft.rfftfreq(len(signal), 1 / SAMPLE_RATE) > cutoff_hz] = 0
    return np.fft.irfft(spectrum, len(signal))


def synth_explosion() -> bytes:
    """A fizzing fuse followed by a cartoon boom."""
    rng = np.random.default_rng(7)

    fuse_t = np.arange(int(SAMPLE_RATE * 1.0)) / SAMPLE_RATE
    fuse = rng.uniform(-1, 1, len(fuse_t)) - lowpass(rng.uniform(-1, 1, len(fuse_t)), 3000)
    fuse *= 0.15 * (0.6 + 0.4 * fuse_t) * (rng.random(len(fuse_t)) > 0.3)

    boom_t = np.arange(int(SAMPLE_RATE * 2.5)) / SAMPLE_RATE
    rumble = lowpass(rng.uniform(-1, 1, len(boom_t)), 300)
    rumble /= np.abs(rumble).max()
    thump = np.sin(2 * np.pi * 55 * boom_t * np.exp(-boom_t))  # pitch drops as it decays
    crack = rng.uniform(-1, 1, len(boom_t)) * np.exp(-boom_t / 0.03)
    boom = (rumble + 0.8 * thump) * np.exp(-boom_t / 0.5) + crack
    boom = np.tanh(2.5 * boom) * 0.8  # soft clip for extra crunch

    pcm = np.concatenate([fuse, np.zeros(int(SAMPLE_RATE * 0.1)), boom])
    return (pcm * 32767).astype(np.int16).tobytes()


def load_wav(path: Path) -> bytes:
    """Reads a 16-bit WAV and converts it to 24 kHz mono."""
    with wave.open(str(path)) as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    mono = samples.reshape(-1, channels).mean(axis=1)
    resampled = np.interp(
        np.arange(0, len(mono), rate / SAMPLE_RATE), np.arange(len(mono)), mono
    )
    return resampled.astype(np.int16).tobytes()


SELF_DESTRUCT = load_wav(SELF_DESTRUCT_WAV) if SELF_DESTRUCT_WAV.exists() else synth_explosion()


class Player:
    """Plays queued PCM16 audio as it arrives; can be cut off mid-sentence."""

    def __init__(self):
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.last_sound_at = 0.0
        self.stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=self._fill
        )
        self.stream.start()

    def add(self, pcm: bytes):
        with self.lock:
            self.buffer.extend(pcm)

    def stop(self):
        with self.lock:
            self.buffer.clear()

    def is_playing(self) -> bool:
        with self.lock:
            return bool(self.buffer)

    def seconds_since_sound(self) -> float:
        return 0.0 if self.is_playing() else time.monotonic() - self.last_sound_at

    def wait_until_done(self):
        while self.is_playing():
            time.sleep(0.05)

    def _fill(self, outdata, frames, time_info, status):
        wanted = len(outdata)
        with self.lock:
            chunk = bytes(self.buffer[:wanted])
            del self.buffer[:wanted]
        if chunk:
            self.last_sound_at = time.monotonic()
        outdata[: len(chunk)] = chunk
        outdata[len(chunk) :] = b"\x00" * (wanted - len(chunk))


def nasal_filter(boost_db: float) -> np.ndarray:
    """High-pass to thin out the chest tone plus a peaking boost in the honky midrange."""
    highpass = signal.butter(2, 300, "highpass", fs=SAMPLE_RATE, output="sos")
    # Peaking EQ from the RBJ audio EQ cookbook.
    a, w = 10 ** (boost_db / 40), 2 * np.pi * 1200 / SAMPLE_RATE
    alpha = np.sin(w) / (2 * 1.4)
    b0, b1, b2 = 1 + alpha * a, -2 * np.cos(w), 1 - alpha * a
    a0, a1, a2 = 1 + alpha / a, -2 * np.cos(w), 1 - alpha / a
    peak = np.array([[b0 / a0, b1 / a0, b2 / a0, 1, a1 / a0, a2 / a0]])
    return np.vstack([highpass, peak])


class VoiceEffect:
    """Pitch-shifts, filters, and roughens streamed PCM16 chunks without clicks at chunk boundaries."""

    def __init__(self, caller: Caller):
        self.step = 2 ** (caller.pitch_semitones / 12)  # input samples consumed per output sample
        self.grit = caller.grit
        self.nasal = caller.nasal
        self.tail = np.zeros(0)
        self.pos = 0.0
        if self.nasal:
            self.sos = nasal_filter(self.nasal)
            self.filter_state = np.zeros((len(self.sos), 2))

    def __call__(self, pcm: bytes) -> bytes:
        if self.step == 1 and not (self.grit or self.nasal):
            return pcm
        x = np.concatenate([self.tail, np.frombuffer(pcm, dtype=np.int16) / 32768])
        positions = np.arange(self.pos, len(x) - 1, self.step)
        y = np.interp(positions, np.arange(len(x)), x)
        if len(positions):
            self.pos = positions[-1] + self.step
        self.pos -= len(x) - 1
        self.tail = x[-1:]
        if self.nasal:
            y, self.filter_state = signal.sosfilt(self.sos, y, zi=self.filter_state)
            y *= 10 ** (-self.nasal / 40)  # claw back some of the boost's extra loudness
        if self.grit:
            y = np.tanh(self.grit * y) / np.tanh(self.grit)
        return (np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes()


def load_secrets():
    """Loads SECRETS into the environment; variables already set in the shell take precedence."""
    if not SECRETS_FILE.exists():
        return
    for line in SECRETS_FILE.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class Microphone:
    """Keeps one mic stream open for the whole run and forwards audio to the active call.

    Reopening the mic for every call breaks some setups: a Bluetooth headset on the Pi 4 only
    delivers audio the first time its mic is opened, and is silent after that.
    """

    def __init__(self, player: Player):
        self.player = player
        self.connection = None  # set while a call is in progress
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * CHUNK_SECONDS),
            callback=self._on_audio,
        )
        self.stream.start()

    def _on_audio(self, indata, frames, time_info, status):
        connection = self.connection
        if connection is None or self.player.seconds_since_sound() < ECHO_GUARD_SECONDS:
            return
        pcm16 = (np.clip(indata[:, 0], -1, 1) * 32767).astype(np.int16)
        try:
            connection.input_audio_buffer.append(audio=base64.b64encode(pcm16.tobytes()).decode())
        except Exception:
            # The connection dropped. An exception escaping this callback would stop the mic
            # stream for good; the call itself notices the drop and ends.
            pass


print_lock = threading.Lock()


def log(line: str = ""):
    """Prints whole lines only, so the event thread and the main loop never interleave."""
    with print_lock:
        print(line, flush=True)


class CallState:
    def __init__(self):
        self.hung_up = threading.Event()
        self.guest_spoke_at = time.monotonic()
        self.transcript: list[str] = []  # finished lines in the order they arrive
        self.dropped = False  # the connection failed mid-call


def handle_events(connection, caller: Caller, player: Player, state: CallState):
    try:
        read_events(connection, caller, player, state)
    except Exception as error:
        log(f"[connection lost] {error}")
        state.dropped = True
        state.hung_up.set()


def read_events(connection, caller: Caller, player: Player, state: CallState):
    effect = VoiceEffect(caller)
    for event in connection:
        if event.type == "response.output_audio.delta":
            player.add(effect(base64.b64decode(event.delta)))
        # Transcripts are printed whole, once finished: the guest's transcription arrives
        # asynchronously and would otherwise land in the middle of the caller's streamed line.
        elif event.type == "response.output_audio_transcript.done":
            line = f"{caller.name}: {event.transcript.strip()}"
            state.transcript.append(line)
            log(line)
        elif event.type == "input_audio_buffer.speech_started":
            state.guest_spoke_at = time.monotonic()
        elif event.type == "conversation.item.input_audio_transcription.completed":
            line = f"Guest: {event.transcript.strip()}"
            state.transcript.append(line)
            log(line)
        elif event.type == "response.done":
            if any(
                item.type == "function_call" and item.name == "hang_up"
                for item in event.response.output or []
            ):
                state.hung_up.set()
        elif event.type == "error":
            log(f"[error] {event.error.message}")


def instructions(caller: Caller, earlier_calls: list[str]) -> str:
    now = time.strftime("%-I:%M %p on %A, %B %-d")
    parts = [SETTING, f"It is now {now}.", caller.persona]
    if earlier_calls:
        parts.append(EARLIER_CALLS_INTRO + "\n\n" + "\n\n".join(earlier_calls))
    return "\n\n".join(parts)


def session_config(caller: Caller, earlier_calls: list[str]) -> dict:
    return {
        "type": "realtime",
        "instructions": instructions(caller, earlier_calls),
        "output_modalities": ["audio"],
        "tools": [HANG_UP_TOOL],
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": VAD_THRESHOLD,
                    "silence_duration_ms": SILENCE_TO_END_TURN_MS,
                    "idle_timeout_ms": NUDGE_AFTER_SILENCE_MS,
                    "create_response": True,
                    # Party noise shouldn't cut the caller off mid-sentence.
                    "interrupt_response": False,
                },
                "noise_reduction": {"type": "far_field"},
                "transcription": {"model": "gpt-4o-mini-transcribe"},
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                # A session can't change voice once it has spoken, which is why each
                # caller gets a fresh connection.
                "voice": caller.voice,
                "speed": caller.speed,
            },
        },
    }


def take_call(
    client: OpenAI,
    caller: Caller,
    earlier_calls: list[str],
    player: Player,
    mic: Microphone,
    button: Button,
    light: ButtonLight,
) -> str:
    """Rings, runs the call, and returns its transcript for later callers to know about, or an
    empty string if the guest never said anything."""
    started = time.strftime("%-I:%M %p")
    log()
    log("☎  Ring ring...")
    light.ringing()
    player.add(RINGING)
    state = CallState()

    # Connect while the phone is still ringing to hide the connection delay.
    with client.realtime.connect(model=caller.model) as connection:
        connection.session.update(session=session_config(caller, earlier_calls))
        threading.Thread(
            target=handle_events, args=(connection, caller, player, state), daemon=True
        ).start()

        player.wait_until_done()
        light.on_call()
        connection.response.create()  # the caller speaks first
        state.guest_spoke_at = time.monotonic()
        button.clear()

        mic.connection = connection
        try:
            while not state.hung_up.is_set():
                if button.wait(timeout=0.1):
                    log("(wearer hung up)")
                    player.stop()
                    break
                if time.monotonic() - state.guest_spoke_at > IDLE_HANGUP_SECONDS:
                    log("(nobody's there)")
                    break
            player.wait_until_done()
        finally:
            mic.connection = None

    if state.hung_up.is_set() and caller.self_destructs and not state.dropped:
        log("💥")
        player.add(SELF_DESTRUCT)
    else:
        player.add(HANG_UP_BEEPS)
    player.wait_until_done()
    log("📴 Call ended.")
    if not any(line.startswith("Guest:") for line in state.transcript):
        return ""
    return f"{caller.name} called at {started}:\n" + "\n".join(state.transcript)


def online() -> bool:
    """True if the OpenAI API can be reached."""
    try:
        socket.create_connection(("api.openai.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


def wait_until_online(light: ButtonLight):
    if online():
        return
    log("(no internet: waiting for it to come back)")
    light.no_internet()
    while not online():
        time.sleep(3)
    log("(back online)")


def main(light: ButtonLight):
    load_secrets()
    client = OpenAI()
    player = Player()
    mic = Microphone(player)
    button = Button()
    last_caller = None
    earlier_calls: deque[str] = deque(maxlen=EARLIER_CALLS_TO_REMEMBER)

    log("Gadget Phone online. Press the button to make it ring, Ctrl+C to quit.")
    while True:
        wait_until_online(light)
        light.ready()
        button.clear()
        if not button.wait(timeout=ONLINE_CHECK_SECONDS):
            continue
        caller = random.choice([c for c in CALLERS if c is not last_caller])
        try:
            transcript = take_call(client, caller, list(earlier_calls), player, mic, button, light)
        except Exception as error:  # e.g. couldn't connect: beep and carry on rather than crash
            log(f"[call failed] {error}")
            player.stop()
            player.add(HANG_UP_BEEPS)
            player.wait_until_done()
            continue
        if transcript:
            earlier_calls.append(transcript)
        last_caller = caller


if __name__ == "__main__":
    try:
        main(light)
    except KeyboardInterrupt:
        print("\nThis message will self-destruct.")
