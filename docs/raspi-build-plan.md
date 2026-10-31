# Raspberry Pi and costume build plan

Goal: the Gadget Phone runs untethered inside the trench coat, starts on its own when powered on,
and is driven by one hidden button. Halloween is October 31, so this is laid out as five
milestones, each testable on its own.

## How it fits in the costume

In the show, Gadget's phone is built into his hand: thumb to his ear, pinky to his mouth. So the
"phone" is the gloved hand.

```
 glove (phone hand)  mic at the pinky, cable up the sleeve
 glove (other hand)  Voice HAT button (with its light) in the palm, pressed with your fingers
 chest pocket ────── speaker, facing out so nearby guests can hear
 inside pocket ───── Pi 4 + Voice HAT in a vented case, power bank
```

- **Mic in the glove, speaker on the chest.** Guests lean in to talk into your hand, and everyone
  nearby hears the caller. Keeping the speaker away from the mic also cuts down on echo.
- **Button in the other glove's palm.** You can press it while holding your phone hand out to a
  guest, without anyone seeing. Its light shows the phone's state (see the README).
- **Connectors at the wrists** (USB extension or 3.5 mm) so gloves come off without unthreading the
  sleeves. Tape or zip-tie the cables inside the sleeve lining so they don't get yanked.

## Parts list

| Part | Notes |
|---|---|
| Raspberry Pi 4 + microSD (16 GB+) | |
| Google AIY Voice HAT | Provides the button connector (GPIO 23), the button light (GPIO 25), and a MAX98357A class-D amp (about 3 W into 4 Ω) for the speaker terminals |
| Speaker | Wired to the Voice HAT's speaker terminals. A 4 Ω speaker gets more power from the amp than an 8 Ω one |
| AIY arcade button with built-in light | Plugs into the Voice HAT's button connector |
| USB mic | A USB lavalier/lapel mic is ideal: tiny, fits in a glove, picks up close voices over room noise. USB EarPods work for testing |
| Power bank, 10,000–20,000 mAh, 5 V / 3 A output | The Pi 4 is fussy about power, and the speaker amp draws from the same 5 V: a weak bank causes random reboots or quiet, distorted audio |
| USB-C cable, short | For bank → Pi |
| Vented case with heatsink | It will live in a warm coat pocket |
| USB extension cables | For the wrist connectors and routing |

## Milestone 1: Pi software setup (done)

1. Flash **Raspberry Pi OS (64-bit)** with Raspberry Pi Imager. In the Imager settings, turn on
   SSH and add **your phone hotspot's Wi-Fi** (plus home Wi-Fi), since the hotspot is what you'll
   use at the party.
2. Enable the Voice HAT by adding `dtoverlay=googlevoicehat-soundcard` to
   `/boot/firmware/config.txt`, then reboot.
3. SSH in and install system packages:
   ```bash
   sudo apt update && sudo apt install -y git python3-venv libportaudio2 python3-gpiozero
   ```
4. Clone the repo, create the venv with `python3 -m venv --system-site-packages .venv`, and
   `pip install -r requirements.txt`.
5. Create the gitignored `SECRETS` file in the repo folder (copy it over with `scp`, never commit it):
   ```bash
   echo "OPENAI_API_KEY=sk-..." > SECRETS && chmod 600 SECRETS
   ```
6. Run `python gadget_realtime.py` over SSH and press Enter to place a call.

## Milestone 2: audio on the Pi

Audio goes through PipeWire, so `wpctl` (not `alsamixer` or `~/.asoundrc`) picks the devices and
sets the volume.

1. Plug in the mic and check both devices show up: `wpctl status`. The speaker is the
   "Built-in Audio Stereo" sink on `hw:1` (the Voice HAT); the default sink and source are marked `*`.
   Pick another with `wpctl set-default <id>`.
2. **Set the speaker volume to 70%:** `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.7`. The Voice
   HAT has no hardware volume control, so this software volume is the only one, and WirePlumber
   starts new devices at 40%. That slider is cubic, so 40% is 0.4³ ≈ 6% amplitude (−24 dB): the
   reason the phone was so quiet. 70% (0.7³ ≈ 34%, −9 dB) is the chosen level; 100% is the
   loudest the Pi can play without clipping, but too loud up close. The setting is
   saved in `~/.local/state/wireplumber/default-routes` and survives reboots.
3. Check it with `python mic_test.py` and in a loud room. If it's still too quiet, the remaining
   options are the speaker itself (a 4 Ω, efficient speaker in an open pocket) and making the
   audio louder in software (compressing the callers' voices so their quiet parts come up).
4. Code change: switch `noise_reduction` to `near_field` once the mic is close to the mouth in a glove.

## Milestone 3: the button (done)

The AIY arcade button plugs into the Voice HAT's button connector: the switch is GPIO 23 and its
light is GPIO 25 (see `button.py`). Enter on the keyboard still works as the button over SSH.

Optional: hold the button for 5 seconds to shut the Pi down cleanly, which protects the SD card.

## Milestone 4: run on its own (done)

1. Network and API errors during a call play the hang-up beeps and carry on instead of crashing.
2. The button light shows when the phone is ready, so no screen or startup sound is needed: fast
   blink while starting up, slow breathing when ready, and a no-internet pattern when the hotspot
   is down.
3. The systemd user service in [gadget-phone.service](../gadget-phone.service) starts it on boot
   and restarts it if it crashes. See the README's "Running on the Pi".
4. Test the real cold start: power bank → Pi, phone hotspot on, wait for the light to breathe,
   press the button.

## Milestone 5: build it into the costume (a weekend)

1. Mount the mic at the pinky of the phone glove (a small hole in the fingertip, hot glue or a
   stitch to hold it).
2. Mount the button in the other glove's palm. Glue it to a small piece of card so it doesn't
   twist, and route its wires up the sleeve.
3. Put the speaker in the chest pocket facing out; cut a few slits in the pocket if the fabric
   muffles it.
4. Pi + power bank in the inside pocket. Leave the case vents uncovered, and check after
   30 minutes that it isn't overheating (`vcgencmd measure_temp`; under 75 °C is fine).
5. Dress rehearsal somewhere loud, like with music playing. Tune `VAD_THRESHOLD` and the speaker
   volume there, not in a quiet room.

## Risks to check early

- **Speaker too quiet:** check `wpctl get-volume @DEFAULT_AUDIO_SINK@` says 0.70 (see milestone 2).
  A new or reset device starts at 0.40 again.
- **Hotspot drops:** Realtime needs a steady connection. Keep your phone on you with its hotspot
  set to stay on, and note that your phone's data use is small (audio is a few MB per call).
- **Callers replying to party noise:** raise `VAD_THRESHOLD`; a close-up lav mic with `near_field`
  noise reduction helps the most.
- **SD card corruption from yanking power:** use the hold-to-shutdown button, or enable the
  read-only overlay filesystem in `raspi-config` once everything works.
- **Cost:** each call is a minute or two of `gpt-realtime` audio. Set a monthly spending limit on
  the OpenAI dashboard before the party.
