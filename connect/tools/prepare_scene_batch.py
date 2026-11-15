"""Resume-safe Gemini batch generation of public scene audio only.

--submit queues missing clips; --collect imports finished results; --watch waits.
Batch jobs use Google's documented independent batch quota, not live TTS quota.
"""
import argparse
import base64
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from connect.backend import voice, gemini
from connect.tools.prepare_scene_samples import parts

STATE = ROOT / 'connect/data/cache/scene-batch.json'
API = 'https://generativelanguage.googleapis.com/v1beta/'


def call(path, body=None):
    req = urllib.request.Request(API + path, method='POST' if body is not None else 'GET',
        headers={'x-goog-api-key':gemini.api_key(),'Content-Type':'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as error:
        message=json.loads(error.read()).get('error',{}).get('message','')
        raise RuntimeError(f'HTTP {error.code}: '+message.replace(gemini.api_key(),'[redacted]')) from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--submit', action='store_true')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--model', default='gemini-3.1-flash-tts-preview')
    args = parser.parse_args()
    library=json.loads((ROOT/'connect/frontend/scene-samples.json').read_text(encoding='utf-8'))
    phrases=sorted({s for b in library['buildings'].values() for p in b['positions'].values()
                    for e in p.values() for t in e.values() for s in parts(t)})
    state=json.loads(STATE.read_text()) if STATE.exists() else {'jobs':[]}
    if args.submit:
        outstanding={key for job in state['jobs'] if not job.get('collected') for key in job['texts']}
        missing=[t for t in phrases if not voice.path_for(t,'calm','Sulafat').exists()
                 and voice.key(t,'calm','Sulafat') not in outstanding]
        if args.limit:
            missing=missing[:args.limit]
        if missing:
            texts={voice.key(t,'calm','Sulafat'):t for t in missing}
            requests=[{'metadata':{'key':key},'request':{
                'contents':[{'parts':[{'text':text}]}],
                'generationConfig':{'responseModalities':['AUDIO'],'speechConfig':{
                    'voiceConfig':{'prebuiltVoiceConfig':{'voiceName':'Sulafat'}}}}}}
                for key,text in texts.items()]
            result=call('models/'+args.model+':batchGenerateContent',{'batch':{
                'display_name':'Meridies public scene samples',
                'input_config':{'requests':{'requests':requests}}}})
            state['jobs'].append({'name':result['name'],'texts':texts,'collected':False})
            STATE.parent.mkdir(parents=True,exist_ok=True)
            STATE.write_text(json.dumps(state),encoding='utf-8')
            print(json.dumps({'submitted':len(texts),'job':result['name']}),flush=True)
    while args.collect or args.watch:
        waiting=False
        for job in state['jobs']:
            if job.get('collected'):
                continue
            result=call(job['name'])
            status=result.get('metadata',{}).get('state')
            print(json.dumps({'job':job['name'],'state':status,'done':result.get('done',False)}),flush=True)
            if not result.get('done'):
                waiting=True
                continue
            response=result.get('response',{})
            inline=response.get('inlinedResponses',{})
            entries=inline if isinstance(inline,list) else inline.get('inlinedResponses',[])
            if not entries and not result.get('error'):
                raise RuntimeError('Finished batch has no inline results; refusing to mark it collected')
            ready=rejected=failed=0
            for entry in entries:
                key=entry.get('metadata',{}).get('key')
                text=job['texts'].get(key)
                if not text or entry.get('error'):
                    failed+=1;continue
                try:
                    part=next(p['inlineData'] for p in entry['response']['candidates'][0]['content']['parts'] if 'inlineData' in p)
                    mime=part.get('mimeType','')
                    rate=int(mime.split('rate=')[1].split(';')[0]) if 'rate=' in mime else voice.RATE
                    wav=voice.wav(base64.b64decode(part['data']),rate)
                    if not voice.plausible(text,wav):
                        rejected+=1;continue
                    voice._write(voice.path_for(text,'calm','Sulafat'),wav);ready+=1
                except (KeyError,StopIteration,ValueError):
                    failed+=1
            job.update(collected=True,state=status,ready=ready,rejected=rejected,failed=failed)
            # Retain status/errors, never huge base64 response payloads.
            if result.get('error'):
                job['error']=result['error']
            STATE.write_text(json.dumps(state),encoding='utf-8')
            print(json.dumps({k:v for k,v in job.items() if k!='texts'}),flush=True)
        if not args.watch or not waiting:
            break
        time.sleep(45)
    ready=sum(voice.path_for(t,'calm','Sulafat').exists() for t in phrases)
    print(json.dumps({'ready':ready,'total':len(phrases)}),flush=True)


if __name__=='__main__':
    main()
