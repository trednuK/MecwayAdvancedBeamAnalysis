# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of Scripts/beam_model.py. Against Mecway 35, own models, values measured by
# tests/mecway/run_mecway.py and stored in tests/mecway/results:
#   V4  signs: each of the six forces, reversed node order, V vector with angle
#   V5  sigma against stressptu
#   V9  distributed load and gravity, straight and skew, against equilibrium
#   V11 units, through the same models
# The reference of V4 and V9 is statics from the loads as applied, not Mecway's
# own pattern. Run with a Python with numpy and scipy:
#
#   python tests/test_beam_model.py
#
# Reruns itself with the Python of a Code-Aster installation if needed.
# The parts that need tests/mecway/results/ (not in git) are skipped when it is missing;
# BEAM_REQUIRE_RESULTS=1 makes that a failure.

from __future__ import print_function

import glob
import json
import math
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(globals().get("__file__", sys.argv[0])))
sys.path.insert(0, os.path.join(_HERE, "..", "Scripts"))
sys.dont_write_bytecode = True
try:
    import numpy  # noqa: F401
    import scipy  # noqa: F401
except ImportError:
    pats = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "code_aster", "*", "*",
                                  "Python*", "python.exe"))
    if not pats:
        print("no Python with numpy and scipy found")
        sys.exit(2)
    sys.exit(subprocess.call([sorted(pats)[-1], os.path.abspath(sys.argv[0])]))

import numpy as np  # noqa: E402
import beam_model as bm  # noqa: E402
import beam_utils as bu  # noqa: E402
import section_profiles as sp  # noqa: E402
import section_stress as ss  # noqa: E402

FAILED = []
MECWAY = os.path.join(_HERE, "mecway")
NAMES = ("N", "Vv", "Vw", "T", "Mv", "Mw")
WORST = {}


def check(name, ok, detail=""):
    line = ("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else "")
    print(line.encode("ascii", "replace").decode())
    if not ok:
        FAILED.append(name)


SKIPPED = []
RUNNERS = "tests/mecway/run_mecway.py"


class NoResults(Exception):
    """A result file of a Mecway run is missing. tests/mecway/results/ is not in git (the
    files hold local paths), so on a fresh checkout the parts that need it are skipped."""


def result_file(name):
    """The path of a result file; NoResults when it is not on this PC. A file that is
    there but cannot be read stays a failure."""
    path = os.path.join(MECWAY, "results", name)
    if not os.path.isfile(path):
        raise NoResults(name)
    return path


def run(test):
    """One part of this test; skipped when it needs a missing result file, failed when
    BEAM_REQUIRE_RESULTS=1 is set."""
    try:
        test()
    except NoResults as exc:
        if os.environ.get("BEAM_REQUIRE_RESULTS") == "1":
            check(test.__name__ + ": result file tests/mecway/results/{0}".format(exc), False)
        else:
            SKIPPED.append(test.__name__)
            print("SKIP {0}: no tests/mecway/results/{1}".format(test.__name__, exc))


def summary(count):
    """The last line: failures and skipped parts of count. Exit code 0 when nothing
    failed, also when parts were skipped."""
    skipped = ""
    if SKIPPED:
        print("The skipped parts need the results of Mecway runs on this PC: "
              "tests/mecway/results/ is not in git. Made by " + RUNNERS + ".")
        skipped = ", {0} of {1} parts skipped (no local Mecway results)".format(
            len(SKIPPED), count)
    if FAILED:
        print("FAILED {0}: {1}{2}".format(len(FAILED), "; ".join(FAILED), skipped))
        return 1
    print(("nothing ran" if len(SKIPPED) == count else "all passed") + skipped)
    return 0


def load(name):
    """Model, result, info and element values of a Mecway run."""
    with open(result_file(name + ".json")) as f:
        result = json.load(f)
    model, info = load_model(name)
    raw = dict((int(e), r) for e, r in result["elements"].items())
    return model, result, info, raw


def load_model(name):
    """Model and info only: these are in git."""
    with open(os.path.join(MECWAY, "models", name + ".info.json")) as f:
        info = json.load(f)
    return bm.read_model(os.path.join(MECWAY, "models", name + ".liml")), info


def statics(model, beam, density_g, chain, s):
    """Force and moment that the part on the tip side of the section at arc length
    s exerts on the rest, about the section centre, from the applied loads only."""
    el, s = chain.element_at(s)
    start = np.array(model.nodes[el.nodes[-1 if el.reversed else 0]])
    p = start + np.array(el.frame[0]) * (s - el.s0)
    tip = np.array(model.nodes[beam["tip"]])
    force = np.array(beam["F"], dtype=float)
    moment = np.array(beam["M"], dtype=float) + np.cross(tip - p, force)
    # the elements from the root, each with its near and far corner
    node = beam["root"]
    passed = False
    segments = []
    for eid in beam["eids"]:
        element = model.elements[eid]
        a, b = element.nodes[0], element.nodes[-1]
        other = b if a == node else a
        segments.append((eid, np.array(model.nodes[node]), np.array(model.nodes[other])))
        node = other
    for eid, a, b in segments:
        d = b - a
        length = math.sqrt(d.dot(d))
        t = (p - a).dot(d) / d.dot(d)
        inside = np.linalg.norm(a + t * d - p) < 1e-9
        if inside and -1e-12 <= t <= 1.0 + 1e-12:
            passed = True
            a = p
            length = math.sqrt((b - p).dot(b - p))
        elif not passed:
            continue
        q = np.zeros(3)
        current = next(e for e in full_chains[id(chain)].elements if e.eid == eid)
        for item in beam["q"]:
            if eid in item["eids"]:
                if item["local"]:
                    u, v, w = current.axes
                    q += (item["q"][0] * np.array(u) + item["q"][1] * np.array(v) +
                          item["q"][2] * np.array(w))
                else:
                    q += np.array(item["q"])
        if density_g is not None:
            mass = sp.solver_props(current.geometric)["A"] * model.materials[
                current.material].density
            q += mass * np.array(density_g)
        total = q * length
        force = force + total
        moment = moment + np.cross((a + b) / 2.0 - p, total)
    return force, moment


full_chains = {}


def expected(model, beam, g, chain, s):
    """The six forces in the drawn axes: on the +U face the force of the part on the
    +U side, so with the opposite sign when +U points to the root."""
    el, s = chain.element_at(s)
    force, moment = statics(model, beam, g, chain, s)
    frame = [np.array(a) for a in el.frame]
    # +U towards the tip?
    towards = beam["eids"][0] == chain.elements[0].eid
    sign = 1.0 if towards else -1.0
    return {"N": sign * force.dot(frame[0]), "Vv": sign * force.dot(frame[1]),
            "Vw": sign * force.dot(frame[2]), "T": sign * moment.dot(frame[0]),
            "Mv": sign * moment.dot(frame[1]), "Mw": sign * moment.dot(frame[2])}


def positions(chain):
    out = [0.0, chain.length]
    for el in chain.elements:
        out.extend(el.s0 + t * el.length for t in (0.01, 0.13, 0.37, 0.5, 0.71, 0.99))
    return out


def compare(name, model, beam, g, chain, raw, tol, scale=None, use=NAMES):
    """Largest difference of forces_at with statics, relative to the largest value."""
    worst, top, where = 0.0, scale or 1e-9, None
    for s in positions(chain):
        want = expected(model, beam, g, chain, s)
        top = max(top, max(abs(want[k]) for k in use))
    for s in positions(chain):
        want = expected(model, beam, g, chain, s)
        got = bm.forces_at(chain, raw, s)["forces"]
        for k in use:
            err = abs(got[k] - want[k]) / top
            if err > worst:
                worst, where = err, (round(s, 4), k, got[k], want[k])
    group = name.split()[0]
    WORST[group] = max(WORST.get(group, 0.0), worst)
    check(name, worst <= tol, "{0:.2e} {1}".format(worst, where if worst > tol else ""))


def chains(model, info, keys=None):
    out = {}
    for key, beam in sorted(info["beams"].items()):
        if keys is None or key in keys:
            chain = bm.build_chain(model, beam["eids"])
            full_chains[id(chain)] = chain
            out[key] = chain
    return out


def test_signs():
    print("-- V4: signs, statics of the applied loads, drawn axes")
    model, _result, info, raw = load("signs")
    for key, chain in chains(model, info).items():
        compare("V4 " + key, model, info["beams"][key], None, chain, raw, 1e-6)
    # at s = 0.5 of a tip load: F (L - s) in the moment
    expect = {"n_N": {"N": 100.0}, "n_Vy": {"Vv": 100.0, "Mw": 50.0},
              "n_Vz": {"Vw": 100.0, "Mv": -50.0}, "n_T": {"T": 10.0}, "n_My": {"Mv": 10.0},
              "n_Mz": {"Mw": 10.0}}
    for key, want in sorted(expect.items()):
        beam = info["beams"][key]
        chain = bm.build_chain(model, beam["eids"])
        got = bm.forces_at(chain, raw, 0.5)["forces"]
        check("V4 exact sign and only this force: " + key,
              all(abs(got[k] - want.get(k, 0.0)) <= 1e-6 * 100.0 for k in NAMES))
    # the same beam in element axes, as Mecway shows it, with node order swapped
    beam = info["beams"]["mid_rev"]
    chain = bm.build_chain(model, beam["eids"])
    check("reversed middle: two of four reversed, chain from the root",
          [e.reversed for e in chain.elements] == [False, True, True, False])
    at = bm.forces_at(chain, raw, 0.375)
    stored = raw[at["element"].eid]
    check("element_forces are Mecway's own axes",
          abs(at["element_forces"]["Vv"] - stored["sheary"][0]) < 1e-9 and
          abs(at["element_forces"]["Mw"] - 0.5 * sum(stored["bendingmomw"])) < 1e-9)
    data, _res, angle_info, angle_raw = load("angle_only")
    angle_chain = chains(data, angle_info)["angle_only"]
    compare("V4 angle without a vector is ignored (measured)", data,
            angle_info["beams"]["angle_only"], None, angle_chain, angle_raw, 1e-6)
    check("all reversed: chain follows the nodes",
          not any(e.reversed for e in bm.build_chain(model, info["beams"]["r_T"]["eids"]).elements))


def _sigma_at(field, forces, v, w):
    """sigma is linear in v and w: read it from three nodes of the field."""
    pts = field._node_vw.reshape(-1, 2)
    vals = field.stress(forces)["sigma"].reshape(-1)
    a = np.linalg.lstsq(np.c_[np.ones(len(pts)), pts], vals, rcond=None)[0]
    return a[0] + a[1] * v + a[2] * w


def test_stressptu():
    print("-- V5: sigma through forces_at and SectionField against stressptu, 1e-6")
    worst = 0.0
    for name in ("sections", "signs"):
        model, _result, info, raw = load(name)
        fields = {}
        for key, beam in sorted(info["beams"].items()):
            chain = bm.build_chain(model, beam["eids"])
            bad = 0.0
            for element in chain.elements:
                geo = element.geometric
                point = (sp.parse_value(geo["pointy"], "length"),
                         sp.parse_value(geo["pointz"], "length"))
                ids = bm.section_key(element)
                if ids not in fields:
                    spec = bm.section_spec(model, element)
                    fields[ids] = ss.SectionField(spec["loops"], nu=spec["nu"],
                                                  props=spec["props"], n_target=300)
                for end, t in ((0, 0.0), (1, 1.0)):
                    # at the two ends of the element, chain coordinates
                    tt = 1.0 - t if element.reversed else t
                    s = element.s0 + tt * element.length
                    s = min(max(s, element.s0 + 1e-12), element.s1 - 1e-12)
                    at = bm.forces_at(chain, raw, s)
                    sv, sw = element.signs
                    got = _sigma_at(fields[ids], at["forces"], sv * point[0], sw * point[1])
                    want = raw[element.eid]["stressptu"][end]
                    top = max(abs(v) for v in raw[element.eid]["stressptu"]) or 1.0
                    bad = max(bad, abs(got - want) / max(top, 1e3))
            worst = max(worst, bad)
            check("V5 {0} {1}".format(name, key), bad <= 1e-6, "{0:.1e}".format(bad))
    print("     worst {0:.2e}".format(worst))


def test_distributed():
    print("-- V9: distributed load against equilibrium, line2, 1e-6")
    model, result, info, raw = load("dist")
    for key, chain in chains(model, info).items():
        votes = bm.load_convention(chain, raw)
        check("V9 midpoint values detected: " + key, votes == {"line2": "midpoint"}, votes)
        compare("V9 " + key, model, info["beams"][key], None, chain, raw, 1e-6)
    print("-- V9: gravity")
    model, _result, info, raw = load("gravity")
    g = (3.0, -10.0, 4.0)
    for key, chain in chains(model, info).items():
        compare("V9 gravity " + key, model, info["beams"][key], g, chain, raw, 1e-6)
    print("-- V9: line load in element axes")
    model, _result, info, raw = load("local_load")
    for key, chain in chains(model, info).items():
        compare("V9 local " + key, model, info["beams"][key], None, chain, raw, 1e-6)
    print("-- V9: line load on a face selection of the beams")
    model, _result, info, raw = load("face_load")
    for key, chain in chains(model, info).items():
        compare("V9 face " + key, model, info["beams"][key], None, chain, raw, 1e-6)
    print("-- Mecway's own solver and line3")
    _model, result, _info, _raw = load("line3")
    check("no solution, no error (measured)", not result["elements"] and not result["nodes"] and
          not result["errors"])
    print("-- V9: without correction the same data fails, so the test has teeth")
    model, _result, info, raw = load("dist")
    chain = bm.build_chain(model, info["beams"]["x1"]["eids"])
    full_chains[id(chain)] = chain
    chain.elements[0].correct = False
    want = expected(model, info["beams"]["x1"], None, chain, 0.0)
    got = bm.forces_at(chain, raw, 0.0)["forces"]
    check("uncorrected Mw differs from statics", abs(got["Mw"] - want["Mw"]) > 1.0)


def test_kinematics():
    print("-- fallback: N, Mv, Mw from displacement and rotation")
    model, result, info, raw = load("dist")
    for name in ("signs", "dist", "gravity"):
        model, result, info, raw = load(name)
        g = (3.0, -10.0, 4.0) if name == "gravity" else None
        for key, chain in chains(model, info).items():
            motion = {}
            for element in chain.elements:
                n1, n2 = str(element.nodes[0]), str(element.nodes[-1])
                motion[element.eid] = dict(
                    (v, [result["nodes"][n1][v], result["nodes"][n2][v]]) for v in bm.MOTION_VARS)
            ends = bm.forces_from_kinematics(chain, motion)
            bad = 0.0
            for element in chain.elements:
                for var in ("tensileforce", "bendingmomv", "bendingmomw"):
                    top = max(abs(x) for x in raw[element.eid][var]) + 1.0
                    bad = max(bad, max(abs(a - b) for a, b in zip(ends[element.eid][var],
                                                                  raw[element.eid][var])) / top)
            check("kinematics = Mecway's values: {0} {1}".format(name, key), bad < 2e-6,
                  "{0:.1e}".format(bad))
            compare("kinematics against statics: {0} {1}".format(name, key), model,
                    info["beams"][key], g, chain, ends, 2e-6, use=("N", "Mv", "Mw"))
            at = bm.forces_at(chain, ends, chain.length / 2.0)
            codes = [w["code"] for w in at["warnings"]]
            if "kinematics_only" not in codes or "Vv" in at["forces"]:
                check("kinematics says so", False)


def test_ccx():
    print("-- CCX node values, measured (6 digits)")
    for name, line3 in (("ccx_line3", True), ("ccx_line2", False)):
        model, result, info, _raw = load(name)
        beam = info["beams"]["q"]
        chain = bm.build_chain(model, beam["eids"])
        full_chains[id(chain)] = chain
        raw = {}
        for element in chain.elements:
            ends = (str(element.nodes[0]), str(element.nodes[-1]))
            raw[element.eid] = dict((v, [result["nodes"][n][v] for n in ends])
                                    for v in bm.FORCE_VARS)
        codes = [w["code"] for w in chain.warnings]
        check(name + ": warning", ("ccx_line2" in codes) != line3)
        votes = bm.load_convention(chain, raw)
        if line3:
            check("line3: end values for N and V", votes == {"line3": "end"}, votes)
            compare("CCX line3 distributed load, M with the q h^2/12 measured", model, beam, None,
                    chain, raw, 3e-4)
        else:
            check("line2: unknown, no correction", votes == {"line2": "unknown"}, votes)
            at = bm.forces_at(chain, raw, 0.1)
            check("line2: says so",
                  [w["code"] for w in at["warnings"]] == ["load_convention_unknown"])


    model, result, info, _raw = load("ccx_rev")
    chain = bm.build_chain(model, info["beams"]["tip"]["eids"])
    check("CCX: node order changes along the chain: warned", "ccx_reversed" in
          [w["code"] for w in chain.warnings])
    junction = str(chain.elements[1].nodes[-1])
    check("that warning is needed: Vw and Mw average to 0 at the junction (measured)",
          abs(result["nodes"][junction]["shearz"]) < 1e-6 and
          abs(result["nodes"][junction]["bendingmomw"]) < 1e-3)


def _liml(body):
    fd, path = tempfile.mkstemp(suffix=".liml")
    os.close(fd)
    with open(path, "w", encoding="utf8") as f:
        f.write(body)
    return path


BASE = u"""<liml version="33">
  <analysis type="%s" />
  <node nid="1" x="0" y="0" z="0" /><node nid="2" x="1" y="0" z="0" />
  <node nid="3" x="2" y="0" z="0" /><node nid="4" x="3" y="0" z="0" />
  <node nid="5" x="2" y="1" z="0" /><node nid="6" x="1.5" y="0" z="0" />
  <elset name="B" material="M">
    <elem eid="1" shape="line2" nodes="1 2" />
    <elem eid="2" shape="line2" nodes="3 2" />
    <elem eid="3" shape="line2" nodes="3 4" orientationvz="1" angle="90 °" />
    <elem eid="4" shape="line2" nodes="3 5" />
    <elem eid="5" shape="line2" nodes="2 1" truss="true" />
    <elem eid="6" shape="line3" nodes="2 6 3" />
  </elset>
  %s
  <mat name="M">
    <geometric type="RectBar" recta="100 mm" rectb="50 mm" pointy="0 m" pointz="0 m" />
    <mechanical type="Isotropic" youngsmodulus="210 GPa" poissonratio="0.3" />
    <density type="Simple" density="7850 kg/m³" />
  </mat>
  <mat name="G">
    <geometric type="UniformGeneralSection" area="2000 mm²" iyy="2000000 mm⁴" izz="8000000 mm⁴"
               torsionconstant="1000000 mm⁴" />
    <mechanical type="Isotropic" youngsmodulus="210 GPa" poissonratio="0.3" />
  </mat>
  %s
</liml>
"""


def synthetic(items=u"", after=u"", analysis="S30"):
    path = _liml(BASE % (analysis, items, after))
    try:
        return bm.read_model(path)
    finally:
        os.remove(path)


def error_code(fn, *args):
    try:
        fn(*args)
    except (bm.ChainError, bm.ModelError) as exc:
        return exc.code
    return None


def test_read():
    print("-- reading the file")
    model = synthetic(after=u"<solution><this is not parsed &&& </solution>")
    check("stops at <solution>", len(model.nodes) == 6 and len(model.elements) == 6)
    check("analysis", model.analysis == "S30" and model.solver is None)
    check("V11: density", model.materials["M"].density == 7850.0)
    check("attributes: vector and angle", model.elements[3].vector == (0.0, 0.0, 1.0) and
          abs(model.elements[3].angle - math.pi / 2.0) < 1e-15)
    u, v, w = bm.element_axes((0, 0, 0), (1, 0, 0), None, 0.0)
    check("default V = Z x U", v == (0.0, 1.0, 0.0) and w == (0.0, 0.0, 1.0))
    u, v, w = bm.element_axes((0, 0, 0), (1, 0, 0), (0.0, 0.0, 1.0), math.pi / 2.0)
    check("angle turns right-handed about U", abs(v[1] + 1.0) < 1e-15, v)
    u, v, w = bm.element_axes((0, 0, 0), (0, 0, -1), None, 0.0)
    check("along Z: V = Y", v == (0.0, 1.0, 0.0))
    u, v, w = bm.element_axes((0, 0, 0), (1, 0, 0), (2.0, 0.0, 0.0), 0.5)
    check("vector along U: default", v == (0.0, 1.0, 0.0))

    card = (u'<orientation selection="o"><ux>0</ux><uy>0</uy><uz>0</uz><vx>0</vx><vy>0</vy><vz>1</vz>'
            u'<angleu>30 °</angleu></orientation>')
    sel = u'<elementselection name="o" direct="true"><element eid="1" /></elementselection>'
    model = synthetic(card, sel)
    check("card sets vector and angle", model.elements[1].vector == (0.0, 0.0, 1.0) and
          abs(model.elements[1].angle - math.radians(30.0)) < 1e-15)
    formula = card.replace(u"<vz>1</vz>", u'<vz unit="" timeunit="s" positionunit="m">x</vz>')
    model = synthetic(formula, sel)
    check("card with a formula: refused", error_code(bm.build_chain, model, [1]) ==
          "orientation_formula")
    conflict = sel.replace(u'eid="1"', u'eid="3"')
    model = synthetic(card, conflict)
    check("card on an element with attributes: warned",
          "orientation_conflict" in [w["code"] for w in model.warnings])

    shape = (u"<ccxmodeldefinition>** ASTER-SECTIE G: A=0.002, IY=2e-06, IZ=8e-06, JX=1e-06, "
             u"EY=0.001, EZ=-0.002\n** ASTER-SECTIE M: A=1, IY=1, IZ=1, JX=1\n"
             u"** SECTIE-VORM G: N=4, A=0.002\n** SECTIE-VORM G: P=-0.05 -0.01 0.05 -0.01 0.05\n"
             u"** SECTIE-VORM G: P=0.01 -0.05 0.01\n** SECTIE-VORM X: N=3, A=1\n"
             u"** SECTIE-VORM X: P=0 0 1</ccxmodeldefinition>")
    model = synthetic(shape)
    check("SECTIE-VORM", len(model.shapes["G"]["loops"][0]) == 4 and
          model.shapes["G"]["area"] == 0.002)
    check("ASTER-SECTIE", model.aster["G"]["EZ"] == -0.002)
    check("ASTER-SECTIE of a non-general material is dropped", "M" not in model.aster)
    check("bad shape: warned, no shape", "X" not in model.shapes and
          "bad_shape" in [w["code"] for w in model.warnings])

    good = (u'<linepressure selection="s"><x>0 N/m</x><y>-2 N/mm</y><z>0 N/m</z></linepressure>'
            u'<gravity><x>0 m/s²</x><y>0 m/s²</y><z>-10 m/s²</z></gravity>')
    sel1 = u'<elementselection name="s" direct="true"><element eid="1" /></elementselection>'
    model = synthetic(good, sel1)
    chain = bm.build_chain(model, [1])
    check("V11: loads to element axes, summed", np.allclose(
        chain.elements[0].q, (0.0, -2000.0, -7850.0 * 0.005 * 10.0)), chain.elements[0].q)
    check("nothing to warn", not chain.warnings, chain.warnings)
    suppressed = good.replace(u"<linepressure ", u'<linepressure suppressed="true" ')
    chain = bm.build_chain(synthetic(suppressed, sel1), [1])
    check("suppressed load left out", np.allclose(chain.elements[0].q, (0, 0, -392.5)))
    conf = u'<configuration name="C" />' + good.replace(
        u"</linepressure>", u'<suppressed configuration="C" /></linepressure>')
    path = _liml(BASE % ("S30", conf, sel1))
    chain = bm.build_chain(bm.read_model(path, configuration="C"), [1])
    check("suppressed in the configuration", abs(chain.elements[0].q[1]) < 1e-12)
    chain = bm.build_chain(bm.read_model(path), [1])
    os.remove(path)
    check("no configuration given: warned", "configuration_unknown" in
          [w["code"] for w in chain.warnings])
    for name, item, reason in (
            ("formula", u'<linepressure selection="s"><y unit="N/m" timeunit="s" positionunit="m">'
                        u'x</y></linepressure>', "load_formula"),
            ("table", u'<linepressure selection="s"><y time="1 s">1 N/m</y><y time="2 s">2 N/m</y>'
                      u'</linepressure>', "load_table"),
            ("traction", u'<traction selection="s"><x>1 Pa</x></traction>', "load_traction"),
            ("no selection", u'<linepressure selection="nope"><y>1 N/m</y></linepressure>',
             "load_selection_linepressure"),
            ("offset", u'<offset selection="s"><w>0.002 m</w></offset>', "element_offset"),
            ("joint", u'<flexiblejoint selection="s"><rotw /></flexiblejoint>', "flexible_joint"),
            ("centrifugal", u'<centrifugal><x>1 r/min</x></centrifugal>', "load_centrifugal"),
            ("force on elements", u'<force selection="s"><x>1 N</x></force>', "load_force"),
            ("joint with a free direction", u'<flexiblejoint selection="s"><rotu /></flexiblejoint>',
             "flexible_joint"),
            ("unit", u'<linepressure selection="s"><y>1 foo</y></linepressure>', "load_unit")):
        chain = bm.build_chain(synthetic(item + good, sel1), [1])
        found = [w["data"].get("reason") for w in chain.warnings
                 if w["code"] == "correction_skipped"]
        check(name + ": correction skipped with a warning",
              reason in found and not chain.elements[0].correct, found)
    bad = good.replace(u"-10 m/s²", u"1 foo")
    chain = bm.build_chain(synthetic(bad, sel1), [1])
    check("a gravity in an unknown unit says so", [w["data"]["reason"] for w in chain.warnings] ==
          ["gravity_unit"], chain.warnings)
    chain = bm.build_chain(synthetic(u'<flexiblejoint selection="s" />' + good, sel1), [1])
    check("joint without a free direction has no effect", chain.elements[0].correct)
    chain = bm.build_chain(synthetic(u'<force selection="n"><x>1 N</x></force>' + good,
                                     sel1 + u'<nodeselection name="n" direct="true">'
                                            u'<node nid="2" /></nodeselection>'), [1])
    check("a point load on a node stays a point load", chain.elements[0].correct)
    chain = bm.build_chain(synthetic(analysis="S30"), [1])
    chain.elements[0].young = None
    motion = {1: dict((v, [0.0, 0.0]) for v in bm.MOTION_VARS)}
    check("no E: coded error", error_code(bm.forces_from_kinematics, chain, motion) == "no_young")
    check("no motion: coded error", error_code(bm.forces_from_kinematics, chain, {}) == "no_motion")
    path = _liml(u'<liml><node nid="1" x="a" y="0" z="0" /></liml>')
    check("a bad attribute: coded error", error_code(bm.read_model, path) == "bad_file")
    os.remove(path)
    chain = bm.build_chain(synthetic(good, sel1, analysis="V30"), [1])
    raw = {1: {"tensileforce": [0.0, 0.0], "sheary": [5.0, 5.0], "shearz": [1.0, 1.0],
               "torsion": [0.0, 0.0], "bendingmomv": [0.0, 0.0], "bendingmomw": [0.0, 0.0]}}
    check("modal analysis: no correction", bm.forces_at(chain, raw, 0.5)["forces"]["Vv"] == 5.0 and
          "correction_skipped" in [w["code"] for w in chain.warnings])
    check("no line elements in a file: empty", error_code(bm.read_model, os.devnull) in
          ("bad_file", "empty_model"))
    check("missing file", error_code(bm.read_model, os.path.join(_HERE, "nope.liml")) == "no_file")


def _read(body, configuration=None):
    path = _liml(body)
    try:
        return bm.read_model(path, configuration)
    finally:
        os.remove(path)


def test_configurations():
    """Items suppressed per configuration: left out in that configuration, used in the
    others, and with the configuration unknown never silently used (review finding)."""
    print("-- configurations")
    bu.settings()["language"] = "en"
    confs = u'<configuration name="C" /><configuration name="D" />'
    sel1 = u'<elementselection name="s" direct="true"><element eid="1" /></elementselection>'
    load = (u'<linepressure selection="s"><x>0 N/m</x><y>-2 N/mm</y><z>0 N/m</z>'
            u'<suppressed configuration="C" /></linepressure>')
    body = BASE % ("S30", confs + load, sel1)
    chain = bm.build_chain(_read(body, "C"), [1])
    check("load suppressed in the configuration: left out", chain.elements[0].q == (0.0, 0.0, 0.0)
          and chain.elements[0].correct, chain.elements[0].q)
    chain = bm.build_chain(_read(body, "D"), [1])
    check("load in another configuration: used", abs(chain.elements[0].q[1] + 2000.0) < 1e-9 and
          chain.elements[0].correct, chain.elements[0].q)
    chain = bm.build_chain(_read(body), [1])
    skipped = [w["data"] for w in chain.warnings if w["code"] == "correction_skipped"]
    check("configuration unknown: correction off", not chain.elements[0].correct and
          [d.get("reason") for d in skipped] == ["configuration"] and skipped[0]["elements"] == [1],
          chain.warnings)
    check("configuration unknown: said", "configuration_unknown" in
          [w["code"] for w in chain.warnings])
    fixed = u'<fixed selection="s"><suppressed configuration="C" /></fixed>'
    model = _read(BASE % ("S30", confs + fixed, sel1))
    check("an item the tool does not use: no warning", not model.warnings, model.warnings)
    text = bu.warning_text("correction_skipped", skipped[0] if skipped else {})
    check("configuration unknown: the reason in words", u"configuration" in text and u"?" not in text,
          text)
    gravity = (u'<gravity><x>0 m/s²</x><y>0 m/s²</y><z>-10 m/s²</z>'
               u'<suppressed configuration="D" /></gravity>')
    chain = bm.build_chain(_read(BASE % ("S30", confs + gravity, sel1)), [1, 2])
    skipped = [w["data"] for w in chain.warnings if w["code"] == "correction_skipped"]
    check("gravity, configuration unknown: correction off on the whole chain",
          not any(e.correct for e in chain.elements) and
          [d.get("reason") for d in skipped] == ["configuration"] and skipped[0]["elements"] == [1, 2],
          chain.warnings)
    chain = bm.build_chain(_read(BASE % ("S30", confs + gravity, sel1), "C"), [1])
    check("gravity in the configuration: used", chain.elements[0].correct and chain.elements[0].q[2] < 0.0)

    card = (u'<orientation selection="o"><vx>0</vx><vy>0</vy><vz>1</vz>'
            u'<suppressed configuration="C" /></orientation>')
    sel = u'<elementselection name="o" direct="true"><element eid="1" /></elementselection>'
    body = BASE % ("S30", confs + card, sel)
    model = _read(body, "C")
    check("card suppressed in the configuration: default axes", model.elements[1].vector is None)
    model = _read(body, "D")
    check("card in another configuration: used", model.elements[1].vector == (0.0, 0.0, 1.0))
    check("card, configuration unknown: refused",
          error_code(bm.build_chain, _read(body), [1]) == "orientation_configuration")
    check("card, configuration unknown: other elements open",
          error_code(bm.build_chain, _read(body), [2]) is None)

    named = u'<configuration name="A &amp; B" />'
    load_ab = load.replace(u'configuration="C"', u'configuration="A &amp; B"')
    path = _liml(BASE % ("S30", named + load_ab, sel1))
    try:
        head = bu.scan_head(path)
        check("a name with an entity: the head unescapes it", head["configurations"] == [u"A & B"],
              head["configurations"])
        chain = bm.build_chain(bm.read_model(path, head["configurations"][0]), [1])
        check("a name with an entity: suppressed", chain.elements[0].q == (0.0, 0.0, 0.0))
        check("a configuration not in the file: refused",
              error_code(bm.read_model, path, u"A &amp; B") == "configuration_missing")
    finally:
        os.remove(path)

    # finding 3: the reason analysis names its elements
    good = u'<linepressure selection="s"><x>0 N/m</x><y>-2 N/mm</y><z>0 N/m</z></linepressure>'
    chain = bm.build_chain(synthetic(good, sel1, analysis="V30"), [1, 2])
    skipped = [w["data"] for w in chain.warnings if w["code"] == "correction_skipped"]
    check("analysis: the loaded elements named", skipped and skipped[0].get("elements") == [1],
          skipped)
    text = bu.warning_text("correction_skipped", skipped[0] if skipped else {})
    check("analysis: count in the text", u"?" not in text, text)

    # finding 5: a bad section in the kinematics route is a coded error
    chain = bm.build_chain(synthetic(), [1])
    chain.elements[0].geometric = dict(chain.elements[0].geometric, recta=u"0 mm")
    motion = {1: dict((v, [0.0, 0.0]) for v in bm.MOTION_VARS)}
    check("kinematics, bad section: coded error",
          error_code(bm.forces_from_kinematics, chain, motion) == "bad_dimensions")


def test_chain():
    print("-- chain")
    model = synthetic()
    chain = bm.build_chain(model, [3, 2, 1])
    check("order by shared nodes", [e.eid for e in chain.elements] in ([1, 2, 3], [3, 2, 1]))
    check("fewest reversed", sum(e.reversed for e in chain.elements) == 1)
    check("arc length", [e.s1 for e in chain.elements] == [1.0, 2.0, 3.0] and chain.length == 3.0)
    check("twist warned", "section_twist" in [w["code"] for w in chain.warnings], chain.warnings)
    check("reversed neighbour: the same section, mirrored",
          "section_mirrored" in [w["code"] for w in chain.warnings] and
          "section_change" not in [w["code"] for w in chain.warnings], chain.warnings)
    check("branch", error_code(bm.build_chain, model, [1, 2, 3, 4]) == "branch")
    check("gap", error_code(bm.build_chain, model, [1, 3]) == "gap")
    check("empty", error_code(bm.build_chain, model, []) == "empty")
    check("unknown element", error_code(bm.build_chain, model, [99]) == "unknown_element")
    check("truss is no beam", error_code(bm.build_chain, model, [5]) == "not_a_beam")
    kink = bm.build_chain(model, [2, 4])
    codes = dict((w["code"], w["data"]) for w in kink.warnings)
    check("kink warned with the angle", abs(codes["kink"]["degrees"] - 90.0) < 1e-9, codes)
    check("elements may repeat in the input", len(bm.build_chain(model, [1, 1]).elements) == 1)
    line3 = bm.build_chain(model, [6])
    check("line3 corners, chord frame", abs(line3.length - 1.0) < 1e-12 and
          line3.elements[0].axes[0] == (1.0, 0.0, 0.0))
    check("shared node with a line3 mid node is no junction",
          error_code(bm.build_chain, model, [1, 2, 6]) == "branch")

    curved = _liml(BASE.replace(u'nid="6" x="1.5" y="0"', u'nid="6" x="1.5" y="0.5"') % ("S30", "", ""))
    model2 = bm.read_model(curved)
    os.remove(curved)
    arc = bm.build_chain(model2, [6])
    exact = math.sqrt(1.25) + 0.25 * math.asinh(2.0)        # the parabola through the nodes
    check("curved line3: arc length and warning", abs(arc.length - exact) < 1e-3 and
          "curved_element" in [w["code"] for w in arc.warnings], arc.length)

    print("-- at a junction, outside, missing")
    raw = dict((e, dict((v, [float(e), float(e)]) for v in bm.FORCE_VARS)) for e in (1, 2, 3))
    chain = bm.build_chain(model, [1, 2, 3])
    first = chain.elements[0].eid
    check("junction goes downstream", bm.forces_at(chain, raw, 1.0)["element"].eid ==
          chain.elements[1].eid)
    check("the end of the chain is in the last element",
          bm.forces_at(chain, raw, chain.length)["element"].eid == chain.elements[-1].eid)
    check("xi along the chain", abs(bm.forces_at(chain, raw, 0.25)["xi"] - 0.25) < 1e-12 and
          bm.forces_at(chain, raw, 0.25)["element"].eid == first)
    check("outside", error_code(bm.forces_at, chain, raw, 3.5) == "outside_chain")
    check("no forces", error_code(bm.forces_at, chain, {}, 1.5) == "no_section_forces")
    partial = {1: {"tensileforce": [0.0, 0.0], "bendingmomv": [0.0, 0.0], "bendingmomw": [0.0, 0.0],
                   "sheary": [0.0, 0.0]}}
    check("partly stored forces", error_code(bm.forces_at, bm.build_chain(model, [1]), partial,
                                             0.5) == "no_section_forces")

    print("-- fold back, tie, mirrored frame")
    model = synthetic()
    model.elements[7] = bm.Element(7, "line2", (1, 3), "M", None, 0.0, False)
    check("closed loop", error_code(bm.build_chain, model, [1, 2, 7]) == "closed_loop")
    model.nodes[8] = (1.5, 0.0, 0.0)
    model.elements[8] = bm.Element(8, "line2", (1, 8), "M", None, 0.0, False)
    model.elements[9] = bm.Element(9, "line2", (8, 2), "M", None, 0.0, False)
    check("fold back of two elements", error_code(bm.build_chain, model, [8, 9]) == "fold_back")
    print("-- #ASTER-SECTIE offset")
    data, info = load_model("sections")
    ch = bm.build_chain(data, info["beams"]["n_Gen"]["eids"])
    spec = bm.section_spec(data, ch.elements[0], aster_offset=True)
    check("offset read", spec["offset"] == (0.001, -0.002))
    check("offset only when asked", bm.section_spec(data, ch.elements[0])["offset"] is None)
    plain = bm.build_chain(synthetic(), [1]).elements[0]
    check("an element without the line has no offset", plain.aster_offset is None)


def test_reversed_between():
    print("-- a reversed element between normal ones")
    # a reversed element between normal ones, default orientation and with a vector:
    # the same stress at the same physical point. Only sigma and invariants: the tau
    # components are (sw tau_v, sv tau_w) of the element axes.
    for name, keys in (("signs", ("mid_rev", "mid_rev_vec")),):
        data, _res, info, raw = load(name)
        for key in keys:
            ch = bm.build_chain(data, info["beams"][key]["eids"])
            element = next(e for e in ch.elements if e.reversed)
            sv, sw = element.signs
            check("{0}: V kept next to the neighbour".format(key),
                  np.dot(element.frame[1], ch.elements[0].frame[1]) > 0.99)
            check("{0}: one axis flips, right-handed".format(key), sv * sw == -1 and np.allclose(
                np.cross(element.frame[1], element.frame[2]), element.frame[0]))
            at = bm.forces_at(ch, raw, element.s0 + 0.3 * element.length)
            drawn, own = at["forces"], at["element_forces"]
            spec = bm.section_spec(data, element)
            mirrored = ss.SectionField(spec["loops"], nu=spec["nu"], props=spec["props"],
                                       n_target=500)
            base = sp.loops_for(element.geometric, element.section)
            plain = ss.SectionField(base["loops"], nu=spec["nu"], props=base["props"], n_target=500)
            s1, s2 = mirrored.stress(drawn), plain.stress(own)
            m1, m2 = ss.measures(s1), ss.measures(s2)
            check("{0}: largest sigma the same".format(key), abs(
                np.abs(m1["sigma"]).max() / np.abs(m2["sigma"]).max() - 1.0) < 1e-9)
            # two different meshes, outside the corners
            check("{0}: tau and von Mises outside the corners the same".format(key), all(
                abs(abs(mirrored.extremes(m1[k])["outside"]["value"]) /
                    abs(plain.extremes(m2[k])["outside"]["value"]) - 1.0) < 0.02
                for k in ("tau", "vonmises")))
            check("{0}: resultants in drawn axes back".format(key), all(
                abs(mirrored.resultants(s1)[k] - drawn[k]) <= 1e-6 * (abs(drawn[k]) + 1.0)
                for k in ("N", "Vv", "Vw", "Mv", "Mw")))
            for v, w in ((0.03, 0.02), (-0.04, 0.01)):
                a = _sigma_at(mirrored, drawn, sv * v, sw * w)
                b = _sigma_at(plain, own, v, w)
                check("{0}: sigma at the same physical point".format(key),
                      abs(a - b) <= 1e-9 * (abs(b) + 1.0))
            check("{0}: mirrored outline has another key".format(key),
                  bm.section_key(element) != bm.section_key(ch.elements[0]))


def test_detection():
    print("-- detection per solution")
    model = synthetic(u'<linepressure selection="s"><y>-100 N/m</y></linepressure>',
                      u'<elementselection name="s" direct="true"><element eid="1" />'
                      u'<element eid="2" /></elementselection>')
    chain = bm.build_chain(model, [1, 2, 3])
    rows = {}

    def row(v0, v1, n=0.0):
        return {"tensileforce": [n, n], "sheary": [v0, v1], "shearz": [0.0, 0.0],
                "torsion": [0.0, 0.0], "bendingmomv": [0.0, 0.0], "bendingmomw": [10.0, 10.0]}

    # element 1 runs along the chain, element 2 is reversed, q along its own -V or +V
    q1 = chain.elements[0].q[1] if chain.elements[0].eid == 1 else chain.elements[2].q[1]
    q2 = next(e.q[1] for e in chain.elements if e.eid == 2)
    rows = {1: row(50.0, 50.0), 2: row(30.0, 30.0), 3: row(0.0, 0.0)}
    check("V equal, q not 0: midpoint", bm.load_convention(chain, rows) == {"line2": "midpoint"})
    rows = {1: row(50.0, 50.0 - q1), 2: row(30.0, 30.0 - q2), 3: row(0.0, 0.0)}
    check("V(h) - V(0) = -q h: end values", bm.load_convention(chain, rows) == {"line2": "end"})
    at = bm.forces_at(chain, rows, next(e.s0 for e in chain.elements if e.eid == 1) + 0.5)
    expect = 10.0 + q1 * (-0.5 * 0.5 / 2.0)
    check("end values: only -q s(h-s)/2 in M", abs(at["element_forces"]["Mw"] - expect) < 1e-12)
    check("end values: V linear", abs(at["element_forces"]["Vv"] - (50.0 - q1 / 2.0)) < 1e-12)
    rows = {1: row(50.0, 50.0), 2: row(30.0, 30.0 - q2), 3: row(0.0, 0.0)}
    check("mixed: unknown", bm.load_convention(chain, rows) == {"line2": "unknown"})
    rows = {1: row(50.0, 50.0 - 0.5 * q1), 2: row(30.0, 30.0), 3: row(0.0, 0.0)}
    check("half way: unknown", bm.load_convention(chain, rows) == {"line2": "unknown"})
    at = bm.forces_at(chain, rows, next(e.s0 for e in chain.elements if e.eid == 1) + 0.5)
    check("unknown: no correction, warned", abs(at["element_forces"]["Mw"] - 10.0) < 1e-12 and
          [w["code"] for w in at["warnings"]] == ["load_convention_unknown"])
    rows = {1: row(1e12, 1e12 + 1e-4), 2: row(1e12, 1e12), 3: row(0.0, 0.0)}
    check("V far above q h: rounding gives no vote", bm.load_convention(chain, rows) == {})
    rows = {1: row(0.0, 0.0), 2: row(0.0, 0.0), 3: row(0.0, 0.0)}
    check("V zero: midpoint", bm.load_convention(chain, rows) == {"line2": "midpoint"})
    empty = bm.build_chain(synthetic(), [1])
    check("no load: no verdict", bm.load_convention(empty, {1: row(5.0, 7.0)}) == {})


DIRECTION = (u"<ccxmodeldefinition>** SECTIE-VORM %s: N=4, A=0.002\n"
             u"** SECTIE-VORM %s: P=-0.05 -0.01 0.05 -0.01 0.05\n"
             u"** SECTIE-VORM %s: P=0.01 -0.05 0.01\n"
             u"** SECTIE-RICHTING %s: %s</ccxmodeldefinition>")
GENERAL = (u'  <elset name="F" material="G"><elem eid="11" shape="line2" nodes="1 2" />'
           u'<elem eid="12" shape="line2" nodes="2 3" /></elset>\n')


def general(line=u"V=0 1 0, U=1 0 0", name=u"G", iyy=u"66666.6666666667 mm⁴",
            izz=u"1666666.66666667 mm⁴"):
    """Beams along X with a flat general section, Ivv and Iww of the outline as given."""
    text = BASE % ("S30", GENERAL + DIRECTION % (name, name, name, name, line), u"")
    text = text.replace(u'iyy="2000000 mm⁴"', u'iyy="%s"' % iyy)
    text = text.replace(u'izz="8000000 mm⁴"', u'izz="%s"' % izz)
    path = _liml(text)
    try:
        return bm.read_model(path)
    finally:
        os.remove(path)


def reasons(chain):
    return sorted(w["data"]["reason"] for w in chain.warnings if w["code"] == "section_direction")


def test_direction():
    print("-- SECTIE-RICHTING")
    model = general()
    check("line read", model.directions == {"G": {"V": (0.0, 1.0, 0.0), "U": (1.0, 0.0, 0.0)}})
    check("a beam that fits: nothing", not bm.build_chain(model, [11, 12]).warnings,
          bm.build_chain(model, [11, 12]).warnings)
    # the same elements with the node order or V changed
    cases = (("V reversed, same U: on its head", (1, 2), (0.0, -1.0, 0.0), ["turned"], 180.0),
             ("V across", (1, 2), (0.0, 0.0, 1.0), ["turned"], 90.0),
             ("reversed nodes, default V: both", (2, 1), None, ["mirrored", "turned"], 180.0),
             ("reversed nodes, same V: mirrored", (2, 1), (0.0, 1.0, 0.0), ["mirrored"], 180.0),
             ("two degrees off", (1, 2), (0.0, 1.0, math.tan(math.radians(2.0))), ["turned"], 2.0))
    for name, nodes, vector, want, degrees in cases:
        element = bm.Element(21, "line2", nodes, "G", vector, 0.0, False)
        model.elements[21] = element
        chain = bm.build_chain(model, [21])
        check(name, reasons(chain) == want, reasons(chain))
        data = [w["data"] for w in chain.warnings if w["code"] == "section_direction"]
        check(name + ": data", data and all(d["material"] == "G" and d["elements"] == [21]
                                            for d in data) and abs(data[-1]["degrees"] - degrees) < 1e-9)
    element = bm.Element(21, "line2", (1, 2), "G", (0.0, 1.0, math.tan(math.radians(0.5))), 0.0, False)
    model.elements[21] = element
    check("half a degree passes", reasons(bm.build_chain(model, [21])) == [])
    element = bm.Element(21, "line2", (2, 5), "G", None, 0.0, False)
    model.elements[21] = element
    check("not parallel to U", "skew" in reasons(bm.build_chain(model, [21])))
    mixed = general()
    mixed.elements[12] = bm.Element(12, "line2", (3, 2), "G", (0.0, 1.0, 0.0), 0.0, False)
    chain = bm.build_chain(mixed, [11, 12])
    found = [w["data"] for w in chain.warnings if w["code"] == "section_direction"]
    check("in the element's own axes: only the reversed element, chain or no chain",
          len(found) == 1 and found[0]["elements"] == [12] and found[0]["reason"] == "mirrored")
    path = _liml(BASE % ("S30", GENERAL + (DIRECTION % ("G", "G", "G", "G", "V=0 0 1, U=1 0 0")).replace(
        u"</ccxmodeldefinition>", u"\n** SECTIE-RICHTING G: V=0 1 0, U=1 0 0\n"
        u"** SECTIE-RICHTING G: V=x, U=1 0 0</ccxmodeldefinition>"), u""))
    model = bm.read_model(path)
    os.remove(path)
    check("newest good line counts, a bad one is skipped and reported",
          model.directions["G"]["V"] == (0.0, 1.0, 0.0) and
          "bad_direction" in [w["code"] for w in model.warnings])
    chain = bm.build_chain(general(line=u"V=0 0 0, U=1 0 0"), [11])
    check("a zero vector is no direction", "bad_direction" in [w["code"] for w in chain.warnings])
    for line in (u"V=0 1 0, V=0 0 1, U=1 0 0", u"V=0 1 0, U=1 0 0, X=1 0 0"):
        chain = bm.build_chain(general(line=line), [11])
        check("refused: " + line, "bad_direction" in [w["code"] for w in chain.warnings])
    element = bm.Element(21, "line2", (2, 5), "G", (1.0, 0.0, 0.0), 0.0, False)
    model = general()
    model.elements[21] = element
    check("a beam across U is skew, not against it", reasons(bm.build_chain(model, [21])) ==
          ["skew", "turned"], reasons(bm.build_chain(model, [21])))
    chain = bm.build_chain(general(name=u"Other"), [11])
    check("a line of another material does not count", reasons(chain) == [])
    for lang in ("nl", "en"):
        bu.settings()["language"] = lang
        text = bu.warning_text("section_direction", {
            "material": "G", "reason": "mirrored", "degrees": 180.0, "elements": [1, 2]})
        check("text says what and how much ({0})".format(lang), u"MecwaySectionProps" in text and
              u"180" in text and u"2 " in text and u"?" not in text, text)

    print("-- outline against the material")
    chain = bm.build_chain(general(), [11, 12])
    check("an outline that fits: nothing", "shape_inertia" not in [w["code"] for w in chain.warnings])
    chain = bm.build_chain(general(iyy=u"1666666.66666667 mm⁴", izz=u"66666.6666666667 mm⁴"), [11, 12])
    found = [w["data"] for w in chain.warnings if w["code"] == "shape_inertia"]
    check("swapped iyy and izz: one warning with both values", len(found) == 1 and
          abs(found[0]["outline_ivv"] - 0.1 * 0.02 ** 3 / 12.0) < 1e-15 and
          abs(found[0]["iyy"] - 1.66666666666667e-06) < 1e-15, found)
    bu.settings()["language"] = "en"
    text = bu.warning_text("shape_inertia", found[0])
    check("text in mm4 with four values", all(t in text for t in (u"66667", u"1666667")) and
          u"?" not in text, text)
    chain = bm.build_chain(general(izz=u"1800000 mm⁴"), [11])
    check("an edited izz is caught", "shape_inertia" in [w["code"] for w in chain.warnings])
    chain = bm.build_chain(general(izz=u"1690000 mm⁴"), [11])
    check("rounding passes", "shape_inertia" not in [w["code"] for w in chain.warnings])
    sections, info = load_model("sections")
    chain = bm.build_chain(sections, info["beams"]["n_Gen"]["eids"])
    check("sections.liml: Gen has an outline of other sizes, as generated",
          [w["data"]["material"] for w in chain.warnings if w["code"] == "shape_inertia"] == ["Gen"])
    for key, beam in sorted(info["beams"].items()):
        if beam["material"] != "Gen":
            codes = [w["code"] for w in bm.build_chain(sections, beam["eids"]).warnings]
            if "shape_inertia" in codes:
                check("no warning on a standard section: " + key, False)

    stale = load_model("sections")[0]
    stale.shapes["Rect"] = stale.shapes["Gen"]
    rect_beam = load_model("sections")[1]["beams"]["n_Rect"]
    chain = bm.build_chain(stale, rect_beam["eids"])
    check("a line left under a standard profile is not used, so no warning",
          "shape_inertia" not in [w["code"] for w in chain.warnings])
    flat = general(izz=u"1666666.66666667 mm⁴")
    flat.shapes["G"]["loops"] = [[(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]]
    chain = bm.build_chain(flat, [11, 12])
    check("an outline without area: one warning, the chain opens",
          [w["code"] for w in chain.warnings].count("shape_inertia") == 1)

    print("-- a shape that is gone")
    model = general(name=u"Old")        # the material is G, the lines stand under the old name
    chain = bm.build_chain(model, [11])
    element = chain.elements[0]
    try:
        bm.section_spec(model, element)
        code = None
    except ss.SectionError as exc:
        code = exc.code
    check("renamed material: no_shape, not a drawing error", code == "no_shape", code)
    check("the chain still opens, without a false direction warning", reasons(chain) == [])
    for lang, words in (("nl", (u"MecwaySectionProps", u"opslaan", u".liml", u"hernoemd")),
                        ("en", (u"MecwaySectionProps", u"sav", u".liml", u"renamed"))):
        bu.settings()["language"] = lang
        text = bu.warning_text("no_shape", {"name": "G", "eid": 11, "what": "x"})
        check("no_shape says what to do ({0})".format(lang), all(w in text for w in words), text)
    bu.settings()["language"] = "en"


def test_direction_measured():
    print("-- measured: a beam with another V axis, Mecway 35, file as Mecway saved it")
    with open(result_file("direction.json")) as f:
        result = json.load(f)
    path = os.path.join(MECWAY, "models", "direction_saved.liml")
    saved = bm.read_model(path)
    with open(os.path.join(MECWAY, "models", "direction.info.json")) as f:
        info = json.load(f)
    raw = dict((int(e), r) for e, r in result["elements"].items())
    want = {"ok": [], "vz": ["turned"], "flip": ["turned"], "card": ["turned"],
            "rev_default": ["mirrored", "turned"], "rev_vec": ["mirrored"],
            "skew": ["skew", "turned"]}
    check("the line survives a save in Mecway", saved.directions.get("Flat") ==
          {"V": (0.0, 1.0, 0.0), "U": (1.0, 0.0, 0.0)})
    for key, beam in sorted(info["beams"].items()):
        chain = bm.build_chain(saved, beam["eids"])
        full_chains[id(chain)] = chain
        compare("V4 direction " + key, saved, beam, None, chain, raw, 1e-6)
        check("warned: " + key, reasons(chain) == want[key], reasons(chain))
    first = info["beams"]["ok"]["eids"][0]
    flip = info["beams"]["flip"]["eids"][0]
    check("the reversed V is real: the same point has another stress in Mecway",
          abs(raw[first]["stressptu"][0] - raw[flip]["stressptu"][0]) > 1e5,
          (raw[first]["stressptu"][0], raw[flip]["stressptu"][0]))


def test_saved():
    print("-- a model as Mecway saves it")
    path = os.path.join(MECWAY, "models", "sections_saved.liml")
    if not os.path.isfile(path):
        check("saved model present", False)
        return
    saved = bm.read_model(path)
    model = bm.read_model(os.path.join(MECWAY, "models", "sections.liml"))
    check("same nodes", all(abs(a - b) < 1e-12 for n in model.nodes for a, b in zip(
        model.nodes[n], saved.nodes[n])) and len(saved.nodes) == len(model.nodes))
    check("same elements", sorted(model.elements) == sorted(saved.elements))
    check("same materials", sorted(model.materials) == sorted(saved.materials))
    for name, mat in sorted(model.materials.items()):
        check(name + ": same properties after a save", sp.solver_props(mat.geometric) ==
              sp.solver_props(saved.materials[name].geometric))
    check("shapes and extra data kept", "Gen" in saved.shapes and "Gen" in saved.aster)


ASTER_LINE = (u"<ccxmodeldefinition>** ASTER-SECTIE G: A=0.002, IY=2e-06, IZ=8e-06%s, "
              u"EY=0.001, EZ=-0.002\n** SECTIE-VORM G: N=4, A=0.002\n"
              u"** SECTIE-VORM G: P=-0.05 -0.01 0.05 -0.01 0.05 0.01 -0.05 0.01"
              u"</ccxmodeldefinition>")


def aster_model(jx=u", JX=1e-06", torsion=u'torsionconstant="1000000 mm⁴"'):
    """The general section G on elements 11 and 12, with an #ASTER-SECTIE line."""
    text = BASE % ("S30", GENERAL + ASTER_LINE % jx, u"")
    text = text.replace(u'torsionconstant="1000000 mm⁴"', torsion)
    path = _liml(text)
    try:
        return bm.read_model(path)
    finally:
        os.remove(path)


def test_aster_check():
    print("-- #ASTER-SECTIE only when A, IY, IZ and JX fit, as MecwayAsterBridge")

    def used(model):
        stale = "aster_section_stale" in [w["code"] for w in model.warnings]
        element = bm.build_chain(model, [11, 12]).elements[0]
        offset = bm.section_spec(model, element, aster_offset=True)["offset"]
        return "G" in model.aster, stale, element.aster_offset, offset

    check("JX fits: line used", used(aster_model()) == (True, False, (0.001, -0.002),
                                                     (0.001, -0.002)), used(aster_model()))
    check("JX within the tolerance: line used", used(aster_model(u", JX=1.0000005e-06"))[0])
    for name, model in (
            ("JX edited", aster_model(u", JX=1.01e-06")),
            ("JX missing in the line", aster_model(u"")),
            ("no torsion constant, JX not 0", aster_model(torsion=u"")),
            ("empty torsion constant, JX not 0", aster_model(torsion=u'torsionconstant=""')),
            ("torsion constant with a wrong unit", aster_model(torsion=u'torsionconstant="1 m"'))):
        check(name + ": line dropped with a warning, no EY/EZ",
              used(model) == (False, True, None, None), used(model))
    check("no torsion constant and JX=0: line used, as the bridge",
          used(aster_model(u", JX=0", torsion=u""))[0])
    check("an unknown key: line dropped, as the bridge",
          not used(aster_model(u", JX=1e-06, XX=1"))[0])
    check("keys in lower case: line used, as the bridge", used(aster_model(u", jx=1e-06"))[0])
    model = aster_model()
    model.aster["G"].pop("IZ")
    bm._check_aster(model)
    check("IZ missing in the line: line dropped", "G" not in model.aster)
    text = BASE % ("S30", GENERAL + u"<ccxmodeldefinition>** ASTER-SECTIE M: A=0.005, IY=1e-06, "
                   u"IZ=4e-06, JX=1e-06, EY=0.001, EZ=0</ccxmodeldefinition>", u"")
    path = _liml(text)
    try:
        model = bm.read_model(path)
    finally:
        os.remove(path)
    check("a line for a material that is not a general section: dropped with a warning",
          "M" not in model.aster and "aster_section_stale" in [w["code"] for w in model.warnings])
    model = aster_model(u", JX=1e-06\n** ASTER-SECTIE no colon")
    check("a line without a material: a warning", "bad_aster_section" in
          [w["code"] for w in model.warnings] and "G" in model.aster)

def main():
    tests = [test_signs, test_stressptu, test_distributed, test_kinematics, test_ccx, test_read,
             test_configurations, test_chain, test_reversed_between, test_detection, test_direction,
             test_direction_measured, test_saved, test_aster_check]
    for test in tests:
        run(test)
    print("")
    for group, worst in sorted(WORST.items()):
        print("worst {0}: {1:.2e}".format(group, worst))
    return summary(len(tests))


if __name__ == "__main__":
    sys.exit(main())
