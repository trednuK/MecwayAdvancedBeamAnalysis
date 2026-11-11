# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# beam_mecway.py
# The only module that calls mw and solution. Every function here runs on
# Mecway's script thread: the window hands them over (beam_gui._MwCaller).
#
#   sources(mw)                     named selections and the current selection
#   beam_elements(mw, source)       the line elements of a choice
#   result_sets(solution)           time steps and modes
#   with_configurations(sets, head) every result set per configuration of the .liml
#   pick_route(...)                 element values, node values or kinematics
#   read_chunk(...)                 section forces of some elements
#   write_results(...)              node values into the solution
#   solve_again(...)                the model solved anew, after the user said yes
#   config_paths()                  Mecway's settings files (up axis of 3D)
#   show_marker, move_marker, remove_marker
#                                   the position as a temporary element
#
# Measured on Mecway 35 (tests/mecway/e2e.py): node_value and element_node_value take
# the keywords time_step and mode, and give None where a variable has no value.
# element_node_value counts from 1: ends 1 and n, on line2 (research note E7) and on
# line3 with a solution of Code-Aster, where n = 3 is the second corner.
#
# Compatible with IronPython 2.7.9.

import traceback

from beam_utils import MARKER_PREFIX, MECWAY_VARS, is_marker_name, log, tr

# Reading forces stops Mecway's message pump; the window asks for this many
# elements per request, so that Mecway stays alive on a long chain.
CHUNK = 200


def _element_of(item):
    """Element number of a selection item: an int or a FaceId."""
    return int(getattr(item, "element_id", item))


def _line_elements(mw, ids):
    """(line elements, number of others) of element numbers."""
    lines, others = [], 0
    for eid in ids:
        try:
            shape = u"{0}".format(mw.element_shape(eid)).lower()
        except Exception:
            others += 1
            continue
        if shape.startswith("line"):
            lines.append(eid)
        else:
            others += 1
    return lines, others


def sources(mw):
    """[{"key": name or None, "name", "count", "faces", "uncertain"}]: the current
    selection first (key None), then the named selections with line elements.
    named_selection gives ints for a node selection and for an element selection
    alike, FaceIds for a face selection (measured, Mecway 35, tests/mecway/e2e.py
    case selections); no call tells the kind. So only "faces" is decided here; the
    window drops node selections and marks the other int selections "uncertain" by
    the head of the saved file (beam_utils.scan_head)."""
    out = [{"key": None, "name": None, "faces": False, "uncertain": False,
            "count": len(_line_elements(mw, current_selection(mw))[0])}]
    try:
        names = list(mw.named_selections())
    except Exception:
        names = []
    for name in names:
        try:
            items = list(mw.named_selection(name))
        except Exception:
            continue
        if not items:
            continue
        faces = hasattr(items[0], "element_id")
        ids = sorted(set(_element_of(i) for i in items))
        lines, _ = _line_elements(mw, ids)
        if lines:
            out.append({"key": name, "name": name, "count": len(lines),
                        "faces": faces, "uncertain": False})
    return out


def current_selection(mw):
    """Element numbers of the current selection, in elements or in faces."""
    ids = [int(e) for e in (mw.selected_elements() or [])]
    if not ids:
        ids = [_element_of(f) for f in (mw.selected_faces() or [])]
    return sorted(set(ids))


def beam_elements(mw, key):
    """{"ids", "others", "file", "n_nodes", "n_elements"} of a choice; key None
    is the current selection. The order is the chain's job."""
    if key is None:
        ids = current_selection(mw)
    else:
        ids = sorted(set(_element_of(i) for i in mw.named_selection(key)))
    lines, others = _line_elements(mw, ids)
    n_nodes, n_elements = marker_counts(mw)
    return {"ids": lines, "others": others, "file": file_path(mw),
            "n_nodes": len(mw.all_nodes()) - n_nodes,
            "n_elements": len(mw.all_elements()) - n_elements}


def file_path(mw):
    try:
        return mw.file_name() or u""
    except Exception:
        return u""


def result_sets(solution):
    """[{"kind", "number", "value", "time_step", "mode", "configuration"}], at
    least the active result; None without a solution."""
    if solution is None:
        return None
    out = []
    try:
        steps = list(solution.time_steps() or [])
    except Exception:
        steps = []
    for step in steps:
        value = None
        try:
            value = float(solution.time(step))
        except Exception:
            pass
        out.append({"kind": "step", "number": step, "value": value,
                    "time_step": step, "mode": None, "configuration": None})
    try:
        modes = list(solution.modes() or [])
    except Exception:
        modes = []
    for mode in modes:
        out.append(_mode_set(solution, mode))
    if not out:
        out.append({"kind": "active", "number": None, "value": None,
                    "time_step": None, "mode": None, "configuration": None})
    return out


def with_configurations(sets, head):
    """The result sets once per configuration of the file (head of beam_utils.scan_head),
    each with "configuration" and "switch"; without configurations the sets as they are.
    No mw call. Also one configuration is named and switched to: what Mecway has active
    cannot be read, so the tool never assumes it. The steps and modes are those of the
    solution read at the time; another configuration without them gives no values."""
    names = list((head or {}).get("configurations") or [])
    if not names:
        return list(sets)
    out = []
    for name in names:
        for item in sets:
            item = dict(item)
            item["configuration"], item["switch"] = name, True
            out.append(item)
    return out


def _mode_set(solution, mode):
    value, kind = None, "mode"
    try:
        value = float(solution.frequency(mode))
    except Exception:
        try:
            value = float(solution.buckling_factor(mode))
            kind = "buckling"
        except Exception:
            pass
    return {"kind": kind, "number": mode, "value": value, "time_step": None,
            "mode": mode, "configuration": None}


def activate(mw, result, solution=None):
    """Switch Mecway to the configuration of a result set. Mecway's active configuration
    is in neither the file nor the API, so every read and write of a set with a
    configuration calls this first, also per chunk: the user may switch in Mecway
    meanwhile (whether a call costs a redraw in Mecway on a long chain is NOT verified).
    Measured on Mecway 35 (tests/mecway/e2e.py, one solve per configuration): `solution`
    follows set_active_configuration, the solution of the other configuration stays, and
    a name Mecway does not have raises ("does not exist") and leaves the active one.
    With solution: the set must be one of that configuration's own (with_configurations
    lists the steps and modes of one solution for all). Errors go up, to the status line."""
    if not result.get("switch"):
        return
    name = result["configuration"]
    try:
        mw.set_active_configuration(name)
    except Exception as e:
        raise RuntimeError(tr(u"Mecway kan configuratie {0} niet actief zetten ({1}). Sla het "
                              u"model op en kies Opnieuw lezen.",
                              u"Mecway cannot make configuration {0} active ({1}). Save the "
                              u"model and choose Read again.").format(name, e))
    if solution is None:
        return
    own = result_sets(solution) or []
    if not any((s["kind"], s["number"]) == (result["kind"], result["number"]) for s in own):
        raise RuntimeError(tr(u"Configuratie {0} heeft dit resultaat niet: de lijst komt uit de "
                              u"oplossing die actief was bij het lezen. Kies Opnieuw lezen.",
                              u"Configuration {0} does not have this result: the list comes "
                              u"from the solution that was active when it was read. Choose "
                              u"Read again.").format(name))


def _keywords(result):
    kw = {}
    if result.get("time_step") is not None:
        kw["time_step"] = result["time_step"]
    if result.get("mode") is not None:
        kw["mode"] = result["mode"]
    return kw


def _element_value(solution, var, eid, k, kw):
    try:
        value = solution.element_node_value(var, eid, k, **kw)
    except Exception:
        return None
    return None if value is None else float(value)


def _node_value(solution, var, nid, kw):
    try:
        value = solution.node_value(var, nid, **kw)
    except Exception:
        return None
    return None if value is None else float(value)


def _ends(mw, eid):
    """First and last node of an element, with the number of nodes."""
    nodes = list(mw.nodes(eid))
    return nodes[0], nodes[-1], len(nodes)


_FORCE_VARS = tuple(MECWAY_VARS[k] for k in ("N", "Vv", "Vw", "T", "Mv", "Mw"))
_MOTION_VARS = ("displx", "disply", "displz", "rotx", "roty", "rotz")


def pick_route(mw, solution, eid, result):
    """Where the forces of this result come from, judged on one element:
    "element", "node" (a solver without element values, research note E4),
    "kinematics" (no forces stored, E5, E6), or None."""
    kw = _keywords(result)
    first, last, n = _ends(mw, eid)
    if all(_element_value(solution, v, eid, 1, kw) is not None and
           _element_value(solution, v, eid, n, kw) is not None
           for v in _FORCE_VARS):
        return "element"
    if all(_node_value(solution, v, first, kw) is not None and
           _node_value(solution, v, last, kw) is not None
           for v in _FORCE_VARS):
        return "node"
    if all(_node_value(solution, v, first, kw) is not None and
           _node_value(solution, v, last, kw) is not None
           for v in _MOTION_VARS):
        return "kinematics"
    return None


def read_chunk(mw, solution, ids, result, route):
    """{"raw": {eid: {var: [end 1, end 2]}}, "nodes": {nid: xyz}, "missing":
    [eid]} of some elements. In the kinematics route raw holds displacement and
    rotation under their Mecway names."""
    activate(mw, result, solution)
    kw = _keywords(result)
    raw, nodes, missing = {}, {}, []
    for eid in ids:
        first, last, n = _ends(mw, eid)
        values = {}
        complete = True
        if route == "element":
            for var in _FORCE_VARS:
                pair = [_element_value(solution, var, eid, 1, kw),
                        _element_value(solution, var, eid, n, kw)]
                complete = complete and None not in pair
                values[var] = pair
        else:
            for var in (_MOTION_VARS if route == "kinematics" else _FORCE_VARS):
                pair = [_node_value(solution, var, first, kw),
                        _node_value(solution, var, last, kw)]
                complete = complete and None not in pair
                values[var] = pair
        if not complete:
            missing.append(eid)
        raw[str(eid)] = values
        for nid in (first, last):
            if nid not in nodes:
                nodes[nid] = [float(c) for c in _xyz(mw.node(nid))]
    return {"raw": raw, "missing": missing,
            "nodes": dict((str(k), v) for k, v in nodes.items())}


def _xyz(point):
    """Coordinates of mw.node: a Vector with x, y, z or a sequence."""
    if hasattr(point, "x"):
        return point.x, point.y, point.z
    return point


def write_results(mw, solution, values, result):
    """values {variable: {nid as text: value}} into the solution, as node variables
    with 0 on every other node. Measured on Mecway 35 (tests/mecway/e2e.py): set_variable
    takes a new name, wants one value per node of the solution in the order of
    all_nodes, which is 1..n also when the numbers are out of order along the beam
    and in the file; a file with gaps Mecway 35 refuses with a dialog. It leaves the
    other variables and the other time steps and modes alone, and the variable is in
    the file after a save. Node values only.

    First the sizes: {"solution": nodes, "model": nodes without the marker}. When they
    differ nothing is written: the node numbers of file and solution need not be the
    same nodes any more. {"names": [...]} when written, {"refused": text, "solution",
    "model"} when not. Any other error of Mecway goes up, to the log and the status
    line.

    Measured on Mecway 35, own models (tests/test_mecway_e2e.py): set_variable raises a
    null reference on a solution of the bridge with its shells of "Section in results".
    A solution with more nodes as such is no reason: Mecway's own solution with the
    same shells takes it, and so does a plain Code-Aster solution. Why the first refuses
    is NOT verified. A refusal after the first variable leaves "written": the names
    before it; that has not been seen."""
    activate(mw, result, solution)
    sizes = node_counts(mw, solution)
    out = dict(sizes)
    if sizes["solution"] != sizes["model"]:
        out["refused"] = "size"
        return out
    return set_all(solution, values, result, out)


def node_counts(mw, solution):
    """{"solution", "model"}: the nodes of both, the marker not counted."""
    return {"solution": len(list(solution.all_nodes())),
            "model": len(list(mw.all_nodes())) - marker_counts(mw)[0]}


def _null_reference(error):
    """Mecway's refusal: a .NET null reference, which IronPython hands on as a
    SystemError (measured). Told by the type, the text depends on the language."""
    inner = getattr(error, "clsException", None)
    return inner is not None and inner.GetType().Name == "NullReferenceException"


def set_all(solution, values, result, out=None):
    """The writing of write_results, without the check of the sizes. out: the dict to
    answer in."""
    out = {} if out is None else out
    ids = [int(n) for n in solution.all_nodes()]
    kw = _keywords(result)
    done = []
    for name in sorted(values):
        row = values[name]
        try:
            solution.set_variable(name, [float(row.get(str(n), 0.0)) for n in ids], **kw)
        except SystemError as e:
            if not _null_reference(e):
                raise
            log(u"WARNING: set_variable refused: {0}".format(e))
            out["refused"] = u"{0}".format(e)
            out["written"] = done
            return out
        done.append(name)
    out["names"] = done
    return out


def solve_again(mw, solution, result):
    """Solve the model anew and answer with the new result sets; only after the user
    said yes, and never on a solution from another program, which it would replace."""
    activate(mw, result)
    mw.solve()
    return result_sets(solution)


# ==========================================
# THE POSITION AS A TEMPORARY ELEMENT
# ==========================================
# Research note par. 7 E1 (measured on Mecway 35): an element made by the API is
# visible in the model view only, and the solution stays valid before, during
# and after. Approach of bridge_section_model.py in MecwayAsterBridge:
#
# Every answer comes from the model, never from remembered numbers: Mecway
# renumbers after a delete, also one of the user, so a remembered node number
# can belong to real geometry by then.
#
# WHAT IS OURS. No API marks or lists anything, so: a component in the series
# MARKER_PREFIX, MARKER_PREFIX(2) ... with a material in that series that is
# exactly the soft Plate below, or with none; such a material itself. A user's
# component or material with that name and other content stays. A material
# another component uses stays as well: delete_material of a material in use
# does not fail, it silently takes it off that component (measured).
#
# ORDER. delete_nodes also deletes every element on the node: first elements,
# then the nodes no element uses, then the empty component, then the material.
#
# THE MATERIAL. Without one an internal solve is silently empty; no density, so
# gravity and mass stay those of the beams. A solve with the plate in it, measured on
# Mecway 35: static, internal and CCX, gives the same section forces (2e-13) and a
# solution with one element more; a frequency analysis gives an empty solution, no
# error. The window says to turn the option off before solving.
#
# UNDO (measured). The script API has no undo call. On its own the marker makes no
# Undo step and a save stays byte-equal. But when the user makes a change Undo can
# take back while the marker is shown, or presses Undo while it is shown, Mecway's
# Undo or Redo puts the marker back into the model, also after the panel is closed;
# only the next start of the panel removes it. The manuals say so at the option,
# which stays off by default.
_SOFT_TYPES = (("geometric", "Plate"), ("mechanical", "Isotropic"),
               ("density", "None"))
_SOFT_VALUES = (("thickness", 1.0e-4), ("youngsmodulus", 1.0e3),
                ("poissonratio", 0.3))
_REL_TOL = 1.0e-9
_CORNERS = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))


def _series_name(k):
    return MARKER_PREFIX if k == 1 else u"{0}({1})".format(MARKER_PREFIX, k)


def _material_exists(mw, name):
    """material_type raises on a missing material (measured)."""
    try:
        mw.material_type(name, "geometric")
    except Exception:
        return False
    return True


def _material_series(mw):
    names = []
    while _material_exists(mw, _series_name(len(names) + 1)):
        names.append(_series_name(len(names) + 1))
    return names


def _is_our_material(mw, name):
    if not is_marker_name(name):
        return False
    try:
        for group, value in _SOFT_TYPES:
            if mw.material_type(name, group) != value:
                return False
        for prop, value in _SOFT_VALUES:
            if abs(float(mw.material_property(name, prop)) - value) > _REL_TOL * value:
                return False
    except Exception:
        return False
    return True


def _our_components(mw):
    out = []
    for comp in mw.components():
        if is_marker_name(comp):
            material = mw.material(comp)
            if material is None or _is_our_material(mw, material):
                out.append(comp)
    return out


def _our_materials(mw, comps):
    """Our materials no component outside comps refers to, also one that lost
    its component."""
    keep = set(mw.material(c) for c in mw.components() if c not in comps)
    names = set(_material_series(mw))
    names.update(mw.material(c) for c in comps)
    return sorted(n for n in names if n not in keep and _is_our_material(mw, n))


def marker_counts(mw):
    """(nodes, elements) of the marker in the model now."""
    try:
        nodes, elements = set(), 0
        for comp in _our_components(mw):
            for e in mw.component_elements(comp):
                elements += 1
                nodes.update(int(n) for n in mw.nodes(e))
        return len(nodes), elements
    except Exception:
        return 0, 0


def _delete_added(mw, elements, nodes, components, materials):
    alive = set(int(e) for e in mw.all_elements())
    gone = [e for e in elements if e in alive]
    if gone:
        mw.delete_elements(gone)
    used = set()
    for e in mw.all_elements():
        used.update(int(n) for n in mw.nodes(e))
    existing = set(int(n) for n in mw.all_nodes())
    free = sorted(n for n in set(nodes) if n in existing and n not in used)
    if free:
        mw.delete_nodes(free)
    present = list(mw.components())
    for comp in components:
        if comp in present and not list(mw.component_elements(comp)):
            mw.delete_component(comp)
    for material in materials:
        mw.delete_material(material)


def _display(mw):
    """Redraw; a failure here is no reason to undo anything."""
    try:
        mw.display()
    except Exception as e:
        log(u"WARNING: display: {0}".format(e))


def clear_leftover(mw):
    """Start of the window: {"found", "removed"}: what a crash or a saved file
    left behind is removed like a marker of a moment ago."""
    try:
        comps = _our_components(mw)
        found = bool(comps or _our_materials(mw, comps))
    except Exception:
        return {"found": False, "removed": True}
    return {"found": found, "removed": remove_marker(mw) if found else True}


def remove_marker(mw):
    """Remove the marker, by what is in the model. Safe with nothing there and
    never raises: this also runs in a finally. True when none of it is left."""
    try:
        comps = _our_components(mw)
        materials = _our_materials(mw, comps)
        if not comps and not materials:
            return True
        elements, nodes = [], set()
        for comp in comps:
            for e in mw.component_elements(comp):
                elements.append(int(e))
                nodes.update(int(n) for n in mw.nodes(e))
        _delete_added(mw, elements, nodes, comps, materials)
    except Exception as e:
        log(u"ERROR: marker not removed: {0}".format(e))
        _display(mw)
        return False
    log(u"INFO: marker removed ({0} elements).".format(len(elements)))
    _display(mw)
    return True


def _corners(place):
    p, v, w, half = place["point"], place["v"], place["w"], place["half"]
    return [[p[i] + half * (a * v[i] + b * w[i]) for i in range(3)] for a, b in _CORNERS]


def show_marker(mw, Vector, place):
    """A plate across the beam at the place, in the model view; all or nothing.
    Raises after rolling back."""
    if not remove_marker(mw):
        raise RuntimeError("the earlier marker is still there")
    added = {"elements": [], "nodes": [], "components": [], "materials": []}
    try:
        material = mw.new_material(_series_name(len(_material_series(mw)) + 1))
        added["materials"].append(material)
        for group, value in _SOFT_TYPES:
            if value != "None":
                mw.set_material_type(material, group, value)
        for prop, value in _SOFT_VALUES:
            mw.set_material_property(material, prop, value)
        component = mw.new_component(MARKER_PREFIX) or MARKER_PREFIX
        added["components"].append(component)
        mw.set_material(component, material)
        ids = []
        for corner in _corners(place):
            ids.append(int(mw.new_node(Vector(corner[0], corner[1], corner[2]))))
            added["nodes"].append(ids[-1])
        added["elements"].append(int(mw.new_element("quad4", ids, component)))
    except Exception:
        log(u"ERROR: marker not made: {0}".format(traceback.format_exc()))
        try:
            _delete_added(mw, added["elements"], added["nodes"], added["components"],
                          added["materials"])
        except Exception as e:
            log(u"ERROR: marker rollback: {0}".format(e))
        raise
    mw.set_model_active()
    _display(mw)
    return {"component": component, "material": material}


def move_marker(mw, place):
    """Move the plate. False when it is not there any more; the nodes are looked
    up each time, because a delete by the user renumbers them."""
    comps = _our_components(mw)
    if len(comps) != 1:
        return False
    elements = list(mw.component_elements(comps[0]))
    if len(elements) != 1:
        return False
    nodes = [int(n) for n in mw.nodes(elements[0])]
    if len(nodes) != 4:
        return False
    for nid, corner in zip(nodes, _corners(place)):
        mw.set_node_x(nid, corner[0])
        mw.set_node_y(nid, corner[1])
        mw.set_node_z(nid, corner[2])
    _display(mw)
    return True


def solution_view(mw):
    """Back to the results. The active view cannot be read, so the window calls
    this only when it switched to the model view itself."""
    try:
        mw.set_solution_active()
    except Exception as e:
        log(u"WARNING: solution view: {0}".format(e))
    _display(mw)


def config_paths():
    """(user.config, mecway.exe.config) of the running Mecway, for
    beam_utils.UpAxis; None where .NET gives no path. Public .NET API, no mw
    call; measured on Mecway 35: the user file is the one of the running version.
    Resolved once per window: it reads the whole configuration."""
    paths = [None, None]
    try:
        import clr
        clr.AddReference("System.Configuration")
        from System.Configuration import ConfigurationManager, ConfigurationUserLevel
        levels = (ConfigurationUserLevel.PerUserRoamingAndLocal,
                  getattr(ConfigurationUserLevel, "None"))     # a keyword in Python
        for i, level in enumerate(levels):
            try:
                paths[i] = ConfigurationManager.OpenExeConfiguration(level).FilePath
            except Exception as e:      # a damaged user.config, among others
                log(u"WARNING: Mecway's settings file {0}: {1}".format(i + 1, e))
    except Exception as e:
        log(u"WARNING: Mecway's settings: {0}".format(e))
    return tuple(paths)
