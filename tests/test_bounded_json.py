"""Exact transport bytes and bounded ownership, without estimator dependencies."""

from enum import IntEnum
import json
import math
import random
import struct

import pytest

from vision_nav.estimation import bounded_json


def standard(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=True)


@pytest.mark.parametrize("value", [
    None, True, False, 0, -1, 10**250, -(10**250), -0.0, 1e-200, 1e200,
    float.fromhex("0x1.fffffffffffffp+1023"), float.fromhex("0x0.0000000000001p-1022"),
    "", "quote\" slash\\ /\b\f\n\r\t\x00\x1f", "\u00e9\u4e2d\U0001f680\ud800",
    "\u00e9\U0001f680\n"*5000, [], {}, (), [None, False, [], {}],
    {"z": [1, 2.0], "": ("\udfff", True), "a": {"b": -0.0}},
])
def test_exact_encoding_and_one_byte_short_limit(value):
    expected = standard(value)
    assert expected.isascii()
    assert bounded_json.dumps(value, len(expected)) == expected
    with pytest.raises(ValueError, match="byte limit"):
        bounded_json.dumps(value, len(expected)-1)
    with pytest.raises(ValueError, match="byte limit"):
        bounded_json.owned(value, len(expected)-1)
    assert standard(bounded_json.owned(value, len(expected))) == expected


def test_random_finite_floats_and_nested_values_match_standard_encoder():
    rng = random.Random(921)
    alphabet = ["a", "\n", "\x7f", "\u0080", "\ud800", "\U0010ffff", "\"", "\\"]
    for _ in range(200):
        numbers = [struct.unpack("!d", rng.getrandbits(64).to_bytes(8, "big"))[0] for _ in range(8)]
        numbers = [number for number in numbers if math.isfinite(number)]
        value = {"floats": numbers, "matrix": [numbers, list(reversed(numbers))],
                 "text": "".join(rng.choices(alphabet, k=32)),
                 "nested": [None, {"integer": rng.randrange(-10**80, 10**80)}]}
        expected = standard(value)
        assert bounded_json.dumps(value, len(expected)) == expected
        assert standard(bounded_json.owned(value, len(expected))) == expected
        with pytest.raises(ValueError, match="byte limit"):
            bounded_json.dumps(value, len(expected)-1)


def test_copy_is_plain_owned_json_and_aliases_are_separated():
    child = [1, {"nested": [2]}]
    source = {"first": child, "second": child, "tuple": (child,)}
    actual = bounded_json.owned(source, 1000)
    assert actual == json.loads(standard(source))
    assert actual["first"] is not actual["second"]
    assert actual["tuple"][0] is not actual["first"]
    assert list(actual) == sorted(actual)
    actual["first"][1]["nested"].append(3)
    assert child[1]["nested"] == [2] and actual["second"][1]["nested"] == [2]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_values_never_become_null_or_strings(value):
    with pytest.raises(ValueError, match="Nonfinite"):
        bounded_json.owned({"covariance": [[value]]}, 1000)


@pytest.mark.parametrize("limit", [0, -1, 1.5, True, None, "100"])
def test_invalid_limit(limit):
    with pytest.raises(ValueError, match="positive"):
        bounded_json.dumps({}, limit)


def test_unsupported_types_keys_cycles_and_excess_depth():
    for value in [{1: "integer key"}, {None: 1}, b"bytes", {1, 2}, object()]:
        with pytest.raises(TypeError):
            bounded_json.dumps(value, 1000)
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="Circular"):
        bounded_json.dumps(cycle, 1000)
    nested = None
    for _ in range(64):
        nested = [nested]
    assert bounded_json.dumps(nested, 1000) == standard(nested)
    with pytest.raises(ValueError, match="nesting"):
        bounded_json.dumps([nested], 1000)


def test_numeric_subclasses_use_underlying_values_without_user_hooks():
    class Code(IntEnum):
        VALUE = 7

    class Integer(int):
        def __int__(self):
            raise AssertionError("custom integer conversion")

        def bit_length(self):
            raise AssertionError("custom integer hook")

        def __repr__(self):
            raise AssertionError("custom integer repr")

    class Float(float):
        def __float__(self):
            raise AssertionError("custom float conversion")

        def __repr__(self):
            raise AssertionError("custom float repr")

    value = [Code.VALUE, Integer(12), Float(.3)]
    result = bounded_json.owned(value, 100)
    assert result == [7, 12, .3] and [type(x) for x in result] == [int, int, float]


def test_owned_copy_does_not_serialize_or_parse_the_numeric_tree(monkeypatch):
    value = [[.3, 2, None, True, -0.0]]
    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("Unnecessary owned decode"))
    monkeypatch.setattr(json, "dumps", lambda *a, **k: pytest.fail("Unnecessary full-tree encode"))
    actual = bounded_json.owned(value, 1000)
    assert actual == value and actual[0] is not value[0]


def test_large_inputs_reject_before_full_encoding(monkeypatch):
    encode = json.dumps
    calls = []

    def watched(value, **kwargs):
        calls.append(value)
        assert type(value) is str and len(value) <= 4096
        return encode(value, **kwargs)

    monkeypatch.setattr(bounded_json.json, "dumps", watched)
    for value in ["a"*100_000, [0]*100_000, 1 << 100_000, "\x00"*5000]:
        with pytest.raises(ValueError, match="byte limit"):
            bounded_json.dumps(value, 6000)
    assert len(calls) == 1 and len(calls[0]) == 4096


def test_final_guard_rejects_growth_after_preflight(monkeypatch):
    encode = json.dumps
    value = [0]

    def mutate(item, **kwargs):
        if item is value:
            item.append(1000)
        return encode(item, **kwargs)

    monkeypatch.setattr(bounded_json.json, "dumps", mutate)
    with pytest.raises(ValueError, match="byte limit"):
        bounded_json.dumps(value, 3)


def test_wire_encoding_does_not_decode_and_reencode(monkeypatch):
    from vision_nav.estimation.alignment_worker import _encode

    value = {"nested": [[1., .2]], "name": "\u00e9"}
    expected = standard(value).encode("ascii")
    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("Unnecessary wire decode"))
    assert _encode(value) == expected


@pytest.mark.parametrize("length", [16, 255, 256, 257, 1024])
def test_numeric_chunk_boundary_preserves_exact_bytes_and_ownership(length):
    values = [-0., 1e-300, 1e300, .3, float.fromhex("0x1.fffffffffffffp+1023")]
    row = [values[i % len(values)] for i in range(length)]
    source = {"a": row, "b": tuple(row)}
    expected = standard(source)
    copied = bounded_json.owned(source, len(expected))
    assert standard(copied) == bounded_json.dumps(source, len(expected)) == expected
    assert copied["a"] is not row and copied["a"] is not copied["b"]
    copied["a"][0] = 99.
    assert math.copysign(1, row[0]) == math.copysign(1, copied["b"][0]) == -1
    for fn in (bounded_json.dumps, bounded_json.owned):
        with pytest.raises(ValueError, match="byte limit"):
            fn(source, len(expected)-1)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_in_later_numeric_chunk_is_rejected(bad):
    row = [0.] * 513
    row[-1] = bad
    for fn in (bounded_json.dumps, bounded_json.owned):
        with pytest.raises(ValueError, match="Nonfinite"):
            fn(row, 10000)


def test_numeric_chunk_allocation_is_bounded_and_depth_is_not_bypassed(monkeypatch):
    encode = json.dumps
    lengths = []
    def checked(item, **kwargs):
        assert type(item) in (list, tuple) and len(item) <= 256
        result = encode(item, **kwargs)
        assert len(result) < 7*1024
        lengths.append(len(item))
        return result
    monkeypatch.setattr(bounded_json.json, "dumps", checked)
    bounded_json.owned([1e300]*1024, 16384)
    assert lengths == [256]*4
    nested = [0.]*16
    for _ in range(64):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting"):
        bounded_json.owned(nested, 10000)


def test_numeric_owned_copy_keeps_checked_chunks_not_later_source_values(monkeypatch):
    encode = json.dumps
    row = [0.] * 16
    def mutate_source(chunk, **kwargs):
        row[0] = float("nan")
        return encode(chunk, **kwargs)
    monkeypatch.setattr(bounded_json.json, "dumps", mutate_source)
    assert bounded_json.owned(row, 1000) == [0.] * 16
