"""Explicit gallery Refresh failures and per-host drag hints (Phase 4, 2026-11).

An explicit Refresh (the button, the background menu, its Retry) that fails
says so once, keyed by what failed, and keeps the list it had. Implicit
refreshes stay silent, a project switch silences everything, and a Refresh
deferred behind pending edits replays once as the sync it asked for.
"""
import re
from pathlib import Path

from test_project_mutation_queue import _run_gesture_node, _run_node

ROOT = Path(__file__).resolve().parents[1]
GALLERY = (ROOT / "web/js/shared_asset_gallery.js").read_text(encoding="utf-8")
WIDGET = (ROOT / "web/js/editor_widget.js").read_text(encoding="utf-8")
CONTROLLER = (ROOT / "web/js/editor_node_controller.js").read_text(encoding="utf-8")
LIBRARY = (ROOT / "web/js/editor_reference_library.js").read_text(encoding="utf-8")


def _module_function(name: str) -> str:
    return re.search(r"^export function " + name + r"\(.*?^\}\n", GALLERY, re.M | re.S)[0].replace("export ", "", 1)


def _inner_function(name: str) -> str:
    return re.search(r"^    (?:async )?function " + name + r"\(.*?^    \}\n", GALLERY, re.M | re.S)[0]


_REFRESH_SLICE = "\n".join(
    [_module_function("refreshFailureMessage"),
     re.search(r"^    const refreshNoticeSource = .*$", GALLERY, re.M)[0]]
    + [_inner_function(n) for n in ("runExplicitRefresh", "dismissRefreshFailure",
                                     "reportRefreshFailure", "noteRefreshPending")])

_GALLERY_HARNESS = """
    import assert from 'node:assert/strict';
    const toasts = [], busy = [];
    const state = { destroyed: false };
    let projectDir = 'project';
    const currentProjectDir = () => projectDir;
    const detailOwnerBase = 'gallery#1';
    const refreshBtn = {};
    const setBusyButton = (button, on) => { busy.push(on); };
    const toast = (tier) => (message, opts = {}) => {
        const entry = { tier, message, ...opts, dismissed: false };
        toasts.push(entry);
        return { dismiss: () => { entry.dismissed = true; } };
    };
    const notifyError = toast('error'), notifyInfo = toast('info');
    const console = { warn: () => {} };
    let hostCalls = [], hostResult = async () => null;
    const options = { onRefresh: async (opts) => { hostCalls.push(opts); return await hostResult(opts); } };
""" + _REFRESH_SLICE


def test_an_explicit_refresh_failure_says_what_failed_once_and_offers_retry():
    _run_node(_GALLERY_HARNESS + """
        const httpError = (status, payload = {}) => Object.assign(new Error('raw text'), { status, payload });
        const cases = [
            [Object.assign(new TypeError('Failed to fetch')),
                "Couldn't refresh assets — ComfyUI didn't answer. The list shown is the last one loaded."],
            [httpError(500, { code: 'project_storage_unreadable', error: "This project's stored data could not be read." }),
                "This project's stored data could not be read."],
            [Object.assign(httpError(409, { code: 'project_version_conflict' }), { code: 'project_version_conflict' }),
                'The project changed while assets were refreshing. Try again.'],
            [httpError(404), 'This project could not be found. Reopen it, then try Refresh again.'],
            [httpError(500), "Couldn't refresh assets. The list shown is the last one loaded."],
        ];
        for (const [error, message] of cases) {
            toasts.length = 0; busy.length = 0;
            hostResult = async () => { throw error; };
            await runExplicitRefresh();
            assert.deepEqual(hostCalls.pop(), { explicit: true });
            assert.equal(toasts.length, 1, message);
            assert.equal(toasts[0].tier, 'error');
            assert.equal(toasts[0].source, 'gallery-refresh:gallery#1');
            assert.equal(toasts[0].message, message);
            assert.equal(toasts[0].detail, error.message);
            assert.equal(typeof toasts[0].onRetry,
                error.payload?.code === 'project_storage_unreadable' ? 'object' : 'function',
                'no Retry for unreadable storage: retrying cannot fix it');
            assert.deepEqual(busy, [true, false], 'the busy label resets');
        }
        // Never echoes a raw message as the sentence.
        assert.equal(refreshFailureMessage(Object.assign(new Error('C:/secret/path'), { status: 500 })),
            "Couldn't refresh assets. The list shown is the last one loaded.");
    """)


def test_retry_success_and_teardown_close_the_error():
    _run_node(_GALLERY_HARNESS + """
        hostResult = async () => { throw new TypeError('Failed to fetch'); };
        await runExplicitRefresh();
        const error = toasts[0];
        hostResult = async () => ({ ok: true });
        error.onRetry();
        await new Promise((r) => setTimeout(r, 0));
        assert.equal(hostCalls.length, 2, 'Retry is an explicit refresh');
        assert.deepEqual(hostCalls[1], { explicit: true });
        assert.equal(error.dismissed, true, 'a successful refresh closes the old error');
        // After teardown Retry is inert.
        hostResult = async () => { throw new TypeError('Failed to fetch'); };
        await runExplicitRefresh();
        state.destroyed = true;
        dismissRefreshFailure();
        toasts.at(-1).onRetry();
        await new Promise((r) => setTimeout(r, 0));
        assert.equal(hostCalls.length, 3);
    """)


def test_a_project_switch_or_a_resolved_refresh_is_silent():
    _run_node(_GALLERY_HARNESS + """
        hostResult = async () => null;
        await runExplicitRefresh();
        assert.equal(toasts.length, 0, 'a refresh that applied nothing says nothing');
        hostResult = async () => { projectDir = 'other'; throw new TypeError('Failed to fetch'); };
        await runExplicitRefresh();
        assert.equal(toasts.length, 0, 'a failure for a project no longer open says nothing');
        // A host replaying a deferred refresh reports through the same gate.
        reportRefreshFailure(new TypeError('x'), 'project');
        assert.equal(toasts.length, 0);
        reportRefreshFailure(new TypeError('x'), 'other');
        assert.equal(toasts.length, 1);
        noteRefreshPending();
        assert.equal(toasts.at(-1).message, 'Assets will refresh once your edits have saved.');
        assert.equal(toasts.at(-1).tier, 'info');
    """)


def test_only_the_button_and_the_menu_are_explicit():
    assert GALLERY.count("await runExplicitRefresh()") == 2
    assert 'label: "Refresh", action: async () => await runExplicitRefresh()' in GALLERY
    # Implicit refreshes keep calling the host without the flag.
    assert GALLERY.count("await options.onRefresh?.();") == 5
    assert "options.onRefresh({ explicit: true })" in GALLERY
    # The list is never touched on failure.
    assert "setData" not in _inner_function("runExplicitRefresh")
    assert "setData" not in _inner_function("reportRefreshFailure")


def test_the_fullscreen_refresh_rethrows_only_a_transport_failure_in_the_open_project():
    _run_gesture_node("""
        const w = makeWidget();
        w._fetchAssets = EditorWidget.prototype._fetchAssets;
        w._settings = {};
        w._markStaleReplayApplied = () => {};
        let applied = 0;
        w._applyAssetPayload = () => { applied += 1; return true; };
        let answer = async () => { throw new TypeError('Failed to fetch'); };
        globalThis.fetch = async (...args) => await answer(...args);
        await assert.rejects(w._fetchAssets({ mode: 'sync', manual: true, rethrow: true, explicit: true }),
            /Failed to fetch/);
        assert.equal(await w._fetchAssets({ mode: 'sync', manual: true }), null, 'implicit callers stay silent');
        // A switch while the request was out: silent even for an explicit refresh.
        answer = async () => { w.projectDir = 'other'; throw new TypeError('Failed to fetch'); };
        assert.equal(await w._fetchAssets({ mode: 'sync', manual: true, rethrow: true }), null);
        w.projectDir = 'project';
        // A frontend fault while applying is not a transport failure.
        answer = async () => new Response(JSON.stringify({ assets: [], folders: [], modified_at: 'v2' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } });
        w._applyAssetPayload = () => { throw new Error('render bug'); };
        assert.equal(await w._fetchAssets({ mode: 'sync', manual: true, rethrow: true }), null);
    """)


def test_an_explicit_refresh_behind_pending_edits_defers_with_its_intent():
    _run_gesture_node("""
        const w = makeWidget();
        w._fetchAssets = EditorWidget.prototype._fetchAssets;
        w._replayDeferredAssets = EditorWidget.prototype._replayDeferredAssets;
        w._hasPendingProjectMutations = () => true;
        const deferred = [];
        w._deferProjectBackedRefresh = (keys, reason) => { deferred.push([keys, reason]); };
        const outcome = await w._fetchAssets({ mode: 'sync', manual: true, rethrow: true, explicit: true,
            reason: 'gallery_manual_refresh' });
        assert.deepEqual(outcome, { deferred: true }, 'returns at once');
        assert.deepEqual(deferred, [[['assets'], 'gallery_manual_refresh']]);
        assert.deepEqual(w._deferredAssetsIntent, { mode: 'sync', manual: true, projectDir: 'project' });
        // An implicit deferral never downgrades the recorded intent.
        await w._fetchAssets({ reason: 'later_read' });
        assert.deepEqual(w._deferredAssetsIntent, { mode: 'sync', manual: true, projectDir: 'project' });

        // The replay runs ONE sync with that intent, and reports its failure.
        const calls = [], reports = [];
        w._assetGallery = { reportRefreshFailure: (error, dir) => reports.push([error.message, dir]) };
        w._fetchAssets = async (opts) => { calls.push(opts); throw new TypeError('Failed to fetch'); };
        await w._replayDeferredAssets();
        assert.equal(calls.length, 1);
        assert.equal(calls[0].mode, 'sync');
        assert.equal(calls[0].manual, true);
        assert.equal(calls[0].rethrow, true);
        assert.equal(calls[0].ignoreMutationGate, true);
        assert.deepEqual(reports, [['Failed to fetch', 'project']]);
        assert.equal(w._deferredAssetsIntent, null, 'consumed once');

        // A project switch before the replay drops the explicit part.
        w._deferredAssetsIntent = { mode: 'sync', manual: true, projectDir: 'project' };
        w.projectDir = 'other';
        calls.length = 0;
        w._fetchAssets = async (opts) => { calls.push(opts); return null; };
        await w._replayDeferredAssets();
        assert.deepEqual(calls, [{ ignoreMutationGate: true }]);
        assert.equal(reports.length, 1);
    """)


def test_the_slow_refresh_toast_says_refreshed_only_when_a_list_applied():
    start = WIDGET.index("onRefresh: async ({ explicit = false } = {}) => {")
    block = WIDGET[start:WIDGET.index("\n            },\n", start)]
    assert "if (outcome && !outcome.deferred) handle.resolve({ tier: \"info\", message: \"Assets refreshed\" });" in block
    assert "else handle.dismiss();" in block
    assert "rethrow: explicit" in block
    assert "this._assetGallery?.noteRefreshPending?.()" in block
    # The replay goes through the intent-keeping path, never a bare read.
    replay = WIDGET[WIDGET.index("    _replayDeferredProjectBackedRefresh() {"):]
    replay = replay[:replay.index("\n    }\n")]
    assert "this._fetchAssets({ ignoreMutationGate: true })" not in replay
    assert replay.count("this._replayDeferredAssets()") == 2


def test_the_dormant_refresh_rethrows_only_for_an_explicit_click_in_the_open_project():
    start = CONTROLLER.index("    async _refreshAssets({")
    method = CONTROLLER[start:CONTROLLER.index("\n    _seedAssetRefreshResult(", start)]
    _run_node("""
        import assert from 'node:assert/strict';
        let answer, seeded = 0;
        const requestProjectAssetRefresh = async () => await answer();
        const allocateAssetRefreshWave = () => 'w', buildAssetRefreshPolicy = () => ({});
        const getEditorSettings = () => ({}), getProjectAssetMutationEpoch = () => 0;
        const projectIdFromDir = (dir) => dir;
        const console = { warn: () => {} };
        const c = {""" + method.strip() + """,
            state: { projectDir: 'project' }, _destroyed: false, _recordDiagEvent: () => {},
            _seedAssetRefreshResult() { seeded += 1; } };
        answer = async () => { throw new TypeError('Failed to fetch'); };
        await assert.rejects(c._refreshAssets({ rethrow: true }), /Failed to fetch/);
        assert.equal(await c._refreshAssets({}), null);
        answer = async () => { c.state.projectDir = 'other'; throw new TypeError('Failed to fetch'); };
        assert.equal(await c._refreshAssets({ rethrow: true }), null);
        c.state.projectDir = 'project';
        answer = async () => ({ epoch: 0, payload: {} });
        c._seedAssetRefreshResult = () => { throw new Error('render bug'); };
        assert.equal(await c._refreshAssets({ rethrow: true }), null, 'an apply fault is not a refresh failure');
    """)
    mount = CONTROLLER[CONTROLLER.index("onRefresh: async ({ explicit = false } = {}) => {"):]
    mount = mount[:mount.index("onRequestResize")]
    assert "rethrow: explicit" in mount
    assert "} finally {\n                    this.card.syncModuleContainerHeight?.();" in mount


def test_each_host_names_its_drop_target_and_each_type_gets_its_sentence():
    _run_node("import assert from 'node:assert/strict';\n" + _module_function("assetRowDragHint") + """
        const hint = (type, dropTarget, manageMode = false) => assetRowDragHint({ asset_type: type }, { dropTarget, manageMode });
        for (const type of ['video', 'audio']) {
            assert.equal(hint(type, 'timeline'), 'Click to inspect. Drop on a matching lane, or on the ruler to create a new lane.');
            assert.equal(hint(type, 'graph'), 'Click to inspect. Drag onto the graph to create a loader node.');
        }
        assert.equal(hint('image', 'timeline'), 'Click to inspect. Drag onto the timeline to add a guide frame.');
        assert.equal(hint('image', 'graph'), 'Click to inspect. Drag onto the graph to create a loader node.');
        assert.equal(hint('artifact', 'timeline'), "Click to inspect. Artifacts can't be placed on the timeline.");
        assert.equal(hint('artifact', 'graph'), "Click to inspect. Artifacts can't be dropped on the graph.");
        assert.equal(hint('video', undefined), 'Click to inspect. Drag onto the graph to create a loader node.');
        for (const target of ['graph', 'timeline']) {
            assert.equal(hint('video', target, true), 'Click to inspect. Drag onto a folder header to move assets.');
        }
    """)
    # Both row renderers (active and folder view) use the helper; no sentence is left inline.
    assert GALLERY.count("assetRowDragHint(asset, { manageMode: state.manageMode, dropTarget: options.dropTarget })") == 2
    assert "graph-drop support is deferred" not in GALLERY
    widget_mount = WIDGET[WIDGET.index("this._assetGallery = mountSharedAssetGallery(this.assetGrid, {"):][:400]
    assert 'dropTarget: "timeline"' in widget_mount
    dormant_mount = CONTROLLER[CONTROLLER.index("const gallery = mountSharedAssetGallery(galleryHost, {"):][:400]
    assert 'dropTarget: "graph"' in dormant_mount


def test_the_fullscreen_overlay_is_marked_so_sidebar_drops_never_reach_the_graph():
    # The sidebar (gallery, Library) is moved into the overlay, outside
    # `this.container`. A text field there accepts drops by default, and an
    # unmarked overlay let the graph interceptor create a loader on the hidden
    # graph (reproduced live on 2026-11-09 with the gallery search field).
    overlay = WIDGET[WIDGET.index("    _createFullscreenOverlay() {"):]
    overlay = overlay[:overlay.index("// Toolbar")]
    assert 'overlay.dataset.sonderEditor = "1";' in overlay
    extension = (ROOT / "web/js/extension.js").read_text(encoding="utf-8")
    assert 'if (e.target.closest?.("[data-sonder-editor]")) return;' in extension


def test_library_rows_name_the_reference_lane():
    assert 'export const REFERENCE_DRAG_HINT = "Drag onto a Reference lane, or onto the ruler to add one.";' in LIBRARY
    header = LIBRARY[LIBRARY.index("header.draggable = !pendingEntity"):][:400]
    assert 'if (pendingEntity) header.title = "Saving this Reference.' in header
    assert "else if (header.draggable) header.title = REFERENCE_DRAG_HINT;" in header
    assert 'edit.title = "Edit Reference";' in LIBRARY
    assert 'remove.title = "Delete Reference";' in LIBRARY
    assert LIBRARY.count("`Inspect Reference member. ${REFERENCE_DRAG_HINT}`") == 2
    # A member still saving keeps its saving title.
    assert 'target.title = "Saving this member.";' in LIBRARY


def test_open_source_workflow_shows_one_notice():
    start = WIDGET.index("onOpenSourceWorkflow: async (asset) => {")
    block = WIDGET[start:WIDGET.index("canOpenSourceWorkflow", start)]
    assert '_showToast' not in block
    assert 'if (typeof open !== "function")' in block


def test_each_gallery_keeps_its_own_notice_and_retry_stays_in_its_project():
    _run_node(_GALLERY_HARNESS + """
        assert.equal(refreshNoticeSource, 'gallery-refresh:gallery#1', 'one slot per gallery instance');
        hostResult = async () => { throw new TypeError('Failed to fetch'); };
        await runExplicitRefresh();
        const error = toasts.at(-1);
        assert.equal(state.refreshFailureProjectDir, 'project');
        // The user opens another project; Retry must not refresh it.
        projectDir = 'other';
        const calls = hostCalls.length;
        error.onRetry();
        await new Promise((r) => setTimeout(r, 0));
        assert.equal(hostCalls.length, calls, 'Retry after a switch refreshes nothing');
        assert.equal(error.dismissed, true, 'and closes the stale notice');
        noteRefreshPending();
        assert.equal(toasts.at(-1).source, 'gallery-refresh:gallery#1');
    """)
    # setData closes a failure that belongs to another project; destroy closes it too.
    set_data = GALLERY[GALLERY.index("    function setData(nextData) {"):]
    set_data = set_data[:set_data.index("const previousAssets")]
    assert "state.refreshFailureProjectDir !== dataProjectDir" in set_data
    assert "dismissRefreshFailure();" in set_data
    destroy = GALLERY[GALLERY.index("    function destroy() {"):]
    assert "dismissRefreshFailure();" in destroy[:destroy.index("\n    }\n")]


def test_the_fullscreen_refresh_handler_runs_its_outcomes():
    # The real `onRefresh` from the fullscreen mount, run against a stub host.
    start = WIDGET.index("onRefresh: async ({ explicit = false } = {}) => {")
    handler = WIDGET[start:WIDGET.index("\n            },\n", start)] + "\n            },"
    _run_node("""
        import assert from 'node:assert/strict';
        const handles = [];
        const notifyProgress = (opts) => {
            const handle = { opts, resolved: null, dismissed: false,
                resolve(value) { this.resolved = value; }, dismiss() { this.dismissed = true; } };
            handles.push(handle);
            return handle;
        };
        // The progress toast waits 500 ms; here it shows at once.
        const window = { setTimeout: (fn) => setTimeout(fn, 0), clearTimeout };
        let answer, pending = 0;
        const seen = [];
        const host = {
            _fetchAssets: async (opts) => { seen.push(opts); await new Promise((r) => setTimeout(r, 5)); return await answer(); },
            _assetGallery: { noteRefreshPending: () => { pending += 1; } },
        };
        const options = (function () { return {""" + handler + """ }; }).call(host);
        answer = async () => ({ payload: {} });
        await options.onRefresh({ explicit: true });
        assert.deepEqual(handles.at(-1).resolved, { tier: 'info', message: 'Assets refreshed' });
        assert.equal(seen.at(-1).rethrow, true);
        assert.equal(seen.at(-1).explicit, true);
        answer = async () => null;
        await options.onRefresh();
        assert.equal(handles.at(-1).resolved, null, 'nothing applied: no "Assets refreshed"');
        assert.equal(handles.at(-1).dismissed, true);
        assert.equal(seen.at(-1).rethrow, false, 'implicit refreshes never rethrow');
        answer = async () => ({ deferred: true });
        await options.onRefresh({ explicit: true });
        assert.equal(handles.at(-1).dismissed, true);
        assert.equal(pending, 1, 'a deferred explicit refresh says it will run');
        await options.onRefresh();
        assert.equal(pending, 1, 'an implicit deferral stays silent');
        answer = async () => { throw new TypeError('Failed to fetch'); };
        await assert.rejects(options.onRefresh({ explicit: true }), /Failed to fetch/);
        assert.equal(handles.at(-1).resolved, null);
        assert.equal(handles.at(-1).dismissed, true);
    """)
