"""Small cached clip posters, generated from published media without changing it."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


class PosterUnavailable(Exception):
    pass


class ClipPosters:
    def __init__(self, directory, binary='ffmpeg'):
        self.directory = Path(directory)
        self.binary = binary

    def get(self, data, clip, source):
        stat = source.stat()
        key = hashlib.sha256(json.dumps([data['id'], clip['id'], data['published_at'],
                                        str(source), stat.st_size, stat.st_mtime_ns,
                                        bool(data['video'].get('hdr')), 1]).encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        output = self.directory / (key+'.jpg')
        if output.is_file():
            return output
        # A shared lock bounds NAS CPU use across Gunicorn workers. Another
        # request may have produced this poster while we waited.
        with (self.directory/'generation.lock').open('a') as lock:
            until = time.monotonic()+30
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= until:
                        raise PosterUnavailable()
                    time.sleep(.05)
            if output.is_file():
                return output
            filters = 'scale=320:-2'
            if data['video'].get('hdr'):
                filters = 'zscale=w=320:h=-2:t=linear:npl=100,format=gbrpf32le,tonemap=hable:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv'
            filters += ',format=yuvj420p,setsar=1'
            fd, temporary = tempfile.mkstemp(suffix='.jpg', dir=self.directory)
            os.close(fd)
            try:
                at = min(.25, clip['duration_seconds']/2)
                subprocess.run([self.binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                                '-threads', '1', '-ss', str(at), '-i', str(source), '-map', '0:v:0',
                                '-frames:v', '1', '-an', '-sn', '-vf', filters, '-filter_threads', '1',
                                '-threads', '1', '-q:v', '4', temporary],
                               check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                with open(temporary, 'rb') as image:
                    if image.read(2) != b'\xff\xd8':
                        raise PosterUnavailable()
                os.replace(temporary, output)
            except (OSError, subprocess.SubprocessError) as error:
                raise PosterUnavailable() from error
            finally:
                Path(temporary).unlink(missing_ok=True)
        return output
