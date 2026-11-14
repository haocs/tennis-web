"""Optional, fault-isolated 2D pose extraction on extracted coaching stills."""
import hashlib
import json
import math
from pathlib import Path
import time

from .annotations import POSE_ENRICHMENT_SCHEMA_VERSION

MODEL_DIR = Path(__file__).resolve().parent / 'models'
MANIFEST_PATH = MODEL_DIR / 'MANIFEST.json'
LIMITATIONS_TEXT = (
    'Approximate 2D monocular still-frame pose estimates; not exact pixel coordinates, '
    'center of mass, or 3D biomechanical angles.'
)

_LANDMARK_MAP = (
    (0, 'nose'),
    (11, 'left_shoulder'),
    (12, 'right_shoulder'),
    (13, 'left_elbow'),
    (14, 'right_elbow'),
    (15, 'left_wrist'),
    (16, 'right_wrist'),
    (23, 'left_hip'),
    (24, 'right_hip'),
    (25, 'left_knee'),
    (26, 'right_knee'),
    (27, 'left_ankle'),
    (28, 'right_ankle'),
)


def _finite_unit(value):
    return type(value) in (int, float) and math.isfinite(value) and 0.0 <= value <= 1.0


def load_verified_model():
    """Verify bundled pose model metadata and SHA-256 before loading MediaPipe."""
    if not MANIFEST_PATH.is_file():
        return None
    manifest = json.loads(MANIFEST_PATH.read_text())
    files = manifest.get('files') or []
    if not files:
        return None
    entry = files[0]
    model_path = (MODEL_DIR / str(entry.get('path') or '')).resolve()
    if model_path.parent != MODEL_DIR.resolve() or not model_path.is_file():
        return None
    expected_sha = str(entry.get('sha256') or '')
    if len(expected_sha) != 64:
        return None
    digest = hashlib.sha256()
    with model_path.open('rb') as handle:
        while chunk := handle.read(1 << 16):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha:
        return None
    return dict(
        path=model_path,
        name=str(manifest.get('package') or 'mediapipe_pose_landmarker_lite'),
        version=str(manifest.get('version') or ''),
        license=str(manifest.get('license') or 'Apache-2.0'),
        sha256=expected_sha,
    )


def pose_available():
    try:
        import mediapipe  # noqa: F401
    except Exception:
        return False
    return load_verified_model() is not None


def _landmark_attr(lm, key, default=1.0):
    val = lm.get(key) if isinstance(lm, dict) else getattr(lm, key, None)
    if type(val) in (int, float) and math.isfinite(val):
        return max(0.0, min(1.0, float(val)))
    return default


def _pose_score(landmarks):
    xs, ys, confs = [], [], []
    for idx, _name in _LANDMARK_MAP:
        if idx >= len(landmarks):
            continue
        lm = landmarks[idx]
        x = lm['x'] if isinstance(lm, dict) else getattr(lm, 'x', None)
        y = lm['y'] if isinstance(lm, dict) else getattr(lm, 'y', None)
        if not (_finite_unit(x) and _finite_unit(y)):
            continue
        conf = min(_landmark_attr(lm, 'visibility'), _landmark_attr(lm, 'presence'))
        if conf < 0.2:
            continue
        xs.append(float(x))
        ys.append(float(y))
        confs.append(conf)
    if len(confs) < 4:
        return 0.0
    span_x = max(0.05, max(xs) - min(xs))
    span_y = max(0.08, max(ys) - min(ys))
    mean_conf = sum(confs) / len(confs)
    return mean_conf * (span_y + 0.35 * span_x)


def _extract_joints(landmarks):
    joints = {}
    for idx, name in _LANDMARK_MAP:
        if idx >= len(landmarks):
            continue
        lm = landmarks[idx]
        x = lm['x'] if isinstance(lm, dict) else getattr(lm, 'x', None)
        y = lm['y'] if isinstance(lm, dict) else getattr(lm, 'y', None)
        if not (_finite_unit(x) and _finite_unit(y)):
            continue
        conf = min(_landmark_attr(lm, 'visibility'), _landmark_attr(lm, 'presence'))
        joints[name] = dict(
            x=round(float(x), 4),
            y=round(float(y), 4),
            confidence=round(float(conf), 4),
        )
    return joints


def _analyze_pose_candidates(pose_landmarks_list, subject_hint=''):
    """Select the dominant player only when not ambiguous across multiple visible people."""
    candidates = [p for p in (pose_landmarks_list or []) if p]
    person_count = len(candidates)
    if person_count == 0:
        return dict(person_count=0, subject_ambiguous=False, ambiguity_reason='', joints={})

    scored = sorted(
        ((_pose_score(lm_list), lm_list) for lm_list in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best_landmarks = scored[0]
    if best_score <= 0.0:
        return dict(person_count=person_count, subject_ambiguous=False, ambiguity_reason='', joints={})

    has_hint = bool((subject_hint or '').strip()) if isinstance(subject_hint, str) else False
    if person_count > 1:
        if has_hint:
            return dict(
                person_count=person_count,
                subject_ambiguous=True,
                ambiguity_reason='Multiple players visible with target-player cue; landmarks suppressed',
                joints={},
            )
        second_score = scored[1][0]
        if second_score >= 0.75 * best_score:
            return dict(
                person_count=person_count,
                subject_ambiguous=True,
                ambiguity_reason='Multiple prominent people visible; target player uncertain',
                joints={},
            )

    return dict(
        person_count=person_count,
        subject_ambiguous=False,
        ambiguity_reason='',
        joints=_extract_joints(best_landmarks),
    )


def extract_pose_for_frames(local_dir, frames, *, subject_hint='', budget_seconds=8.0):
    """Run bounded CPU pose extraction on extracted stills; never raise on model/runtime errors."""
    model = load_verified_model()
    if not model:
        return None, 'unavailable'
    try:
        import mediapipe as mp
        from mediapipe.tasks.python.core.base_options import BaseOptions
        from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode
    except Exception:
        return None, 'unavailable'

    deadline = time.monotonic() + max(0.5, float(budget_seconds))
    options = PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model['path'])),
        running_mode=RunningMode.IMAGE,
        num_poses=4,
        min_pose_detection_confidence=0.45,
        min_pose_presence_confidence=0.45,
    )

    frame_results = []
    try:
        with PoseLandmarker.create_from_options(options) as landmarker:
            for entry in frames:
                if time.monotonic() > deadline:
                    return None, 'unavailable'
                img_path = Path(local_dir) / entry['file']
                if not img_path.is_file():
                    continue
                image = mp.Image.create_from_file(str(img_path))
                result = landmarker.detect(image)
                analysis = _analyze_pose_candidates(result.pose_landmarks, subject_hint=subject_hint)
                frame_results.append(
                    dict(
                        file=entry['file'],
                        sha256=entry['sha256'],
                        width=int(image.width),
                        height=int(image.height),
                        **analysis,
                    )
                )
    except Exception:
        return None, 'unavailable'

    return (
        dict(
            schema_version=POSE_ENRICHMENT_SCHEMA_VERSION,
            coordinate_space='display_oriented_evidence_normalized',
            model=dict(
                name=model['name'],
                version=model['version'],
                sha256=model['sha256'],
                license=model['license'],
            ),
            limitations=LIMITATIONS_TEXT,
            frames=frame_results,
        ),
        'ok',
    )
