"""Bounded, public processor context for one declared published clip."""
import math


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def public_clip_study(session, clip):
    start, end = clip['original_start_seconds'], clip['original_end_seconds']
    observations = []
    raw = clip.get('processor_observations', [])
    if isinstance(raw, list):
        for item in raw[:200]:
            if not isinstance(item, dict):
                continue
            label = item.get('label')
            a, b = item.get('original_start_seconds'), item.get('original_end_seconds')
            if (not isinstance(label, str) or not label.strip() or len(label) > 120
                    or not number(a) or not number(b) or not start <= a < b <= end):
                continue
            # Only optional, explicitly public fields; never pass through raw logs.
            result = {'label': label.strip(), 'original_start_seconds': a,
                      'original_end_seconds': b, 'local_start_seconds': a-start,
                      'local_end_seconds': b-start}
            detail = item.get('detail')
            if isinstance(detail, str) and len(detail) <= 600:
                result['detail'] = detail
            score, kind = item.get('score'), item.get('score_kind')
            if number(score) and kind in {'score', 'margin', 'probability'}:
                if abs(score) <= 1_000_000 and (kind != 'probability' or 0 <= score <= 1):
                    result.update(score=score, score_kind=kind)
            observations.append(result)
            if len(observations) >= 20:
                break
    algorithm = session.get('algorithm')
    return {'schema_version': 1, 'session_id': session['id'], 'clip_id': clip['id'],
            'publication': session.get('published_at'),
            'algorithm': algorithm[:120] if isinstance(algorithm, str) else 'Not published',
            'hdr': bool(session['video'].get('hdr')),
            'original_start_seconds': start, 'original_end_seconds': end,
            'observations': observations,
            'time_note': 'Local offsets are approximate; encoded frames can round original cut boundaries.'}
