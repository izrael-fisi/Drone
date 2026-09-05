"""Size-checked standard JSON without a Python generator per numeric element."""

import json
import math


def _prepare(value, limit, *, own):
    """Count exact ASCII JSON bytes, optionally making the decoded-form copy."""
    if type(limit) is not int or limit <= 0:
        raise ValueError("Expected a positive JSON byte limit")
    remaining = limit
    ancestors = set()

    def consume(size):
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            raise ValueError("Delayed alignment input exceeds byte limit")

    def string_size(text):
        consume(2)
        # Chunking also bounds temporary expansion of control/non-ASCII text.
        if len(text) > remaining:
            raise ValueError("Delayed alignment input exceeds byte limit")
        for offset in range(0, len(text), 4096):
            consume(len(json.dumps(text[offset:offset+4096], ensure_ascii=True))-2)

    def visit(item, depth):
        if depth > 64:
            raise ValueError("JSON nesting exceeds 64 levels")
        kind = type(item)
        if item is None:
            consume(4)
            return None
        elif kind is bool:
            consume(4 if item else 5)
            return item
        elif isinstance(item, int):
            if kind is not int:
                item = int.__int__(item)
            # Reject obviously oversized integers before decimal formatting.
            if int.bit_length(item) > 4*remaining:
                raise ValueError("Delayed alignment input exceeds byte limit")
            consume(len(int.__repr__(item)))
            return item
        elif isinstance(item, float):
            if kind is not float:
                item = float.__float__(item)
            if not math.isfinite(item):
                raise ValueError("Nonfinite values are not valid estimator JSON")
            consume(len(float.__repr__(item)))
            return item
        elif kind is str:
            string_size(item)
            return item
        elif kind in (dict, list, tuple):
            identity = id(item)
            if identity in ancestors:
                raise ValueError("Circular estimator JSON")
            ancestors.add(identity)
            consume(2+2*max(0, len(item)-1))
            if kind in (list, tuple) and len(item) >= 16 and all(type(child) is float for child in item):
                if depth == 64:
                    raise ValueError("JSON nesting exceeds 64 levels")
                # Covariance rows contain immutable builtin floats. Count their
                # exact bytes in small C-encoded chunks, not one Python visit
                # and repr per element. Temporary encoding stays below 7 KiB.
                result = [] if own else None
                for offset in range(0, len(item), 256):
                    chunk = item[offset:offset+256]
                    if not all(type(child) is float for child in chunk):
                        raise TypeError("Numeric estimator JSON row changed during validation")
                    try:
                        encoded = json.dumps(chunk, allow_nan=False)
                    except ValueError as exc:
                        raise ValueError("Nonfinite values are not valid estimator JSON") from exc
                    consume(len(encoded)-2-2*(len(chunk)-1))
                    if own:
                        result.extend(chunk)
                ancestors.remove(identity)
                return result
            if kind is dict:
                result = {} if own else None
                for key, child in item.items():
                    if type(key) is not str:
                        raise TypeError("Estimator JSON keys must be strings")
                    string_size(key)
                    consume(2)
                    checked = visit(child, depth+1)
                    if own:
                        result[key] = checked
            else:
                result = [] if own else None
                for child in item:
                    checked = visit(child, depth+1)
                    if own:
                        result.append(checked)
            ancestors.remove(identity)
            # Preserve the key iteration order of the former sorted JSON round trip.
            return {key: result[key] for key in sorted(result)} if own and kind is dict else result
        else:
            raise TypeError("Unsupported estimator JSON type: "+kind.__name__)

    return visit(value, 0)


def dumps(value, limit):
    """Preflight exact ASCII JSON size, then use the standard C encoder.

    Inputs are caller-owned JSON trees with string-keyed plain dictionaries,
    lists/tuples and standard scalar values. No application/custom serializer
    runs here. The final size check also catches growth between the two passes.
    """
    _prepare(value, limit, own=False)
    result = json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=True)
    if len(result) > limit:
        raise ValueError("Delayed alignment input exceeds byte limit")
    return result


def owned(value, limit):
    """Return an isolated plain JSON tree, preserving no mutable aliases."""
    return _prepare(value, limit, own=True)
