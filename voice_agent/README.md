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

**From git:** the key is not committed. Create `voice_agent/.env` containing `GEMINI_API_KEY=...` (ask the team), or copy the ready-made folder from the Pi at `~/meridies/gemini_voice_agent/`.

**On the Pi** (camera on the front of the cap, either a CSI Camera Module or a USB webcam):
```bash
cd ~/meridies/gemini_voice_agent && .venv/bin/python camera_server.py           # add --rotate 180 if mounted upside down
curl -o /tmp/t.jpg http://localhost:8090/snapshot.jpg                         # sanity check
```

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

## Things to say
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
