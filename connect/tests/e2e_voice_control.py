"""Blindfold voice control, end to end: a spoken command through the microphone moves the walker.

    python connect/tests/e2e_voice_control.py

Chrome's microphone is fed a WAV of a person saying "Take me to the kitchen" (made with a neural voice). With the
blindfold on, the page hears it (Chrome's recogniser, or its own speech detection + /api/hear with Gemini), parses it
and walks the route. Then other commands are checked directly. Runs its own server; your data is not touched.
"""
import asyncio
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import edge_tts
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import connect.server as srv  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="meridies-voice-"))


def spoken_wav(text: str) -> Path:
    mp3, wav = TMP / "cmd.mp3", TMP / "cmd.wav"
    asyncio.run(edge_tts.Communicate(text, "en-US-GuyNeural").save(str(mp3)))
    # 2.5 s of quiet (the page learns the room), the command, then quiet
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "2.5", "-i", "anullsrc=r=48000:cl=mono", "-i", str(mp3),
                    "-f", "lavfi", "-t", "4", "-i", "anullsrc=r=48000:cl=mono", "-filter_complex",
                    "[1:a]aresample=48000,aformat=channel_layouts=mono[c];[0:a][c][2:a]concat=n=3:v=0:a=1",
                    "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)], check=True)
    return wav


def main():
    srv.MEM, srv.AUTO_PROCESS, srv.VOICE_ON, srv.VOICE_AUTOGEN = TMP, False, False, False
    wav = spoken_wav("Take me to the kitchen.")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}%noloop", "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(permissions=["microphone"])
        ctx.add_init_script("""window.__said = [];
          speechSynthesis.speak = (u) => { window.__said.push(u.text); setTimeout(() => u.onend && u.onend(), 400); };""")
        p = ctx.new_page()
        p.on("console", lambda m: "speech recognition" in m.text and print("   ", m.text))
        p.goto(base + "/walk?building=zvKHR9DHNjJ")
        p.wait_for_function("state.ready === true", timeout=30000)
        start_room = p.evaluate("voiceControl.findTarget('kitchen') && (sweepAt(state.walker.sweep) || {}).room")
        p.click("#whereLine")
        p.keyboard.press("Space")                                     # blindfold on -> listening
        p.wait_for_selector("#blind", state="visible")
        print("blindfold on, start:", start_room, "| hearing with:", p.evaluate("voiceControl.mode()"))
        p.wait_for_function("(document.getElementById('blind').textContent || '').toLowerCase().includes('kitchen')", timeout=60000)
        print("heard:", p.inner_text("#blind").splitlines()[0], "| hearing with:", p.evaluate("voiceControl.mode()"))
        p.wait_for_function("!window.voiceRoute && /kitchen/i.test((sweepAt(state.walker.sweep) || {}).room || '')", timeout=60000)
        print("walked to:", p.evaluate("sweepAt(state.walker.sweep).room"), "| said:", [s[:70] for s in p.evaluate("window.__said")][-3:])

        # the rest of the vocabulary, as if heard
        def cmd(text, check, timeout=20000):
            p.evaluate(f"voiceControl.handle({text!r})")
            try:
                p.wait_for_function(check, timeout=timeout)
            except Exception:
                print("  FAILED", text, "| said:", [s[:60] for s in p.evaluate("window.__said")][-4:],
                      "| current:", p.evaluate("!!Speech.current"), "| parse:", p.evaluate(f"JSON.stringify(voiceControl.parse({text!r}))"))
                raise
            print(f"  {text!r:34} ok")
        h0 = p.evaluate("state.walker.heading")
        cmd("turn left", f"Math.abs(((state.walker.heading - {h0} + 540) % 360) - 180) === 90")
        cmd("where am I", "document.getElementById('whereText').textContent.length > 10")
        cmd("where's the bedroom", "window.__said.some(s => /bedroom is/i.test(s))")
        cmd("take me there", "!window.voiceRoute && /bedroom/i.test(sweepAt(state.walker.sweep).room)", 60000)
        cmd("help", "window.__said.some(s => /you can say/i.test(s))")
        cmd("blah blah", "window.__said.some(s => /say help|didn't catch/i.test(s))")
        cmd("take off the blindfold", "document.getElementById('blind').hidden")
        b.close()
    httpd.shutdown()
    print("voice control e2e: ok")


if __name__ == "__main__":
    main()
