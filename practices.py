"""Home-page grouping based exclusively on original, timezone-aware recording time."""
from datetime import datetime
import math


def recording_time(value):
    """Never substitute publish time or infer a timezone from the server's clock."""
    if not isinstance(value, str) or not value.strip():
        return None, None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None, None
        stamp = parsed.timestamp()
        if not math.isfinite(stamp):
            return None, None
        return parsed.isoformat(), stamp
    except (ValueError, OverflowError):
        return None, None


def group_recordings(recordings, gap_minutes=30):
    """Overlaps and gaps <= threshold join a practice; undated videos stand alone."""
    dated = sorted((s for s in recordings if s['recorded_at_epoch'] is not None),
                   key=lambda s: (s['recorded_at_epoch'], s['id']))
    groups = []
    gap_seconds = gap_minutes * 60
    for s in dated:
        start = s['recorded_at_epoch']
        duration = float(s['source']['duration_seconds'])
        end = start + (duration if math.isfinite(duration) and duration > 0 else 0)
        if groups and start - groups[-1]['ended_at_epoch'] <= gap_seconds:
            groups[-1]['recording_ids'].append(s['id'])
            groups[-1]['ended_at_epoch'] = max(groups[-1]['ended_at_epoch'], end)
        else:
            groups.append({'id': s['id'], 'recording_ids': [s['id']],
                           'recorded_at_iso': s['recorded_at_iso'],
                           'started_at_epoch': start, 'ended_at_epoch': end})
    for s in sorted((s for s in recordings if s['recorded_at_epoch'] is None), key=lambda s: s['id']):
        groups.append({'id': s['id'], 'recording_ids': [s['id']], 'recorded_at_iso': None,
                       'started_at_epoch': None, 'ended_at_epoch': None})
    return groups
