# Laptop-side agent: view the ToF streams and hear the obstacle warnings

**You are:** an agent on the team's laptop.
**The Pi** (`172.20.10.5`, phone hotspot network) is already running two processes, and you only **connect** to them:

| Port on the Pi | What | Format | Producer on the Pi |
|---|---|---|---|
| **5557** | raw sensor frames, about 9/s | TCP, one JSON line per frame: `{"t","res","d":[6×64 mm],"s":[6×64 status]}` | `sensing_pi/tests/multi6/tof6_record.py --serve 5557 --allow-missing` |
| **5556** | obstacle warnings, about 8/s | ZMQ PUB, one JSON **list** per message (≤3 entries, nearest first) | `sensing_pi/run_objects.py --rig config/cap_mounts.json --source tcp:localhost:5557` |
| **5558** | object debug stream | TCP JSON lines `{"t","objects","warnings","points"}` | same `run_objects.py` |

The sensors are in the **cap layout** (`sensing_pi/docs/cap_layout.md`):
- S1/S2/S3: brim, at −40° / 0° / +40°
- S4/S6: sides, at −85° / +85°
- S5: under the brim, tilted down

**S6 is currently not detected**: it's a wiring fault on the Pi side, and its zones arrive as invalid. Don't try to fix that from the laptop.

## Rules
- **Don't publish or bind** on 5556, 5557 or 5558, so **don't run `audio/mock_warnings.py`** while the Pi is publishing. The audio engine only connects (ZMQ SUB), which is fine.
- **Don't edit `sensing_pi/`.** It's owned by the Pi-side agent; report problems instead. The one exception: your local row-flip fix in `sensing_pi/tests/multi6/tof6_common.py` / `tof6_view.py` may conflict with the Pi's version. Keep the Pi's version of `tof6_common.py` (pins now come from `sensing_pi/sensors/pins.py`) and re-apply your display fix on top.
- `audio/` changes follow `sensing_pi/docs/audio_integration_guide.md`. They're optional and not needed for this test.

## 0. Get the code
1. **Preferred:** `git pull` on `main`. The Pi's commits may not be pushed yet; check that `sensing_pi/tools/objects_view.py` and `sensing_pi/tools/feed_check.py` exist after pulling.
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
- Ask the Pi side whether `tof6_record.py` and `run_objects.py` are still running.

## 3. Raw view: grayscale 8×8 tiles
```bash
python tests/multi6/tof6_view.py --live 172.20.10.5:5557
```
You'll see six tiles: near = white, far = black, invalid = dark red. S6 shows all dark red.

The tile arrangement is the old bench 2×3 grid. It's only a display; on the cap the sensors point in the directions listed above. If a tile looks upside-down, try `--tile flipy`. The Pi now uses `zone_flip_v: true`, so expect to need `--tile flipy` or your own row-flip fix.

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
2. **Direction:** wearer holds a hand about 40 cm in front of S2 (brim centre), then S1 (front-left), S3 (front-right) and S4 (left side).
   - The beep should come from ahead, front-left, front-right and left respectively.
   - In the object view, the object should appear at az ≈ 0°, −40°, +40° and −85°.
   - If left and right are swapped, the Pi side needs `zone_flip_h` or a mount fix; report which sensor.
3. **Distance:** move the hand from 1.2 m to 0.3 m in front. The beep rate should rise, and below about 0.5 m (or when approaching quickly) the urgent chirp should play.
4. **One voice per object:** a single hand should give a single steady voice, not several flickering ones. Report the ids shown in the object view.
5. **Silence:** with nothing within about 1.5 m and nothing approaching, the sound should stop within about 0.5 s.
6. **Walls:** stand about 0.4 m beside a wall, parallel to it. The object view should show `wall_parallel` and the level should be `"wall"`. It plays as a normal beep until the audio side adds a quiet wall sound.
7. **Latency (optional):** film a hand and the sound in slow motion, and count the frames. The target is under 200 ms.

**Known state at hand-off:**
- With the cap sitting still, the pipeline reports three "urgent, narrow" objects 10–18 cm away and slightly above the brim (ids O2/O8/O9). Something is touching-close to the sensors: cables, the cap itself or a surface. Expect constant urgent beeps until the cap is worn in open space. Report whether they disappear then.
- S6 (right side) is not detected.
