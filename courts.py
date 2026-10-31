"""Local saved courts, GPS proximity, and home-page practice names."""
from datetime import datetime
from collections import Counter
import math
import re

COURT_ID = re.compile(r'[a-f0-9]{32}')


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def geo_point(value):
    if not isinstance(value, dict):
        return None
    lat, lon = value.get('latitude'), value.get('longitude')
    if not finite_number(lat) or not finite_number(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    result = {'latitude': lat, 'longitude': lon}
    if finite_number(value.get('altitude_m')):
        result['altitude_m'] = value['altitude_m']
    return result


def validate_court(body):
    if not isinstance(body, dict) or set(body) - {'name','address','latitude','longitude','altitude_m','radius_m','version'}:
        raise ValueError('Invalid court details')
    name = body.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError('Enter a court name of at most 120 characters')
    address = body.get('address', '')
    if not isinstance(address, str) or len(address.strip()) > 500:
        raise ValueError('Enter an address of at most 500 characters')
    lat, lon = body.get('latitude'), body.get('longitude')
    point = None
    if lat is not None or lon is not None:
        point = geo_point(body)
        if point is None:
            raise ValueError('Enter a valid latitude and longitude, or leave both empty')
    altitude = body.get('altitude_m')
    if altitude is not None and (point is None or not finite_number(altitude)):
        raise ValueError('Altitude needs valid coordinates and a finite number')
    radius = body.get('radius_m', 150)
    if not finite_number(radius) or not 25 <= radius <= 2000:
        raise ValueError('GPS matching range must be between 25 and 2000 metres')
    return {'name':' '.join(name.split()),'address':address.strip(),'latitude':lat,'longitude':lon,
            'altitude_m':altitude,'radius_m':float(radius)}


def distance_m(a, b):
    lat1, lat2 = math.radians(a['latitude']), math.radians(b['latitude'])
    dlat = lat2-lat1
    dlon = math.radians(b['longitude']-a['longitude'])
    term = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, term))))


def court_list(shared):
    return sorted([{'id':key[6:], 'address':'', **row['value'], 'version':row['version']}
                   for key,row in shared.items()
                   if key.startswith('court:') and COURT_ID.fullmatch(key[6:]) and row['value']],
                  key=lambda c:(c['name'].casefold(),c['id']))


def rough_time(iso):
    if not iso:
        return 'Date not recorded'
    dt = datetime.fromisoformat(iso)
    hour = dt.hour
    period = 'morning' if 5 <= hour < 12 else 'afternoon' if 12 <= hour < 17 else 'evening' if 17 <= hour < 22 else 'night'
    return f'{dt:%Y/%m/%d} {period.capitalize()}'


def enrich_groups(groups, recordings, shared):
    courts = court_list(shared)
    by_court = {c['id']:c for c in courts}
    by_video = {v['id']:v for v in recordings}
    for group in groups:
        videos = [by_video[identity] for identity in group['recording_ids']]
        points = [point for v in videos if (point := geo_point(v['source'].get('location')))]
        # Seed a new court from an actual recording, not an invented/geocoded point.
        group['location'] = points[0] if points else None
        assignments = {v['id']:shared.get('court-assignment:'+v['id'], {'value':None,'version':0}) for v in videos}
        group['court_versions'] = {identity:row['version'] for identity,row in assignments.items()}
        chosen_ids = {row['value']['court_id'] for row in assignments.values()
                      if row['value'] and row['value'].get('court_id') in by_court}
        candidates = []
        for court in courts:
            anchor = geo_point(court)
            if not points or anchor is None:
                continue
            # Every available recording position must be inside the venue range.
            distance = max(distance_m(anchor, point) for point in points)
            if distance <= court['radius_m']:
                candidates.append({'id':court['id'],'name':court['name'],'distance_m':round(distance,1)})
        candidates.sort(key=lambda c:(c['distance_m'],c['id']))
        selected, status = None, 'unknown'
        if len(chosen_ids) == 1:
            selected, status = by_court[next(iter(chosen_ids))], 'selected'
        elif len(chosen_ids) > 1:
            status = 'conflict'
        elif len(candidates) == 1:
            selected, status = by_court[candidates[0]['id']], 'gps_match'
        elif candidates:
            status = 'ambiguous'
        legacy = {v.get('location_label') for v in videos if v.get('location_label')}
        name = selected['name'] if selected else 'Multiple courts' if status == 'conflict' else next(iter(legacy)) if len(legacy)==1 else 'Unknown court'
        group.update(court=selected, court_status=status, court_candidates=candidates,
                     court_name=name, time_label=rough_time(group['recorded_at_iso']))
    # Count before any browser filtering, so titles stay stable during search/sort.
    labels = Counter(g['time_label'] for g in groups if g['recorded_at_iso'])
    for group in groups:
        group['title'] = group['time_label']
        if group['recorded_at_iso'] and labels[group['time_label']] > 1:
            group['title'] += ' @ ' + group['court_name']
    return groups
