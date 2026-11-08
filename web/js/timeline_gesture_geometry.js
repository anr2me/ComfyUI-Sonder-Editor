// @server-mirror server/routes.py::_require_media_target_bounds_fit
// Scope and parity disposition: tests/test_mutation_authoring_contract.py::MIRRORED_MODULES
// Pure geometry for two timeline gestures: where a clip or audio swap puts
// its pair and whether that fits, and whether a trim changed anything.
//
// Leaf module: DOM-free, host-free, no editor imports, so Node tests can reach
// it (`editor_widget.js` cannot be imported under Node).
//
// Only `swapFitRefusal` mirrors the server. It reproduces
// `_require_media_target_bounds_fit`, which a drag commit now runs over the
// final bounds of every item it names (`validate_lane_collision_after_batch`).
// `tests/test_gesture_geometry_parity.py` drives both over one table. Where a
// swap PUTS its pair is client gesture policy: the server receives explicit
// bounds and lanes and only judges whether they fit.

/** Did a trim change the item's stored bounds?
 *
 *  Compared on the stored start and end only. A media trim moves its source
 *  window with its timeline edge (the left edge shifts `source_in_frame` by the
 *  same delta, the right edge `source_out_frame`), so unchanged bounds mean an
 *  unchanged source window. A Reference end is compared as stored, so a `-1`
 *  that became an explicit end, or the reverse, is a real write.
 */
export function trimChangedBounds(type, data, origStart, origStoredEnd) {
    if (!data) return false;
    const media = type === "clip" || type === "audio";
    const start = media ? data.timeline_start_frame : data.start_frame;
    const end = media ? data.timeline_end_frame : data.end_frame;
    return (Number(start) || 0) !== (Number(origStart) || 0)
        || (Number(end) || 0) !== (Number(origStoredEnd) || 0);
}

/** Where a swap puts the dragged item and the item it was dropped on.
 *
 *  Each side is `{ start, end, laneType, laneIndex }` as it stood at mousedown.
 *
 *  On one lane the pair trades ORDER inside the span it already covers: the
 *  later item moves to the earlier item's start, and the earlier item ends
 *  where the later one ended, so any gap between them is kept and nothing
 *  outside the pair's footprint is touched (maintainer, 2026-11-08). Equal
 *  lengths give the plain position exchange.
 *
 *  Across lanes each takes the other's start and lane and keeps its length;
 *  `swapFitRefusal` decides whether that lands on anything.
 */
export function swapDestinations(anchor, target) {
    const span = (side) => Math.max(0, (Number(side.end) || 0) - (Number(side.start) || 0));
    const anchorDuration = span(anchor);
    const targetDuration = span(target);
    const anchorStart = Number(anchor.start) || 0;
    const targetStart = Number(target.start) || 0;
    const sameLane = anchor.laneType === target.laneType
        && (Number(anchor.laneIndex) || 0) === (Number(target.laneIndex) || 0);
    const place = (start, duration, from) => ({
        start, end: start + duration, laneType: from.laneType, laneIndex: Number(from.laneIndex) || 0,
    });
    if (sameLane) {
        if (anchorStart <= targetStart) {
            const targetEnd = Number(target.end) || 0;
            return {
                anchor: place(targetEnd - anchorDuration, anchorDuration, anchor),
                target: place(anchorStart, targetDuration, target),
            };
        }
        const anchorEnd = Number(anchor.end) || 0;
        return {
            anchor: place(targetStart, anchorDuration, anchor),
            target: place(anchorEnd - targetDuration, targetDuration, target),
        };
    }
    return {
        anchor: place(targetStart, anchorDuration, target),
        target: place(anchorStart, targetDuration, anchor),
    };
}

/** Why these placements would be refused, or "" when they fit.
 *
 *  Mirrors `_require_media_target_bounds_fit`: each target must span at least
 *  one frame (`invalid_range`); targets on one lane must not overlap each
 *  other; and no target may overlap an item on its lane that is not itself a
 *  target (`lane_collision`). Half-open, so touching edges fit.
 *
 *  `targets`: `{ id, start, end, laneType, laneIndex }`. `laneItems`: every
 *  item of the relevant lanes in the same shape, targets included (they are
 *  skipped by id, as the server skips them).
 */
export function swapFitRefusal(targets, laneItems) {
    const overlap = (aStart, aEnd, bStart, bEnd) => aStart < bEnd && aEnd > bStart;
    const sameLane = (a, b) => a.laneType === b.laneType
        && (Number(a.laneIndex) || 0) === (Number(b.laneIndex) || 0);
    const targetIds = new Set(targets.map((target) => String(target.id)));
    for (let index = 0; index < targets.length; index++) {
        const target = targets[index];
        if (target.end <= target.start) return "invalid_range";
        for (const other of targets.slice(index + 1)) {
            if (sameLane(target, other) && overlap(target.start, target.end, other.start, other.end)) {
                return "lane_collision";
            }
        }
        for (const other of laneItems || []) {
            if (targetIds.has(String(other.id)) || !sameLane(target, other)) continue;
            if (overlap(target.start, target.end, other.start, other.end)) return "lane_collision";
        }
    }
    return "";
}
