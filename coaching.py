"""Dedicated coaching/export queues. Never consumes video-processing tasks."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from coaching_annotations import Annotations
from worker.tennis_coach.annotations import RENDER_SHARE_CAPABILITY

MAX_AUTO_REVIEWS_PER_CLIP = 2


class QueueConflict(ValueError):
    pass


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def recipe(body, data, clip):
    intent = body.get('intent', 'coaching')
    if intent not in ('coaching', 'share'):
        raise ValueError('Invalid export intent')
    auto_detect = body.get('auto_detect', False)
    if type(auto_detect) is not bool:
        raise ValueError('Invalid auto_detect flag')
    if auto_detect and intent != 'coaching':
        raise ValueError('Auto detect is only supported for coaching exports')
    start, end = body.get('start'), body.get('end')
    max_seconds = 120 if intent == 'share' else 30
    if not number(start) or not number(end) or not 0 <= start < end <= clip['duration_seconds'] + .05 or end-start > max_seconds:
        raise ValueError(f'Select between 0.1 and {max_seconds} seconds within this clip')
    if end-start < .1:
        raise ValueError('Select at least 0.1 seconds')
    speed = body.get('speed', 1)
    if not number(speed) or not .1 <= speed <= 1:
        raise ValueError('Speed must be between 0.1 and 1')
    crop = body.get('crop', [0, 0, 1, 1])
    if not isinstance(crop, list) or len(crop) != 4 or not all(number(v) for v in crop):
        raise ValueError('Invalid focus area')
    x, y, w, h = crop
    if min(x,y) < 0 or min(w,h) < .05 or x+w > 1.00001 or y+h > 1.00001:
        raise ValueError('Focus area must be within the video')
    question=body.get('question','')
    if not isinstance(question,str) or len(question)>4000:
        raise ValueError('Question must be at most 4000 characters')
    if auto_detect:
        if not question.strip():
            raise ValueError('Enter a question up to 4000 characters')
        if body.get('consent') is not True:
            raise ValueError('Confirm sending selected frames and your question to OpenAI')
    auto_slot = body.get('auto_slot')
    if auto_slot is not None and (type(auto_slot) is not int or auto_slot not in (1, 2)):
        raise ValueError('Invalid auto coach slot')
    quality = body.get('quality', 'high' if intent == 'share' else 'balanced')
    if intent == 'share':
        if quality != 'high':
            raise ValueError('Invalid quality')
    elif quality not in ('balanced', 'small'):
        raise ValueError('Invalid quality')
    spec = dict(start=start, end=end, speed=speed, crop=crop, quality=quality,
                schema_version=1, question=question.strip(), source_id=data['id'], clip_id=clip['id'], publication=data['published_at'],
                original_start=min(clip['original_end_seconds'],clip['original_start_seconds']+start),
                original_end=min(clip['original_end_seconds'],clip['original_start_seconds']+end),
                media_ref={'storage_id':'library', 'path':data.get('media_directory',data['id'])+'/'+clip['file']},
                input_sha256=data.get('media_checksums',{}).get(clip['file']),
                hdr=bool(data['video'].get('hdr')),
                audio='retime_if_present' if intent == 'share' else 'removed',
                color='preserve-hdr-or-sdr' if intent == 'share' else 'sdr-bt709',
                analysis_mode='none' if intent == 'share' else 'timestamped_frames',
                renderer_version=3 if intent == 'share' else 1,
                source_sha256=data.get('source',{}).get('sha256',data['id']),processing=data.get('processing'))
    if auto_detect:
        spec['auto_detect'] = True
        spec['consent'] = True
        if auto_slot is not None:
            spec['auto_slot'] = auto_slot
    if intent == 'share':
        spec.update(intent='share', required_capability=RENDER_SHARE_CAPABILITY)
    return spec


class Coaching(Annotations):
    def __init__(self, path, clock=time.time):
        self.path=str(path); self.clock=clock
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            # SQLite can reject a concurrent journal-mode transition before its busy handler runs.
            for attempt in range(10):
                try:
                    db.execute('PRAGMA journal_mode=WAL');break
                except sqlite3.OperationalError as error:
                    if 'locked' not in str(error) or attempt==9:raise
                    time.sleep(.02*(attempt+1))
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, role TEXT, state TEXT, body TEXT, request_id TEXT UNIQUE, fingerprint TEXT, worker TEXT, token TEXT, expires REAL, version INTEGER, updated REAL, result TEXT, asked_at REAL)')
            if 'asked_at' not in [r['name'] for r in db.execute('PRAGMA table_info(jobs)')]:
                db.execute('ALTER TABLE jobs ADD COLUMN asked_at REAL')
            db.execute('CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, role TEXT, updated REAL, status TEXT)')
            if 'capabilities' not in [r['name'] for r in db.execute('PRAGMA table_info(workers)')]:
                db.execute("ALTER TABLE workers ADD COLUMN capabilities TEXT DEFAULT '[]'")
            self.init_annotations(db)
            db.commit()

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None);db.row_factory=sqlite3.Row
        try: yield db
        finally: db.close()

    def decode(self,row,private=False):
        d=dict(row);d['body']=json.loads(d['body']);d['result']=json.loads(d['result']) if d['result'] else None
        d['asked_at']=d.get('asked_at')
        if not private:
            d.pop('token',None); d.pop('fingerprint',None);d.pop('request_id',None)
        return d

    def active_auto_coach_slots(self, db, source_id, publication, clip_id, exclude_export_id=None):
        ai_count = db.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE role = 'ai'
              AND json_extract(body, '$.source_id') = ?
              AND json_extract(body, '$.publication') = ?
              AND json_extract(body, '$.clip_id') = ?
              AND json_extract(body, '$.auto_detect') = 1
              AND (
                state IN ('queued', 'leased', 'calling', 'completed', 'needs_review')
                OR (state = 'failed' AND json_extract(result, '$.provider_called') = 1)
              )
            """,
            (source_id, publication, clip_id),
        ).fetchone()[0]
        render_count = db.execute(
            """
            SELECT COUNT(*) FROM jobs AS r
            WHERE r.role = 'render'
              AND json_extract(r.body, '$.source_id') = ?
              AND json_extract(r.body, '$.publication') = ?
              AND json_extract(r.body, '$.clip_id') = ?
              AND json_extract(r.body, '$.auto_detect') = 1
              AND r.state IN ('queued', 'leased', 'completed')
              AND (? IS NULL OR r.id != ?)
              AND NOT EXISTS (
                SELECT 1 FROM jobs AS a
                WHERE a.role = 'ai'
                  AND json_extract(a.body, '$.export_id') = r.id
              )
            """,
            (source_id, publication, clip_id, exclude_export_id, exclude_export_id),
        ).fetchone()[0]
        return ai_count + render_count

    def create(self,role,body,request_id):
        if not isinstance(request_id,str) or not 8 <= len(request_id) <= 100:
            raise ValueError('A request ID is required')
        encoded=json.dumps(body,sort_keys=True);fingerprint=hashlib.sha256((role+encoded).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM jobs WHERE request_id=?',(request_id,)).fetchone()
            if row:
                if row['fingerprint']!=fingerprint: raise QueueConflict('Request ID already used for a different selection')
                return self.decode(row)
            export_id=body.get('export_id') if role=='ai' else None
            render_body={}
            if role=='ai' and export_id is not None:
                render_row=db.execute('SELECT * FROM jobs WHERE id=?',(export_id,)).fetchone()
                if render_row is not None or body.get('auto_detect') or 'artifact_directory' in body:
                    if not render_row or render_row['role']!='render' or render_row['state']!='completed':
                        raise QueueConflict('Prepare an excerpt first')
                    render_body=json.loads(render_row['body'])
                    render_result=json.loads(render_row['result']) if render_row['result'] else {}
                    if render_body.get('intent')=='share':
                        raise ValueError('Share downloads cannot be used as coaching evidence')
                    if (
                        render_body.get('source_id')!=body.get('source_id')
                        or render_body.get('publication')!=body.get('publication')
                        or render_body.get('clip_id')!=body.get('clip_id')
                        or ('artifact_directory' in body and render_result.get('artifact_directory')!=body.get('artifact_directory'))
                    ):
                        raise QueueConflict('Excerpt identity mismatch; prepare a fresh excerpt')
                    if 'player_context' in render_body:
                        if render_body.get('player_context')!=body.get('player_context'):
                            raise QueueConflict('Selected player profile changed. Refresh the review before asking.' if (render_body.get('subject_mode') or body.get('subject_mode'))=='selected_player' else 'Main player or profile changed. Refresh the review before asking.')
                        if 'subject_mode' in render_body and render_body.get('subject_mode')!=body.get('subject_mode'):
                            raise QueueConflict('Coaching subject changed. Refresh the review before asking.')
                        if body.get('follow_up_of') is None and render_body.get('subject_hint')!=body.get('subject_hint'):
                            raise QueueConflict('Coaching subject changed. Refresh the review before asking.')
                    if render_body.get('auto_detect') and not body.get('auto_detect') and body.get('follow_up_of') is None:
                        raise ValueError('Automatic coaching exports must be submitted as automatic coaching requests')
            if role=='ai' and body.get('follow_up_of') is not None:
                parent_row=db.execute('SELECT * FROM jobs WHERE id=?',(body.get('follow_up_of'),)).fetchone()
                if not parent_row or parent_row['role']!='ai' or parent_row['state']!='completed':
                    raise QueueConflict('Follow-ups must refer to a completed response for this excerpt')
                parent_body=json.loads(parent_row['body'])
                parent_eff_mode=parent_body.get('subject_mode') or ('main_player' if parent_body.get('player_context') else 'none')
                curr_eff_mode=body.get('subject_mode') or ('main_player' if body.get('player_context') else 'none')
                if (
                    parent_body.get('export_id')!=export_id
                    or parent_body.get('publication')!=body.get('publication')
                    or parent_body.get('player_context')!=body.get('player_context')
                    or parent_eff_mode!=curr_eff_mode
                ):
                    raise QueueConflict('Follow-ups must refer to a completed response for this excerpt')
            if body.get('auto_detect'):
                source_id=body.get('source_id')
                publication=body.get('publication')
                clip_id=body.get('clip_id')
                if role=='ai':
                    if not export_id:
                        raise ValueError('Automatic coaching requests require a completed automatic export')
                    if not render_body.get('auto_detect'):
                        raise ValueError('Automatic coaching requests require an automatic coaching export')
                    existing_ai=db.execute(
                        """
                        SELECT id FROM jobs
                        WHERE role = 'ai'
                          AND json_extract(body, '$.export_id') = ?
                          AND json_extract(body, '$.auto_detect') = 1
                          AND (
                            state IN ('queued', 'leased', 'calling', 'completed', 'needs_review')
                            OR (state = 'failed' AND json_extract(result, '$.provider_called') = 1)
                          )
                        """,
                        (export_id,),
                    ).fetchone()
                    if existing_ai:
                        raise QueueConflict('An automatic coaching request already exists for this excerpt')
                used=self.active_auto_coach_slots(db,source_id,publication,clip_id,exclude_export_id=export_id)
                if used>=MAX_AUTO_REVIEWS_PER_CLIP:
                    raise QueueConflict(f'Auto coach is limited to {MAX_AUTO_REVIEWS_PER_CLIP} reviews per clip')
            identity=uuid.uuid4().hex
            now=self.clock()
            db.execute('INSERT INTO jobs(id,role,state,body,request_id,fingerprint,worker,token,expires,version,updated,result,asked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(identity,role,'queued',encoded,request_id,fingerprint,None,None,0,1,now,None,now))
            db.commit()
        return self.get(identity)

    def get(self,identity):
        with self.db() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
        if not row: raise KeyError(identity)
        return self.decode(row)

    def listing(self,source):
        with self.db() as db:
            rows=db.execute(
                """
                SELECT * FROM jobs
                WHERE json_extract(body,'$.source_id')=?
                  AND (
                    role='ai'
                    OR (
                      role='render'
                      AND json_extract(body,'$.auto_detect')=1
                      AND state IN ('queued','leased','completed')
                      AND NOT EXISTS (
                        SELECT 1 FROM jobs AS a
                        WHERE a.role='ai'
                          AND json_extract(a.body,'$.export_id')=jobs.id
                      )
                    )
                    OR id IN (
                      SELECT id FROM jobs
                      WHERE json_extract(body,'$.source_id')=?
                      ORDER BY COALESCE(asked_at,updated) DESC, id DESC
                      LIMIT 100
                    )
                  )
                ORDER BY COALESCE(asked_at,updated) DESC, id DESC
                """,
                (source,source),
            ).fetchall()
            workers=[dict(r) for r in db.execute('SELECT * FROM workers')]
        jobs=[self.decode(r) for r in rows]
        for job in jobs:
            for field in ('submitted_prompt','response_contract'):job['body'].pop(field,None)
        return dict(jobs=jobs,workers=[dict(w,online=self.clock()-w['updated']<90) for w in workers])

    def claim(self,role,worker,capabilities=None):
        capabilities=[] if capabilities is None else capabilities
        if not isinstance(capabilities,list) or len(capabilities)>20 or any(not isinstance(c,str) or len(c)>80 for c in capabilities):raise ValueError('Invalid capabilities')
        if role not in ('render','ai') or not isinstance(worker,str) or not 1<=len(worker)<=80:
            raise ValueError('Invalid worker')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            now=self.clock()
            # Expired provider calls are ambiguous. Never automatically spend quota twice.
            db.execute("UPDATE jobs SET state=CASE WHEN state='calling' THEN 'needs_review' ELSE 'queued' END,token=NULL,version=version+1,updated=? WHERE role=? AND state IN ('leased','calling') AND expires<?",(now,role,now))
            db.execute('INSERT OR REPLACE INTO workers(id,role,updated,status,capabilities) VALUES(?,?,?,?,?)',(worker,role,now,'ready',json.dumps(capabilities)))
            row=db.execute("SELECT * FROM jobs WHERE role=? AND state='queued' AND (json_extract(body,'$.required_capability') IS NULL OR json_extract(body,'$.required_capability') IN (SELECT value FROM json_each(?))) ORDER BY updated LIMIT 1",(role,json.dumps(capabilities))).fetchone()
            if not row: db.commit(); return None
            token=secrets.token_urlsafe(32)
            db.execute("UPDATE jobs SET state='leased',worker=?,token=?,expires=?,version=version+1,updated=? WHERE id=?",(worker,token,now+90,now,row['id']))
            result=self.decode(db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone(),True);db.commit();return result

    def worker_update(self,identity,token,action,result=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
            if row and token and row['token']==token and row['state']=='completed' and action=='complete':
                return self.decode(row)
            if not row or not token or row['token']!=token or row['expires']<self.clock() or row['state'] not in ('leased','calling'):
                raise QueueConflict('Lease expired or task cancelled; result was not accepted')
            state=row['state'];body=json.loads(row['body'])
            if action=='calling':
                if row['role']!='ai' or state!='leased':raise QueueConflict('Provider call already started')
                if body.get('evidence_frames') and not db.execute('SELECT 1 FROM evidence WHERE job_id=?',(identity,)).fetchone():raise ValueError('Retain submitted evidence before calling the provider')
                state='calling'
            elif action=='complete':
                if row['role']=='ai' and state!='calling':raise QueueConflict('Provider call not started')
                if row['role']=='ai':
                    result=self.save_learning_plan(db,row,result) if body.get('purpose')=='learning_plan' else self.save_annotations(db,row,result)
                state='completed'
            elif action=='evidence':
                if row['role']!='ai' or state!='leased' or body.get('purpose')=='learning_plan':raise QueueConflict('Evidence can only be registered before the provider call')
                self.pin_evidence(db,self.decode(row),result)
                result=None
            elif action=='fail':
                if row['role']=='ai' and state=='calling':
                    result=dict(result or {},provider_called=True)
                state='failed'
            elif action!='heartbeat':raise ValueError('Invalid worker action')
            db.execute('UPDATE jobs SET state=?,result=?,expires=?,updated=?,version=version+1 WHERE id=?',(state,json.dumps(result) if result is not None else row['result'],self.clock()+90,self.clock(),identity))
            db.execute('UPDATE workers SET updated=?,status=? WHERE id=?',(self.clock(),state,row['worker']))
            db.commit()
        return self.get(identity)

    def edit(self,identity,version,action,cue=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
            if not row: raise KeyError(identity)
            if type(version)!=int or row['version']!=version:raise QueueConflict('This request changed. Reload its latest state')
            state=row['state'];body=json.loads(row['body']);result=json.loads(row['result']) if row['result'] else {}
            if action=='cancel':
                if state in ('queued','leased'):
                    state='cancelled'
                elif (
                    row['role']=='render'
                    and state=='completed'
                    and body.get('auto_detect')
                    and not db.execute("SELECT 1 FROM jobs WHERE role='ai' AND json_extract(body,'$.export_id')=?",(identity,)).fetchone()
                ):
                    state='cancelled'
                else:
                    raise QueueConflict('A provider call already started; wait for its result')
            elif action=='cue':
                if body.get('purpose')=='learning_plan' or result.get('structured') or result.get('structured_plan') or state!='completed' or row['role']!='ai' or not isinstance(cue,str) or len(cue)>400:raise ValueError('Invalid coaching cue')
                result['cue']=cue.strip();result['cue_reviewed']=bool(cue.strip())
            else:raise ValueError('Invalid action')
            db.execute('UPDATE jobs SET state=?,result=?,token=NULL,updated=?,version=version+1 WHERE id=?',(state,json.dumps(result),self.clock(),identity));db.commit()
        return self.get(identity)
