"""Plays sample lines so you can pick voices.

Usage: python voice_sampler.py            # the same line in every built-in voice
       python voice_sampler.py ash cedar  # just these voices
       python voice_sampler.py --callers  # each phone caller with their voice, speed, and effects
       python voice_sampler.py --caller claw ash echo cedar  # one caller tried in several voices
"""

import base64
import sys

import numpy as np
import sounddevice as sd
from openai import OpenAI

from gadget_realtime import CALLERS, MODEL, SAMPLE_RATE, SPEECH_SPEED, VoiceEffect, load_secrets

VOICES = ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]
SAMPLE_LINE = (
    "Your mission, should you accept it, is to find out who stole the candy. "
    "I suspect Dr. Claw. This message will self-destruct."
)
READ_ALOUD = "Read the user's text aloud exactly as written, in a crisp, official tone."


def speak(
    client: OpenAI, voice: str, speed: float, instructions: str, text: str, model: str = MODEL
) -> bytes:
    audio = bytearray()
    # A session's voice can't change once it has spoken, so each sample gets its own connection.
    with client.realtime.connect(model=model) as connection:
        connection.session.update(
            session={
                "type": "realtime",
                "instructions": instructions,
                "output_modalities": ["audio"],
                "audio": {
                    "output": {
                        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        "voice": voice,
                        "speed": speed,
                    }
                },
            }
        )
        connection.conversation.item.create(
            item={"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
        )
        connection.response.create()
        for event in connection:
            if event.type == "response.output_audio.delta":
                audio.extend(base64.b64decode(event.delta))
            elif event.type == "error":
                raise RuntimeError(event.error.message)
            elif event.type == "response.done":
                break
    return bytes(audio)


def play(pcm: bytes):
    sd.play(np.frombuffer(pcm, dtype=np.int16), SAMPLE_RATE, blocking=True)


def play_caller(client: OpenAI, caller, voice: str):
    print(f"▶ {caller.name} as {voice}...", flush=True)
    try:
        pcm = speak(
            client,
            voice,
            caller.speed,
            caller.persona,
            "The call just connected. Say your opening line.",
            caller.model,
        )
    except RuntimeError as error:
        print(f"  [error] {error}")
        return
    play(VoiceEffect(caller)(pcm))


def main():
    load_secrets()
    client = OpenAI()
    args = sys.argv[1:]

    if args == ["--callers"]:
        for caller in CALLERS:
            play_caller(client, caller, caller.voice)
        return

    if args[:1] == ["--caller"] and len(args) > 1:
        matches = [c for c in CALLERS if args[1].lower() in c.name.lower()]
        if not matches:
            sys.exit(f"No caller matching {args[1]!r}. Callers: {', '.join(c.name for c in CALLERS)}")
        for voice in args[2:] or [matches[0].voice]:
            play_caller(client, matches[0], voice)
        return

    for voice in args or VOICES:
        print(f"▶ {voice}...", flush=True)
        try:
            play(speak(client, voice, SPEECH_SPEED, READ_ALOUD, SAMPLE_LINE))
        except RuntimeError as error:
            print(f"  [error] {error}")


if __name__ == "__main__":
    main()
