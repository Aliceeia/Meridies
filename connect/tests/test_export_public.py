import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from connect.tools import export_web as exporter


def record(mid, visibility='community', **extra):
    return dict(id=mid*32, author_id='user-a', building_id='zvKHR9DHNjJ', visibility=visibility,
                position={'x':0,'y':0,'z':1.5}, created_at='2026-11-15T00:00:00+00:00',
                audio_file=f'{mid*32}.webm', audio_type='audio/webm', **extra)


def test_public_filter_removes_sensitive_metadata_and_unapproved_records():
    public=record('a',reports=[{'secret':'report'}],decisions=['secret'],audioId='internal')
    inputs=[public,record('b','private'),record('c','family'),record('d',draft=True),
            record('e',share_request={'status':'pending_review'}),record('f',share_request={'status':'rejected'})]
    result=exporter.public_memories(inputs)
    assert len(result)==1 and result[0]['id']=='a'*32
    assert not {'reports','decisions','audioId','share_request'} & result[0].keys()
    assert len(inputs)==6 and 'reports' in public


def test_snapshot_apis_only_see_public_records_and_empty_relationships(tmp_path):
    records=exporter.public_memories([record('a'),record('b','private'),record('c','family')])
    original=exporter.srv.all_memories
    with exporter.public_snapshot(records):
        exporter.snapshot_api(tmp_path)
    assert exporter.srv.all_memories is original
    for path in (tmp_path/'data/api').glob('*.json'):
        text=path.read_text(encoding='utf-8')
        assert 'b'*32 not in text and 'c'*32 not in text
    circle=json.loads((tmp_path/'data/api'/exporter.snap_key('/api/circle',{'viewer':'user-a'})).read_text())
    assert circle['members']==[] and circle['in_circles_of']==[]
    family=json.loads((tmp_path/'data/api'/exporter.snap_key('/api/family-link',{'viewer':'user-a'})).read_text())
    assert family['token'] is None


def test_packagers_reject_nonpublic_data_before_writing(tmp_path):
    for packager in [exporter.copy_media,exporter.build_function]:
        with pytest.raises(ValueError,match='non-public'):
            packager(tmp_path,[record('a','private')])
    assert list(tmp_path.iterdir())==[]


def test_voice_cache_allowlist_never_contains_unrelated_private_text(tmp_path,monkeypatch):
    (tmp_path/'scene-samples.json').write_text(json.dumps({'buildings':{'b':{'positions':{'0':{'0':{'where':'Public room.','more':'Public doorway.'}}}}}}))
    monkeypatch.setattr(exporter,'FRONT',tmp_path)
    allowed=exporter.public_voice_keys([record('a',transcript={'text':'Public recording.'})])
    key=lambda text:exporter.voice.key(text,'calm',exporter.voice.DEFAULT_VOICE)
    assert key('Public room.') in allowed and key('Public recording.') in allowed
    assert key('Private diary with a secret address.') not in allowed


def test_demo_copies_only_verified_scripts_without_mutating_source(tmp_path):
    import copy
    from connect.tools.demo_export import build_demo_samples
    from connect.tools.seed_samples import SAMPLES, sample_id
    author,bid,room,key,vis,days,text=next(s for s in SAMPLES if s[3]=='p-alone')
    mid=sample_id(key)
    (tmp_path/(mid+'.ogg')).write_bytes(b'scripted test audio')
    source=dict(id=mid,author_id=author,building_id=bid,visibility='private',is_sample=True,
                audio_file=mid+'.ogg',audio_type='audio/ogg',sweep=7,heading_deg=0,
                transcript={'source':'script','text':text},description={'text':'DO NOT EXPORT THIS PRIVATE EDIT'})
    before=copy.deepcopy(source)
    plan=lambda _: {'sweeps':[{'index':7,'position':{'x':1,'y':2},'floor':0}]}
    added,assets=build_demo_samples([source],[],tmp_path,plan)
    assert source==before
    assert len(added)==1 and added[0]['id']!=mid
    assert added[0]['visibility']=='community' and added[0]['is_public_demo'] is True
    assert mid not in json.dumps(added)
    assert 'DO NOT EXPORT' not in json.dumps(added)
    assert added[0]['id'] in assets
    source['transcript']['text']='user changed this recording'
    assert build_demo_samples([source],[],tmp_path,plan)==([],{})
