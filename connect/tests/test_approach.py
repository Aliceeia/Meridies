import io
from PIL import Image
from connect.backend import approach, meri_agent


def test_approach_uses_requested_model_and_image(monkeypatch):
    image=io.BytesIO();Image.new('RGB',(4,4)).save(image,'JPEG')
    monkeypatch.setattr(approach,'locate',lambda _: (34.,-118.))
    monkeypatch.setattr(approach.gemini,'secret',lambda _: 'test')
    monkeypatch.setattr(approach.describe,'aerial_crop',lambda *a,**k:image.getvalue())
    seen={}
    def generate(parts,**kwargs):
        seen.update(kwargs);assert '5836 La Mirada Ave' in parts[0]['text'];assert parts[1]['inline_data']['mime_type']=='image/jpeg'
        return 'The entrance is obscured. This is based on aerial imagery and may not reflect current conditions.'
    monkeypatch.setattr(approach.gemini,'generate',generate)
    assert 'aerial imagery' in approach.describe_approach('5836 La Mirada Ave')
    assert seen['model']=='gemini-3.8-flash' and seen['fallback'] is False


def test_fetch_and_model_failures_are_spoken_messages(monkeypatch):
    monkeypatch.setattr(approach.gemini,'secret',lambda _: 'test')
    monkeypatch.setattr(approach,'locate',lambda _: (_ for _ in ()).throw(RuntimeError('private service details')))
    assert approach.describe_approach('unknown')==approach.FAILURE


def test_front_door_dispatch_and_inside_followup(monkeypatch):
    plans={'building':{'building':{'id':'building','name':'5836 La Mirada Ave'},'rooms':[]}}
    monkeypatch.setattr(meri_agent.gemini,'secret',lambda _:None)
    seen=[]
    monkeypatch.setitem(meri_agent.TOOLS,'describe_approach',lambda address:seen.append(address) or 'Based on aerial imagery, the doorway is obscured.')
    reply=meri_agent.respond('Hi Meri, how do I get to the front door?','user-a',[],plans)
    assert seen==['5836 La Mirada Ave']
    assert 'Would you like to continue inside' in reply['text']
    history=[{'role':'user','text':'How do I get to the front door?'},{'role':'assistant','text':reply['text']}]
    assert meri_agent.respond('yes','user-a',[],plans,history)['navigate']=='/walk?building=building'
    assert meri_agent.respond(reply['suggestions'][0]['question'],'user-a',[],plans)['navigate']=='/walk?building=building'


def test_tool_failure_does_not_offer_unavailable_result(monkeypatch):
    plans={'b':{'building':{'id':'b','name':'5836 La Mirada Ave'}}}
    monkeypatch.setattr(meri_agent.gemini,'secret',lambda _:None)
    monkeypatch.setitem(meri_agent.TOOLS,'describe_approach',lambda _:approach.FAILURE)
    reply=meri_agent.respond('main entrance','a',[],plans)
    assert reply['text']==approach.FAILURE and reply['sources']==[]


def test_image_fetch_failure_and_gemini_failure(monkeypatch):
    monkeypatch.setattr(approach.gemini,'secret',lambda _: 'test')
    monkeypatch.setattr(approach,'locate',lambda _: (34.,-118.))
    def fail(*a,**k):
        raise OSError('service unavailable')
    monkeypatch.setattr(approach.describe,'aerial_crop',fail)
    assert approach.describe_approach('5836 La Mirada Ave')==approach.FAILURE
    image=io.BytesIO();Image.new('RGB',(4,4)).save(image,'JPEG')
    monkeypatch.setattr(approach.describe,'aerial_crop',lambda *a,**k:image.getvalue())
    monkeypatch.setattr(approach.gemini,'generate',fail)
    assert approach.describe_approach('5836 La Mirada Ave')==approach.FAILURE
