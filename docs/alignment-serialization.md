# Bounded Alignment Serialization

## Immutable Canonical Fields

The coordinator now retains canonical JSON bytes alongside its single owned,
validated native snapshot. A prepared draft references those immutable bytes
and the unchanged configuration, encoding its exact ordered fix set separately.
The unbound request hash and noise-bound request hash are composed from these
fields using the **same** sorted-key, compact-separator, ASCII-escaped JSON
convention as `correlated_window.payload_digest`. This is not a new digest
format, a weaker identity check, or reuse of a previous uncertainty model.

`estimation/canonical_identity.py` is private composition of already-encoded
values from the standard JSON encoder. It must not receive arbitrary raw JSON.
Each new snapshot gets new bytes and identity; each new noise response is
encoded afresh. An in-flight draft retains its original snapshot bytes even
when sensor ingestion advances. The 2 MiB input limit bounds each retained
snapshot encoding, not total process RSS. Config/draft/fix bounds still apply;
there is no historical snapshot cache. Epoch invalidation discards the bytes.

Public drafts remain JSON-shaped isolated copies and expose no encoded fields.
External callbacks still receive copied requests. Combined responses must
match both the complete original request hash and the exact noise-bound result
hash. Direct solver callbacks retain full post-call mutation checks instead of
trusting cached fragments. Tests cover Unicode/escaping, float extremes and
signed zero, empty/nested fields, noise insertion, callback mutation, snapshot
replacement during solving and epoch invalidation.

**Latest transport:** The private estimator socket defaults to `msgpack_v1`.
The JSON ownership and canonical identity helpers below are unchanged, and
`json_v1` remains an explicit comparison option. See
[compact worker transport](#compact-worker-transport) for its bounds and evidence.

The research alignment transport now avoids an unnecessary encode/decode/encode
round trip. This is a CPU/allocation improvement to existing evidence ownership
and process messages, not a new estimator, qualified latency budget or aircraft
publication path.

## Contract

`estimation/bounded_json.py` provides two internal helpers:

- `dumps(value, limit)` checks the exact encoded ASCII JSON size before invoking
  the standard C encoder. It retains sorted keys, default JSON separators,
  finite numbers and ASCII escaping, so supported payload bytes and hashes
  agree with the former transport.
- `owned(value, limit)` performs the same size/type checks while constructing
  a separate plain JSON tree. It does not serialize and parse the complete tree.
  Tuples become lists, numeric subclasses become builtin scalars, dictionary
  order remains sorted and repeated mutable references become separate copies.

Both reject invalid limits, nonfinite values, cycles, unsupported types,
non-string dictionary keys and nesting deeper than 64 levels. Plain dictionaries,
lists/tuples and JSON scalars are the intended inputs. The stricter key/container
contract is intentional; custom containers and application serializers are not
supported. Integer/float normalization does not execute overridden conversion or
representation methods. Inputs must remain caller-owned during the operation;
this is not synchronization for concurrently mutated objects.

String preflight escapes chunks of at most 4096 characters to avoid creating one
unbounded escaped temporary for a rejected string. Numeric and container budgets
are checked before completing the encoded message. The final encoder result has
a second length check. Limits are encoded bytes, not a total process RSS guarantee.
The direct-copy path shares only immutable scalar values, never mutable containers.

Long rows of builtin floats now use small C-encoded chunks to count exact JSON
bytes. Each chunk contains at most 256 values and its encoded temporary is below
7 KiB. Checked chunks supply the owned result; the code does not reread the source
row after validation. Nonfinite numbers, depth limits, one-byte-overflow checks
and exact canonical bytes remain enforced. Mixed rows and numeric subclasses
retain the scalar path. This adds no NumPy or third-party serialization dependency.

The coordinator also avoids a second public-export copy when its own refresh
prepares a private draft. Public `prepare()` results remain separate owned trees;
provider callbacks still receive copies, and native-digest mutation checks remain.

`delayed_alignment._owned` remains an alias for existing callers. The worker's
`_encode` now obtains checked bytes directly. Operation deadlines, source hashes,
request identities, covariance semantics, noise requirements, retained-window
rules and PX4 publication restrictions are unchanged.

## Reproduce The Measurement

Run in the repository's Python environment with an existing successful alignment
trace. The script reads local evidence only and does not start models or sensors.

```bash
python scripts/research/benchmark_alignment_json.py \
  --trace /absolute/path/to/alignment-trace.jsonl \
  --output research/outputs/alignment-json/report.json \
  --iterations 30
```

The output must be new. The benchmark hashes the complete trace, selects the
first successful event and its final numerical replay input, and measures both
that request and the complete event. It compares against a frozen copy of the
former incremental serialization algorithm. Exact byte equality is mandatory.
Three warmups precede paired samples with rotating operation order. Python
allocation peaks are measured separately with `tracemalloc`, outside timed runs;
they are not process RSS or native allocator measurements. No flaky timing
threshold is used as a unit-test acceptance gate.

## September 5 Local Results

Source: the existing actual-OpenVINS replay test with synthetic PnP observations
and explicitly synthetic noise, not an X500 flight. The input is a one-fix case;
it does not establish scaling at maximum retained-fix count.

| Payload | Operation | Former median | New median | Reduction |
| --- | --- | --- | --- | --- |
| 218,692-byte request | Owned copy | 11.294 ms | 8.550 ms | 24.3% |
| 218,692-byte request | Message encoding | 14.620 ms | 11.314 ms | 22.6% |
| 306,396-byte event | Owned copy | 17.009 ms | 12.080 ms | 29.0% |
| 306,396-byte event | Message encoding | 21.385 ms | 15.741 ms | 26.4% |

Owned-copy peak Python allocations decreased from approximately 2.72 MB to
0.25 MB for the request and 3.82 MB to 0.36 MB for the event. Both cases retained
identical encoded bytes. The report includes individual timing samples, source
digests, payload hashes and host/Python architecture:
`research/outputs/alignment-json-final-20260905-r1/report.json` (Git-ignored).

These are serialization-only measurements on a development Mac. They do not
prove equivalent end-to-end speedup, live 500 ms freshness, sustained Pi capacity,
physical noise correctness or propagation after map-anchor marginalization.
Those remain separate gates in [continuous alignment](continuous-research-alignment.md).

## Checks

```bash
python -m pytest -q tests/test_bounded_json.py tests/test_alignment_json_benchmark.py
```

Coverage includes exact limits, one-byte overflow, Unicode/control/surrogate
escaping, randomized finite floats, nested values, mutable alias isolation,
numeric subclasses, invalid keys/types, depth/cycle rejection, oversized input,
final-size protection and no redundant parsing in the worker path. Existing
process and estimator regression suites additionally check request integrity,
direct-versus-isolated numerical replay, stale/expired evidence and teardown.

The September 5 focused run passed 309 tests (including the 43 serializer and
benchmark checks) in the Python 3.11 estimator environment with the actual
native OpenVINS binary. Evidence is under
`research/outputs/alignment-json-estimator-regression-20260905-r1/`.
The native integration again advanced from 50 ms to 150 ms map-fix age without
another fix. Its individual background operation times were about 311 ms and
317 ms, followed by a 173 ms final drain. These are not controlled before/after
end-to-end timings and still use the permissive 10 s replay receipt-age policy.
They must not be presented as proof of live-camera cadence or a Pi benchmark.

The broader Python 3.13 ONNX environment run passed 133 learned/native/map tests
using the actual MegaLoc and XFeat/LighterGlue exports; three optional direct
checkpoint tests were skipped because `VISION_NAV_LOCAL_REFERENCE_CONFIG` was
unset. Outputs are under `research/outputs/alignment-json-model-regression-20260905-r1/`.
Existing ONNX/geospatial deprecation warnings remain. All 104 main project
checks and `git diff --check` also passed. Desktop/Rust tests were not rerun;
this pass did not change desktop code. No hardware or cloud resources were used.

## Compact Worker Transport

`estimation/bounded_msgpack.py` uses the MessagePack library for private process
messages, with a checked subset of JSON-shaped values. It is not a new disk,
map, dataset, telemetry or model-release format. Saved replay JSON and canonical
JSON digests remain unchanged. See the official
[MessagePack Python API](https://msgpack-python.readthedocs.io/en/latest/api.html)
for float precision and unpacker limits, and the
[format specification](https://github.com/msgpack/msgpack/blob/master/spec.md)
for layout tags.

- The default is `msgpack_v1`; `ProcessAlignmentEngine(wire_format="json_v1")`
  retains the original wire codec for controlled comparisons. There is no
  automatic fallback or mixed-codec negotiation. Startup confirms the selected
  format before sensor opening.
- Install `.[estimation]`, which now includes `msgpack>=1.1,<2`. The observed
  local test version is 1.2.2. A missing dependency fails before worker creation;
  the runtime does not install packages automatically.
- Floats use 64-bit encoding. Tests preserve binary float bits, negative zero,
  subnormals, maximal finite values and canonical JSON digests after decoding.
  Byte strings, extension/timestamp objects, custom types, non-string/duplicate
  map keys, nonfinite values, cycles and malformed/trailing data are rejected.
- Limits are 40 MiB per encoded frame, depth 64, 1,048,576 array entries,
  16,384 entries per map and 4,194,304 total tree nodes. Exact packed size is
  checked before encoding, then checked again afterwards. A non-allocating
  layout guard checks nesting, node counts, declared lengths, allowed format
  tags and finite float exponent bits before the library builds containers.
  The library decodes values/UTF-8 with additional container limits; the map
  hook rejects invalid/duplicate keys. These are not total process RSS caps.
- Integers are limited to signed 64-bit negatives / unsigned 64-bit positives.
  Text must be valid UTF-8; lone surrogates and numeric subclasses are not part
  of this private codec's input domain. The existing broader JSON reference
  behavior remains available only through explicit `json_v1` selection.
- Caller-owned inputs must not be mutated during encoding. The codec is not a
  synchronization primitive. Decoding creates new containers; it never loads
  executable Python objects or imports an application serializer.
- Socket framing, single in-flight operation, source/epoch/request binding,
  deadlines, kill/reap behavior, covariance and publication gates are retained.
  A malformed compact response terminates the worker instead of falling back.
  The private envelope hash covers the selected encoded packet bytes; only
  canonical map/navigation/replay identities remain identical across codecs.

The finite learned session accepts `--alignment-wire-format msgpack_v1` or
`json_v1`. The benchmark tool accepts `--wire-format` with the same values:

```bash
python scripts/research/benchmark_joint_transaction.py \
  --request /absolute/path/to/joint-benchmark-request.json \
  --provider /absolute/path/to/reviewed_noise_provider.py \
  --wire-format msgpack_v1 --iterations 20 \
  --output research/outputs/compact-transaction/report.json
```

This opt-in research command does not connect to a sensor or aircraft. The
provider must remain explicit; a synthetic fixture is not physical calibration.
MessagePack can increase the size of zero-heavy float matrices compared with
short JSON literals, even while reducing decimal encoding work. Do not infer
uniform bandwidth or memory savings from the format name.

### Live Observation, Not Qualification

The first paired native test (`compact-alignment-20260905-r1`) measured median
parent verification/encoding of 8.407 ms with JSON and 2.678 ms with MessagePack;
median worker round trips were 40.372 and 33.963 ms. The tests used separate
finite synthetic-map streams on the same Mac, not identical physical trajectories
or isolated-system benchmarks. The compact run produced one advancing current
update; JSON produced zero. This was not sustained behavior.

A later paired run (`compact-alignment-20260905-r2`) and three explicit compact
reruns (`compact-live-repeat-20260905-r1`, `-r2`, `-r3`) all recorded zero
advancing compact-path updates. Each offered 20 more frames at 20 Hz, retained
the 500 ms receipt-age gate, and preserved original pose timestamps. Outputs
are in ignored `research/outputs/`; symlink aliases are not additional trials.
The initial positive result therefore does **not** close the current-state gate.

The next estimator architecture needs to separate immutable accepted map evidence
from repeated current-state updates, avoid returning full replay requests on
every hot-path response, and retain enough snapshot-specific joint information
for correlated forward propagation. That is remaining work, not implemented by
this codec. It must not infer independence, reuse a previous covariance for a
new pose, or restamp an old solution. No Pi or aircraft qualification is claimed.
