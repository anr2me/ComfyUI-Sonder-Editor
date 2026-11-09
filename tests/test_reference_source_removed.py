"""Phase 3b: a deleted Reference source never blocks generation.

The compiler leaves out what the removed source contributed (only that part of
a chip that still has other sources, the whole chip when nothing is left),
warns once per chip with `reference_source_removed`, and lets the run go on;
the node the Bridge feeds decides whether generation can run. Nothing at rest
changes. Plan: plans/ux-qol-phase-3b-deleted-sources.md.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from server import prompt_context, routes
from server.timeline_state import GenerationJob, PromptSection, ReferenceMember
from test_minimax_h3_format_coverage import (
    WINDOW_END, _identity_dormancy_fixture, _reference_chip,
)
from test_reference_prose_policy import _generic_project, _policy_compile

ROOT = Path(__file__).resolve().parents[1]
REMOVED = "reference_source_removed"


def _removed(result, attachment_id=None):
    return [row for row in result["warnings"] if row["code"] == REMOVED
            and (attachment_id is None or row["attachment_id"] == attachment_id)]


def _codes(result, tier="errors"):
    return {row["code"] for row in result[tier]}


# ── Physical sources ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["global_chip", "section_chip"])
def test_a_partial_chip_keeps_its_survivor_with_its_real_ordinal(scope):
    chip = _reference_chip(
        "partial", {"picture_ids": ["staged-member", "deleted-member"]},
        {"definition": "the staged picture"}, capabilities=("definitions", "mentions"))
    result = _identity_dormancy_fixture(**{scope: chip})
    assert not result["errors"]
    [row] = _removed(result, "partial")
    assert row["omitted"] == "partial"
    assert row["removed"] == [{"kind": "picture", "id": "deleted-member"}]
    assert row["location"] == ("Global" if scope == "global_chip" else "Section 1")
    assert "<Picture 1> is the staged picture" in result["prompt"]
    # The text and the slot manifest agree on every ordinal the prompt names.
    named = {int(value) for value in re.findall(r"<Picture (\d+)>", result["prompt"])}
    numbered = set(result["ordinal_manifest"]["pictures"].values())
    assert named and named <= numbered == {1}


def test_a_survivors_library_prose_still_emits_beside_a_removed_member():
    members = [ReferenceMember(member_id="staged-member", asset_id="img_a", prompt="STAGED PROSE")]
    chip = _reference_chip("partial", {"picture_ids": ["staged-member", "deleted-member"]}, {},
                           capabilities=("definitions",))
    result = _identity_dormancy_fixture(section_chip=chip, catalog_members=members)
    assert not result["errors"]
    assert "STAGED PROSE" in result["prompt"]
    assert len(_removed(result, "partial")) == 1


@pytest.mark.parametrize("scope", ["global_chip", "section_chip"])
def test_a_chip_whose_every_source_was_removed_is_left_out_whole(scope):
    chip = _reference_chip(
        "whole", {"picture_ids": ["deleted-member"]},
        {"summary": "WHOLE SUMMARY", "task_types": ["video editing"],
         "definition": "WHOLE DEFINITION"},
        capabilities=("summary", "definitions"))
    baseline = _identity_dormancy_fixture()
    result = _identity_dormancy_fixture(**{scope: chip})
    assert not result["errors"]
    assert "missing_reference_source" not in _codes(result)
    assert "reference_source_dormant" not in _codes(result, "warnings")
    [row] = _removed(result, "whole")
    assert row["omitted"] == "chip"
    assert "WHOLE" not in result["prompt"] and "video editing" not in result["prompt"]
    # Its explicit Summary task types no longer steer the scene's prefix.
    assert (result.get("h3_task_type_preview") or {}).get("scene_effective") == (
        baseline.get("h3_task_type_preview") or {}).get("scene_effective")
    # Inert like a disabled chip: no capability claims an emission.
    assert not [projection for projection in result["attachment_capability_projections"]
                if projection["attachment_id"] == "whole" and projection["state"] == "emitted"]


def _two_section_compile(second_chip, window_start=0, exceptions=()):
    from test_minimax_h3_format_coverage import _resolve, PICTURE_RECIPE
    from server.timeline_state import ReferenceEntity, ReferenceItem, ReferenceLaneRecipe
    entity = ReferenceEntity(reference_id="entity", name="Cast", members=[
        ReferenceMember(member_id="staged-member", asset_id="img_a")])
    resolved = _resolve(
        setup={"mode": "reference", "picture_lane_ids": ["lane"]}, entities=[entity],
        items=[ReferenceItem(reference_item_id="item", lane_index=0, start_frame=0,
                             end_frame=WINDOW_END, members=[{"entity_id": "entity",
                                                             "member_id": "staged-member"}])],
        recipes=[ReferenceLaneRecipe(lane_id="lane", media_kind="image", recipe=PICTURE_RECIPE)])
    first = PromptSection(0, WINDOW_END // 2, channels={"detailed_description": "FIRST."})
    first.prompt_id = "first"
    second = PromptSection(WINDOW_END // 2, WINDOW_END, channels={}, attachments=[second_chip])
    second.prompt_id = "second"
    second.global_channel_exceptions = list(exceptions)
    context = {"setup_manifest": resolved["setup_manifest"],
               "ordinal_manifest": resolved["ordinal_manifest"],
               "semantic_units": [], "references": [entity.to_dict()]}
    return prompt_context.compile_prompt_context(
        global_channels={"detailed_description": "GLOBAL."}, sections=[first, second],
        window_start=window_start, window_end=WINDOW_END, fps=24.0,
        template="minimax_h3_ref", profile="minimax_h3_ref@1", context=context,
        labels_on=True)


@pytest.mark.parametrize("window_start,exceptions", [
    (0, ()), (WINDOW_END * 6 // 10, ()), (WINDOW_END * 6 // 10, ("detailed_description",))])
def test_a_section_holding_only_a_removed_chip_behaves_like_a_disabled_one(
        window_start, exceptions):
    """Deleting a Library member must not erase another section's held text or
    a global channel: the section that held only that chip drops out of
    selection exactly as if the chip were disabled. It still warns while its
    frames overlap the window."""
    removed = _reference_chip("only", {"picture_ids": ["deleted-member"]}, {"text": "GONE"},
                              capabilities=("mentions",))
    disabled = _reference_chip("only", {"picture_ids": ["staged-member"]}, {"text": "GONE"},
                               capabilities=("mentions",))
    disabled["enabled"] = False
    after = _two_section_compile(removed, window_start, exceptions)
    reference = _two_section_compile(disabled, window_start, exceptions)
    assert not after["errors"]
    assert after["prompt"] == reference["prompt"]
    assert "FIRST." in after["prompt"] and "GLOBAL." in after["prompt"]
    assert [row["location"] for row in _removed(after, "only")] == ["Section 2"]


def test_a_chip_authored_with_no_source_still_reports_missing_source():
    chip = _reference_chip("empty", {}, {"text": "EMPTY"}, capabilities=("mentions",))
    result = _identity_dormancy_fixture(section_chip=chip)
    assert "missing_reference_source" in _codes(result)
    assert not _removed(result)


def test_without_a_catalog_a_missing_physical_id_stays_dormant():
    """A project-less compile has no deletion verdict (legacy fail-open)."""
    from test_minimax_h3_format_coverage import _compile, _resolve, PICTURE_RECIPE
    from server.timeline_state import ReferenceEntity, ReferenceItem, ReferenceLaneRecipe
    entity = ReferenceEntity(reference_id="entity", name="Cast", members=[
        ReferenceMember(member_id="staged-member", asset_id="img_a")])
    resolved = _resolve(
        setup={"mode": "reference", "picture_lane_ids": ["lane"]}, entities=[entity],
        items=[ReferenceItem(reference_item_id="item", lane_index=0, start_frame=0,
                             end_frame=WINDOW_END, members=[{"entity_id": "entity",
                                                             "member_id": "staged-member"}])],
        recipes=[ReferenceLaneRecipe(lane_id="lane", media_kind="image", recipe=PICTURE_RECIPE)])
    chip = _reference_chip("legacy", {"picture_ids": ["deleted-member"]}, {"text": "LEGACY"},
                           capabilities=("mentions",))
    section = PromptSection(0, WINDOW_END, channels={"detailed_description": "Moves."},
                            attachments=[chip])
    section.prompt_id = "section"
    result = _compile(resolved, [], [section], references=None)
    assert not _removed(result)
    assert "reference_source_dormant" in _codes(result, "warnings")


# ── Subjects ──────────────────────────────────────────────────────────────────

def test_a_deleted_subject_with_authored_text_emits_nothing():
    chip = _reference_chip("subject", {"semantic_unit_ids": ["gone"]},
                           {"definition": "AUTHORED SUBJECT TEXT", "text": "AUTHORED MENTION"},
                           capabilities=("definitions", "mentions"))
    result = _identity_dormancy_fixture(section_chip=chip)
    assert not result["errors"]
    [row] = _removed(result, "subject")
    assert row == {**row, "omitted": "chip", "removed": [{"kind": "subject", "id": "gone"}]}
    assert "AUTHORED" not in result["prompt"]


@pytest.mark.parametrize("policy", ["drop", "keep"])
def test_a_deleted_subject_beside_an_unstaged_picture_is_never_dormant_or_named(monkeypatch, policy):
    chip = _reference_chip("mixed", {"semantic_unit_ids": ["gone"], "picture_ids": ["dormant-member"]},
                           {"text": "MIXED TEXT"}, capabilities=("mentions",))
    result = _policy_compile(monkeypatch, policy, section_chip=chip)
    assert not result["errors"]
    assert [row["omitted"] for row in _removed(result, "mixed")] == ["partial"]
    dormant = [row for row in result["warnings"] if row["code"] == "reference_source_dormant"]
    assert all("gone" not in row.get("semantic_unit_ids", []) for row in dormant)
    assert "Prompt identity" not in result["prompt"]
    assert "Reference:" not in result["prompt"]
    if policy == "drop":
        assert "MIXED TEXT" not in result["prompt"]


# ── Stable tokens in a field ───────────────────────────────────────────────────

def test_a_field_naming_a_removed_id_is_left_out_with_no_library_fallback():
    members = [ReferenceMember(member_id="staged-member", asset_id="img_a", prompt="LIBRARY PROSE")]
    chip = _reference_chip("token", {"picture_ids": ["staged-member"]},
                           {"definition": "wearing the hat from @picture(gone-member)"},
                           capabilities=("definitions",))
    result = _identity_dormancy_fixture(section_chip=chip, catalog_members=members)
    assert not result["errors"]
    assert "wearing the hat" not in result["prompt"]
    assert "LIBRARY PROSE" not in result["prompt"], "the omitted field must not fall back"
    [row] = _removed(result, "token")
    assert row["removed"] == [{"kind": "token", "id": "gone-member"}]
    assert row["omitted"] == "partial"


def test_a_subject_token_naming_a_removed_identity_is_left_out():
    """`@subject(missing)` used to block; with a catalog it names nothing that
    exists, so its line is left out and the chip warns."""
    chip = _reference_chip("subject-token", {"semantic_unit_ids": ["staged"]},
                           {"text": "beside @subject(missing)"}, capabilities=("mentions",))
    result = _identity_dormancy_fixture(global_chip=chip)
    assert "unresolved_prompt_token" not in _codes(result)
    assert "beside" not in result["prompt"]
    assert _removed(result, "subject-token")[0]["removed"] == [{"kind": "token", "id": "missing"}]


def test_a_token_in_inherited_identity_text_is_left_out():
    units = [{"semantic_unit_id": "staged", "name": "Staged", "kind": "subject",
              "definition": "the subject holding @picture(gone-member)",
              "sources": [{"entity_id": "entity", "member_id": "staged-member"}]}]
    chip = _reference_chip("inherited", {"semantic_unit_ids": ["staged"]}, {},
                           capabilities=("definitions",))
    result = _identity_dormancy_fixture(section_chip=chip, units=units)
    assert not result["errors"]
    assert "holding" not in result["prompt"]
    assert _removed(result, "inherited")[0]["removed"] == [{"kind": "token", "id": "gone-member"}]


@pytest.mark.parametrize("token_id", [
    "dormant-member",                         # an unstaged member
    "img_b",                                  # its asset
    "picture-lane:picture-item:dormant-member",  # an unstaged slot
    "dormant",                                # an unstaged identity
])
def test_a_token_naming_something_that_exists_still_blocks(token_id):
    kind = "subject" if token_id == "dormant" else "picture"
    chip = _reference_chip("live", {"picture_ids": ["staged-member"]},
                           {"definition": f"beside @{kind}({token_id})"},
                           capabilities=("definitions",))
    result = _identity_dormancy_fixture(section_chip=chip)
    assert "unresolved_prompt_token" in _codes(result)
    assert not _removed(result)


def test_one_warning_per_chip_across_validation_and_render_passes():
    chip = _reference_chip(
        "many", {"picture_ids": ["staged-member", "deleted-member"]},
        {"definition": "x @picture(gone-a)", "retention_detail": "y @picture(gone-b)"},
        capabilities=("definitions", "retention", "mentions"))
    result = _identity_dormancy_fixture(global_chip=chip)
    assert not result["errors"]
    [row] = _removed(result, "many")
    kinds = sorted((value["kind"], value["id"]) for value in row["removed"])
    assert ("picture", "deleted-member") in kinds and ("token", "gone-a") in kinds


def test_a_muted_section_never_warns():
    chip = _reference_chip("muted", {"picture_ids": ["deleted-member"]}, {"text": "M"},
                           capabilities=("mentions",))
    from test_minimax_h3_format_coverage import _compile, _resolve, PICTURE_RECIPE
    from server.timeline_state import ReferenceEntity, ReferenceItem, ReferenceLaneRecipe
    entity = ReferenceEntity(reference_id="entity", name="Cast", members=[
        ReferenceMember(member_id="staged-member", asset_id="img_a")])
    resolved = _resolve(
        setup={"mode": "reference", "picture_lane_ids": ["lane"]}, entities=[entity],
        items=[ReferenceItem(reference_item_id="item", lane_index=0, start_frame=0,
                             end_frame=WINDOW_END, members=[{"entity_id": "entity",
                                                             "member_id": "staged-member"}])],
        recipes=[ReferenceLaneRecipe(lane_id="lane", media_kind="image", recipe=PICTURE_RECIPE)])
    live = PromptSection(0, WINDOW_END, channels={"detailed_description": "Live."})
    live.prompt_id = "live"
    muted = PromptSection(0, WINDOW_END, channels={"detailed_description": "Muted."},
                          attachments=[chip], muted=True)
    muted.prompt_id = "muted"
    result = _compile(resolved, [], [live, muted], references=[entity])
    assert not result["errors"] and not _removed(result)


def test_nothing_at_rest_changes():
    chip = _reference_chip("rest", {"picture_ids": ["staged-member", "deleted-member"]}, {},
                           capabilities=("definitions",))
    before = json.dumps(chip, sort_keys=True)
    _identity_dormancy_fixture(section_chip=chip)
    assert json.dumps(chip, sort_keys=True) == before


# ── Generic Reference items, enqueue and frozen jobs ───────────────────────────

def _enqueue(project, scene):
    from server import prompt_channel_templates
    job = GenerationJob(scene_id=scene.scene_id, selection_start=0, selection_end=100, params={
        "snapshot_version": 1, "prompt_context_format": "prompt_context_v1",
        prompt_channel_templates.PROJECT_TEMPLATE_KEY:
            prompt_channel_templates.get_channel_template("standard")})
    routes._compose_frozen_job_prompt(project, job)
    return job


def test_enqueue_goes_ahead_and_freezes_the_omission_and_its_warning():
    project, scene = _generic_project()
    scene.reference_items[0].muted = False
    scene.reference_items = []  # the item was removed from the timeline
    job = _enqueue(project, scene)
    assert "WALK" in job.prompt and "CHIP TEXT" not in job.prompt
    [row] = [value for value in job.compiled_prompt_context["warnings"] if value["code"] == REMOVED]
    assert row["removed"] == [{"kind": "item", "id": "item"}] and row["omitted"] == "chip"
    # The response a client stores carries it, so the toast reads the job.
    assert any(value["code"] == REMOVED
               for value in job.to_dict()["compiled_prompt_context"]["warnings"])


def test_each_job_in_a_batch_composes_without_refusal():
    project, scene = _generic_project()
    scene.reference_items = []
    jobs = [_enqueue(project, scene) for _ in range(3)]
    assert all("WALK" in job.prompt for job in jobs)


# ── Frontend: enqueue toast and chip mark ─────────────────────────────────────

def _node_binary():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the JavaScript checks")
    return node


def _run_node(script):
    result = subprocess.run([_node_binary(), "--input-type=module", "-e", script],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_enqueue_toast_folds_a_batch_into_one_chip_count():
    uri = (ROOT / "web/js/prompt_context_diagnostics.js").as_uri()
    row = lambda origin, attachment, location: {
        "code": REMOVED, "origin": origin, "attachment_id": attachment, "location": location}
    jobs = [
        {"scene_id": "s", "compiled_prompt_context": {"warnings": [
            row("global", "g", "Global"), row("p3", "c", "Section 3"),
            {"code": "unrelated"}]}},
        # A second chunk of the same batch names the same Global chip again.
        {"scene_id": "s", "compiled_prompt_context": {"warnings": [row("global", "g", "Global")]}},
        {"scene_id": "s", "compiled_prompt_context": {}},
    ]
    out = _run_node(f"""
const m = await import({json.dumps(uri)});
const chips = m.removedReferenceSourceChips({json.dumps(jobs)});
const none = m.removedReferenceSourceChips([{{compiled_prompt_context: {{warnings: []}}}}]);
console.log(JSON.stringify({{count: chips.length,
  message: m.removedReferenceSourcesMessage(chips.length, chips.map((r) => r.location)),
  none: none.length, empty: m.removedReferenceSourcesMessage(0, [])}}));
""")
    assert out["count"] == 2 and out["none"] == 0 and out["empty"] == ""
    assert out["message"] == (
        "2 chips (Global, Section 3) name Reference sources that were removed, so those "
        "parts are left out of the prompt. The chips are marked Source removed.")


def test_the_chip_mark_reads_whole_or_partial_and_a_repaint_clears_it():
    from test_prompt_context_corrections import _run_chip_dom_script
    panel_source = (ROOT / "web/js/editor_prompt_panel.js").read_text(encoding="utf-8")
    start = panel_source.index("export function paintSourceRemovedMark(")
    source = panel_source[start:panel_source.index("\n}\n", start) + 2].replace(
        "export function", "function", 1)
    uri = (ROOT / "web/js/prompt_context_diagnostics.js").as_uri()
    result = _run_chip_dom_script(f"""
const {{REFERENCE_SOURCE_REMOVED, buildPromptContextDiagnostics}} = await import({json.dumps(uri)});
const COLORS = {{warningText: 'yellow'}};
{source}
// Through the real projection the panel uses: it must keep `omitted`.
const projected = buildPromptContextDiagnostics({{warnings: [
  {{code: 'reference_source_removed', attachment_id: 'w', origin: 'live', omitted: 'chip',
    removed: [{{kind: 'picture', id: 'gone'}}], message: 'm'}}]}}).byAttachment.w;
const panel = mod.createScopeChipRow({{attachments: [{{attachment_id: 'a', kind: 'reference', source: {{}}}}],
  allowedKinds: ['reference']}});
const chip = panel.querySelectorAll('button').find((x) => x.dataset.attachmentId === 'a');
const read = () => chip.querySelectorAll('span').filter((x) => x.dataset.sonderSourceRemovedMark === '1')
  .map((x) => x.textContent);
paintSourceRemovedMark(chip, [{{code: 'reference_source_removed', omitted: 'partial',
  removed: [{{kind: 'picture', id: 'x'}}, {{kind: 'picture', id: 'y'}}]}}]);
const partial = read(); const partialFlag = chip.dataset.sonderSourceRemoved;
chip.querySelector('[data-sonder-source-removed-mark]')?.remove();
paintSourceRemovedMark(chip, projected);
const whole = read();
chip.querySelector('[data-sonder-source-removed-mark]')?.remove();
const other = paintSourceRemovedMark(chip, [{{code: 'reference_source_dormant'}}]);
// A compiled projection pill shares the attachment id but is not the chip.
const pill = document.createElement('button');
pill.dataset.attachmentId = 'a'; pill.dataset.sonderPromptProjection = '1';
const pillMark = paintSourceRemovedMark(pill, projected);
console.log(JSON.stringify({{partial, partialFlag, whole, cleared: read(), other: other === null,
  pill: pillMark === null && pill.childNodes.length === 0}}));
""")
    assert result["partial"] == ["2 sources removed"] and result["partialFlag"] == "partial"
    assert result["whole"] == ["Source removed"]
    assert result["cleared"] == [] and result["other"] is True
    assert result["pill"] is True
