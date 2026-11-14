"""Asynchronous best-effort mirror of coaching/export tasks to Worker Portal."""
import threading
import time
from processing_bridge import ProcessingRejected, ProcessingUnavailable


class PortalMirror:
    """Coalesces coaching, learning-plan, and clip-export updates for Worker Portal."""

    def __init__(self, app, library, state):
        self.app = app
        self.library = library
        self.state = state
        self._lock = threading.Lock()
        self._dirty = False
        self._running = False
        self._deleted = set()
        self.last_sync_at = 0.0

    def _session_lookup(self):
        shared = [None]

        def lookup(sid):
            if not isinstance(sid, str) or not sid:
                return None
            if shared[0] is None:
                try:
                    shared[0] = self.state.all()
                except Exception:
                    shared[0] = {}
            override = ((shared[0].get(f'meta:{sid}') or {}).get('value') or {}).get('title')
            if isinstance(override, str) and override.strip():
                return override.strip()[:160]
            try:
                title = self.library.get(sid).get('title')
                if isinstance(title, str) and title.strip():
                    return title.strip()[:160]
            except Exception:
                pass
            return None

        return lookup

    def sync_now(self):
        """Build a sanitized coaching/export snapshot and POST it to processing-server."""
        client = self.app.extensions.get('processing_client')
        queue = self.app.extensions.get('coaching')
        if not client or not getattr(client, 'url', None) or not queue:
            with self._lock:
                self._deleted.clear()
                self._dirty = False
            return None
        with self._lock:
            deleted = sorted(self._deleted)[:200]
            self._deleted.clear()
            self._dirty = False
        payload = queue.portal_snapshot(session_lookup=self._session_lookup(), deleted=deleted)
        try:
            res = client.call('/external-sync', payload, method='POST')
        except (ProcessingUnavailable, ProcessingRejected, Exception) as exc:
            if deleted:
                with self._lock:
                    if len(self._deleted) < 500:
                        self._deleted.update(deleted)
            self.app.logger.warning('Worker Portal mirror sync skipped: %s', exc)
            return None
        if isinstance(res, dict) and isinstance(res.get('desired_ai_config'), dict):
            try:
                applied = queue.apply_portal_ai_config(res['desired_ai_config'])
                if applied is not None:
                    follow_payload = queue.portal_snapshot(session_lookup=self._session_lookup(), deleted=[])
                    res = client.call('/external-sync', follow_payload, method='POST')
            except (ProcessingUnavailable, ProcessingRejected, Exception) as exc:
                self.app.logger.warning('Worker Portal desired AI config follow-up failed: %s', exc)
        self.last_sync_at = time.monotonic()
        return res

    def _run_loop(self):
        try:
            while True:
                self.sync_now()
                with self._lock:
                    if not self._dirty:
                        self._running = False
                        return
        except Exception:
            with self._lock:
                self._running = False

    def notify(self, deleted_ids=None):
        """Record a state change and schedule a non-blocking Worker Portal sync."""
        if deleted_ids:
            with self._lock:
                for item in deleted_ids:
                    if isinstance(item, str) and item and len(self._deleted) < 500:
                        self._deleted.add(item)
        client = self.app.extensions.get('processing_client')
        if not client or not getattr(client, 'url', None):
            return
        if self.app.config.get('PORTAL_SYNC_INLINE'):
            self.sync_now()
            return
        if self.app.config.get('TESTING'):
            return
        with self._lock:
            self._dirty = True
            if self._running:
                return
            self._running = True
        threading.Thread(target=self._run_loop, name='portal-mirror-sync', daemon=True).start()

    def notify_if_stale(self, max_age=30.0):
        """Refresh the mirror if no successful sync has completed within max_age seconds."""
        if (time.monotonic() - self.last_sync_at) >= max_age:
            self.notify()


def register(app, library, state):
    """Attach a PortalMirror instance to app.extensions."""
    mirror = PortalMirror(app, library, state)
    app.extensions['portal_mirror'] = mirror
    return mirror


def notify_portal_mirror(app, deleted_ids=None, stale_only=False):
    """Trigger a PortalMirror refresh when attached to app."""
    mirror = app.extensions.get('portal_mirror')
    if not mirror:
        return
    if stale_only:
        mirror.notify_if_stale()
    else:
        mirror.notify(deleted_ids=deleted_ids)
