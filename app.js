(() => {
'use strict';

/* Reversed (dark-stage) version of the Meridies logo, embedded so the page is self-contained. */
const LOGO_SRC = './assets/logo.png';

/* Saved memories. To use real recordings, set audio to a data URI,
   e.g. 'data:audio/mpeg;base64,…'. The text stays on screen as the caption. */
const MEMS = [
  {label:'Kitchen table', when:'saved Tuesday at 08:14', x:-1.2, y:0.98, z:-0.92,
   text:'Your reading glasses are on the near left corner of the table, right beside the fruit bowl.', audio:null},
  {label:'Front door', when:'saved Monday at 19:02', x:2.9, y:1.55, z:-3.72,
   text:'Front door. Your keys hang on the hook just to the right. The step outside is a little uneven.', audio:null},
  {label:'Bookshelf', when:'saved Sunday at 16:40', x:-4.4, y:1.3, z:-2.25,
   text:'Third shelf, far end: the photo album from Lisbon. Dad’s favorite.', audio:null},
];

/* ---------------- helpers ---------------- */
const $ = s => document.querySelector(s);
const body = document.body;
const clamp = (v,a,b) => v<a?a:v>b?b:v;
const lerp = (a,b,t) => a+(b-a)*t;
const sstep = (a,b,v) => { const t=clamp((v-a)/(b-a),0,1); return t*t*(3-2*t); };
const easeIO = t => t<.5 ? 4*t*t*t : 1-Math.pow(-2*t+2,3)/2;
const easeOut = t => 1-Math.pow(1-t,3);
const TAU = Math.PI*2;
const angDiff = (a,b) => { let d=(b-a)%TAU; if(d>Math.PI) d-=TAU; if(d<-Math.PI) d+=TAU; return d; };
const rnd = (a=1) => (Math.random()*2-1)*a;
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const coarse = matchMedia('(pointer: coarse)').matches;

/* ---------------- WebGL setup ---------------- */
const canvas = $('#gl');
const gl = canvas.getContext('webgl',{antialias:false,alpha:false,depth:false,stencil:false,powerPreference:'high-performance'});
function noGL(){ $('#nogl').hidden=false; const g=$('#gate'); if(g) g.remove(); }
if(!gl){ noGL(); return; }

const VS_ROOM = `
precision highp float;
attribute vec3 aPos; attribute vec3 aCol; attribute float aBase; attribute float aSeed;
uniform mat4 uView; uniform mat4 uProj;
uniform float uTime; uniform float uReveal; uniform float uDim; uniform float uPx; uniform float uMaxPt;
uniform vec2 uOrigin;
uniform vec4 uPA[6]; uniform vec4 uPB[6];
varying vec3 vCol; varying float vA;
const vec3 COOL = vec3(0.78,0.90,1.0);
const vec3 GOLD = vec3(1.0,0.72,0.36);
void main(){
  vec4 mv = uView*vec4(aPos,1.0);
  gl_Position = uProj*mv;
  float d0 = length(aPos.xz-uOrigin) + aSeed*0.5;
  float rv = 1.0 - smoothstep(uReveal-1.4, uReveal, d0);
  float q0 = (d0-uReveal)/0.34;
  float edge = exp(-q0*q0) * step(0.05,uReveal) * (1.0-smoothstep(10.0,13.0,uReveal));
  float pulse = 0.0; float gold = 0.0;
  for(int i=0;i<6;i++){
    vec4 A = uPA[i];
    vec2 dv = aPos.xz-A.xy;
    float d = length(dv);
    float ang = atan(dv.x,-dv.y);
    float rel = ang-uPB[i].x;
    float l = mod(rel+0.5236,1.0472)-0.5236;
    float lobe = 1.0-smoothstep(0.06,0.38,abs(l));
    float rw = mod(rel+3.14159,6.28318)-3.14159;
    float fr = 1.0-smoothstep(0.3,0.6,abs(rw));
    float q = (d-A.z)/0.3;
    float p = exp(-q*q)*lobe*A.w;
    pulse += p; gold += p*fr;
  }
  float tw = 0.8+0.2*sin(uTime*1.6+aSeed*40.0);
  float b = aBase*(0.62*rv*tw + 1.5*pulse) + edge*1.05;
  float g = clamp(gold/max(pulse,0.001),0.0,1.0);
  vCol = mix(aCol, mix(COOL,GOLD,g), clamp(pulse*1.2+edge,0.0,1.0)*0.85);
  vA = clamp(b,0.0,1.6)*uDim;
  float sz = (0.75+aSeed*0.5)*(1.0+pulse*1.2+edge*0.9);
  gl_PointSize = min(uMaxPt, uPx*sz/max(0.1,-mv.z));
}`;
const FS_ROOM = `
precision mediump float;
varying vec3 vCol; varying float vA;
void main(){
  float r = length(gl_PointCoord-0.5)*2.0;
  if(r>1.0) discard;
  float a = 1.0-r; a*=a;
  gl_FragColor = vec4(vCol*(vA*a),1.0);
}`;
const VS_DYN = `
precision highp float;
attribute vec3 aPos; attribute vec3 aCol; attribute float aAl; attribute float aSz;
uniform mat4 uView; uniform mat4 uProj; uniform float uPx; uniform float uDim; uniform float uMaxPt;
varying vec3 vCol; varying float vA;
void main(){
  vec4 mv = uView*vec4(aPos,1.0);
  gl_Position = uProj*mv;
  vCol = aCol; vA = aAl*uDim;
  gl_PointSize = min(uMaxPt, uPx*aSz/max(0.1,-mv.z));
}`;
const FS_DYN = `
precision mediump float;
varying vec3 vCol; varying float vA; uniform float uGlow;
void main(){
  float r = length(gl_PointCoord-0.5)*2.0;
  if(r>1.0) discard;
  float a;
  if(uGlow>0.5){ a = exp(-r*r*14.0)*0.85 + exp(-r*r*3.2)*0.3; a *= 1.0-smoothstep(0.75,1.0,r); }
  else { a = 1.0-r; a*=a; }
  gl_FragColor = vec4(vCol*(vA*a),1.0);
}`;
function compile(type,src){ const s=gl.createShader(type); gl.shaderSource(s,src); gl.compileShader(s); if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; }
function program(vs,fs,attrs){
  const p=gl.createProgram();
  gl.attachShader(p,compile(gl.VERTEX_SHADER,vs)); gl.attachShader(p,compile(gl.FRAGMENT_SHADER,fs));
  attrs.forEach((n,i)=>gl.bindAttribLocation(p,i,n));
  gl.linkProgram(p);
  if(!gl.getProgramParameter(p,gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const u={}; const n=gl.getProgramParameter(p,gl.ACTIVE_UNIFORMS);
  for(let i=0;i<n;i++){ const info=gl.getActiveUniform(p,i); u[info.name.replace(/\[0\]$/,'')]=gl.getUniformLocation(p,info.name); }
  return {p,u};
}
let PROOM, PDYN;
try{
  PROOM = program(VS_ROOM,FS_ROOM,['aPos','aCol','aBase','aSeed']);
  PDYN = program(VS_DYN,FS_DYN,['aPos','aCol','aAl','aSz']);
}catch(err){ console.error(err); noGL(); return; }
const MAXPT = (gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)||[1,64])[1];

/* ---------------- the room, as a point cloud ---------------- */
const ROOM = {x0:-5,x1:5,z0:-4,z1:4,h:2.8};
const HEAD_Y = 1.45, WAVE_V = 4.2;
const START = {x:0.6, z:1.7};
const RP = [];
const C = {edge:[.80,.88,1], wall:[.52,.63,.86], floor:[.36,.45,.64], furn:[.95,.89,.80], door:[1,.88,.72], book:[.88,.80,.70]};
function pt(x,y,z,c,b){ RP.push(x,y,z,c[0],c[1],c[2],b,Math.random()); }
function line3(ax,ay,az,bx,by,bz,per,c,b){
  const L=Math.hypot(bx-ax,by-ay,bz-az), n=Math.max(2,Math.round(L*per));
  for(let i=0;i<=n;i++){ const t=clamp((i+rnd(0.25))/n,0,1);
    pt(lerp(ax,bx,t)+rnd(0.006),lerp(ay,by,t)+rnd(0.006),lerp(az,bz,t)+rnd(0.006),c,b*(0.8+Math.random()*0.4)); }
}
function plane(ox,oy,oz,ux,uy,uz,vx,vy,vz,w,h,sp,c,b,hole){
  const nu=Math.max(1,Math.round(w/sp)), nv=Math.max(1,Math.round(h/sp));
  for(let i=0;i<nu;i++) for(let k=0;k<nv;k++){
    const s=(i+0.5+rnd(0.45))*w/nu, t=(k+0.5+rnd(0.45))*h/nv;
    if(hole && hole(s,t)) continue;
    pt(ox+ux*s+vx*t, oy+uy*s+vy*t, oz+uz*s+vz*t, c, b*(0.65+Math.random()*0.7));
  }
}
function ring(cx,y,cz,rx,rz,n,c,b){ for(let i=0;i<n;i++){ const a=i/n*TAU+rnd(0.02); pt(cx+Math.cos(a)*rx,y+rnd(0.004),cz+Math.sin(a)*rz,c,b*(0.8+Math.random()*0.4)); } }
function box3(x0,x1,y0,y1,z0,z1,per,c,b,sp,fb){
  const E=[[x0,y0,z0,x1,y0,z0],[x0,y1,z0,x1,y1,z0],[x0,y0,z1,x1,y0,z1],[x0,y1,z1,x1,y1,z1],
           [x0,y0,z0,x0,y1,z0],[x1,y0,z0,x1,y1,z0],[x0,y0,z1,x0,y1,z1],[x1,y0,z1,x1,y1,z1],
           [x0,y0,z0,x0,y0,z1],[x1,y0,z0,x1,y0,z1],[x0,y1,z0,x0,y1,z1],[x1,y1,z0,x1,y1,z1]];
  for(const e of E) line3(e[0],e[1],e[2],e[3],e[4],e[5],per,c,b);
  if(sp){
    plane(x0,y1,z0,1,0,0,0,0,1,x1-x0,z1-z0,sp,c,fb);
    plane(x0,y0,z1,1,0,0,0,1,0,x1-x0,y1-y0,sp,c,fb);
    plane(x0,y0,z0,1,0,0,0,1,0,x1-x0,y1-y0,sp,c,fb);
    plane(x0,y0,z0,0,0,1,0,1,0,z1-z0,y1-y0,sp,c,fb);
    plane(x1,y0,z0,0,0,1,0,1,0,z1-z0,y1-y0,sp,c,fb);
  }
}
/* obstacles in plan view: segments for echoes, boxes for walking */
const SEGS=[], BOXES=[];
function addSeg(ax,az,bx,bz,top){ const dx=bx-ax, dz=bz-az, L=Math.hypot(dx,dz); SEGS.push({ax,az,bx,bz,nx:-dz/L,nz:dx/L,top}); }
function addBox(x0,x1,z0,z1,top){ BOXES.push({x0,x1,z0,z1}); addSeg(x0,z0,x1,z0,top); addSeg(x1,z0,x1,z1,top); addSeg(x1,z1,x0,z1,top); addSeg(x0,z1,x0,z0,top); }

(function buildRoom(){
  const H=ROOM.h;
  // floor, plus a sliver of hallway beyond the open door
  plane(-5,0,4, 1,0,0, 0,0,-1, 10,8, .22, C.floor, .5);
  plane(1.6,0,-4, 1,0,0, 0,0,-1, 1,1.4, .2, C.floor, .28);
  line3(-5,0,-4,5,0,-4,36,C.edge,.85); line3(-5,0,4,5,0,4,36,C.edge,.5);
  line3(-5,0,-4,-5,0,4,36,C.edge,.85); line3(5,0,-4,5,0,4,36,C.edge,.85);
  // walls (the wall nearest the viewer is cut away)
  plane(-5,0,-4, 1,0,0, 0,1,0, 10,H, .1, C.wall, .55, (s,t)=>(s>6.55&&s<7.65&&t<2.15)||(s>1.72&&s<3.68&&t>.92&&t<2.18));
  plane(-5,0,4, 0,0,-1, 0,1,0, 8,H, .11, C.wall, .5);
  plane(5,0,-4, 0,0,1, 0,1,0, 8,H, .11, C.wall, .5);
  for(const [x,z,b] of [[-5,-4,.95],[5,-4,.95],[-5,4,.4],[5,4,.4]]) line3(x,0,z,x,H,z,40,C.edge,b);
  line3(-5,H,-4,5,H,-4,36,C.edge,.9); line3(-5,H,-4,-5,H,4,36,C.edge,.8); line3(5,H,-4,5,H,4,36,C.edge,.8);
  addSeg(-5,-4,1.6,-4,H); addSeg(2.6,-4,5,-4,H); addSeg(5,-4,5,4,H); addSeg(5,4,-5,4,H); addSeg(-5,4,-5,-4,H);
  // door: frame, and the door itself standing open
  line3(1.6,0,-4,1.6,2.1,-4,70,C.door,1.1); line3(1.6,2.1,-4,2.6,2.1,-4,70,C.door,1.1); line3(2.6,2.1,-4,2.6,0,-4,70,C.door,1.1);
  const DA=70*Math.PI/180, dux=Math.cos(DA), duz=Math.sin(DA), DW=.96, dex=1.6+dux*DW, dez=-4+duz*DW;
  plane(1.6,0,-4, dux,0,duz, 0,1,0, DW,2.06, .065, C.door, .5);
  line3(dex,0,dez,dex,2.06,dez,70,C.door,1.05); line3(1.6,2.06,-4,dex,2.06,dez,70,C.door,1); line3(1.6,.02,-4,dex,.02,dez,60,C.door,.7);
  for(let i=0;i<14;i++) pt(1.6+dux*(DW-.1)+rnd(.02),1.0+rnd(.02),-4+duz*(DW-.1)+rnd(.02)+.03,C.door,1.3);
  addSeg(1.6,-4,dex,dez,2.06);
  // key hook beside the door
  line3(2.82,1.5,-3.98,2.98,1.5,-3.98,120,C.door,1.1);
  for(let i=0;i<26;i++) pt(2.9+rnd(.035),1.4+rnd(.05),-3.96+rnd(.01),[1,.85,.6],1.2);
  // window
  const wz=-3.99;
  line3(-3.28,.92,wz,-1.32,.92,wz,70,C.edge,1); line3(-3.28,2.18,wz,-1.32,2.18,wz,70,C.edge,1);
  line3(-3.28,.92,wz,-3.28,2.18,wz,70,C.edge,1); line3(-1.32,.92,wz,-1.32,2.18,wz,70,C.edge,1);
  line3(-2.3,.92,wz,-2.3,2.18,wz,60,C.edge,.75); line3(-3.28,1.55,wz,-1.32,1.55,wz,60,C.edge,.75);
  line3(-3.4,.9,-3.9,-1.2,.9,-3.9,60,C.edge,.8);
  // picture above the sofa
  const px=4.98;
  line3(px,1.25,.25,px,1.25,1.95,60,C.furn,.9); line3(px,2,.25,px,2,1.95,60,C.furn,.9);
  line3(px,1.25,.25,px,2,.25,60,C.furn,.9); line3(px,1.25,1.95,px,2,1.95,60,C.furn,.9);
  line3(px,1.35,.4,px,1.75,.9,50,C.furn,.55); line3(px,1.75,.9,px,1.5,1.25,50,C.furn,.55);
  line3(px,1.5,1.25,px,1.82,1.6,50,C.furn,.55); line3(px,1.82,1.6,px,1.35,1.82,50,C.furn,.55);
  // table, with a bowl, glasses and a mug on it
  const TX0=-1.5,TX1=.3,TZ0=-1.5,TZ1=-.5,TY=.75;
  plane(TX0,TY,TZ0,1,0,0,0,0,1,TX1-TX0,TZ1-TZ0,.055,C.furn,.5);
  for(const y of [TY,TY-.05]){ line3(TX0,y,TZ0,TX1,y,TZ0,70,C.furn,1); line3(TX0,y,TZ1,TX1,y,TZ1,70,C.furn,1); line3(TX0,y,TZ0,TX0,y,TZ1,70,C.furn,1); line3(TX1,y,TZ0,TX1,y,TZ1,70,C.furn,1); }
  for(const [x,z] of [[TX0+.07,TZ0+.07],[TX1-.07,TZ0+.07],[TX0+.07,TZ1-.07],[TX1-.07,TZ1-.07]]) line3(x,0,z,x,TY-.05,z,60,C.furn,.85);
  ring(-1.15,TY+.11,-1.15,.16,.16,34,C.furn,1); ring(-1.15,TY+.05,-1.15,.12,.12,26,C.furn,.8); ring(-1.15,TY+.01,-1.15,.07,.07,16,C.furn,.7);
  for(const [fx,fz] of [[-1.2,-1.18],[-1.09,-1.1],[-1.13,-1.24]]) for(let i=0;i<22;i++){ const a=Math.random()*TAU, b=Math.acos(rnd(1)); pt(fx+Math.sin(b)*Math.cos(a)*.05,TY+.12+Math.cos(b)*.05,fz+Math.sin(b)*Math.sin(a)*.05,[1,.8,.6],.8); }
  ring(-1.36,TY+.012,-.8,.045,.03,18,C.furn,1.1); ring(-1.24,TY+.012,-.8,.045,.03,18,C.furn,1.1); line3(-1.315,TY+.015,-.8,-1.285,TY+.015,-.8,200,C.furn,1);
  ring(0,TY+.01,-.75,.045,.045,16,C.furn,.8); ring(0,TY+.1,-.75,.045,.045,16,C.furn,1);
  for(let i=0;i<9;i++){ const a=-Math.PI/2+i/8*Math.PI; pt(.065+Math.cos(a)*.025,TY+.055+Math.sin(a)*.03,-.75,C.furn,1); }
  addBox(TX0,TX1,TZ0,TZ1,TY);
  // chairs
  function chair(cx,cz,face){
    const s=.24, y=.45;
    box3(cx-s,cx+s,y-.03,y,cz-s,cz+s,60,C.furn,.9,.07,.45);
    for(const [lx,lz] of [[-s+.03,-s+.03],[s-.03,-s+.03],[-s+.03,s-.03],[s-.03,s-.03]]) line3(cx+lx,0,cz+lz,cx+lx,y-.03,cz+lz,60,C.furn,.8);
    const bx=cx-face*(s-.02);
    line3(bx,y,cz-s+.02,bx,y+.5,cz-s+.02,60,C.furn,.9); line3(bx,y,cz+s-.02,bx,y+.5,cz+s-.02,60,C.furn,.9);
    line3(bx,y+.5,cz-s+.02,bx,y+.5,cz+s-.02,60,C.furn,.95); line3(bx,y+.32,cz-s+.02,bx,y+.32,cz+s-.02,60,C.furn,.7);
    addBox(cx-s,cx+s,cz-s,cz+s,.95);
  }
  chair(-1.95,-1.0,1); chair(.75,-1.0,-1);
  ring(-.6,.01,-1,1.7,1.15,140,C.floor,.75); ring(-.6,.01,-1,1.6,1.05,130,C.floor,.45);
  // bookshelf
  box3(-5,-4.6,0,2,-2.6,-.6,55,C.furn,.95);
  for(const y of [.03,.5,1.0,1.5]){
    line3(-4.6,y,-2.6,-4.6,y,-.6,55,C.furn,.9);
    plane(-5,y,-2.6,1,0,0,0,0,1,.4,2.0,.09,C.furn,.35);
    let z=-2.56;
    while(z<-.66){
      const w=.03+Math.random()*.05, hb=.18+Math.random()*.17;
      if(Math.random()<.1){ z+=.08; continue; }
      const bz=z+w/2;
      line3(-4.64,y+.01,bz,-4.64,y+hb,bz,70,C.book,.85);
      line3(-4.64,y+hb,z+.004,-4.64,y+hb,z+w-.004,120,C.book,.7);
      z+=w+.006;
    }
  }
  addBox(-5,-4.6,-2.6,-.6,2.0);
  // sofa
  box3(3.95,4.9,0,.42,-.1,2.3,45,C.furn,.85,.1,.35);
  box3(4.62,4.9,.42,.86,-.1,2.3,45,C.furn,.85,.1,.35);
  box3(3.95,4.62,.42,.6,-.1,.1,45,C.furn,.8); box3(3.95,4.62,.42,.6,2.1,2.3,45,C.furn,.8);
  line3(3.95,.43,.7,4.62,.43,.7,50,C.furn,.6); line3(3.95,.43,1.5,4.62,.43,1.5,50,C.furn,.6);
  addBox(3.95,4.9,-.1,2.3,.86);
  // floor lamp
  line3(4.45,0,-1,4.45,1.5,-1,50,C.furn,.8); ring(4.45,.02,-1,.15,.15,22,C.furn,.8);
  ring(4.45,1.48,-1,.24,.24,30,C.door,.95); ring(4.45,1.76,-1,.14,.14,22,C.door,.95);
  for(let i=0;i<8;i++){ const a=i/8*TAU; line3(4.45+Math.cos(a)*.24,1.48,-1+Math.sin(a)*.24,4.45+Math.cos(a)*.14,1.76,-1+Math.sin(a)*.14,40,C.door,.6); }
  addBox(4.27,4.63,-1.18,-.82,1.76);
})();
const ROOM_N = RP.length/8;
const roomBuf = gl.createBuffer();
gl.bindBuffer(gl.ARRAY_BUFFER,roomBuf);
gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(RP),gl.STATIC_DRAW);

function raycast(ox,oz,dx,dz,maxD){
  let best=maxD, hit=null;
  for(const s of SEGS){
    const ex=s.bx-s.ax, ez=s.bz-s.az, den=dx*ez-dz*ex;
    if(Math.abs(den)<1e-9) continue;
    const fx=s.ax-ox, fz=s.az-oz;
    const t=(fx*ez-fz*ex)/den, u=(fx*dz-fz*dx)/den;
    if(t>0.05 && t<best && u>=0 && u<=1){ best=t; hit=s; }
  }
  return hit ? {d:best,x:ox+dx*best,z:oz+dz*best,nx:hit.nx,nz:hit.nz,top:hit.top} : null;
}
function blocked(x,z){
  const m=.3;
  if(x<ROOM.x0+m||x>ROOM.x1-m||z<ROOM.z0+m||z>ROOM.z1-m) return true;
  for(const b of BOXES) if(x>b.x0-m&&x<b.x1+m&&z>b.z0-m&&z<b.z1+m) return true;
  return false;
}

/* ---------------- dynamic points: waves, echoes, ripples, glows ---------------- */
const ARC_N=64, DYN_CAP=ARC_N*240;
const dyn=new Float32Array(DYN_CAP*8);
const dynBuf=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,dynBuf); gl.bufferData(gl.ARRAY_BUFFER,dyn.byteLength,gl.DYNAMIC_DRAW);
const GLOW_CAP=24, glow=new Float32Array(GLOW_CAP*8);
const glowBuf=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,glowBuf); gl.bufferData(gl.ARRAY_BUFFER,glow.byteLength,gl.DYNAMIC_DRAW);
const JIT=new Float32Array(4096); for(let i=0;i<JIT.length;i++) JIT[i]=Math.random()*2-1;
const COOL=[.74,.86,1], GOLD=[1,.72,.36], ECHO=[.92,.96,1], ECHO_G=[1,.84,.58];
let wc=0, gc=0;
const arcs=[], glPulses=[];
const rs=()=>(Math.random()*2000)|0;

function spawnArc(o){ if(arcs.length<300) arcs.push(o); }
function writeArc(cx,y,cz,ang,span,r,n,col,alpha,size,full,seed){
  if(alpha<=0.003) return;
  if(wc+n>DYN_CAP) n=DYN_CAP-wc;
  for(let i=0;i<n;i++){
    const u = full ? i/n : (n>1 ? i/(n-1) : 0.5);
    const th = full ? ang+u*TAU : ang+(u*2-1)*span;
    const j = ((seed+i)*2)&4095;
    const rr = r+JIT[j]*0.022*(1+r*0.12);
    const k = wc*8;
    dyn[k]=cx+Math.sin(th)*rr; dyn[k+1]=y+JIT[j+1]*0.028; dyn[k+2]=cz-Math.cos(th)*rr;
    dyn[k+3]=col[0]; dyn[k+4]=col[1]; dyn[k+5]=col[2];
    const tp = full ? 1 : Math.pow(Math.sin(Math.PI*u),0.75);
    dyn[k+6]=alpha*tp; dyn[k+7]=size*(0.7+0.3*tp);
    wc++;
  }
}
function addGlow(x,y,z,col,alpha,size){
  if(gc>=GLOW_CAP||alpha<=0.003) return;
  const k=gc*8; glow[k]=x; glow[k+1]=y; glow[k+2]=z; glow[k+3]=col[0]; glow[k+4]=col[1]; glow[k+5]=col[2]; glow[k+6]=alpha; glow[k+7]=size; gc++;
}

/* ---------------- state ---------------- */
const S = {
  phase:'gate', t0:0, tl:0,
  pos:{x:START.x,z:START.z}, heading:0, tHeading:0, headSrc:'none', walk:null, keys:{},
  ndcX:0, ndcY:0, mx:-9999, my:-9999, hasPtr:false, down:null,
  par:{x:0,y:0}, focus:{x:START.x*0.5,z:START.z*0.4-1.1},
  flash:0, nextPulse:Infinity, dim:1, lvis:0,
  playing:null, token:0, heard:0, origin:[START.x,START.z]
};
let clock=0;

/* ---------------- waves ---------------- */
function emitPulse(){
  const ox=S.pos.x, oz=S.pos.z, h=S.heading;
  glPulses.push({cx:ox,cz:oz,h,t0:clock}); if(glPulses.length>6) glPulses.shift();
  for(let k=0;k<6;k++){
    const a=h+k*TAU/6, dx=Math.sin(a), dz=-Math.cos(a);
    spawnArc({kind:'out',cx:ox,cz:oz,y:HEAD_Y,ang:a,span:.3,r0:.35,v:WAVE_V,maxR:7.5,t0:clock,hit:raycast(ox,oz,dx,dz,7.5),
      front:k===0,col:k===0?GOLD:COOL,base:k===0?1.25:1,size:k===0?2.3:1.9,seed:rs()});
  }
  Au.ping(1); S.flash=1; pingHud();
}
function emitOmni(){
  const ox=S.pos.x, oz=S.pos.z;
  for(let k=0;k<12;k++){
    const a=k*TAU/12, dx=Math.sin(a), dz=-Math.cos(a);
    spawnArc({kind:'out',cx:ox,cz:oz,y:HEAD_Y,ang:a,span:.262,r0:.3,v:6.2,maxR:9,t0:clock,hit:raycast(ox,oz,dx,dz,9),
      front:false,quiet:k%2===1,col:COOL,base:1,size:1.9,seed:rs()});
  }
  Au.ping(.7); S.flash=1;
}
function onHit(a){
  const h=a.hit, dx=Math.sin(a.ang), dz=-Math.cos(a.ang), dot=dx*h.nx+dz*h.nz;
  const rx=dx-2*dot*h.nx, rz=dz-2*dot*h.nz, ra=Math.atan2(rx,-rz);
  const y=Math.min(a.y,h.top-.08), c=a.front?ECHO_G:ECHO;
  spawnArc({kind:'echo',cx:h.x,cz:h.z,y,ang:ra,span:.62,r0:.03,v:1.7,life:.8,t0:clock,col:c,base:a.front?1.5:1.2,size:2.3,seed:rs()});
  spawnArc({kind:'echo',cx:h.x,cz:h.z,y,ang:ra,span:.55,r0:.03,v:1.5,life:.75,t0:clock+.14,col:c,base:a.front?1:.75,size:2,seed:rs()});
  if(!a.quiet) Au.echo(h.x,y,h.z,h.d,a.front);
}
function memRipple(m){
  spawnArc({kind:'ring',full:true,cx:m.x,cz:m.z,y:m.y,ang:0,span:0,r0:.08,v:1.25,life:1.7,t0:clock,col:GOLD,base:1.1,size:1.9,seed:rs()});
}
function updateArcs(){
  for(let i=arcs.length-1;i>=0;i--){
    const a=arcs[i], age=clock-a.t0;
    if(age<0) continue;
    let r, al;
    if(a.kind==='out'){
      r=a.r0+a.v*age;
      if(a.hit&&!a.hitDone&&r>=a.hit.d){ a.hitDone=true; a.hitT=clock; onHit(a); }
      if(a.hitDone){ r=a.hit.d; al=a.base*(1-(clock-a.hitT)/.22); }
      else al=a.base*(1-sstep(a.maxR-2.2,a.maxR,r));
      if(al<=0||r>a.maxR){ arcs.splice(i,1); continue; }
    } else {
      if(age>a.life){ arcs.splice(i,1); continue; }
      r=a.r0+a.v*age; al=a.base*Math.pow(1-age/a.life,1.6)*Math.min(1,age/.06);
    }
    const n=a.full ? clamp(Math.round(TAU*r/.06),16,ARC_N) : clamp(Math.round(2*a.span*r/.045),8,ARC_N);
    writeArc(a.cx,a.y,a.cz,a.ang,a.span,r,n,a.col,al,a.size,!!a.full,a.seed);
  }
}
function writeHead(){
  const v=S.lvis; if(v<.01) return;
  const vis=v*(1+.4*S.flash);
  for(let k=0;k<6;k++){
    const a=S.heading+k*TAU/6, f=k===0;
    writeArc(S.pos.x,HEAD_Y,S.pos.z,a,.36,.34,10,f?GOLD:COOL,(f?1.2:.55)*vis,f?1.9:1.4,false,100+k*16);
    writeArc(S.pos.x,HEAD_Y,S.pos.z,a,.3,.5,12,f?GOLD:COOL,(f?.7:.24)*vis,1.3,false,300+k*16);
  }
  writeArc(S.pos.x,.02,S.pos.z,clock*.2,0,.3,30,COOL,.18*v,1,true,600);
  for(let i=0;i<10&&wc<DYN_CAP;i++){
    const k=wc*8; dyn[k]=S.pos.x; dyn[k+1]=.05+i*(HEAD_Y-.1)/9; dyn[k+2]=S.pos.z;
    dyn[k+3]=COOL[0]; dyn[k+4]=COOL[1]; dyn[k+5]=COOL[2]; dyn[k+6]=.16*v; dyn[k+7]=1; wc++;
  }
  addGlow(S.pos.x,HEAD_Y,S.pos.z,[.8,.9,1],.7*v,22+14*S.flash);
  addGlow(S.pos.x,HEAD_Y,S.pos.z,[1,1,1],.95*v,5);
}

/* ---------------- camera ---------------- */
const VIEW=new Float32Array(16), PROJ=new Float32Array(16), EYE=[0,0,0], TGT=[0,0,0];
const FOV=45*Math.PI/180;
function perspective(o,fy,asp,n,f){ const t=1/Math.tan(fy/2), nf=1/(n-f); o.fill(0); o[0]=t/asp; o[5]=t; o[10]=(f+n)*nf; o[11]=-1; o[14]=2*f*n*nf; }
function lookAt(o,e,c,up){
  let zx=e[0]-c[0], zy=e[1]-c[1], zz=e[2]-c[2], l=Math.hypot(zx,zy,zz); zx/=l; zy/=l; zz/=l;
  let xx=up[1]*zz-up[2]*zy, xy=up[2]*zx-up[0]*zz, xz=up[0]*zy-up[1]*zx; l=Math.hypot(xx,xy,xz); xx/=l; xy/=l; xz/=l;
  const yx=zy*xz-zz*xy, yy=zz*xx-zx*xz, yz=zx*xy-zy*xx;
  o[0]=xx; o[1]=yx; o[2]=zx; o[3]=0; o[4]=xy; o[5]=yy; o[6]=zy; o[7]=0; o[8]=xz; o[9]=yz; o[10]=zz; o[11]=0;
  o[12]=-(xx*e[0]+xy*e[1]+xz*e[2]); o[13]=-(yx*e[0]+yy*e[1]+yz*e[2]); o[14]=-(zx*e[0]+zy*e[1]+zz*e[2]); o[15]=1;
}
function project(x,y,z){
  const v=VIEW;
  const vx=v[0]*x+v[4]*y+v[8]*z+v[12], vy=v[1]*x+v[5]*y+v[9]*z+v[13], vz=v[2]*x+v[6]*y+v[10]*z+v[14];
  if(vz>-.05) return null;
  const w=-vz;
  return {x:(PROJ[0]*vx/w*.5+.5)*innerWidth, y:(.5-PROJ[5]*vy/w*.5)*innerHeight};
}
function floorHit(nx,ny){
  const th=Math.tan(FOV/2), asp=innerWidth/innerHeight, dx=nx*th*asp, dy=ny*th, v=VIEW;
  const wx=dx*v[0]+dy*v[1]-v[2], wy=dx*v[4]+dy*v[5]-v[6], wz=dx*v[8]+dy*v[9]-v[10];
  if(wy>-1e-4) return null;
  const t=-EYE[1]/wy; if(t>60) return null;
  return {x:EYE[0]+wx*t, z:EYE[2]+wz*t};
}
function updateCamera(dt){
  const asp=innerWidth/innerHeight, k=asp<1?1+(1-asp)*1.9:1;
  const b=S.phase==='live'?1:easeIO(sstep(3.8,6.6,S.tl));
  const f=1-Math.exp(-dt*1.4);
  S.focus.x+=(S.pos.x*.5-S.focus.x)*f; S.focus.z+=(S.pos.z*.4-1.1-S.focus.z)*f;
  const g=1-Math.exp(-dt*2); S.par.x+=(S.ndcX-S.par.x)*g; S.par.y+=(S.ndcY-S.par.y)*g;
  const hx=S.pos.x, hz=S.pos.z;
  const cE=[hx+Math.sin(clock*.17)*.3, HEAD_Y+1.5, hz+3.4], cT=[hx, HEAD_Y-.3, hz-1.6];
  const up=asp<1?1.3:1, back=asp<1?.78:1;
  const fT=[S.focus.x,.55,S.focus.z], fE=[fT[0]+S.par.x*.55, fT[1]+6.3*k*up+S.par.y*.35, fT[2]+9*k*back];
  for(let i=0;i<3;i++){ EYE[i]=lerp(cE[i],fE[i],b); TGT[i]=lerp(cT[i],fT[i],b); }
  lookAt(VIEW,EYE,TGT,[0,1,0]); perspective(PROJ,FOV,asp,.05,140);
}

/* ---------------- audio ---------------- */
const Au = {
  ctx:null, on:false, master:null, wet:null, amb:null, drone:null, ambStarted:false,
  init(){
    if(this.ctx) return;
    const AC=window.AudioContext||window.webkitAudioContext; if(!AC) return;
    try{ this.ctx=new AC(); }catch(e){ this.ctx=null; return; }
    const c=this.ctx;
    this.master=c.createGain(); this.master.gain.value=0;
    const comp=c.createDynamicsCompressor(); comp.threshold.value=-16; comp.knee.value=12; comp.ratio.value=3;
    this.master.connect(comp); comp.connect(c.destination);
    const conv=c.createConvolver(); conv.buffer=this.impulse(2.6,2.4);
    this.wet=c.createGain(); this.wet.gain.value=.35; this.wet.connect(conv); conv.connect(this.master);
    // room tone: slow brown noise
    const len=c.sampleRate*6, buf=c.createBuffer(2,len,c.sampleRate);
    for(let ch=0;ch<2;ch++){ const d=buf.getChannelData(ch); let l=0; for(let i=0;i<len;i++){ l=(l+.02*(Math.random()*2-1))/1.02; d[i]=l*3.2; } const e=d[len-1]-d[0]; for(let i=0;i<len;i++) d[i]-=e*i/(len-1); }
    const src=c.createBufferSource(); src.buffer=buf; src.loop=true;
    const lp=c.createBiquadFilter(); lp.type='lowpass'; lp.frequency.value=380;
    const lfo=c.createOscillator(); lfo.frequency.value=.05; const lg=c.createGain(); lg.gain.value=160; lfo.connect(lg); lg.connect(lp.frequency); lfo.start();
    this.amb=c.createGain(); this.amb.gain.value=0; src.connect(lp); lp.connect(this.amb); this.amb.connect(this.master); src.start();
    // low drone
    this.drone=c.createGain(); this.drone.gain.value=0;
    const dl=c.createBiquadFilter(); dl.type='lowpass'; dl.frequency.value=260;
    [[55,.5],[82.41,.3],[110.4,.14],[164.8,.05]].forEach(([fr,g])=>{ const o=c.createOscillator(); o.frequency.value=fr; const og=c.createGain(); og.gain.value=g; o.connect(og); og.connect(dl); o.start(); });
    dl.connect(this.drone); this.drone.connect(this.master); this.drone.connect(this.wet);
  },
  impulse(sec,dec){ const c=this.ctx, len=Math.floor(c.sampleRate*sec), b=c.createBuffer(2,len,c.sampleRate); for(let ch=0;ch<2;ch++){ const d=b.getChannelData(ch); for(let i=0;i<len;i++) d[i]=(Math.random()*2-1)*Math.pow(1-i/len,dec); } return b; },
  live(){ return !!(this.ctx&&this.on); },
  setOn(v){
    if(!this.ctx) return;
    this.on=v; if(this.ctx.state==='suspended') this.ctx.resume();
    const t=this.ctx.currentTime; this.master.gain.cancelScheduledValues(t); this.master.gain.setTargetAtTime(v?.9:0,t,.2);
    if(v&&S.phase!=='gate') this.startAmbient();
  },
  startAmbient(){ if(!this.ctx||this.ambStarted) return; this.ambStarted=true; const t=this.ctx.currentTime; this.amb.gain.setTargetAtTime(.085,t,1); this.drone.gain.setTargetAtTime(.03,t+.6,1.5); },
  duck(v){ if(!this.ctx||!this.ambStarted) return; const t=this.ctx.currentTime; this.amb.gain.setTargetAtTime(v?.035:.085,t,.4); this.drone.gain.setTargetAtTime(v?.012:.03,t,.4); },
  panner(x,y,z){
    const p=this.ctx.createPanner(); p.panningModel='HRTF'; p.distanceModel='inverse'; p.refDistance=1.2; p.maxDistance=60; p.rolloffFactor=.8;
    if(p.positionX){ p.positionX.value=x; p.positionY.value=y; p.positionZ.value=z; } else p.setPosition(x,y,z);
    return p;
  },
  listener(x,y,z,h){
    if(!this.ctx) return;
    const L=this.ctx.listener, fx=Math.sin(h), fz=-Math.cos(h);
    if(L.positionX){ L.positionX.value=x; L.positionY.value=y; L.positionZ.value=z; L.forwardX.value=fx; L.forwardY.value=0; L.forwardZ.value=fz; L.upX.value=0; L.upY.value=1; L.upZ.value=0; }
    else { L.setPosition(x,y,z); L.setOrientation(fx,0,fz,0,1,0); }
  },
  env(g,t,peak,att,dec){ g.gain.setValueAtTime(0,t); g.gain.linearRampToValueAtTime(peak,t+att); g.gain.exponentialRampToValueAtTime(.0001,t+att+dec); },
  ping(level){
    if(!this.live()) return; const c=this.ctx, t=c.currentTime;
    const o=c.createOscillator(); o.frequency.setValueAtTime(1560,t); o.frequency.exponentialRampToValueAtTime(1040,t+.1);
    const g=c.createGain(); this.env(g,t,.03*level,.005,.16); o.connect(g); g.connect(this.master); g.connect(this.wet); o.start(t); o.stop(t+.25);
  },
  echo(x,y,z,d,front){
    if(!this.live()) return; const c=this.ctx, t=c.currentTime;
    const o=c.createOscillator(); o.type=front?'triangle':'sine'; o.frequency.value=440+clamp(6-d,0,6)*115;
    const g=c.createGain(); this.env(g,t,(front?.12:.08)*(.55+.45*clamp(1-d/7,0,1)),.004,.2);
    const p=this.panner(x,y,z); o.connect(g); g.connect(p); p.connect(this.master);
    const s=c.createGain(); s.gain.value=.4; p.connect(s); s.connect(this.wet);
    o.start(t); o.stop(t+.3);
  },
  bell(m,notes,gain,gap){
    if(!this.live()) return; const c=this.ctx, t0=c.currentTime, p=this.panner(m.x,m.y,m.z);
    p.connect(this.master); const s=c.createGain(); s.gain.value=.6; p.connect(s); s.connect(this.wet);
    notes.forEach((f,i)=>{ const t=t0+i*gap;
      [[1,1],[2.76,.18]].forEach(([mul,amp])=>{ const o=c.createOscillator(); o.frequency.value=f*mul; const g=c.createGain(); this.env(g,t,gain*amp,.004,mul>1?.5:1.4); o.connect(g); g.connect(p); o.start(t); o.stop(t+1.6); });
    });
  },
  sparkle(m){ this.bell(m,[1318.5,1760,2093],.035,.07); },
  chime(m){ this.bell(m,[659.25,987.77],.07,.11); },
  swell(){
    if(!this.live()) return; const c=this.ctx, t=c.currentTime;
    [146.83,220,293.66,369.99,440,587.33].forEach((f,i)=>{
      const o=c.createOscillator(); o.type=i%2?'triangle':'sine'; o.frequency.value=f; o.detune.value=rnd(4);
      const g=c.createGain(); g.gain.setValueAtTime(0,t); g.gain.linearRampToValueAtTime(.022,t+1.3+i*.08); g.gain.setTargetAtTime(0,t+2.6,1.2);
      o.connect(g); g.connect(this.master); g.connect(this.wet); o.start(t); o.stop(t+8);
    });
  },
  addHum(m){
    const c=this.ctx, p=this.panner(m.x,m.y,m.z), g=c.createGain(); g.gain.value=0;
    const trem=c.createGain(); trem.gain.value=.75;
    const lfo=c.createOscillator(); lfo.frequency.value=.5+Math.random()*.4; const lg=c.createGain(); lg.gain.value=.25; lfo.connect(lg); lg.connect(trem.gain); lfo.start();
    const base=[196,220,174.61][m.idx]||196;
    [[1,.6],[1.5,.32],[2,.08]].forEach(([mul,a])=>{ const o=c.createOscillator(); o.frequency.value=base*mul*(1+rnd(.002)); const og=c.createGain(); og.gain.value=a; o.connect(og); og.connect(trem); o.start(); });
    trem.connect(g); g.connect(p); p.connect(this.master); m.hum=g;
  },
  humTo(m,v){ if(m.hum) m.hum.gain.setTargetAtTime(v,this.ctx.currentTime,.25); },
  recording(m){
    const el=new Audio(); el.src=m.audio; el.preload='auto';
    if(this.ctx){ try{ const src=this.ctx.createMediaElementSource(el), p=this.panner(m.x,m.y,m.z); src.connect(p); p.connect(this.master); }catch(e){} }
    return el;
  }
};

/* ---------------- memories: chips, captions, speech ---------------- */
let VOICE=null;
function pickVoice(){
  if(!('speechSynthesis' in window)) return;
  const vs=speechSynthesis.getVoices(); if(!vs.length) return;
  for(const p of ['Samantha','Serena','Karen','Moira','Google UK English Female','Microsoft Aria','Microsoft Jenny','Google US English','Daniel']){
    const v=vs.find(v=>v.name.includes(p)); if(v){ VOICE=v; return; }
  }
  VOICE=vs.find(v=>/^en/i.test(v.lang))||null;
}
if('speechSynthesis' in window){ pickVoice(); speechSynthesis.onvoiceschanged=pickVoice; }

const memLayer=$('#mems'), capEl=$('#caption'), capMeta=$('#capMeta'), capText=$('#capText');
MEMS.forEach((m,i)=>{
  Object.assign(m,{idx:i,vis:0,heard:false,near:false,sx:-999,sy:-999,front:false,nextRipple:0,hum:null,el:null});
  const b=document.createElement('button'); b.type='button'; b.className='mem';
  b.setAttribute('aria-label','Play the memory saved at the '+m.label.toLowerCase());
  b.innerHTML='<span class="mem-ico" aria-hidden="true"><i></i><i></i><i></i></span>';
  const l=document.createElement('span'); l.textContent=m.label; b.appendChild(l);
  b.addEventListener('click',e=>{ e.stopPropagation(); if(S.playing===m) stopMemory(); else playMemory(m); });
  memLayer.appendChild(b); m.btn=b;
});
let capTimers=[], revealEnd=0;
function clearCapTimers(){ capTimers.forEach(clearTimeout); capTimers=[]; }
function showCaption(m,token){
  capMeta.textContent='MeriMark: '+m.label+', '+m.when;
  capText.textContent='';
  const words=m.text.split(' ');
  const spans=words.map((w,i)=>{
    const s=document.createElement('span'); s.textContent=(i===0?'“':'')+w+(i===words.length-1?'”':'');
    capText.appendChild(s); if(i<words.length-1) capText.appendChild(document.createTextNode(' '));
    return s;
  });
  capEl.classList.add('show');
  spans.forEach((s,i)=>capTimers.push(setTimeout(()=>{ if(S.token===token) s.classList.add('on'); },450+i*330)));
  revealEnd=performance.now()+450+words.length*330;
}
function playMemory(m){
  stopMemory();
  const token=++S.token;
  S.playing=m; m.nextRipple=clock; m.btn.classList.add('playing');
  if(!m.heard){ m.heard=true; m.btn.classList.add('heard'); S.heard++; $('#memCount').textContent=S.heard; }
  showCaption(m,token);
  Au.chime(m); Au.duck(true);
  const est=1800+m.text.split(' ').length*330;
  const end=()=>{ if(S.token===token) finishMemory(); };
  if(m.audio){
    const el=Au.recording(m); m.el=el;
    el.onended=end; el.onerror=end; el.play().catch(end);
    capTimers.push(setTimeout(end,90000)); return;
  }
  if(Au.live()&&('speechSynthesis' in window)){
    capTimers.push(setTimeout(()=>{
      if(S.token!==token) return;
      try{ const u=new SpeechSynthesisUtterance(m.text); if(VOICE){ u.voice=VOICE; u.lang=VOICE.lang; } else u.lang='en-US'; u.rate=.95; u.onend=end; u.onerror=end; speechSynthesis.speak(u); }catch(e){}
    },420));
    capTimers.push(setTimeout(end,est+7000));
  } else capTimers.push(setTimeout(end,est));
}
function finishMemory(){
  const m=S.playing; if(!m) return;
  m.btn.classList.remove('playing');
  if(m.el){ try{ m.el.pause(); }catch(e){} m.el=null; }
  S.playing=null; Au.duck(false);
  const token=S.token, wait=Math.max(1800,revealEnd-performance.now()+1500);
  capTimers.push(setTimeout(()=>{ if(S.token===token&&!S.playing) capEl.classList.remove('show'); },wait));
}
function stopMemory(){
  S.token++;
  if('speechSynthesis' in window) speechSynthesis.cancel();
  clearCapTimers();
  const m=S.playing;
  if(m){ m.btn.classList.remove('playing'); if(m.el){ try{ m.el.pause(); }catch(e){} m.el=null; } }
  S.playing=null; Au.duck(false); capEl.classList.remove('show');
}
let humT=0;
function updateMemories(dt){
  const live=S.phase==='live', intro=S.phase==='intro', tl=S.tl;
  const doHum=Au.ctx&&clock-humT>.2; if(doHum) humT=clock;
  for(const m of MEMS){
    const target=live?1:(intro&&m.idx===0?sstep(5.95,6.25,tl):0);
    m.vis+=(target-m.vis)*(1-Math.exp(-dt*(live?1.5:10)));
    const playing=S.playing===m;
    const pop=(intro&&m.idx===0&&tl>5.95)?1+1.5*Math.exp(-(tl-5.95)*4.5):1;
    const sc=(playing?1.3:1)*(1+.12*Math.sin(clock*2.1+m.idx*1.7))*pop;
    if(m.vis>.01){
      addGlow(m.x,m.y,m.z,[1,.64,.28],.8*m.vis,34*sc);
      addGlow(m.x,m.y,m.z,[1,.95,.85],.95*m.vis,6*sc);
      writeArc(m.x,m.y,m.z,clock*.9+m.idx,0,.17+.015*Math.sin(clock*3+m.idx),12,GOLD,.5*m.vis,.85,true,900+m.idx*40);
    }
    const p=project(m.x,m.y,m.z);
    if(p){ m.sx=p.x; m.sy=p.y; m.front=true; } else m.front=false;
    const dm=Math.hypot(S.mx-m.sx,S.my-m.sy), dw=Math.hypot(S.pos.x-m.x,S.pos.z-m.z);
    const near=live&&m.vis>.6&&m.front&&(dm<90||dw<2.3||playing);
    if(near!==m.near){ m.near=near; m.btn.classList.toggle('near',near); }
    if(m.front) m.btn.style.transform='translate('+m.sx.toFixed(1)+'px,'+(m.sy-24).toFixed(1)+'px) translate(-50%,-100%)';
    if(playing&&clock>=m.nextRipple){ m.nextRipple=clock+.8; memRipple(m); }
    if(doHum){ if(!m.hum&&m.vis>.02) Au.addHum(m); Au.humTo(m,m.vis*(.004+.026*clamp(1-dw/5.5,0,1))*(S.playing?.25:1)); }
  }
}

/* ---------------- logo: particles that settle into the mark ---------------- */
const logoEl=$('#logo'), fx=$('#fx'), g2=fx.getContext('2d');
let logoAR=312/1686, CR={x:0,y:0,w:600,h:111}, LP=null, fxDirty=false, PRd=1, SIZE_ADJ=1;
logoEl.addEventListener('load',()=>{ if(logoEl.naturalWidth) logoAR=logoEl.naturalHeight/logoEl.naturalWidth; layout(); });
logoEl.src=LOGO_SRC;
function centerRect(){ const vw=innerWidth, vh=innerHeight, w=Math.min(vw*.72,880), h=w*logoAR; return {x:(vw-w)/2,y:vh*.46-h/2,w,h}; }
function headerRect(){ const r=$('#logoSlot').getBoundingClientRect(); return {x:r.left,y:r.top,w:r.height/logoAR,h:r.height}; }
function layout(){ CR=centerRect(); logoEl.style.width=CR.w+'px'; }
function placeLogo(){
  let r, op;
  if(S.phase==='gate'){ r=CR; op=0; }
  else if(S.phase==='live'){ r=headerRect(); op=1; }
  else { const tl=S.tl, H=headerRect(), m=easeIO(sstep(9.6,10.6,tl)); op=sstep(8.15,8.75,tl); r={x:lerp(CR.x,H.x,m),y:lerp(CR.y,H.y,m),w:lerp(CR.w,H.w,m)}; }
  logoEl.style.opacity=op;
  logoEl.style.transform='translate('+r.x.toFixed(2)+'px,'+r.y.toFixed(2)+'px) scale('+(r.w/CR.w).toFixed(4)+')';
}
function buildLP(){
  if(!logoEl.complete||!logoEl.naturalWidth) return;
  const m=MEMS[0], o=project(m.x,m.y,m.z)||{x:innerWidth/2,y:innerHeight/2};
  const R=centerRect(), w=Math.max(1,Math.round(R.w)), h=Math.max(1,Math.round(R.h));
  const c=document.createElement('canvas'); c.width=w; c.height=h;
  const cc=c.getContext('2d'); cc.drawImage(logoEl,0,0,w,h);
  let d; try{ d=cc.getImageData(0,0,w,h).data; }catch(e){ return; }
  let opaque=0; for(let i=3;i<d.length;i+=4) if(d[i]>120) opaque++;
  const step=Math.max(1,Math.sqrt(opaque/(coarse?1500:2600)));
  const P=[];
  for(let y=0;y<h;y+=step) for(let x=0;x<w;x+=step){
    const xi=Math.floor(x+Math.random()*step*.8), yi=Math.floor(y+Math.random()*step*.8);
    if(xi>=w||yi>=h) continue;
    const k=(yi*w+xi)*4;
    if(d[k+3]>120) P.push({tx:R.x+xi,ty:R.y+yi,r:d[k],g:d[k+1],b:d[k+2],ring:P.length%4,ang:Math.random()*TAU,j:Math.random(),delay:Math.random()*.35+(xi/w)*.3});
  }
  LP={ox:o.x,oy:o.y,P};
}
function drawLP(){
  if(fxDirty){ g2.clearRect(0,0,innerWidth,innerHeight); fxDirty=false; }
  if(!LP||S.phase!=='intro') return;
  const tb=S.tl-6.7; if(tb<0||tb>2.1) return;
  fxDirty=true;
  g2.globalCompositeOperation='lighter';
  const fade=sstep(0,.18,tb)*(1-sstep(1.55,2.05,tb)), grow=easeOut(clamp(tb/1,0,1));
  for(const p of LP.P){
    const rr=(26+p.ring*34+p.j*14)*grow+p.ring*10*tb;
    const bx=LP.ox+Math.cos(p.ang)*rr, by=LP.oy+Math.sin(p.ang)*rr*.6;
    const e=easeIO(clamp((tb-.32-p.delay)/.95,0,1)), sw=Math.sin(e*Math.PI)*(14+p.j*20);
    const x=lerp(bx,p.tx,e)+Math.cos(p.ang+1.3)*sw, y=lerp(by,p.ty,e)+Math.sin(p.ang+1.3)*sw;
    const a=fade*(.55+.45*e);
    g2.fillStyle='rgba('+(lerp(255,p.r,e)|0)+','+(lerp(176,p.g,e)|0)+','+(lerp(96,p.b,e)|0)+','+a.toFixed(3)+')';
    const s=1.1+(1-e)*1.1; g2.fillRect(x-s/2,y-s/2,s,s);
  }
  g2.globalCompositeOperation='source-over';
}

/* ---------------- timeline ---------------- */
const EVENTS=[
  {t:0,    always:true, f:()=>Au.startAmbient()},
  {t:2.75, f:()=>emitPulse()},
  {t:3.45, f:()=>emitPulse()},
  {t:4.0,  f:()=>emitOmni()},
  {t:5.95, f:()=>Au.sparkle(MEMS[0])},
  {t:6.1,  f:()=>memRipple(MEMS[0])},
  {t:6.45, f:()=>memRipple(MEMS[0])},
  {t:6.7,  f:()=>{ buildLP(); Au.swell(); memRipple(MEMS[0]); }},
  {t:9.9,  always:true, f:()=>body.classList.add('hud-on')},
  {t:10.8, always:true, f:()=>goLive()},
];
function runEvents(){ for(const e of EVENTS){ if(S.phase!=='intro') break; if(!e.fired&&S.tl>=e.t){ e.fired=true; e.f(); } } }
function jumpTo(t){ S.t0=clock-t; S.tl=t; LP=null; for(const e of EVENTS){ if(!e.fired&&e.t<=t){ e.fired=true; if(e.always) e.f(); } } }
function resetEvents(){ EVENTS.forEach(e=>e.fired=false); }

function start(withSound){
  if(S.phase!=='gate') return;
  if(withSound){ Au.init(); Au.setOn(true); }
  updateSoundBtn();
  const g=$('#gate'); g.classList.add('gone'); setTimeout(()=>{ g.hidden=true; },1500);
  S.phase='intro'; body.classList.remove('gate'); body.classList.add('intro');
  S.t0=clock; S.tl=0; resetEvents(); S.origin=[S.pos.x,S.pos.z];
  if(reduceMotion) jumpTo(10.75);
}
function goLive(){
  S.phase='live'; LP=null;
  body.classList.remove('intro'); body.classList.add('live','hud-on');
  S.nextPulse=clock+.5;
}
function replay(){
  stopMemory();
  S.phase='intro'; body.classList.remove('live','hud-on'); body.classList.add('intro');
  S.pos.x=START.x; S.pos.z=START.z; S.walk=null; S.heading=0; S.tHeading=0; S.headSrc='none';
  arcs.length=0; glPulses.length=0; LP=null; S.nextPulse=Infinity;
  S.t0=clock; S.tl=0; resetEvents(); EVENTS[0].fired=true; S.origin=[S.pos.x,S.pos.z];
  if(reduceMotion) jumpTo(10.75);
}

/* ---------------- input ---------------- */
function setPtr(e){ S.mx=e.clientX; S.my=e.clientY; S.ndcX=e.clientX/innerWidth*2-1; S.ndcY=-(e.clientY/innerHeight)*2+1; S.hasPtr=true; }
addEventListener('pointermove',e=>{ setPtr(e); if(e.pointerType==='mouse'||S.down) S.headSrc='mouse'; },{passive:true});
addEventListener('pointerdown',e=>{ if(e.target.closest&&e.target.closest('button')) return; setPtr(e); S.down={x:e.clientX,y:e.clientY,t:performance.now()}; S.headSrc='mouse'; });
addEventListener('pointerup',e=>{
  const d=S.down; S.down=null;
  if(!d||(e.target.closest&&e.target.closest('button'))) return;
  if(Math.hypot(e.clientX-d.x,e.clientY-d.y)<10&&performance.now()-d.t<650) tap(e.clientX,e.clientY);
});
document.addEventListener('pointerleave',()=>{ S.mx=S.my=-9999; });
function tap(x,y){
  if(S.phase!=='live') return;
  let best=null, bd=coarse?52:64;
  for(const m of MEMS){ if(m.vis<.6||!m.front) continue; const d=Math.hypot(x-m.sx,y-m.sy); if(d<bd){ bd=d; best=m; } }
  if(best){ if(S.playing===best) stopMemory(); else playMemory(best); return; }
  const h=floorHit(x/innerWidth*2-1,-(y/innerHeight)*2+1);
  if(h&&Math.abs(h.x)<7&&Math.abs(h.z)<6){
    const tx=clamp(h.x,ROOM.x0+.35,ROOM.x1-.35), tz=clamp(h.z,ROOM.z0+.35,ROOM.z1-.35);
    S.walk={x:tx,z:tz};
    spawnArc({kind:'ring',full:true,cx:tx,cz:tz,y:.03,ang:0,span:0,r0:.05,v:.9,life:.9,t0:clock,col:COOL,base:.9,size:1.1,seed:rs()});
  }
}
const KEYMAP={ArrowUp:'fwd',KeyW:'fwd',ArrowDown:'back',KeyS:'back',ArrowLeft:'left',KeyA:'left',ArrowRight:'right',KeyD:'right'};
addEventListener('keydown',e=>{
  if(S.phase==='gate'){ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); start(true); } return; }
  const onBtn=e.target.closest&&e.target.closest('button');
  const k=KEYMAP[e.code];
  if(k){ S.keys[k]=true; if(k==='left'||k==='right') S.headSrc='key'; e.preventDefault(); return; }
  if(e.code==='Space'&&!onBtn){ e.preventDefault(); if(S.phase==='live') emitPulse(); }
  if(e.code==='Enter'&&!onBtn&&S.phase==='live'){ const m=MEMS.find(m=>m.near); if(m) playMemory(m); }
  if(e.code==='KeyM') toggleSound();
});
addEventListener('keyup',e=>{ const k=KEYMAP[e.code]; if(k) S.keys[k]=false; });
addEventListener('blur',()=>{ S.keys={}; });

$('#enter').addEventListener('click',()=>start(true));
$('#enterQuiet').addEventListener('click',e=>{ e.stopPropagation(); start(false); });
$('#skip').addEventListener('click',()=>{ if(S.phase==='intro'&&S.tl<9.6) jumpTo(9.6); });
$('#btnReplay').addEventListener('click',replay);
$('#btnSound').addEventListener('click',toggleSound);
function toggleSound(){
  if(!Au.ctx) Au.init(); if(!Au.ctx) return;
  Au.setOn(!Au.on);
  if(!Au.on&&('speechSynthesis' in window)) speechSynthesis.cancel();
  updateSoundBtn();
}
function updateSoundBtn(){ const b=$('#btnSound'), on=Au.live(); b.dataset.on=String(on); b.querySelector('.lbl').textContent=on?'Sound on':'Sound off'; }
$('#hint').innerHTML = coarse
  ? 'Drag to turn your head. Tap the floor to walk.<br>Tap a warm light, a MeriMark, to hear what was saved there.'
  : 'Move the cursor to turn your head. Click the floor to walk.<br>Warm lights are MeriMarks. Get close and listen.';
const featA=$('#featA'), hexRot=$('#hexRot');
function pingHud(){ if(S.phase!=='live') return; featA.classList.remove('ping'); void featA.offsetWidth; featA.classList.add('ping'); }

function aim(){
  if(!S.hasPtr||S.headSrc!=='mouse') return;
  const h=floorHit(S.ndcX,S.ndcY);
  if(h){ const dx=h.x-S.pos.x, dz=h.z-S.pos.z; if(dx*dx+dz*dz>.05) S.tHeading=Math.atan2(dx,-dz); }
  else S.tHeading=S.ndcX*1.4;
}
function walk(dt){
  let vx=0, vz=0;
  if(S.keys.fwd||S.keys.back){ const s=(S.keys.fwd?1:0)-(S.keys.back?1:0); vx=Math.sin(S.heading)*s; vz=-Math.cos(S.heading)*s; S.walk=null; }
  else if(S.walk){ const dx=S.walk.x-S.pos.x, dz=S.walk.z-S.pos.z, d=Math.hypot(dx,dz); if(d<.06) S.walk=null; else { vx=dx/d; vz=dz/d; } }
  if(!vx&&!vz) return;
  const sp=1.5*dt, nx=S.pos.x+vx*sp, nz=S.pos.z+vz*sp;
  if(!blocked(nx,nz)){ S.pos.x=nx; S.pos.z=nz; }
  else if(!blocked(nx,S.pos.z)) S.pos.x=nx;
  else if(!blocked(S.pos.x,nz)) S.pos.z=nz;
  else S.walk=null;
}

/* ---------------- render ---------------- */
const PA=new Float32Array(24), PB=new Float32Array(24);
function bindLayout(){
  for(let i=0;i<4;i++) gl.enableVertexAttribArray(i);
  gl.vertexAttribPointer(0,3,gl.FLOAT,false,32,0); gl.vertexAttribPointer(1,3,gl.FLOAT,false,32,12);
  gl.vertexAttribPointer(2,1,gl.FLOAT,false,32,24); gl.vertexAttribPointer(3,1,gl.FLOAT,false,32,28);
}
function draw(reveal){
  gl.viewport(0,0,canvas.width,canvas.height);
  gl.clearColor(0,0,0,1); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.enable(gl.BLEND); gl.blendFunc(gl.ONE,gl.ONE);
  const px=canvas.height/(2*Math.tan(FOV/2))*.03*SIZE_ADJ;
  let u=PROOM.u;
  gl.useProgram(PROOM.p);
  gl.uniformMatrix4fv(u.uView,false,VIEW); gl.uniformMatrix4fv(u.uProj,false,PROJ);
  gl.uniform1f(u.uTime,clock); gl.uniform1f(u.uReveal,reveal); gl.uniform1f(u.uDim,S.dim);
  gl.uniform1f(u.uPx,px); gl.uniform1f(u.uMaxPt,MAXPT); gl.uniform2f(u.uOrigin,S.origin[0],S.origin[1]);
  gl.uniform4fv(u.uPA,PA); gl.uniform4fv(u.uPB,PB);
  gl.bindBuffer(gl.ARRAY_BUFFER,roomBuf); bindLayout(); gl.drawArrays(gl.POINTS,0,ROOM_N);
  u=PDYN.u;
  gl.useProgram(PDYN.p);
  gl.uniformMatrix4fv(u.uView,false,VIEW); gl.uniformMatrix4fv(u.uProj,false,PROJ);
  gl.uniform1f(u.uPx,px); gl.uniform1f(u.uDim,S.dim); gl.uniform1f(u.uMaxPt,MAXPT);
  if(wc>0){ gl.uniform1f(u.uGlow,0); gl.bindBuffer(gl.ARRAY_BUFFER,dynBuf); gl.bufferSubData(gl.ARRAY_BUFFER,0,dyn.subarray(0,wc*8)); bindLayout(); gl.drawArrays(gl.POINTS,0,wc); }
  if(gc>0){ gl.uniform1f(u.uGlow,1); gl.bindBuffer(gl.ARRAY_BUFFER,glowBuf); gl.bufferSubData(gl.ARRAY_BUFFER,0,glow.subarray(0,gc*8)); bindLayout(); gl.drawArrays(gl.POINTS,0,gc); }
}
function updateQuestion(){
  const q=$('#question');
  if(S.phase!=='intro'){ if(q.style.opacity!=='0') q.style.opacity='0'; return; }
  const t=S.tl;
  q.style.opacity=(sstep(.3,1.6,t)*(1-sstep(2,2.6,t))).toFixed(3);
  q.style.filter='blur('+((1-sstep(.3,1.8,t))*10).toFixed(2)+'px)';
  q.style.transform='translate(-50%,calc(-50% - '+(sstep(2,2.6,t)*8).toFixed(1)+'px))';
}
function resize(){
  PRd=Math.min(window.devicePixelRatio||1,2);
  const w=innerWidth, h=innerHeight;
  canvas.width=Math.round(w*PRd); canvas.height=Math.round(h*PRd);
  fx.width=canvas.width; fx.height=canvas.height; g2.setTransform(PRd,0,0,PRd,0,0);
  SIZE_ADJ=clamp(820/Math.min(w,h),.9,1.5);
  layout();
}
addEventListener('resize',resize); resize();

let last=performance.now();
function frame(now){
  const dt=Math.min(.05,Math.max(0,(now-last)/1000)); last=now; clock+=dt;
  if(S.phase!=='gate') S.tl=clock-S.t0;
  if(S.phase==='intro') runEvents();
  const tl=S.tl, intro=S.phase==='intro', live=S.phase==='live';
  S.lvis=live?1:intro?sstep(2.1,2.8,tl):0;
  S.dim=intro?clamp(1-.72*sstep(6.7,7.3,tl)+.72*sstep(9.6,10.6,tl),0,1):1;
  const reveal=live?40:intro?(tl<4?0:Math.min(40,.3+6.2*(tl-4))):0;

  updateCamera(dt);
  aim();
  if(S.keys.left) S.tHeading-=dt*2; if(S.keys.right) S.tHeading+=dt*2;
  S.heading+=angDiff(S.heading,S.tHeading)*(1-Math.exp(-dt*7));
  if(live){ walk(dt); if(clock>=S.nextPulse){ emitPulse(); S.nextPulse=clock+2; } }
  S.flash*=Math.exp(-dt*5);

  wc=0; gc=0;
  writeHead();
  updateMemories(dt);
  updateArcs();
  for(let i=glPulses.length-1;i>=0;i--) if(.35+WAVE_V*(clock-glPulses[i].t0)>8.5) glPulses.splice(i,1);
  for(let i=0;i<6;i++){
    const p=glPulses[i];
    if(p){ const r=.35+WAVE_V*(clock-p.t0); PA[i*4]=p.cx; PA[i*4+1]=p.cz; PA[i*4+2]=r; PA[i*4+3]=1-sstep(5.5,8,r); PB[i*4]=p.h; }
    else { PA[i*4+2]=-50; PA[i*4+3]=0; }
  }
  draw(reveal);
  drawLP();
  placeLogo();
  updateQuestion();
  hexRot.setAttribute('transform','rotate('+(S.heading*180/Math.PI).toFixed(1)+')');
  if(Au.ctx) Au.listener(S.pos.x,HEAD_Y,S.pos.z,S.heading);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
})();
