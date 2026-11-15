"use strict";
(() => {
 const $=id=>document.getElementById(id), user=()=>localStorage.getItem('meridies.user')||'user-a';
 let memoryAudio=null, history=[], historyViewer=user();
 let active=false,awake=false,stream=null,recognizer=null,ctx=null,processor=null,source=null,sink=null,request=null,recording=null,parts=[],silence=0,started=0,pending=false;
 Speech.useVoice=true;
 const bars=[];
 for(let i=0;i<81;i++){
  const bar=document.createElement('i');bar.style.setProperty('--peak',`${14+18*Math.sin(i*.3)**2}px`);bar.style.setProperty('--delay',`${-i*.06}s`);$('wave').append(bar);bars.push(bar);
 }
 function layoutArc(){const box=$('wave').getBoundingClientRect();bars.forEach((bar,i)=>{const angle=(200+i*320/80)*Math.PI/180;bar.style.left=`${box.width/2+Math.cos(angle)*(box.width/2-12)}px`;bar.style.top=`${box.height/2+Math.sin(angle)*(box.height/2-12)}px`;bar.style.setProperty('--angle',`${angle*180/Math.PI+90}deg`);});}
 layoutArc();window.addEventListener('resize',layoutArc);
 setInterval(()=>document.body.classList.toggle('speaking',!!Speech.current || !!(memoryAudio && !memoryAudio.paused)),120);
 const status=text=>$('status').textContent=text;
 const speak=text=>Speech.say(text,{kind:'help',force:true});
 document.addEventListener('speech-unavailable',()=>status('Gemini speech is unavailable. You can still read the response.'));
 async function ask(text){
  text=text.trim();if(!text)return;
  if(historyViewer!==user()){history=[];historyViewer=user();}
  if(request)request.abort();request=new AbortController();const own=request;
  Speech.cancel();memoryAudio?.pause();document.body.classList.add('working');status(`Heard: ${text}`);
  try{
   const response=await fetch('/api/meri/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:text,viewer:user(),history}),signal:own.signal});
   const result=await response.json();if(!response.ok)throw Error(result.error||'Meri could not answer');
   if(own!==request)return;
   history.push({role:'user',text},{role:'assistant',text:result.text});history=history.slice(-8);
   $('answer').textContent=result.text;
   $('suggestions').replaceChildren();
   for(const item of result.suggestions||[]){const button=document.createElement('button');button.textContent=item.label;button.onclick=()=>ask(item.question);$('suggestions').append(button);}
   $('sources').textContent=result.sources?.length?`Based on ${result.sources.length} accessible memory or scene records.`:'';
   if(result.navigate){stop();location.assign(result.navigate);return;}
   if(result.audio){const audio=memoryAudio=new Audio(`${result.audio}?viewer=${encodeURIComponent(user())}`);audio.play().catch(()=>status('Tap the page to allow audio.'));}
   else speak(result.text);
   status(active?'Listening. Say “digital gallery” to open your memories.':'Enable the microphone to talk with Meri.');
  }catch(error){if(error.name!=='AbortError'){status(error.message);$('answer').textContent='Please try again.';}}
  finally{if(own===request)document.body.classList.remove('working');}
 }
 function heard(raw){
  let text=raw.trim();if(/^(digital gallery|数字画廊)[.!]?$/i.test(text)){awake=true;ask(text);return;}const wake=/\b(?:hi|hey|hello)\s+(?:meri|mary|merry|marie)\b/i;
  if(wake.test(text)){awake=true;text=text.replace(wake,'').trim();if(!text)text='Hi Meri';}
  else if(!awake)return;
  if(/^(stop|be quiet|pause)[.!]?$/i.test(text)){Speech.cancel();memoryAudio?.pause();request?.abort();return;}
  if(Speech.current && !wake.test(raw))return;
  ask(text);
 }
 async function transcribe(blob){
  if(!active||pending||blob.size<100)return;pending=true;
  try{const response=await fetch('/api/hear',{method:'POST',headers:{'Content-Type':blob.type},body:blob});if(!response.ok)throw Error('Could not transcribe');const result=await response.json();if(active&&result.text)heard(result.text);}
  catch(_){if(active)status('Could not hear that. Please try again, or type below.');}finally{pending=false;}
 }
 async function enable(){
  if(active){stop();return;}
  try{
   stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});active=true;
   $('mic').textContent='Microphone on';$('mic').setAttribute('aria-pressed','true');status('Listening for “Hi Meri”.');
   ctx=new AudioContext();await ctx.resume();source=ctx.createMediaStreamSource(stream);processor=ctx.createScriptProcessor(2048,1,1);sink=ctx.createGain();sink.gain.value=0;source.connect(processor);processor.connect(sink);sink.connect(ctx.destination);
   const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(m=>MediaRecorder.isTypeSupported(m));
   processor.onaudioprocess=event=>{
    if(!active)return;const samples=event.inputBuffer.getChannelData(0);let sum=0;for(const value of samples)sum+=value*value;const level=Math.sqrt(sum/samples.length);
    [...$('wave').children].forEach((bar,i)=>bar.style.height=`${6+Math.min(1,level*18)*70*Math.sin((i+1)/46*Math.PI)}px`);
    if(recognizer || Speech.current || pending)return;
    if(!recording && level>.009){parts=[];recording=new MediaRecorder(stream,mime?{mimeType:mime}:{});recording.ondataavailable=e=>{if(e.data.size)parts.push(e.data)};recording.onstop=()=>transcribe(new Blob(parts,{type:recording?.mimeType||mime||'audio/webm'}));recording.start();started=performance.now();silence=0;}
    if(recording){silence=level<.006?silence+2048/ctx.sampleRate:0;if(silence>.55||performance.now()-started>6000){const capture=recording;recording=null;capture.stop();}}
   };
   const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
   if(SR){recognizer=new SR();recognizer.lang='en-US';recognizer.continuous=true;recognizer.onresult=e=>{for(let i=e.resultIndex;i<e.results.length;i++)if(e.results[i].isFinal)heard(e.results[i][0].transcript)};recognizer.onend=()=>{if(active&&recognizer)setTimeout(()=>{try{recognizer?.start()}catch(_){}},300)};recognizer.onerror=e=>{if(['network','not-allowed','service-not-allowed'].includes(e.error)){const previous=recognizer;recognizer=null;previous.abort();status('Listening using microphone transcription. Say “Hi Meri”.');}};try{recognizer.start()}catch(_){recognizer=null;}}
  }catch(_){stop();status('Microphone access is blocked. Allow permission, or type below.');}
 }
 function stop(){memoryAudio?.pause();active=false;awake=false;recognizer?.abort();recognizer=null;if(recording?.state==='recording')recording.stop();recording=null;stream?.getTracks().forEach(t=>t.stop());processor?.disconnect();source?.disconnect();sink?.disconnect();ctx?.close();$('mic').textContent='Enable microphone';$('mic').setAttribute('aria-pressed','false');}
 $('mic').onclick=enable;
 $('ask').onsubmit=event=>{event.preventDefault();awake=true;ask($('question').value);};
 window.addEventListener('pagehide',stop);
})();
