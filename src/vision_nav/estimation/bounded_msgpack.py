"""Bounded JSON-shaped MessagePack for the private estimator socket only."""

import math


MAX_CONTAINER_ITEMS = 1_048_576
MAX_MAP_ITEMS = 16_384
MAX_TOTAL_ITEMS = 4_194_304
MAX_DEPTH = 64


def _module():
    try:
        import msgpack
    except ImportError as exc:
        raise RuntimeError("Compact estimator IPC requires msgpack; install the estimation extra") from exc
    return msgpack


def _measure(value, limit):
    """Preflight exact packed size without formatting covariance floats as text."""
    if type(limit) is not int or limit <= 0:
        raise ValueError("Expected a positive estimator packet byte limit")
    used, nodes = 0, 0
    ancestors = set()

    def consume(count):
        nonlocal used
        used += count
        if used > limit:
            raise ValueError("Estimator packet exceeds byte limit")

    def count_nodes(count):
        nonlocal nodes
        nodes += count
        if nodes > MAX_TOTAL_ITEMS:
            raise ValueError("Estimator packet exceeds item count limit")

    def visit(item, depth):
        if depth > MAX_DEPTH:
            raise ValueError("Estimator packet nesting exceeds 64 levels")
        count_nodes(1)
        kind = type(item)
        if item is None or kind is bool:
            consume(1)
        elif kind is int:
            if not -(1 << 63) <= item < (1 << 64):
                raise ValueError("Estimator packet integer exceeds MessagePack 64-bit range")
            if -32 <= item <= 127:
                consume(1)
            elif item >= 0:
                consume(2 if item < 256 else 3 if item < 65536 else 5 if item < 1 << 32 else 9)
            else:
                consume(2 if item >= -128 else 3 if item >= -32768 else 5 if item >= -(1 << 31) else 9)
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError("Nonfinite estimator packet value")
            consume(9)
        elif kind is str:
            if len(item) > limit-used:
                raise ValueError("Estimator packet exceeds byte limit")
            size = 0
            for offset in range(0, len(item), 4096):
                chunk_size = len(item[offset:offset+4096].encode("utf-8"))
                consume(chunk_size)
                size += chunk_size
            consume(1 if size < 32 else 2 if size < 256 else 3 if size < 65536 else 5)
        elif kind in (dict, list, tuple):
            size = len(item)
            if size > (MAX_MAP_ITEMS if kind is dict else MAX_CONTAINER_ITEMS):
                raise ValueError("Estimator packet container exceeds item limit")
            identity = id(item)
            if identity in ancestors:
                raise ValueError("Circular estimator packet")
            ancestors.add(identity)
            consume(1 if size < 16 else 3 if size < 65536 else 5)
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise TypeError("Estimator packet keys must be strings")
                    visit(key, depth+1)
                    visit(child, depth+1)
            elif size >= 16 and all(type(child) is float for child in item):
                if depth == MAX_DEPTH:
                    raise ValueError("Estimator packet nesting exceeds 64 levels")
                count_nodes(size)
                if not all(math.isfinite(child) for child in item):
                    raise ValueError("Nonfinite estimator packet value")
                consume(9*size)
            else:
                for child in item:
                    visit(child, depth+1)
            ancestors.remove(identity)
        else:
            raise TypeError("Unsupported estimator packet type: "+kind.__name__)

    visit(value, 0)
    return used


def pack(value, limit):
    size = _measure(value, limit)
    # The preflight admits only builtin JSON-shaped values. No object serializer
    # or extension hook runs; tuples retain the former JSON-to-list semantics.
    encoded = _module().packb(value, use_single_float=False, use_bin_type=True)
    if len(encoded) != size or len(encoded) > limit:
        raise ValueError("Estimator packet changed during encoding or exceeds limit")
    return encoded


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("Estimator packet contains a non-string or duplicate map key")
        result[key] = value
    return result


def _extension(code, data):
    raise ValueError("Estimator packet extension types are forbidden")


def _preflight_packet(data):
    """Check layout budgets before the library allocates decoded containers.

    This checks headers and float exponent bits, without constructing values.
    MessagePack decodes values/UTF-8 and _pairs checks string/duplicate keys.
    """
    offset, nodes = 0, 0
    pending = [1]

    def length(width):
        nonlocal offset
        end = offset+width
        if end > len(data):
            raise ValueError("Truncated estimator packet header")
        result = int.from_bytes(data[offset:end], "big")
        offset = end
        return result

    while pending:
        if pending[-1] == 0:
            pending.pop()
            continue
        if len(pending)-1 > MAX_DEPTH:
            raise ValueError("Estimator packet nesting exceeds 64 levels")
        if offset >= len(data):
            raise ValueError("Truncated estimator packet")
        pending[-1] -= 1
        tag = data[offset]
        offset += 1
        nodes += 1
        children, is_map = None, False
        if tag <= 0x7f or tag >= 0xe0 or tag in (0xc0, 0xc2, 0xc3):
            pass
        elif tag == 0xcb:
            if offset+8 > len(data):
                raise ValueError("Truncated estimator packet value")
            if data[offset] & 0x7f == 0x7f and data[offset+1] >= 0xf0:
                raise ValueError("Nonfinite estimator packet value")
            offset += 8
        elif 0xcc <= tag <= 0xd3:
            offset += (1, 2, 4, 8)[(tag-0xcc) % 4]
        elif 0xa0 <= tag <= 0xbf:
            offset += tag-0xa0
        elif tag in (0xd9, 0xda, 0xdb):
            size = length({0xd9: 1, 0xda: 2, 0xdb: 4}[tag])
            offset += size
        elif 0x90 <= tag <= 0x9f or tag in (0xdc, 0xdd):
            children = tag-0x90 if tag <= 0x9f else length(2 if tag == 0xdc else 4)
            if children > MAX_CONTAINER_ITEMS:
                raise ValueError("Estimator packet container exceeds item limit")
        elif 0x80 <= tag <= 0x8f or tag in (0xde, 0xdf):
            count = tag-0x80 if tag <= 0x8f else length(2 if tag == 0xde else 4)
            if count > MAX_MAP_ITEMS:
                raise ValueError("Estimator packet container exceeds item limit")
            children, is_map = 2*count, True
        else:
            raise ValueError("Unsupported estimator packet format tag")
        if offset > len(data):
            raise ValueError("Truncated estimator packet value")
        if children:
            if len(pending) > MAX_DEPTH:
                raise ValueError("Estimator packet nesting exceeds 64 levels")
            # Each pending value needs at least one tag byte. Reject impossible
            # declarations before entering the allocating MessagePack decoder.
            if children+sum(pending) > len(data)-offset:
                raise ValueError("Truncated estimator packet container")
            if not is_map and children >= 16 and offset+9*children <= len(data) and (
                    data[offset:offset+9*children:9] == b"\xcb"*children):
                first = data[offset+1:offset+9*children:9]
                second = data[offset+2:offset+9*children:9]
                if any(a & 0x7f == 0x7f and b >= 0xf0 for a, b in zip(first, second)):
                    raise ValueError("Nonfinite estimator packet value")
                nodes += children
                offset += 9*children
            else:
                pending.append(children)
        if nodes+sum(pending) > MAX_TOTAL_ITEMS:
            raise ValueError("Estimator packet exceeds item count limit")
    if offset != len(data):
        raise ValueError("Trailing estimator packet data")


def unpack(encoded, limit):
    if type(encoded) is not bytes:
        raise TypeError("Expected an owned bytes estimator packet")
    if type(limit) is not int or limit <= 0 or not 0 < len(encoded) <= limit:
        raise ValueError("Estimator packet exceeds byte limit or is empty")
    _preflight_packet(encoded)
    result = _module().unpackb(encoded, raw=False, use_list=True, strict_map_key=True,
        object_pairs_hook=_pairs, ext_hook=_extension, max_str_len=limit,
        max_bin_len=0, max_ext_len=0, max_array_len=MAX_CONTAINER_ITEMS, max_map_len=MAX_MAP_ITEMS)
    return result
