"""Repair double-wrapped Gemini WAV caches, preserving the original WAV and provenance.

Default: inspect only. --apply: back up each broken wrapper and restore its inner WAV.
Only system speech is inspected; user memory recordings are never changed.
"""
import argparse
import io
import json
import shutil
import sys
import time
import wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from connect.backend import voice


def unwrap(data):
    result=data
    for _ in range(4):
        with wave.open(io.BytesIO(result),'rb') as audio:
            payload=audio.readframes(audio.getnframes())
        if payload[:4]!=b'RIFF' or payload[8:12]!=b'WAVE':
            return result
        # Verify that this is a complete nested container, not a PCM coincidence.
        if int.from_bytes(payload[4:8],'little')+8 != len(payload):
            raise ValueError('Nested WAV length mismatch')
        with wave.open(io.BytesIO(payload),'rb') as audio:
            if audio.getnframes()<=0:
                raise ValueError('Empty inner WAV')
        result=payload
    raise ValueError('Too many nested WAV containers')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    backup=voice.CACHE.parent/'voice-container-backup'/str(time.time_ns())
    repaired=[];checked=0
    for path in voice.CACHE.rglob('*.wav'):
        original=path.read_bytes();correct=unwrap(original);checked+=1
        if correct==original:continue
        repaired.append(str(path.relative_to(voice.CACHE)))
        if args.apply:
            target=backup/path.relative_to(voice.CACHE);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)
            voice._write(path,correct)
    report={'checked':checked,'affected':len(repaired),'applied':args.apply,'files':repaired}
    if args.apply:
        target=backup/'report.json';target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,indent=2))
    print(json.dumps({'checked':checked,'affected':len(repaired),'applied':args.apply,'backup':str(backup) if args.apply else None}),flush=True)


if __name__=='__main__':main()
