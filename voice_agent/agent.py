"""Meridies real-time voice agent (runs on the LAPTOP; headset mic in, headset audio out).

    python agent.py                               # camera + LiDAR from the Pi, memories from localhost:8765
    python agent.py --camera webcam               # laptop webcam instead of the cap camera (testing)
    python agent.py --text                        # type instead of talk (no mic), still speaks back
    python agent.py --text --mute                 # type and read, no audio at all
    python agent.py --list-devices                # pick --mic / --speaker for the headset

Say things like:
    "What am I looking at?"          -> look (camera + Gemini vision, LiDAR distances fused in)
    "Is there a chair nearby?"       -> look mode=find
    "Read that sign"                 -> look mode=read
    "Guide me to the door"           -> start_guidance: spoken instruction every few seconds until arrived/stop
    "What did I record here before?" -> recall_memories (memory layer + transcripts)
    "What's around me?"              -> obstacles (the hat's LiDAR)
"""
from __future__ import annotations

import argparse
import asyncio
import math
import struct
import sys
import time
import traceback

from google import genai
from google.genai import types

import config
import memory
import sensors
import vision
from sensors import direction

IN_RATE, OUT_RATE = 16000, 24000

SYSTEM = """You are Meridies, a calm, warm voice assistant for a blind or low-vision person, speaking through \
their headset in real time. They wear a cap with LiDAR distance sensors (which already beep for obstacles) and \
a forward-facing camera. You are their eyes and their memory.

Rules:
- Keep every answer short and spoken-friendly: one to three sentences. No lists, no markdown.
- Body-relative directions only (ahead, slightly left, on your right, behind you). Never compass directions.
- For anything about what is in front of them or around them, ALWAYS call the `look` tool, even if you looked \
a moment ago: they move and turn their head, so earlier answers go stale within seconds. Each call takes a fresh photo. Use mode "find" when they are looking for something, "read" for text.
- For "take me / guide me / help me get to X", call `start_guidance`. It speaks instructions by itself. \
Call `stop_guidance` when they say stop, they've arrived, or they ask for something else.
- Messages starting with [GUIDANCE] come from the guidance system, not the person: say the sentence in them \
naturally and briefly, nothing else.
- For questions about the past, places they've been, or what they or others recorded, call `recall_memories`.
- Safety first: if a tool result mentions an urgent hazard, say that before anything else. You can't \
guarantee safety; they should keep using their cane or dog.
- Before a tool that takes a moment, you may say a two or three word filler like "Let me look."

Their saved memories (from the memory layer) at session start:
{memories}"""

TOOLS = [types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="look", description="Take a photo with the cap camera and answer a question about it.",
        parameters=types.Schema(type="OBJECT", required=["question"], properties={
            "question": types.Schema(type="STRING", description="What the person wants to know, in their words."),
            "mode": types.Schema(type="STRING", enum=["describe", "find", "read"],
                                 description="describe (default), find a specific thing, or read text."),
        })),
    types.FunctionDeclaration(
        name="start_guidance", description="Start step-by-step walking guidance toward a goal using the camera "
                                           "and LiDAR. Instructions are spoken automatically every few seconds.",
        parameters=types.Schema(type="OBJECT", required=["goal"], properties={
            "goal": types.Schema(type="STRING", description="Where they want to go, e.g. 'the door', 'an empty chair'."),
        })),
    types.FunctionDeclaration(name="stop_guidance", description="Stop walking guidance."),
    types.FunctionDeclaration(name="obstacles", description="What the cap's LiDAR sensors detect around them right now."),
    types.FunctionDeclaration(
        name="recall_memories", description="Search the person's saved audio memories (what was said, where, when).",
        parameters=types.Schema(type="OBJECT", properties={
            "query": types.Schema(type="STRING", description="What to look for, e.g. 'kitchen', 'mom', 'last week'."),
        })),
])]


# ---------------------------------------------------------------- audio
class Speaker:
    """24 kHz int16 playback with barge-in (clear) and ducking while the hat is warning."""

    def __init__(self, device=None, enabled=True):
        self.buf = bytearray()
        self.gain = 1.0
        self.stream = None
        if enabled:
            import sounddevice as sd
            self.stream = sd.RawOutputStream(samplerate=OUT_RATE, channels=1, dtype="int16", device=device,
                                             blocksize=1200, callback=self._cb)
            self.stream.start()

    def _cb(self, out, frames, _t, _s):
        n = frames * 2
        chunk, self.buf[:n] = bytes(self.buf[:n]), b""
        if self.gain != 1.0 and chunk:
            k = len(chunk) // 2
            chunk = struct.pack(f"<{k}h", *(int(s * self.gain) for s in struct.unpack(f"<{k}h", chunk)))
        out[:] = chunk + b"\0" * (n - len(chunk))

    def play(self, pcm: bytes):
        if self.stream:
            self.buf += pcm

    def clear(self):
        self.buf.clear()

    @property
    def busy(self) -> bool:
        return len(self.buf) > 0

    def tone(self, freq=740, dur=0.05, vol=0.08):
        n = int(OUT_RATE * dur)
        fade = int(OUT_RATE * 0.008)   # 8 ms fades, same as the memory layer
        env = lambda i: min(1, i / fade, (n - i) / fade)
        self.play(struct.pack(f"<{n}h", *(int(32767 * vol * env(i) * math.sin(2 * math.pi * freq * i / OUT_RATE))
                                         for i in range(n))))


def mic_stream(loop, q: asyncio.Queue, device=None):
    import sounddevice as sd

    def cb(data, *_):
        loop.call_soon_threadsafe(q.put_nowait, bytes(data))
    s = sd.RawInputStream(samplerate=IN_RATE, channels=1, dtype="int16", blocksize=512, device=device, callback=cb)
    s.start()
    return s


# ---------------------------------------------------------------- agent
class Agent:
    def __init__(self, a):
        self.a = a
        self.cam = sensors.Camera(a.camera)
        self.obs = sensors.Obstacles(None if a.no_lidar else a.lidar)
        self.mem = memory.Memories(a.memory_url, a.viewer)
        self.spk = Speaker(a.speaker, enabled=not a.mute)
        self.session = None
        self.guide_task: asyncio.Task | None = None
        self.resume_handle = None
        self.obs.listeners.append(self._duck)

    def _duck(self, obs):
        # an obstacle warning from the hat outranks the voice: drop to 35% while anything is urgent
        self.spk.gain = 0.35 if any(o.get("level") == "urgent" for o in obs) else 1.0

    async def _thinking(self):
        # a soft heartbeat so silence never means "broken" (same idea as connect/frontend/voice.js)
        try:
            await asyncio.sleep(1.2)
            while True:
                if not self.spk.busy:
                    self.spk.tone(740, 0.05, 0.05)
                await asyncio.sleep(1.6)
        except asyncio.CancelledError:
            pass

    # ---- tools
    async def tool(self, name: str, args: dict) -> dict:
        if name == "look":
            mode = args.get("mode") or "describe"
            jpeg = await asyncio.to_thread(self.cam.snapshot)
            if self.a.save_snapshots:
                d = config.HERE / "snapshots"; d.mkdir(exist_ok=True)
                (d / f"{int(time.time())}_{mode}.jpg").write_bytes(jpeg)
            lidar = self.obs.summary()
            out = {"answer": await vision.ask(jpeg, mode, args.get("question", ""), lidar)}
            if "urgent" in lidar:   # deterministic: never let a close hazard depend on the vision model mentioning it
                out["urgent_hazard"] = lidar + " Say this first."
            return out
        if name == "obstacles":
            return {"answer": self.obs.summary()}
        if name == "recall_memories":
            await asyncio.to_thread(self.mem.refresh)
            return {"memories": self.mem.as_context(args.get("query", ""))}
        if name == "start_guidance":
            if not (args.get("goal") or "").strip():
                return {"error": "no goal given; ask them where they want to go"}
            self.stop_guidance()
            self.guide_task = asyncio.create_task(self._guide(args.get("goal", "")))
            return {"status": "guidance started; instructions will follow automatically. Tell them briefly."}
        if name == "stop_guidance":
            self.stop_guidance()
            return {"status": "guidance stopped"}
        return {"error": f"unknown tool {name}"}

    def stop_guidance(self):
        if self.guide_task:
            self.guide_task.cancel()
            self.guide_task = None

    async def _guide(self, goal: str):
        """Look -> decide -> speak, every few seconds. LiDAR 'urgent' overrides the vision model with a stop."""
        last_action, last_t, last_stop, t_end = None, 0.0, 0.0, time.time() + 180
        try:
            while time.time() < t_end:
                await asyncio.sleep(0.5)
                while self.spk.busy:                     # never talk over the previous instruction
                    await asyncio.sleep(0.2)
                urgent = [o for o in self.obs.current() if o.get("level") == "urgent"]
                if urgent and time.time() - last_stop > 8:
                    # say stop once; the next step (vision with LiDAR as a hard constraint) then steers around it
                    o = urgent[0]
                    await self._inject(f"Stop. Something very close {direction(o['az'])}.")
                    last_stop, last_action = time.time(), "stop"
                    await asyncio.sleep(1.5)
                    continue
                jpeg = await asyncio.to_thread(self.cam.snapshot)
                step = await vision.navigate(jpeg, goal, self.obs.summary())
                print(f"[guide] {step}")
                say = step.get("say") or ""
                if step.get("action") == "arrived":
                    await self._inject(say or f"You've reached {goal}.")
                    return
                # speak when the instruction changes; repeat an unchanged one only every ~6 s as reassurance
                if say and (step.get("action") != last_action or time.time() - last_t > 6):
                    await self._inject(say)
                    last_action, last_t = step.get("action"), time.time()
                await asyncio.sleep(self.a.guide_interval)
            await self._inject("I've stopped guiding after three minutes. Ask again if you need me.")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            traceback.print_exc()
            await self._inject(f"Guidance stopped because something went wrong: {str(e)[:80]}")

    async def _inject(self, sentence: str):
        if self.session:
            await self.session.send_realtime_input(text=f"[GUIDANCE] {sentence}")

    async def _warm_up(self):
        """The first vision call is ~10 s cold; pay that at startup, not on the first question."""
        try:
            jpeg = await asyncio.to_thread(self.cam.snapshot)
            await vision.ask(jpeg, "describe", "One word: is the image dark or lit?", self.obs.summary())
            print("[vision] camera + vision ready")
        except Exception as e:
            print(f"[vision] warm-up failed (camera at {self.a.camera}?): {e}")

    # ---- session
    def live_config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM.format(memories=self.mem.as_context()),
            tools=TOOLS,
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.a.voice))),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            session_resumption=types.SessionResumptionConfig(handle=self.resume_handle),
        )

    async def run(self):
        await asyncio.to_thread(self.mem.refresh)
        if self.mem.ok:
            print(f"[memory] {len(self.mem.items)} memories visible to {self.a.viewer}")
            asyncio.create_task(self.mem.transcribe_missing())
        asyncio.create_task(self._warm_up())
        # google-genai 3.x defaults to api_version v1, where the Live websocket answers 404
        client = genai.Client(api_key=config.api_key(), http_options=types.HttpOptions(api_version="v1beta"))
        loop = asyncio.get_running_loop()
        mic_q: asyncio.Queue = asyncio.Queue()
        mic = None if self.a.text else mic_stream(loop, mic_q, self.a.mic)
        while True:   # reconnect (with resumption) when the server rotates the session
            try:
                async with client.aio.live.connect(model=self.a.model, config=self.live_config()) as s:
                    self.session = s
                    print(f"[live] connected to {self.a.model}. " + ("Type and press Enter." if self.a.text
                                                                    else "Talk into the headset. Ctrl+C to quit."))
                    sender = self._send_text(s) if self.a.text else self._send_mic(s, mic_q)
                    done, pending = await asyncio.wait([asyncio.create_task(sender),
                                                        asyncio.create_task(self._receive(s))],
                                                       return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    for t in done:
                        if t.exception():
                            raise t.exception()
                        if t.result() == "quit":
                            return
            except (KeyboardInterrupt, asyncio.CancelledError):
                return
            except Exception as e:
                print(f"[live] session ended: {e!r}; reconnecting in 1 s")
                await asyncio.sleep(1)
            finally:
                self.session = None
        if mic:
            mic.stop()

    async def _send_mic(self, s, q):
        while True:
            pcm = await q.get()
            await s.send_realtime_input(audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={IN_RATE}"))

    async def _send_text(self, s):
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line or line.strip() in ("quit", "exit"):
                return "quit"
            if line.strip():
                await s.send_realtime_input(text=line.strip())

    async def _receive(self, s):
        heard, said = [], []
        while True:
            async for msg in s.receive():
                if msg.session_resumption_update and msg.session_resumption_update.new_handle:
                    self.resume_handle = msg.session_resumption_update.new_handle
                if msg.go_away:
                    print("[live] server asked to reconnect")
                    return "reconnect"
                if msg.tool_call:
                    asyncio.create_task(self._run_tools(s, msg.tool_call))
                sc = msg.server_content
                if not sc:
                    continue
                if sc.interrupted:                       # the person started talking: stop speaking now
                    self.spk.clear()
                if sc.input_transcription and sc.input_transcription.text:
                    heard.append(sc.input_transcription.text)
                if sc.output_transcription and sc.output_transcription.text:
                    said.append(sc.output_transcription.text)
                if sc.model_turn:
                    for p in sc.model_turn.parts or []:
                        if p.inline_data and p.inline_data.data:
                            self.spk.play(p.inline_data.data)
                if sc.turn_complete:
                    if heard:
                        print(f"you : {''.join(heard).strip()}")
                    if said:
                        print(f"bot : {''.join(said).strip()}")
                    heard, said = [], []

    async def _run_tools(self, s, call):
        tick = asyncio.create_task(self._thinking())
        responses = []
        try:
            for fc in call.function_calls:
                t0 = time.time()
                try:
                    result = await self.tool(fc.name, dict(fc.args or {}))
                except Exception as e:
                    traceback.print_exc()
                    result = {"error": f"{type(e).__name__}: {str(e)[:200]}. Tell the person briefly."}
                print(f"[tool] {fc.name}({dict(fc.args or {})}) {time.time() - t0:.1f}s -> {str(result)[:160]}")
                responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result))
        finally:
            tick.cancel()
        await s.send_tool_response(function_responses=responses)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", default=config.CAMERA_URL, help="Pi snapshot URL | webcam[:N] | image.jpg")
    ap.add_argument("--lidar", default=config.OBSTACLE_ENDPOINT, help="sensing_pi warning feed (ZMQ SUB)")
    ap.add_argument("--no-lidar", action="store_true")
    ap.add_argument("--memory-url", default=config.MEMORY_URL)
    ap.add_argument("--viewer", default=config.MEMORY_VIEWER, help="memory layer author_id of the wearer")
    ap.add_argument("--model", default=config.LIVE_MODEL)
    ap.add_argument("--voice", default=config.VOICE)
    ap.add_argument("--mic", help="input device index/name (see --list-devices)")
    ap.add_argument("--speaker", help="output device index/name")
    ap.add_argument("--text", action="store_true", help="type instead of using the mic")
    ap.add_argument("--mute", action="store_true", help="no audio output (print transcripts only)")
    ap.add_argument("--guide-interval", type=float, default=2.0, help="seconds between guidance steps")
    ap.add_argument("--save-snapshots", action="store_true", help="keep every photo in snapshots/")
    ap.add_argument("--list-devices", action="store_true")
    a = ap.parse_args()
    for k in ("mic", "speaker"):
        v = getattr(a, k)
        if v is not None and v.isdigit():
            setattr(a, k, int(v))
    if a.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return
    try:
        asyncio.run(Agent(a).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
