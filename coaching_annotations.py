"""Durable evidence, reviewed annotations, and player learning plans in the dedicated coaching database."""
import json
from worker.tennis_coach.annotations import (
    canonical,
    identity,
    evidence_manifest,
    parse_response,
    normalize_suggestion,
    coaching_prompt,
    response_schema,
    LEARNING_PLAN_CAPABILITY,
    LEARNING_PLAN_PROMPT_VERSION,
    LEARNING_PLAN_SCHEMA_VERSION,
    learning_plan_schema,
    learning_plan_prompt,
    parse_learning_plan,
)

PRACTICE_STATUSES = ('not_started', 'ongoing', 'practiced', 'working_well', 'archived')
MAX_PLAN_EVIDENCE_ITEMS = 24
MAX_PLAN_EVIDENCE_BYTES = 48000
MAX_PLAN_CARRIED_CITATIONS = 60
MAX_PLAN_CARRIED_BYTES = 24000
MAX_PLAN_REMOVED_ITEMS = 64
MAX_PLAN_REMOVED_BYTES = 8000
MAX_PLAN_PRACTICE_LOGS = 20
MAX_PLAN_PRACTICE_BYTES = 16000


class Annotations:
    def init_annotations(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS evidence(job_id TEXT PRIMARY KEY, manifest TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source_id TEXT NOT NULL, proposal TEXT NOT NULL, current TEXT NOT NULL, status TEXT NOT NULL, reviewed_publication REAL, version INTEGER NOT NULL, updated REAL NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS annotations_source ON annotations(source_id)')
        db.execute('CREATE TABLE IF NOT EXISTS annotation_revisions(annotation_id TEXT, version INTEGER, value TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(annotation_id, version))')
        db.execute(
            'CREATE TABLE IF NOT EXISTS learning_plans('
            'player_id TEXT PRIMARY KEY, active_revision INTEGER NOT NULL, active_job_id TEXT, '
            'covered_state_digest TEXT NOT NULL, request_digest TEXT NOT NULL, snapshot_json TEXT NOT NULL, '
            'plan_json TEXT NOT NULL, provenance_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)'
        )
        db.execute(
            'CREATE TABLE IF NOT EXISTS learning_plan_revisions('
            'player_id TEXT NOT NULL, revision INTEGER NOT NULL, job_id TEXT NOT NULL UNIQUE, '
            'parent_revision INTEGER, status TEXT NOT NULL, covered_state_digest TEXT NOT NULL, '
            'request_digest TEXT NOT NULL, snapshot_json TEXT NOT NULL, plan_json TEXT NOT NULL, '
            'provenance_json TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(player_id, revision))'
        )
        db.execute(
            'CREATE TABLE IF NOT EXISTS learning_practice_logs('
            'id TEXT PRIMARY KEY, player_id TEXT NOT NULL, item_key TEXT NOT NULL, origin TEXT NOT NULL, '
            'title TEXT NOT NULL, topic TEXT NOT NULL, observed_issue TEXT NOT NULL, cue TEXT NOT NULL, '
            'drill TEXT NOT NULL, uncertainty TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, '
            'evidence_ids_json TEXT NOT NULL, origin_revision INTEGER, last_plan_revision INTEGER, '
            'user_edited_content INTEGER NOT NULL DEFAULT 0, user_version INTEGER NOT NULL DEFAULT 0, '
            'version INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, request_id TEXT, '
            'UNIQUE(player_id, item_key))'
        )
        db.execute('CREATE INDEX IF NOT EXISTS learning_practice_logs_player ON learning_practice_logs(player_id, updated_at DESC)')
        db.execute(
            'CREATE TABLE IF NOT EXISTS learning_practice_revisions('
            'log_id TEXT NOT NULL, version INTEGER NOT NULL, player_id TEXT NOT NULL, item_key TEXT NOT NULL, '
            'actor TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, value_json TEXT NOT NULL, '
            'created_at REAL NOT NULL, PRIMARY KEY(log_id, version))'
        )

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
                rows = db.execute('SELECT a.*, j.body, j.asked_at FROM annotations a JOIN jobs j ON j.id=a.job_id WHERE a.source_id=? ORDER BY COALESCE(j.asked_at, a.updated) DESC, a.updated DESC, a.id DESC', (source,)).fetchall()
            else:
                rows = db.execute('SELECT a.*, j.body, j.asked_at FROM annotations a JOIN jobs j ON j.id=a.job_id WHERE a.source_id=? AND a.id>? ORDER BY a.id LIMIT ?', (source,after,limit)).fetchall()
        result = []
        for row in rows:
            a = dict(row); body = json.loads(a.pop('body')); asked_at = a.pop('asked_at', None)
            a['asked_at'] = asked_at
            a['proposal'] = json.loads(a['proposal']); a['current'] = json.loads(a['current'])
            a['provenance'] = {k: body.get(k) for k in ('source_id','source_sha256','clip_id','publication','export_id','prompt_version','processing','player_context','original_start','original_end')}
            a['provenance']['asked_at'] = asked_at
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
        if job['role']!='ai' or job['state']!='completed' or job['body'].get('purpose')=='learning_plan' or not job['body'].get('source_id') or not job['body'].get('evidence_frames'):
            raise ValueError('A completed response with retained evidence is required')
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

    def _practice_revision(self, db, log_id, version, player_id, item_key, actor, status, note, content, created_at):
        payload = dict(
            title=content['title'],
            topic=content['topic'],
            observed_issue=content.get('observed_issue', ''),
            cue=content['cue'],
            drill=content['drill'],
            uncertainty=content.get('uncertainty', ''),
            evidence_ids=content.get('evidence_ids', []),
            origin_revision=content.get('origin_revision'),
            last_plan_revision=content.get('last_plan_revision'),
            user_edited_content=bool(content.get('user_edited_content')),
            user_version=content.get('user_version', 0),
            training_label_status='not_released',
        )
        db.execute(
            'INSERT INTO learning_practice_revisions(log_id,version,player_id,item_key,actor,status,note,value_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (log_id, version, player_id, item_key, actor, status, note, canonical(payload), created_at),
        )

    def _decode_practice_log(self, db, row, include_revisions=True):
        d = dict(row)
        d['evidence_ids'] = json.loads(d.pop('evidence_ids_json', '[]'))
        d['user_edited_content'] = bool(d.get('user_edited_content'))
        d.pop('request_id', None)
        d['training_label_status'] = 'not_released'
        if include_revisions:
            rev_rows = db.execute(
                'SELECT version, actor, status, note, value_json, created_at FROM learning_practice_revisions WHERE log_id=? ORDER BY version DESC LIMIT 50',
                (d['id'],),
            ).fetchall()
            d['revisions'] = [
                dict(
                    version=r['version'],
                    actor=r['actor'],
                    status=r['status'],
                    note=r['note'],
                    created_at=r['created_at'],
                    **json.loads(r['value_json']),
                )
                for r in rev_rows
            ]
        return d

    def practice_log_list(self, player_id, include_revisions=True):
        with self.db() as db:
            rows = db.execute(
                'SELECT * FROM learning_practice_logs WHERE player_id=? ORDER BY updated_at DESC, id DESC',
                (player_id,),
            ).fetchall()
            return [self._decode_practice_log(db, r, include_revisions=include_revisions) for r in rows]

    def practice_log_revisions(self, player_id, log_id, after=0):
        with self.db() as db:
            owner = db.execute('SELECT 1 FROM learning_practice_logs WHERE id=? AND player_id=?', (log_id, player_id)).fetchone()
            if not owner:
                raise KeyError(log_id)
            rows = db.execute(
                'SELECT version, actor, status, note, value_json, created_at FROM learning_practice_revisions WHERE log_id=? AND version>? ORDER BY version ASC LIMIT 101',
                (log_id, after),
            ).fetchall()
        values = [
            dict(
                version=r['version'],
                actor=r['actor'],
                status=r['status'],
                note=r['note'],
                created_at=r['created_at'],
                **json.loads(r['value_json']),
            )
            for r in rows
        ]
        return dict(revisions=values[:100], next_after=values[99]['version'] if len(values) > 100 else None)

    def create_practice_log(self, player_id, body):
        from coaching import QueueConflict
        if not isinstance(body, dict):
            raise ValueError('Expected an object')
        request_id = body.get('request_id')
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100:
            raise ValueError('A request ID is required')
        title = body.get('title', '')
        topic = body.get('topic', '')
        cue = body.get('cue', '')
        drill = body.get('drill', '')
        status = body.get('status', 'ongoing')
        note = body.get('note', '')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError('Enter a practice focus title up to 120 characters')
        if not isinstance(topic, str) or len(topic.strip()) > 80:
            raise ValueError('Topic must be at most 80 characters')
        if not isinstance(cue, str) or len(cue.strip()) > 400:
            raise ValueError('Cue must be at most 400 characters')
        if not isinstance(drill, str) or len(drill.strip()) > 1200:
            raise ValueError('Drill must be at most 1200 characters')
        if status not in PRACTICE_STATUSES:
            raise ValueError('Choose a valid practice status')
        if not isinstance(note, str) or len(note.strip()) > 1000:
            raise ValueError('Practice note must be at most 1000 characters')
        title, topic, cue, drill, note = title.strip(), topic.strip(), cue.strip(), drill.strip(), note.strip()
        item_key = 'manual_' + identity([player_id, request_id])[:16]
        log_id = 'pl_' + identity([player_id, item_key])[:24]
        spec_sig = canonical(dict(title=title, topic=topic, cue=cue, drill=drill, status=status, note=note))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM learning_practice_logs WHERE id=?', (log_id,)).fetchone()
            if existing:
                first_rev = db.execute('SELECT status, note, value_json FROM learning_practice_revisions WHERE log_id=? AND version=1', (log_id,)).fetchone()
                if first_rev:
                    first_val = json.loads(first_rev['value_json'])
                    existing_sig = canonical(dict(
                        title=first_val.get('title', ''),
                        topic=first_val.get('topic', ''),
                        cue=first_val.get('cue', ''),
                        drill=first_val.get('drill', ''),
                        status=first_rev['status'],
                        note=first_rev['note'],
                    ))
                    if existing_sig != spec_sig:
                        raise QueueConflict('Request ID already used for a different practice item')
                return self._decode_practice_log(db, existing)
            now = self.clock()
            db.execute(
                'INSERT INTO learning_practice_logs(id,player_id,item_key,origin,title,topic,observed_issue,cue,drill,uncertainty,status,note,evidence_ids_json,origin_revision,last_plan_revision,user_edited_content,user_version,version,created_at,updated_at,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (log_id, player_id, item_key, 'manual', title, topic, '', cue, drill, '', status, note, '[]', None, None, 1, 1, 1, now, now, request_id),
            )
            self._practice_revision(
                db, log_id, 1, player_id, item_key, 'human', status, note,
                dict(title=title, topic=topic, observed_issue='', cue=cue, drill=drill, uncertainty='', evidence_ids=[], origin_revision=None, last_plan_revision=None, user_edited_content=True, user_version=1),
                now,
            )
            row = db.execute('SELECT * FROM learning_practice_logs WHERE id=?', (log_id,)).fetchone()
            result = self._decode_practice_log(db, row)
            db.commit()
        return result

    def update_practice_log(self, player_id, log_id, body):
        from coaching import QueueConflict
        if not isinstance(body, dict):
            raise ValueError('Expected an object')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM learning_practice_logs WHERE id=? AND player_id=?', (log_id, player_id)).fetchone()
            if not row:
                raise KeyError(log_id)
            if type(body.get('version')) is not int or body['version'] != row['version']:
                raise QueueConflict('This practice item changed. Reload before saving.')
            status = body.get('status', row['status'])
            note = body.get('note', row['note'])
            title = body.get('title', row['title'])
            topic = body.get('topic', row['topic'])
            cue = body.get('cue', row['cue'])
            drill = body.get('drill', row['drill'])
            if status not in PRACTICE_STATUSES:
                raise ValueError('Choose a valid practice status')
            if not isinstance(note, str) or len(note.strip()) > 1000:
                raise ValueError('Practice note must be at most 1000 characters')
            if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
                raise ValueError('Enter a practice focus title up to 120 characters')
            if not isinstance(topic, str) or len(topic.strip()) > 80:
                raise ValueError('Topic must be at most 80 characters')
            if not isinstance(cue, str) or len(cue.strip()) > 400:
                raise ValueError('Cue must be at most 400 characters')
            if not isinstance(drill, str) or len(drill.strip()) > 1200:
                raise ValueError('Drill must be at most 1200 characters')
            title, topic, cue, drill, note = title.strip(), topic.strip(), cue.strip(), drill.strip(), note.strip()
            content_edited = int(
                bool(row['user_edited_content'])
                or title != row['title']
                or topic != row['topic']
                or cue != row['cue']
                or drill != row['drill']
            )
            new_version = row['version'] + 1
            new_user_version = row['user_version'] + 1
            now = self.clock()
            db.execute(
                'UPDATE learning_practice_logs SET title=?,topic=?,cue=?,drill=?,status=?,note=?,user_edited_content=?,user_version=?,version=?,updated_at=? WHERE id=?',
                (title, topic, cue, drill, status, note, content_edited, new_user_version, new_version, now, log_id),
            )
            self._practice_revision(
                db, log_id, new_version, player_id, row['item_key'], 'human', status, note,
                dict(
                    title=title,
                    topic=topic,
                    observed_issue=row['observed_issue'],
                    cue=cue,
                    drill=drill,
                    uncertainty=row['uncertainty'],
                    evidence_ids=json.loads(row['evidence_ids_json']),
                    origin_revision=row['origin_revision'],
                    last_plan_revision=row['last_plan_revision'],
                    user_edited_content=bool(content_edited),
                    user_version=new_user_version,
                ),
                now,
            )
            updated_row = db.execute('SELECT * FROM learning_practice_logs WHERE id=?', (log_id,)).fetchone()
            result = self._decode_practice_log(db, updated_row)
            db.commit()
        return result

    def save_learning_plan(self, db, row, result):
        existing_rev = db.execute('SELECT * FROM learning_plan_revisions WHERE job_id=?', (row['id'],)).fetchone()
        if existing_rev:
            return dict(
                result,
                structured_plan=json.loads(existing_rev['plan_json']),
                plan_revision=existing_rev['revision'],
                plan_status=existing_rev['status'],
                provenance=json.loads(existing_rev['provenance_json']),
            )
        body = json.loads(row['body'])
        player_id = body['player_id']
        snapshot = body.get('snapshot') or {}
        parent_revision = body.get('parent_revision')
        covered_state_digest = body.get('covered_state_digest') or ''
        request_digest = body.get('request_digest') or ''
        parsed = parse_learning_plan(result.get('answer', ''), snapshot)
        max_row = db.execute('SELECT COALESCE(MAX(revision), 0) AS max_rev FROM learning_plan_revisions WHERE player_id=?', (player_id,)).fetchone()
        new_revision = int(max_row['max_rev']) + 1
        active_row = db.execute('SELECT active_revision FROM learning_plans WHERE player_id=?', (player_id,)).fetchone()
        current_active_rev = active_row['active_revision'] if active_row else None
        player_valid = self.player_exists(player_id) if getattr(self, 'player_exists', None) else True
        is_cas_winner = bool(player_valid and current_active_rev == parent_revision)
        is_valid_plan = bool(parsed.get('parse_status') != 'unstructured' and parsed.get('observed_summary'))
        can_activate = bool(is_cas_winner and is_valid_plan)
        existing_logs = {
            r['item_key']: dict(r)
            for r in db.execute('SELECT * FROM learning_practice_logs WHERE player_id=?', (player_id,)).fetchall()
        }
        used_keys = set()
        now = self.clock()
        for idx, focus in enumerate(parsed['focus_areas']):
            candidate_key = (focus.get('item_key') or '').strip()
            if candidate_key and candidate_key in existing_logs and candidate_key not in used_keys:
                item_key = candidate_key
                used_keys.add(item_key)
                existing = existing_logs[item_key]
                log_id = existing['id']
                if can_activate:
                    if existing['user_edited_content']:
                        title, topic, cue, drill = existing['title'], existing['topic'], existing['cue'], existing['drill']
                    else:
                        title, topic, cue, drill = focus['title'], focus['topic'], focus['cue'], focus['drill']
                    observed_issue = focus['observed_issue']
                    uncertainty = focus['uncertainty']
                    evidence_ids_json = canonical(focus['evidence_ids'])
                    changed = (
                        title != existing['title']
                        or topic != existing['topic']
                        or observed_issue != existing['observed_issue']
                        or cue != existing['cue']
                        or drill != existing['drill']
                        or uncertainty != existing['uncertainty']
                        or evidence_ids_json != existing['evidence_ids_json']
                    )
                    if changed:
                        new_ver = existing['version'] + 1
                        db.execute(
                            'UPDATE learning_practice_logs SET title=?,topic=?,observed_issue=?,cue=?,drill=?,uncertainty=?,evidence_ids_json=?,last_plan_revision=?,version=?,updated_at=? WHERE id=?',
                            (title, topic, observed_issue, cue, drill, uncertainty, evidence_ids_json, new_revision, new_ver, now, log_id),
                        )
                        self._practice_revision(
                            db, log_id, new_ver, player_id, item_key, 'ai_plan', existing['status'], existing['note'],
                            dict(
                                title=title,
                                topic=topic,
                                observed_issue=observed_issue,
                                cue=cue,
                                drill=drill,
                                uncertainty=uncertainty,
                                evidence_ids=focus['evidence_ids'],
                                origin_revision=existing['origin_revision'],
                                last_plan_revision=new_revision,
                                user_edited_content=bool(existing['user_edited_content']),
                                user_version=existing['user_version'],
                            ),
                            now,
                        )
                    else:
                        db.execute('UPDATE learning_practice_logs SET last_plan_revision=? WHERE id=?', (new_revision, log_id))
            else:
                item_key = 'fi_' + identity([player_id, row['id'], idx])[:16]
                used_keys.add(item_key)
                log_id = 'pl_' + identity([player_id, item_key])[:24]
                if can_activate:
                    db.execute(
                        'INSERT INTO learning_practice_logs(id,player_id,item_key,origin,title,topic,observed_issue,cue,drill,uncertainty,status,note,evidence_ids_json,origin_revision,last_plan_revision,user_edited_content,user_version,version,created_at,updated_at,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (log_id, player_id, item_key, 'ai_plan', focus['title'], focus['topic'], focus['observed_issue'], focus['cue'], focus['drill'], focus['uncertainty'], 'not_started', '', canonical(focus['evidence_ids']), new_revision, new_revision, 0, 0, 1, now, now, None),
                    )
                    self._practice_revision(
                        db, log_id, 1, player_id, item_key, 'ai_plan', 'not_started', '',
                        dict(
                            title=focus['title'],
                            topic=focus['topic'],
                            observed_issue=focus['observed_issue'],
                            cue=focus['cue'],
                            drill=focus['drill'],
                            uncertainty=focus['uncertainty'],
                            evidence_ids=focus['evidence_ids'],
                            origin_revision=new_revision,
                            last_plan_revision=new_revision,
                            user_edited_content=False,
                            user_version=0,
                        ),
                        now,
                    )
            focus['item_key'] = item_key
            focus['log_id'] = log_id

        revision_status = 'active' if can_activate else ('invalid' if not is_valid_plan else 'superseded')
        provenance = dict(
            job_id=row['id'],
            asked_at=row['asked_at'],
            completed_at=now,
            parent_revision=parent_revision,
            revision=new_revision,
            status=revision_status,
            covered_state_digest=covered_state_digest,
            request_digest=request_digest,
            prompt_version=body.get('prompt_version', LEARNING_PLAN_PROMPT_VERSION),
            player_profile=body.get('player_profile'),
            model=result.get('model'),
            elapsed_seconds=result.get('elapsed_seconds'),
            usage=result.get('usage'),
            delta_evidence_count=len(snapshot.get('delta_evidence', [])),
            carried_citations_count=len(snapshot.get('carried_citations', [])),
            removed_evidence_count=len(snapshot.get('removed_evidence', [])),
            covered_evidence_count=len(snapshot.get('covered_evidence_map', {})),
            truncated_evidence_count=snapshot.get('truncated_evidence_count', 0),
            truncated_logs_count=snapshot.get('truncated_logs_count', 0),
            parse_status=parsed.get('parse_status', 'valid'),
            validation_warnings=parsed.get('validation_warnings', []),
            training_label_status='not_released',
        )
        if can_activate:
            db.execute("UPDATE learning_plan_revisions SET status='historical' WHERE player_id=? AND status='active'", (player_id,))
        db.execute(
            'INSERT INTO learning_plan_revisions(player_id,revision,job_id,parent_revision,status,covered_state_digest,request_digest,snapshot_json,plan_json,provenance_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (player_id, new_revision, row['id'], parent_revision, revision_status, covered_state_digest, request_digest, canonical(snapshot), canonical(parsed), canonical(provenance), now),
        )
        if can_activate:
            db.execute(
                'INSERT INTO learning_plans(player_id,active_revision,active_job_id,covered_state_digest,request_digest,snapshot_json,plan_json,provenance_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(player_id) DO UPDATE SET active_revision=excluded.active_revision,active_job_id=excluded.active_job_id,covered_state_digest=excluded.covered_state_digest,request_digest=excluded.request_digest,snapshot_json=excluded.snapshot_json,plan_json=excluded.plan_json,provenance_json=excluded.provenance_json,updated_at=excluded.updated_at',
                (player_id, new_revision, row['id'], covered_state_digest, request_digest, canonical(snapshot), canonical(parsed), canonical(provenance), now, now),
            )
        return dict(result, structured_plan=parsed, plan_revision=new_revision, plan_status=revision_status, provenance=provenance)

    def collect_player_evidence(self, db, player_id, source_loader):
        jobs = db.execute(
            "SELECT id, body, result, version, updated, asked_at FROM jobs "
            "WHERE role='ai' AND state='completed' "
            "AND COALESCE(json_extract(body,'$.purpose'),'coaching_frames')='coaching_frames' "
            "AND json_extract(body,'$.source_id') IS NOT NULL "
            "ORDER BY COALESCE(asked_at, updated) DESC, updated DESC, id DESC"
        ).fetchall()
        annots_by_job = {}
        for a in db.execute('SELECT * FROM annotations ORDER BY updated DESC, id ASC').fetchall():
            annots_by_job.setdefault(a['job_id'], []).append(dict(a))

        sources = {}
        all_valid = {}
        invalid_reasons = {}
        for row in jobs:
            body = json.loads(row['body'])
            result = json.loads(row['result'] or '{}')
            sid = body['source_id']
            if sid not in sources:
                sources[sid] = source_loader(sid)
            data = sources[sid]
            profile = body.get('player_context') or {}
            if profile.get('player_id') not in (None, player_id):
                continue
            explicit = profile.get('player_id') == player_id
            start, end = body['original_start'], body['original_end']
            overlapping_clips = (
                [c for c in data['clips'] if max(start, c['original_start_seconds']) < min(end, c['original_end_seconds'])]
                if data else []
            )
            job_annots = annots_by_job.get(row['id'], [])
            is_structured = bool(job_annots) or isinstance(result.get('structured'), dict) or bool(body.get('evidence_frames'))
            job_evidence_ids = (['coach:' + a['id'] for a in job_annots] if is_structured else ['coach-response:' + row['id']])

            if not explicit:
                matching_clips = [c for c in overlapping_clips if (c.get('main_player') or {}).get('id') == player_id]
                if not matching_clips:
                    for eid in job_evidence_ids:
                        invalid_reasons[eid] = 'reassigned_player'
                    continue
                clips = matching_clips
            else:
                mismatch = bool(overlapping_clips and any((c.get('main_player') or {}).get('id') != player_id for c in overlapping_clips))
                if mismatch:
                    for eid in job_evidence_ids:
                        invalid_reasons[eid] = 'reassigned_player'
                    continue
                clips = overlapping_clips

            primary_clip = clips[0] if clips else None
            cid = primary_clip['id'] if primary_clip else body.get('clip_id', 'unavailable')
            clip_index = primary_clip.get('index') if primary_clip else None
            recording_title = data.get('title', 'Practice recording') if data else 'Recording unavailable'
            historical = data is None or body.get('publication') != data.get('published_at')
            media_available = primary_clip is not None

            if is_structured:
                # Never fall back to freeform coach-response when structured suggestions exist or were all dismissed.
                invalid_reasons['coach-response:' + row['id']] = 'dismissed'
                for a in job_annots:
                    eid = 'coach:' + a['id']
                    if a['status'] == 'dismissed':
                        invalid_reasons[eid] = 'dismissed'
                        continue
                    current_obj = json.loads(a['current'])
                    spans = current_obj.get('applies_to') or [dict(start_seconds=start, end_seconds=end)]
                    span_start = min(s['start_seconds'] for s in spans)
                    span_end = max(s['end_seconds'] for s in spans)
                    clamped_start = max(primary_clip['original_start_seconds'], min(primary_clip['original_end_seconds'], span_start)) if primary_clip else span_start
                    clamped_end = max(primary_clip['original_start_seconds'], min(primary_clip['original_end_seconds'], span_end)) if primary_clip else span_end
                    study_url = (
                        f"/review/{sid}?clip={cid}&coach_time={clamped_start}&coach_end={clamped_end}&coach_job={row['id']}"
                        if media_available else None
                    )
                    v_digest = identity({
                        'id': eid,
                        'version': a['version'],
                        'status': a['status'],
                        'current': current_obj,
                        'asked_at': row['asked_at'],
                    })
                    all_valid[eid] = dict(
                        evidence_id=eid,
                        kind='annotation',
                        job_id=row['id'],
                        source_id=sid,
                        clip_id=cid,
                        clip_index=clip_index,
                        recording_title=recording_title,
                        asked_at=row['asked_at'],
                        updated_at=a['updated'],
                        original_start=span_start,
                        original_end=span_end,
                        review_status=a['status'],
                        provenance_trust='user_confirmed' if a['status'] == 'accepted' else 'unconfirmed_ai_proposal',
                        version=a['version'],
                        title=(current_obj.get('title') or '')[:120],
                        topic=(current_obj.get('topic') or '')[:80],
                        observation=(current_obj.get('observation') or '')[:600],
                        cue=(current_obj.get('cue') or '')[:400],
                        drill=(current_obj.get('drill') or '')[:500],
                        uncertainty=(current_obj.get('uncertainty') or '')[:500],
                        historical=historical,
                        media_available=media_available,
                        study_url=study_url,
                        version_digest=v_digest,
                    )
            else:
                eid = 'coach-response:' + row['id']
                cue_reviewed = bool(result.get('cue_reviewed'))
                clamped_start = max(primary_clip['original_start_seconds'], min(primary_clip['original_end_seconds'], start)) if primary_clip else start
                clamped_end = max(primary_clip['original_start_seconds'], min(primary_clip['original_end_seconds'], end)) if primary_clip else end
                study_url = (
                    f"/review/{sid}?clip={cid}&coach_time={clamped_start}&coach_end={clamped_end}&coach_job={row['id']}"
                    if media_available else None
                )
                v_digest = identity({
                    'id': eid,
                    'version': row['version'],
                    'cue': result.get('cue', ''),
                    'cue_reviewed': cue_reviewed,
                    'answer_sha': identity(result.get('answer', '')),
                    'asked_at': row['asked_at'],
                })
                all_valid[eid] = dict(
                    evidence_id=eid,
                    kind='response',
                    job_id=row['id'],
                    source_id=sid,
                    clip_id=cid,
                    clip_index=clip_index,
                    recording_title=recording_title,
                    asked_at=row['asked_at'],
                    updated_at=row['updated'],
                    original_start=start,
                    original_end=end,
                    review_status='accepted' if cue_reviewed else 'saved_response',
                    provenance_trust='user_confirmed' if cue_reviewed else 'unconfirmed_ai_response',
                    version=row['version'],
                    title='Coaching response',
                    topic='',
                    observation=(result.get('answer') or '')[:600],
                    cue=(result.get('cue') or result.get('answer', '')[:160])[:400],
                    drill='',
                    uncertainty='',
                    historical=historical,
                    media_available=media_available,
                    study_url=study_url,
                    version_digest=v_digest,
                )
        return all_valid, invalid_reasons

    def _compute_plan_snapshot(self, db, player_id, player_profile, source_loader):
        all_valid, invalid_reasons = self.collect_player_evidence(db, player_id, source_loader)
        all_valid_evidence_map = {eid: item['version_digest'] for eid, item in all_valid.items()}
        log_rows = db.execute(
            'SELECT * FROM learning_practice_logs WHERE player_id=? ORDER BY updated_at DESC, id DESC',
            (player_id,),
        ).fetchall()
        all_logs = [self._decode_practice_log(db, r, include_revisions=True) for r in log_rows]
        all_user_logs_map = {}
        for log in all_logs:
            if log['user_version'] > 0:
                all_user_logs_map[log['item_key']] = identity({
                    'item_key': log['item_key'],
                    'user_version': log['user_version'],
                    'status': log['status'],
                    'note': log['note'],
                    'title': log['title'],
                    'topic': log['topic'],
                    'cue': log['cue'],
                    'drill': log['drill'],
                })

        anon_profile = dict(
            height_cm=player_profile.get('height_cm'),
            level=player_profile.get('level'),
            profile_version=player_profile.get('version', 1),
        )
        target_state_digest = identity({
            'profile': anon_profile,
            'evidence': all_valid_evidence_map,
            'practice_logs': all_user_logs_map,
            'prompt_version': LEARNING_PLAN_PROMPT_VERSION,
        })

        active_plan_row = db.execute('SELECT * FROM learning_plans WHERE player_id=?', (player_id,)).fetchone()
        if active_plan_row:
            prior_snapshot = json.loads(active_plan_row['snapshot_json'])
            prior_covered_evidence_map = prior_snapshot.get('covered_evidence_map') or {}
            prior_covered_logs_map = prior_snapshot.get('covered_logs_map') or {}
            parent_revision = active_plan_row['active_revision']
            prior_plan_obj = json.loads(active_plan_row['plan_json'])
            prior_plan_summary = {
                k: prior_plan_obj.get(k)
                for k in ('observed_summary', 'summary_evidence_ids', 'focus_areas', 'uncertainties', 'unassessed_areas', 'progress_notes', 'next_evidence_requests')
            }
        else:
            prior_snapshot = {}
            prior_covered_evidence_map = {}
            prior_covered_logs_map = {}
            parent_revision = None
            prior_plan_summary = None

        all_removed = [
            dict(evidence_id=eid, reason=invalid_reasons.get(eid, 'removed'))
            for eid in sorted(prior_covered_evidence_map)
            if eid not in all_valid_evidence_map
        ]
        removed_evidence = []
        for rem in all_removed:
            if len(removed_evidence) >= MAX_PLAN_REMOVED_ITEMS or len(canonical(removed_evidence + [rem]).encode('utf-8')) > MAX_PLAN_REMOVED_BYTES:
                break
            removed_evidence.append(rem)
        sent_removed_ids = {r['evidence_id'] for r in removed_evidence}

        uncovered_items = [
            item for eid, item in all_valid.items()
            if prior_covered_evidence_map.get(eid) != item['version_digest']
        ]
        uncovered_items.sort(
            key=lambda x: (
                -(x['asked_at'] if x.get('asked_at') is not None else (x.get('updated_at') or 0)),
                -(x.get('updated_at') or 0),
                x['evidence_id'],
            )
        )
        delta_evidence = []
        prompt_keys = (
            'evidence_id', 'kind', 'job_id', 'source_id', 'clip_id', 'asked_at',
            'original_start', 'original_end', 'review_status', 'provenance_trust',
            'version', 'title', 'topic', 'observation', 'cue', 'drill', 'uncertainty', 'version_digest',
        )
        for item in uncovered_items:
            prompt_item = {k: item[k] for k in prompt_keys}
            if len(delta_evidence) >= MAX_PLAN_EVIDENCE_ITEMS or len(canonical(delta_evidence + [prompt_item]).encode('utf-8')) > MAX_PLAN_EVIDENCE_BYTES:
                break
            delta_evidence.append(prompt_item)
        truncated_evidence_count = len(uncovered_items) - len(delta_evidence)

        # Truthful coverage: prior covered (minus sent removals) UNION exact newly supplied delta_evidence.
        covered_evidence_map = {
            eid: digest
            for eid, digest in prior_covered_evidence_map.items()
            if eid not in sent_removed_ids
        }
        for item in delta_evidence:
            covered_evidence_map[item['evidence_id']] = item['version_digest']

        delta_ids = {d['evidence_id'] for d in delta_evidence}
        prior_items_by_id = {}
        for p_item in (prior_snapshot.get('delta_evidence') or []) + (prior_snapshot.get('carried_citations') or []):
            if isinstance(p_item, dict) and p_item.get('evidence_id'):
                prior_items_by_id[p_item['evidence_id']] = p_item

        carried_candidates = []
        for eid, expected_digest in covered_evidence_map.items():
            if eid not in all_valid or eid in delta_ids:
                continue
            live_item = all_valid[eid]
            if live_item.get('version_digest') == expected_digest:
                carried_candidates.append(live_item)
            else:
                # Revised evidence was deferred by the 24-item delta cap; carry only the frozen covered revision.
                frozen_item = prior_items_by_id.get(eid)
                if frozen_item and frozen_item.get('version_digest') == expected_digest:
                    carried_candidates.append(frozen_item)

        carried_candidates.sort(
            key=lambda x: (
                -(x['asked_at'] if x.get('asked_at') is not None else (x.get('updated_at') or 0)),
                x['evidence_id'],
            )
        )
        carried_citations = []
        carried_keys = (
            'evidence_id', 'job_id', 'source_id', 'clip_id', 'asked_at',
            'original_start', 'original_end', 'review_status', 'provenance_trust',
            'version', 'version_digest', 'title', 'topic', 'cue',
        )
        for item in carried_candidates:
            entry = {k: item.get(k) for k in carried_keys}
            if len(carried_citations) >= MAX_PLAN_CARRIED_CITATIONS or len(canonical(carried_citations + [entry]).encode('utf-8')) > MAX_PLAN_CARRIED_BYTES:
                break
            carried_citations.append(entry)

        changed_user_logs = [
            log for log in all_logs
            if log['user_version'] > 0 and prior_covered_logs_map.get(log['item_key']) != all_user_logs_map.get(log['item_key'])
        ]
        changed_keys = {log['item_key'] for log in changed_user_logs}
        other_logs = [log for log in all_logs if log['item_key'] not in changed_keys and log['status'] != 'archived']
        prompt_practice_logs = []
        sent_changed_user_logs = 0
        for log in changed_user_logs + other_logs:
            entry = dict(
                item_key=log['item_key'],
                origin=log['origin'],
                title=log['title'][:120],
                topic=log['topic'][:80],
                cue=log['cue'][:400],
                drill=log['drill'][:600],
                status=log['status'],
                note=log['note'][:600],
                user_version=log['user_version'],
                version=log['version'],
                user_digest=all_user_logs_map.get(log['item_key']),
            )
            if len(prompt_practice_logs) >= MAX_PLAN_PRACTICE_LOGS or len(canonical(prompt_practice_logs + [entry]).encode('utf-8')) > MAX_PLAN_PRACTICE_BYTES:
                break
            prompt_practice_logs.append(entry)
            if log['item_key'] in changed_keys:
                sent_changed_user_logs += 1
        truncated_logs_count = len(changed_user_logs) - sent_changed_user_logs

        covered_logs_map = {
            k: v for k, v in prior_covered_logs_map.items() if k in all_user_logs_map
        }
        for entry in prompt_practice_logs:
            if entry['user_version'] > 0 and entry.get('user_digest'):
                covered_logs_map[entry['item_key']] = entry['user_digest']

        next_covered_state_digest = identity({
            'profile': anon_profile,
            'evidence': covered_evidence_map,
            'practice_logs': covered_logs_map,
            'prompt_version': LEARNING_PLAN_PROMPT_VERSION,
        })
        snapshot = dict(
            player_profile=anon_profile,
            prior_plan=prior_plan_summary,
            delta_evidence=delta_evidence,
            removed_evidence=removed_evidence,
            carried_citations=carried_citations,
            practice_logs=prompt_practice_logs,
            existing_focus_keys=[log['item_key'] for log in all_logs[:100]],
            covered_evidence_map=covered_evidence_map,
            covered_logs_map=covered_logs_map,
            truncated_evidence_count=truncated_evidence_count,
            truncated_logs_count=truncated_logs_count,
        )
        request_digest = identity({
            'player_profile': anon_profile,
            'parent_revision': parent_revision,
            'prior_plan': prior_plan_summary,
            'delta_evidence': delta_evidence,
            'removed_evidence': removed_evidence,
            'carried_citations': carried_citations,
            'practice_logs': prompt_practice_logs,
            'existing_focus_keys': snapshot['existing_focus_keys'],
            'truncated_evidence_count': truncated_evidence_count,
            'truncated_logs_count': truncated_logs_count,
            'prompt_version': LEARNING_PLAN_PROMPT_VERSION,
        })
        return dict(
            all_valid=all_valid,
            all_valid_evidence_map=all_valid_evidence_map,
            invalid_reasons=invalid_reasons,
            all_logs=all_logs,
            all_user_logs_map=all_user_logs_map,
            anon_profile=anon_profile,
            active_plan_row=active_plan_row,
            prior_snapshot=prior_snapshot,
            prior_covered_evidence_map=prior_covered_evidence_map,
            prior_covered_logs_map=prior_covered_logs_map,
            parent_revision=parent_revision,
            uncovered_items=uncovered_items,
            all_removed=all_removed,
            changed_user_logs=changed_user_logs,
            snapshot=snapshot,
            target_state_digest=target_state_digest,
            next_covered_state_digest=next_covered_state_digest,
            request_digest=request_digest,
        )

    def _expire_ai_leases(self, db):
        now = self.clock()
        if db.in_transaction:
            db.execute(
                "UPDATE jobs SET state=CASE WHEN state='calling' THEN 'needs_review' ELSE 'queued' END,token=NULL,version=version+1,updated=? "
                "WHERE role='ai' AND state IN ('leased','calling') AND expires<?",
                (now, now),
            )
        else:
            stale = db.execute("SELECT 1 FROM jobs WHERE role='ai' AND state IN ('leased','calling') AND expires<? LIMIT 1", (now,)).fetchone()
            if stale:
                db.execute('BEGIN IMMEDIATE')
                db.execute(
                    "UPDATE jobs SET state=CASE WHEN state='calling' THEN 'needs_review' ELSE 'queued' END,token=NULL,version=version+1,updated=? "
                    "WHERE role='ai' AND state IN ('leased','calling') AND expires<?",
                    (now, now),
                )
                db.commit()

    def _latest_pending_plan_job(self, db, player_id, active_job_id=None, active_updated_at=0):
        rows = db.execute(
            "SELECT * FROM jobs WHERE role='ai' AND json_extract(body,'$.purpose')='learning_plan' AND json_extract(body,'$.player_id')=? ORDER BY COALESCE(asked_at, updated) DESC, updated DESC, id DESC LIMIT 10",
            (player_id,),
        ).fetchall()
        for row in rows:
            if row['state'] in ('queued', 'leased', 'calling'):
                job = self.decode(row)
                for field in ('submitted_prompt', 'response_contract', 'snapshot'):
                    job['body'].pop(field, None)
                return job
        for row in rows:
            if row['id'] == active_job_id:
                break
            if row['state'] in ('needs_review', 'failed') and row['updated'] >= active_updated_at:
                job = self.decode(row)
                for field in ('submitted_prompt', 'response_contract', 'snapshot'):
                    job['body'].pop(field, None)
                return job
        return None

    def build_learning_plan_state(self, player_id, player_profile, source_loader, enabled=False):
        with self.db() as db:
            self._expire_ai_leases(db)
            computed = self._compute_plan_snapshot(db, player_id, player_profile, source_loader)
            active_row = computed['active_plan_row']
            all_valid = computed['all_valid']
            invalid_reasons = computed['invalid_reasons']
            prior_snapshot = computed['prior_snapshot']

            active_plan = None
            if active_row:
                plan_obj = json.loads(active_row['plan_json'])
                prov_obj = json.loads(active_row['provenance_json'])
                snap_obj = prior_snapshot
                citations = {}
                prior_items_by_id = {}
                for item in (snap_obj.get('delta_evidence') or []) + (snap_obj.get('carried_citations') or []):
                    if isinstance(item, dict) and item.get('evidence_id'):
                        prior_items_by_id[item['evidence_id']] = item
                covered_map = snap_obj.get('covered_evidence_map') or {}
                cite_sources = {}
                for eid, covered_digest in covered_map.items():
                    hist = prior_items_by_id.get(eid) or {}
                    live = all_valid.get(eid)
                    is_unchanged = bool(live and live.get('version_digest') == covered_digest)
                    is_revised = bool(live and live.get('version_digest') != covered_digest)
                    sid = hist.get('source_id') or (live and live.get('source_id'))
                    job_id = hist.get('job_id') or (live and live.get('job_id'))
                    orig_start = hist.get('original_start') if hist.get('original_start') is not None else (live and live.get('original_start'))
                    orig_end = hist.get('original_end') if hist.get('original_end') is not None else (live and live.get('original_end'))
                    study_url = None
                    media_available = False
                    clip_id = hist.get('clip_id') or (live and live.get('clip_id'))
                    clip_index = None
                    recording_title = (live and live.get('recording_title')) or 'Outdated or removed review'
                    historical = True
                    if sid and orig_start is not None and orig_end is not None and (live is not None):
                        if sid not in cite_sources:
                            cite_sources[sid] = source_loader(sid)
                        s_data = cite_sources[sid]
                        if s_data:
                            recording_title = s_data.get('title', recording_title)
                            overlapping = [
                                c for c in s_data.get('clips', [])
                                if max(orig_start, c['original_start_seconds']) < min(orig_end, c['original_end_seconds'])
                            ]
                            if overlapping:
                                p_clip = overlapping[0]
                                clip_id = p_clip['id']
                                clip_index = p_clip.get('index')
                                media_available = True
                                historical = bool(live.get('historical'))
                                c_start = max(p_clip['original_start_seconds'], min(p_clip['original_end_seconds'], orig_start))
                                c_end = max(p_clip['original_start_seconds'], min(p_clip['original_end_seconds'], orig_end))
                                study_url = f"/review/{sid}?clip={clip_id}&coach_time={c_start}&coach_end={c_end}&coach_job={job_id}"
                    citations[eid] = dict(
                        evidence_id=eid,
                        job_id=job_id,
                        source_id=sid,
                        clip_id=clip_id,
                        clip_index=clip_index,
                        recording_title=recording_title,
                        asked_at=hist.get('asked_at') if hist.get('asked_at') is not None else (live and live.get('asked_at')),
                        original_start=orig_start,
                        original_end=orig_end,
                        review_status=hist.get('review_status') or (live and live.get('review_status')) or 'unknown',
                        provenance_trust=hist.get('provenance_trust') or (live and live.get('provenance_trust')) or 'unknown',
                        version=hist.get('version') if hist.get('version') is not None else (live and live.get('version')),
                        version_digest=covered_digest,
                        title=hist.get('title') if hist.get('title') is not None else ((live and live.get('title')) or 'Outdated review'),
                        topic=hist.get('topic') if hist.get('topic') is not None else ((live and live.get('topic')) or ''),
                        cue=hist.get('cue') if hist.get('cue') is not None else ((live and live.get('cue')) or ''),
                        historical=historical,
                        media_available=media_available,
                        study_url=study_url,
                        currently_valid=is_unchanged,
                        revision_changed=is_revised,
                        invalid_reason=None if is_unchanged else ('revised' if is_revised else invalid_reasons.get(eid, 'removed')),
                    )
                active_plan = dict(
                    player_id=player_id,
                    revision=active_row['active_revision'],
                    job_id=active_row['active_job_id'],
                    input_digest=active_row['covered_state_digest'],
                    covered_state_digest=active_row['covered_state_digest'],
                    request_digest=active_row['request_digest'],
                    plan=plan_obj,
                    provenance=prov_obj,
                    citations=citations,
                    coverage=dict(
                        covered_reviews_count=len(covered_map),
                        covered_practice_logs_count=len(snap_obj.get('covered_logs_map') or {}),
                        truncated_reviews_count=snap_obj.get('truncated_evidence_count', 0),
                        truncated_practice_logs_count=snap_obj.get('truncated_logs_count', 0),
                    ),
                    created_at=active_row['created_at'],
                    updated_at=active_row['updated_at'],
                )

            rev_rows = db.execute(
                'SELECT revision, job_id, parent_revision, status, covered_state_digest, request_digest, plan_json, provenance_json, created_at '
                'FROM learning_plan_revisions WHERE player_id=? ORDER BY revision DESC LIMIT 25',
                (player_id,),
            ).fetchall()
            revisions = []
            for r in rev_rows:
                r_plan = json.loads(r['plan_json'])
                r_prov = json.loads(r['provenance_json'])
                revisions.append(dict(
                    revision=r['revision'],
                    job_id=r['job_id'],
                    parent_revision=r['parent_revision'],
                    status=r['status'],
                    input_digest=r['covered_state_digest'],
                    covered_state_digest=r['covered_state_digest'],
                    request_digest=r['request_digest'],
                    observed_summary=r_plan.get('observed_summary', ''),
                    focus_count=len(r_plan.get('focus_areas') or []),
                    provenance=r_prov,
                    created_at=r['created_at'],
                ))

            pending_job = self._latest_pending_plan_job(
                db,
                player_id,
                active_job_id=active_row['active_job_id'] if active_row else None,
                active_updated_at=active_row['updated_at'] if active_row else 0,
            )

        now = self.clock()
        days_since_last = (
            max(0.0, (now - active_row['updated_at']) / 86400.0)
            if active_row else None
        )
        active_covered_digest = active_row['covered_state_digest'] if active_row else None
        target_state_digest = computed['target_state_digest']
        next_covered_state_digest = computed['next_covered_state_digest']
        request_digest = computed['request_digest']
        uncovered_count = len(computed['uncovered_items'])
        removed_count = len(computed['all_removed'])
        log_changes_count = len(computed['changed_user_logs'])
        profile_changed = bool(active_row and (prior_snapshot.get('player_profile') != computed['anon_profile']))
        prior_truncated_reviews = prior_snapshot.get('truncated_evidence_count', 0) if active_row else 0
        prior_truncated_logs = prior_snapshot.get('truncated_logs_count', 0) if active_row else 0
        next_truncated_reviews = computed['snapshot']['truncated_evidence_count']
        next_truncated_logs = computed['snapshot']['truncated_logs_count']

        if pending_job and pending_job['state'] in ('queued', 'leased', 'calling'):
            can_generate = False
            reason = 'in_progress'
            input_digest = next_covered_state_digest
        elif active_row is None and len(computed['all_valid_evidence_map']) == 0:
            can_generate = False
            reason = 'no_reviews'
            input_digest = next_covered_state_digest
        elif active_row is not None and active_covered_digest == target_state_digest:
            can_generate = False
            reason = 'up_to_date'
            input_digest = active_covered_digest
        else:
            can_generate = True
            reason = 'initial_eligible' if active_row is None else 'updates_available'
            input_digest = next_covered_state_digest

        last_attempt_invalid = bool(
            revisions
            and revisions[0]['status'] == 'invalid'
            and (active_row is None or revisions[0]['revision'] > active_row['active_revision'])
        )
        last_invalid_warnings = (
            (revisions[0]['provenance'].get('validation_warnings') or [])
            if last_attempt_invalid else []
        )
        eligibility = dict(
            can_generate=can_generate,
            reason=reason,
            expected_parent_revision=active_row['active_revision'] if active_row else None,
            input_digest=input_digest,
            covered_state_digest=next_covered_state_digest,
            target_state_digest=target_state_digest,
            request_digest=request_digest,
            valid_reviews_count=len(computed['all_valid_evidence_map']),
            new_or_revised_reviews_count=uncovered_count,
            removed_reviews_count=removed_count,
            practice_log_changes_count=log_changes_count,
            profile_changed=profile_changed,
            backlog_reviews_count=prior_truncated_reviews if (active_row and uncovered_count > 0 and prior_truncated_reviews > 0) else next_truncated_reviews,
            backlog_practice_logs_count=prior_truncated_logs if (active_row and log_changes_count > 0 and prior_truncated_logs > 0) else next_truncated_logs,
            prior_truncated_reviews_count=prior_truncated_reviews,
            next_truncated_reviews_count=next_truncated_reviews,
            prior_truncated_logs_count=prior_truncated_logs,
            next_truncated_logs_count=next_truncated_logs,
            days_since_last_plan=round(days_since_last, 1) if days_since_last is not None else None,
            recommended_cadence_days=14,
            requires_ambiguous_retry=bool(pending_job and pending_job['state'] == 'needs_review'),
            last_attempt_invalid=last_attempt_invalid,
            last_invalid_warnings=last_invalid_warnings,
        )
        return dict(
            enabled=bool(enabled),
            active_plan=active_plan,
            revisions=revisions,
            practice_logs=computed['all_logs'],
            eligibility=eligibility,
            pending_job=pending_job,
            training_label_status='not_released',
        )

    def queue_learning_plan(self, player_id, player_profile, body, source_loader):
        import hashlib
        import uuid
        from coaching import QueueConflict
        if not isinstance(body, dict):
            raise ValueError('Expected an object')
        request_id = body.get('request_id')
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100:
            raise ValueError('A request ID is required')
        if body.get('consent') is not True:
            raise ValueError('Confirm sending coaching review summaries and practice progress to OpenAI')
        expected_parent = body.get('expected_parent_revision')
        if expected_parent is not None and type(expected_parent) is not int:
            raise ValueError('Invalid expected parent revision')
        expected_digest = body.get('input_digest')
        if not isinstance(expected_digest, str) or not expected_digest:
            raise ValueError('An input digest is required')

        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expire_ai_leases(db)
            existing_req = db.execute('SELECT * FROM jobs WHERE request_id=?', (request_id,)).fetchone()
            if existing_req:
                existing_body = json.loads(existing_req['body'])
                if (
                    existing_req['role'] != 'ai'
                    or existing_body.get('purpose') != 'learning_plan'
                    or existing_body.get('player_id') != player_id
                    or existing_body.get('parent_revision') != expected_parent
                    or expected_digest not in (existing_body.get('covered_state_digest'), existing_body.get('request_digest'))
                ):
                    raise QueueConflict('Request ID already used for a different selection')
                return self.decode(existing_req)

            active_row = db.execute('SELECT * FROM learning_plans WHERE player_id=?', (player_id,)).fetchone()
            current_active_rev = active_row['active_revision'] if active_row else None
            if expected_parent != current_active_rev:
                raise QueueConflict('Active learning plan changed. Refresh before updating.')

            pending = self._latest_pending_plan_job(
                db,
                player_id,
                active_job_id=active_row['active_job_id'] if active_row else None,
                active_updated_at=active_row['updated_at'] if active_row else 0,
            )
            if pending:
                if pending['state'] in ('queued', 'leased', 'calling'):
                    raise QueueConflict('A learning plan request is already in progress for this player')
                if pending['state'] == 'needs_review':
                    if body.get('retry_ambiguous') is not True:
                        raise QueueConflict('Previous learning plan request expired after calling the provider and needs review. Confirm retry to submit a new request.')
                    now = self.clock()
                    db.execute("UPDATE jobs SET state='cancelled',token=NULL,version=version+1,updated=? WHERE id=?", (now, pending['id']))

            computed = self._compute_plan_snapshot(db, player_id, player_profile, source_loader)
            if active_row is None and not computed['all_valid_evidence_map']:
                raise ValueError('Complete at least one coaching review for this player before generating a learning plan')
            if active_row is not None and active_row['covered_state_digest'] == computed['target_state_digest']:
                raise QueueConflict('Learning plan is already up to date with current reviews and practice logs')
            if expected_digest not in (computed['next_covered_state_digest'], computed['request_digest']):
                raise QueueConflict('Reviews or practice logs changed. Refresh before generating a plan.')

            job_body = dict(
                purpose='learning_plan',
                player_id=player_id,
                player_profile=computed['anon_profile'],
                parent_revision=current_active_rev,
                covered_state_digest=computed['next_covered_state_digest'],
                request_digest=computed['request_digest'],
                snapshot=computed['snapshot'],
                provider='chatgpt-codex',
                consent=True,
                required_capability=LEARNING_PLAN_CAPABILITY,
                prompt_version=LEARNING_PLAN_PROMPT_VERSION,
                schema_version=LEARNING_PLAN_SCHEMA_VERSION,
            )
            job_body['submitted_prompt'] = learning_plan_prompt(job_body)
            job_body['response_contract'] = learning_plan_schema()
            encoded = json.dumps(job_body, sort_keys=True)
            fingerprint = hashlib.sha256(('ai' + encoded).encode()).hexdigest()
            job_id = uuid.uuid4().hex
            now = self.clock()
            db.execute(
                'INSERT INTO jobs(id,role,state,body,request_id,fingerprint,worker,token,expires,version,updated,result,asked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (job_id, 'ai', 'queued', encoded, request_id, fingerprint, None, None, 0, 1, now, None, now),
            )
            created_row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            db.commit()
        return self.decode(created_row)
