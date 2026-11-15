# Laptop-side agent: view the ToF streams and hear the obstacle warnings

**You are:** an agent on the team's laptop.
**The Pi** (`172.20.10.5`, phone hotspot network) runs `sensing_pi/main.py`, which starts two processes. You only **connect** to them:

| Port on the Pi | What | Format | Producer on the Pi (started by `main.py`) |
|---|---|---|---|
| **5557** | raw sensor frames, about 9/s | TCP, one JSON line per frame: `{"t","res","d":[6×64 mm],"s":[6×64 status],"sigma","signal"}` | `python -m sensors.record --serve 5557 --allow-missing` |
| **5556** | obstacle warnings, about 8/s | ZMQ PUB, one JSON **list** per message (≤3 entries, nearest first) | `python -m objects.run --rig config/cap_mounts.json --source tcp:localhost:5557` |
| **5558** | object debug stream | TCP JSON lines `{"t","objects","warnings","points"}` | same `objects.run` |

The sensors are in the **cap layout**: where each sensor sits and points is in `sensing_pi/config/sensor_layout.py` (`CAP` table; angle 0 = front, counter-clockwise, 90 = left). Background: `sensing_pi/docs/cap_layout.md`.

If a sensor isn't detected at start (loose wiring on the Pi side), its zones arrive as invalid. Don't try to fix that from the laptop.

## Rules
- **Don't publish or bind** on 5556, 5557 or 5558, so **don't run `audio/mock_warnings.py`** while the Pi is publishing. The audio engine only connects (ZMQ SUB), which is fine.
- **Don't edit `sensing_pi/`.** It's owned by the Pi-side agent; report problems instead.
- `audio/` changes follow `sensing_pi/docs/audio_integration_guide.md`. They're optional and not needed for this test.

## 0. Get the code
1. **Preferred:** `git pull` on the **`sound-slab`** branch. Check that `sensing_pi/main.py`, `sensing_pi/tools/objects_view.py` and `sensing_pi/tools/feed_check.py` exist after pulling.
2. **If they're missing,** copy the folder straight from the Pi (password or key for user `pi`):
   ```bash
   scp -r pi@172.20.10.5:~/meridies/HackPrinceton_2026/sensing_pi ./sensing_pi_from_pi
   ```
   Then run the tools from `sensing_pi_from_pi/`. Don't overwrite the repo's `sensing_pi/`.

## 1. Python environment (once)
```bash
python3 -m venv ~/tof-venv && source ~/tof-venv/bin/activate
pip install numpy matplotlib pyzmq
```
For the audio engine, also install `audio/requirements.txt` (slab, soundfile, sounddevice, pyzmq, numpy). On Linux, `sounddevice` needs PortAudio (`sudo apt install libportaudio2`). On macOS it's bundled.

## 2. Check the network and the feeds (run from `sensing_pi/`)
```bash
ping -c 3 172.20.10.5
python tools/feed_check.py --host 172.20.10.5 --raw --secs 8
```
**Expected:**
- `5556 audio warnings: ~8/s, schema ok=True`
- `5558 debug stream: ~8 msgs/s`
- `5557 raw frames: ~9 msgs/s`

**If something is 0:**
- Confirm the laptop is on the same hotspot.
- On macOS, allow incoming connections for Python if prompted.
- Ask the Pi side whether `main.py` is still running.

## 3. Raw view: grayscale 8×8 tiles
```bash
python tools/tof6_view.py --live 172.20.10.5:5557
```
You'll see six tiles: near = white, far = black, invalid = dark red. A sensor that wasn't detected shows all dark red.

The tile arrangement is the old bench 2×3 grid. It's only a display; on the cap the sensors point in the directions in `config/sensor_layout.py`. The top row is turned by default (`--top-tile rot180`); if a tile still looks upside-down, try `--tile flipy` or `--top-tile none`.

## 4. Object view: top-down map
```bash
python tools/objects_view.py --live 172.20.10.5:5558
```
- The head is at the origin, with forward pointing up.
- Grey dots are scan points. Coloured arcs are objects: red narrow, orange wide, purple facing wall, blue parallel wall.
- Bold labels are the up to 3 objects being sent to the audio engine.

## 5. Sound
From the repo's `audio/` folder:
```bash
python hrtf_render.py                                   # once; downloads KEMAR, writes audio/sounds/
python engine.py --list-devices                         # pick the wired headphones if needed
python engine.py --endpoint tcp://172.20.10.5:5556
```
Use **wired headphones**.

## 6. What to test and report back
For each item, report OK or not OK, plus what you saw:
1. **Feeds:** paste the `feed_check.py` output.
2. **Direction:** wearer holds a hand about 40 cm in front of each sensor in turn.
   - The beep should come from that sensor's direction, and in the object view the object should appear near its angle. The angles are in `config/sensor_layout.py`; the object view shows az with + = right, so a layout angle of 75° (left) appears at about −75°.
   - If left and right are swapped, the Pi side needs `zone_flip_h` or a mount fix; report which sensor.
3. **Distance:** move the hand from 1.2 m to 0.3 m in front. The beep rate should rise, and below about 0.5 m (or when approaching quickly) the urgent chirp should play.
4. **One voice per object:** a single hand should give a single steady voice, not several flickering ones. Report the ids shown in the object view.
5. **Silence:** with nothing within about 1.5 m and nothing approaching, the sound should stop within about 0.5 s.
6. **Walls:** stand about 0.4 m beside a wall, parallel to it. The object view should show `wall_parallel` and the level should be `"wall"`. It plays as a normal beep until the audio side adds a quiet wall sound.
7. **Latency (optional):** film a hand and the sound in slow motion, and count the frames. The target is under 200 ms.

**Known state at hand-off (2026-11-15):**
- Noise cleanup is on: the sigma/signal filter, a self-mask learned standing in open space (it masks nothing for the cap as worn), and the body zone (shoulders, torso, arms).
- Sensor wiring is still loose: S2, S3 and S6 have each dropped out at some point. A sensor missing at start shows as all dark red in the raw view.
- Low surfaces 1–1.5 m to the sides (desks, chairs) give "warn" objects that often flicker or are labelled "narrow" rather than "wall"; that is a known tuning item.
