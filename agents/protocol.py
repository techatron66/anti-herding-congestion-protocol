"""Versioned JSON control-message helpers."""
from __future__ import annotations
import json, time, uuid
VERSION = 1
MESSAGE_TYPES = {"INTENT", "GRANT", "REJECT", "COMMIT", "CANCEL", "RELEASE"}
def message(kind: str, **fields):
    if kind not in MESSAGE_TYPES: raise ValueError(f"unsupported message type: {kind}")
    return {"version": VERSION, "type": kind, "created_at": time.time(), **fields}
def encode(value): return json.dumps(value, separators=(",", ":")).encode("utf-8")
def decode(payload):
    value = json.loads(payload.decode("utf-8"))
    if value.get("version") != VERSION or value.get("type") not in MESSAGE_TYPES:
        raise ValueError("unsupported protocol version or message type")
    return value
def request_id(): return str(uuid.uuid4())
