import copy
import json
from test_app import setup,ID
from entity_model import output_version
from processing_bridge import ProcessingRejected


def two_versions(setup):
    app,root,first=setup;second=copy.deepcopy(first);second['published_at']+=1
    second['processing']={'attempt_id':'d'*32,'task_id':'c'*32};second['media_directory']='d'*32;second['artifact_directory']='d'*32
    second['clips'][0]['original_start_seconds']=7;second['clips'][0]['original_end_seconds']=17
    return app,root,first,second,output_version(ID,first['published_at']),output_version(ID,second['published_at'])


class Server:
    def __init__(self,data):self.data,self.calls,self.evicted=data,[],False
    def call(self,path,body=None,**kwargs):
        self.calls.append((path,body,kwargs))
        if path.endswith('/versions'):return {'items':[dict(id=output_version(ID,self.data['published_at']),source_sha256=ID,attempt_id='d'*32,task_id='c'*32,source_id='e'*32,manifest=self.data,media_state='staged_candidate')]}
        if self.evicted:raise ProcessingRejected(409,{'error':'media_already_evicted'})
        return {'media_state':'staged_candidate','acknowledged':True}


def test_candidate_import_does_not_activate_and_media_requires_confirmed_lease(setup):
    app,root,a,b,aid,bid=two_versions(setup);server=Server(b);app.extensions['processing_client']=server;c=app.test_client();headers={'X-Tennis-Request':'1'}
    r=c.post(f'/api/processing/videos/{ID}/versions/import',json={},headers=headers);assert r.status_code==200,r.json
    with app.extensions['entity_catalog'].db() as db:assert db.execute('SELECT current_version_id FROM videos WHERE id=?',(ID,)).fetchone()[0]==aid
    directory=root/'media'/('d'*32)/'Clips';directory.mkdir(parents=True);(directory/'clip-001.mp4').write_bytes(b'staged clip')
    url=f'/media/versions/{bid}/Clips/clip-001.mp4'
    assert c.get(url).status_code==409
    assert c.get(url+'?lease_id='+'1'*32).data==b'staged clip'
    server.evicted=True;assert c.get(url+'?lease_id='+'1'*32).status_code==409
    assert c.get(f'/media/versions/{aid}/../private.mov').status_code==404


def test_saved_preference_is_evidence_and_revision_conflicts_preserve_winner(setup):
    app,root,a,b,aid,bid=two_versions(setup);app.extensions['entity_catalog'].admit_version(b);c=app.test_client();headers={'X-Tennis-Request':'1'};url=f'/api/processing/videos/{ID}/comparisons/cmp_'+('1'*32)
    body=dict(version=0,version_a_id=aid,version_b_id=bid,preferred_version_id=bid,note='More rally context',retain_versions=[])
    first=c.put(url,json=body,headers=headers);assert first.status_code==200 and first.json['training_label_status']=='not_released'
    assert c.put(url,json=dict(body,note='Stale update'),headers=headers).status_code==409
    listing=c.get(f'/api/processing/videos/{ID}/comparisons').json['items'];assert listing[0]['note']=='More rally context' and listing[0]['version']==1
    with app.extensions['entity_catalog'].db() as db:assert db.execute('SELECT current_version_id FROM videos WHERE id=?',(ID,)).fetchone()[0]==aid


def test_failed_pin_cannot_claim_retention_was_saved(setup):
    app,root,a,b,aid,bid=two_versions(setup);app.extensions['entity_catalog'].admit_version(b);server=Server(b);server.evicted=True;app.extensions['processing_client']=server;c=app.test_client()
    r=c.put(f'/api/processing/videos/{ID}/comparisons/cmp_'+('2'*32),json=dict(version=0,version_a_id=aid,version_b_id=bid,preferred_version_id=bid,note='',retain_versions=[bid]),headers={'X-Tennis-Request':'1'})
    assert r.status_code==409
    assert c.get(f'/api/processing/videos/{ID}/comparisons').json['items']==[]
