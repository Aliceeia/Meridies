"""Browser audio for the agent: the laptop browser is the headset's mic + speaker.

    python agent.py --web 8092        # laptop: https://<pi>:8443 (self-signed cert) or http://localhost:8092 (forwarded)

Use it when the agent runs on the Pi (which has no microphone) or when you'd rather not install PortAudio.
Listens on 127.0.0.1 only: reach it through VS Code's port forwarding or `ssh -L 8092:localhost:8092 pi@...`.
Browsers only allow the mic on https or localhost, so the forwarded localhost URL is what makes it work.

Protocol on /ws:
    browser -> agent   binary: 16 kHz mono int16 mic PCM;   text: {"type":"text","text":...} typed message
    agent -> browser   binary: 24 kHz mono int16 speech PCM; text: {"type":"clear"} | {"type":"you"|"bot"|"tool"|"status","text":...}
"""
from __future__ import annotations

import asyncio
import json
import struct
import time

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

import config

OUT_RATE = 24000


class WebBridge:
    """Same interface as agent.Speaker (play/clear/busy/tone/gain), plus a mic queue fed by the browser."""

    def __init__(self, port: int, mic_q: asyncio.Queue, text_q: asyncio.Queue):
        self.port, self.mic_q, self.text_q = port, mic_q, text_q
        self.clients: set = set()
        self.gain = 1.0
        self.playing_until = 0.0

    # ---- Speaker interface
    def play(self, pcm: bytes):
        if self.gain != 1.0:
            k = len(pcm) // 2
            pcm = struct.pack(f"<{k}h", *(int(s * self.gain) for s in struct.unpack(f"<{k}h", pcm[:k * 2])))
        self.playing_until = max(self.playing_until, time.time()) + len(pcm) / (2 * OUT_RATE)
        self._broadcast(pcm)

    def clear(self):
        self.playing_until = 0.0
        self.send("clear")

    @property
    def busy(self) -> bool:
        return time.time() < self.playing_until

    def tone(self, freq=740, dur=0.05, vol=0.08):
        import math
        n, fade = int(OUT_RATE * dur), int(OUT_RATE * 0.008)
        self.play(struct.pack(f"<{n}h", *(int(32767 * vol * min(1, i / fade, (n - i) / fade)
                                             * math.sin(2 * math.pi * freq * i / OUT_RATE)) for i in range(n))))

    # ---- messages
    def send(self, kind: str, text: str = ""):
        self._broadcast(json.dumps({"type": kind, "text": text}))

    def _broadcast(self, msg):
        for ws in list(self.clients):
            asyncio.get_running_loop().create_task(self._safe_send(ws, msg))

    async def _safe_send(self, ws, msg):
        try:
            await ws.send(msg)
        except Exception:
            self.clients.discard(ws)

    # ---- server
    async def _handler(self, ws):
        self.clients.add(ws)
        print(f"[web] browser connected ({len(self.clients)})")
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    self.mic_q.put_nowait(msg)
                else:
                    m = json.loads(msg)
                    if m.get("type") == "text" and m.get("text", "").strip():
                        self.text_q.put_nowait(m["text"].strip())
        except Exception:
            pass
        finally:
            self.clients.discard(ws)
            print("[web] browser disconnected")

    async def _http(self, connection, request):
        if request.path.startswith("/ws"):
            return None                                   # websocket upgrade
        if request.path.startswith("/cam.jpg"):           # camera proxied through here: an https page can't load http
            try:
                body = await asyncio.to_thread(_fetch, config.CAMERA_URL)
                return _resp(body, "image/jpeg")
            except Exception:
                return Response(503, "No camera", Headers([("Content-Length", "0")]), b"")
        return _resp(PAGE.encode(), "text/html; charset=utf-8")

    async def start(self):
        await serve(self._handler, "127.0.0.1", self.port, process_request=self._http, max_size=2**20)
        print(f"[web] http://localhost:{self.port} (on the Pi, or via VS Code port forwarding)")
        if CERT.exists():
            import socket, ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(CERT, CERT.with_name("key.pem"))
            await serve(self._handler, "0.0.0.0", HTTPS_PORT, process_request=self._http, ssl=ctx, max_size=2**20)
            print(f"[web] on the laptop open https://{config.PI_HOST}:{HTTPS_PORT}  (accept the certificate warning once)")


CERT = config.HERE / "certs" / "cert.pem"     # self-signed: openssl req -x509 ... (see README)
HTTPS_PORT = 8443


def _fetch(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.read()


def _resp(body: bytes, ctype: str) -> Response:
    return Response(200, "OK", Headers([("Content-Type", ctype), ("Content-Length", str(len(body))),
                                        ("Cache-Control", "no-store")]), body)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Meridies voice</title>
<style>
:root{--bg:#f6f4f1;--fg:#1d1b19;--mut:#6d665f;--card:#fff;--line:#e3ded8;--acc:#c2410c;--you:#1d4ed8}
@media (prefers-color-scheme:dark){:root{--bg:#141210;--fg:#ece7e1;--mut:#9a9189;--card:#1e1b18;--line:#332e29;--acc:#fb923c;--you:#93c5fd}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:16px;display:grid;gap:16px;grid-template-columns:1fr 1fr}
@media (max-width:760px){main{grid-template-columns:1fr}}
h1{font-size:18px;margin:0}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
img{width:100%;border-radius:6px;background:#000;min-height:120px}
button{font:inherit;padding:12px 18px;border-radius:999px;border:0;background:var(--acc);color:#fff;cursor:pointer;font-weight:600}
button.off{background:var(--mut)}#status{color:var(--mut);font-size:13px;margin-left:8px}
#log{height:52vh;overflow:auto;display:flex;flex-direction:column;gap:6px;font-size:14px}
.you{color:var(--you)}.tool{color:var(--mut);font-size:12px}.bot{}
form{display:flex;gap:8px;margin-top:10px}input{flex:1;font:inherit;padding:10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
#meter{height:6px;border-radius:3px;background:var(--line);margin-top:10px;overflow:hidden}#lvl{height:100%;width:0;background:var(--acc)}
</style></head><body><main>
<section class="card"><h1>Cap camera</h1><p style="color:var(--mut);margin:4px 0 10px;font-size:13px">What the agent sees when you ask</p>
<img id="cam" alt="live cap camera feed"></section>
<section class="card"><div style="display:flex;align-items:center"><button id="go">Start talking</button><span id="status">not connected</span></div>
<div id="meter"><div id="lvl"></div></div>
<p style="color:var(--mut);font-size:13px">Try: "What am I looking at?", "Read that sign", "Guide me to the door", "What did I record here?"</p>
<div id="log"></div>
<form id="f"><input id="t" placeholder="…or type a question" autocomplete="off"><button>Send</button></form></section>
</main><script>
const $=id=>document.getElementById(id), log=(cls,t)=>{const d=document.createElement('div');d.className=cls;d.textContent=(cls==='you'?'You: ':cls==='bot'?'Agent: ':'')+t;$('log').append(d);$('log').scrollTop=1e9};
let ws, ctx, nextT=0, sources=[], micNode, stream;
const WORKLET=`class Mic extends AudioWorkletProcessor{constructor(){super();this.r=sampleRate/16000;this.p=0;this.buf=[]}
process(i){const x=i[0][0];if(!x)return true;let s=0;for(let k=0;k<x.length;k++)s+=x[k]*x[k];
for(;this.p<x.length;this.p+=this.r){const v=Math.max(-1,Math.min(1,x[Math.floor(this.p)]));this.buf.push(v*32767)}
this.p-=x.length;if(this.buf.length>=640){this.port.postMessage({pcm:Int16Array.from(this.buf).buffer,lvl:Math.sqrt(s/x.length)},);this.buf=[]}return true}}
registerProcessor('mic',Mic)`;
function connect(){
  ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);ws.binaryType='arraybuffer';
  ws.onopen=()=>$('status').textContent='connected: listening';
  ws.onclose=()=>{$('status').textContent='disconnected, retrying…';setTimeout(connect,1500)};
  ws.onmessage=e=>{
    if(typeof e.data!=='string'){play(new Int16Array(e.data));return}
    const m=JSON.parse(e.data);
    if(m.type==='clear'){sources.forEach(s=>{try{s.stop()}catch(_){}});sources=[];nextT=0}
    else if(m.type==='status')$('status').textContent=m.text; else log(m.type,m.text)};
}
function play(pcm){
  if(!ctx)return;const b=ctx.createBuffer(1,pcm.length,24000),c=b.getChannelData(0);
  for(let i=0;i<pcm.length;i++)c[i]=pcm[i]/32768;
  const s=ctx.createBufferSource();s.buffer=b;s.connect(ctx.destination);
  nextT=Math.max(nextT,ctx.currentTime+0.05);s.start(nextT);nextT+=b.duration;
  sources.push(s);s.onended=()=>{sources=sources.filter(x=>x!==s)};
}
$('go').onclick=async()=>{
  if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;micNode&&micNode.disconnect();$('go').textContent='Start talking';$('go').classList.remove('off');$('status').textContent='mic off';return}
  ctx=ctx||new AudioContext();await ctx.resume();
  stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET],{type:'text/javascript'})));
  micNode=new AudioWorkletNode(ctx,'mic');ctx.createMediaStreamSource(stream).connect(micNode);
  micNode.port.onmessage=e=>{if(ws&&ws.readyState===1)ws.send(e.data.pcm);$('lvl').style.width=Math.min(100,e.data.lvl*400)+'%'};
  $('go').textContent='Mute mic';$('go').classList.add('off');$('status').textContent='listening: just talk';
};
$('f').onsubmit=e=>{e.preventDefault();const t=$('t').value.trim();if(!t||!ws||ws.readyState!==1)return;
  ctx=ctx||new AudioContext();ctx.resume();ws.send(JSON.stringify({type:'text',text:t}));log('you',t);$('t').value=''};
connect();
(function cam(){const i=new Image();i.onload=()=>{$('cam').src=i.src;setTimeout(cam,250)};i.onerror=()=>setTimeout(cam,1500);i.src='/cam.jpg?'+Date.now()})();
</script></body></html>"""
