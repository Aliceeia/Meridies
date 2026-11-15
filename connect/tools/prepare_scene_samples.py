"""Prepare public scene narration, then optionally synthesize missing Gemini audio.

python connect/tools/prepare_scene_samples.py --generate
Safe to resume: existing Sulafat clips are reused; short limits honor RetryInfo.
Stops on confirmed daily quota or persistent failures. --workers 3 bounds parallel work.
No user memories, transcripts or circle information enter this public library.
"""
import argparse
import json
import re
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from connect.backend import describe, spatial, voice


def parts(text):
    return [s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s]


def build():
    random.seed(0)
    spatial._last.clear()
    library = {'version': 1, 'voice': 'Sulafat', 'headingStep': 15, 'buildings': {}}
    for bid in describe.BUILDINGS:
        plan = describe.load_plan(bid)
        building = {'kind': plan['kind'], 'positions': {}}
        library['buildings'][bid] = building
        if plan['kind'] != 'interior':
            for i, zone in enumerate(plan.get('zones', [])):
                name = zone.get('name', 'outdoor area')
                building['positions'][str(i)] = {'0': {
                    'where': f"You're in {name}.",
                    'more': f"This is {name}, at {plan['building']['name']}. This outdoor sample describes the area, not your exact facing direction."
                }}
            continue
        for sweep in plan['sweeps']:
            position = sweep['position']
            headings = {}
            building['positions'][str(sweep['index'])] = headings
            for heading in range(0, 360, 15):
                result = spatial.around(plan, position['x'], position['y'], heading, [], '', lambda _: '', sweep.get('floor', 0))
                where = result.get('room_line') or f"You're at {plan['building']['name']}."
                more = list(result.get('hazard_lines') or []) + [where]
                room = spatial.room_at(plan, position['x'], position['y'], sweep.get('floor', 0))
                if room and room.get('polygon'):
                    polygon = room['polygon']
                    area = abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(polygon,polygon[1:]+polygon[:1]))) / 2
                    if area >= 1:
                        more.append(f"The mapped floor area of this room is about {round(area)} square metres.")
                if len(more) == 1:
                    more.append('There are no additional mapped details at this spot.')
                headings[str(heading)] = {'where': where, 'more': ' '.join(more)}
    return library


def generate_sentence(text):
    # Separate processes keep the driver's error/cooldown state local to a request.
    for attempt in range(6):
        try:
            voice.ensure(text, 'calm', 'Sulafat', gemini_only=True)
            return None
        except voice.GeminiQuotaError as error:
            if error.daily or error.retry_after > 60 or attempt == 5:
                return 'daily quota' if error.daily else 'quota'
            print(f'Short-term speech limit: waiting {error.retry_after:.0f}s before retry.', flush=True)
            time.sleep(error.retry_after + 1)
        except Exception as error:
            return type(error).__name__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    library = build()
    manifest = ROOT / 'connect/frontend/scene-samples.json'
    manifest.write_text(json.dumps(library, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    phrases = sorted({sentence for building in library['buildings'].values()
                      for position in building['positions'].values() for entry in position.values()
                      for text in entry.values() for sentence in parts(text)})
    existing = sum(voice.path_for(t, 'calm', 'Sulafat').exists() for t in phrases)
    positions = sum(len(b['positions']) for b in library['buildings'].values())
    combinations = sum(len(p) for b in library['buildings'].values() for p in b['positions'].values())
    print(f'Manifest: {positions} positions, {combinations} position/facing combinations, {len(phrases)} unique sentences; {existing} cached.', flush=True)
    quota = False
    target = ROOT / 'connect/data/cache/scene-samples-progress.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    def report_progress():
        ready = sum(voice.path_for(t, 'calm', 'Sulafat').exists() for t in phrases)
        report = {'sentences': len(phrases), 'ready': ready, 'missing': len(phrases)-ready, 'quotaReached': quota}
        target.write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    report_progress()
    if args.generate:
        missing = iter(t for t in phrases if not voice.path_for(t, 'calm', 'Sulafat').exists())
        stopped = False
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            pending = set()
            def submit_next():
                text = next(missing, None)
                if text is not None:
                    pending.add(pool.submit(generate_sentence, text))
            for _ in range(args.workers):
                submit_next()
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    error = future.result()
                    if error:
                        stopped = True
                        quota = quota or 'quota' in error
                        print(f'STOP: {error}; no new work will be submitted. Generation can be resumed.', flush=True)
                    else:
                        report = report_progress()
                        print(f"Ready: {report['ready']}/{len(phrases)}", flush=True)
                if not stopped:
                    for _ in done:
                        submit_next()
    print(json.dumps(report_progress()), flush=True)


if __name__ == '__main__':
    main()
