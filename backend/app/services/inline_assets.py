"""Image files referenced from execution payloads, inlined only while a request is in flight.

Payloads used to carry each reference image as a base64 data URL, which put roughly half a megabyte into
the database for every scene. They now store `vo3-file:<path under storage>` and the image bytes are read
just before the payload is sent to the AI service.
"""

import base64
import copy
from pathlib import Path

FILE_REF_PREFIX = "vo3-file:"
# Left in finished rows whose inline image was dropped; nothing sends those payloads again.
PRUNED_REF = FILE_REF_PREFIX + "pruned"


class AssetFileMissing(RuntimeError):
    pass


def file_ref(storage: Path, path: Path) -> str:
    return FILE_REF_PREFIX + path.resolve().relative_to(storage.resolve()).as_posix()


def _ref_path(storage: Path, ref: str) -> Path:
    root = storage.resolve()
    path = (root / ref[len(FILE_REF_PREFIX):]).resolve()
    if ref == PRUNED_REF or root not in path.parents or not path.is_file():
        raise AssetFileMissing(ref)
    return path


def inline_payload(storage: Path, payload: dict) -> dict:
    """A copy whose file references are data URLs, ready to send; the stored payload keeps references."""
    assets = payload.get("assets") or []
    if not any(str(item.get("source_url", "")).startswith(FILE_REF_PREFIX) for item in assets):
        return payload
    sent = copy.deepcopy(payload)
    for item in sent["assets"]:
        source = str(item.get("source_url", ""))
        if source.startswith(FILE_REF_PREFIX):
            data = _ref_path(storage, source).read_bytes()
            item["source_url"] = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    return sent


def restore_refs(original: dict, request: dict) -> dict:
    """Put the file references of `original` back into a validated copy of it before it is stored."""
    refs = {
        item.get("asset_id"): item["source_url"] for item in original.get("assets") or []
        if str(item.get("source_url", "")).startswith(FILE_REF_PREFIX)
    }
    if not refs:
        return request
    request = copy.deepcopy(request)  # never alter the copy that was just sent
    for item in request.get("assets") or []:
        if item.get("asset_id") in refs:
            item["source_url"] = refs[item["asset_id"]]
    return request


def strip_inline(payload: dict) -> bool:
    """Drop inline images from a payload that will never be sent again; True when something changed."""
    changed = False
    for item in payload.get("assets") or []:
        if str(item.get("source_url", "")).startswith("data:"):
            item["source_url"] = PRUNED_REF
            changed = True
    return changed
