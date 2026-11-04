"""Durable evidence and reviewed annotations in the dedicated coaching database."""
import json
from worker.tennis_coach.annotations import canonical, identity, evidence_manifest, parse_response, normalize_suggestion, coaching_prompt, response_schema


class Annotations:
    def init_annotations(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS evidence(job_id TEXT PRIMARY KEY, manifest TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source_id TEXT NOT NULL, proposal TEXT NOT NULL, current TEXT NOT NULL, status TEXT NOT NULL, reviewed_publication REAL, version INTEGER NOT NULL, updated REAL NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS annotations_source ON annotations(source_id)')
        db.execute('CREATE TABLE IF NOT EXISTS annotation_revisions(annotation_id TEXT, version INTEGER, value TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(annotation_id, version))')

    def evidence(self, job_id):
        with self.db() as db:
            row = db.execute('SELECT manifest FROM evidence WHERE job_id=?', (job_id,)).fetchone()
        if not row: raise KeyError(job_id)
        return json.loads(row['manifest'])

    def pin_evidence(self, db, job, manifest):
        if manifest != evidence_manifest(job): raise ValueError('Evidence does not match the requested frame sequence')
        encoded = canonical(manifest)
        row = db.execute('SELECT manifest FROM evidence WHERE job_id=?', (job['id'],)).fetchone()
        if row and row['manifest'] != encoded: raise ValueError('Submitted evidence is immutable')
        db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?)', (job['id'], encoded))

    def save_annotations(self, db, row, result):
        body = json.loads(row['body'])
        if not body.get('evidence_frames'): return result
        evidence = db.execute('SELECT manifest FROM evidence WHERE job_id=?', (row['id'],)).fetchone()
        if not evidence: raise ValueError('Submitted evidence must be retained before completing a response')
        parsed = parse_response(result['answer'], json.loads(evidence['manifest']))
        result = dict(result, structured=parsed)
        for suggestion in parsed['suggestions']:
            key = 'a_' + identity([row['id'], suggestion['response_index']])[:32]
            encoded = canonical(suggestion)
            db.execute('INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?,?)',
                       (key, row['id'], body['source_id'], encoded, encoded, 'proposed', None, 1, self.clock()))
            self.revision(db, key, 1, suggestion, 'proposed', None)
        return result

    def revision(self, db, key, version, value, status, publication, actor=None):
        data = dict(content=value, status=status, reviewed_publication=publication, actor=actor or ('human' if version > 1 else 'ai_proposal'), training_label_status='not_released')
        db.execute('INSERT INTO annotation_revisions VALUES(?,?,?,?)', (key, version, canonical(data), self.clock()))

    def annotation_list(self, source, publication, clips, after=None, limit=201):
        with self.db() as db:
            if after is None:
                rows = db.execute('SELECT a.*, j.body FROM annotations a JOIN jobs j ON j.id=a.job_id WHERE a.source_id=? ORDER BY a.updated DESC', (source,)).fetchall()
            else:
                rows = db.execute('SELECT a.*, j.body FROM annotations a JOIN jobs j ON j.id=a.job_id WHERE a.source_id=? AND a.id>? ORDER BY a.id LIMIT ?', (source,after,limit)).fetchall()
        result = []
        for row in rows:
            a = dict(row); body = json.loads(a.pop('body'))
            a['proposal'] = json.loads(a['proposal']); a['current'] = json.loads(a['current'])
            a['provenance'] = {k: body.get(k) for k in ('source_id','source_sha256','clip_id','publication','export_id','prompt_version','processing','player_context','original_start','original_end')}
            # Even an untimed proposal retains its historical selection without inventing precise cue timing.
            intervals = a['current']['applies_to'] or [dict(start_seconds=body['original_start'], end_seconds=body['original_end'])]
            a['related_clip_ids'] = [c['id'] for c in clips if any(i['start_seconds'] <= c['original_end_seconds'] and i['end_seconds'] >= c['original_start_seconds'] for i in intervals)]
            context = body.get('player_context')
            related = [c for c in clips if c['id'] in a['related_clip_ids']]
            a['player_matches_current'] = not context or bool(related) and all((c.get('main_player') or {}).get('id') == context['player_id'] for c in related)
            a['historical'] = body['publication'] != publication
            a['playback_eligible'] = a['player_matches_current'] and a['status'] == 'accepted' and a['reviewed_publication'] == publication and bool(a['current']['applies_to'])
            a['training_label_status'] = 'not_released'
            result.append(a)
        return result

    def review_annotation(self, key, body, publication):
        from coaching import QueueConflict
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM annotations WHERE id=?', (key,)).fetchone()
            if not row: raise KeyError(key)
            if type(body.get('version')) != int or body['version'] != row['version']: raise QueueConflict('This suggestion changed. Reload before saving your review')
            status = body.get('status')
            if status not in ('accepted','dismissed','proposed'): raise ValueError('Invalid review status')
            manifest = json.loads(db.execute('SELECT manifest FROM evidence WHERE job_id=?', (row['job_id'],)).fetchone()['manifest'])
            current = json.loads(row['current'])
            candidate = body.get('content', current)
            # Do not silently clear model reference warnings just by pressing Accept.
            if status == 'accepted' and current.get('validation_warnings') and 'content' not in body: raise ValueError('Edit this suggestion to resolve its reference warnings before accepting')
            content = normalize_suggestion(candidate, manifest, strict=True)
            version = row['version'] + 1
            reviewed = publication if status == 'accepted' else None
            db.execute('UPDATE annotations SET current=?,status=?,reviewed_publication=?,version=?,updated=? WHERE id=?', (canonical(content),status,reviewed,version,self.clock(),key))
            self.revision(db,key,version,content,status,reviewed)
            db.commit()
        return key

    def manual_annotation(self, job, content, publication, request_id):
        from coaching import QueueConflict
        if job['role']!='ai' or job['state']!='completed' or not job['body'].get('evidence_frames'): raise ValueError('A completed response with retained evidence is required')
        if not isinstance(request_id,str) or not 8<=len(request_id)<=100:raise ValueError('A request ID is required')
        content=normalize_suggestion(content,self.evidence(job['id']),strict=True)
        key='a_'+identity([job['id'],'human',request_id])[:32]
        encoded=canonical(dict(content,origin='human'))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT proposal FROM annotations WHERE id=?',(key,)).fetchone()
            if previous:
                if previous['proposal']!=encoded:raise QueueConflict('Request ID already used for a different suggestion')
                return key
            db.execute('INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?,?)',(key,job['id'],job['body']['source_id'],encoded,canonical(content),'accepted',publication,1,self.clock()))
            self.revision(db,key,1,content,'accepted',publication,'human')
            db.commit()
        return key

    def annotation_export(self, source, after='', limit=100):
        # Page by immutable request ID so empty/invalid/legacy responses are also exportable.
        with self.db() as db:
            jobs=db.execute("SELECT * FROM jobs WHERE role='ai' AND json_extract(body,'$.source_id')=? AND id>? ORDER BY id LIMIT ?",(source,after,limit+1)).fetchall()
            items,responses=[],[]
            for row in jobs[:limit]:
                job=self.decode(row)
                saved=db.execute('SELECT manifest FROM evidence WHERE job_id=?',(row['id'],)).fetchone()
                manifest=json.loads(saved['manifest']) if saved else None
                responses.append(dict(request=job,evidence=manifest))
                for a in db.execute('SELECT * FROM annotations WHERE job_id=? ORDER BY id',(row['id'],)):
                    value=dict(a)
                    for key in ('proposal','current'):value[key]=json.loads(value[key])
                    value.update(request=job,evidence=manifest,submitted_prompt=job['body'].get('submitted_prompt'),response_schema=job['body'].get('response_contract'),training_label_status='not_released')
                    value['revisions']=[dict(version=r['version'],created=r['created'],**json.loads(r['value'])) for r in db.execute('SELECT * FROM annotation_revisions WHERE annotation_id=? ORDER BY version',(a['id'],))]
                    items.append(value)
        return dict(schema_version=1,annotations=items,responses=responses,next_after=jobs[limit-1]['id'] if len(jobs)>limit else None)
