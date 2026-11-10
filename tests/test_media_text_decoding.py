"""ffmpeg/ffprobe text reads survive non-ASCII metadata (Phase 4, 2026-11).

On Windows a text-mode subprocess read without `encoding=` decodes with cp1252.
UTF-8 output holding a byte cp1252 leaves undefined (0x81, 0x8D, 0x8F, 0x90,
0x9D; a curly quote is E2 80 9D) kills subprocess's reader thread, and the
read returns `stdout=None` with nothing raised. Every character in WORKFLOW_TEXT
below contains one of those bytes, so on a cp1252 host these tests fail
without the fix; on a UTF-8 host they still pin the exact round trip.
"""
from __future__ import annotations

import json
import locale
import os
import shutil
import subprocess

import pytest

from server import media_helpers
from server.timeline_state import Asset, TimelineProject


WORKFLOW_TEXT = "“quoted” Á с я"
WORKFLOW = {"nodes": [{"id": 1, "type": "CLIPTextEncode", "widgets_values": [WORKFLOW_TEXT]}]}


def _ffmpeg() -> str:
    path = media_helpers.get_ffmpeg_path()
    if not path or not (os.path.isfile(path) or shutil.which(path)):
        pytest.skip("ffmpeg is not available")
    return path


@pytest.fixture
def tagged_mp4(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    out = media / "curly.mp4"
    result = subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=8", "-t", "1",
            "-pix_fmt", "yuv420p",
            "-metadata", "workflow=" + json.dumps(WORKFLOW, ensure_ascii=False),
            "-movflags", "use_metadata_tags",
            str(out),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg could not write the fixture: " + result.stderr.decode("utf-8", "replace")[-200:])
    return out


def test_fixture_bytes_hit_the_cp1252_undefined_set():
    undefined = {0x81, 0x8D, 0x8F, 0x90, 0x9D}
    for char in "”Áся":
        assert undefined & set(char.encode("utf-8")), char
    # Recorded so a run's log says whether this host reproduces the bug.
    print("preferred encoding:", locale.getpreferredencoding(False))


def test_embedded_workflow_reads_exact_text_through_ffprobe(tagged_mp4, monkeypatch):
    # Bundled imageio-ffmpeg ships no ffprobe; without one this path cannot be
    # exercised for real (the ffmetadata test below covers that install, and
    # test_phase43_routes pins the ffprobe call's encoding arguments).
    if not shutil.which(media_helpers.get_ffprobe_path()):
        pytest.skip("ffprobe is not installed")
    calls = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        result = real_run(cmd, *args, **kwargs)
        calls.append((cmd[0], result.returncode))
        return result

    monkeypatch.setattr(media_helpers.subprocess, "run", spy)
    workflow = media_helpers.extract_embedded_workflow_metadata(str(tagged_mp4))
    assert workflow == WORKFLOW
    assert calls[0] == (media_helpers.get_ffprobe_path(), 0), "ffprobe itself answered"
    assert len(calls) == 1, "no fallback was needed"


def test_embedded_workflow_reads_exact_text_through_the_ffmetadata_fallback(tagged_mp4, monkeypatch):
    # Bundled imageio-ffmpeg ships no ffprobe; the fallback is that install's path.
    monkeypatch.setattr(media_helpers, "get_ffprobe_path", lambda: "sonder-missing-ffprobe")
    workflow = media_helpers.extract_embedded_workflow_metadata(str(tagged_mp4))
    assert workflow == WORKFLOW


def test_open_source_workflow_route_reads_exact_text(tagged_mp4):
    from server import routes

    project = TimelineProject(project_dir=str(tagged_mp4.parent.parent), name="Project", assets=[
        Asset(asset_id="a1", name="curly.mp4", asset_type="video", path=os.path.join("media", "curly.mp4")),
    ])
    assert routes._extract_embedded_workflow(project, project.assets[0]) == WORKFLOW


def test_ffmpeg_banner_read_keeps_the_metadata_block(tagged_mp4):
    text = media_helpers._ffmpeg_input_text(str(tagged_mp4))
    assert "Input #0" in text
    assert "“quoted”" in text


def test_a_bridge_take_with_curly_quotes_is_stored_as_embedded(tagged_mp4, tmp_path, monkeypatch):
    # The Save Bridge records `has_embedded_workflow` from this read; on a
    # cp1252 host before the fix it stored False for such a take for good.
    from test_save_bridge import _import_io_nodes

    io_nodes = _import_io_nodes(tmp_path, monkeypatch)
    params = io_nodes._generation_params_with_detected_workflow(
        {"editor_export": {"has_embedded_workflow": False}}, str(tagged_mp4))
    assert params["editor_export"]["has_embedded_workflow"] is True
    assert params["editor_export"]["workflow_sha256"]


def test_open_source_workflow_reads_the_file_off_the_event_loop(monkeypatch):
    import asyncio
    import threading
    from test_phase43_routes import DummyRequest, _load_route_module, _route_handler

    routes = _load_route_module(monkeypatch)
    project = TimelineProject(project_dir="project", name="Project", assets=[
        Asset(asset_id="a1", name="a.mp4", asset_type="video", path=os.path.join("media", "a.mp4"))])
    threads = []
    monkeypatch.setattr(routes, "_load_project_from_request", lambda request: project)

    def recording_reader(project, asset):
        threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(routes, "_extract_embedded_workflow", recording_reader)
    handler = _route_handler(routes, "GET", "/sonder-editor/project/{project_id}/assets/{asset_id}/workflow")

    async def go():
        return threading.get_ident(), await handler(DummyRequest(match_info={"asset_id": "a1"}))

    loop_thread, response = asyncio.run(go())
    assert response.status == 404
    assert threads and threads[0] != loop_thread, "ffprobe/ffmpeg must not run on the event loop"
