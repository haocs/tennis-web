import copy
import hashlib
import json
import pytest
from cut_diagnostics import validate
from entity_model import output_version
from test_app import setup,ID,CLIP


def diagnostics_for(data):
    provenance={'cache_schema_version':1,'source_sha256':ID,'proxy_spec':{'target_fps':8},'detector_spec':{'backend':'synthetic'},'extractor_code_digest':'f'*64,'roi_and_filter_config':{'target_region':[0,0,1,1]}}
    key=hashlib.sha256(json.dumps(provenance,sort_keys=True,separators=(',',':')).encode()).hexdigest();provenance['extraction_cache_key']=key
    hint={'hint_id':'tail','code':'person_track_lost_near_end','original_start_ms':12000,'original_end_ms':15000,'candidate_end_ms':12000,'summary':'Person track disappeared near the end','mapped_reasons':['end_late','lost_player']}
    return dict(schema_version=1,source_sha256=ID,source_duration_ms=20000,video_version_id=output_version(ID,data['published_at']),published_at=data['published_at'],truth_status='heuristic_only_not_play_or_swing_truth',training_label_status='not_released',
        generator={'kind':'worker_heuristic_v1','truth_status':'heuristic_only_not_play_or_swing_truth','training_label_status':'not_released','benchmark_truth':False,'verified_play_or_swing':False},provenance=provenance,extraction_cache_key=key,
        clips=[{'clip_id':CLIP,'clip_index':1,'original_start_ms':5000,'original_end_ms':15000,'heuristic_status':'check_boundary_activity','hints':[hint]}],
        removed_gaps=[{'gap_index':i+1,'original_start_ms':a,'original_end_ms':b,'heuristic_status':'unknown','hints':[]} for i,(a,b) in enumerate([(0,5000),(15000,20000)])])


def test_bounded_diagnostics_are_non_authoritative_and_match_exact_version(setup):
    _,_,data=setup;payload=diagnostics_for(data)
    _,rows=validate(json.dumps(payload).encode(),data);assert len(rows)==3
    for mutation in ('bounds','version','provenance','truth','hint_count'):
        bad=copy.deepcopy(payload)
        if mutation=='bounds':bad['clips'][0]['hints'][0]['original_end_ms']=15001
        elif mutation=='version':bad['video_version_id']='v_'+'0'*32
        elif mutation=='provenance':bad['provenance']['proxy_spec']['target_fps']=30
        elif mutation=='truth':bad['generator']['benchmark_truth']=True
        else:bad['clips'][0]['hints']*=6
        with pytest.raises(ValueError):validate(json.dumps(bad).encode(),data)
    with pytest.raises(ValueError):validate(b' '*524289,data)


def test_diagnostics_api_checksum_pagination_and_no_media_exposure(setup):
    app,root,data=setup;c=app.test_client();url=f'/api/processing/videos/{ID}/heuristics'
    assert c.get(url).json['available'] is False
    raw=json.dumps(diagnostics_for(data)).encode();path=root/'metadata/sessions'/ID/'heuristic-diagnostics.json';path.write_bytes(raw)
    data['artifacts']=['heuristic-diagnostics.json'];data['metadata_checksums']={'heuristic-diagnostics.json':hashlib.sha256(raw).hexdigest()}
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    first=c.get(url+'?limit=1').json;assert first['available'] is True and len(first['items'])==1 and first['next_after']=='1'
    assert len(c.get(url+'?after=1').json['items'])==2
    assert c.get(f'/api/sessions/{ID}/artifacts/heuristic-diagnostics.json').status_code==404
    path.write_bytes(raw+b' ');assert c.get(url).status_code==503
