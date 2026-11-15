# Laptop agent: run the laptop half of Meridies

**You are** the agent on the team laptop. **The Pi** (`172.20.10.5`, on the phone hotspot) already runs its half. You run the parts that need the headset and the screen, and you only **connect** to the Pi.

## What is already running on the Pi (don't start these)

| Port on the Pi | Service | Check |
|---|---|---|
| ZMQ **5556** | obstacle warnings (≤3, nearest first) from the six ToF sensors | `tools/feed_check.py` below |
| TCP **5557** | raw sensor frames | `tools/feed_check.py --raw` |
| TCP **5558** | object debug stream | `tools/feed_check.py` |
| HTTP **8090** | cap camera: `/snapshot.jpg`, `/health`, live view at `/` | open http://172.20.10.5:8090/ |
| HTTP **8080** | intro web experience | open http://172.20.10.5:8080/ |

## What you run on the laptop

| Part | Folder | Needs |
|---|---|---|
| A. Audio warning engine (spatial beeps from 5556) | `audio/` | wired headphones |
| B. Voice agent (Gemini Live: see / feel distance / remember) | `voice_agent/` | headset mic + earphones, the Gemini key |
| C. Memory layer (optional, for "what did I record…") | `connect/` | nothing extra |
| D. Viewers (optional, for debugging/demo) | `sensing_pi/tools/` | a screen |

## Rules
- **Never bind or publish on 5555–5558.** In particular **don't run `audio/mock_warnings.py`**, because it would fight the Pi for 5556. The engine and the agent only *subscribe*, which is fine.
- **Don't edit `sensing_pi/`.** Report problems to the Pi side instead.
- **Never commit the API key.** `voice_agent/.env` is git-ignored; keep it that way.

## 0. Code and network
```bash
git clone https://github.com/Aliceeia/HackPrinceton_2026.git   # or: cd HackPrinceton_2026 && git checkout main && git pull
cd HackPrinceton_2026                                          # all paths below are relative to this
ping -c 3 172.20.10.5                                          # laptop must be on the same hotspot
```
Use the **`main`** branch; it contains `audio/`, `voice_agent/`, `connect/` and `sensing_pi/`.

## 1. Python environment (once)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r audio/requirements.txt -r voice_agent/requirements.txt matplotlib pillow
```
- **Linux:** `sounddevice` needs PortAudio: `sudo apt install libportaudio2`.
- **macOS:** allow microphone access for the terminal, and allow incoming connections for Python if asked.

## Easiest: one window for everything
After steps 0 and 1 (and copying `voice_agent/.env` for Meri, step B.1), run this from the repo root:
```bash
python meridies_launcher.py
```
- **Mode:**
  - **Spatial audio demo:** drag virtual obstacles and hear them (`audio/sim_view.py`).
  - **Sensing (no camera):** the object map plus the sound engine, from the Pi's live stream.
  - **Sensing + camera (Meri):** the same, plus the voice agent. Say "Hey Meri"; it uses the Pi camera.
  - Switching modes stops the previous one.
- **Earphones:**
  - Pick the sound output, and Meri's microphone.
  - **Swap left / right** if the earphones are worn the other way round.
  - **Test left/right** plays a low tone in the left ear, then a high tone in the right.
  - **Apply** restarts the current mode with the new settings.
- **Other buttons:** Check Pi feeds, Open camera view. The log shows the output of every program it started.
- **Settings** are remembered in `~/.meridies_launcher.json`.

The steps below do the same things by hand.

## 2. Check the Pi feeds
```bash
python sensing_pi/tools/feed_check.py --host 172.20.10.5 --raw --secs 6
```
**Expect** `5556 audio warnings: ~9/s, schema ok=True`, `5558 debug stream: ~9 msgs/s` and `5557 raw frames: ~9 msgs/s`. If any reads 0, check the hotspot and firewall, and ask the Pi side whether `sensing_pi/main.py` is running.

## A. Audio warning engine (terminal 1)
```bash
cd audio
python tone_render.py                                   # once: makes audio/sounds/ (git-ignored)
python engine.py --list-devices                         # find the wired headphones
python engine.py --endpoint tcp://172.20.10.5:5556 --device "<part of the headphone name>"
```
- **What you should hear:** a beep from the direction of each obstacle. It gets faster as the obstacle gets closer, and adds an urgent cue under about 0.5 m.
- **Silence** is normal when nothing is within about 1.5 m.

## B. Voice agent (terminal 2)
1. Get the key from the Pi into the (git-ignored) `.env`. Copy only that file:
   ```bash
   scp pi@172.20.10.5:~/meridies/gemini_voice_agent/.env voice_agent/.env
   ```
2. Start it:
   ```bash
   cd voice_agent
   python agent.py --list-devices                       # find the headset mic and speaker
   python agent.py --mic <N> --speaker <N>
   ```
3. What it uses by default (no flags needed):
   - the camera at `http://172.20.10.5:8090/snapshot.jpg`
   - the LiDAR warnings at `tcp://172.20.10.5:5556`
   - the memory layer at `http://localhost:8765`
4. Useful flags:
   - `--text`: type instead of talking (a good first test)
   - `--no-lidar`: don't use the LiDAR warnings
   - `--camera webcam`: use the laptop camera instead of the cap's
5. Things to say: "What am I looking at?", "What's around me?", "Read that sign.", "Take me to the door." ("stop" ends guidance)

**If the beeps and the agent both play on the headset:** that's intended. The agent lowers its voice while an urgent warning is sounding. On Linux, use PipeWire/PulseAudio so both can share the device.

## C. Memory layer (terminal 3, optional, start before B)
```bash
python connect/server.py            # from the repo root -> http://localhost:8765
```
- **Where it reads from:** `models_index.csv` and `.env` in the folder *above* the repo (`GEMINI_API_KEY`, plus `VEXCEL_API_KEY` for place descriptions).
- **Memories** live in `connect/data/memories/` (git-ignored, so only what's on this laptop).
- **Without it,** the agent still works and says it can't reach the memories.

## D. Viewers (optional)
```bash
python sensing_pi/tools/objects_view.py --live 172.20.10.5:5558   # top-down map of objects and warnings
python sensing_pi/tools/tof6_view.py --live 172.20.10.5:5557      # raw 8x8 grayscale tiles
```
- Camera live view: http://172.20.10.5:8090/
- Intro experience: http://172.20.10.5:8080/ (use headphones; it's spatial audio)

## What to report back to the Pi side
1. **Feeds:** the `feed_check.py` output.
2. **Audio direction:** hold a hand about 40 cm in front of each cap sensor in turn. Does the beep come from that side? Which sensor, if not?
3. **Distance:** move a hand from 1.2 m to 0.3 m. Does the beep speed up, and does the urgent cue start near 0.5 m?
4. **Voice agent:** do "What am I looking at?" and "What's around me?" answer correctly? Roughly how long does each take?
5. **Memory:** does a recall question work, if C was started?
6. **Any errors**, pasted in full.

## Known state at hand-off (2026-11-15)
- **Pi services:** all five Pi services were up and checked over the hotspot. All six ToF sensors are ranging at about 9.5 Hz.
- **Loose sensor wiring:** a sensor (S3 or S6) sometimes drops out mid-run. The stream doesn't recover it by itself; the Pi side must restart `main.py`. You'll notice the warning rate falling to about 4–5/s and one direction going quiet.
- **Low surfaces to the sides** (desks, chairs) at 1–1.5 m give "warn" objects. That's expected indoors.
