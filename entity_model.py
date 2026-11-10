"""Serving identities and retained output manifests; media remains processor-owned."""
import copy
import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from practices import group_recordings, recording_time

VERSION_ID_RE = re.compile(r'v_[a-f0-9]{32}')


def output_version(source, publication):
    # Publication is the v1 publisher's immutable generation token, not capture time.
    pub = float(publication)
    if not math.isfinite(pub):
        raise ValueError('Publication timestamp must be finite')
    raw = json.dumps([source, pub], separators=(',', ':'), allow_nan=False)
    return 'v_' + hashlib.sha256(raw.encode()).hexdigest()[:32]


def canonical_manifest(v):
    manifest = {k: copy.deepcopy(v[k]) for k in ('id', 'source', 'published_at', 'video', 'clips', 'processing', 'artifact_directory', 'media_directory') if k in v}
    manifest['clips'] = [{k: c[k] for k in ('id', 'index', 'file', 'original_start_seconds', 'original_end_seconds', 'duration_seconds', 'playback_start_seconds', 'playback_end_seconds')} for c in v['clips']]
    manifest['video'] = {k: val for k, val in v['video'].items() if k != 'nas_url'}
    return manifest


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def clip_identity(data, clip_id):
    return 'c_' + hashlib.sha256((output_version(data['id'], data['published_at'])+':'+clip_id).encode()).hexdigest()[:32]


def record_class(value):
    if value['kind'] in ('cut_feedback','processing_label'):return 'processing_annotation'
    if value['kind'] in ('highlight', 'coach') or value['target']['scope'] in ('point', 'interval'):
        return 'moment'
    return 'entity_metadata'


def subject(value):
    target = value['target'];source = value['source_id']
    version = output_version(source, value['context']['publication'])
    if target['scope'] == 'clip':
        return dict(type='clip', id='c_'+hashlib.sha256((version+':'+target['clip_id']).encode()).hexdigest()[:32],
                    video_id=source, video_version_id=version, clip_id=target['clip_id'])
    if value['kind'] in ('usefulness','cut_feedback','processing_label'):
        return dict(type='video_version', id=version, video_id=source, video_version_id=version)
    return dict(type='video', id=source, video_id=source)


def classify(value):
    value = copy.deepcopy(value)
    value['record_class'] = record_class(value)
    if value['record_class'] != 'moment':value['subject'] = subject(value)
    else:
        value.pop('subject', None)
        value['moment_type'] = {'note':'comment','coach':'coaching'}.get(value['kind'],value['kind'])
    return value


def moment_view(value):
    t = value['target']
    result = dict(id=value['id'], type=value.get('moment_type', 'coaching' if value['kind']=='coach' else value['kind']),
        original_recording_id=value['source_id'], start_ms=round(t['start_seconds']*1000),
        payload=value['body'], payload_schema_version=1, version=value['version'], status=value['status'],
        player_id=value.get('player_id'), provenance=value.get('origin'),created_at=value.get('created_at'),updated_at=value.get('updated_at'),
        clip_ids=value.get('related_clip_ids', []), fragments=value.get('visible_intervals', []),
        relation=value.get('relation'), playback_eligible=value.get('playback_eligible', False),
        read_only=value.get('read_only', False), edit_url=value.get('edit_url'),already_highlighted=value.get('already_highlighted',False))
    if 'end_seconds' in t:result['end_ms'] = round(t['end_seconds']*1000)
    if t.get('selectors'):result['segments']=[dict(start_ms=round(s['start_seconds']*1000),end_ms=round(s['end_seconds']*1000)) for s in t['selectors']]
    return result


class EntityCatalog:
    """Independent sessions plus retained versions, in the website's local DB."""
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS original_recordings(id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, original_recording_id TEXT NOT NULL, current_version_id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS clips(id TEXT PRIMARY KEY, video_version_id TEXT NOT NULL, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS video_versions(id TEXT PRIMARY KEY, video_id TEXT NOT NULL, manifest TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS practice_sessions(id TEXT PRIMARY KEY, metadata TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS session_members(video_id TEXT PRIMARY KEY, session_id TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path, timeout=10, isolation_level=None);db.row_factory=sqlite3.Row
        try:yield db
        finally:db.close()

    def _check_version_collision(self, existing_row, video_id, manifest):
        if existing_row['video_id'] != video_id:
            raise ValueError('Video version collision across different video IDs')
        old = json.loads(existing_row['manifest'])
        if _canonical_json(canonical_manifest(old)) != _canonical_json(manifest):
            raise ValueError('Video version collision with different manifest contents')

    def _write_version_manifest(self, db, v, *, update_current_serving=False, strict_collision=True):
        version = output_version(v['id'], v['published_at'])
        manifest = canonical_manifest(v)
        existing = db.execute('SELECT video_id,manifest FROM video_versions WHERE id=?', (version,)).fetchone()
        if existing and strict_collision:
            self._check_version_collision(existing, v['id'], manifest)
        if update_current_serving:
            db.execute('INSERT INTO videos VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET current_version_id=excluded.current_version_id WHERE current_version_id<>excluded.current_version_id', (v['id'], v['id'], version))
        if not existing:
            for clip in v['clips']:
                descriptor = {k: clip[k] for k in ('id', 'index', 'file', 'original_start_seconds', 'original_end_seconds', 'duration_seconds', 'playback_start_seconds', 'playback_end_seconds')}
                db.execute('INSERT OR IGNORE INTO clips VALUES(?,?,?)', (clip_identity(v, clip['id']), version, json.dumps(descriptor)))
            db.execute('INSERT OR IGNORE INTO original_recordings VALUES(?,?)', (v['id'], json.dumps(v['source'])))
            db.execute('INSERT OR IGNORE INTO video_versions VALUES(?,?,?)', (version, v['id'], json.dumps(manifest)))
        return version

    def admit_version(self, recording, *, update_current_serving=False):
        """Admit a validated version manifest without changing current_version_id unless serving."""
        v = copy.deepcopy(recording)
        version = output_version(v['id'], v['published_at'])
        manifest = canonical_manifest(v)
        with self.db() as db:
            existing = db.execute('SELECT video_id,manifest FROM video_versions WHERE id=?', (version,)).fetchone()
            vid_row = db.execute('SELECT current_version_id FROM videos WHERE id=?', (v['id'],)).fetchone() if update_current_serving else None
            if existing:
                self._check_version_collision(existing, v['id'], manifest)
                if not update_current_serving or (vid_row and vid_row['current_version_id'] == version):
                    return version
            db.execute('BEGIN IMMEDIATE')
            version = self._write_version_manifest(db, v, update_current_serving=update_current_serving, strict_collision=True)
            db.commit()
        return version

    def sync_serving_video(self, recording):
        """Ingest the active serving session manifest and update videos.current_version_id."""
        return self.admit_version(recording, update_current_serving=True)

    def sync(self, recordings, gap_minutes=30):
        """New recordings may join a session; existing membership never regroups."""
        values=copy.deepcopy(recordings)
        for v in values:v['recorded_at_iso'],v['recorded_at_epoch']=recording_time(v['source'].get('recorded_at'))
        proposals=group_recordings(values,gap_minutes)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            memberships={r['video_id']:r['session_id'] for r in db.execute('SELECT * FROM session_members')}
            for v in values:
                self._write_version_manifest(db, v, update_current_serving=True, strict_collision=False)
            for group in proposals:
                existing=[memberships[i] for i in group['recording_ids'] if i in memberships]
                identity=existing[0] if existing else group['id']
                db.execute('INSERT OR IGNORE INTO practice_sessions(id) VALUES(?)',(identity,))
                for video_id in group['recording_ids']:
                    if video_id not in memberships:
                        db.execute('INSERT INTO session_members VALUES(?,?)',(video_id,identity));memberships[video_id]=identity
            db.commit()
        return self.groups(values)

    def groups(self, recordings):
        by_id={v['id']:v for v in recordings};groups={}
        with self.db() as db:
            for r in db.execute('SELECT m.video_id,s.* FROM session_members m JOIN practice_sessions s ON s.id=m.session_id ORDER BY m.video_id'):
                if r['video_id'] not in by_id:continue
                g=groups.setdefault(r['id'],dict(id=r['id'],entity_type='session',recording_ids=[],metadata=json.loads(r['metadata']),metadata_version=r['version']))
                g['recording_ids'].append(r['video_id'])
        for g in groups.values():
            videos=sorted((by_id[i] for i in g['recording_ids']),key=lambda v:(v['recorded_at_epoch'] is None,v['recorded_at_epoch'] or 0,v['id']))
            g['recording_ids']=[v['id'] for v in videos]
            dated=[v for v in videos if v['recorded_at_epoch'] is not None]
            g.update(video_ids=g['recording_ids'],recorded_at_iso=dated[0]['recorded_at_iso'] if dated else None,
                     started_at_epoch=dated[0]['recorded_at_epoch'] if dated else None,
                     ended_at_epoch=max(v['recorded_at_epoch']+v['source']['duration_seconds'] for v in dated) if dated else None)
        return list(groups.values())

    def edit_session(self, identity, values, expected):
        from state import Conflict
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM practice_sessions WHERE id=?',(identity,)).fetchone()
            if not row:raise KeyError(identity)
            current=dict(value=json.loads(row['metadata']),version=row['version'])
            if current['version']!=expected:raise Conflict(current)
            merged={**current['value'],**values}
            db.execute('UPDATE practice_sessions SET metadata=?,version=? WHERE id=?',(json.dumps(merged),expected+1,identity));db.commit()
            return dict(value=merged,version=expected+1)

    def versions(self, video_id):
        with self.db() as db:return [dict(id=r['id'],video_id=video_id,manifest=json.loads(r['manifest'])) for r in db.execute('SELECT * FROM video_versions WHERE video_id=? ORDER BY rowid',(video_id,))]

    def version_manifest(self, video_id, version_id):
        if not isinstance(video_id, str) or not isinstance(version_id, str) or not VERSION_ID_RE.fullmatch(version_id):
            return None
        with self.db() as db:
            row = db.execute('SELECT * FROM video_versions WHERE id=? AND video_id=?', (version_id, video_id)).fetchone()
        if not row:
            return None
        try:
            manifest = json.loads(row['manifest'])
            if (not isinstance(manifest, dict) or manifest.get('id') != video_id
                    or not isinstance(manifest.get('source'), dict) or not isinstance(manifest.get('clips'), list)
                    or not isinstance(manifest.get('video'), dict)):
                return None
            pub = manifest.get('published_at')
            if type(pub) not in (int, float) or not math.isfinite(pub):
                return None
            if output_version(video_id, pub) != version_id:
                return None
        except (ValueError, TypeError, KeyError):
            return None
        return manifest

    def decorate(self, data):
        data['entity_type']='video';data['original_recording_id']=data['id'];data['video_version_id']=output_version(data['id'],data['published_at'])
        for c in data['clips']:
            c.update(entity_type='clip',entity_id=clip_identity(data,c['id']),video_id=data['id'],video_version_id=data['video_version_id'])
        return data
