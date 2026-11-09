"""The duplicate-text advisory, through the real H3 setup resolver and compiler.

Phase 3a of the UX, QoL and Correctness Pass. One member's Library prose
reaching a compile through a Subject chip and a Picture chip is sent twice:
"<Subject 1> is X from <Picture 1>" and "<Picture 1> is X.". The advisory
used to compare whole lines, labels included, so it never fired for that
case, and its remedy ("blank one chip's field") brought the text back, since
a blank Definition follows the member's prose. It now compares the shared
member's prose, warns the Picture chip, and names the part to untick.
"""

from server import prompt_context
from server.timeline_state import (
    PromptSection,
    ReferenceEntity,
    ReferenceItem,
    ReferenceLaneRecipe,
    ReferenceMember,
)
from test_minimax_h3_format_coverage import PICTURE_RECIPE, WINDOW_END, _compile, _resolve

PROSE = "a western whiskey bottle with the S label."


def _chip(attachment_id, source, *, config=None, enabled=None, kind="definitions"):
    capability = {"capability_id": kind, "kind": kind,
                  "channel_key": "subject_definitions" if kind == "definitions"
                  else "retention_analysis",
                  "placement": "section_prefix"}
    if enabled is not None:
        capability["enabled"] = enabled
    return prompt_context.normalize_attachment({
        "attachment_id": attachment_id, "emission_group_id": attachment_id,
        "kind": "reference", "provider_id": "minimax_h3_ref", "provider_version": "1",
        "source": source, "config": config or {}, "capabilities": [capability]})


def _compile_with(global_chips=(), section_chips=(), *, sheet_prose="a revolver.",
                  units=None):
    entity = ReferenceEntity(reference_id="prop", name="Prop", members=[
        ReferenceMember(member_id="sonder", asset_id="img_a", name="Sonder", prompt=PROSE),
        ReferenceMember(member_id="sheet", asset_id="img_b", name="Weapon_Sheet",
                        prompt=sheet_prose)])
    recipes = [ReferenceLaneRecipe(lane_id="picture-lane", media_kind="image",
                                   recipe=PICTURE_RECIPE)]
    items = [ReferenceItem(
        reference_item_id="picture-item", lane_index=0, start_frame=0, end_frame=WINDOW_END,
        members=[{"entity_id": "prop", "member_id": "sonder", "role": "identity"},
                 {"entity_id": "prop", "member_id": "sheet", "role": "identity"}])]
    units = units if units is not None else [
        {"semantic_unit_id": "bottle", "name": "Bottle", "kind": "subject",
         "sources": [{"entity_id": "prop", "member_id": "sonder"}]}]
    resolved = _resolve(setup={"mode": "reference", "picture_lane_ids": ["picture-lane"]},
                        entities=[entity], items=items, recipes=recipes, units=units)
    section = PromptSection(0, WINDOW_END, channels={"detailed_description": "The shot."},
                            attachments=[prompt_context.shot_attachment(), *section_chips])
    section.prompt_id = "section"
    return _compile(resolved, units, [section], global_attachments=list(global_chips),
                    references=[entity])


def _duplicates(compiled):
    return [row for row in compiled["warnings"]
            if row["code"] == "duplicate_reference_emission"]


def _definitions(compiled):
    return [line for line in compiled["prompt"].splitlines()
            if line.startswith("<Subject") or line.startswith("<Picture")]


SUBJECT = {"semantic_unit_ids": ["bottle"]}
PICTURE = {"picture_ids": ["sonder"]}


def test_a_subject_and_a_picture_chip_sharing_prose_warn_the_picture_chip():
    compiled = _compile_with([_chip("subject", SUBJECT), _chip("picture", PICTURE)])
    assert not compiled["errors"]
    # Advisory only: both lines are still sent.
    assert _definitions(compiled) == [
        f"<Subject 1> is {PROSE[:-1]} from <Picture 1>",
        f"<Picture 1> is {PROSE}"]
    [warning] = _duplicates(compiled)
    assert warning["attachment_id"] == "picture"
    assert warning["capability_kind"] == "definitions"
    assert warning["message"] == (
        "Text for Prop · Sonder is already sent by another chip in this channel. "
        "To send it once, untick Definition under Prompt parts and routing on this "
        "chip and on any other chip sending this Picture.")


def test_the_picture_chip_is_warned_whichever_compiles_first():
    compiled = _compile_with([_chip("picture", PICTURE), _chip("subject", SUBJECT)])
    assert [row["attachment_id"] for row in _duplicates(compiled)] == ["picture"]


def test_unticking_definition_on_the_picture_chip_sends_the_prose_once():
    """The remedy the message gives works: no inherited fallback brings the
    line back, and the Subject line stays."""
    compiled = _compile_with([_chip("subject", SUBJECT),
                              _chip("picture", PICTURE, enabled=False)])
    assert _duplicates(compiled) == []
    assert _definitions(compiled) == [f"<Subject 1> is {PROSE[:-1]} from <Picture 1>"]


def test_blanking_the_picture_chips_field_does_not_stop_it():
    """Why the old remedy was wrong: a blank Definition follows the member."""
    compiled = _compile_with([_chip("subject", SUBJECT),
                              _chip("picture", PICTURE, config={"definition": ""})])
    assert len(_duplicates(compiled)) == 1
    assert f"<Picture 1> is {PROSE}" in _definitions(compiled)


def test_a_picture_sent_from_two_chips_moves_rather_than_stops():
    """Unticking one copy promotes the other; the warning follows it, and
    unticking every copy clears both."""
    both = _compile_with([_chip("subject", SUBJECT), _chip("picture", PICTURE)],
                         [_chip("picture-section", PICTURE)])
    assert [row["attachment_id"] for row in _duplicates(both)] == ["picture"]
    moved = _compile_with([_chip("subject", SUBJECT), _chip("picture", PICTURE, enabled=False)],
                          [_chip("picture-section", PICTURE)])
    assert [row["attachment_id"] for row in _duplicates(moved)] == ["picture-section"]
    # The section's copy joins the global lines in the composed prompt.
    assert moved["prompt"].count(f"<Picture 1> is {PROSE}") == 1
    none = _compile_with([_chip("subject", SUBJECT), _chip("picture", PICTURE, enabled=False)],
                         [_chip("picture-section", PICTURE, enabled=False)])
    assert _duplicates(none) == []
    assert "<Picture 1> is" not in none["prompt"]
    assert f"<Subject 1> is {PROSE[:-1]} from <Picture 1>" in none["prompt"]


def test_different_members_with_the_same_prose_are_not_duplicates():
    compiled = _compile_with([_chip("bottle", PICTURE), _chip("sheet", {"picture_ids": ["sheet"]})],
                             sheet_prose=PROSE)
    assert _duplicates(compiled) == []
    assert _definitions(compiled) == [f"<Picture 1> is {PROSE}", f"<Picture 2> is {PROSE}"]


def test_one_chip_never_warns_against_itself():
    """A Subject chip binding two identities with one chip-level definition
    sends the same prose for two members: its own lines, no duplicate."""
    units = [
        {"semantic_unit_id": "bottle", "name": "Bottle", "kind": "subject",
         "sources": [{"entity_id": "prop", "member_id": "sonder"}]},
        {"semantic_unit_id": "gun", "name": "Gun", "kind": "subject",
         "sources": [{"entity_id": "prop", "member_id": "sheet"}]}]
    compiled = _compile_with([_chip("both", {"semantic_unit_ids": ["bottle", "gun"]},
                                    config={"definition": "a prop on the bar"})], units=units)
    assert _duplicates(compiled) == []
    assert len(_definitions(compiled)) == 2


def test_a_global_and_a_section_chip_sharing_an_id_are_still_two_chips():
    """Chip ids are scope-local: a global Picture chip and a section Subject
    chip may both be "shared". They are different chips, so the duplicate is
    reported, on the Picture chip in its own scope."""
    compiled = _compile_with([_chip("shared", PICTURE)], [_chip("shared", SUBJECT)])
    [warning] = _duplicates(compiled)
    assert (warning["attachment_id"], warning["origin"]) == ("shared", "global")


def test_different_prose_for_the_same_member_is_not_a_duplicate():
    compiled = _compile_with([_chip("subject", SUBJECT, config={"definition": "the hero's bottle"}),
                              _chip("picture", PICTURE)])
    assert _duplicates(compiled) == []
