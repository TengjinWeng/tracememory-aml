"""Decoded-pixel fingerprints. No learned visual embeddings or model calls."""
import base64
import hashlib
from io import BytesIO
import struct
import warnings

from PIL import Image, ImageOps


def pixel_fingerprints(content):
    if isinstance(content, str):
        return []
    result = []
    for part in content:
        if part.get("type") != "image_url":
            continue
        binary = base64.b64decode(part["image_url"]["url"].split(",", 1)[1], validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(binary)) as source:
                if source.width * source.height > 40_000_000:
                    raise ValueError("decoded image exceeds candidate pixel limit")
                # EXIF orientation is interpreted consistently; original evidence is retained.
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                digest = hashlib.sha256(struct.pack("!II", *normalized.size) + normalized.tobytes()).hexdigest()
                result.append(digest)
    return result
