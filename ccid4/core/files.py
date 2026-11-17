"""Serve a file with HTTP Range support (audio/video seeking) without loading it into memory."""

import os
import re

from django.http import FileResponse, HttpResponse, StreamingHttpResponse

RANGE = re.compile(r"bytes=(\d+)-(\d*)")


def is_first_request(request) -> bool:
    """True for the request that starts playback (no Range, or a Range from byte 0) — log only that one."""
    m = RANGE.match(request.headers.get("Range", ""))
    return not m or m.group(1) == "0"


def ranged(request, path: str, content_type: str, filename: str = ""):
    size = os.path.getsize(path)
    m = RANGE.match(request.headers.get("Range", ""))
    if not m:
        resp = FileResponse(open(path, "rb"), content_type=content_type)  # noqa: SIM115 - closed by Django
        resp["Accept-Ranges"] = "bytes"
        if filename:
            from urllib.parse import quote

            resp["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(filename)}"
        return resp
    start, end = int(m.group(1)), int(m.group(2) or size - 1)
    end = min(end, size - 1)
    if start > end:
        return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"})

    def stream():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                block = f.read(min(1 << 16, left))
                if not block:
                    break
                left -= len(block)
                yield block

    resp = StreamingHttpResponse(stream(), status=206, content_type=content_type)
    resp["Content-Range"], resp["Content-Length"], resp["Accept-Ranges"] = (
        f"bytes {start}-{end}/{size}",
        str(end - start + 1),
        "bytes",
    )
    return resp
