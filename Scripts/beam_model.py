# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# beam_model.py
# The model behind the stresses: what only the saved .liml holds (orientation,
# materials, section shapes, loads), the chain of beam elements, and the section
# forces at an arc length. CPython 3; NOT in IronPython.
#
#   read_model(path, configuration)     Model; stops at <solution>
#   build_chain(model, element_ids)     Chain, ordered along shared nodes
#   forces_at(chain, raw, s)            section forces in the drawn axes
#   forces_from_kinematics(chain, raw)  end values from displacement and rotation
#   section_key(element)                key of the SectionField of an element
#   section_spec(model, element)        arguments of that SectionField
#
# A GENERAL SECTION has its shape in lines that MecwaySectionProps writes, under the
# material name, so build_chain checks that the shape still fits: section_direction
# when an element has another U or V than ** SECTIE-RICHTING says, in its own axes
# (reversed nodes count: the beam then sees the section mirrored), and shape_inertia
# when the outline does not give the material's iyy and izz.
#
# All values are SI: the .liml holds strings with units (section_profiles).
# Warnings and errors are codes with data, text in beam_utils: warnings are
# {"code", "data"}, ChainError and ModelError have .code and .data.
#
# ELEMENT AXES, measured on Mecway 35 (tests/test_beam_model.py): U from the first
# to the last node of the element. A vector is projected on the plane normal to U
# and turned about U by the angle, right-handed. Without a vector V = Z x U, along
# Z (within 0.01 %) V = Y, and an angle is ignored. W = U x V.
#
# SECTION FORCES in element axes, on the +U face, measured: (N, Vv, Vw) and (T, Mv,
# Mw) are the components along (U, V, W) of the force and the moment that the part
# on the +U side exerts on the rest. So dVv/ds = -q_v, dMw/ds = -Vv, dMv/ds = +Vw.
#
# DRAWN AXES. The section is drawn from one side along the chain, so
# ChainElement.frame has U along the chain. A reversed element has U = -U_e, and
# exactly one of V, W flips to stay right-handed: V = sv V_e, W = sw W_e, sv sw = -1.
# Which one: the pair nearest to the neighbour, carried across a kink. The physical
# point v V_e + w W_e is at (sv v, sw w), a mirrored outline, so section_spec mirrors
# the loops and section_key tells the two apart. With su = -1 for reversed:
#     N, T the same; Vv = su sv Vv_e; Vw = su sw Vw_e; Mv = sw Mv_e; Mw = sv Mw_e
# sigma and every invariant of a point are the same as in element axes, the tau
# components are (sw tau_v, sv tau_w) of the element axes. The values in element
# axes, as Mecway shows them, are also returned.
#
# DISTRIBUTED LOAD (research note par. 5.4). In element axes, x from the first node:
#   midpoint values (Mecway's own line2): N, Vv, Vw hold the value at h/2 and
#     N(x)  = N_fem  + q_u (h/2 - x)         Vv(x) = Vv_fem + q_v (h/2 - x)
#     Mw(x) = linear + q_v (h^2/12 - x(h-x)/2)
#     Mv(x) = linear - q_w (h^2/12 - x(h-x)/2)
#   end values: N, V linear between the ends, in M only the term -x(h-x)/2.
#   CCX line3 (node values): N and V are end values, but M at the corner nodes is
#     measured to carry the same q h^2/12 as Mecway's line2, so it gets the full
#     term. The note did not know this; with only -x(h-x)/2 M was 1 % off.
# Which one is decided per solution: r = (V(h) - V(0)) / (-q h) on the loaded elements.
# r near 0 is midpoint values, r near 1 end values; line2 and line3 are judged apart.
#   Code-Aster, measured through MecwayAsterBridge (tests/test_beam_aster.py): FORCE_POUTRE
#     gives exact end values on line2 with POU_D_E, POU_D_T and POU_D_TG, one element or
#     many, and on line3 with the setting POU_D_E: end values, -x(h-x)/2 is all that is
#     missing. See the table EXPECTED_LOADS there for gravity (PESANTEUR): end values
#     on POU_D_E, midpoint values with q h^2/12 on POU_D_TG. The detection tells them
#     apart; a mix of the two on POU_D_TG is neither, and gets the warning below.
# No correction, with a warning: anything else, an analysis other than S30 (a modal
# solution has q in the file and no load), an element with a flexible joint or an
# offset (the pattern holds for a plain element only), a load the tool does not know.
# CCX line2 gets a warning. Mecway's own solver gives no solution for line3 (measured:
# no nodes, no elements, no error).
#
# CONFIGURATIONS. read_model leaves out what is suppressed in the configuration given.
# Mecway's active configuration is in neither the file nor the API, so the window
# names one for every result when the file has configurations, and switches Mecway to
# it (beam_mecway.with_configurations). Without one, an item suppressed in some
# configuration is never used silently: a load switches the correction off on its
# elements (reason "configuration"), an orientation card refuses its elements
# (orientation_configuration); a joint, an offset or a point load counts as present,
# which only adds warnings. A name that is not in the file is configuration_missing.
# Measured on Mecway 35 (tests/mecway/e2e.py, case config): Mecway leaves out a line
# load and an orientation card suppressed in the active configuration, and the window
# gives the hand values in both configurations, inside an element too. Components and
# materials suppressed per configuration: NOT verified, not read.
#
# NOT verified in Mecway, read as MecwayAsterBridge reads them: suppressed="true",
# truss="true", the tag names in UNKNOWN_LOADS, a flexible joint or offset on a
# chain element. These only lead to a warning or a refused element.
#
# KINEMATICS (no section forces stored): N, Mv, Mw at the element ends from the
# Hermite field, as bridge_section_view of MecwayAsterBridge. Measured equal to the
# section forces of Mecway's own line2, including its q h^2/12, and on POU_D_E of
# Code-Aster the same.

import bisect
import math
import re
import xml.etree.ElementTree as ET

import section_profiles
from section_profiles import UnitError, parse_value, solver_props
from section_stress import SectionError

FORCE_VARS = ("tensileforce", "sheary", "shearz", "torsion", "bendingmomv", "bendingmomw")
MOTION_VARS = ("displx", "disply", "displz", "rotx", "roty", "rotz")
LINE_SHAPES = ("line2", "line3")
# The free text fields where MecwaySectionProps writes its lines
CUSTOM_TAGS = ("ccxmodeldefinition", "ccxstep")
# Distributed effects the tool does not know; point loads need no correction
UNKNOWN_LOADS = ("traction", "surfacepressure", "hydrostaticpressure", "centrifugal",
                 "elasticsupport", "compressiononly", "temperature2", "temperature3")
# Items a configuration can suppress that the tool uses
USED_TAGS = ("orientation", "flexiblejoint", "offset", "force", "moment", "gravity",
             "linepressure") + UNKNOWN_LOADS
STATIC = "S30"
ASTER = "aster"                     # Model.solver of a solution from Code-Aster
# Mecway's gradient limit for "along Z"
ALONG_Z = 1.0e-4
KINK_DEG = 0.01
TWIST_DEG = 0.01
CURVED = 1.0e-6                     # of the chord: a line3 that is not straight
LENGTH_TOL = 1.0e-12                # of the model size: a zero-length element
ASTER_TOL = 1.0e-6                  # as MecwayAsterBridge
DIRECTION_DEG = 1.0                 # as MecwayAsterBridge: a beam against SECTIE-RICHTING
# r of the detection: near 0 midpoint values, near 1 end values
R_BAND = 0.25
# A vote needs q h above the rounding of V
ROUNDING = 1.0e-9
_ASTER_RE = re.compile(r"^\s*(?:#|\*\*|\$)\s*ASTER-SECTI(?:E|ON)\s+(.*)$")
_SHAPE_RE = re.compile(r"^\s*\*\*\s*SECTIE-VORM\s+(.*)$")
_DIRECTION_RE = re.compile(r"^\s*\*\*\s*SECTIE-RICHTING\s+(.*)$")
# The keys MecwayAsterBridge knows; it skips a line with another key, or without
# one of the first four, which repeat the Mecway section (bridge_from_liml.py)
_ASTER_CHECK = ("A", "IY", "IZ", "JX")
_ASTER_KEYS = _ASTER_CHECK + ("AY", "AZ", "EY", "EZ", "RY", "RZ", "RT", "JG", "IYR2", "IZR2")
# Gauss rule on (-1, 1), for the length of a line3
_GAUSS = ((-0.906179845938664, 0.236926885056189), (-0.538469310105683, 0.478628670499366),
          (0.0, 0.568888888888889), (0.538469310105683, 0.478628670499366),
          (0.906179845938664, 0.236926885056189))


class ModelError(Exception):
    """A file that cannot be read. code: for the window; data: for its text."""

    def __init__(self, code, **data):
        Exception.__init__(self, "{0}: {1}".format(code, data))
        self.code = code
        self.data = data


class ChainError(Exception):
    """Elements that do not form one line, or forces that cannot be used."""

    def __init__(self, code, **data):
        Exception.__init__(self, "{0}: {1}".format(code, data))
        self.code = code
        self.data = data


def _warn(out, code, **data):
    item = {"code": code, "data": data}
    if item not in out:
        out.append(item)


# ---------------------------------------------------------------- vectors
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _scale(a, f):
    return (a[0] * f, a[1] * f, a[2] * f)


def _unit(a):
    return _scale(a, 1.0 / math.sqrt(_dot(a, a)))


def _carry(v, a, b):
    """v turned with the smallest rotation that takes unit a to unit b."""
    axis = _cross(a, b)
    c = _dot(a, b)
    term = _dot(axis, v) / (1.0 + c)
    w = _cross(axis, v)
    return tuple(v[i] * c + w[i] + axis[i] * term for i in range(3))


# ---------------------------------------------------------------- model
class Element(object):
    def __init__(self, eid, shape, nodes, material, vector, angle, truss):
        self.eid, self.shape, self.nodes = eid, shape, nodes
        self.material = material
        self.vector, self.angle = vector, angle       # orientation, or None and 0
        self.truss = truss
        self.formula = False                          # orientation given as a formula
        self.unknown_card = False                     # card suppressed in a configuration


class Material(object):
    def __init__(self, name):
        self.name = name
        self.geometric = {}
        self.young = None
        self.poisson = None
        self.density = 0.0


class Model(object):
    """nodes {nid: (x, y, z)}, elements {eid: Element}, materials {name: Material},
    shapes {material: {"loops", "area"}}, directions {material: {"V", "U"}}, aster
    {material: {key: value}}, analysis, solver (None = Mecway's own), fields (the field
    variables), warnings."""

    def __init__(self):
        self.nodes = {}
        self.elements = {}
        self.materials = {}
        self.shapes = {}
        self.directions = {}
        self.aster = {}
        self.analysis = None
        self.solver = None
        self.fields = []
        self.warnings = []
        self.loads = []                 # {"tag", "selection", "vector", "local", "problem"}
        self.element_selections = {}
        self.face_selections = {}
        self.node_selections = {}
        self.cards = []                 # orientation cards
        self.joints = []                # selection names of flexible joints
        self.offsets = []               # selection names of element offsets
        self.loaded_nodes = []          # selection names of point loads
        self.configurations = []


def _suppressed(item, configuration, model):
    """True: left out; False: present; None: suppressed in some configuration while
    the solved one is not known (no configuration given). The caller then never uses
    the item silently: a load switches the correction off, a card refuses its elements."""
    if item.get("suppressed", "") == "true":
        return True
    unknown = False
    for child in item.findall("suppressed"):
        if configuration is None:
            unknown = True
        elif child.get("configuration") == configuration:
            return True
    if unknown and item.tag in USED_TAGS:
        _warn(model.warnings, "configuration_unknown", tag=item.tag)
    return None if unknown else False


def _component(item, key, quantity):
    """(SI value, None), or (None, problem): the child is a formula or a table, or
    it cannot be read. A missing child is 0."""
    nodes = item.findall(key)
    if not nodes:
        return 0.0, None
    if len(nodes) > 1 or nodes[0].get("time") is not None:
        return None, "table"
    if nodes[0].get("positionunit") is not None or nodes[0].get("timeunit") is not None:
        return None, "formula"
    try:
        return parse_value(nodes[0].text, quantity), None
    except UnitError:
        return None, "unit" if section_profiles.starts_with_number(nodes[0].text) else "formula"


def _load_vector(item, keys, quantity):
    values = [_component(item, k, quantity) for k in keys]
    problems = [p for _v, p in values if p]
    return tuple(v for v, _p in values), (problems[0] if problems else None)


def _read_load(item, model):
    tag = item.tag
    sel = item.get("selection", "")
    if tag == "gravity":
        vector, problem = _load_vector(item, "xyz", "acceleration")
        model.loads.append({"tag": tag, "selection": "", "vector": vector, "local": False,
                            "problem": problem})
    elif tag == "linepressure":
        local = any(item.find(k) is not None for k in "uvw")
        vector, problem = _load_vector(item, "uvw" if local else "xyz", "line_load")
        model.loads.append({"tag": tag, "selection": sel, "vector": vector, "local": local,
                            "problem": problem})
    else:
        model.loads.append({"tag": tag, "selection": sel, "vector": None, "local": False,
                            "problem": "unknown"})


def _read_orientation(item, model):
    card = {"selection": item.get("selection", ""), "vector": [0.0, 0.0, 0.0], "angle": 0.0,
            "formula": False}
    for i, key in enumerate("xyz"):
        value, problem = _component(item, "v" + key, "angle")
        card["formula"] = card["formula"] or problem is not None
        if value is not None:
            card["vector"][i] = value
    value, problem = _component(item, "angleu", "angle")
    card["formula"] = card["formula"] or problem is not None
    card["angle"] = value or 0.0
    model.cards.append(card)


def _read_material(item, model):
    mat = Material(item.get("name", ""))
    geometric = item.find("geometric")
    if geometric is not None:
        mat.geometric = dict(geometric.attrib)
    mech = item.find("mechanical")
    try:
        if mech is not None and mech.get("youngsmodulus"):
            mat.young = parse_value(mech.get("youngsmodulus"), "pressure")
        if mech is not None and mech.get("poissonratio"):
            mat.poisson = float(mech.get("poissonratio"))
        density = item.find("density")
        if density is not None and density.get("density"):
            mat.density = parse_value(density.get("density"), "density")
    except (UnitError, ValueError) as exc:
        raise ModelError("bad_value", material=mat.name, what=str(exc))
    model.materials[mat.name] = mat


def _read_elset(item, model):
    material = item.get("material", "")
    for elem in item.findall("elem"):
        keys = ("orientationvx", "orientationvy", "orientationvz")
        vector = None
        if any(elem.get(k) is not None for k in keys):
            vector = tuple(float(elem.get(k, "0")) for k in keys)
        try:
            angle = parse_value(elem.get("angle", "0"), "angle")
        except UnitError as exc:
            raise ModelError("bad_value", eid=elem.get("eid"), what=str(exc))
        model.elements[int(elem.get("eid"))] = Element(
            int(elem.get("eid")), elem.get("shape"),
            tuple(int(n) for n in elem.get("nodes", "").split()), material, vector, angle,
            elem.get("truss") == "true")


def _read_direction(body, model):
    """'<material>: V=0 1 0, U=1 0 0': the newest good line of a material counts, a
    bad line leaves an earlier one in place. Format: bridge_custom of MecwayAsterBridge."""
    name, sep, rest = body.rpartition(":")
    found = {}
    parts = rest.split(",")
    for part in parts:
        key, eq, raw = part.partition("=")
        try:
            vector = tuple(_numbers(raw))
        except ValueError:
            vector = ()
        if len(vector) == 3 and any(vector) and eq and key.strip() in ("V", "U"):
            found[key.strip()] = _unit(vector)
    if not sep or not name.strip() or len(found) != 2 or len(parts) != 2:
        _warn(model.warnings, "bad_direction", what=body[:40])
    else:
        model.directions[name.strip()] = found


def _read_custom(text, model, bodies):
    for line in (text or "").splitlines():
        match = _SHAPE_RE.match(line)
        if match:
            bodies.append(match.group(1).strip())
            continue
        match = _DIRECTION_RE.match(line)
        if match:
            _read_direction(match.group(1).strip(), model)
            continue
        match = _ASTER_RE.match(line)
        if match:
            name, sep, rest = match.group(1).rpartition(":")
            values = {}
            try:
                for part in rest.split(","):
                    if part.strip():
                        key, _eq, raw = part.partition("=")
                        key = key.strip().upper()
                        if key not in _ASTER_KEYS:
                            raise ValueError(key)
                        values[key] = float(raw)
            except ValueError:
                _warn(model.warnings, "bad_aster_section", material=name.strip())
                continue
            if sep and name.strip():
                model.aster[name.strip()] = values
            else:
                _warn(model.warnings, "bad_aster_section", material=match.group(1).strip())


def _numbers(text):
    values = [float(t) for t in text.split()]
    if not all(math.isfinite(v) for v in values):
        raise ValueError(text)
    return values


def _clean_loop(points):
    """Without repeated points: the file rounds to a few digits."""
    out = []
    for p in points:
        if not out or p != out[-1]:
            out.append(p)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def parse_shapes(bodies, model):
    """{material: {"loops", "area"}} from the SECTIE-VORM lines, in file order: a
    new N= replaces what was read before, also when it is bad. Bad data gives a
    warning and no shape. Format: bridge_section_view of MecwayAsterBridge."""
    states = {}
    for body in bodies:
        name, sep, rest = body.rpartition(":")
        name, rest = name.strip(), rest.strip()
        if not sep or not name:
            _warn(model.warnings, "bad_shape", what=body[:40])
            continue
        if rest.startswith("N="):
            state = {"counts": None, "area": None, "values": [], "bad": False}
            states[name] = state
            try:
                for part in rest.split(","):
                    key, _eq, raw = part.partition("=")
                    if key.strip() == "N":
                        state["counts"] = [int(t) for t in raw.split()]
                    elif key.strip() == "A":
                        state["area"] = float(raw)
                    else:
                        state["bad"] = True
            except ValueError:
                state["bad"] = True
            if not state["counts"] or min(state["counts"]) < 3 or not state["area"]:
                state["bad"] = True
            continue
        state = states.setdefault(name, {"counts": None, "area": None, "values": [],
                                         "bad": False})
        if not rest.startswith("P=") or state["counts"] is None:
            state["bad"] = True
            continue
        try:
            state["values"].extend(_numbers(rest[2:]))
        except ValueError:
            state["bad"] = True
    out = {}
    for name, state in states.items():
        values = state["values"]
        if state["bad"] or len(values) != 2 * sum(state["counts"]):
            _warn(model.warnings, "bad_shape", material=name)
            continue
        loops, pos = [], 0
        for n in state["counts"]:
            chunk = values[pos:pos + 2 * n]
            pos += 2 * n
            loop = _clean_loop(list(zip(chunk[0::2], chunk[1::2])))
            if len(loop) < 3:
                _warn(model.warnings, "bad_shape", material=name)
                loops = None
                break
            loops.append(loop)
        if loops:
            out[name] = {"loops": loops, "area": state["area"]}
    return out


def _selection_ids(item):
    return set(int(e.get("eid")) for e in item.findall("element"))


def _apply_cards(model):
    """Orientation cards to the elements of their element selection. Mecway does
    not solve a card on an element that already has an orientation; a card on a
    name without an element selection has no effect (MecwayAsterBridge, measured)."""
    owned = set(e.eid for e in model.elements.values() if e.vector is not None)
    for card in model.cards:
        eids = model.element_selections.get(card["selection"])
        if eids is None:
            continue
        for eid in sorted(eids):
            element = model.elements.get(eid)
            if element is None or element.shape not in LINE_SHAPES:
                continue
            if eid in owned:
                _warn(model.warnings, "orientation_conflict", eid=eid)
            owned.add(eid)
            element.formula = card["formula"]
            element.unknown_card = element.unknown_card or card.get("unknown", False)
            if any(card["vector"]):
                element.vector, element.angle = tuple(card["vector"]), card["angle"]


def _check_aster(model):
    """Only lines that still belong to their material count: A, IY, IZ and JX
    as the general section, as MecwayAsterBridge checks them before it passes
    EY, EZ to Code-Aster. A missing torsion constant counts as 0, as there. A
    value with a unit that cannot be read drops the line here; the bridge then
    takes the bare number, so the two differ only when the line has that number."""
    for name, values in list(model.aster.items()):
        mat = model.materials.get(name)
        problem = mat is None or mat.geometric.get("type") != "UniformGeneralSection"
        if not problem:
            try:
                props = solver_props(mat.geometric)
                torsion = (mat.geometric.get("torsionconstant") or u"").strip()
                ref = {"A": props["A"], "IY": props["ivv"], "IZ": props["iww"],
                       "JX": parse_value(torsion, "inertia") if torsion else 0.0}
                problem = any(k not in values or abs(values[k] - ref[k]) >
                              ASTER_TOL * max(abs(ref[k]), 1e-30) for k in _ASTER_CHECK)
            except (SectionError, UnitError):
                problem = True
        if problem:
            _warn(model.warnings, "aster_section_stale", material=name)
            del model.aster[name]


def read_model(path, configuration=None):
    """The model part of a .liml, streaming, up to <solution>. configuration: the
    name of the configuration whose suppressed items are left out."""
    model = Model()
    bodies = []
    depth = 0
    root = None
    try:
        with open(path, "rb") as f:
            for event, item in ET.iterparse(f, events=("start", "end")):
                if event == "start":
                    depth += 1
                    if depth == 1:
                        root = item
                    elif depth == 2 and item.tag == "solution":
                        break
                    continue
                depth -= 1
                if depth != 1:
                    continue
                try:
                    _read_item(item, model, configuration, bodies)
                except (TypeError, ValueError) as exc:
                    raise ModelError("bad_file", what="{0}: {1}".format(item.tag, exc))
                del root[:]
    except ET.ParseError as exc:
        raise ModelError("bad_file", what=str(exc))
    except OSError as exc:
        raise ModelError("no_file", what=str(exc))
    if not model.nodes:
        raise ModelError("empty_model")
    if configuration is not None and configuration not in model.configurations:
        raise ModelError("configuration_missing", name=configuration)
    model.shapes = parse_shapes(bodies, model)
    _apply_cards(model)
    _check_aster(model)
    return model


def _read_item(item, model, configuration, bodies):
    tag = item.tag
    if tag == "node":
        model.nodes[int(item.get("nid"))] = (float(item.get("x")), float(item.get("y")),
                                             float(item.get("z")))
    elif tag == "elset":
        _read_elset(item, model)
    elif tag == "analysis":
        model.analysis, model.solver = item.get("type"), item.get("solver")
    elif tag == "mat":
        _read_material(item, model)
    elif tag == "elementselection":
        model.element_selections[item.get("name")] = _selection_ids(item)
    elif tag == "faceselection":
        model.face_selections[item.get("name")] = set(
            int(f.get("eid")) for f in item.findall("face"))
    elif tag == "nodeselection":
        model.node_selections[item.get("name")] = set(
            int(n.get("nid")) for n in item.findall("node"))
    elif tag == "fieldvariable":
        model.fields.append(item.get("name"))
    elif tag == "configuration":
        model.configurations.append(item.get("name"))
    elif tag in CUSTOM_TAGS:
        _read_custom(item.text, model, bodies)
    else:
        _read_part(item, model, _suppressed(item, configuration, model))


def _read_part(item, model, suppressed):
    """An item that a configuration can suppress; suppressed: see _suppressed. Joints,
    offsets and point loads in an unknown configuration count as present: they only
    switch the correction off or add a warning."""
    tag = item.tag
    if suppressed:
        return
    if tag == "orientation":
        _read_orientation(item, model)
        if suppressed is None:
            model.cards[-1]["unknown"] = True
    elif tag == "flexiblejoint":
        if len(item):           # a child is a free direction, without one it has no effect
            model.joints.append(item.get("selection", ""))
    elif tag == "offset":
        model.offsets.append(item.get("selection", ""))
    elif tag in ("force", "moment"):
        model.loaded_nodes.append(item.get("selection", ""))
    elif tag in ("gravity", "linepressure") or tag in UNKNOWN_LOADS:
        _read_load(item, model)
        if suppressed is None:
            model.loads[-1]["problem"] = "configuration"


# ---------------------------------------------------------------- chain
def element_axes(p1, p2, vector, angle):
    """(U, V, W) of a beam from p1 to p2, see the file header."""
    u = _unit(_sub(p2, p1))
    if vector is not None and _dot(vector, vector) > 0.0:
        v = _sub(vector, _scale(u, _dot(vector, u)))
        if _dot(v, v) > 1.0e-18 * _dot(vector, vector):
            v = _unit(v)
            if angle:
                w = _cross(u, v)
                v = tuple(v[i] * math.cos(angle) + w[i] * math.sin(angle) for i in range(3))
            return u, v, _cross(u, v)
    if math.hypot(u[0], u[1]) < ALONG_Z * abs(u[2]):
        v = (0.0, 1.0, 0.0)
    else:
        v = _unit(_cross((0.0, 0.0, 1.0), u))
    return u, v, _cross(u, v)


def _arc_length(nodes):
    """Length of a line3 c0, m, c1, or of the chord of a line2."""
    if len(nodes) == 2:
        return math.sqrt(_dot(_sub(nodes[1], nodes[0]), _sub(nodes[1], nodes[0])))
    a, m, b = nodes
    total = 0.0
    for t, weight in _GAUSS:
        d = tuple((t - 0.5) * a[i] - 2.0 * t * m[i] + (t + 0.5) * b[i] for i in range(3))
        total += weight * math.sqrt(_dot(d, d))
    return total


class ChainElement(object):
    """One element in a chain. s0, s1, length: along the chain. frame (U, V, W): the
    drawn axes, U along the chain. signs (sv, sw) from element to drawn axes, axes
    (U, V, W) of the element itself. q (q_u, q_v, q_w) in N/m in its own axes.
    correct: its q is complete and the correction can hold. nodes: as in the file."""

    def __init__(self, eid):
        self.eid = eid
        self.s0 = self.s1 = self.length = 0.0
        self.reversed = False
        self.frame = self.axes = None
        self.signs = (1, 1)
        self.material = None
        self.shape = None
        self.nodes = ()
        self.q = (0.0, 0.0, 0.0)
        self.correct = True
        self.geometric = None
        self.section = None
        self.young = self.poisson = None
        self.aster_offset = None        # (EY, EZ) of #ASTER-SECTIE


class Chain(object):
    """elements, ordered; length; warnings; solver and analysis of the model."""

    def __init__(self, model, elements):
        self.model = model
        self.elements = elements
        self.length = elements[-1].s1
        self.warnings = []
        self.solver = model.solver
        self.analysis = model.analysis
        self._starts = [e.s0 for e in elements]

    def element_at(self, s):
        """The element at arc length s; at a junction the one downstream."""
        tol = 1.0e-9 * self.length
        if s < -tol or s > self.length + tol:
            raise ChainError("outside_chain", s=s, length=self.length)
        s = min(max(s, 0.0), self.length)
        return self.elements[max(bisect.bisect_right(self._starts, s) - 1, 0)], s


def _order(model, ids):
    """(eid, reversed) from one end to the other, or ChainError. The direction
    with the fewest reversed elements; a tie goes to the end with the lowest node."""
    ends = {}
    size = max(max(abs(c) for c in p) for p in model.nodes.values()) or 1.0
    for eid in ids:
        element = model.elements.get(eid)
        if element is None:
            raise ChainError("unknown_element", eid=eid)
        mat = model.materials.get(element.material)
        if (element.shape not in LINE_SHAPES or element.truss or mat is None or
                mat.geometric.get("type") not in section_profiles.BEAM_TYPES):
            raise ChainError("not_a_beam", eid=eid)
        for n in element.nodes:
            if n not in model.nodes:
                raise ChainError("missing_node", eid=eid, node=n)
        a, b = element.nodes[0], element.nodes[-1]
        d = _sub(model.nodes[a], model.nodes[b])
        if math.sqrt(_dot(d, d)) <= LENGTH_TOL * size:
            raise ChainError("zero_length", eid=eid)
        ends[eid] = (a, b)
    touching = {}
    for eid, pair in ends.items():
        for n in pair:
            touching.setdefault(n, []).append(eid)
    for n, eids in touching.items():
        if len(eids) > 2:
            raise ChainError("branch", node=n, elements=sorted(eids))
    tips = sorted(n for n, eids in touching.items() if len(eids) == 1)
    if not tips:
        raise ChainError("closed_loop")
    if len(tips) > 2:
        raise ChainError("gap", ends=tips)
    walk, node, seen = [], tips[0], set()
    while True:
        next_eid = [e for e in touching[node] if e not in seen]
        if not next_eid:
            break
        eid = next_eid[0]
        seen.add(eid)
        a, b = ends[eid]
        walk.append((eid, a != node))
        node = b if a == node else a
    if len(walk) != len(ids):
        raise ChainError("gap", ends=tips)
    backward = sum(1 for _e, rev in walk if rev)
    if 2 * backward > len(walk):
        walk = [(e, not rev) for e, rev in reversed(walk)]
    return walk


def _released(model):
    """(elements with a flexible joint, elements with an offset)."""
    joints, offsets = set(), set()
    for names, target in ((model.joints, joints), (model.offsets, offsets)):
        for name in names:
            target |= model.face_selections.get(name, set())
            target |= model.element_selections.get(name, set())
    return joints, offsets


def _load_eids(model, load):
    """Elements of a load, or None when it is not resolved. An item on nodes
    has no element."""
    sel = load["selection"]
    if sel in model.element_selections:
        return model.element_selections[sel]
    if sel in model.face_selections:
        return model.face_selections[sel]
    if sel in model.node_selections:
        return set()
    return None


def _apply_loads(chain):
    model = chain.model
    joints, offsets = _released(model)
    ids = set(e.eid for e in chain.elements)
    q_global = dict((e.eid, [0.0, 0.0, 0.0]) for e in chain.elements)
    q_local = dict((e.eid, [0.0, 0.0, 0.0]) for e in chain.elements)
    skipped = {}

    def skip(reason, eids):
        skipped.setdefault(reason, set()).update(eids)

    for load in model.loads:
        if load["problem"] == "configuration":
            eids = None if load["tag"] == "gravity" else _load_eids(model, load)
            here = ids if eids is None or not load["selection"] else eids & ids
            if here:
                skip("configuration", here)
            continue
        if load["tag"] == "gravity":
            if load["problem"]:
                skip("gravity_" + load["problem"], ids)
                continue
            for element in chain.elements:
                try:
                    mass = solver_props(element.geometric)["A"] * model.materials[
                        element.material].density
                except SectionError:
                    skip("gravity_area", [element.eid])
                    continue
                for i in range(3):
                    q_global[element.eid][i] += mass * load["vector"][i]
            continue
        eids = _load_eids(model, load)
        if load["problem"] == "unknown" and not load["selection"]:
            skip("load_" + load["tag"], ids)
            continue
        if eids is None:
            skip("load_selection_" + load["tag"], ids)
            continue
        here = eids & ids
        if not here:
            continue
        if load["problem"]:
            reason = load["tag"] if load["problem"] == "unknown" else load["problem"]
            skip("load_" + reason, here)
            continue
        for eid in here:
            target = q_local if load["local"] else q_global
            for i in range(3):
                target[eid][i] += load["vector"][i]
    for name in model.loaded_nodes:
        # a point load is no problem, but spread over faces or elements it is unknown
        here = ids & (model.face_selections.get(name, set()) |
                      model.element_selections.get(name, set()))
        if here:
            skip("load_force", here)
    for element in chain.elements:
        axes = element.axes
        element.q = tuple(_dot(q_global[element.eid], axes[i]) + q_local[element.eid][i]
                          for i in range(3))
        if element.eid in joints:
            skip("flexible_joint", [element.eid])
        if element.eid in offsets:
            skip("element_offset", [element.eid])
    for reason, eids in skipped.items():
        for element in chain.elements:
            if element.eid in eids:
                element.correct = False
        _warn(chain.warnings, "correction_skipped", reason=reason, elements=sorted(eids))


def _section_id(element):
    return (element.geometric.get("type"), tuple(sorted(
        (k, v) for k, v in element.geometric.items() if k not in ("pointy", "pointz"))))


def _set_frames(chain):
    """The drawn frame of each element, see the file header."""
    elements = chain.elements
    for element in elements:
        if not element.reversed:
            element.frame = element.axes

    def reference(i):
        """(U, V, W) of the nearest element already drawn: first upstream, else
        the next one that is not reversed."""
        for j in range(i - 1, -1, -1):
            if elements[j].frame is not None:
                return elements[j].frame
        for j in range(i + 1, len(elements)):
            if elements[j].frame is not None:
                return elements[j].frame
        return None

    for i, element in enumerate(elements):
        if element.frame is not None:
            continue
        u, v, w = element.axes
        drawn = _scale(u, -1.0)
        ref = reference(i)
        score = 1.0
        if ref is not None and _dot(ref[0], drawn) > -1.0 + 1.0e-9:
            vt, wt = _carry(ref[1], ref[0], drawn), _carry(ref[2], ref[0], drawn)
            score = _dot(v, vt) - _dot(w, wt)
            if abs(score) < 1.0e-9:
                _warn(chain.warnings, "frame_tie", eid=element.eid)
                score = 1.0
        element.signs = (1, -1) if score > 0.0 else (-1, 1)
        element.frame = (drawn, _scale(v, element.signs[0]), _scale(w, element.signs[1]))


def _angle(a, b, either_sign=False):
    """Angle between unit vectors, degrees; either_sign: between the lines."""
    n = _cross(a, b)
    c = _dot(a, b)
    return math.degrees(math.atan2(math.sqrt(_dot(n, n)), abs(c) if either_sign else c))


def _check_directions(chain):
    """Elements that do not stand as SECTIE-RICHTING says, per material and kind. In
    the element's own axes, because that is how the section sits on it. The three are
    independent: a reversed beam with the default V also has its V reversed. V
    reversed at the same U is 180 degrees: the shape stands on its head."""
    found = {}
    across = math.sin(math.radians(DIRECTION_DEG))      # a beam across U is not against it
    for element in chain.elements:
        wanted = chain.model.directions.get(element.material)
        if wanted is None:
            continue
        u, v, _w = element.axes
        for reason, off in (("turned", _angle(v, wanted["V"])),
                            ("mirrored", 180.0 if _dot(u, wanted["U"]) < -across else 0.0),
                            ("skew", _angle(u, wanted["U"], True))):
            if off > DIRECTION_DEG:
                item = found.setdefault((element.material, reason), [0.0, []])
                item[0] = max(item[0], off)
                item[1].append(element.eid)
    for (material, reason), (degrees, eids) in sorted(found.items()):
        _warn(chain.warnings, "section_direction", material=material, reason=reason,
              degrees=degrees, elements=eids)


def _check_inertia(chain):
    """An outline that does not give the iyy and izz of its material. A standard
    profile does not use a line that is left under its name."""
    seen = set()
    for element in chain.elements:
        if (element.material in seen or element.section is None or
                element.geometric.get("type") != "UniformGeneralSection"):
            continue
        seen.add(element.material)
        try:
            props = section_profiles.loops_for(element.geometric, element.section)["props"]
        except SectionError:
            continue                    # section_spec reports it
        outline = section_profiles.inertia_mismatch(element.section["loops"], props)
        if outline:
            _warn(chain.warnings, "shape_inertia", material=element.material,
                  outline_ivv=outline[0], outline_iww=outline[1], iyy=props["ivv"],
                  izz=props["iww"])


def _junctions(chain):
    limit = math.radians(KINK_DEG)
    twist = math.radians(TWIST_DEG)
    for a, b in zip(chain.elements[:-1], chain.elements[1:]):
        c = _dot(a.frame[0], b.frame[0])
        if c < -1.0 + 1.0e-9:
            raise ChainError("fold_back", s=b.s0, eid=b.eid)
        n = _cross(a.frame[0], b.frame[0])
        angle = math.atan2(math.sqrt(_dot(n, n)), c)
        if angle > limit:
            _warn(chain.warnings, "kink", s=b.s0, degrees=math.degrees(angle), eid=b.eid)
        v = _carry(a.frame[1], a.frame[0], b.frame[0])
        turn = math.atan2(_dot(v, b.frame[2]), _dot(v, b.frame[1]))
        if abs(turn) > twist:
            _warn(chain.warnings, "section_twist", s=b.s0, degrees=math.degrees(turn), eid=b.eid)
        if _section_id(a) != _section_id(b):
            _warn(chain.warnings, "section_change", s=b.s0, eid=b.eid, material=b.material)
        elif a.signs != b.signs:
            _warn(chain.warnings, "section_mirrored", s=b.s0, eid=b.eid)


def build_chain(model, element_ids):
    """The chain of the elements, or ChainError (code: empty, unknown_element,
    not_a_beam, missing_node, zero_length, branch, gap, closed_loop, fold_back,
    orientation_formula, orientation_configuration)."""
    ids = list(dict.fromkeys(int(e) for e in element_ids))
    if not ids:
        raise ChainError("empty")
    elements = []
    s = 0.0
    for eid, backward in _order(model, ids):
        source = model.elements[eid]
        if source.formula:
            raise ChainError("orientation_formula", eid=eid)
        if source.unknown_card:
            raise ChainError("orientation_configuration", eid=eid)
        mat = model.materials[source.material]
        pts = [model.nodes[n] for n in source.nodes]
        item = ChainElement(eid)
        item.reversed = backward
        item.material = source.material
        item.shape = source.shape
        item.nodes = source.nodes
        item.geometric = mat.geometric
        item.section = model.shapes.get(source.material)
        item.young, item.poisson = mat.young, mat.poisson
        extra = model.aster.get(source.material) or {}
        if "EY" in extra and "EZ" in extra:
            item.aster_offset = (extra["EY"], extra["EZ"])
        item.length = _arc_length(pts)
        item.s0, item.s1 = s, s + item.length
        s = item.s1
        item.axes = element_axes(pts[0], pts[-1], source.vector, source.angle)
        elements.append(item)
    chain = Chain(model, elements)
    _set_frames(chain)
    _junctions(chain)
    _apply_loads(chain)
    _check_directions(chain)
    _check_inertia(chain)
    chain.warnings.extend(w for w in model.warnings if w not in chain.warnings)
    mid_nodes = set()
    for item in elements:
        if item.shape == "line3":
            pts = [model.nodes[n] for n in item.nodes]
            chord = _sub(pts[2], pts[0])
            off = _sub(_scale(_sub(pts[1], pts[0]), 2.0), chord)
            size = math.sqrt(_dot(chord, chord))
            if math.sqrt(_dot(off, off)) > CURVED * size:
                _warn(chain.warnings, "curved_element", eid=item.eid)
            mid_nodes.add(item.nodes[1])
    for name in model.loaded_nodes:
        if model.node_selections.get(name, set()) & mid_nodes:
            _warn(chain.warnings, "load_on_mid_node", selection=name)
    if model.solver == "ccx" and any(e.shape == "line2" for e in elements):
        _warn(chain.warnings, "ccx_line2")
    if model.solver == "ccx" and any(a.reversed != b.reversed
                                     for a, b in zip(elements[:-1], elements[1:])):
        # node values are averages in element axes: measured Vw and Mw 0 at such a node
        _warn(chain.warnings, "ccx_reversed")
    loaded = sorted(e.eid for e in elements if any(e.q))
    if model.analysis != STATIC and loaded:
        _warn(chain.warnings, "correction_skipped", reason="analysis", analysis=model.analysis,
              elements=loaded)
    return chain


# ---------------------------------------------------------------- forces
def _values(raw, element, var):
    row = raw.get(element.eid)
    if row is None or row.get(var) is None:
        return None
    pair = row[var]
    if pair[0] is None or pair[-1] is None:
        return None
    return float(pair[0]), float(pair[-1])


def _votes(chain, raw):
    """{shape: "midpoint", "end", "unknown" or None (no loaded element)}."""
    seen = {}
    for element in chain.elements:
        if not element.correct:
            continue
        h = element.length
        for var, q in zip(("tensileforce", "sheary", "shearz"), element.q):
            pair = _values(raw, element, var)
            if q == 0.0 or pair is None:
                continue
            if abs(q * h) <= ROUNDING * max(abs(pair[0]), abs(pair[1])):
                continue
            r = (pair[1] - pair[0]) / (-q * h)
            vote = ("midpoint" if abs(r) <= R_BAND else
                    "end" if abs(r - 1.0) <= R_BAND else "unknown")
            seen.setdefault(element.shape, set()).add(vote)
    return dict((shape, votes.pop() if len(votes) == 1 else "unknown")
                for shape, votes in seen.items())


def load_convention(chain, raw):
    """What the end values of this solution mean per element shape: {"line2":
    "midpoint", ...}; "end", "unknown"; a shape without loaded element is absent."""
    return _votes(chain, raw)


def _element_forces(chain, element, raw, x, votes):
    """Forces in element axes at x from the first node, and the warnings."""
    h = element.length
    t = x / h
    out, notes = {}, []
    linear = {}
    for var in FORCE_VARS:
        pair = _values(raw, element, var)
        if pair is not None:
            linear[var] = pair[0] + (pair[1] - pair[0]) * t
    for var in ("tensileforce", "bendingmomv", "bendingmomw"):
        if var not in linear:
            raise ChainError("no_section_forces", eid=element.eid, variable=var)
    kinematic = "sheary" not in linear and "shearz" not in linear and "torsion" not in linear
    if not kinematic:
        for var in FORCE_VARS:
            if var not in linear:
                raise ChainError("no_section_forces", eid=element.eid, variable=var)
    n = linear["tensileforce"]
    vv = linear.get("sheary", 0.0)
    vw = linear.get("shearz", 0.0)
    mv, mw = linear["bendingmomv"], linear["bendingmomw"]
    q_u, q_v, q_w = element.q
    if any(element.q) and element.correct and chain.analysis == STATIC:
        if kinematic:
            euler = chain.solver in (None, ASTER) and element.shape == "line2"
            kind = "midpoint" if euler else None
        else:
            kind = votes.get(element.shape)
        bulge = -x * (h - x) / 2.0
        if kind == "midpoint":
            n += q_u * (h / 2.0 - x)
            vv += q_v * (h / 2.0 - x)
            vw += q_w * (h / 2.0 - x)
            term = h * h / 12.0 + bulge
        elif kind == "end":
            # CCX line3: M at the corners carries q h^2/12, see the file header
            term = bulge + (h * h / 12.0 if chain.solver == "ccx" else 0.0)
        else:
            term = 0.0
            _warn(notes, "load_convention_unknown", eid=element.eid)
        mw += q_v * term
        mv -= q_w * term
    if kinematic:
        out = {"N": n, "Mv": mv, "Mw": mw}
        _warn(notes, "kinematics_only")
    else:
        out = {"N": n, "Vv": vv, "Vw": vw, "T": linear["torsion"], "Mv": mv, "Mw": mw}
    return out, notes


def _to_drawn(element, f):
    su = -1 if element.reversed else 1
    sv, sw = element.signs
    out = {"N": f["N"], "Mv": sw * f["Mv"], "Mw": sv * f["Mw"]}
    if "T" in f:
        out.update({"Vv": su * sv * f["Vv"], "Vw": su * sw * f["Vw"], "T": f["T"]})
    return out


def forces_at(chain, raw, s):
    """{"element", "xi", "forces", "element_forces", "warnings"} at arc length s. raw:
    {eid: {var: [end 1, end 2]}}, Mecway's names, as the element stores them. forces:
    in the axes of ChainElement.frame, for section_spec; element_forces: in the
    element's own axes, as Mecway shows them. xi: 0..1 along the chain. From the
    kinematics only N, Mv, Mw exist. A chain has one solution per raw, so the detection
    runs per call; it is linear in the number of elements."""
    element, s = chain.element_at(s)
    xi = (s - element.s0) / element.length
    x = element.length * (1.0 - xi if element.reversed else xi)
    forces, notes = _element_forces(chain, element, raw, x, _votes(chain, raw))
    return {"element": element, "xi": xi, "forces": _to_drawn(element, forces),
            "element_forces": forces, "warnings": notes}


def _curvature(x, h, y1, slope1, y2, slope2):
    """Second derivative at x of the Hermite cubic through end values and slopes."""
    t = x / h
    return ((-6.0 + 12.0 * t) * y1 / h ** 2 + (-4.0 + 6.0 * t) * slope1 / h +
            (6.0 - 12.0 * t) * y2 / h ** 2 + (-2.0 + 6.0 * t) * slope2 / h)


def forces_from_kinematics(chain, raw):
    """{eid: {"tensileforce", "bendingmomv", "bendingmomw": [end 1, end 2]}} from raw
    {eid: {"displx", ..., "rotz": [end 1, end 2]}}, for forces_at. See the file header:
    N, Mv, Mw only, and forces_at says so with kinematics_only. A line3 is one Hermite
    element over its chord; shear deformation and joints are not in this field."""
    out = {}
    for element in chain.elements:
        row = raw.get(element.eid) or {}
        ok = all(row.get(v) is not None and None not in row[v] for v in MOTION_VARS)
        if not ok:
            raise ChainError("no_motion", eid=element.eid)
        d1 = tuple(float(row[v][0]) for v in MOTION_VARS[:3])
        d2 = tuple(float(row[v][-1]) for v in MOTION_VARS[:3])
        r1 = tuple(float(row[v][0]) for v in MOTION_VARS[3:])
        r2 = tuple(float(row[v][-1]) for v in MOTION_VARS[3:])
        u, v, w = element.axes
        pts = [chain.model.nodes[n] for n in (element.nodes[0], element.nodes[-1])]
        h = _arc_length(pts)
        try:
            props = solver_props(element.geometric)
        except SectionError as exc:
            raise ChainError(exc.code, eid=element.eid, name=element.material,
                             what=u"{0}".format(exc.detail))
        e = element.young
        if e is None:
            raise ChainError("no_young", eid=element.eid)
        strain = (_dot(d2, u) - _dot(d1, u)) / h
        row_out = {"tensileforce": [e * props["A"] * strain] * 2, "bendingmomv": [],
                   "bendingmomw": []}
        for x in (0.0, h):
            v2 = _curvature(x, h, _dot(d1, v), _dot(r1, w), _dot(d2, v), _dot(r2, w))
            w2 = _curvature(x, h, _dot(d1, w), -_dot(r1, v), _dot(d2, w), -_dot(r2, v))
            row_out["bendingmomv"].append(-e * props["ivv"] * w2)
            row_out["bendingmomw"].append(e * props["iww"] * v2)
        out[element.eid] = row_out
    return out


# ---------------------------------------------------------------- sections
def section_key(element):
    """Hashable: one SectionField per shape, Poisson ratio, side and #ASTER-SECTIE
    offset."""
    shape = None
    if element.section:
        shape = tuple(tuple(map(tuple, loop)) for loop in element.section["loops"])
    return (_section_id(element), shape, element.poisson, element.signs, element.aster_offset)


def section_spec(model, element, aster_offset=False):
    """{"loops", "nu", "props", "offset"} for a SectionField in the drawn axes. Raises
    SectionError, see section_profiles.loops_for. aster_offset: use EY, EZ of
    #ASTER-SECTIE, for a Code-Aster solution; otherwise the field takes its own."""
    if element.poisson is None:
        raise SectionError("no_poisson", element.material)
    section = section_profiles.loops_for(element.geometric, element.section)
    sv, sw = element.signs
    props = dict(section["props"])
    props["ivw"] *= sv * sw
    loops = [[(sv * v, sw * w) for v, w in loop] for loop in section["loops"]]
    offset = None
    if aster_offset and element.aster_offset:
        offset = (sv * element.aster_offset[0], sw * element.aster_offset[1])
    return {"loops": loops, "nu": element.poisson, "props": props, "offset": offset}
