"""Explicitly public, scripted demonstration copies; never changes source memories."""
import copy
import uuid
from datetime import datetime, timedelta, timezone

NS = uuid.UUID('e5894f80-46af-4cd5-8241-177ad564a44d')


def build_demo_samples(source_records, public_records, media_root, load_plan):
    from connect.tools.seed_samples import SAMPLES, sample_id
    existing = {m['id']: m for m in source_records}
    included = {m['id'] for m in public_records}
    additions, assets = [], {}
    for author, bid, room, key, _visibility, days, text in SAMPLES:
        source = existing.get(sample_id(key))
        if not source:
            continue
        transcript = source.get('transcript') or {}
        if not (source.get('is_sample') is True and transcript.get('source') == 'script'
                and transcript.get('text') == text and source.get('author_id') == author
                and source.get('building_id') == bid):
            continue
        audio = (media_root / source.get('audio_file', '')).resolve()
        if not audio.is_relative_to(media_root.resolve()) or not audio.is_file():
            continue
        plan = load_plan(bid)
        sweep = next((s for s in plan['sweeps'] if s['index'] == source.get('sweep')), None)
        if sweep is None:
            continue
        # Already explicitly public samples are labeled in the export only.
        if source['id'] in included:
            for record in public_records:
                if record['id'] == source['id']:
                    record.update(is_public_demo=True, demo_shared=author != 'user-a')
            continue
        mid = uuid.uuid5(NS, key).hex
        meta = dict(id=mid, author_id=author, building_id=bid, visibility='community',
                    position=copy.deepcopy(sweep['position']), sweep=sweep['index'],
                    heading_deg=float(source.get('heading_deg', 0)), floor=sweep.get('floor', 0),
                    created_at=(datetime(2026,11,15,tzinfo=timezone.utc)-timedelta(days=days)).isoformat(),
                    audio_file=mid+audio.suffix, audio_type=source['audio_type'],
                    transcript={'status':'done','text':text,'source':'public-demo-script'},
                    description={'text':f'A scripted demonstration memory in {room.replace("hazard:", "the area near the ")}.',
                                 'model':'public-demo-script','images':[]},
                    draft=False, is_sample=True, is_public_demo=True, demo_shared=author != 'user-a')
        additions.append(meta)
        meta['position'].setdefault('z', 1.5)
        assets[mid] = {'audio':audio,'building':bid,'sweep':sweep['index'],'heading':meta['heading_deg']}
    return additions, assets
