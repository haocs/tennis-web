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
from entity_model import classify, record_class, output_version

SCOPES = {'point', 'interval', 'clip', 'video'}
CUT_REASONS = {'poor_cut', 'no_play', 'downtime', 'too_short', 'wrong_player',
               'start_early', 'start_late', 'end_early', 'end_late'}
KINDS = {
    'note': dict(domain='application', scopes=sorted(SCOPES)),
    'highlight': dict(domain='application', scopes=['point', 'interval']),
    'usefulness': dict(domain='entity_metadata', scopes=['clip', 'video']),
    'cut_feedback': dict(domain='processing_feedback', scopes=['clip']),
    'processing_label': dict(domain='processing_feedback', scopes=sorted(SCOPES)),
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


def validate_body(kind, body, *, allow_empty=False):
    if not isinstance(body, dict):
        raise ValueError('Expected note details')
    text = body.get('text', '')
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('Use up to 2000 characters')
    result = {'text': text.strip()}
    allowed = {'text'}
    if kind == 'note' and not result['text'] and not allow_empty:
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
    if kind == 'processing_label':
        allowed |= {'label','value','confidence'}
        label=body.get('label');confidence=body.get('confidence')
        if not isinstance(label,str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}',label):
            raise ValueError('Choose a valid processing label')
        if confidence is not None and (not finite(confidence) or not 0<=confidence<=1):
            raise ValueError('Confidence must be between zero and one')
        try:encoded=canonical(body.get('value'))
        except (ValueError,TypeError):raise ValueError('Use a finite JSON label value')
        if len(encoded)>8000:raise ValueError('Processing label value is too large')
        result.update(label=label,value=body.get('value'),confidence=confidence)
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
        if start == duration:
            raise ValueError('Start must be before the end of the original recording')
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
    result = classify(annotation)
    target = result['target'];scope = target['scope']
    result.update(related_clip_ids=[], visible_intervals=[], relation='unavailable', playback_eligible=False,
                  training_label_status='not_released')
    if data is None:
        return result
    clips = data['clips']
    historical_run = (result['kind'] in ('cut_feedback', 'processing_label', 'usefulness') or (result['kind']=='note' and scope=='clip')) and result['context']['publication'] != data['published_at']
    if scope == 'video':
        result['relation'] = 'historical_run' if historical_run else 'recording'
        result['related_clip_ids'] = [c['id'] for c in clips]
        return result
    spans = target.get('selectors') or [target]
    exact_clips = []
    for clip in clips:
        a, b = clip['original_start_seconds'], clip['original_end_seconds']
        for span in spans:
            start = span['start_seconds'];end = span.get('end_seconds', start)
            point = scope == 'point'
            intersects = (a <= start < b) if point else max(a, start) < min(b, end)
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
        result['matched_clip_ids'] = []
    elif scope == 'clip':
        exact_clips = [c for c in exact_clips if c == target['clip_id']]
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
        self.like_seed = lambda source,item: {'value':False,'version':0}
        self.current_publication = lambda source: None
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
            db.execute('CREATE TABLE IF NOT EXISTS reaction_likes(like_key TEXT PRIMARY KEY, liked INTEGER NOT NULL, version INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata_migrations(name TEXT PRIMARY KEY)')
            if 'record_class' not in {r['name'] for r in db.execute('PRAGMA table_info(annotations)')}:
                db.execute("ALTER TABLE annotations ADD COLUMN record_class TEXT NOT NULL DEFAULT 'unclassified'")
            for row in db.execute("SELECT id,data FROM annotations WHERE record_class='unclassified'").fetchall():
                db.execute('UPDATE annotations SET record_class=? WHERE id=?',(record_class(json.loads(row['data'])),row['id']))
            for name, category in [('serving_moments','moment'),('entity_metadata','entity_metadata'),('processing_annotations','processing_annotation')]:
                db.execute(f"CREATE VIEW IF NOT EXISTS {name} AS SELECT id,source_id,kind,data,version FROM annotations WHERE record_class='{category}'")
            db.execute("INSERT OR IGNORE INTO metadata_migrations VALUES('serving-entities-v2')")
            if not db.execute('SELECT 1 FROM metadata_migrations WHERE name=?',('unique-point-highlights-v1',)).fetchone():
                seen=set()
                rows=[json.loads(r['data']) for r in db.execute("SELECT data FROM annotations WHERE kind='highlight' AND json_extract(data,'$.status')='active' AND json_extract(data,'$.target.scope')='point'")]
                # Keep a captioned highlight first; never discard an archived
                # duplicate's text, original selector or immutable old revisions.
                rows.sort(key=lambda a:(not bool(a['body'].get('text')),a.get('created_at',0),a['id']))
                for value in rows:
                    key=(value['source_id'],int(math.floor(value['target']['start_seconds']*1000+.5)))
                    if key in seen:
                        value.update(status='archived',version=value['version']+1,updated_at=time.time(),archive_reason='duplicate_highlight',change_origin='system_deduplicate')
                        encoded=canonical(value)
                        db.execute('UPDATE annotations SET data=?,version=? WHERE id=?',(encoded,value['version'],value['id']))
                        db.execute('INSERT INTO revisions VALUES(?,?,?)',(value['id'],value['version'],encoded))
                    else:seen.add(key)
                db.execute('INSERT INTO metadata_migrations VALUES(?)',('unique-point-highlights-v1',))
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS unique_active_point_highlight ON annotations(source_id,CAST(round(json_extract(data,'$.target.start_seconds')*1000) AS INTEGER)) WHERE kind='highlight' AND json_extract(data,'$.status')='active' AND json_extract(data,'$.target.scope')='point'")
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

    def listing(self, source, after='', limit=201, category=None):
        with self.db() as db:
            clause=' AND record_class=?' if category else ''
            args=(source,after,category,limit) if category else (source,after,limit)
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM annotations WHERE source_id=? AND id>?'+clause+' ORDER BY id LIMIT ?', args)]

    def revisions(self, identity, after=0, limit=101):
        with self.db() as db:
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM revisions WHERE id=? AND version>? ORDER BY version LIMIT ?', (identity,after,limit))]

    @staticmethod
    def like_key(source,item):
        return 'like:'+source+(':'+item if item not in ('video','session') else '')

    def _like(self,db,source,item):
        row=db.execute('SELECT liked,version FROM reaction_likes WHERE like_key=?',(self.like_key(source,item),)).fetchone()
        return {'value':bool(row['liked']),'version':row['version']} if row else self.like_seed(source,item)

    def _save_like(self,db,source,item,liked,version):
        db.execute('INSERT INTO reaction_likes VALUES(?,?,?) ON CONFLICT(like_key) DO UPDATE SET liked=excluded.liked,version=excluded.version',(self.like_key(source,item),int(liked),version))

    def like_states(self,source,publication):
        """Overlay existing favorites; old rows/history stay intact in website state."""
        with self.db() as db:
            prefix='like:'+source
            result={r['like_key']:{'value':bool(r['liked']),'version':r['version']} for r in db.execute('SELECT * FROM reaction_likes WHERE like_key=? OR like_key LIKE ?',(prefix,prefix+':%'))}
            # Older releases allowed both values. Prefer an existing current downvote
            # without rewriting either historical field during a read.
            for r in db.execute("SELECT data FROM annotations WHERE source_id=? AND kind='usefulness' AND json_extract(data,'$.status')='active'",(source,)):
                a=json.loads(r['data'])
                if a['context']['publication']!=publication:continue
                item=a['target'].get('clip_id','video');key=self.like_key(source,item)
                result[key]={**(result[key] if key in result else self.like_seed(source,item)),'value':False}
            return result

    def active_highlights_by_source(self,source_ids=None):
        with self.db() as db:
            if source_ids is not None:
                ids=[s for s in source_ids if isinstance(s,str)]
                if not ids:return {}
                placeholders=','.join('?' for _ in ids)
                rows=db.execute(f"SELECT source_id,data FROM annotations WHERE kind='highlight' AND json_extract(data,'$.status')='active' AND source_id IN ({placeholders}) ORDER BY id",ids).fetchall()
            else:
                rows=db.execute("SELECT source_id,data FROM annotations WHERE kind='highlight' AND json_extract(data,'$.status')='active' ORDER BY id").fetchall()
        grouped={}
        for row in rows:
            grouped.setdefault(row['source_id'],[]).append(json.loads(row['data']))
        return grouped

    def clip_highlights(self,data,rows=None):
        identity=data['id']
        if rows is None:
            rows=self.active_highlights_by_source([identity]).get(identity,[])
        by_clip={c['id']:[] for c in data.get('clips',[])}
        for raw in rows:
            if raw.get('kind')!='highlight' or raw.get('status')!='active':continue
            projected=project(raw,data)
            if projected['status']!='active' or not projected['playback_eligible']:continue
            text=(projected.get('body') or {}).get('text','')
            for interval in projected.get('visible_intervals',[]):
                cid=interval['clip_id']
                if cid in by_clip:
                    by_clip[cid].append(dict(id=projected['id'],start_seconds=interval['original_start'],
                        end_seconds=interval['original_end'],local_start=interval['local_start'],text=text))
        for items in by_clip.values():
            items.sort(key=lambda h:(h['start_seconds'],h['id']))
        return by_clip

    def write_like(self,source,item,liked,expected,publication):
        """One transaction enforces the up/down invariant, including downvote history."""
        from state import Conflict
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._like(db,source,item)
            if expected!=current['version']:raise Conflict(current)
            if liked:
                for r in db.execute("SELECT data FROM annotations WHERE source_id=? AND kind='usefulness' AND json_extract(data,'$.status')='active'",(source,)).fetchall():
                    a=json.loads(r['data'])
                    if a['context']['publication']!=publication or a['target'].get('clip_id','video')!=item:continue
                    a.update(status='archived',change_origin='human')
                    self._write(db,a,expected=a['version'])
            self._save_like(db,source,item,liked,expected+1)
            db.commit()
            return {'value':liked,'version':expected+1}

    def write(self, value, expected=None, request_id=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            saved=self._write(db,value,expected,request_id)
            db.commit()
            return saved

    def _write(self, db, value, expected=None, request_id=None):
        value=classify(value)
        fingerprint=canonical(value)
        if request_id:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if row:
                if row['fingerprint'] != fingerprint:raise AnnotationConflict('Request ID already used for different metadata')
                return json.loads(db.execute('SELECT data FROM annotations WHERE id=?', (row['annotation_id'],)).fetchone()['data'])
        identity = value.get('id')
        if identity is None and value['kind']=='usefulness':
            # Legacy singleton IDs were derived from bounds; recover the same
            # field by its output subject instead of creating a second value.
            candidates=db.execute("SELECT data FROM annotations WHERE source_id=? AND kind='usefulness'",(value['source_id'],)).fetchall()
            for candidate in candidates:
                old=classify(json.loads(candidate['data']))
                if old['subject']==value['subject']:
                    identity=old['id'];value['id']=identity;break
        if value['kind']=='highlight' and value['target']['scope']=='point' and value['status']=='active':
            tick=int(math.floor(value['target']['start_seconds']*1000+.5))
            duplicate=db.execute("SELECT data FROM annotations WHERE source_id=? AND kind='highlight' AND json_extract(data,'$.status')='active' AND json_extract(data,'$.target.scope')='point' AND CAST(round(json_extract(data,'$.target.start_seconds')*1000) AS INTEGER)=? AND id<>? ORDER BY id LIMIT 1",(value['source_id'],tick,identity or '')).fetchone()
            if duplicate:
                existing=json.loads(duplicate['data'])
                if expected is not None or value['body'].get('text'):
                    raise AnnotationConflict('This moment is already highlighted. Edit the existing highlight instead.',existing)
                if request_id:db.execute('INSERT INTO requests VALUES(?,?,?)',(request_id,fingerprint,existing['id']))
                return dict(existing,already_highlighted=True)
        if identity is None:
            if value['kind'] == 'usefulness':
                selector = [value['source_id'], value['kind'], value['subject']]
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
        db.execute('INSERT INTO annotations(id,source_id,kind,data,version,record_class) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_id=excluded.source_id,kind=excluded.kind,data=excluded.data,version=excluded.version,record_class=excluded.record_class', (identity, value['source_id'], value['kind'], encoded, value['version'], value['record_class']))
        db.execute('INSERT INTO revisions VALUES(?,?,?)', (identity, value['version'], encoded))
        if request_id:db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, fingerprint, identity))
        if value['kind']=='usefulness' and value['context']['publication']==self.current_publication(value['source_id']):
            item=value['target'].get('clip_id','video')
            reaction=self._like(db,value['source_id'],item)
            liked=False if value['status']=='active' or current and current['status']=='active' else reaction['value']
            self._save_like(db,value['source_id'],item,liked,reaction['version']+1)
        return value
