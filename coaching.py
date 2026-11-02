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


class QueueConflict(ValueError):
    pass


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def recipe(body, data, clip):
    start, end = body.get('start'), body.get('end')
    if not number(start) or not number(end) or not 0 <= start < end <= clip['duration_seconds'] + .05 or end-start > 30:
        raise ValueError('Select between 0.1 and 30 seconds within this clip')
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
    quality = body.get('quality', 'balanced')
    if quality not in ('balanced', 'small'):
        raise ValueError('Invalid quality')
    return dict(start=start, end=end, speed=speed, crop=crop, quality=quality,
                schema_version=1, question=question.strip(), source_id=data['id'], clip_id=clip['id'], publication=data['published_at'],
                original_start=min(clip['original_end_seconds'],clip['original_start_seconds']+start),
                original_end=min(clip['original_end_seconds'],clip['original_start_seconds']+end),
                media_ref={'storage_id':'library', 'path':data.get('media_directory',data['id'])+'/'+clip['file']},
                input_sha256=data.get('media_checksums',{}).get(clip['file']),
                hdr=bool(data['video'].get('hdr')), audio='removed', color='sdr-bt709',
                analysis_mode='timestamped_frames', renderer_version=1)


class Coaching:
    def __init__(self, path, clock=time.time):
        self.path=str(path); self.clock=clock
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, role TEXT, state TEXT, body TEXT, request_id TEXT UNIQUE, fingerprint TEXT, worker TEXT, token TEXT, expires REAL, version INTEGER, updated REAL, result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, role TEXT, updated REAL, status TEXT)')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None);db.row_factory=sqlite3.Row
        try: yield db
        finally: db.close()

    def decode(self,row,private=False):
        d=dict(row);d['body']=json.loads(d['body']);d['result']=json.loads(d['result']) if d['result'] else None
        if not private:
            d.pop('token',None); d.pop('fingerprint',None);d.pop('request_id',None)
        return d

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
            identity=uuid.uuid4().hex
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(identity,role,'queued',encoded,request_id,fingerprint,None,None,0,1,self.clock(),None))
            db.commit()
        return self.get(identity)

    def get(self,identity):
        with self.db() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
        if not row: raise KeyError(identity)
        return self.decode(row)

    def listing(self,source):
        with self.db() as db:
            rows=db.execute("SELECT * FROM jobs WHERE json_extract(body,'$.source_id')=? ORDER BY updated DESC LIMIT 100",(source,)).fetchall()
            workers=[dict(r) for r in db.execute('SELECT * FROM workers')]
        return dict(jobs=[self.decode(r) for r in rows],workers=[dict(w,online=self.clock()-w['updated']<90) for w in workers])

    def claim(self,role,worker):
        if role not in ('render','ai') or not isinstance(worker,str) or not 1<=len(worker)<=80:
            raise ValueError('Invalid worker')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            now=self.clock()
            # Expired provider calls are ambiguous. Never automatically spend quota twice.
            db.execute("UPDATE jobs SET state=CASE WHEN state='calling' THEN 'needs_review' ELSE 'queued' END,token=NULL,version=version+1,updated=? WHERE role=? AND state IN ('leased','calling') AND expires<?",(now,role,now))
            db.execute('INSERT OR REPLACE INTO workers VALUES(?,?,?,?)',(worker,role,now,'ready'))
            row=db.execute("SELECT * FROM jobs WHERE role=? AND state='queued' ORDER BY updated LIMIT 1",(role,)).fetchone()
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
                state='calling'
            elif action=='complete':
                if row['role']=='ai' and state!='calling':raise QueueConflict('Provider call not started')
                state='completed'
            elif action=='fail':state='failed'
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
            state=row['state'];result=json.loads(row['result']) if row['result'] else {}
            if action=='cancel':
                if state not in ('queued','leased'):raise QueueConflict('A provider call already started; wait for its result')
                state='cancelled'
            elif action=='cue':
                if state!='completed' or row['role']!='ai' or not isinstance(cue,str) or len(cue)>400:raise ValueError('Invalid coaching cue')
                result['cue']=cue.strip();result['cue_reviewed']=bool(cue.strip())
            else:raise ValueError('Invalid action')
            db.execute('UPDATE jobs SET state=?,result=?,token=NULL,updated=?,version=version+1 WHERE id=?',(state,json.dumps(result),self.clock(),identity));db.commit()
        return self.get(identity)
