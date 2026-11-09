/**
 * Project the authoritative candidate compile into the small amount of state
 * the Prompt tool needs. Candidate payloads are ephemeral and window-specific;
 * they must never be confused with the full-scene PromptRelay preview.
 */
export function promptCandidateVisuallyStale(payload) {
    return payload?._stale === true && payload?._stale_visual !== false;
}

export function buildPromptContextDiagnostics(payload) {
    const value = payload && typeof payload === "object" ? payload : {};
    const byAttachment = {};
    const general = [];
    const collect = (tier, rows) => {
        for (const raw of Array.isArray(rows) ? rows : []) {
            const diagnostic = {
                tier,
                code: String(raw?.code || tier),
                message: String(raw?.message || raw?.detail || "Prompt Context diagnostic."),
                attachment_id: String(raw?.attachment_id || ""),
                channel_key: String(raw?.channel_key || ""),
                capability_id: String(raw?.capability_id || ""),
                origin: String(raw?.origin || ""),
                semantic_unit_id: String(raw?.semantic_unit_id || ""),
                field: String(raw?.field || ""),
            };
            // The chip's Source removed mark reads whether the whole chip or
            // only part of it was left out, and how many sources were removed.
            if (diagnostic.code === REFERENCE_SOURCE_REMOVED) {
                diagnostic.omitted = raw?.omitted === "chip" ? "chip" : "partial";
                diagnostic.removed = Array.isArray(raw?.removed)
                    ? raw.removed.map((row) => ({ kind: String(row?.kind || ""), id: String(row?.id || "") }))
                    : [];
            }
            if (Array.isArray(raw?.attachments) && raw.attachments.length) {
                for (const owner of raw.attachments) {
                    const id = String(owner?.attachment_id || "");
                    if (id) (byAttachment[id] ||= []).push({ ...diagnostic,
                        attachment_id: id, origin: String(owner?.origin || "") });
                }
            } else if (diagnostic.attachment_id) {
                (byAttachment[diagnostic.attachment_id] ||= []).push(diagnostic);
            } else {
                general.push(diagnostic);
            }
        }
    };
    collect("error", value.errors);
    collect("warning", value.warnings);

    const window = value.execution_window && typeof value.execution_window === "object"
        ? value.execution_window : null;
    let windowLabel = "Checking the effective render window…";
    if (window) {
        const start = Number(window.render_start);
        const end = Number(window.render_end);
        const generationStart = Number(window.generation_start);
        const generationEnd = Number(window.generation_end);
        if ([start, end, generationStart, generationEnd].every(Number.isFinite)) {
            const pre = Math.max(0, Number(window.actual_pre) || 0);
            const post = Math.max(0, Number(window.actual_post) || 0);
            windowLabel = `Effective render window: frames ${start}–${end} `
                + `(selection ${generationStart}–${generationEnd}; pre ${pre}f, post ${post}f).`;
        }
    }
    return {
        byAttachment,
        general,
        windowLabel,
        errorCount: (Array.isArray(value.errors) ? value.errors.length : 0),
        warningCount: (Array.isArray(value.warnings) ? value.warnings.length : 0),
        attachmentDiagnosticCount: Object.values(byAttachment)
            .reduce((sum, rows) => sum + rows.length, 0),
        stale: promptCandidateVisuallyStale(value),
    };
}

// A whole-attachment chip shows all failures; individual capability pills only
// show failures in their own scope. Missing diagnostic scope remains broad.
export function promptCapabilityDiagnostics(
    rows, channelKey = "", capabilityId = "", origin = "",
) {
    const scoped = rows.filter((row) => !row.origin || !origin || row.origin === origin);
    if (!capabilityId) return scoped;
    return scoped.filter((row) =>
        (!row.channel_key || !channelKey || row.channel_key === channelKey)
        && (!row.capability_id || row.capability_id === capabilityId));
}

export function promptContextDiagnosticTitle(rows) {
    return (Array.isArray(rows) ? rows : []).map((row) =>
        `${String(row?.tier || "diagnostic").toUpperCase()} ${row?.code || ""}: ${row?.message || ""}`
            .trim()).join("\n");
}

/** Match queue semantics: an absent selection means the entire scene. */
export function resolvePromptCandidateSelection(start, end, duration) {
    const sceneEnd = Math.max(0, Math.round(Number(duration) || 0));
    const selectionStart = Math.max(0, Math.min(sceneEnd, Math.round(Number(start) || 0)));
    const selectionEnd = Math.max(0, Math.min(sceneEnd, Math.round(Number(end) || 0)));
    if (selectionStart < selectionEnd) {
        return { selectionStart, selectionEnd };
    }
    return { selectionStart: 0, selectionEnd: sceneEnd };
}

export const REFERENCE_SOURCE_REMOVED = "reference_source_removed";

/**
 * One sentence for chips a run or an enqueue left out because their Reference
 * sources were removed (Phase 3b). Shared by the enqueue toast and the run
 * notice so both say the same thing. ``locations`` are the compiler's
 * "Global" / "Section N" strings, de-duplicated by the caller.
 */
export function removedReferenceSourcesMessage(count, locations = []) {
    const total = Math.max(0, Number(count) || 0);
    if (!total) return "";
    const places = [...new Set((Array.isArray(locations) ? locations : [])
        .map((value) => String(value || "")).filter(Boolean))];
    const where = places.length ? ` (${places.join(", ")})` : "";
    return total === 1
        ? `1 chip${where} names a Reference source that was removed, so that part is left out of the prompt. The chip is marked Source removed.`
        : `${total} chips${where} name Reference sources that were removed, so those parts are left out of the prompt. The chips are marked Source removed.`;
}

/** The removed-source warnings of compiled jobs, one per chip across them. */
export function removedReferenceSourceChips(jobs) {
    const chips = new Map();
    for (const job of Array.isArray(jobs) ? jobs : [jobs]) {
        const warnings = job?.compiled_prompt_context?.warnings;
        for (const row of Array.isArray(warnings) ? warnings : []) {
            if (row?.code !== REFERENCE_SOURCE_REMOVED) continue;
            const key = `${job?.scene_id || ""}|${row.origin || ""}|${row.attachment_id || ""}`;
            if (!chips.has(key)) chips.set(key, row);
        }
    }
    return [...chips.values()];
}
