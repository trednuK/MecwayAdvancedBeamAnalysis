# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of Scripts/section_profiles.py: the units of a .liml (V11) and the nine
# section types against what Mecway 35 measured on own models (tests/mecway):
# properties from tip displacements, axes from the stress recovery points, part
# of V5. Run with a Python with numpy and scipy:
#
#   python tests/test_section_profiles.py
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

import beam_model as bm  # noqa: E402
import section_profiles as sp  # noqa: E402
import section_stress as ss  # noqa: E402

FAILED = []
MECWAY = os.path.join(_HERE, "mecway")


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
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


def raises(code, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ss.SectionError as exc:
        return exc.code == code
    return False


def test_units():
    print("-- V11: unit strings, exact")
    cases = [
        (u"10000 mm⁴", "inertia", 1e4 * 1e-12), (u"2000 mm²", "area", 2e-3),
        (u"1 cm⁴", "inertia", 1e-8), (u"100 mm", "length", 0.1), (u"0.1 m", "length", 0.1),
        (u"2 in", "length", 0.0508), (u"1 ft", "length", 0.3048), (u"3 µm", "length", 3e-6),
        (u"210 GPa", "pressure", 210e9), (u"210000 MPa", "pressure", 210e9),
        (u"210000 N/mm²", "pressure", 210e9), (u"2 bar", "pressure", 2e5),
        (u"1 psi", "pressure", 4.4482216152605 / 0.0254 ** 2),
        (u"7850 kg/m³", "density", 7850.0), (u"7.85 g/cm³", "density", 7850.0),
        (u"7.85e-09 t/mm³", "density", 7850.0), (u"-10 m/s²", "acceleration", -10.0),
        (u"-9810 mm/s²", "acceleration", -9.81), (u"100 N/m", "line_load", 100.0),
        (u"1 N/mm", "line_load", 1000.0), (u"2 kN/m", "line_load", 2000.0),
        (u"1 lbf/in", "line_load", 4.4482216152605 / 0.0254), (u"3 N.m", "moment", 3.0),
        (u"3 N·m", "moment", 3.0), (u"3 kN.m", "moment", 3000.0),
        (u"30 °", "angle", math.radians(30.0)), (u"30 deg", "angle", math.radians(30.0)),
        (u"0.5 rad", "angle", 0.5), (u"0.5", "angle", 0.5), (u"2.60028546770801E-05 m⁴",
                                                            "inertia", 2.60028546770801e-05),
        (u"1000", "line_load", 1000.0), (u"  -1.5e3   N  ", "force", -1500.0),
    ]
    for text, quantity, want in cases:
        got = sp.parse_value(text, quantity)
        check(u"{0} as {1}".format(text, quantity).encode("ascii", "replace").decode(),
              abs(got - want) <= 4e-16 * abs(want), got)
    for text, quantity in ((u"10 mm²", "inertia"), (u"10 N", "pressure"), (u"10 mN.m/x", "moment"),
                           (u"10 foo", "length"), (u"", "length"), (None, "length"),
                           (u"x", "length"), (u"10 MM", "length"), (u"5 pa", "pressure")):
        try:
            sp.parse_value(text, quantity)
            ok = False
        except sp.UnitError:
            ok = True
        check("refused: {0!r}".format(text), ok)
    check("milli and mega apart", sp.parse_value("1 mN", "force") == 1e-3 and
          sp.parse_value("1 MN", "force") == 1e6)


def _load(name):
    with open(result_file(name + ".json")) as f:
        result = json.load(f)
    with open(os.path.join(MECWAY, "models", name + ".info.json")) as f:
        info = json.load(f)
    return _model(name), result, info


def _model(name):
    """The model only: it is in git."""
    return bm.read_model(os.path.join(MECWAY, "models", name + ".liml"))


def _sigma(props, e, v, w):
    n, mv, mw = e["tensileforce"][0], e["bendingmomv"][0], e["bendingmomw"][0]
    return n / props["A"] + mv * w / props["ivv"] - mw * v / props["iww"]


def test_measured():
    print("-- solver properties from tip displacements, axes from stress points (measured)")
    model, result, info = _load("sections")
    young = model.materials["Rect"].young
    check("V11: E 210 GPa", young == 210e9)
    for mat, row in sorted(result["material_properties"].items()):
        geo = model.materials[mat].geometric
        for key in ("recta", "rectb", "rectthickness", "roundod", "roundid"):
            if key in row:
                check("V11: {0} {1} against the API".format(mat, key),
                      abs(sp.parse_value(geo[key], "length") - row[key]) <= 1e-15 * row[key])
    gen = model.materials["Gen"].geometric
    api = result["material_properties"]["Gen"]
    check("V11: general section against the API",
          abs(sp.parse_value(gen["area"], "area") - api["area"]) <= 1e-15 * api["area"] and
          abs(sp.parse_value(gen["iyy"], "inertia") - api["iyy"]) <= 1e-15 * api["iyy"] and
          abs(sp.parse_value(gen["izz"], "inertia") - api["izz"]) <= 1e-15 * api["izz"])
    for key, beam in sorted(info["beams"].items()):
        if not key.startswith("n_"):
            continue
        mat = beam["material"]
        geo = model.materials[mat].geometric
        props = sp.solver_props(geo)
        tip = result["nodes"][str(beam["tip"])]
        f, m = beam["F"], beam["M"]
        # Euler cantilever, length 1 m, V = Y, W = Z
        area = f[0] / (young * tip["displx"])
        izz = (f[1] / 3.0 + m[2] / 2.0) / (young * tip["disply"])
        iyy = (f[2] / 3.0 - m[1] / 2.0) / (young * tip["displz"])
        check(mat + ": A, iyy, izz of the solver",
              max(abs(area / props["A"] - 1.0), abs(izz / props["iww"] - 1.0),
                  abs(iyy / props["ivv"] - 1.0)) < 1e-9)
        element = result["elements"][str(beam["eids"][0])]
        got = _sigma(props, element, sp.parse_value(geo["pointy"], "length"),
                     sp.parse_value(geo["pointz"], "length"))
        check(mat + ": stressptu at pointy, pointz = V, W about the centroid (V5)",
              abs(got / element["stressptu"][0] - 1.0) < 1e-6)
        if mat in ("Gen", "Circ", "Tube"):
            continue
        loop = sp.loops_for(geo, model.shapes.get(mat))["loops"][0]
        found = []
        for k in range(1, 5):
            value = element["stresspt%d" % k][0]
            errors = [abs(_sigma(props, element, v, w) - value) for v, w in loop]
            found.append(errors.index(min(errors)))
            check("{0}: stresspt{1} is a corner of the outline".format(mat, k),
                  min(errors) <= 1e-6 * abs(value))
        check(mat + ": four different corners", len(set(found)) == 4)


def _assert_polygon(name, section, props, tol):
    loops = section["loops"]
    area = sx = sy = xx = yy = xy = 0.0
    for sign, loop in zip((1.0, -1.0), loops):
        m = sp._moments(loop)
        s = sign * (1.0 if m[0] > 0.0 else -1.0)
        area += s * m[0]
        sx += s * m[1]
        sy += s * m[2]
        xx += s * m[3]
        yy += s * m[4]
        xy += s * m[5]
    check(name + ": polygon centred on the centroid",
          math.hypot(sx, sy) / area < 1e-9 * math.sqrt(area))
    check(name + ": polygon has the solver's A, ivv, iww, no ivw",
          abs(area / props["A"] - 1.0) < tol and abs(yy / props["ivv"] - 1.0) < 2.0 * tol and
          abs(xx / props["iww"] - 1.0) < 2.0 * tol and
          abs(xy) < 1e-9 * math.sqrt(props["ivv"] * props["iww"]))


def test_shapes():
    print("-- loops: consistent with the properties, and a field builds on each")
    model = _model("sections")
    for name, mat in sorted(model.materials.items()):
        section = sp.loops_for(mat.geometric, model.shapes.get(name))
        tol = 3e-4 if name in ("Circ", "Tube") else 1e-12
        if name != "Gen":
            _assert_polygon(name, section, section["props"], tol)
        field = ss.SectionField(section["loops"], nu=0.3, props=section["props"], n_target=400)
        check(name + ": SectionField", field.parts == 1 and
              math.hypot(*field.centroid) < 1e-6 * math.sqrt(section["props"]["A"]))
    print("-- errors are codes")
    rect = {"type": "RectBar", "recta": "10 mm", "rectb": "5 mm"}
    check("no_shape", raises("no_shape", sp.loops_for, {
        "type": "UniformGeneralSection", "area": "1 m²", "iyy": "1 m⁴", "izz": "1 m⁴"}))
    check("stale_shape", raises("stale_shape", sp.loops_for, {
        "type": "UniformGeneralSection", "area": "1 m²", "iyy": "1 m⁴", "izz": "1 m⁴"},
        {"loops": [[(0, 0), (1, 0), (0, 1)]], "area": 0.5}))
    check("unknown_section", raises("unknown_section", sp.loops_for, {"type": "Plate"}))
    check("missing_dimension", raises("missing_dimension", sp.loops_for, {"type": "RectBar",
                                                                          "recta": "1 m"}))
    check("bad_value", raises("bad_value", sp.loops_for, dict(rect, recta="1 kg")))
    check("bad_dimensions", raises("bad_dimensions", sp.loops_for, {
        "type": "I", "ia": "10 mm", "ib": "5 mm", "ic": "6 mm", "id": "1 mm"}))
    check("bad_dimensions tube", raises("bad_dimensions", sp.loops_for, {
        "type": "RectTube", "recta": "10 mm", "rectb": "5 mm", "rectthickness": "3 mm"}))
    check("bad_properties", raises("bad_properties", sp.solver_props, {
        "type": "UniformGeneralSection", "area": "0 m²", "iyy": "1 m⁴", "izz": "1 m⁴"}))
    tube = sp.loops_for({"type": "UniformCircularTube", "roundod": "10 mm", "roundid": "0 mm"})
    check("tube without hole is one loop", len(tube["loops"]) == 1)


def test_outline():
    print("-- outline against the properties of the material")
    square = [(-1.0, -2.0), (1.0, -2.0), (1.0, 2.0), (-1.0, 2.0)]
    props = sp.outline_props([square])
    check("rectangle: ivv = int w^2, iww = int v^2", abs(props["A"] - 8.0) < 1e-12 and
          abs(props["ivv"] - 2.0 * 64.0 / 12.0) < 1e-12 and
          abs(props["iww"] - 4.0 * 8.0 / 12.0) < 1e-12)
    check("loop direction does not count", sp.outline_props([square[::-1]]) == props)
    shifted = sp.outline_props([[(v + 5.0, w - 3.0) for v, w in square]])
    check("about its own centroid", abs(shifted["ivv"] - props["ivv"]) < 1e-9 and
          abs(shifted["iww"] - props["iww"]) < 1e-9 and abs(shifted["ivw"]) < 1e-9)
    hole = [(-0.5, -1.0), (0.5, -1.0), (0.5, 1.0), (-0.5, 1.0)]
    island = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
    box = sp.outline_props([square, hole])
    check("a loop inside a loop is a hole", abs(box["A"] - 6.0) < 1e-12 and
          abs(box["ivv"] - (2.0 * 64.0 - 8.0) / 12.0) < 1e-12)
    check("an island in the hole counts", abs(
        sp.outline_props([square, hole, island])["A"] - 6.04) < 1e-12)
    apart = sp.outline_props([square, [(x + 10.0, y) for x, y in square]])
    check("separate parts add up", abs(apart["A"] - 16.0) < 1e-12)

    model = _model("sections")
    for name, mat in sorted(model.materials.items()):
        if name not in ("Gen", "Circ", "Tube"):
            section = sp.loops_for(mat.geometric, model.shapes.get(name))
            check(name + ": a standard outline gives its own properties",
                  sp.inertia_mismatch(section["loops"], section["props"]) is None)
    section = sp.loops_for(model.materials["Gen"].geometric, model.shapes["Gen"])
    found = sp.inertia_mismatch(section["loops"], section["props"])
    check("sections.liml: the outline of Gen does not fit its material, as generated",
          found is not None and abs(found[0] - 0.1 * 0.02 ** 3 / 12.0) < 1e-15 and
          abs(found[1] - 0.02 * 0.1 ** 3 / 12.0) < 1e-15, found)
    print("-- tolerance")
    rect = [[(-0.05, -0.01), (0.05, -0.01), (0.05, 0.01), (-0.05, 0.01)]]
    good = {"A": 0.002, "ivv": 0.1 * 0.02 ** 3 / 12.0, "iww": 0.02 * 0.1 ** 3 / 12.0, "ivw": 0.0}
    check("exact passes", sp.inertia_mismatch(rect, good) is None)
    check("1.8 % as measured on rounded outlines passes", sp.inertia_mismatch(
        rect, dict(good, ivv=good["ivv"] * 1.018, iww=good["iww"] * 0.982)) is None)
    check("swapped is caught", sp.inertia_mismatch(rect, dict(good, ivv=good["iww"],
                                                              iww=good["ivv"])) is not None)
    check("edited by 10 % is caught", sp.inertia_mismatch(
        rect, dict(good, iww=good["iww"] * 1.1)) is not None)
    for factor, want in ((1.05, False), (1.06, True), (0.94, True), (0.96, False)):
        got = sp.inertia_mismatch(rect, dict(good, ivv=good["ivv"] * factor)) is not None
        check("limit at {0} is the same both ways".format(factor), got == want)
    flat = [[(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]]
    check("an outline without area is reported, no crash",
          sp.inertia_mismatch(flat, good) == (0.0, 0.0))
    outer = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)]
    touching = [(0.0, 0.0), (1.0, 0.5), (1.0, 1.0), (0.5, 1.0)]
    check("a hole with one point on the outer edge is a hole",
          abs(sp.outline_props([outer, touching])["A"] - (8.0 - sp._moments(touching)[0])) < 1e-12)
    rounded = [[(float("%.5g" % v), float("%.5g" % w)) for v, w in loop]
               for loop in sp._standard_loops({"type": "UniformCircularTube", "roundod": "80 mm",
                                               "roundid": "60 mm"})]
    check("a rounded tube passes", sp.inertia_mismatch(rounded, sp.solver_props(
        {"type": "UniformCircularTube", "roundod": "80 mm", "roundid": "60 mm"})) is None)


def main():
    tests = [test_units, test_measured, test_shapes, test_outline]
    for test in tests:
        run(test)
    print("")
    return summary(len(tests))


if __name__ == "__main__":
    sys.exit(main())
