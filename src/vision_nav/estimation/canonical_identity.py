"""Compose exact canonical JSON identities from immutable encoded fields."""

import hashlib
import json


def encode(value):
    """Same JSON convention as correlated_window.payload_digest, not wire IPC."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def object_digest(fields):
    """Hash a private object's already-encoded values without encoding them again.

    Only use with immutable bytes returned by encode, never untrusted raw JSON.
    Keys still use the standard JSON encoder. No parse, scalar formatting or
    alternate hash convention is introduced at the composition boundary.
    """
    result = hashlib.sha256(b"{")
    for index, key in enumerate(sorted(fields)):
        if type(key) is not str or type(fields[key]) is not bytes:
            raise TypeError("Canonical fields require string keys and owned encoded bytes")
        if index:
            result.update(b",")
        result.update(encode(key))
        result.update(b":")
        result.update(fields[key])
    result.update(b"}")
    return result.hexdigest()
