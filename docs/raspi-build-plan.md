# Raspberry Pi and costume build plan

Goal: the Gadget Phone runs untethered inside the trench coat, starts on its own when powered on,
and is driven by one hidden button. Halloween is October 31, so this is laid out as five
milestones, each testable on its own.

## How it fits in the costume

In the show, Gadget's phone is built into his hand: thumb to his ear, pinky to his mouth. So the
"phone" is the gloved hand.

```
 fedora ──────────── (optional) status LED under the brim
 glove (phone hand)  mic at the pinky, cable up the sleeve
 glove (other hand)  button in the palm, pressed with your fingers
 chest pocket ────── speaker, facing out so nearby guests can hear
 inside pocket ───── Pi 4 in a vented case + power bank
```

- **Mic in the glove, speaker on the chest.** Guests lean in to talk into your hand, and everyone
  nearby hears the caller. Keeping the speaker away from the mic also cuts down on echo.
- **Button in the other glove's palm.** You can press it while holding your phone hand out to a
  guest, without anyone seeing.
- **Connectors at the wrists** (USB extension or 3.5 mm) so gloves come off without unthreading the
  sleeves. Tape or zip-tie the cables inside the sleeve lining so they don't get yanked.

## Parts list

| Part | Notes |
|---|---|
| Raspberry Pi 4 + microSD (16 GB+) | Already have |
| USB mic | A USB lavalier/lapel mic is ideal: tiny, fits in a glove, picks up close voices over room noise |
| USB speaker | Already have. Test it at party volume first (see milestone 2); if too quiet, use a USB sound card + PAM8403 amp board + 3 W speaker |
| Momentary push button + 2 jumper wires | Any small tactile or arcade-style button |
| Power bank, 10,000–20,000 mAh, 5 V / 3 A output | The Pi 4 is fussy about power: a weak bank causes random reboots. 20,000 mAh lasts well over 8 hours |
| USB-C cable, short | For bank → Pi |
| Vented case with heatsink | It will live in a warm coat pocket |
| Optional: LED + 330 Ω resistor | Ready / on-a-call / error light under the hat brim |
| USB extension cables | For the wrist connectors and routing |

## Milestone 1: Pi software setup (an evening)

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In the Imager settings, turn on
   SSH and add **your phone hotspot's Wi-Fi** (plus home Wi-Fi), since the hotspot is what you'll
   use at the party.
2. SSH in and install system packages:
   ```bash
   sudo apt update && sudo apt install -y git python3-venv libportaudio2 python3-gpiozero
   ```
3. Clone the repo, create a venv, `pip install -r requirements.txt`.
4. Create the gitignored `SECRETS` file in the repo folder (copy it over with `scp`, never commit it):
   ```bash
   echo "OPENAI_API_KEY=sk-..." > SECRETS && chmod 600 SECRETS
   ```
5. Run `python gadget_realtime.py` over SSH and press Enter to place a call.

## Milestone 2: audio on the Pi (an evening)

1. Plug in the mic and speaker, then find them: `arecord -l` and `aplay -l`.
2. Test: `arecord -D plughw:<mic card>,0 -f S16_LE -r 24000 -d 5 test.wav && aplay -D plughw:<speaker card>,0 test.wav`.
3. Make them the defaults in `~/.asoundrc` so the program picks them up. The `plug` layer
   converts the API's 24 kHz to whatever the USB devices support:
   ```
   pcm.!default { type asym playback.pcm "plug:hw:<speaker card>" capture.pcm "plug:hw:<mic card>" }
   ```
4. Turn the speaker volume up with `alsamixer` and check it's audible in a loud room.
5. Code change: switch `noise_reduction` to `near_field` once the mic is close to the mouth in a glove.

## Milestone 3: the button (an evening)

1. Wire the button between **GPIO 17 (physical pin 11)** and **ground (pin 9)**. No resistor
   needed; the Pi's internal pull-up handles it.
2. Code change: drive the existing `Button` class from GPIO
   (`gpiozero.Button(17, bounce_time=0.05).when_pressed = button.press`), keeping Enter as a
   fallback for testing over SSH.
3. Optional: hold the button for 5 seconds to shut the Pi down cleanly, which protects the SD card.

## Milestone 4: run on its own (an evening)

1. Code changes for running unattended:
   - Catch network/API errors during a call, play a "line's dead" beep, and keep going instead of
     crashing.
   - Play a short "Gadget Phone online" sound at startup, so you know it's ready without a screen.
   - Optional: drive the status LED (slow blink = connecting to Wi-Fi, solid = ready, fast = error).
2. Add a systemd service so it starts on boot and restarts if it ever crashes:
   ```ini
   # /etc/systemd/system/gadget-phone.service
   [Unit]
   Description=Gadget Phone
   After=network-online.target sound.target
   Wants=network-online.target

   [Service]
   User=pi
   WorkingDirectory=/home/pi/detective-bot
   EnvironmentFile=/home/pi/detective-bot/SECRETS
   ExecStart=/home/pi/detective-bot/.venv/bin/python gadget_realtime.py
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   ```
   `sudo systemctl enable --now gadget-phone`; logs with `journalctl -u gadget-phone -f`.
3. Test the real cold start: power bank → Pi, phone hotspot on, wait for the startup sound, press
   the button.

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

- **Speaker too quiet:** the most likely problem. Test in milestone 2 and order an amp board early
  if needed.
- **Hotspot drops:** Realtime needs a steady connection. Keep your phone on you with its hotspot
  set to stay on, and note that your phone's data use is small (audio is a few MB per call).
- **Callers replying to party noise:** raise `VAD_THRESHOLD`; a close-up lav mic with `near_field`
  noise reduction helps the most.
- **SD card corruption from yanking power:** use the hold-to-shutdown button, or enable the
  read-only overlay filesystem in `raspi-config` once everything works.
- **Cost:** each call is a minute or two of `gpt-realtime` audio. Set a monthly spending limit on
  the OpenAI dashboard before the party.
