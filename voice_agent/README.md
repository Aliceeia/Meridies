# gemini_voice_agent: the real-time voice assistant

This is a hands-free voice assistant for the cap wearer. It runs on the **laptop** in real time. You talk to it through the headset mic, and it answers in the headset using Gemini Live voice. It can:

- **See** with the cap's forward camera. Each question takes a fresh snapshot, which goes to Gemini vision.
- **Feel distance** with the cap's LiDAR (sensing_pi warnings on :5556). The LiDAR readings are fused into every answer.
- **Remember** using the memory layer (`connect/server.py` from the `memory-layer` branch). The agent also transcribes the audio memories so it knows what was said in them.

```
 headset mic ──► agent.py (laptop) ◄──► Gemini Live (gemini-3.8-live, voice in/out)
 headset ear ◄──┘   │  tools:
                    ├─ look ────────────► Pi :8090/snapshot.jpg ──► gemini-3.8-flash vision
                    ├─ start/stop_guidance (look → JSON step → spoken, every ~2 s)
                    ├─ obstacles ◄─────── Pi :5556 ZMQ (sensing_pi, SUB only)
                    └─ recall_memories ─► laptop :8765 memory layer + Gemini transcripts
```

This folder lives outside the repo on purpose: it changes nothing in `HackPrinceton_2026/`. It only reads from the memory layer and sensing_pi through their published interfaces.

## Run

**On the Pi** (camera on the front of the cap, either a CSI Camera Module or a USB webcam):
```bash
~/meridies/gemini_voice_agent/run_camera.sh       # background server, log in camera.log (add --rotate 180 if upside down)
curl http://localhost:8090/health                 # {"ok": true, "source": "picam", ...}
```
Live view: open **http://172.20.10.5:8090/** in any browser on the hotspot. The server keeps looking for a camera: a USB webcam is picked up while it runs, but a ribbon (CSI) camera is only detected after a reboot.


**On the laptop:**
```bash
scp -r pi@172.20.10.5:~/meridies/gemini_voice_agent .    # includes .env with the key
cd gemini_voice_agent && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agent.py --list-devices                 # find the headset
python agent.py --mic 3 --speaker 3            # go
```
The venv you just made on the laptop replaces the Pi's `.venv` (which you can skip copying).

Optional: start the memory layer first (`python connect/server.py` from the memory-layer branch) so that past memories are available. Without it the agent still works and says it can't reach the memories.

| Flag | Use |
|---|---|
| `--camera webcam` | Use the laptop webcam instead of the cap camera (desk testing) |
| `--camera some.jpg` | Use a fixed image (testing without any camera) |
| `--text` / `--mute` | Type instead of talking / no audio output |
| `--viewer user-a` | The wearer's `author_id` in the memory layer |
| `--no-lidar` | Don't subscribe to :5556 |
| `--save-snapshots` | Keep every photo in `snapshots/` for debugging |

Settings such as `PI_HOST`, `LIVE_MODEL`, `VISION_MODEL`, `VOICE` and `MEMORY_URL` can go in `.env`.

## Wake word: "Hey Meri"
Meri only listens after you say **"Hey Meri"**. A rising two-tone chime means she's listening, and the mic stays open for one request (up to 8 s to start speaking). Saying "Hey Meri" while she's talking cuts her off.

- Detection runs **offline on the Pi** with Vosk (`wake.py`), limited to the wake phrase plus decoys such as "hey everybody". It uses no Gemini quota, and audio isn't sent anywhere until the wake word is heard. The ~40 MB model downloads itself into `models/` on first run.
- Until the wake word is heard, and while Meri is answering, Gemini receives **silence**. This is what fixed the answers cutting off mid-sentence: the LiDAR beeps leaked into the mic, and Gemini treated them as the person interrupting.
- Flags: `--no-wake` (always listening, the old behaviour), `--listen-window 8`, and `--followup 0` (seconds to accept a follow-up without the wake word).
- Measured on the Pi, with beeps mixed into the mic throughout: 6/6 wake phrases caught, 0/8 look-alikes triggered it, and 0 false interruptions during long answers.

## Things to say (each one starts with "Hey Meri")
- "What am I looking at?" / "What's in front of me?"
- "Is there an empty chair?" / "Where's the door?" (find)
- "Read that sign." / "What does this label say?" (read)
- "Take me to the door." Guidance speaks a step every few seconds until you arrive or say "stop".
- "What did I record in the kitchen?" / "What did I say about grandma's mug?"
- "What's around me?" (LiDAR)

## Behaviour rules (consistent with the memory layer)
- Body-relative directions only: never compass words.
- LiDAR hazards are never left to the model. An urgent obstacle is attached to every `look` result. Guidance says "stop" before any vision step and then steers to the open side.
- While the hat is sounding an urgent warning, the agent's voice drops to 35%, so warnings outrank speech.
- Barge-in: when you start talking, the agent stops talking immediately.
- A soft tick plays while a tool is working, so silence never means "broken".
- Private memories stay private. The agent queries the memory layer *as the viewer*.

## Tested (on the Pi, 2026-11-14)
Tested in text mode with a fixed photo standing in for the camera (no camera was attached yet), a mock LiDAR publisher, and a copy of the memory-layer server holding one synthetic voice memo. Results:
- look / find / read: about 1.5–3 s per answer, warm.
- Memory recall: the memo was transcribed and quoted back.
- Guidance: says "stop" on an urgent obstacle, then gives a side-step.
- The headset audio path (sounddevice) has **not** been run yet. The Pi has no mic/speaker; run it on the laptop.

## Gotcha
google-genai 3.x defaults to API `v1`, where the Live websocket answers **404**. The code forces `v1beta`.
