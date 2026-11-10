"""Save Video and Preview Video convert frames one at a time into the encoder.

Covers the hand-off (a stream plus its count), the poster, early shape
failure, real ffmpeg with audio through the stream, and a producer failure
partway through the encode.
"""

import importlib
import math
from pathlib import Path

import numpy as np
import pytest

from test_editor_node import TEST_PACKAGE, _import_io_nodes
from test_tensor_frame_stream import oracle_drift, oracle_plain, single_affine
from diagnostics.test_video_fidelity import _count_video_frames, _decoded_audio_seconds, _require_ffmpeg

FPS = 24.0
DRIFT_RECORD = {
    "enabled": True, "applied": True, "mode": "global", "frames_used": [0],
    "residual_before": {"mae": 1.0}, "residual_after": {"mae": 0.5},
}


def _batch(frame_count=5, height=6, width=10, seed=11):
    torch = importlib.import_module("torch")
    values = np.random.default_rng(seed).random((frame_count, height, width, 3)) * 1.3 - 0.15
    return torch.from_numpy(values.astype(np.float32))


def _env(tmp_path, monkeypatch, *, scene=False):
    io = _import_io_nodes(tmp_path, monkeypatch)
    state = importlib.import_module(f"{TEST_PACKAGE}.server.timeline_state")
    thumbs = importlib.import_module(f"{TEST_PACKAGE}.server.thumbnail_service")
    monkeypatch.setattr(thumbs, "ensure_thumbnail", lambda *a, **k: None)
    monkeypatch.setattr(io, "_build_take_proxy", lambda *a: None)
    saves = []
    monkeypatch.setattr(io, "save_project", lambda project: saves.append(project))
    posters = []
    real_thumbnail = io._save_preview_thumbnail

    def capture_poster(frame_bgr, prefix="sonder_thumb"):
        import cv2
        posters.append(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        return real_thumbnail(frame_bgr, prefix)

    monkeypatch.setattr(io, "_save_preview_thumbnail", capture_poster)
    (tmp_path / "media").mkdir(exist_ok=True)
    scenes = [state.Scene(scene_id="s", name="S", duration_frames=96)] if scene else []
    project = state.TimelineProject(project_dir=str(tmp_path), scenes=scenes)
    return io, project, saves, posters


def _recording_encoder(io, seen):
    def fake(frames_iter, *, preset_id, output_path, custom_options=None, expected_frame_count=None, **kwargs):
        seen["is_array"] = isinstance(frames_iter, np.ndarray)
        seen["frames"] = [np.array(frame, copy=True) for frame in frames_iter]
        seen["expected_frame_count"] = expected_frame_count
        seen["timeout"] = kwargs.get("timeout")
        Path(output_path).write_bytes(b"video")
        return io.metadata_for_save_preset(preset_id, custom_options)
    return fake


@pytest.mark.parametrize("drift", [False, True], ids=["plain", "drift"])
def test_save_video_streams_frames_and_its_poster_is_encoded_frame_zero(tmp_path, monkeypatch, drift):
    io, project, _saves, posters = _env(tmp_path, monkeypatch)
    frames = _batch()
    if drift:
        monkeypatch.setattr(io, "fit_drift_correction",
                            lambda *_a, **_k: (single_affine(), dict(DRIFT_RECORD)))
    seen = {}
    monkeypatch.setattr(io, "encode_video", _recording_encoder(io, seen))

    io.SonderSaveVideo().save_video(project, frames, embed_metadata=False, color_drift_correction=drift)

    mode = io.tensor_mode_for_preset(io.DEFAULT_SAVE_VIDEO_PRESET)
    expected = oracle_drift(frames, single_affine(), mode) if drift else oracle_plain(frames, mode)
    assert seen["is_array"] is False
    assert seen["expected_frame_count"] == 5
    assert np.array_equal(np.stack(seen["frames"]), expected)
    assert np.array_equal(posters[-1], seen["frames"][0])
    asset = project.assets[0]
    assert (asset.frame_count, asset.width, asset.height) == (5, 10, 6)
    # Streamed conversion, drift included, stays under 10% of the tightest
    # encode budget (measured 2026-11-10), so the timeout formula is unchanged.
    assert seen["timeout"] == io.save_video_encode_timeout_seconds(io.DEFAULT_SAVE_VIDEO_PRESET, 5, 10, 6)


def test_preview_video_streams_frames_and_its_poster_is_encoded_frame_zero(tmp_path, monkeypatch):
    io, _project, _saves, posters = _env(tmp_path, monkeypatch)
    frames = _batch(frame_count=4)
    seen = {}
    monkeypatch.setattr(io, "encode_video", _recording_encoder(io, seen))

    io.SonderPreviewVideo().preview(frames, fps=FPS)

    expected = oracle_plain(frames, io.tensor_mode_for_preset(io.DEFAULT_SAVE_VIDEO_PRESET))
    assert seen["is_array"] is False
    assert seen["expected_frame_count"] == 4
    assert np.array_equal(np.stack(seen["frames"]), expected)
    assert np.array_equal(posters[-1], seen["frames"][0])


@pytest.mark.parametrize("drift", [False, True], ids=["plain", "drift"])
def test_png_sequence_writes_every_frame_equal_to_the_oracle(tmp_path, monkeypatch, drift):
    from PIL import Image

    io, project, _saves, posters = _env(tmp_path, monkeypatch)
    frames = _batch(frame_count=4)
    if drift:
        monkeypatch.setattr(io, "fit_drift_correction",
                            lambda *_a, **_k: (single_affine(), dict(DRIFT_RECORD)))
    monkeypatch.setattr(io, "encode_video", lambda *a, **k: pytest.fail("PNG sequence must not encode"))
    custom = {"custom_output_kind": io.CUSTOM_OUTPUT_KIND_PNG_SEQUENCE}

    io.SonderSaveVideo().save_video(
        project, frames, save_preset=io.CUSTOM_SAVE_VIDEO_PRESET, embed_metadata=False,
        color_drift_correction=drift, **custom,
    )

    mode = io.resolve_custom_export_options(custom)["tensor_mode"]
    expected = oracle_drift(frames, single_affine(), mode) if drift else oracle_plain(frames, mode)
    assert len(project.assets) == 4
    written = []
    for asset in project.assets:
        with Image.open(Path(project.project_dir) / asset.path) as image:
            written.append(np.asarray(image.convert("RGB")))
    assert np.array_equal(np.stack(written), expected)
    assert np.array_equal(posters[-1], expected[0])


@pytest.mark.parametrize("shape", [(0, 4, 4, 3), (2, 4, 4, 4), (4, 4, 3)])
def test_bad_batch_fails_before_any_audio_or_output_file(tmp_path, monkeypatch, shape):
    io, project, saves, _posters = _env(tmp_path, monkeypatch)
    torch = importlib.import_module("torch")
    monkeypatch.setattr(io, "encode_video", lambda *a, **k: pytest.fail("encode must not run"))
    audio = {"waveform": torch.zeros(1, 2, 4800), "sample_rate": 48000}

    with pytest.raises(ValueError, match=re_escape(str(shape))):
        io.SonderSaveVideo().save_video(project, torch.zeros(shape), audio=audio, embed_metadata=False)
    with pytest.raises(ValueError, match=re_escape(str(shape))):
        io.SonderPreviewVideo().preview(torch.zeros(shape), audio=audio)

    assert not list((tmp_path / "temp").glob("*.wav"))
    assert not list((tmp_path / "media").iterdir())
    assert project.assets == [] and saves == []


def test_png_sequence_with_an_empty_batch_writes_nothing(tmp_path, monkeypatch):
    io, project, saves, _posters = _env(tmp_path, monkeypatch)
    torch = importlib.import_module("torch")
    with pytest.raises(ValueError, match=re_escape("(0, 4, 4, 3)")):
        io.SonderSaveVideo().save_video(
            project, torch.zeros(0, 4, 4, 3), save_preset=io.CUSTOM_SAVE_VIDEO_PRESET,
            custom_output_kind=io.CUSTOM_OUTPUT_KIND_PNG_SEQUENCE, embed_metadata=False,
        )
    assert not list((tmp_path / "media").iterdir())
    assert project.assets == [] and saves == []


def re_escape(text):
    import re
    return re.escape(text)


# --- Real ffmpeg -----------------------------------------------------------


def _real_ffmpeg(io, monkeypatch):
    ffmpeg = _require_ffmpeg(io)
    media_helpers = importlib.import_module(f"{TEST_PACKAGE}.server.media_helpers")
    monkeypatch.setattr(media_helpers, "get_ffmpeg_path", lambda: ffmpeg)
    return ffmpeg, media_helpers


def _audio(seconds):
    torch = importlib.import_module("torch")
    return {"waveform": torch.zeros(1, 2, int(round(seconds * 48000))), "sample_rate": 48000}


FRAME_COUNT = 49
VIDEO_SECONDS = FRAME_COUNT / FPS
AAC_FRAME_SECONDS = 1024 / 48000


@pytest.mark.parametrize("label,seconds", [
    ("short", VIDEO_SECONDS - 0.0283),
    ("tiny", 0.5),
    ("long", VIDEO_SECONDS + 1.5),
])
def test_save_video_stream_with_real_ffmpeg_keeps_every_frame_and_bounds_audio(tmp_path, monkeypatch, label, seconds):
    io, project, _saves, _posters = _env(tmp_path, monkeypatch)
    ffmpeg, _media = _real_ffmpeg(io, monkeypatch)

    result = io.SonderSaveVideo().save_video(
        project, _batch(FRAME_COUNT, 32, 48), fps=FPS, audio=_audio(seconds), embed_metadata=False,
    )

    output = Path(result["result"][0])
    assert _count_video_frames(ffmpeg, output) == FRAME_COUNT
    assert abs(_decoded_audio_seconds(ffmpeg, output) - VIDEO_SECONDS) <= AAC_FRAME_SECONDS + 1e-3
    assert project.assets[0].frame_count == FRAME_COUNT and project.assets[0].has_audio


def test_preview_video_stream_with_real_ffmpeg_keeps_every_frame_and_bounds_audio(tmp_path, monkeypatch):
    io, _project, _saves, _posters = _env(tmp_path, monkeypatch)
    ffmpeg, _media = _real_ffmpeg(io, monkeypatch)

    result = io.SonderPreviewVideo().preview(_batch(FRAME_COUNT, 32, 48), fps=FPS, audio=_audio(VIDEO_SECONDS - 0.0283))

    output = tmp_path / "temp" / result["ui"]["sonder_video"][0]["filename"]
    assert _count_video_frames(ffmpeg, output) == FRAME_COUNT
    assert abs(_decoded_audio_seconds(ffmpeg, output) - VIDEO_SECONDS) <= AAC_FRAME_SECONDS + 1e-3


def _take_context():
    return {"scene_id": "s", "scene_name": "S", "selection_start": 0, "selection_end": FRAME_COUNT,
            "actual_pre_context_frames": 0, "take_placement_mode": "untrimmed"}


def test_take_mode_stream_with_real_ffmpeg_places_its_audio_sidecar(tmp_path, monkeypatch):
    io, project, _saves, _posters = _env(tmp_path, monkeypatch, scene=True)
    ffmpeg, _media = _real_ffmpeg(io, monkeypatch)
    project._execution_context = _take_context()

    result = io.SonderSaveVideo().save_video(
        project, _batch(FRAME_COUNT, 32, 48), fps=FPS, mode="Take", audio=_audio(VIDEO_SECONDS - 0.0283),
        embed_metadata=False,
    )

    output = Path(result["result"][0])
    assert _count_video_frames(ffmpeg, output) == FRAME_COUNT
    video, audio_asset = project.assets
    assert video.frame_count == FRAME_COUNT
    assert audio_asset.generation_params["encoded_frame_count"] == FRAME_COUNT
    assert abs(audio_asset.duration_sec - VIDEO_SECONDS) <= AAC_FRAME_SECONDS + 1e-3
    sidecar = Path(project.project_dir) / audio_asset.path
    assert sidecar.is_file()
    scene = project.get_scene("s")
    assert len(scene.clips) == 1 and len(scene.audio_tracks) == 1


@pytest.mark.parametrize("fail_at", [0, 7])
def test_producer_failure_mid_encode_leaves_no_file_asset_or_save(tmp_path, monkeypatch, fail_at):
    io, project, saves, _posters = _env(tmp_path, monkeypatch, scene=True)
    _ffmpeg, media = _real_ffmpeg(io, monkeypatch)
    project._execution_context = _take_context()
    terminated = []
    real_terminate = media._terminate_process
    monkeypatch.setattr(media, "_terminate_process", lambda proc: (terminated.append(proc), real_terminate(proc)))
    monkeypatch.setattr(io, "_save_generated_project", lambda *a, **k: pytest.fail("no project save on failure"))
    real_stream = io.iter_transform_tensor_to_uint8_frames

    class ProducerFailed(RuntimeError):
        pass

    def failing_stream(*args, **kwargs):
        for index, frame in enumerate(real_stream(*args, **kwargs)):
            if index == fail_at:
                raise ProducerFailed(f"frame {index} could not be converted")
            yield frame

    monkeypatch.setattr(io, "iter_transform_tensor_to_uint8_frames", failing_stream)

    with pytest.raises(ProducerFailed, match=f"frame {fail_at}"):
        io.SonderSaveVideo().save_video(
            project, _batch(FRAME_COUNT, 32, 48), fps=FPS, mode="Take", audio=_audio(VIDEO_SECONDS),
            embed_metadata=False,
        )

    if fail_at > 0:
        assert terminated, "ffmpeg was running and must be terminated"
        assert all(proc.poll() is not None for proc in terminated)
    assert not [p for p in (tmp_path / "media").iterdir() if p.suffix in (".mp4", ".wav")]
    assert not list((tmp_path / "temp").glob("_tmp_audio_*.wav"))
    assert project.assets == [] and saves == []
    assert not project.get_scene("s").clips and not project.get_scene("s").audio_tracks
