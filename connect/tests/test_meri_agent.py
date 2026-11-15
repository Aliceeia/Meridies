import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from connect import server
from connect.backend import gemini


def test_chat_filters_memories_before_retrieval(monkeypatch):
    records = [dict(id='a'*32, author_id='alice', building_id='building', visibility='private',
                    position={'x':0,'y':0,'z':0}, transcript={'text':'private chrysanthemum note'}),
               dict(id='b'*32, author_id='bob', building_id='building', visibility='community',
                    position={'x':0,'y':0,'z':0}, transcript={'text':'public chrysanthemum note'})]
    monkeypatch.setattr(server, 'all_memories', lambda: records)
    monkeypatch.setattr(server.desc, 'BUILDINGS', {'building': ('unused','interior')})
    monkeypatch.setattr(server.desc, 'load_plan', lambda _: {'building':{'name':'Test building'},'rooms':[]})
    prompts=[]
    monkeypatch.setattr(gemini, 'generate', lambda parts, **kwargs: prompts.append(parts[0]['text']) or 'Grounded response.')
    httpd=ThreadingHTTPServer(('127.0.0.1',0), server.Handler)
    threading.Thread(target=httpd.serve_forever,daemon=True).start()
    def call(question,viewer='visitor'):
        request=urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}/api/meri/chat',
            data=json.dumps({'question':question,'viewer':viewer}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as response:return json.load(response)
    try:
        result=call('chrysanthemum')
        assert [s['id'] for s in result['sources']]==['b'*32]
        assert 'private chrysanthemum' not in prompts[0]
        assert 'public chrysanthemum' in prompts[0]
        assert call('digital gallery')['navigate']=='/gallery'
        assert call('Hi Meri')['sources']==[]
        greeting=call('Hi Meri','alice')
        assert [r['id'] for r in greeting['sources']]==['a'*32]
        assert greeting['suggestions'][0]['question']=='hear my latest memory'
        assert call('hear my latest memory','alice')['audio']==f"/api/memories/{'a'*32}/audio"
        assert call('completely unmatched phrase')['sources']==[]
        assert call('hear my memories', 'alice')['audio']==f"/api/memories/{'a'*32}/audio"
    finally:
        httpd.shutdown();httpd.server_close()
