from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from courts import distance_m, geo_point, rough_time, enrich_groups


def test_distance_uses_horizontal_gps_and_accepts_altitude_variation():
    a={'latitude':0,'longitude':0,'altitude_m':10}
    b={'latitude':0,'longitude':0.001,'altitude_m':200}
    assert 110 < distance_m(a,b) < 112
    assert distance_m(a,{**a,'altitude_m':1000})==0
    assert geo_point({'latitude':True,'longitude':0}) is None


def test_matching_range_uses_every_recording_and_includes_boundary():
    anchor={'latitude':0,'longitude':0}
    point={'latitude':0,'longitude':0.001}
    court={'name':'Synthetic court',**anchor,'radius_m':distance_m(anchor,point),'altitude_m':None}
    videos=[{'id':'v1','source':{'location':point}}, {'id':'v2','source':{'location':anchor}}]
    group={'id':'v1','recording_ids':['v1','v2'],'recorded_at_iso':'2026-10-30T13:00:00+00:00'}
    shared={'court:'+'a'*32:{'value':court,'version':1}}
    assert enrich_groups([dict(group)],videos,shared)[0]['court_status']=='gps_match'
    videos[1]['source']['location']={'latitude':1,'longitude':1}
    assert enrich_groups([dict(group)],videos,shared)[0]['court_status']=='unknown'


def test_practice_time_label_uses_original_timezone_and_date():
    assert rough_time('2026-10-30T13:00:00-07:00')=='2026/10/30 afternoon'
    assert rough_time('2026-10-30T06:00:00+09:00')=='2026/10/30 morning'
    assert rough_time(None)=='Date not recorded'
