"""Read-only validation of bounded worker hints, never serving or training truth."""
import hashlib
import json
import math
import re
from entity_model import output_version

MAX_BYTES=512*1024
TRUTH='heuristic_only_not_play_or_swing_truth'
STATUSES={'no_detector_warnings','check_boundary_activity','extended_low_motion','person_track_interrupted','ambiguous_multiple_persons','unknown'}
REASONS={'poor_cut','no_play','downtime','too_short','wrong_player','start_early','start_late','end_early','end_late','lost_player','split_rally','missed_play'}


def validate(raw,manifest):
    if len(raw)>MAX_BYTES:raise ValueError('Diagnostics artifact is too large')
    data=json.loads(raw)
    def check(ok):
        if not ok:raise ValueError('Diagnostics do not match this processed version')
    check(isinstance(data,dict) and data.get('schema_version')==1)
    check(data.get('source_sha256')==manifest['id'] and data.get('video_version_id')==output_version(manifest['id'],manifest['published_at']) and data.get('published_at')==manifest['published_at'])
    duration=round(manifest['source']['duration_seconds']*1000)
    check(type(data.get('source_duration_ms')) is int and data['source_duration_ms']==duration)
    generator=data.get('generator',{})
    check(data.get('truth_status')==TRUTH and generator.get('truth_status')==TRUTH and generator.get('kind')=='worker_heuristic_v1')
    check(data.get('training_label_status')=='not_released' and generator.get('training_label_status')=='not_released' and generator.get('benchmark_truth') is False and generator.get('verified_play_or_swing') is False)
    provenance=data.get('provenance',{});cachekey=data.get('extraction_cache_key')
    check(isinstance(provenance,dict) and provenance.get('source_sha256')==manifest['id'] and isinstance(cachekey,str) and bool(re.fullmatch('[a-f0-9]{64}',cachekey)))
    keys=('cache_schema_version','source_sha256','proxy_spec','detector_spec','extractor_code_digest','roi_and_filter_config')
    check(all(k in provenance for k in keys))
    computed=hashlib.sha256(json.dumps({k:provenance[k] for k in keys},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    check(cachekey==computed and provenance.get('extraction_cache_key')==cachekey)
    processing=manifest.get('processing',{})
    for key in ('task_id','attempt_id'):
        if processing.get(key):check(data.get(key)==processing[key])
    clips=data.get('clips');gaps=data.get('removed_gaps')
    check(isinstance(clips,list) and isinstance(gaps,list) and len(clips)==len(manifest['clips']))
    expected_gaps=[];cursor=0;entries=[];seen=set()
    for c,diag in zip(manifest['clips'],clips):
        start,end=round(c['original_start_seconds']*1000),round(c['original_end_seconds']*1000)
        if cursor<start:expected_gaps.append((cursor,start))
        check(isinstance(diag,dict) and diag.get('clip_id')==c['id'] and diag.get('clip_index')==c['index'])
        entries.append((diag,start,end,{'scope':'clip','clip_id':c['id']}));cursor=end
    if cursor<duration:expected_gaps.append((cursor,duration))
    check(len(gaps)==len(expected_gaps))
    for index,(diag,(start,end)) in enumerate(zip(gaps,expected_gaps),1):
        check(isinstance(diag,dict) and diag.get('gap_index')==index)
        entries.append((diag,start,end,{'scope':'gap','gap_index':index}))
    result=[]
    for diag,start,end,target in entries:
        check(type(diag.get('original_start_ms')) is int and type(diag.get('original_end_ms')) is int and (diag['original_start_ms'],diag['original_end_ms'])==(start,end))
        check(diag.get('heuristic_status') in STATUSES)
        hints=diag.get('hints');check(isinstance(hints,list) and len(hints)<=5)
        for h in hints:
            check(isinstance(h,dict))
            identity=h.get('hint_id');check(isinstance(identity,str) and 0<len(identity)<=80 and bool(re.fullmatch('[A-Za-z0-9_.:-]+',identity)) and identity not in seen);seen.add(identity)
            a,b=h.get('original_start_ms'),h.get('original_end_ms')
            check(type(a) is int and type(b) is int and start<=a<b<=end)
            check(isinstance(h.get('summary'),str) and 0<len(h['summary'])<=2000)
            check(isinstance(h.get('mapped_reasons'),list) and bool(h['mapped_reasons']) and set(h['mapped_reasons'])<=REASONS)
            for key in ('candidate_start_ms','candidate_end_ms'):
                if h.get(key) is not None:check(target['scope']=='clip' and type(h[key]) is int and start<h[key]<end)
        for key in ('person_presence_ratio','active_motion_ratio','ambiguous_multi_person_ratio'):
            value=diag.get(key)
            if value is not None:check(type(value) in (int,float) and math.isfinite(value) and 0<=value<=1)
        result.append(dict(target=target,original_start_ms=start,original_end_ms=end,heuristic_status=diag['heuristic_status'],identity_status=diag.get('identity_status','unknown'),hints=hints))
    return data,result
