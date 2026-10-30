"""Read-only filesystem adapter for the published library contract v1."""
from pathlib import Path
import json
import re
import math
from urllib.parse import urlparse, unquote

IDENTITY = re.compile(r"[a-f0-9]{64}")
CLIP_ID = re.compile(r"[a-f0-9]{24}")
ARTIFACTS = {"edit-timeline.json", "edit-timeline.csv", "timestamps.csv", "Review.html", "poster.jpg"}


def storage_path(value):
    parsed = urlparse(str(value))
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError("Storage must be a mounted filesystem path or file:// URL. Mount SMB/NFS outside the app.")
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("Remote file URL hosts are unsupported; mount the storage first")
        value = unquote(parsed.path)
    return Path(value).expanduser().resolve()


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Invalid storage path")
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Storage path escaped its root")
    return result


class Library:
    def __init__(self, root, media_root=None):
        self.root = storage_path(root)
        self.metadata = self.root / "metadata"
        self.media_root = storage_path(media_root) if media_root else self.root / "media"

    def get(self, identity):
        if not IDENTITY.fullmatch(identity):
            raise KeyError(identity)
        path = self.metadata / "sessions" / f"{identity}.json"
        try:
            if path.is_symlink():
                raise ValueError("Symlink manifests are unsupported")
            data = json.loads(path.read_text())
            self.validate(data, identity)
            return data
        except FileNotFoundError:
            raise KeyError(identity)

    def validate(self, data, identity):
        if data.get("schema_version") != 1 or data.get("id") != identity:
            raise ValueError("Unsupported session manifest")
        directory = data.get("media_directory", identity)
        if Path(directory).name != directory or directory.startswith('.'):
            raise ValueError("Invalid media directory")
        if data["video"]["file"] != "Practice.mp4" or data["video"]["duration_seconds"] <= 0:
            raise ValueError("Invalid combined video")
        if not isinstance(data.get("clips"), list) or not data["clips"]:
            raise ValueError("A published session must contain clips")
        previous = -1
        original_end = 0
        ids = set()
        for index, c in enumerate(data["clips"], 1):
            values = [c[k] for k in ("duration_seconds", "playback_start_seconds", "playback_end_seconds", "original_start_seconds", "original_end_seconds")]
            if c["index"] != index or any(type(v) not in (int, float) or not math.isfinite(v) for v in values) or c["duration_seconds"] <= 0:
                raise ValueError("Invalid clip timing")
            if c["original_start_seconds"] < original_end:
                raise ValueError("Original intervals overlap")
            original_end = c["original_end_seconds"]
            if not CLIP_ID.fullmatch(c["id"]) or c["id"] in ids:
                raise ValueError("Invalid clip identity")
            if not re.fullmatch(r"Clips/clip-\d{3,}.mp4", c["file"]):
                raise ValueError("Invalid clip file")
            if not previous < c["playback_start_seconds"] < c["playback_end_seconds"] <= data["video"]["duration_seconds"] + .1:
                raise ValueError("Invalid chapter order")
            if not 0 <= c["original_start_seconds"] < c["original_end_seconds"] <= data["source"]["duration_seconds"] + .001:
                raise ValueError("Invalid original timeline")
            previous = c["playback_start_seconds"]
            ids.add(c["id"])

    def sessions(self):
        result, errors = [], 0
        for p in sorted((self.metadata / "sessions").glob("*.json")):
            if not IDENTITY.fullmatch(p.stem):
                continue
            try:
                result.append(self.get(p.stem))
            except (KeyError, ValueError, TypeError, OSError):
                errors += 1
        return result, errors

    def media(self, session, filename):
        allowed = {session["video"]["file"]} | {c["file"] for c in session["clips"]} | {"poster.jpg"}
        if filename not in allowed:
            raise KeyError(filename)
        directory = inside(self.media_root, session.get("media_directory", session["id"]))
        return inside(directory, filename)

    def artifact(self, identity, filename):
        self.get(identity)
        if filename not in ARTIFACTS:
            raise KeyError(filename)
        return inside(self.metadata, f"sessions/{identity}/{filename}")
