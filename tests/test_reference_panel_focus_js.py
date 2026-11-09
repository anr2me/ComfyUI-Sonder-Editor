"""Reference Lane Setup keyboard focus, on the real panel and host.

Phase 3a of the UX, QoL and Correctness Pass: the first invalid control takes
focus once per lane (bug G4-F01 focused the last), a rebuild gives focus back
to the control that held it, and a template commit lands on the control focus
was moving to, or on the Template select when that control is gone.

The harness is ``run_item_panel``: a real ``EditorWidget`` and panel on a
minimal DOM whose focus refuses disabled and detached nodes and drops to the
body when the focused node is removed, so "focus survived" cannot pass by the
old node simply staying `activeElement`.
"""

from test_reference_panel_item_writes_js import _MEMBERS, fixture_project, run_item_panel


def _two_invalid_roles():
    """Both image-lane items carry a role the generic format does not declare."""
    project = fixture_project()
    lane = project.scenes[0].reference_lane_recipes[0]
    lane.recipe["soft"]["role_fields"] = ["role"]
    project.scenes[0].reference_items[1].members[0]["role"] = "bogus"
    return project


_FOCUS = _MEMBERS + """
const active = () => document.activeElement;
const where = () => {
  const node = active();
  if (!node || node === document.body) return 'body';
  if (!node.isConnected) return 'detached';
  return node.dataset.focusKey || node.dataset.referenceItemId || node.tagName;
};
const keyed = (key) => nodes().find((n) => n.dataset.focusKey === key);
const picker = () => nodes().find((n) => n.dataset.sonderReferenceTemplate === '1');
// A browser blur: focus leaves while the event runs, then lands on the next
// control if that control is still in the document.
const tabFrom = (from, to) => {
  document.activeElement = document.body;
  from.dispatch('blur', { relatedTarget: to });
  if (to && to.isConnected) to.focus();
};
const SCHEMA = [
  { key: 'mode', label: 'Mode', type: 'enum', values: ['one', 'two'], group: 'Assembly', section: 'hard' },
  { key: 'size', label: 'Size', type: 'int', group: 'Sizing', section: 'hard' },
];
"""


def test_lane_setup_focuses_the_first_invalid_role_not_the_last(tmp_path):
    """G4-F01: two items with invalid roles; the earlier item's role takes focus."""
    result = run_item_panel(_FOCUS + """
    w._promptContextCatalog = { schema_version: 1 };
    mount();
    await tick();
    return { focused: where(), invalid: active()?.dataset?.sonderInvalid === '1',
      connected: active()?.isConnected === true };
    """, tmp_path, project=_two_invalid_roles())
    assert result == {"focused": "role:item-1:member-a", "invalid": True, "connected": True}


def test_an_invalid_role_outranks_an_invalid_recipe_field(tmp_path):
    """The recipe form is drawn below the items, so a role comes first."""
    project = _two_invalid_roles()
    project.scenes[0].reference_lane_recipes[0].recipe["hard"]["mode"] = "retired"
    result = run_item_panel(_FOCUS + """
    w._referenceRecipeFieldSchema = SCHEMA;
    w._promptContextCatalog = { schema_version: 1 };
    mount();
    await tick();
    return where();
    """, tmp_path, project=project)
    assert result == "role:item-1:member-a"


def test_an_invalid_recipe_field_takes_focus_itself_when_no_role_is_invalid(tmp_path):
    """Not the group's first control: the unsupported select."""
    project = fixture_project()
    project.scenes[0].reference_lane_recipes[0].recipe["hard"]["mode"] = "retired"
    result = run_item_panel(_FOCUS + """
    w._referenceRecipeFieldSchema = [
      { key: 'size', label: 'Size', type: 'int', group: 'Assembly', section: 'hard' },
      ...SCHEMA.slice(0, 1)];
    w._promptContextCatalog = { schema_version: 1 };
    mount();
    await tick();
    return { focused: where(), invalid: active()?.dataset?.sonderInvalid === '1' };
    """, tmp_path, project=project)
    assert result == {"focused": "recipe:mode", "invalid": True}


def test_a_slow_catalog_defers_the_one_autofocus_to_the_role(tmp_path):
    """While the catalog loads no role can be judged, so nothing is focused
    early -- not even an invalid recipe field -- and the role gets it once the
    catalog arrives. Later redraws never take focus again."""
    project = _two_invalid_roles()
    project.scenes[0].reference_lane_recipes[0].recipe["hard"]["mode"] = "retired"
    result = run_item_panel(_FOCUS + """
    w._referenceRecipeFieldSchema = SCHEMA;
    mount();
    await tick();
    const whileLoading = where();
    w._promptContextCatalog = { schema_version: 1 };
    handle.refresh();
    await tick();
    const ready = where();
    active().blur();
    handle.refresh();
    await tick();
    return { whileLoading, ready, again: where() };
    """, tmp_path, project=project)
    assert result == {"whileLoading": "body", "ready": "role:item-1:member-a", "again": "body"}


def test_a_locked_lane_focuses_nothing(tmp_path):
    """A lock disables every role and recipe control, and a disabled control
    cannot take focus: the panel opens with focus left where it was."""
    project = _two_invalid_roles()
    project.scenes[0].reference_lane_configs[0].locked = True
    result = run_item_panel(_FOCUS + """
    w._promptContextCatalog = { schema_version: 1 };
    w._isLaneLocked = () => true;
    mount();
    await tick();
    return where();
    """, tmp_path, project=project)
    assert result == "body"


def test_the_autofocus_never_takes_focus_from_a_control_in_the_panel(tmp_path):
    """The catalog-ready redraw finds the author already in the panel."""
    result = run_item_panel(_FOCUS + """
    mount();
    await tick();
    picker().focus();
    w._promptContextCatalog = { schema_version: 1 };
    handle.refresh();
    await tick();
    return where();
    """, tmp_path, project=_two_invalid_roles())
    assert result == "template"


def test_a_catalog_that_failed_to_load_spends_the_autofocus_on_the_recipe(tmp_path):
    """No role can be judged without the catalog, so the one turn goes to an
    invalid recipe field rather than waiting forever."""
    project = _two_invalid_roles()
    project.scenes[0].reference_lane_recipes[0].recipe["hard"]["mode"] = "retired"
    result = run_item_panel(_FOCUS + """
    w._referenceRecipeFieldSchema = SCHEMA;
    w._referencesError = 'catalog unavailable';
    mount();
    await tick();
    return where();
    """, tmp_path, project=project)
    assert result == "recipe:mode"


def test_an_invalid_catalog_list_is_reached_through_its_remove_button(tmp_path):
    """A checkbox list is marked invalid on its wrapper; the control that
    fixes it is the unsupported value's Remove."""
    project = fixture_project()
    project.scenes[0].reference_lane_recipes[0].recipe["soft"]["role_fields"] = ["retired_field"]
    result = run_item_panel(_FOCUS + """
    w._referenceRecipeFieldSchema = [
      { key: 'role_fields', label: 'Per-member options', type: 'string_list', group: 'Prompt', section: 'soft' }];
    w._promptContextCatalog = { schema_version: 1 };
    mount();
    await tick();
    return where();
    """, tmp_path, project=project)
    assert result == "recipe:role_fields:remove:retired_field"


def test_every_focus_key_in_the_panel_is_unique(tmp_path):
    result = run_item_panel(_FOCUS + """
    w._referenceRecipeFieldSchema = SCHEMA;
    w._promptContextCatalog = { schema_version: 1 };
    mount();
    const keys = nodes().map((n) => n.dataset.focusKey).filter(Boolean);
    return { count: keys.length, unique: new Set(keys).size };
    """, tmp_path, project=_two_invalid_roles())
    assert result["count"] > 10 and result["unique"] == result["count"]


_TEMPLATES = """
w._referenceRecipePresets = [
  { id: 'a', name: 'Image A', media_kind: 'image', builtIn: true },
  { id: 'b', name: 'Image B', media_kind: 'image', builtIn: true },
];
w._referenceRecipeFieldSchema = SCHEMA;
w._promptContextCatalog = { schema_version: 1 };
w._saveLaneConfig = async (entries) => {
  w.activeScene.reference_lane_recipes[0] = entries[0].referenceRecipe;
};
"""


def test_a_template_switch_keeps_focus_on_the_template_select(tmp_path):
    """Pointer pick and Enter both rebuild the panel; focus stays on the select."""
    result = run_item_panel(_FOCUS + _TEMPLATES + """
    mount();
    await tick();
    picker().focus();
    picker().dispatch('pointerdown'); picker().value = 'a'; picker().dispatch('change');
    await settle();
    const pointer = where();
    const select = picker();
    select._sonderTemplateKeydown({ key: 'ArrowDown' });
    select.value = 'b'; select.dispatch('change');
    select._sonderTemplateKeydown({ key: 'Enter' });
    await settle();
    return { pointer, enter: where(), recipe: w.activeScene.reference_lane_recipes[0].recipe_id };
    """, tmp_path)
    assert result == {"pointer": "template", "enter": "template", "recipe": "b"}


def test_leaving_the_template_select_for_a_summary_lands_on_that_summary(tmp_path):
    """A blur commit rebuilds the panel; focus lands on the rebuilt control it
    was moving to. The old distance count skipped summaries entirely."""
    result = run_item_panel(_FOCUS + _TEMPLATES + """
    mount();
    await tick();
    const select = picker();
    select.focus();
    select._sonderTemplateKeydown({ key: 'ArrowDown' });
    select.value = 'a'; select.dispatch('change');
    tabFrom(select, keyed('group:Assembly'));
    await settle();
    return { focused: where(), recipe: w.activeScene.reference_lane_recipes[0].recipe_id };
    """, tmp_path)
    assert result == {"focused": "group:Assembly", "recipe": "a"}


def test_a_target_the_new_template_removes_falls_back_to_the_template_select(tmp_path):
    """Detaching a built-in lane removes Edit as custom, where focus was heading."""
    project = fixture_project()
    project.scenes[0].reference_lane_recipes[0].recipe_id = "a"
    result = run_item_panel(_FOCUS + _TEMPLATES + """
    mount();
    await tick();
    const select = picker();
    select.focus();
    select._sonderTemplateKeydown({ key: 'ArrowUp' });
    select.value = ''; select.dispatch('change');
    tabFrom(select, keyed('edit-custom'));
    await settle();
    return { focused: where(), forkGone: !keyed('edit-custom') };
    """, tmp_path, project=project)
    assert result == {"focused": "template", "forkGone": True}


def test_a_blur_with_nowhere_to_go_leaves_focus_alone(tmp_path):
    """A window blur or a click on blank space has no relatedTarget: the commit
    still happens, and nothing is focused on the author's behalf."""
    result = run_item_panel(_FOCUS + _TEMPLATES + """
    mount();
    await tick();
    const select = picker();
    select.focus();
    select._sonderTemplateKeydown({ key: 'ArrowDown' });
    select.value = 'a'; select.dispatch('change');
    tabFrom(select, null);
    await settle();
    return { focused: where(), recipe: w.activeScene.reference_lane_recipes[0].recipe_id };
    """, tmp_path)
    assert result == {"focused": "body", "recipe": "a"}


def test_a_rebuild_gives_focus_back_to_the_focused_button(tmp_path):
    """A refresh while a button holds focus no longer drops it on the body."""
    result = run_item_panel(_FOCUS + """
    mount();
    await tick();
    keyed('item:item-2:mute').focus();
    handle.refresh();
    return where();
    """, tmp_path)
    assert result == "item:item-2:mute"


def test_add_member_opens_the_picker_without_moving_focus_elsewhere(tmp_path):
    """The clicked button is replaced by the picker; with no surviving key the
    restore does nothing rather than jumping to the Template select below."""
    result = run_item_panel(_FOCUS + """
    mount();
    await tick();
    const add = memberButtons(50, '+ Add member')[0];
    add.focus();
    add.dispatch('click');
    const opened = { focused: where(), picker: nodes().some((n) => n.title === 'Stage this member') };
    const stage = nodes().find((n) => n.title === 'Stage this member');
    stage.focus();
    stage.dispatch('click');
    return { opened, staged: where(), closed: !nodes().some((n) => n.title === 'Stage this member') };
    """, tmp_path)
    assert result == {"opened": {"focused": "body", "picker": True},
                      "staged": "body", "closed": True}


def test_a_focused_recipe_select_is_not_restored_so_a_rollback_repaints(tmp_path):
    """Restoring focus to a recipe select or text field would hold the host's
    gated refresh (it defers while such a field has focus) and leave a refused
    value on screen; those controls are deliberately not restored."""
    result = run_item_panel(_FOCUS + """
    w._referenceRecipeFieldSchema = SCHEMA;
    mount();
    await tick();
    const select = keyed('recipe:mode');
    select.focus();
    const busyBefore = w._managementPanelBusy(handle.element);
    handle.refresh();
    return { busyBefore, focused: where(), busyAfter: w._managementPanelBusy(handle.element) };
    """, tmp_path)
    # Focused, the select holds the gate; rebuilt, it is not given focus back,
    # so the next reconcile or rollback repaint is not held behind it.
    assert result == {"busyBefore": True, "focused": "body", "busyAfter": False}
