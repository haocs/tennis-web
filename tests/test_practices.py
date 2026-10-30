from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from practices import recording_time, group_recordings


def video(identity, recorded_at, duration=600, published=0):
    iso, epoch = recording_time(recorded_at)
    return {'id': identity, 'recorded_at_iso': iso, 'recorded_at_epoch': epoch,
            'source': {'duration_seconds': duration}, 'published_at': published}


def test_original_creation_time_not_publish_time_and_offset_normalization():
    early = video('a', '2026-10-26T16:00:00-0700', published=9000000000)
    late = video('b', '2026-10-26T18:00:00-07:00', published=1)
    groups = group_recordings([late, early])
    assert [g['recording_ids'] for g in groups] == [['a'], ['b']]
    assert early['recorded_at_iso'] == '2026-10-26T16:00:00-07:00'
    assert recording_time('2026-10-26T23:00:00Z')[1] == early['recorded_at_epoch']


def test_gap_measured_from_original_end_with_inclusive_30_minute_boundary():
    # A 50-minute video and a recording starting 80 minutes later share a practice.
    first = video('a', '2026-01-01T10:00:00Z', duration=3000)
    at_boundary = video('b', '2026-01-01T11:20:00Z')
    outside = video('c', '2026-01-01T12:00:01Z')
    groups = group_recordings([outside, at_boundary, first])
    assert [g['recording_ids'] for g in groups] == [['a', 'b'], ['c']]


def test_overlapping_recordings_keep_latest_end_and_cross_midnight():
    a = video('a', '2026-01-01T23:00:00-0800', duration=3600)
    b = video('b', '2026-01-01T23:10:00-0800', duration=60)
    c = video('c', '2026-01-02T00:20:00-0800', duration=60)
    assert group_recordings([c,b,a])[0]['recording_ids'] == ['a','b','c']


def test_missing_invalid_and_unzoned_times_are_not_guessed_or_grouped():
    known = video('a', '2026-01-01T10:00:00Z')
    missing = video('b', None, published=known['recorded_at_epoch'])
    invalid = video('c', 'not a date', published=known['recorded_at_epoch'])
    unzoned = video('d', '2026-01-01T10:00:00', published=known['recorded_at_epoch'])
    groups = group_recordings([unzoned, missing, known, invalid])
    assert [g['recording_ids'] for g in groups] == [['a'], ['b'], ['c'], ['d']]
    assert all(g['started_at_epoch'] is None for g in groups[1:])


def test_configured_gap_and_real_instant_across_daylight_saving_change():
    a = video('a', '2026-11-01T01:30:00-0700', duration=600)
    b = video('b', '2026-11-01T01:30:00-0800', duration=600)
    assert len(group_recordings([a,b], gap_minutes=30)) == 2
    assert len(group_recordings([a,b], gap_minutes=60)) == 1
