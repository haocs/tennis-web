"""Courtside: a local-network tennis library, with no account system."""
import copy
from datetime import datetime
import json
import mimetypes
import os
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from storage import Library, smb_root, nas_media_address
from thumbnails import ClipPosters, PosterUnavailable
from practices import recording_time, group_recordings
from state import State, Conflict
from entity_model import EntityCatalog, clip_identity, output_version
from clip_study import public_clip_study, number as finite_number
from coaching_routes import register_coaching
from annotation_routes import register_annotations
from players import register_players, decorate_players, decorate_groups, player_list
from courts import COURT_ID, court_list, validate_court, enrich_groups


def create_app(config=None):
    mimetypes.add_type('application/wasm', '.wasm')
    mimetypes.add_type('text/javascript', '.js')
    mimetypes.add_type('application/octet-stream', '.tflite')
    app = Flask(__name__)
    app.config.update(LIBRARY_ROOT=os.environ.get("TENNIS_LIBRARY_ROOT", "/library"),
                      MEDIA_ROOT=os.environ.get("TENNIS_MEDIA_ROOT"),
                      DATA_ROOT=os.environ.get("TENNIS_DATA_ROOT", "/data"),
                      STATUS_FILE=os.environ.get("TENNIS_STATUS_FILE"),
                      PROCESSING_API_URL=os.environ.get("TENNIS_PROCESSING_API_URL", ""),
                      PROCESSING_ROOM_URL=os.environ.get("TENNIS_PROCESSING_ROOM_URL", "/processing"),
                      SESSION_GAP_MINUTES=os.environ.get("TENNIS_SESSION_GAP_MINUTES", "30"),
                      LIBRARY_SMB_URL=os.environ.get("TENNIS_LIBRARY_SMB_URL"),
                      MEDIA_SMB_URL=os.environ.get("TENNIS_MEDIA_SMB_URL"),
                      COACH_MEDIA_ROOT=os.environ.get("TENNIS_COACH_MEDIA_ROOT"),
                      COACH_WORKER_TOKEN=os.environ.get("TENNIS_COACH_WORKER_TOKEN", ""), MAX_CONTENT_LENGTH=131072)
    if config:
        app.config.update(config)
    processing_url = app.config['PROCESSING_ROOM_URL']
    parsed_url = urlparse(processing_url) if isinstance(processing_url, str) else urlparse('')
    if not (isinstance(processing_url, str) and processing_url and
            (processing_url.startswith('/') and not parsed_url.netloc and not processing_url.startswith('//')
             or parsed_url.scheme in {'http', 'https'} and parsed_url.hostname and not parsed_url.username and not parsed_url.password)):
        raise ValueError('Processing Room URL must be a local path or an HTTP(S) URL without credentials')
    library = Library(app.config["LIBRARY_ROOT"], app.config["MEDIA_ROOT"])
    gap_minutes = int(app.config["SESSION_GAP_MINUTES"])
    if not 0 <= gap_minutes <= 1440:
        raise ValueError('Session grouping gap must be between 0 and 1440 minutes')
    library_smb = smb_root(app.config["LIBRARY_SMB_URL"])
    media_smb = smb_root(app.config["MEDIA_SMB_URL"])
    if media_smb is None and library.media_root == library.root / 'media' and library_smb:
        media_smb = library_smb + '/media'
    state = State(Path(app.config["DATA_ROOT"]) / "website.sqlite3")
    catalog = EntityCatalog(Path(app.config["DATA_ROOT"]) / "website.sqlite3")
    app.extensions.update(library=library, state=state, entity_catalog=catalog)

    def serving_groups(recordings, gap=gap_minutes):
        return catalog.sync(recordings, gap)
    app.extensions['serving_groups'] = serving_groups
    posters = ClipPosters(Path(app.config["DATA_ROOT"])/"clip-posters", app.config.get("FFMPEG_BINARY", os.environ.get("TENNIS_FFMPEG", "ffmpeg")))

    def session(identity):
        try:
            return library.get(identity)
        except KeyError:
            abort(404, "Session not found")
        except (OSError, ValueError, TypeError):
            abort(503, "This session's metadata is unavailable")

    if not app.config["COACH_MEDIA_ROOT"]:
        app.config["COACH_MEDIA_ROOT"] = str(Path(app.config["DATA_ROOT"]) / "coaching-media")
    try:catalog.sync(library.sessions()[0], gap_minutes)
    except OSError:pass
    register_players(app, library, session, gap_minutes)
    register_coaching(app, library, lambda identity: decorate_players(copy.deepcopy(session(identity)), state.all()))
    register_annotations(app, library, session)
    from cut_reprocess import register as register_reprocess
    register_reprocess(app, session)
    from cut_comparisons import register as register_comparisons
    register_comparisons(app, session)
    from portal_mirror import register as register_portal_mirror
    register_portal_mirror(app, library, state)

    def require_publication(data, parameter):
        expected = request.args.get(parameter)
        if expected is None:
            return
        try:
            expected = float(expected)
        except ValueError:
            abort(400, 'Invalid publication version')
        if not finite_number(expected):
            abort(400, 'Invalid publication version')
        if expected != data.get('published_at'):
            abort(409, 'This recording was reprocessed. Refresh the review to load its new context.')

    def staged_versions_map(sessions_list=None):
        by_source = {}
        pclient = app.extensions.get('processing_client')
        if pclient and getattr(pclient, 'url', None):
            try:
                remote = pclient.call('/videos/staged-summary', method='GET')
                if isinstance(remote, dict) and isinstance(remote.get('by_source'), dict):
                    return remote['by_source']
            except Exception:
                pass
        cand_root = library.metadata / 'candidates'
        if not cand_root.is_dir():
            return by_source
        for s in (sessions_list or []):
            sid = s.get('id')
            if not isinstance(sid, str) or not sid:
                continue
            s_dir = cand_root / sid
            if not s_dir.is_dir():
                continue
            active_att = (s.get('processing') or {}).get('attempt_id')
            active_pub = s.get('published_at')
            count = 0
            latest_at = 0.0
            latest_att = None
            for cand_path in s_dir.glob('*.json'):
                try:
                    cand = json.loads(cand_path.read_text())
                except (OSError, ValueError):
                    continue
                if not isinstance(cand, dict):
                    continue
                proc = cand.get('processing') if isinstance(cand.get('processing'), dict) else {}
                att = proc.get('attempt_id') or cand.get('attempt_id') or cand_path.stem
                if active_att and att == active_att:
                    continue
                cand_pub = cand.get('published_at')
                staged_at = proc.get('staged_at')
                if isinstance(active_pub, (int, float)) and isinstance(cand_pub, (int, float)) and staged_at is None and cand_pub < active_pub:
                    continue
                ts = float(staged_at if isinstance(staged_at, (int, float)) else (cand_pub if isinstance(cand_pub, (int, float)) else 0.0))
                count += 1
                if ts >= latest_at:
                    latest_at = ts
                    latest_att = att
            if count:
                by_source[sid] = {'count': count, 'latest_attempt_id': latest_att, 'latest_staged_at': latest_at}
        return by_source

    def decorate(data, shared=None, highlights=None, staged_summary=None):
        data = copy.deepcopy(data)
        shared = {**(shared if shared is not None else state.all()), **app.extensions['annotations'].like_states(data['id'],data['published_at'])}
        clip_hl = app.extensions['annotations'].clip_highlights(data, highlights)
        identity = data["id"]
        data['recorded_at_iso'], data['recorded_at_epoch'] = recording_time(data['source'].get('recorded_at'))
        directory = data.get('media_directory', identity)
        data['video']['nas_url'] = nas_media_address(media_smb, directory, data['video']['file'])
        staged_info = (staged_summary or {}).get(identity) or {}
        staged_count = int(staged_info.get('count') or 0)
        data['staged_version_count'] = staged_count
        data['has_staged_version'] = staged_count > 0
        data["like"] = shared.get(f"like:{identity}", {"value": False, "version": 0})
        data["overrides"] = shared.get(f"meta:{identity}", {"value": {}, "version": 0})
        edits = data["overrides"]["value"] or {}
        data["title"] = edits.get("title", data["title"])
        data["location_label"] = edits.get("location", "")
        for clip in data["clips"]:
            clip['details'] = shared.get('clip-meta:'+clip_identity(data,clip['id']), {'value':{},'version':0})
            clip['title'] = (clip['details']['value'] or {}).get('title') or 'Clip '+str(clip['index']).zfill(2)
            clip['nas_url'] = nas_media_address(media_smb, directory, clip['file'])
            clip["like"] = shared.get(f"like:{identity}:{clip['id']}", {"value": False, "version": 0})
            hl = clip_hl.get(clip['id'], [])
            clip['highlights'] = hl
            clip['highlight_count'] = len(hl)
            clip['highlighted'] = bool(hl)
        return catalog.decorate(decorate_players(data, shared))

    @app.before_request
    def check_mutations():
        if request.method in {"PUT", "POST", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                abort(403, "Cross-origin changes are disabled")
            if request.headers.get("X-Tennis-Request") != "1" or not request.is_json:
                abort(415, "Use the website's JSON request format")

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error=error.description), error.code
        return render_template('error.html', code=error.code, message=error.description), error.code

    @app.get('/healthz')
    def health():
        state.all()
        return jsonify(status='ok')

    @app.get('/')
    @app.get('/review')
    @app.get('/processing')
    @app.get('/learning')
    @app.get('/review/<identity>')
    @app.get('/review/<identity>/compare')
    @app.get('/review/<identity>/cuts')
    @app.get('/processing/cuts/<identity>')
    def index(identity=None):
        if identity:
            session(identity)
        return render_template('index.html', processing_url=processing_url)

    @app.get('/api/sessions')
    def list_sessions():
        try:
            sessions, errors = library.sessions()
        except OSError:
            return jsonify(sessions=[], practice_groups=[], invalid_sessions=0, library_available=False, shared_preferences=True)
        shared = state.all()
        hl_map = app.extensions['annotations'].active_highlights_by_source([s['id'] for s in sessions])
        staged_map = staged_versions_map(sessions)
        decorated = [decorate(s, shared, hl_map.get(s['id'], []), staged_summary=staged_map) for s in sessions]
        groups = decorate_groups(enrich_groups(serving_groups(decorated), decorated, shared), decorated)
        for group in groups:
            group['title'] = group['metadata'].get('title') or group['title']
        return jsonify(sessions=decorated, practice_groups=groups, players=player_list(shared), courts=court_list(shared), invalid_sessions=errors,
                       library_available=(library.metadata / 'sessions').is_dir(), shared_preferences=True)

    @app.get('/api/practices')
    def practices():
        response = list_sessions()
        return jsonify(sessions=response.get_json()['practice_groups'])

    @app.get('/api/entities/sessions/<identity>')
    def session_entity(identity):
        groups=practices().get_json()['sessions']
        result=next((g for g in groups if g['id']==identity),None)
        if result:return jsonify(result)
        with catalog.db() as db:
            row=db.execute('SELECT * FROM practice_sessions WHERE id=?',(identity,)).fetchone()
            ids=[r['video_id'] for r in db.execute('SELECT video_id FROM session_members WHERE session_id=?',(identity,))]
        if not row:abort(404,'Session not found')
        return jsonify(entity_type='session',id=identity,video_ids=ids,metadata=json.loads(row['metadata']),metadata_version=row['version'],media_available=False)

    @app.get('/api/entities/video-versions/<identity>')
    def version_entity(identity):
        with catalog.db() as db:row=db.execute('SELECT * FROM video_versions WHERE id=?',(identity,)).fetchone()
        if not row:abort(404,'Video version not found')
        return jsonify(entity_type='video_version',id=identity,video_id=row['video_id'],manifest=json.loads(row['manifest']))

    @app.get('/api/entities/clips/<identity>')
    def clip_entity(identity):
        with catalog.db() as db:row=db.execute('SELECT * FROM clips WHERE id=?',(identity,)).fetchone()
        if not row:abort(404,'Clip not found')
        details=state.all().get('clip-meta:'+identity,{'value':{},'version':0})
        descriptor=json.loads(row['data'])
        return jsonify(entity_type='clip',id=identity,video_version_id=row['video_version_id'],metadata=descriptor,details=details,title=(details['value'] or {}).get('title') or 'Clip '+str(descriptor['index']).zfill(2))

    @app.put('/api/practices/<identity>/metadata')
    def practice_metadata(identity):
        body=request.get_json()
        if not isinstance(body,dict) or set(body)-{'version','title','note'} or type(body.get('version')) is not int or body['version']<0:
            abort(400,'A current version and session details are required')
        values={}
        for field,limit in [('title',160),('note',2000)]:
            if field in body:
                if not isinstance(body[field],str) or len(body[field])>limit:abort(400,'Invalid session '+field)
                values[field]=body[field].strip()
        if not values:abort(400,'Enter session details')
        try:return jsonify(catalog.edit_session(identity,values,body['version']))
        except KeyError:abort(404,'Session not found')
        except Conflict as error:return jsonify(error='Session changed in another browser. Reload before saving.',current=error.current),409

    @app.get('/api/entities/videos/<identity>')
    def video_entity(identity):
        data=get_session(identity).get_json()
        return jsonify(entity_type='video',id=identity,original_recording_id=identity,
                       title=data['title'],location=data['location_label'],metadata=data['overrides'],
                       current_version_id=data['video_version_id'],clips=data['clips'],versions=catalog.versions(identity))

    @app.get('/api/processing/videos/<identity>/versions')
    def processing_video_versions(identity):
        data = session(identity)
        try:
            catalog.sync_serving_video(data)
        except ValueError as error:
            abort(409, str(error))
        current_vid = output_version(identity, data['published_at'])
        retained = catalog.versions(identity)
        items = []
        for entry in retained:
            manifest = entry['manifest']
            is_serving = entry['id'] == current_vid
            kept_sec = sum(max(0.0, float(c['original_end_seconds']) - float(c['original_start_seconds'])) for c in manifest.get('clips', []))
            source_sec = float((manifest.get('source') or {}).get('duration_seconds', 0))
            processing = manifest.get('processing') if isinstance(manifest.get('processing'), dict) else {}
            items.append(dict(
                id=entry['id'],
                video_id=identity,
                published_at=manifest.get('published_at'),
                is_current_serving=is_serving,
                media_state='available' if is_serving else 'historical_manifest',
                clip_count=len(manifest.get('clips', [])),
                kept_seconds=round(kept_sec, 3),
                removed_seconds=round(max(0.0, source_sec - kept_sec), 3),
                source_duration_seconds=source_sec,
                processing_ref={k: v for k, v in processing.items() if k in ('run_id', 'algorithm_version', 'task_id', 'attempt_id', 'source_id') and isinstance(v, str) and len(v) <= 120},
                manifest=manifest,
            ))
        return jsonify(
            video_id=identity,
            current_version_id=current_vid,
            current_publication=data['published_at'],
            versions=items,
        )

    @app.get('/api/processing/videos/<identity>/heuristics')
    def processing_heuristics(identity):
        import hashlib
        from cut_diagnostics import validate, MAX_BYTES
        data=session(identity)
        try:catalog.sync_serving_video(data)
        except ValueError as error:abort(409,str(error))
        version=request.args.get('version') or output_version(identity,data['published_at'])
        manifest=catalog.version_manifest(identity,version)
        if manifest is None:abort(404,'Video version not found')
        if version!=output_version(identity,data['published_at']):
            if not app.config.get('PROCESSING_API_URL'):
                return jsonify(video_version_id=version,available=False,reason='Historical diagnostics are unavailable',items=[],next_after=None)
            from entity_model import canonical_manifest
            from processing_bridge import version_catalog
            entries=version_catalog(app.extensions['processing_client'],identity,version)
            verified=next((v for v in entries if v.get('id')==version),None)
            if not verified or canonical_manifest(verified['manifest'])!=canonical_manifest(manifest):
                return jsonify(video_version_id=version,available=False,reason='Historical diagnostics are unavailable',items=[],next_after=None)
            data=verified['manifest']
        if 'heuristic-diagnostics.json' not in data.get('artifacts',[]):
            return jsonify(video_version_id=version,available=False,reason='No worker diagnostics for this version',items=[],next_after=None)
        try:
            after=int(request.args.get('after','0'));limit=int(request.args.get('limit','50'))
        except ValueError:abort(400,'Invalid diagnostics page')
        if after<0 or not 1<=limit<=50:abort(400,'Invalid diagnostics page')
        try:
            directory=data.get('artifact_directory')
            relative=f"runs/{directory}/heuristic-diagnostics.json" if directory else f"sessions/{identity}/heuristic-diagnostics.json"
            from storage import inside
            path=inside(library.metadata,relative)
            with path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
            expected=data.get('metadata_checksums',{}).get('heuristic-diagnostics.json')
            if not expected or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Diagnostics checksum mismatch')
            payload,entries=validate(raw,data)
        except (OSError,ValueError,KeyError,TypeError):abort(503,'Worker diagnostics are unavailable or invalid')
        return jsonify(video_version_id=version,available=True,truth_status=payload['truth_status'],training_label_status='not_released',
                       extraction_cache_key=payload['extraction_cache_key'],provenance=payload['provenance'],items=entries[after:after+limit],
                       next_after=str(after+limit) if len(entries)>after+limit else None)

    @app.get('/api/entities/original-recordings/<identity>')
    def original_entity(identity):
        with catalog.db() as db:row=db.execute('SELECT data FROM original_recordings WHERE id=?',(identity,)).fetchone()
        if not row:abort(404,'Original recording not found')
        return jsonify(entity_type='original_recording',id=identity,metadata=json.loads(row['data']))

    @app.get('/api/sessions/<identity>')
    def get_session(identity):
        data = session(identity)
        shared = state.all()
        try:
            recordings, _ = library.sessions()
        except OSError:
            abort(503, 'The recording library is unavailable')
        # Resolve the entire practice, including assignments/GPS on its siblings.
        hl_map = app.extensions['annotations'].active_highlights_by_source([s['id'] for s in recordings] + [identity])
        staged_map = staged_versions_map([data])
        recordings = [decorate(s, shared, hl_map.get(s['id'], []), staged_summary=staged_map) for s in recordings if s['id'] != identity]
        result = decorate(data, shared, hl_map.get(data['id'], []), staged_summary=staged_map)
        recordings.append(result)
        enrich_groups(serving_groups(recordings), recordings, shared)
        return jsonify(result)

    @app.get('/api/sessions/<identity>/clips/<clip_id>/study')
    def study_clip(identity, clip_id):
        data = session(identity)
        clip = next((c for c in data['clips'] if c['id'] == clip_id), None)
        if clip is None:
            abort(404, 'Clip not found')
        require_publication(data, 'publication')
        return jsonify(public_clip_study(data, clip))

    @app.get('/api/courts')
    def get_courts():
        return jsonify(courts=court_list(state.all()))

    @app.post('/api/courts')
    @app.put('/api/courts/<identity>')
    def save_court(identity=None):
        body = request.get_json()
        try:
            value = validate_court(body)
        except ValueError as error:
            abort(400,str(error))
        if identity is None:
            identity, expected = uuid.uuid4().hex, 0
        else:
            existing = state.all().get('court:'+identity)
            if not COURT_ID.fullmatch(identity) or not existing:
                abort(404,'Court not found')
            # Older open browser tabs do not send the newly introduced field.
            if 'address' not in body:
                value['address'] = existing['value'].get('address', '')
            expected = body.get('version')
            if type(expected) is not int or expected < 1:
                abort(400,'The current court version is required')
        try:
            return jsonify(state.save_court(identity,value,expected)), 201 if request.method=='POST' else 200
        except Conflict as conflict:
            return jsonify(error='This court was edited in another browser. Reload its latest details.',current=conflict.current),409
        except ValueError as error:
            return jsonify(error=str(error)),409

    @app.put('/api/practices/<identity>/court')
    def assign_court(identity):
        shared = state.all()
        try:
            videos, _ = library.sessions()
        except OSError:
            abort(503,'The recording library is unavailable')
        decorated = [decorate(v,shared) for v in videos]
        group = next((g for g in serving_groups(decorated) if g['id']==identity),None)
        if group is None:
            if any(v['id']==identity for v in decorated):
                return jsonify(error='This practice now includes different recordings. Reload and try again.'),409
            abort(404,'Practice not found')
        body = request.get_json()
        if not isinstance(body,dict) or set(body)-{'court_id','versions'}:
            abort(400,'Invalid court selection')
        court_id, versions = body.get('court_id'), body.get('versions')
        if court_id is not None and (not isinstance(court_id,str) or not COURT_ID.fullmatch(court_id) or 'court:'+court_id not in shared):
            abort(400,'Choose a saved court')
        if not isinstance(versions,dict) or set(versions)!=set(group['recording_ids']):
            return jsonify(error='Recordings in this practice changed. Reload and try again.'),409
        if any(type(v) is not int or v<0 for v in versions.values()):
            abort(400,'Valid recording versions are required')
        changes=[('court-assignment:'+v, {'court_id':court_id} if court_id else None, versions[v]) for v in group['recording_ids']]
        try:
            state.update_many(changes)
        except Conflict:
            return jsonify(error='Someone else changed this practice court. Reload and try again.'),409
        return jsonify(saved=True)

    @app.put('/api/videos/<identity>/likes/<item>')
    @app.put('/api/sessions/<identity>/likes/<item>')
    def like(identity, item):
        data = session(identity)
        if item not in ('session','video') and item not in {c['id'] for c in data['clips']}:
            abort(404, 'Clip not found')
        body = request.get_json()
        if not isinstance(body, dict):
            abort(400, 'Expected a JSON object')
        if type(body.get('liked')) is not bool or type(body.get('version')) is not int or body['version'] < 0:
            abort(400, 'A liked value and current version are required')
        if request.path.startswith('/api/videos/') and item not in ('video','session'):
            if type(body.get('publication')) not in (int,float) or body['publication'] != data['published_at']:abort(409,'Video was reprocessed. Refresh before liking this clip.')
        try:
            return jsonify(app.extensions['annotations'].write_like(identity,'video' if item in ('session','video') else item,body['liked'],body['version'],data['published_at']))
        except Conflict as conflict:
            return jsonify(error='Someone else updated this item. Its latest value has been loaded.', current=conflict.current), 409

    @app.put('/api/videos/<identity>/details')
    @app.put('/api/sessions/<identity>/metadata')
    def metadata(identity):
        session(identity)
        body = request.get_json()
        if not isinstance(body, dict):
            abort(400, 'Expected a JSON object')
        if type(body.get('version')) is not int or body['version'] < 0 or set(body) - {'version', 'title', 'location'}:
            abort(400, 'Invalid metadata edit')
        values = {}
        for key, limit in [('title', 160), ('location', 200)]:
            value = body.get(key)
            if not isinstance(value, str) or len(value) > limit or (key == 'title' and not value.strip()):
                abort(400, f'Invalid {key}')
            values[key] = value.strip()
        try:
            return jsonify(state.update(f'meta:{identity}', values, body['version']))
        except Conflict as conflict:
            return jsonify(error='Metadata changed in another browser. Reload the latest values before saving.', current=conflict.current), 409

    @app.put('/api/videos/<identity>/clips/<clip_id>/details')
    def clip_details(identity,clip_id):
        data=session(identity);body=request.get_json()
        if not isinstance(body,dict) or set(body)-{'publication','version','title'}:abort(400,'Invalid clip details')
        if type(body.get('publication')) not in (int,float) or body['publication']!=data['published_at']:
            abort(409,'Video was reprocessed. Refresh before editing this clip.')
        clip=next((c for c in data['clips'] if c['id']==clip_id),None)
        if not clip:abort(404,'Clip not found')
        if type(body.get('version')) is not int or body['version']<0:abort(400,'A current clip version is required')
        title=body.get('title')
        if not isinstance(title,str) or not title.strip() or len(title)>160:abort(400,'Enter a clip title up to 160 characters')
        try:return jsonify(state.update('clip-meta:'+clip_identity(data,clip_id),{'title':title.strip()},body['version']))
        except Conflict as error:return jsonify(error='Clip details changed in another browser. Reload before saving.',current=error.current),409

    @app.get('/media/<identity>/<path:filename>')
    def media(identity, filename):
        data = session(identity)
        require_publication(data, 'v')
        try:
            p = library.media(data, filename)
            if not p.is_file():
                abort(404, 'Video not available')
            download = request.args.get('download') == '1'
            if download and filename not in {data['video']['file'], *(c['file'] for c in data['clips'])}:
                abort(404)
            name = secure_filename(f"{Path(data['source']['name']).stem[:100]}-{Path(filename).stem[:80]}.mp4")
            return send_file(p, conditional=True, mimetype='image/jpeg' if filename.endswith('.jpg') else 'video/mp4',
                             as_attachment=download, download_name=name if download else None, max_age=0)
        except (KeyError, ValueError):
            abort(404)
        except OSError:
            abort(503, 'Media storage is unavailable')

    @app.get('/api/sessions/<identity>/clips/<clip_id>/poster')
    def clip_poster(identity, clip_id):
        data = session(identity)
        require_publication(data, 'v')
        clip = next((c for c in data['clips'] if c['id'] == clip_id), None)
        if clip is None:
            abort(404, 'Clip not found')
        try:
            source = library.media(data, clip['file'])
            if not source.is_file():
                abort(404, 'Clip not available')
            image = posters.get(data, clip, source)
            return send_file(image, mimetype='image/jpeg', conditional=True, max_age=0)
        except (KeyError, ValueError):
            abort(404)
        except (OSError, PosterUnavailable):
            abort(503, 'Clip thumbnail unavailable')

    @app.get('/api/sessions/<identity>/poster')
    def poster(identity):
        data = session(identity)
        try:
            p = library.artifact(identity, 'poster.jpg')
            if not p.is_file():
                p = library.media(data, 'poster.jpg')
            if p.is_file():
                return send_file(p, mimetype='image/jpeg', conditional=True, max_age=0)
        except (KeyError, ValueError, OSError):
            pass
        abort(404)

    @app.get('/api/sessions/<identity>/artifacts/<filename>')
    def artifact(identity, filename):
        session(identity)
        try:
            p = library.artifact(identity, filename)
            if not p.is_file() or filename in {'Review.html', 'poster.jpg'}:
                abort(404)
            return send_file(p, as_attachment=True, conditional=True)
        except (KeyError, ValueError):
            abort(404)

    @app.get('/review/<identity>/original')
    def original(identity):
        data = session(identity)
        try:
            content = library.artifact(identity, 'Review.html').read_text()
        except (KeyError, OSError):
            abort(404)
        for clip in data['clips']:
            content = content.replace(f'"{clip["file"]}"', f'"/media/{identity}/{clip["file"]}"')
        content = content.replace('"Practice.mp4"', f'"/media/{identity}/Practice.mp4"')
        for filename in ('edit-timeline.csv', 'edit-timeline.json', 'timestamps.csv'):
            content = content.replace(f'"{filename}"', f'"/api/sessions/{identity}/artifacts/{filename}"')
        response = app.make_response(content)
        response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; media-src 'self'; img-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
        return response

    def compute_worker_health(status_result):
        pclient = app.extensions.get('processing_client')
        video_slots = None
        if pclient and getattr(pclient, 'url', None):
            try:
                wresp = pclient.call('/workers', method='GET')
                if isinstance(wresp, dict) and isinstance(wresp.get('items'), list):
                    items = wresp['items']
                    video_slots = [bool(w.get('online')) and not bool(w.get('paused')) for w in items] if items else [False]
            except Exception:
                video_slots = None
        if video_slots is None:
            files_list = status_result.get('files') or []
            file_bad = any(f.get('error') or f.get('status') in ('needs_attention', 'retry') for f in files_list)
            video_ok = (
                bool(status_result.get('available'))
                and not bool(status_result.get('stale'))
                and (status_result.get('service') not in ('Stopped', 'Error', 'Failed'))
                and not bool(status_result.get('has_error'))
                and not file_bad
            )
            video_slots = [video_ok]
        coach_slots = []
        if bool(app.config.get('COACH_WORKER_TOKEN')) and app.extensions.get('coaching'):
            try:
                ov = app.extensions['coaching'].worker_overview(enabled=True)
                cw = ov.get('workers') or []
                coach_slots = [bool(w.get('online')) and w.get('status') != 'failed' for w in cw] if cw else [False]
            except Exception:
                coach_slots = [False]
        slots = video_slots + coach_slots
        total_count = len(slots)
        online_count = sum(1 for ok in slots if ok)
        if online_count == 0:
            return {'color': 'red', 'label': 'No workers running', 'online_count': 0, 'total_count': total_count}
        if online_count == total_count:
            return {'color': 'green', 'label': 'All workers online', 'online_count': online_count, 'total_count': total_count}
        return {'color': 'yellow', 'label': 'Some workers offline', 'online_count': online_count, 'total_count': total_count}

    @app.get('/api/status')
    def status():
        mirror = app.extensions.get('portal_mirror')
        if mirror:
            mirror.notify_if_stale()
        path = Path(app.config['STATUS_FILE']) if app.config['STATUS_FILE'] else library.metadata / 'Processing-status.json'
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            result = dict(available=False, stale=True, stage='Status unavailable', files=[])
        else:
            files = []
            for item in raw.get('files', []):
                files.append({'name': Path(item['path']).name, 'status': item['status'], 'error': bool(item.get('error'))})
            # Deliberately exclude private source/output paths, PID, and raw errors.
            result = {key: raw.get(key) for key in ['heartbeat', 'progress_at', 'stage', 'service', 'progress', 'storage', 'timezone']}
            result.update(available=True, stale=time.time() - raw.get('heartbeat', 0) > 120,
                          current_video=Path(raw['current_video']).name if raw.get('current_video') else None,
                          files=files, has_error=bool(raw.get('scan_error') or raw.get('mirror_error')))
        result['worker_health'] = compute_worker_health(result)
        return jsonify(result)

    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=int(os.environ.get('PORT', 8765)), threaded=True, debug=False)
