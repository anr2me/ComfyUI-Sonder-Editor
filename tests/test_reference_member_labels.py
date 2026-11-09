"""One member label for Attach, the Reference lane bar, bridges and Lane Setup.

Phase 3a of the UX, QoL and Correctness Pass. ``reference_member_display_names``
(server/reference_prompt_formatter.py) and ``referenceMemberDisplayNames``
(web/js/reference_resolution.js) are one rule on two sides: "Reference ·
Member", made unique across the Library. These tests hold the two to the same
output on the shapes that broke the old labels, and drive the browser surfaces
that read it.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from server.reference_prompt_formatter import reference_member_display_names
from server.timeline_state import ReferenceEntity, ReferenceMember

ROOT = Path(__file__).resolve().parents[1]
RESOLUTION = (ROOT / "web" / "js" / "reference_resolution.js").as_uri()
CHIPS = (ROOT / "web" / "js" / "prompt_context_chips.js").as_uri()


def _node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the browser label rule")
    completed = subprocess.run([node, "--input-type=module", "-e", script],
                               capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _library():
    """The awkward shapes, as Testground holds some of them."""
    return [
        {"reference_id": "prop", "name": "Prop", "members": [
            {"member_id": "sheet", "name": "Weapon_Sheet", "handle": "Clementine"},
            {"member_id": "bottle", "name": "Sonder", "handle": "SonderBottle"}]},
        # Ten members with one name, as Testground's Cowboy has.
        {"reference_id": "cowboy", "name": "Cowboy", "members": [
            {"member_id": "c-sheet", "name": "Cowboy Character Sheet"},
            *({"member_id": f"c-{n}", "name": "ChatGPT_Image_2"} for n in range(10))]},
        # A handle tells one of a colliding pair apart; the other stays plain.
        {"reference_id": "keeper", "name": "Tavern Keeper", "members": [
            {"member_id": "k-1", "name": "Sheet", "handle": "Keeper"},
            {"member_id": "k-2", "name": "Sheet"},
            {"member_id": "k-3", "name": "", "handle": ""},
            {"member_id": "k-4", "name": "  ", "handle": ""}]},
        # Two References with one name, sharing a member name too.
        {"reference_id": "cowboy-2", "name": "Cowboy", "members": [
            {"member_id": "c2-sheet", "name": "Cowboy Character Sheet"}]},
        {"reference_id": "blank", "name": "", "members": [
            {"member_id": "b-1", "name": "Front"}]},
    ]


def test_member_display_names_match_between_python_and_javascript():
    library = _library()
    fallback = {"k-3": "keeper_front.png"}
    python = reference_member_display_names(library)
    python_fallback = reference_member_display_names(library, fallback_names=fallback)
    browser = _node(f"""
const mod = await import({json.dumps(RESOLUTION)});
const library = {json.dumps(library)};
const plain = Object.fromEntries(mod.referenceMemberDisplayNames(library));
const named = Object.fromEntries(mod.referenceMemberDisplayNames(library,
    {{ fallbackNames: new Map(Object.entries({json.dumps(fallback)})) }}));
console.log(JSON.stringify({{ plain, named }}));
""")
    assert browser["plain"] == python
    assert browser["named"] == python_fallback


def test_every_member_label_in_the_library_is_distinct():
    labels = reference_member_display_names(_library())
    texts = [row["text"] for row in labels.values()]
    assert len(texts) == len(set(texts)) == 19
    assert labels["bottle"]["text"] == "Prop · Sonder"
    assert labels["sheet"]["text"] == "Prop · Weapon_Sheet"
    assert labels["c-sheet"]["text"] == "Cowboy · Cowboy Character Sheet"
    assert labels["c-0"]["text"] == "Cowboy · ChatGPT_Image_2 (1)"
    assert labels["c-9"]["text"] == "Cowboy · ChatGPT_Image_2 (10)"
    assert labels["k-1"]["text"] == "Tavern Keeper · Sheet @Keeper"
    # The only unhandled one of its pair needs no number.
    assert labels["k-2"]["text"] == "Tavern Keeper · Sheet"
    # Blank and whitespace member names collide as "no name".
    assert labels["k-3"]["text"] == "Tavern Keeper (1)"
    assert labels["k-4"]["text"] == "Tavern Keeper (2)"
    assert labels["c2-sheet"]["text"] == "Cowboy (2) · Cowboy Character Sheet"
    assert labels["b-1"]["text"] == "Reference · Front"


def test_the_published_reference_names_tokens_do_not_take_the_display_suffixes():
    """`reference_names` is a Bridge output value a graph may consume. It stays
    the token form of `reference_member_labels`; telling members apart is a
    display concern only."""
    from server.reference_prompt_formatter import reference_member_labels
    source = (ROOT / "nodes" / "reference_core.py").read_text(encoding="utf-8")
    assert "reference_member_display_names" not in source
    assert reference_member_labels("Cowboy", "ChatGPT_Image_2")["name"] == "Cowboy_ChatGPT_Image_2"


def test_the_label_reads_dataclass_records_as_the_routes_pass_them():
    references = [ReferenceEntity(reference_id="prop", name="Prop", members=[
        ReferenceMember(member_id="a", asset_id="x", name="Sonder"),
        ReferenceMember(member_id="b", asset_id="y", name="Sonder", handle="Bottle")])]
    labels = reference_member_display_names(references)
    assert [labels[key]["text"] for key in ("a", "b")] == ["Prop · Sonder", "Prop · Sonder @Bottle"]


def test_an_item_label_groups_members_under_their_reference():
    result = _node(f"""
const mod = await import({json.dumps(RESOLUTION)});
const names = mod.referenceMemberDisplayNames({json.dumps(_library())});
const ref = (id) => ({{ member_id: id }});
console.log(JSON.stringify([
  mod.referenceItemLabel([ref('c-sheet'), ref('sheet')], names),
  mod.referenceItemLabel([ref('sheet'), ref('bottle'), ref('c-sheet')], names),
  mod.referenceItemLabel([ref('k-3'), ref('k-4')], names),
  mod.referenceItemLabel([ref('gone')], names),
  mod.referenceItemLabel([ref('sheet')], undefined),
]));
""")
    assert result == [
        "Cowboy · Cowboy Character Sheet + Prop · Weapon_Sheet",
        "Prop · Weapon_Sheet, Sonder + Cowboy · Cowboy Character Sheet",
        "Tavern Keeper",
        "",
        "",
    ]


def _attach(scene, scope="global", references=None):
    return _node(f"""
const mod = await import({json.dumps(CHIPS)});
const result = mod.promptReferenceSourceOptions({{
    scene: {json.dumps(scene)},
    references: {json.dumps(references or _library())},
    semanticUnits: [],
    profileId: "minimax_h3_ref@1",
    scope: {json.dumps(scope)},
    resolvedProfile: {{physical_populations: [{{key: "pictures"}}]}},
}});
console.log(JSON.stringify({{ physical: result.physicalOptions, items: result.itemOptions }}));
""")


def _scene(items, *, lanes=2, compatible=("minimax_h3_ref@1",), start=None, end=None):
    scene = {
        "duration_frames": 100, "reference_lane_count": lanes,
        "reference_lane_configs": [{} for _ in range(lanes)],
        "reference_lane_recipes": [
            {"lane_id": f"pic-{index}", "recipe": {"soft": {
                "physical_population": "pictures",
                "compatible_profiles": list(compatible)}}} for index in range(lanes)],
        "reference_items": items,
    }
    if start is not None:
        scene["_context_consumer_start"] = start
        scene["_context_consumer_end"] = end
    return scene


def _item(item_id, lane, start, end, *members):
    return {"reference_item_id": item_id, "lane_index": lane, "start_frame": start,
            "end_frame": end, "members": [{"entity_id": "x", "member_id": m} for m in members]}


def test_attach_lists_each_member_once_by_its_own_label():
    """The bug: "Picture source: Prop" repeated per staged copy and member."""
    scene = _scene([
        _item("i1", 0, 0, 40, "sheet", "bottle"),
        _item("i2", 0, 50, 90, "sheet", "c-sheet"),
        _item("i3", 1, 0, 40, "bottle"),
    ])
    result = _attach(scene)
    assert result["physical"] == [
        ["physical:picture:sheet", "Picture: Prop · Weapon_Sheet", True],
        ["physical:picture:bottle", "Picture: Prop · Sonder", True],
        ["physical:picture:c-sheet", "Picture: Cowboy · Cowboy Character Sheet", True],
    ]
    assert [row[1] for row in result["items"]] == [
        "Lane 1: Prop · Weapon_Sheet, Sonder",
        "Lane 1: Prop · Weapon_Sheet + Cowboy · Cowboy Character Sheet",
        "Lane 2: Prop · Sonder",
    ]


def test_a_section_shows_the_best_copy_and_flags_two_winning_slots():
    """Selection 0-30: i1 and i3 win their lanes; i2 is outside the window."""
    scene = _scene([
        _item("i1", 0, 0, 40, "sheet", "bottle"),
        _item("i2", 0, 50, 90, "c-sheet", "bottle"),
        _item("i3", 1, 0, 40, "bottle"),
    ], start=0, end=30)
    result = _attach(scene, scope="section")
    rows = {row[0]: row for row in result["physical"]}
    assert rows["physical:picture:sheet"] == [
        "physical:picture:sheet", "Picture: Prop · Weapon_Sheet — In window", True]
    # Staged three times: two copies win, one is outside. Shown once, from the
    # best copy, and flagged because two slots would carry it.
    assert rows["physical:picture:bottle"] == [
        "physical:picture:bottle", "Picture: Prop · Sonder — In window — in 2 slots", True]
    assert rows["physical:picture:c-sheet"] == [
        "physical:picture:c-sheet", "Picture: Cowboy · Cowboy Character Sheet — Outside window", False]


def test_an_incompatible_lane_says_so_with_the_same_dash():
    result = _attach(_scene([_item("i1", 0, 0, 40, "sheet")], lanes=1,
                            compatible=("generic@1",)))
    assert result["physical"] == [
        ["physical:picture:sheet", "Picture: Prop · Weapon_Sheet — incompatible prompt format", False]]
    assert result["items"][0][1] == "Lane 1: Prop · Weapon_Sheet — incompatible prompt format"


def test_a_member_missing_from_the_library_reads_as_its_reference():
    """A deleted member of a Reference that still exists keeps that Reference's
    name; with the Reference gone too, the raw id is all that is left."""
    item = _item("i1", 0, 0, 40, "deleted-member")
    item["members"][0]["entity_id"] = "prop"
    result = _attach(_scene([item], lanes=1))
    assert result["physical"] == [["physical:picture:deleted-member", "Picture: Prop", True]]
    assert result["items"][0][1] == "Lane 1: Prop"
    orphan = _attach(_scene([_item("i1", 0, 0, 40, "deleted-member")], lanes=1))
    assert orphan["physical"] == [["physical:picture:deleted-member", "Picture: deleted-member", True]]
    assert orphan["items"][0][1] == "Lane 1: Reference"
