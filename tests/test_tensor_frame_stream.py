"""Per-frame IMAGE -> uint8 conversion: parity with the whole-batch formulas,
bounded memory, and the input never written.

The two oracles below are frozen copies of the formulas Save Video and Preview
Video used before conversion streamed (2026-11). Do not "fix" them to follow
the code: they are the bit-identity reference.
"""

import os
import sys
import tracemalloc

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server.color_correction as cc
import server.media_helpers as media_helpers

MODES = ("round", "truncate")


# --- Frozen oracles --------------------------------------------------------


def oracle_plain(batch, mode):
    arr = batch.detach().cpu().numpy() if hasattr(batch, "detach") else np.asarray(batch)
    scaled = np.asarray(arr, dtype=np.float32) * 255.0
    if mode == "round":
        scaled = np.rint(scaled)
    return scaled.clip(0, 255).astype(np.uint8)


def oracle_drift(batch, correction, mode):
    batch = batch.detach().cpu() if hasattr(batch, "detach") else np.asarray(batch)
    if isinstance(correction, np.ndarray):
        correction = np.asarray(correction, dtype=np.float64)
    out = np.empty(tuple(batch.shape), dtype=np.uint8)
    for i in range(batch.shape[0]):
        affine = np.asarray(cc._affine_for_frame(correction, i), dtype=np.float64)
        frame = np.ascontiguousarray(np.asarray(batch[i], dtype=np.float32) * 255.0)
        frame = cv2.transform(frame, affine)
        if mode == "round":
            frame = np.rint(frame)
        out[i] = frame.clip(0, 255).astype(np.uint8)
    return out


# --- Inputs ----------------------------------------------------------------

HEIGHT, WIDTH = 7, 5  # odd geometry on purpose


def edge_values() -> np.ndarray:
    """Float64 values around every uint8 boundary, both modes."""
    k = np.arange(256, dtype=np.float64)
    at = (k / 255.0).astype(np.float32)
    up = np.nextafter(at, np.float32(np.inf))
    down = np.nextafter(at, np.float32(-np.inf))
    half = ((k + 0.5) / 255.0).astype(np.float32)
    half_up = np.nextafter(half, np.float32(np.inf))
    half_down = np.nextafter(half, np.float32(-np.inf))
    # Float64 values on and beside float32 rounding midpoints: casting to
    # float32 first and scaling after must round exactly once.
    mid = (at.astype(np.float64) + up.astype(np.float64)) / 2.0
    tiny = np.spacing(mid)
    out_of_range = np.array([-1.0, -0.5, -1e-9, -0.0, 1.0 + 1e-7, 1.5, 2.0, 1e6, -1e6])
    return np.concatenate([
        at, up, down, half, half_up, half_down,
        mid, mid + tiny, mid - tiny, out_of_range,
    ]).astype(np.float64)


def edge_batch(dtype=np.float32) -> np.ndarray:
    values = edge_values()
    if dtype == np.float16:
        values = np.clip(values, -6e4, 6e4)  # stay finite; infinities have their own test
    per_frame = HEIGHT * WIDTH * 3
    frames = -(-values.size // per_frame)
    padded = np.resize(values, frames * per_frame)
    return padded.reshape(frames, HEIGHT, WIDTH, 3).astype(dtype)


def single_affine():
    return np.array([
        [1.02, 0.01, -0.005, 1.5],
        [0.0, 0.98, 0.02, -2.0],
        [0.01, -0.01, 1.01, 0.75],
    ], dtype=np.float64)


def blended_schedule(frame_count):
    tail = single_affine().copy()
    tail[:, 3] *= -1.0
    return {"head": single_affine(), "tail": tail, "ramp_start": 1, "ramp_end": max(2, frame_count - 2)}


def _torch():
    return pytest.importorskip("torch")


def input_variants():
    """(id, factory) pairs; factories build fresh inputs per test."""
    variants = [
        ("numpy_f32", lambda: edge_batch(np.float32)),
        ("numpy_f64", lambda: edge_batch(np.float64)),
        ("numpy_f16", lambda: edge_batch(np.float16)),
        ("numpy_noncontig", lambda: np.ascontiguousarray(
            np.concatenate([edge_batch(np.float32)] * 2, axis=2))[:, :, ::2, :]),
    ]

    def torch_of(dtype_name, *, grad=False, noncontig=False):
        def build():
            torch = _torch()
            source = edge_batch(np.float16 if dtype_name == "float16" else np.float64)
            base = torch.from_numpy(source).to(getattr(torch, dtype_name))
            if noncontig:
                base = torch.cat([base, base], dim=2)[:, :, ::2, :]
                assert not base.is_contiguous()
            if grad:
                base = base.clone().requires_grad_(True)
            return base
        return build

    variants += [
        ("torch_f32", torch_of("float32")),
        ("torch_f16", torch_of("float16")),
        ("torch_f64", torch_of("float64")),
        ("torch_requires_grad", torch_of("float32", grad=True)),
        ("torch_noncontig", torch_of("float32", noncontig=True)),
    ]
    return variants


VARIANTS = input_variants()
VARIANT_IDS = [name for name, _ in VARIANTS]


# --- Parity ----------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name,factory", VARIANTS, ids=VARIANT_IDS)
def test_plain_stream_and_array_match_whole_batch_oracle(name, factory, mode):
    batch = factory()
    expected = oracle_plain(batch, mode)
    streamed = np.stack(list(media_helpers.iter_tensor_to_uint8_frames(batch, mode=mode)))
    assert np.array_equal(streamed, expected)
    assert np.array_equal(media_helpers.tensor_to_uint8_frames(batch, mode=mode), expected)
    assert np.array_equal(cc.transform_tensor_to_uint8_frames(batch, None, mode=mode), expected)
    delegated = np.stack(list(cc.iter_transform_tensor_to_uint8_frames(batch, None, mode=mode)))
    assert np.array_equal(delegated, expected)


@pytest.mark.parametrize("schedule", ["single", "blended"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name,factory", VARIANTS, ids=VARIANT_IDS)
def test_drift_stream_and_array_match_per_frame_oracle(name, factory, mode, schedule):
    batch = factory()
    frame_count = int(batch.shape[0])
    correction = single_affine() if schedule == "single" else blended_schedule(frame_count)
    expected = oracle_drift(batch, correction, mode)
    streamed = np.stack(list(cc.iter_transform_tensor_to_uint8_frames(batch, correction, mode=mode)))
    assert np.array_equal(streamed, expected)
    assert np.array_equal(cc.transform_tensor_to_uint8_frames(batch, correction, mode=mode), expected)


def test_edge_values_cover_both_sides_of_every_boundary():
    # Guards the fixture itself: a refactor that loses the ulp neighbours
    # would turn the parity tests into a tautology.
    batch = edge_batch(np.float32)
    out = oracle_plain(batch, "truncate").reshape(-1)
    assert set(out.tolist()) == set(range(256))
    assert batch.shape[0] > 3


@pytest.mark.filterwarnings("ignore:invalid value encountered in cast:RuntimeWarning")
@pytest.mark.parametrize("mode", MODES)
def test_infinities_match_both_oracles(mode):
    batch = edge_batch(np.float32)
    batch[0, 0, 0, :] = np.inf
    batch[1, 0, 0, :] = -np.inf
    expected = oracle_plain(batch, mode)
    assert np.array_equal(media_helpers.tensor_to_uint8_frames(batch, mode=mode), expected)
    assert np.array_equal(np.stack(list(media_helpers.iter_tensor_to_uint8_frames(batch, mode=mode))), expected)
    affine = single_affine()
    assert np.array_equal(
        cc.transform_tensor_to_uint8_frames(batch, affine, mode=mode), oracle_drift(batch, affine, mode)
    )


@pytest.mark.filterwarnings("ignore:invalid value encountered in cast:RuntimeWarning")
@pytest.mark.parametrize("mode", MODES)
def test_nan_pixels_are_recorded_not_required_identical(mode, capsys):
    # NaN -> uint8 is implementation-defined; whole-batch and per-frame casts
    # may legitimately differ there. Finite pixels must still match.
    batch = edge_batch(np.float32)
    batch[0, 0, 0, :] = np.nan
    batch[2, 3, 1, 0] = np.nan
    nan_mask = np.isnan(batch)
    expected = oracle_plain(batch, mode)
    actual = media_helpers.tensor_to_uint8_frames(batch, mode=mode)
    assert np.array_equal(actual[~nan_mask], expected[~nan_mask])
    with capsys.disabled():
        print(f"\n[nan {mode}] per-frame identical to whole-batch: "
              f"{bool(np.array_equal(actual[nan_mask], expected[nan_mask]))}; "
              f"values per-frame {sorted(set(actual[nan_mask].tolist()))}, "
              f"whole-batch {sorted(set(expected[nan_mask].tolist()))}")


# --- Array function contract ----------------------------------------------


@pytest.mark.parametrize("shape", [(2, 3, 4, 1), (2, 3, 4, 4), (3, 4, 3), (4, 5), (0, 4, 5, 3), (6,), ()])
@pytest.mark.parametrize("mode", MODES)
def test_array_function_keeps_its_contract_for_other_shapes(shape, mode):
    rng = np.random.default_rng(3)
    batch = (rng.random(shape) * 1.4 - 0.2).astype(np.float32)
    expected = oracle_plain(batch, mode)
    actual = media_helpers.tensor_to_uint8_frames(batch, mode=mode)
    assert actual.dtype == np.uint8
    assert actual.shape == expected.shape == tuple(shape)
    assert np.array_equal(actual, expected)


def test_array_function_accepts_a_plain_numpy_batch_and_a_list():
    batch = edge_batch(np.float32)
    assert np.array_equal(media_helpers.tensor_to_uint8_frames(batch.tolist(), mode="round"),
                          oracle_plain(batch, "round"))


# --- Input untouched, no aliasing ------------------------------------------


@pytest.mark.parametrize("name,factory", VARIANTS, ids=VARIANT_IDS)
def test_input_is_byte_identical_after_iteration(name, factory):
    batch = factory()

    def snapshot():
        if hasattr(batch, "detach"):
            return batch.detach().clone()
        return batch.copy()

    before = snapshot()
    list(media_helpers.iter_tensor_to_uint8_frames(batch, mode="round"))
    list(cc.iter_transform_tensor_to_uint8_frames(batch, single_affine(), mode="round"))
    media_helpers.tensor_to_uint8_frames(batch, mode="truncate")
    if hasattr(batch, "detach"):
        torch = _torch()
        assert torch.equal(batch.detach(), before)
    else:
        assert batch.tobytes() == before.tobytes()


@pytest.mark.parametrize("stream", ["plain", "drift"])
def test_collected_frames_are_distinct_arrays(stream):
    batch = edge_batch(np.float32)
    if stream == "plain":
        frames = list(media_helpers.iter_tensor_to_uint8_frames(batch, mode="round"))
        expected = oracle_plain(batch, "round")
    else:
        frames = list(cc.iter_transform_tensor_to_uint8_frames(batch, single_affine(), mode="round"))
        expected = oracle_drift(batch, single_affine(), "round")
    assert len(frames) == batch.shape[0]
    for i, frame in enumerate(frames):
        assert frame.dtype == np.uint8 and frame.shape == batch.shape[1:]
        assert np.array_equal(frame, expected[i])
        for other in frames[i + 1:]:
            assert not np.shares_memory(frame, other)
        assert not np.shares_memory(frame, batch)


def test_drift_stream_is_correct_when_cv2_does_not_reuse_dst(monkeypatch):
    # Some bindings allocate a new array instead of writing into `dst`; the
    # stream must use what cv2.transform returns, not the scratch buffer.
    real_transform = cv2.transform

    def ignoring_dst(src, m, dst=None):
        return real_transform(src, m)

    monkeypatch.setattr(cc.cv2, "transform", ignoring_dst)
    batch = edge_batch(np.float32)
    frames = list(cc.iter_transform_tensor_to_uint8_frames(batch, single_affine(), mode="round"))
    assert np.array_equal(np.stack(frames), oracle_drift(batch, single_affine(), "round"))
    for i, frame in enumerate(frames):
        for other in frames[i + 1:]:
            assert not np.shares_memory(frame, other)


# --- Bounded memory --------------------------------------------------------


@pytest.mark.parametrize("stream", ["plain", "drift"])
def test_draining_the_stream_holds_a_few_frames_not_the_batch(stream):
    frames, height, width = 40, 64, 96
    batch = np.random.default_rng(5).random((frames, height, width, 3), dtype=np.float32)
    float_frame = height * width * 3 * 4
    if stream == "plain":
        iterator = media_helpers.iter_tensor_to_uint8_frames(batch, mode="round")
    else:
        iterator = cc.iter_transform_tensor_to_uint8_frames(batch, single_affine(), mode="round")
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        count = 0
        for frame in iterator:
            count += 1
            del frame
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == frames
    # A whole-batch float or uint8 copy would be >= 10 float frames here.
    assert peak < 4 * float_frame, f"peak {peak} bytes for {float_frame}-byte frames"


class _BatchGuard:
    """Duck-typed torch batch whose whole-batch conversions fail the test.

    Only `shape` and per-frame indexing are allowed; everything that would
    move, cast or copy the whole batch raises.
    """

    def __init__(self, tensor):
        self._tensor = tensor
        self.shape = tensor.shape
        self.indexed = 0

    def __getitem__(self, index):
        if not isinstance(index, int):
            raise AssertionError(f"batch indexed with {index!r}, not one frame")
        self.indexed += 1
        return self._tensor[index]

    def __len__(self):
        return len(self._tensor)

    def _whole_batch(self, *_args, **_kwargs):
        raise AssertionError("whole-batch conversion called on the IMAGE batch")

    detach = cpu = float = contiguous = numpy = to = clone = _whole_batch
    __array__ = _whole_batch
    __iter__ = _whole_batch


@pytest.mark.parametrize("stream", ["plain", "drift_single", "drift_blended"])
def test_torch_batch_is_only_ever_indexed_one_frame_at_a_time(stream):
    torch = _torch()
    tensor = torch.from_numpy(edge_batch(np.float32))
    guard = _BatchGuard(tensor)
    if stream == "plain":
        frames = list(media_helpers.iter_tensor_to_uint8_frames(guard, mode="round"))
        expected = oracle_plain(tensor, "round")
        array = media_helpers.tensor_to_uint8_frames(_BatchGuard(tensor), mode="round")
    else:
        correction = single_affine() if stream == "drift_single" else blended_schedule(tensor.shape[0])
        frames = list(cc.iter_transform_tensor_to_uint8_frames(guard, correction, mode="round"))
        expected = oracle_drift(tensor, correction, "round")
        array = cc.transform_tensor_to_uint8_frames(_BatchGuard(tensor), correction, mode="round")
    assert guard.indexed == tensor.shape[0]
    assert np.array_equal(np.stack(frames), expected)
    assert np.array_equal(array, expected)


# --- Geometry and errors ---------------------------------------------------


def test_image_batch_geometry_reads_good_batches():
    assert media_helpers.image_batch_geometry(np.zeros((4, 6, 8, 3), np.float32)) == (4, 6, 8)
    torch = pytest.importorskip("torch")
    assert media_helpers.image_batch_geometry(torch.zeros(2, 3, 5, 3)) == (2, 3, 5)


@pytest.mark.parametrize("shape", [(2, 4, 4, 4), (4, 4, 3), (0, 4, 4, 3), (2, 0, 4, 3), (2, 4, 0, 3), (3,)])
def test_image_batch_geometry_rejects_other_shapes_naming_the_shape(shape):
    with pytest.raises(ValueError) as excinfo:
        media_helpers.image_batch_geometry(np.zeros(shape, np.float32))
    message = str(excinfo.value)
    assert str(tuple(shape)) in message
    assert "frames_iter" not in message


def test_unknown_mode_raises_when_called_not_when_iterated():
    batch = edge_batch(np.float32)
    with pytest.raises(ValueError, match="Unknown tensor conversion mode"):
        media_helpers.iter_tensor_to_uint8_frames(batch, mode="banana")
    with pytest.raises(ValueError, match="Unknown tensor conversion mode"):
        cc.iter_transform_tensor_to_uint8_frames(batch, single_affine(), mode="banana")
    with pytest.raises(ValueError, match="Unknown tensor conversion mode"):
        cc.iter_transform_tensor_to_uint8_frames(batch, None, mode="banana")
    with pytest.raises(ValueError, match="Unknown tensor conversion mode"):
        media_helpers.tensor_to_uint8_frames(batch, mode="banana")


def test_drift_stream_rejects_a_non_rgb_batch_when_called():
    with pytest.raises(ValueError, match=r"\(N,H,W,3\)"):
        cc.iter_transform_tensor_to_uint8_frames(np.zeros((2, 4, 4, 4), np.float32), single_affine())
