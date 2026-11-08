import copy
import json
from test_app import setup,ID,CLIP
from entity_model import output_version
from processing_bridge import ProcessingUnavailable


def review_setup(setup):
    app,root,data=setup;data['published_at']+=1;data['processing']={'task_id':'c'*32,'attempt_id':'d'*32}
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data));client=app.test_client()
    r=client.post('/api/processing-annotations',json=dict(source_id=ID,publication=data['published_at'],kind='cut_feedback',target=dict(scope='clip',clip_id=CLIP,start_seconds=5,end_seconds=15),body={'reasons':['end_early'],'suggested_end_ms':16000},request_id='selected-review'),headers={'X-Tennis-Request':'1'})
    assert r.status_code==201,r.json
    record=r.json.get('annotation',r.json)
    body=dict(video_version_id=output_version(ID,data['published_at']),evidence_revisions=[dict(annotation_id=record['id'],version=record['version'])],request_id='rerun-request-id',confirmed_commands={'keep_intervals_ms':[],'exclude_intervals_ms':[],'override_clip_bounds_ms':[dict(clip_id=CLIP,original_start_ms=5000,original_end_ms=16000,source_annotation_id=record['id'],source_revision=record['version'])]})
    return app,client,data,record,body


class Server:
    def __init__(self,data):self.data,self.calls=data,[]
    def call(self,path,body=None,**kwargs):
        self.calls.append((path,body,kwargs))
        if path.endswith('/versions'):return {'items':[dict(id=output_version(ID,self.data['published_at']),attempt_id='d'*32,task_id='c'*32,source_id='e'*32,manifest=self.data)]}
        if path=='/feedback-snapshots':return {'snapshot_id':'snapshot-one'}
        return {'task_id':'candidate-task','status':'queued'}


def test_explicit_snapshot_preserves_review_revision_and_never_activates(setup):
    app,c,data,record,body=review_setup(setup);server=Server(data);app.extensions['processing_client']=server
    preview=c.post(f'/api/processing/videos/{ID}/reprocess-preview',json=body,headers={'X-Tennis-Request':'1'})
    assert preview.status_code==200 and preview.json['requires_confirmation'] is True
    assert server.calls==[]
    r=c.post(f'/api/processing/videos/{ID}/reprocess',json=body,headers={'X-Tennis-Request':'1'})
    assert r.status_code==201,r.json
    snapshot=server.calls[1][1]
    assert snapshot['selected_evidence_revisions'][0]['body']['suggested_end_ms']==16000
    assert snapshot['base_attempt_id']=='d'*32 and snapshot['source_sha256']==ID
    assert server.calls[2][1]=={'feedback_snapshot_id':'snapshot-one','publication_policy':'never'}
    assert server.calls[1][2]['key']=='rerun-request-id-snapshot'
    assert not any('activate' in call[0] for call in server.calls)


def test_changed_review_and_unverified_history_cannot_queue_task(setup):
    app,c,data,record,body=review_setup(setup);server=Server(data);app.extensions['processing_client']=server
    body['evidence_revisions'][0]['version']+=1
    assert c.post(f'/api/processing/videos/{ID}/reprocess',json=body,headers={'X-Tennis-Request':'1'}).status_code==409
    assert not server.calls
    body['evidence_revisions'][0]['version']-=1
    server.data=copy.deepcopy(data);server.data['clips'][0]['original_end_seconds']=14
    assert c.post(f'/api/processing/videos/{ID}/reprocess',json=body,headers={'X-Tennis-Request':'1'}).status_code==409
    assert len(server.calls)==1


def test_offline_server_returns_error_without_background_submission(setup):
    app,c,data,record,body=review_setup(setup)
    class Offline:
        def call(self,*args,**kwargs):raise ProcessingUnavailable('Offline')
    app.extensions['processing_client']=Offline()
    response=c.post(f'/api/processing/videos/{ID}/reprocess',json=body,headers={'X-Tennis-Request':'1'})
    assert response.status_code==503
