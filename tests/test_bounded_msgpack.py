"""Compact estimator packets preserve values, not arbitrary Python objects."""

import json
import math
import random
import struct

import msgpack
import pytest

from vision_nav.estimation.bounded_msgpack import MAX_CONTAINER_ITEMS, MAX_MAP_ITEMS, pack, unpack
from vision_nav.estimation.correlated_window import payload_digest


@pytest.mark.parametrize("value", [None, True, False, -2**63, -2**31-1, -32769, -129, -33, -32,
    -1, 0, 127, 128, 255, 256, 65535, 65536, 2**32, 2**64-1, -0., 1.2345678901234567,
    "", "x"*31, "x"*32, "x"*255, "x"*256, "x"*65535, "x"*65536,
    "\u03b1\U0001f680\n\u0000", [], {}, tuple(range(16)), {"a": 1, "nested": [[.2]*32]*4}])
def test_exact_limit_and_json_value_identity(value):
    expected = msgpack.packb(value, use_single_float=False, use_bin_type=True)
    assert pack(value, len(expected)) == expected
    decoded = unpack(expected, len(expected))
    assert decoded == json.loads(json.dumps(value))
    assert payload_digest(decoded) == payload_digest(value)
    with pytest.raises(ValueError):
        pack(value, len(expected)-1)
    with pytest.raises(ValueError):
        unpack(expected, len(expected)-1)


def test_covariance_float64_bits_and_signed_zero_are_preserved():
    rng = random.Random(715)
    values = [0., -0., float.fromhex("0x0.0000000000001p-1022"), float.fromhex("0x1.fffffffffffffp+1023")]
    while len(values) < 4096:
        value = struct.unpack("!d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(value)
    packet = {"covariance": [values[i:i+64] for i in range(0, len(values), 64)]}
    decoded = unpack(pack(packet, 100_000), 100_000)
    assert payload_digest(decoded) == payload_digest(packet)
    assert struct.pack("!4096d", *values) == struct.pack("!4096d", *(v for row in decoded["covariance"] for v in row))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), b"", b"x", bytearray(b"x"),
    {1: "key"}, {b"a": 1}, {"set": {1, 2}}, object(), 2**64, -2**63-1])
def test_unsupported_values_fail_before_encoding(value):
    with pytest.raises((TypeError, ValueError)):
        pack(value, 4096)


def test_aliases_are_copied_and_cycles_are_rejected():
    row = [.125]*20
    decoded = unpack(pack([row, row], 4096), 4096)
    decoded[0][0] = 42.
    assert row[0] == decoded[1][0] == .125
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="Circular"):
        pack(cycle, 4096)


def test_depth_and_container_limits():
    value = 0
    for _ in range(64):
        value = [value]
    assert unpack(pack(value, 4096), 4096) == value
    with pytest.raises(ValueError, match="nesting"):
        pack([value], 4096)
    with pytest.raises(ValueError, match="nesting"):
        unpack(msgpack.packb([value]), 4096)
    for header in (b"\xdd"+struct.pack("!I", MAX_CONTAINER_ITEMS+1), b"\xdf"+struct.pack("!I", MAX_MAP_ITEMS+1)):
        with pytest.raises(ValueError):
            unpack(header, 4096)


@pytest.mark.parametrize("raw", [
    msgpack.packb(float("nan")), msgpack.packb(float("inf")), msgpack.packb(b""),
    msgpack.packb(b"bytes"), msgpack.packb(msgpack.ExtType(1, b"x")),
    msgpack.packb(msgpack.Timestamp(1)), b"\x82\xa1a\x01\xa1a\x02",
    b"\x81\x01\x02", b"\xa1\xff", b"\x91", b"\xc1", b"\x00\x00", b"",
    msgpack.packb(1., use_single_float=True),
])
def test_decoder_rejects_non_json_data_duplicates_and_malformed_frames(raw):
    with pytest.raises((ValueError, TypeError)):
        unpack(raw, 4096)


@pytest.mark.parametrize("limit", [True, 0, -1, .5, None])
def test_limit_policy_is_explicit(limit):
    with pytest.raises(ValueError):
        pack({}, limit)
    with pytest.raises(ValueError):
        unpack(b"\x80", limit)


def test_rejected_packet_never_reaches_encoder(monkeypatch):
    import vision_nav.estimation.bounded_msgpack as codec

    def forbidden():
        raise AssertionError("Oversized packet reached codec allocation")

    monkeypatch.setattr(codec, "_module", forbidden)
    with pytest.raises(ValueError, match="byte limit"):
        pack("x"*4096, 128)
    with pytest.raises(ValueError, match="container"):
        pack([0]*(MAX_CONTAINER_ITEMS+1), 40*1024*1024)


def test_total_item_budget_covers_flat_and_nested_numeric_data(monkeypatch):
    import vision_nav.estimation.bounded_msgpack as codec
    monkeypatch.setattr(codec, "MAX_TOTAL_ITEMS", 16)
    for value in ([0]*16, [.5]*16, [[0]*4]*4):
        with pytest.raises(ValueError, match="item count"):
            pack(value, 4096)
        with pytest.raises(ValueError, match="item count"):
            unpack(msgpack.packb(value), 4096)


def test_declared_nesting_and_node_bombs_never_reach_allocating_decoder(monkeypatch):
    import vision_nav.estimation.bounded_msgpack as codec

    def forbidden():
        raise AssertionError("Invalid layout reached MessagePack allocation")

    nested = b"\x91"*65+b"\x00"
    oversized = b"\xdd"+struct.pack("!I", MAX_CONTAINER_ITEMS+1)
    truncated = (b"\xdd"+struct.pack("!I", MAX_CONTAINER_ITEMS))*10
    excessive_nodes = msgpack.packb([[0]*8]*8)
    monkeypatch.setattr(codec, "_module", forbidden)
    for raw in (nested, oversized, truncated):
        with pytest.raises(ValueError):
            unpack(raw, 4096)
    monkeypatch.setattr(codec, "MAX_TOTAL_ITEMS", 32)
    with pytest.raises(ValueError, match="item count"):
        unpack(excessive_nodes, 4096)


def test_layout_guard_matches_library_on_random_nested_allowed_trees():
    rng = random.Random(3219)

    def tree(depth=0):
        kind = rng.randrange(6 if depth < 4 else 4)
        if kind == 0:
            return None
        if kind == 1:
            return rng.randrange(-2**63, 2**64)
        if kind == 2:
            return rng.uniform(-1e90, 1e90)
        if kind == 3:
            return "a\u03b1\n"*rng.randrange(40)
        if kind == 4:
            return [tree(depth+1) for _ in range(rng.randrange(20))]
        return {str(i): tree(depth+1) for i in range(rng.randrange(20))}

    for _ in range(100):
        value = tree()
        expected = msgpack.packb(value, use_single_float=False, use_bin_type=True)
        actual = pack(value, len(expected))
        assert actual == expected
        assert payload_digest(unpack(actual, len(expected))) == payload_digest(value)


@pytest.mark.parametrize("index", [0, 15, 31])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_float_row_preflight_rejects_all_nonfinite_exponents(index, bad):
    values = [.125]*32
    values[index] = bad
    with pytest.raises(ValueError, match="Nonfinite"):
        unpack(msgpack.packb({"row": values}, use_single_float=False), 4096)


@pytest.mark.parametrize("value", ["x", bytearray(b"\x80"), memoryview(b"\x80"), object()])
def test_decoder_requires_an_owned_byte_frame(value):
    with pytest.raises(TypeError, match="owned bytes"):
        unpack(value, 4096)
