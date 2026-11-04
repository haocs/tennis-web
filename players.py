"""Editable player profiles and original-clock assignments, outside published media."""
import copy
import hashlib
import json
import re
import uuid
from flask import abort, jsonify, request
from practices import group_recordings, recording_time
from state import Conflict

PLAYER_ID = re.compile(r'[a-f0-9]{32}')
LEVELS = {'kids', 'beginner', 'intermediate', 'experienced', 'pro'}
EMPTY = {'value': None, 'version': 0}


def player_list(shared):
    return sorted((dict(id=k[7:], **v['value'], version=v['version'])
                   for k, v in shared.items() if k.startswith('player:') and v['value']),
                  key=lambda p: (p['name'].casefold(), p['id']))


def validate_player(body):
    if not isinstance(body, dict) or set(body)-{'name', 'height_cm', 'level', 'version'}:
        raise ValueError('Invalid player details')
    name = body.get('name')
    height, level = body.get('height_cm'), body.get('level')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('Enter a name up to 100 characters')
    if height is not None and (type(height) not in (int, float) or not 30 <= height <= 260):
        raise ValueError('Height must be between 30 and 260 cm')
    if level is not None and (not isinstance(level, str) or level not in LEVELS):
        raise ValueError('Choose a valid level')
    return dict(name=name.strip(), height_cm=height, level=level)


def focus_key(data, clip):
    # Exact millisecond intervals can survive changed clip IDs; changed cuts never
    # silently acquire the historical main player just because they overlap.
    interval = [round(clip[k]*1000) for k in ('original_start_seconds', 'original_end_seconds')]
    digest = hashlib.sha256(json.dumps(interval).encode()).hexdigest()[:24]
    return f"player-focus:{data['id']}:{digest}"


def decorate_players(data, shared):
    profiles = {p['id']: p for p in player_list(shared)}
    roster = shared.get('players:'+data['id'], EMPTY)
    ids = (roster['value'] or {}).get('player_ids', [])
    data['player_assignment'] = copy.deepcopy(roster)
    data['recording_players'] = [profiles[p] for p in ids if p in profiles]
    all_ids = set(ids)
    for clip in data['clips']:
        focus = shared.get(focus_key(data, clip), EMPTY)
        pid = (focus['value'] or {}).get('player_id')
        overlaps_historical = focus['version'] == 0 and any(
            k.startswith('player-focus:'+data['id']+':') and v['value']
            and v['value'].get('original_start', float('inf')) < clip['original_end_seconds']
            and v['value'].get('original_end', -1) > clip['original_start_seconds']
            for k, v in shared.items())
        inherited = focus['version'] == 0 and len(ids) == 1 and not overlaps_historical
        if inherited:
            pid = ids[0]
        clip['main_player'] = profiles.get(pid)
        clip['player_assignment'] = dict(version=focus['version'], inherited=inherited, needs_review=overlaps_historical)
        clip['players'] = [profiles[pid]] if pid in profiles else []
        if pid in profiles:
            all_ids.add(pid)
    data['players'] = [p for p in profiles.values() if p['id'] in all_ids]
    return data


def decorate_groups(groups, recordings):
    by_id = {s['id']: s for s in recordings}
    for group in groups:
        profiles = {p['id']: p for identity in group['recording_ids'] for p in by_id[identity]['players']}
        group['players'] = sorted(profiles.values(), key=lambda p: (p['name'].casefold(), p['id']))
        group['player_versions'] = {i: by_id[i]['player_assignment']['version'] for i in group['recording_ids']}
    return groups


def coaching_player(data, clip_id, shared):
    data = decorate_players(copy.deepcopy(data), shared)
    clip = next((c for c in data['clips'] if c['id'] == clip_id), None)
    player = clip and clip['main_player']
    if not player:
        return None
    return dict(player_id=player['id'], profile_version=player['version'], height_cm=player['height_cm'],
                level=player['level'], original_start=clip['original_start_seconds'],
                original_end=clip['original_end_seconds'], assignment=clip['player_assignment'])


def register_players(app, library, session, gap_minutes):
    state = app.extensions['state']

    def update(key, value, expected):
        if type(expected) is not int or expected < 0:
            abort(400, 'A current version is required')
        try:
            return state.update(key, value, expected)
        except Conflict as conflict:
            return jsonify(error='Players changed in another browser. Reload and try again.', current=conflict.current), 409

    def roster(body, shared):
        ids = body.get('player_ids')
        if (not isinstance(ids, list) or len(ids) > 8 or any(not isinstance(p, str) for p in ids)
                or len(set(ids)) != len(ids) or any('player:'+p not in shared for p in ids)):
            abort(400, 'Choose up to eight saved players')
        return {'player_ids': ids}

    @app.get('/api/players')
    def players():
        return jsonify(players=player_list(state.all()))

    @app.post('/api/players')
    @app.put('/api/players/<identity>')
    def save_player(identity=None):
        body = request.get_json()
        try:
            value = validate_player(body)
        except ValueError as error:
            abort(400, str(error))
        if identity is None:
            identity, expected = uuid.uuid4().hex, 0
        else:
            if not PLAYER_ID.fullmatch(identity) or 'player:'+identity not in state.all():
                abort(404, 'Player not found')
            expected = body.get('version')
        result = update('player:'+identity, value, expected)
        if not isinstance(result, dict):
            return result
        return jsonify(id=identity, **value, version=result['version']), 201 if request.method == 'POST' else 200

    @app.put('/api/sessions/<identity>/players')
    def recording_players(identity):
        session(identity)
        body = request.get_json()
        if not isinstance(body, dict) or set(body)-{'player_ids', 'version'}:
            abort(400, 'Invalid player selection')
        result = update('players:'+identity, roster(body, state.all()), body.get('version'))
        return jsonify(result) if isinstance(result, dict) else result

    @app.put('/api/practices/<identity>/players')
    def practice_players(identity):
        shared = state.all()
        try:
            recordings, _ = library.sessions()
        except OSError:
            abort(503, 'The recording library is unavailable')
        for data in recordings:
            data['recorded_at_iso'], data['recorded_at_epoch'] = recording_time(data['source'].get('recorded_at'))
        group = next((g for g in group_recordings(recordings, gap_minutes) if g['id'] == identity), None)
        if group is None:
            abort(409, 'This practice changed. Reload and try again.')
        body = request.get_json()
        if not isinstance(body, dict) or set(body)-{'player_ids', 'versions'}:
            abort(400, 'Invalid player selection')
        value = roster(body, shared)
        versions = body.get('versions')
        if not isinstance(versions, dict) or set(versions) != set(group['recording_ids']):
            abort(409, 'Recordings in this practice changed. Reload and try again.')
        if any(type(v) is not int or v < 0 for v in versions.values()):
            abort(400, 'Current recording versions are required')
        try:
            state.update_many([('players:'+i, value, versions[i]) for i in group['recording_ids']])
        except Conflict:
            abort(409, 'Players changed in another browser. Reload and try again.')
        return jsonify(saved=True)

    @app.put('/api/sessions/<identity>/clips/players')
    def clip_players(identity):
        data = session(identity)
        body = request.get_json()
        if not isinstance(body, dict) or set(body)-{'player_id', 'clips', 'publication'}:
            abort(400, 'Invalid main player selection')
        if body.get('publication') != data['published_at']:
            abort(409, 'Recording changed. Refresh before selecting a player.')
        pid = body.get('player_id')
        if pid is not None and (not isinstance(pid, str) or 'player:'+pid not in state.all()):
            abort(400, 'Choose a saved player')
        choices = body.get('clips')
        if not isinstance(choices, list) or not 1 <= len(choices) <= 1000:
            abort(400, 'Select between 1 and 1000 clips')
        by_id = {c['id']: c for c in data['clips']}
        changes, seen = [], set()
        for choice in choices:
            if (not isinstance(choice, dict) or set(choice) != {'id', 'version'}
                    or not isinstance(choice['id'], str) or choice['id'] not in by_id
                    or choice['id'] in seen or type(choice['version']) is not int or choice['version'] < 0):
                abort(400, 'Invalid clip selection')
            seen.add(choice['id'])
            clip = by_id[choice['id']]
            value = dict(player_id=pid, original_start=clip['original_start_seconds'],
                         original_end=clip['original_end_seconds'], publication=data['published_at'])
            changes.append((focus_key(data, clip), value, choice['version']))
        try:
            state.update_many(changes)
        except Conflict:
            abort(409, 'Players changed in another browser. Reload and try again.')
        return jsonify(saved=True, updated=len(changes))

    @app.put('/api/sessions/<identity>/clips/<clip_id>/player')
    def clip_player(identity, clip_id):
        data = session(identity)
        clip = next((c for c in data['clips'] if c['id'] == clip_id), None)
        if clip is None:
            abort(404, 'Clip not found')
        body = request.get_json()
        if not isinstance(body, dict) or set(body)-{'player_id', 'version', 'publication'}:
            abort(400, 'Invalid main player selection')
        if body.get('publication') != data['published_at']:
            abort(409, 'Recording changed. Refresh before selecting a player.')
        pid = body.get('player_id')
        if pid is not None and (not isinstance(pid, str) or 'player:'+pid not in state.all()):
            abort(400, 'Choose a saved player')
        # An explicit unknown is distinct from the recording's one-player default.
        value = dict(player_id=pid, original_start=clip['original_start_seconds'],
                     original_end=clip['original_end_seconds'], publication=data['published_at'])
        result = update(focus_key(data, clip), value, body.get('version'))
        return jsonify(result) if isinstance(result, dict) else result
