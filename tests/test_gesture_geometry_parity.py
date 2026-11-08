"""Timeline gesture geometry: swap placement, swap fit, and the no-op trim.

`web/js/timeline_gesture_geometry.js` mirrors exactly one server decision,
`_require_media_target_bounds_fit`, which a drag commit's final state must pass
(`validate_lane_collision_after_batch`). The parity test drives both over one
table. Where a swap places its pair and whether a trim changed anything are
client gesture policy with no server counterpart; they are pinned directly.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import server.routes as routes
from server.timeline_state import ClipReference, LaneConfig, Scene


ROOT = Path(__file__).resolve().parents[1]
MODULE_URL = (ROOT / "web" / "js" / "timeline_gesture_geometry.js").as_uri()


def _node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for browser module tests")
    result = subprocess.run([node, "--input-type=module", "-e", script],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


# Lane items: (id, start, end, lane). Targets: (id, start, end, lane).
# Every case crosses one branch the two sides could disagree on.
FIT_CASES = {
    "swap fits": ([("a", 0, 20, 0), ("b", 20, 100, 0), ("c", 120, 140, 0)],
                  [("a", 80, 100, 0), ("b", 0, 80, 0)]),
    "pair overlaps itself": ([("a", 0, 20, 0), ("b", 20, 100, 0)],
                             [("a", 20, 40, 0), ("b", 0, 80, 0)]),
    "lands on a third item": ([("a", 0, 20, 0), ("b", 20, 100, 0), ("c", 120, 140, 0)],
                              [("a", 110, 130, 0), ("b", 0, 80, 0)]),
    "touching edges fit": ([("a", 0, 20, 0), ("c", 40, 60, 0)],
                           [("a", 20, 40, 0)]),
    "other lane is ignored": ([("a", 0, 20, 0), ("c", 0, 50, 1)],
                              [("a", 10, 30, 0)]),
    "cross lane collides": ([("a", 0, 20, 0), ("b", 0, 30, 1), ("c", 25, 40, 1)],
                            [("a", 10, 30, 1), ("b", 0, 30, 0)]),
    "empty range": ([("a", 0, 20, 0)], [("a", 20, 20, 0)]),
}


@pytest.mark.parametrize("case", sorted(FIT_CASES))
def test_swap_fit_matches_the_server_final_state_check(case):
    items, targets = FIT_CASES[case]
    scene = Scene(scene_id="s", duration_frames=500)
    scene.video_lane_configs = [LaneConfig(), LaneConfig()]
    scene.video_lane_count = 2
    final = {target[0]: target for target in targets}
    scene.clips = []
    for item_id, start, end, lane in items:
        placed = final.get(item_id, (item_id, start, end, lane))
        scene.clips.append(ClipReference(
            clip_id=item_id, timeline_start_frame=placed[1], timeline_end_frame=placed[2],
            track_index=placed[3], source_out_frame=max(1, placed[2] - placed[1]),
            total_source_frames=max(1, placed[2] - placed[1])))
    by_id = {clip.clip_id: clip for clip in scene.clips}
    # Read through the module at call time: other suites `importlib.reload` it,
    # which replaces the exception class an import-time name would hold.
    try:
        routes._require_media_target_bounds_fit(scene, [
            (by_id[item_id], start, end, "video", lane) for item_id, start, end, lane in targets])
        python = ""
    except routes.ProjectMutationRequestError as exc:
        python = exc.code

    lane_items = [{"id": clip.clip_id, "start": clip.timeline_start_frame, "end": clip.timeline_end_frame,
                   "laneType": "video", "laneIndex": clip.track_index} for clip in scene.clips]
    js_targets = [{"id": item_id, "start": start, "end": end, "laneType": "video", "laneIndex": lane}
                  for item_id, start, end, lane in targets]
    javascript = _node(f"""
const mod = await import({json.dumps(MODULE_URL)});
console.log(JSON.stringify(mod.swapFitRefusal({json.dumps(js_targets)}, {json.dumps(lane_items)})));
""")
    assert javascript == python, case


def test_a_same_lane_swap_exchanges_order_within_the_pairs_footprint():
    result = _node(f"""
const mod = await import({json.dumps(MODULE_URL)});
const side = (start, end, laneIndex = 0) => ({{ start, end, laneType: "video", laneIndex }});
console.log(JSON.stringify({{
    adjacentForward: mod.swapDestinations(side(0, 20), side(20, 100)),
    adjacentBackward: mod.swapDestinations(side(20, 100), side(0, 20)),
    gapKept: mod.swapDestinations(side(0, 20), side(30, 110)),
    equalLengths: mod.swapDestinations(side(0, 20), side(40, 60)),
    crossLane: mod.swapDestinations(side(0, 20, 0), side(50, 130, 1)),
}}));
""")
    span = lambda d: (d["start"], d["end"], d["laneIndex"])
    assert (span(result["adjacentForward"]["anchor"]), span(result["adjacentForward"]["target"])) == (
        (80, 100, 0), (0, 80, 0))
    assert (span(result["adjacentBackward"]["anchor"]), span(result["adjacentBackward"]["target"])) == (
        (0, 80, 0), (80, 100, 0))
    # The 10-frame gap between the pair survives the exchange.
    assert (span(result["gapKept"]["anchor"]), span(result["gapKept"]["target"])) == (
        (90, 110, 0), (0, 80, 0))
    assert (span(result["equalLengths"]["anchor"]), span(result["equalLengths"]["target"])) == (
        (40, 60, 0), (0, 20, 0))
    # Across lanes each takes the other's start and lane and keeps its length.
    assert (span(result["crossLane"]["anchor"]), span(result["crossLane"]["target"])) == (
        (50, 70, 1), (0, 80, 0))


def test_a_trim_that_moved_nothing_reads_as_unchanged():
    result = _node(f"""
const mod = await import({json.dumps(MODULE_URL)});
console.log(JSON.stringify([
    mod.trimChangedBounds("clip", {{ timeline_start_frame: 10, timeline_end_frame: 40, source_in_frame: 0 }}, 10, 40),
    mod.trimChangedBounds("clip", {{ timeline_start_frame: 12, timeline_end_frame: 40 }}, 10, 40),
    mod.trimChangedBounds("audio", {{ timeline_start_frame: 10, timeline_end_frame: 39 }}, 10, 40),
    mod.trimChangedBounds("prompt", {{ start_frame: 5, end_frame: 9 }}, 5, 9),
    mod.trimChangedBounds("reference", {{ start_frame: 5, end_frame: -1 }}, 5, -1),
    mod.trimChangedBounds("reference", {{ start_frame: 5, end_frame: 100 }}, 5, -1),
]));
""")
    assert result == [False, True, True, False, False, True]
