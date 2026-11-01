"""Courtside: a local-network tennis library, with no account system."""
import copy
from datetime import datetime
import json
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
from practices import recording_time, group_recordings
from state import State, Conflict
from courts import COURT_ID, court_list, validate_court, enrich_groups


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(LIBRARY_ROOT=os.environ.get("TENNIS_LIBRARY_ROOT", "/library"),
                      MEDIA_ROOT=os.environ.get("TENNIS_MEDIA_ROOT"),
                      DATA_ROOT=os.environ.get("TENNIS_DATA_ROOT", "/data"),
                      STATUS_FILE=os.environ.get("TENNIS_STATUS_FILE"),
                      PROCESSING_ROOM_URL=os.environ.get("TENNIS_PROCESSING_ROOM_URL", "/processing"),
                      SESSION_GAP_MINUTES=os.environ.get("TENNIS_SESSION_GAP_MINUTES", "30"),
                      LIBRARY_SMB_URL=os.environ.get("TENNIS_LIBRARY_SMB_URL"),
                      MEDIA_SMB_URL=os.environ.get("TENNIS_MEDIA_SMB_URL"), MAX_CONTENT_LENGTH=8192)
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
    app.extensions.update(library=library, state=state)

    def session(identity):
        try:
            return library.get(identity)
        except KeyError:
            abort(404, "Session not found")
        except (OSError, ValueError, TypeError):
            abort(503, "This session's metadata is unavailable")

    def decorate(data, shared=None):
        data = copy.deepcopy(data)
        shared = shared if shared is not None else state.all()
        identity = data["id"]
        data['recorded_at_iso'], data['recorded_at_epoch'] = recording_time(data['source'].get('recorded_at'))
        directory = data.get('media_directory', identity)
        data['video']['nas_url'] = nas_media_address(media_smb, directory, data['video']['file'])
        data["like"] = shared.get(f"like:{identity}", {"value": False, "version": 0})
        data["overrides"] = shared.get(f"meta:{identity}", {"value": {}, "version": 0})
        edits = data["overrides"]["value"] or {}
        data["title"] = edits.get("title", data["title"])
        data["location_label"] = edits.get("location", "")
        for clip in data["clips"]:
            clip['nas_url'] = nas_media_address(media_smb, directory, clip['file'])
            clip["like"] = shared.get(f"like:{identity}:{clip['id']}", {"value": False, "version": 0})
        return data

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
        response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
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
    @app.get('/processing')
    @app.get('/review/<identity>')
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
        decorated = [decorate(s, shared) for s in sessions]
        groups = enrich_groups(group_recordings(decorated, gap_minutes), decorated, shared)
        return jsonify(sessions=decorated, practice_groups=groups, courts=court_list(shared), invalid_sessions=errors,
                       library_available=(library.metadata / 'sessions').is_dir(), shared_preferences=True)

    @app.get('/api/sessions/<identity>')
    def get_session(identity):
        return jsonify(decorate(session(identity)))

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
        group = next((g for g in group_recordings(decorated,gap_minutes) if g['id']==identity),None)
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

    @app.put('/api/sessions/<identity>/likes/<item>')
    def like(identity, item):
        data = session(identity)
        if item != 'session' and item not in {c['id'] for c in data['clips']}:
            abort(404, 'Clip not found')
        body = request.get_json()
        if not isinstance(body, dict):
            abort(400, 'Expected a JSON object')
        if type(body.get('liked')) is not bool or type(body.get('version')) is not int or body['version'] < 0:
            abort(400, 'A liked value and current version are required')
        key = f'like:{identity}' + (f':{item}' if item != 'session' else '')
        try:
            return jsonify(state.update(key, body['liked'], body['version']))
        except Conflict as conflict:
            return jsonify(error='Someone else updated this item. Its latest value has been loaded.', current=conflict.current), 409

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

    @app.get('/media/<identity>/<path:filename>')
    def media(identity, filename):
        data = session(identity)
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

    @app.get('/api/status')
    def status():
        path = Path(app.config['STATUS_FILE']) if app.config['STATUS_FILE'] else library.metadata / 'Processing-status.json'
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return jsonify(available=False, stale=True, stage='Status unavailable', files=[])
        files = []
        for item in raw.get('files', []):
            files.append({'name': Path(item['path']).name, 'status': item['status'], 'error': bool(item.get('error'))})
        # Deliberately exclude private source/output paths, PID, and raw errors.
        result = {key: raw.get(key) for key in ['heartbeat', 'progress_at', 'stage', 'service', 'progress', 'storage', 'timezone']}
        result.update(available=True, stale=time.time() - raw.get('heartbeat', 0) > 120,
                      current_video=Path(raw['current_video']).name if raw.get('current_video') else None,
                      files=files, has_error=bool(raw.get('scan_error') or raw.get('mirror_error')))
        return jsonify(result)

    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=int(os.environ.get('PORT', 8765)), threaded=True, debug=False)
