import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for browser module tests")
    return subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_timeline_header_resize_and_shared_trim_precedence():
    module_url = (ROOT / "web" / "js" / "editor_timeline_canvas.js").as_uri()
    script = f"""
const mod = await import({json.dumps(module_url)});
const clips = [
    {{ clip_id: "left", timeline_start_frame: 0, timeline_end_frame: 10, track_index: 0 }},
    {{ clip_id: "right", timeline_start_frame: 10, timeline_end_frame: 20, track_index: 0 }},
];
let selected = new Set();
const host = {{
    _labelW: 100,
    _scaleTimeline: 1,
    activeScene: {{ clips, audio_tracks: [], prompt_sections: [] }},
    _trackLayout: [{{ type: mod.TRACK_TYPE.VIDEO, laneIndex: 0, collapsed: false }}],
    _layoutIndexFromRawY() {{ return 0; }},
    _clipMatchesTrackEntry(clip, entry) {{ return clip.track_index === entry.laneIndex; }},
    _frameToX(frame) {{ return 100 + frame * 10; }},
    _isSelected(type, id) {{ return selected.has(`${{type}}:${{id}}`); }},
}};
const result = {{
    rulerResize: mod._hitTestHeaderEdge(host, 100, 5),
    laneResize: mod._hitTestHeaderEdge(host, 100, 30),
    frameZero: mod._hitTestEdge(host, 100, 30),
    leftSide: mod._hitTestEdge(host, 199, 30),
    rightSide: mod._hitTestEdge(host, 201, 30),
    exact: mod._hitTestEdge(host, 200, 30),
}};
selected = new Set(["clip:right"]);
result.selectedExact = mod._hitTestEdge(host, 200, 30);
console.log(JSON.stringify(result));
"""
    result = json.loads(_run_node(script))

    assert result["rulerResize"] is True
    assert result["laneResize"] is False
    assert (result["frameZero"]["id"], result["frameZero"]["edge"]) == ("left", "left")
    assert (result["leftSide"]["id"], result["leftSide"]["edge"]) == ("left", "right")
    assert (result["rightSide"]["id"], result["rightSide"]["edge"]) == ("right", "left")
    assert (result["exact"]["id"], result["exact"]["edge"]) == ("left", "right")
    assert (result["selectedExact"]["id"], result["selectedExact"]["edge"]) == ("left", "right")


def test_timeline_edge_auto_scroll_delta_is_quadratic_and_bounded():
    module_url = (ROOT / "web" / "js" / "editor_timeline_canvas.js").as_uri()
    script = f"""
const mod = await import({json.dumps(module_url)});
const delta = (position) => mod._edgeAutoScrollDelta(position, 100, 300, 20, 8);
console.log(JSON.stringify({{
    center: delta(200),
    topEdge: delta(100),
    topHalf: delta(110),
    topBandEnd: delta(120),
    bottomBandStart: delta(280),
    bottomHalf: delta(290),
    bottomEdge: delta(300),
    topOutside: delta(79),
    invalidViewport: mod._edgeAutoScrollDelta(100, 100, 100, 20, 8),
}}));
"""
    result = json.loads(_run_node(script))

    assert result == {
        "center": 0,
        "topEdge": -8,
        "topHalf": -2,
        "topBandEnd": 0,
        "bottomBandStart": 0,
        "bottomHalf": 2,
        "bottomEdge": 8,
        "topOutside": 0,
        "invalidViewport": 0,
    }


def test_editor_widget_wires_vertical_edge_scroll_and_content_anchored_drag_selection():
    source = (ROOT / "web" / "js" / "editor_widget.js").read_text(encoding="utf-8")

    assert 'const horizontalEdgeScrollDragTypes = new Set(["boxSelect", "moveItem", "playhead"])' in source
    assert '|| this.dragType === "laneSelect"' in source
    assert "const edgeScrollEnabled = () => horizontalEdgeScrollDragTypes.has(this.dragType)" in source
    assert "if (!this.isDragging || !edgeScrollEnabled())" in source
    assert '(this.dragType === "moveItem" && this._dragAnchorType === "clip")' in source
    assert "this._totalTracksHeight() <= visibleH" in source
    assert "const deltaPixels = edgeScrollDeltaPixels();" in source
    assert "this.scrollY += deltaPixels;" in source
    assert "this._clampScrollY();" in source
    assert "startContentY: contentY" in source
    assert "rect.currentContentY = this._dragSelectContentY(rawY);" in source
    assert "const top = this._trackY(layoutIdx) - rulerH;" in source
    assert "rulerH + rect.startContentY - this.scrollY" in source
    assert "_lanesInContentRange(rect)" in source
    assert "const top = this._trackY(i) - rulerH;" in source
    assert "const drawY = Math.max(rulerH, y1);" in source


def test_lane_drag_selection_range_remains_anchored_to_timeline_content():
    source = (ROOT / "web" / "js" / "editor_widget.js").read_text(encoding="utf-8")
    method_start = source.index("    _lanesInContentRange(rect) {")
    method_end = source.index("\n    _updateDragSelect(", method_start)
    method_source = source[method_start:method_end].strip()
    script = f"""
const methodSource = {json.dumps(method_source)};
const lanesInContentRange = new Function(
    "return ({{" + methodSource + "}})._lanesInContentRange;"
)();
const host = {{
    scrollY: 80,
    _trackLayout: [
        {{ type: "video", laneIndex: 0 }},
        {{ type: "video", laneIndex: 1 }},
        {{ type: "audio", laneIndex: 0 }},
        {{ type: "spacer", laneIndex: 0 }},
    ],
    _timelineRulerHeight() {{ return 20; }},
    _trackY(index) {{ return 20 + index * 40; }},
    _trackH() {{ return 40; }},
    _isHeaderControllableTrackType(type) {{ return type !== "spacer"; }},
    _laneRefForEntry(entry) {{ return {{ type: entry.type, laneIndex: entry.laneIndex }}; }},
}};
const rect = {{ startContentY: 41, currentContentY: 119 }};
const beforeScroll = lanesInContentRange.call(host, rect);
host.scrollY = 0;
const afterScroll = lanesInContentRange.call(host, rect);
console.log(JSON.stringify({{ beforeScroll, afterScroll }}));
"""
    result = json.loads(_run_node(script))

    expected = [
        {"type": "video", "laneIndex": 1},
        {"type": "audio", "laneIndex": 0},
    ]
    assert result == {"beforeScroll": expected, "afterScroll": expected}


def test_viewport_empty_and_missing_states_draw_optional_scene_outline():
    module_url = (ROOT / "web" / "js" / "viewport_surface.js").as_uri()
    script = f"""
const {{ createViewportSurface }} = await import({json.dumps(module_url)});
function render(scene, enabled) {{
    const calls = [];
    const ctx = {{
        globalAlpha: 1,
        fillStyle: "",
        font: "",
        textAlign: "",
        textBaseline: "",
        lineWidth: 1,
        strokeStyle: "",
        fillRect(...args) {{ calls.push(["fillRect", ...args]); }},
        fillText(...args) {{ calls.push(["fillText", ...args]); }},
        strokeRect(...args) {{ calls.push(["strokeRect", ...args]); }},
        save() {{}},
        restore() {{}},
    }};
    const canvas = {{ width: 320, height: 180, getContext() {{ return ctx; }} }};
    const surface = createViewportSurface({{
        canvas,
        getScene: () => scene,
        getFrame: () => 0,
        getTotalFrames: () => 24,
        getAssetForSourcePath: () => null,
        isSceneOutlineEnabled: () => enabled,
    }});
    surface.renderFrame();
    surface.destroy();
    return calls.filter((call) => call[0] === "strokeRect").length;
}}
const empty = {{ clips: [], audio_tracks: [], guide_frames: [] }};
const missing = {{
    clips: [{{ clip_id: "missing", source_path: "media/missing.mp4", timeline_start_frame: 0, timeline_end_frame: 10, track_index: 0 }}],
    audio_tracks: [],
    guide_frames: [],
}};
console.log(JSON.stringify({{
    emptyOn: render(empty, true),
    emptyOff: render(empty, false),
    missingOn: render(missing, true),
    missingOff: render(missing, false),
}}));
"""
    result = json.loads(_run_node(script))

    assert result == {"emptyOn": 1, "emptyOff": 0, "missingOn": 1, "missingOff": 0}


def test_editor_widget_wires_linked_unmute_and_clicked_type_consolidation():
    source = (ROOT / "web" / "js" / "editor_widget.js").read_text(encoding="utf-8")

    assert "_buildLinkedMuteOperations(unmuteSeeds, false)" in source
    assert "apply_linked: applyLinked" in source
    assert "if (target.hidden)" in source
    assert "this._trackVisibilityState(target) !== \"visible\"" in source
    assert "visibilitySeq !== this._headerVisibilitySeq" in source
    assert "_trimDeltaLimits(edgeHit" in source
    assert "validate_lane_collision: true" in source
    assert "item.maxEnd ?? Number.POSITIVE_INFINITY" in source
    assert "selected?.type !== hit.type" in source
    assert "type: \"consolidate_items\"" in source
    assert "remove_vacated_lanes: true" in source


def test_timeline_canvas_backing_store_changes_only_when_dimensions_change():
    module_url = (ROOT / "web" / "js" / "editor_timeline_canvas.js").as_uri()
    script = f"""
const {{ _syncTimelineCanvasDimensions: syncDimensions }} = await import({json.dumps(module_url)});
let width = 400;
let height = 200;
let widthWrites = 0;
let heightWrites = 0;
let styleWidthWrites = 0;
let styleHeightWrites = 0;
let styleWidth = "400px";
let styleHeight = "200px";
const style = {{}};
Object.defineProperty(style, "width", {{ get: () => styleWidth, set: (value) => {{ styleWidthWrites += 1; styleWidth = value; }} }});
Object.defineProperty(style, "height", {{ get: () => styleHeight, set: (value) => {{ styleHeightWrites += 1; styleHeight = value; }} }});
const canvas = {{ style }};
Object.defineProperty(canvas, "width", {{ get: () => width, set: (value) => {{ widthWrites += 1; width = value; }} }});
Object.defineProperty(canvas, "height", {{ get: () => height, set: (value) => {{ heightWrites += 1; height = value; }} }});
const unchanged = syncDimensions(canvas, 400, 200);
const changed = syncDimensions(canvas, 640, 360);
const stableAgain = syncDimensions(canvas, 640, 360);
console.log(JSON.stringify({{
    unchanged,
    changed,
    stableAgain,
    widthWrites,
    heightWrites,
    styleWidthWrites,
    styleHeightWrites,
}}));
"""
    result = json.loads(_run_node(script))

    assert result == {
        "unchanged": {"backingChanged": False, "styleChanged": False},
        "changed": {"backingChanged": True, "styleChanged": True},
        "stableAgain": {"backingChanged": False, "styleChanged": False},
        "widthWrites": 1,
        "heightWrites": 1,
        "styleWidthWrites": 1,
        "styleHeightWrites": 1,
    }


def test_viewport_playback_callbacks_do_not_duplicate_toolbar_or_transport_updates():
    source = (ROOT / "web" / "js" / "editor_widget.js").read_text(encoding="utf-8")
    start = source.index("            onFrameChange: (frame, meta = {}) => {")
    end = source.index("            onPlaybackWarmStateChange:", start)
    callback_source = source[start:end]

    assert callback_source.count("this._renderTimeline();") == 2
    assert "this._updateToolbar();" not in callback_source
    assert callback_source.count("this._updateTransportUI()") == 1
    assert "if (newFrame === this.playhead)" in source


def test_a_narrow_item_keeps_a_middle_and_a_wide_neighbour_cannot_claim_it():
    """Trim zones are capped at a third of the item's width, and a point in any
    item's middle is never a neighbour's edge (UX pass phase 1)."""
    module_url = (ROOT / "web" / "js" / "editor_timeline_canvas.js").as_uri()
    script = f"""
const mod = await import({json.dumps(module_url)});
// 1 frame = 1 px: "wide" spans 100 px, "narrow" 6 px right after it.
const clips = [
    {{ clip_id: "wide", timeline_start_frame: 0, timeline_end_frame: 100, track_index: 0 }},
    {{ clip_id: "narrow", timeline_start_frame: 100, timeline_end_frame: 106, track_index: 0 }},
];
const host = {{
    activeScene: {{ clips, audio_tracks: [], prompt_sections: [] }},
    _trackLayout: [{{ type: mod.TRACK_TYPE.VIDEO, laneIndex: 0, collapsed: false }}],
    _layoutIndexFromRawY() {{ return 0; }},
    _clipMatchesTrackEntry(clip, entry) {{ return clip.track_index === entry.laneIndex; }},
    _frameToX(frame) {{ return frame; }},
    _isSelected() {{ return false; }},
}};
const at = (x) => {{ const hit = mod._hitTestEdge(host, x, 30); return hit ? [hit.id, hit.edge] : null; }};
console.log(JSON.stringify({{
    zones: [mod.trimZonePx(300), mod.trimZonePx(6), mod.trimZonePx(0)],
    narrowMiddle: at(103),
    narrowRightEdge: at(105.5),
    sharedEdge: at(100),
    wideRightZone: at(95),
    wideMiddle: at(50),
}}));
"""
    result = json.loads(_run_node(script))

    assert result["zones"] == [6, 2, 0]
    # The narrow item's middle selects it; the wide item's 6 px zone, which
    # reaches across all of it, no longer claims it.
    assert result["narrowMiddle"] is None
    assert result["narrowRightEdge"] == ["narrow", "right"]
    assert result["sharedEdge"] is not None
    assert result["wideRightZone"] == ["wide", "right"]
    assert result["wideMiddle"] is None


def test_an_edge_inside_an_overlapping_items_middle_stays_trimmable():
    """Legacy overlap: B's left edge sits inside A's middle. Trimming is how
    the overlap is repaired, so B's edge must still resolve there."""
    module_url = (ROOT / "web" / "js" / "editor_timeline_canvas.js").as_uri()
    script = f"""
const mod = await import({json.dumps(module_url)});
const clips = [
    {{ clip_id: "a", timeline_start_frame: 0, timeline_end_frame: 100, track_index: 0 }},
    {{ clip_id: "b", timeline_start_frame: 50, timeline_end_frame: 150, track_index: 0 }},
];
const host = {{
    activeScene: {{ clips, audio_tracks: [], prompt_sections: [] }},
    _trackLayout: [{{ type: mod.TRACK_TYPE.VIDEO, laneIndex: 0, collapsed: false }}],
    _layoutIndexFromRawY() {{ return 0; }},
    _clipMatchesTrackEntry(clip, entry) {{ return clip.track_index === entry.laneIndex; }},
    _frameToX(frame) {{ return frame; }},
    _isSelected() {{ return false; }},
}};
const hit = mod._hitTestEdge(host, 51, 30);
console.log(JSON.stringify(hit ? [hit.id, hit.edge] : null));
"""
    assert json.loads(_run_node(script)) == ["b", "left"]


def test_phase1_gesture_wiring_contracts():
    """Source contracts for the UX pass phase 1 gestures. A behavioural check
    lives in the live verification; these stop a silent revert."""
    source = (ROOT / "web" / "js" / "editor_widget.js").read_text(encoding="utf-8")

    # A modifier press never starts a trim, and selects the edge's item.
    assert "const selectionModifier = e.ctrlKey || e.metaKey || e.shiftKey;" in source
    assert "if (edgeHit && !selectionModifier) {" in source
    assert "const hit = (edgeHit && selectionModifier)" in source
    # A trim that moved nothing sends nothing and drops its own Undo entry.
    assert "if (trimChangedBounds(trimInfo.type, trimInfo.data, trimInfo.origStart, trimInfo.origStoredEnd)) {" in source
    assert "this._discardUnstampableUndoEntry(trimInfo.historyEntry);" in source
    # A swap validates both destinations and never targets a linked/locked item.
    assert "allFitsValid = !swapFitRefusal([" in source
    assert "&& !this._isItemLocked(candidate) && !this._shouldApplyLinked(candidate)) {" in source
    # A no-op trim then does what a body click does: the linked group, no stale editor.
    assert "this.selectedItems = this._expandItemsWithLinked(this.selectedItems);\n                    this._hideItemEditor();" in source.replace("\r\n", "\n")
    # The cursor follows the press: a modifier over an edge selects.
    assert '(e.ctrlKey || e.metaKey || e.shiftKey) ? "grab" : "ew-resize"' in source
    assert source.count("validate_lane_collision_after_batch: true") == 2
    # A Guide drags from its shown frame and is named by its stored index.
    assert "? this._resolvedGuideFrame(hit.data)" in source
    assert "const oldIdx = orig.origFrameIndex ?? orig.origStart;" in source
    # Set Reference to Selection goes through the staged-item writer.
    assert '"set reference to selection", { writer: "timeline" });' in source
    assert "timeline: Object.freeze({" in source
