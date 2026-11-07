"""Bounded streaming archive I/O shared by the neutral image tools."""
import contextlib
import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import tempfile
from compose_rootfs import normalized_name as normalized


def digest(path):
    hashed = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hashed.update(chunk)
    return hashed.hexdigest()


def write_json(path, document, mode=0o644):
    import json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps(document, indent=2) + '\n')
    path.chmod(mode)


@contextlib.contextmanager
def archive(path):
    with Path(path).open('rb') as source, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(['timeout', '--kill-after=5s', '900s', 'zstd', '-qdc'],
            stdin=source, stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as stream:
                yield stream
            with open(os.devnull, 'wb') as sink:
                shutil.copyfileobj(process.stdout, sink)
            if process.wait(timeout=30):
                raise ValueError('Rootfs decompression failed')
        finally:
            process.stdout.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
