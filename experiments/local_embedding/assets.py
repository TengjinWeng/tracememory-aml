"""Download a CPU wheel from a mirror, checking the publisher's SHA256."""
import hashlib
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
WHEEL = ROOT / "tools/embedding-wheels/torch-2.6.0+cpu-cp310-cp310-linux_x86_64.whl"
WHEEL_URL = "https://mirrors.nju.edu.cn/pytorch/whl/cpu/torch-2.6.0%2Bcpu-cp310-cp310-linux_x86_64.whl"
# Published by https://download.pytorch.org/whl/cpu/torch/ for this exact wheel.
WHEEL_SHA256 = "35a9e78b7e4096968b54c1a198687b981569c50ae93e661aa430f9fd208da102"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(2**20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, target, size, digest):
    if target.is_file() and target.stat().st_size == size and sha256(target) == digest:
        print("Already verified:", target.name, flush=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    os.ftruncate(descriptor, size)

    def part(bounds):
        start, end = bounds
        request = Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "TraceMemory-dev/0.1"})
        with urlopen(request, timeout=30) as response:
            expected = f"bytes {start}-{end}/{size}"
            if response.status != 206 or response.headers.get("Content-Range") != expected:
                raise RuntimeError("download source did not honor the byte range")
            offset = start
            while offset <= end:
                chunk = response.read(min(2**20, end-offset+1))
                if not chunk:
                    raise RuntimeError("incomplete asset download")
                view = memoryview(chunk)
                while view:
                    written = os.pwrite(descriptor, view, offset)
                    if written <= 0:
                        raise RuntimeError("could not write downloaded asset")
                    offset += written
                    view = view[written:]

    try:
        width = (size + 3) // 4
        ranges = [(start, min(size-1, start+width-1)) for start in range(0,size,width)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(part, ranges))
        os.fsync(descriptor)
        if sha256(temporary) != digest:
            raise RuntimeError("asset checksum differs from the publisher's pinned artifact")
        temporary.replace(target)
        print("Verified download:", target.name, flush=True)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    download(WHEEL_URL, WHEEL, 178630806, WHEEL_SHA256)
