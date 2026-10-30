"""Courtside: a local-network tennis library, with no account system."""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from storage import Library
from state import State, Conflict


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(LIBRARY_ROOT=os.environ.get("TENNIS_LIBRARY_ROOT", "/library"),
                      MEDIA_ROOT=os.environ.get("TENNIS_MEDIA_ROOT"),
                      DATA_ROOT=os.environ.get("TENNIS_DATA_ROOT", "/data"),
                      STATUS_FILE=os.environ.get("TENNIS_STATUS_FILE"), MAX_CONTENT_LENGTH=8192)
    if config:
        app.config.update(config)
    library = Library(app.config["LIBRARY_ROOT"], app.config["MEDIA_ROOT"])
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
        data["like"] = shared.get(f"like:{identity}", {"value": False, "version": 0})
        data["overrides"] = shared.get(f"meta:{identity}", {"value": {}, "version": 0})
        edits = data["overrides"]["value"] or {}
        data["title"] = edits.get("title", data["title"])
        data["location_label"] = edits.get("location", "")
        for clip in data["clips"]:
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
    @app.get('/review/<identity>')
    def index(identity=None):
        if identity:
            session(identity)
        return render_template('index.html')

    @app.get('/api/sessions')
    def list_sessions():
        try:
            sessions, errors = library.sessions()
        except OSError:
            return jsonify(sessions=[], invalid_sessions=0, library_available=False, shared_preferences=True)
        shared = state.all()
        return jsonify(sessions=[decorate(s, shared) for s in sessions], invalid_sessions=errors,
                       library_available=(library.metadata / 'sessions').is_dir(), shared_preferences=True)

    @app.get('/api/sessions/<identity>')
    def get_session(identity):
        return jsonify(decorate(session(identity)))

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
            return send_file(p, conditional=True, mimetype='image/jpeg' if filename.endswith('.jpg') else 'video/mp4', max_age=0)
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
