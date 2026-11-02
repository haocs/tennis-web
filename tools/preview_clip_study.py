"""Local-only synthetic playback fixture. Requires ffmpeg; never reads NAS media."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    parser.add_argument('--port', type=int, default=8871)
    args = parser.parse_args()
    if not args.ffmpeg:
        raise SystemExit('Pass --ffmpeg /path/to/ffmpeg for the synthetic preview.')
    root = args.root.resolve()
    identity = 'a'*64
    media = root/'library/media'/identity
    metadata = root/'library/metadata/sessions'
    (media/'Clips').mkdir(parents=True, exist_ok=True)
    (metadata/identity).mkdir(parents=True, exist_ok=True)
    clips = []
    for i, start in enumerate([3, 12, 22], 1):
        name = f'Clips/clip-{i:03d}.mp4'
        file = media/name
        if not file.exists():
            subprocess.run([args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                            '-f', 'lavfi', '-i', f'testsrc2=size=960x540:rate=30',
                            '-vf', f'hue=h={i*40}', '-t', '6', '-c:v', 'libx264',
                            '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(file)], check=True)
        clip = {'id': hashlib.sha256(f'demo-{i}'.encode()).hexdigest()[:24], 'index': i,
                'file': name, 'original_start_seconds': start, 'original_end_seconds': start+6,
                'duration_seconds': 6, 'playback_start_seconds': (i-1)*6,
                'playback_end_seconds': i*6}
        if i == 1:
            clip['processor_observations'] = [{'label': 'Demo movement signal', 'detail': 'Synthetic observation for interface testing; not a tennis judgment.', 'original_start_seconds': 4, 'original_end_seconds': 5.5, 'score': 2.6, 'score_kind': 'margin'}]
        clips.append(clip)
    if not (media/'Practice.mp4').exists():
        subprocess.run([args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-stream_loop', '2', '-i', str(media/clips[0]['file']), '-c', 'copy', str(media/'Practice.mp4')], check=True)
    if not (media/'poster.jpg').exists():
        subprocess.run([args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(media/clips[0]['file']), '-frames:v', '1', str(media/'poster.jpg')], check=True)
    data = {'schema_version': 1, 'id': identity, 'title': 'Clip study demo',
            'source': {'name': 'synthetic.mov', 'sha256': identity, 'recorded_at': '2026-10-24T15:00:00-0700', 'duration_seconds': 30, 'location': None},
            'published_at': 1, 'media_directory': identity, 'video': {'file': 'Practice.mp4', 'poster': 'poster.jpg', 'duration_seconds': 18, 'width': 960, 'height': 540, 'hdr': False},
            'clips': clips, 'removed_seconds': 12, 'kept_seconds': 18, 'algorithm': 'preview-rules-v1'}
    (metadata/f'{identity}.json').write_text(json.dumps(data))
    (metadata/identity/'edit-timeline.csv').write_text('action,start,end\npreserved,3,9\n')
    app = create_app({'LIBRARY_ROOT': str(root/'library'), 'DATA_ROOT': str(root/'data')})
    print(f'Clip study preview: http://127.0.0.1:{args.port}/review/{identity}?clip={clips[0]["id"]}', flush=True)
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
