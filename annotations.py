"""Application metadata: durable original-time targets, separate from predictions."""
import copy
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

SCOPES = {'point', 'interval', 'clip', 'video'}
CUT_REASONS = {'poor_cut', 'no_play', 'downtime', 'too_short', 'wrong_player',
               'start_early', 'start_late', 'end_early', 'end_late'}
KINDS = {
    'note': dict(domain='application', scopes=sorted(SCOPES)),
    'highlight': dict(domain='application', scopes=['point', 'interval']),
    'usefulness': dict(domain='application', scopes=['clip']),
    'cut_feedback': dict(domain='processing_feedback', scopes=['clip']),
    'coach': dict(domain='coaching', scopes=['interval'], read_only=True),
}
SOURCE_ID = re.compile(r'[a-f0-9]{64}')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class AnnotationConflict(ValueError):
    def __init__(self, message, current=None):
        super().__init__(message)
        self.current = current


def validate_body(kind, body):
    if not isinstance(body, dict):
        raise ValueError('Expected note details')
    text = body.get('text', '')
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('Use up to 2000 characters')
    result = {'text': text.strip()}
    allowed = {'text'}
    if kind == 'note' and not result['text']:
        raise ValueError('Enter a note')
    if kind == 'usefulness':
        allowed |= {'value', 'reason'}
        if body.get('value') != 'less_useful':
            raise ValueError('Invalid usefulness selection')
        reason = body.get('reason', '')
        if reason not in ('', 'low_information', 'repetitive', 'poor_view', 'other'):
            raise ValueError('Choose a valid usefulness reason')
        result.update(value='less_useful', reason=reason)
    if kind == 'cut_feedback':
        allowed.add('reasons')
        reasons = body.get('reasons')
        if (not isinstance(reasons, list) or not reasons or len(reasons) > len(CUT_REASONS)
                or any(not isinstance(r, str) or r not in CUT_REASONS for r in reasons)
                or len(set(reasons)) != len(reasons)):
            raise ValueError('Choose at least one cut issue')
        result['reasons'] = sorted(reasons)
    if set(body)-allowed:
        raise ValueError('Unknown note details')
    return result


def validate_target(kind, target, data):
    if not isinstance(target, dict) or set(target)-{'scope', 'start_seconds', 'end_seconds', 'clip_id'}:
        raise ValueError('Invalid note target')
    scope = target.get('scope')
    if not isinstance(scope, str) or scope not in KINDS[kind]['scopes']:
        raise ValueError('Choose a supported note target')
    result = {'scope': scope}
    if scope == 'video':
        if set(target) != {'scope'}:
            raise ValueError('A recording note has no timestamp')
        return result
    start = target.get('start_seconds')
    end = target.get('end_seconds')
    duration = data['source']['duration_seconds']
    if not finite(start) or not 0 <= start <= duration:
        raise ValueError('Start must be within the original recording')
    result['start_seconds'] = start
    if scope == 'point':
        if 'end_seconds' in target:
            raise ValueError('A moment has one timestamp')
    else:
        if not finite(end) or not start < end <= duration:
            raise ValueError('End must be after start within the original recording')
        result['end_seconds'] = end
    if scope == 'clip':
        clip = next((c for c in data['clips'] if c['id'] == target.get('clip_id')), None)
        if not clip or abs(start-clip['original_start_seconds']) > 1e-6 or abs(end-clip['original_end_seconds']) > 1e-6:
            raise ValueError('Reload and select a current clip; its original bounds are required')
        result['clip_id'] = clip['id']
    elif 'clip_id' in target:
        raise ValueError('Only a clip target has a clip reference')
    return result


def coverage(spans):
    total=0;end=None
    for a,b in sorted(spans):
        if end is None or a>end:total+=b-a;end=b
        elif b>end:total+=b-end;end=b
    return total


def project(annotation, data):
    """Return a derived view. Never mutate a saved selector to fit a new cut."""
    result = copy.deepcopy(annotation)
    target = result['target'];scope = target['scope']
    result.update(related_clip_ids=[], visible_intervals=[], relation='unavailable', playback_eligible=False,
                  training_label_status='not_released')
    if data is None:
        return result
    clips = data['clips']
    historical_run = result['kind'] == 'cut_feedback' and result['context']['publication'] != data['published_at']
    if scope == 'video':
        result['relation'] = 'recording'
        result['related_clip_ids'] = [c['id'] for c in clips]
        return result
    spans = target.get('selectors') or [target]
    exact_clips = []
    for clip in clips:
        a, b = clip['original_start_seconds'], clip['original_end_seconds']
        for span in spans:
            start = span['start_seconds'];end = span.get('end_seconds', start)
            point = scope == 'point'
            intersects = (a <= start < b or start == b == data['source']['duration_seconds']) if point else max(a, start) < min(b, end)
            if not intersects:
                continue
            x, y = (start, start) if point else (max(a, start), min(b, end))
            result['visible_intervals'].append(dict(clip_id=clip['id'], original_start=x, original_end=y,
                local_start=x-a, local_end=y-a, playback_start=clip['playback_start_seconds']+x-a,
                playback_end=clip['playback_start_seconds']+y-a))
            if clip['id'] not in result['related_clip_ids']:
                result['related_clip_ids'].append(clip['id'])
            if abs(a-start) <= .001 and abs(b-end) <= .001:
                exact_clips.append(clip['id'])
    if historical_run:
        result['relation'] = 'historical_run'
    elif scope == 'clip':
        result['relation'] = 'exact' if exact_clips else ('needs_review' if result['related_clip_ids'] else 'unavailable')
        # A clip judgment cannot decorate a split/merged/expanded cut by overlap.
        result['playback_eligible'] = bool(exact_clips)
        result['matched_clip_ids'] = exact_clips
    elif result['visible_intervals']:
        result['relation'] = 'retained'
        result['playback_eligible'] = True
        if scope == 'interval':
            expected = coverage((s['start_seconds'],s['end_seconds']) for s in spans)
            visible = coverage((s['original_start'],s['original_end']) for s in result['visible_intervals'])
            if visible + .001 < expected:
                result['relation'] = 'partly_retained'
    if result['status'] != 'active' or historical_run:
        result['playback_eligible'] = False
    return result


class AnnotationStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            for attempt in range(10):
                try:
                    db.execute('PRAGMA journal_mode=WAL');break
                except sqlite3.OperationalError as error:
                    if 'locked' not in str(error) or attempt == 9:raise
                    time.sleep(.02*(attempt+1))
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS annotation_source ON annotations(source_id,id)')
            db.execute('CREATE TABLE IF NOT EXISTS revisions(id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(id,version))')
            db.execute('CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, annotation_id TEXT NOT NULL)')
            db.commit()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:yield db
        finally:db.close()

    def get(self, identity):
        with self.db() as db:
            row = db.execute('SELECT data FROM annotations WHERE id=?', (identity,)).fetchone()
        if not row:raise KeyError(identity)
        return json.loads(row['data'])

    def listing(self, source, after='', limit=201):
        with self.db() as db:
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM annotations WHERE source_id=? AND id>? ORDER BY id LIMIT ?', (source,after,limit))]

    def revisions(self, identity, after=0, limit=101):
        with self.db() as db:
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM revisions WHERE id=? AND version>? ORDER BY version LIMIT ?', (identity,after,limit))]

    def write(self, value, expected=None, request_id=None):
        value = copy.deepcopy(value)
        fingerprint = canonical(value)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if request_id:
                row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
                if row:
                    if row['fingerprint'] != fingerprint:raise AnnotationConflict('Request ID already used for different metadata')
                    return json.loads(db.execute('SELECT data FROM annotations WHERE id=?', (row['annotation_id'],)).fetchone()['data'])
            identity = value.get('id')
            if identity is None:
                if value['kind'] == 'usefulness':
                    selector = [value['source_id'], value['kind'], round(value['target']['start_seconds']*1000), round(value['target']['end_seconds']*1000)]
                    identity = 'n_'+hashlib.sha256(canonical(selector).encode()).hexdigest()[:32]
                else:identity = 'n_'+uuid.uuid4().hex
                value['id'] = identity
            row = db.execute('SELECT data FROM annotations WHERE id=?', (identity,)).fetchone()
            current = json.loads(row['data']) if row else None
            if expected != (current['version'] if current else None):
                raise AnnotationConflict('This item changed in another browser. Reload before saving.', current)
            if current and expected is None:
                raise AnnotationConflict('A shared assessment already exists for this clip.', current)
            value['created_at'] = current['created_at'] if current else time.time()
            value.update(updated_at=time.time(), version=(current['version'] if current else 0)+1)
            encoded = canonical(value)
            db.execute('INSERT OR REPLACE INTO annotations VALUES(?,?,?,?,?)', (identity, value['source_id'], value['kind'], encoded, value['version']))
            db.execute('INSERT INTO revisions VALUES(?,?,?)', (identity, value['version'], encoded))
            if request_id:db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, fingerprint, identity))
            db.commit()
            return value
