# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of Scripts/section_stress.py against exact solutions (V1), thin-walled
# values (V2) and a section turned to its principal axes (V3), see
# docs/research/01_vooronderzoek.md. Run with a Python with numpy and scipy:
#
#   python tests/test_section_stress.py
#
# Reruns itself with the Python of a Code-Aster installation if needed.

from __future__ import print_function

import glob
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

import numpy as np  # noqa: E402
import section_stress as ss  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


def near(a, b, tol):
    return abs(a - b) <= tol * max(abs(b), 1e-300)


def box(v0, w0, v1, w1):
    return [[v0, w0], [v1, w0], [v1, w1], [v0, w1]]


def circle(r, n=720):
    return [[r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n)]
            for i in range(n)]


def i_section(h=0.2, b=0.1, tf=0.01, tw=0.006):
    return [[[-b / 2, 0], [b / 2, 0], [b / 2, tf], [tw / 2, tf], [tw / 2, h - tf],
             [b / 2, h - tf], [b / 2, h], [-b / 2, h], [-b / 2, h - tf], [-tw / 2, h - tf],
             [-tw / 2, tf], [-b / 2, tf]]]


def channel(h=0.2, b=0.08, tf=0.01, tw=0.008):
    return [[[0, 0], [b, 0], [b, tf], [tw, tf], [tw, h - tf], [b, h - tf], [b, h], [0, h]]]


ALL = {"N": 1200.0, "Vv": 300.0, "Vw": -700.0, "T": 45.0, "Mv": 250.0, "Mw": -410.0}

# --- V1: exact solutions --------------------------------------------------
b, h = 0.1, 0.2
f = ss.SectionField([box(0, 0, b, h)], nu=0.0)
s = f.stress({"Vw": 1.0})
top = np.abs(s["tau_w"]).max() / (1.5 / (b * h))
check("V1 rectangle: tau max 1.5 V/A with nu = 0", near(top, 1.0, 5e-3), top)


def rect_torsion(a, c, terms=60):
    """Timoshenko-Goodier: long side c, short side a: J and tau_max / T."""
    s_j = sum(math.tanh(n * math.pi * c / (2 * a)) / n ** 5 for n in range(1, 2 * terms, 2))
    j = a ** 3 * c / 3 * (1 - 192 / math.pi ** 5 * a / c * s_j)
    s_t = sum(1 / (n ** 2 * math.cosh(n * math.pi * c / (2 * a))) for n in range(1, 2 * terms, 2))
    k = a * (1 - 8 / math.pi ** 2 * s_t)
    return j, k / j


j, k = rect_torsion(b, h)
check("V1 rectangle: J against the series", near(f.j, j, 1e-3), f.j / j)
s = f.stress({"T": 1.0})
peak = np.hypot(s["tau_v"], s["tau_w"]).max()
check("V1 rectangle: tau max against the series", near(peak, k, 5e-3), peak / k)
r = f.resultants(s)
check("V1 rectangle: torque back", near(r["Tc"], 1.0, 1e-6), r["Tc"])

R = 0.05
f = ss.SectionField([circle(R)], nu=0.3, n_target=12000)
s = f.stress({"T": 1.0})
peak = np.hypot(s["tau_v"], s["tau_w"]).max()
check("V1 circle: tau max T r / J", near(peak, R / (math.pi * R ** 4 / 2), 5e-3), peak)
s = f.stress({"Vw": 1.0})
A = f.geo["area"]
mid = np.hypot(f._node_vw[:, :, 0], f._node_vw[:, :, 1]) < 0.02 * R
centre = float(s["tau_w"][mid].mean())
check("V1 circle: tau at the centre (1.5 + nu) / (1 + nu) V/A",
      near(centre, (1.5 + 0.3) / 1.3 / A, 5e-3), centre * A)

Ro, Ri = 0.05, 0.04
f = ss.SectionField([circle(Ro), circle(Ri)], nu=0.3, n_target=12000)
s = f.stress({"T": 1.0})
peak = np.hypot(s["tau_v"], s["tau_w"]).max()
jt = math.pi * (Ro ** 4 - Ri ** 4) / 2
check("V1 tube: J", near(f.j, jt, 5e-3), f.j / jt)
check("V1 tube: tau max T Ro / J", near(peak, Ro / jt, 5e-3), peak * jt / Ro)

# --- V2: thin-walled values and resultants -----------------------------------
sec = i_section()
f = ss.SectionField(sec, nu=0.3)
H, B, TF, TW = 0.2, 0.1, 0.01, 0.006
Q = B * TF * (H - TF) / 2 + TW * (H / 2 - TF) ** 2 / 2
Ix = f.mesh_props["ivv"]
s = f.stress({"Vw": 1.0})
na = (np.abs(f._node_vw[:, :, 1]) < 0.002) & (np.abs(f._node_vw[:, :, 0]) < TW / 2)
web = np.abs(s["tau_w"][na]).max() / (Q / (Ix * TW))
check("V2 I section: web on the neutral axis V Q / I t", near(web, 1.0, 1e-3), web)
s = f.stress(ALL)
r = f.resultants(s)
for key in ("N", "Mv", "Mw", "Vv", "Vw"):
    check("V2 I section: resultant " + key, near(r[key], ALL[key], 1e-6), r[key])

f = ss.SectionField(channel(), nu=0.3)
s = f.stress(ALL)
r = f.resultants(s)
for key in ("N", "Mv", "Mw", "Vv", "Vw"):
    check("V2 channel: resultant " + key, near(r[key], ALL[key], 1e-6), r[key])
check("V2 channel: torque about the centroid is the T of the solver",
      near(r["Tc"], ALL["T"], 1e-6), r["Tc"])
hc, bc, tfc, twc = 0.19, 0.076, 0.01, 0.008
e = 3 * bc ** 2 * tfc / (6 * bc * tfc + hc * twc)       # S outside the web
xs_thin = twc / 2 - e - f.centroid[0]
ts, term = f.torque_at_shear_centre({"Vw": 1000.0})
check("V2 channel: load through the centroid twists about S, sign and size thin-walled",
      near(term, -xs_thin * 1000.0, 0.03), (term, -xs_thin * 1000.0))
s2 = f.stress({"Vw": 1000.0}, about_shear_centre=True)
check("V2 channel: T about S, no conversion: moment about the centroid is V e",
      near(f.resultants(s2)["Tc"], f.shear_centre[0] * 1000.0, 1e-6))
check("V2 channel: shear centre near the thin-walled value",
      abs(f.shear_centre[0] - xs_thin) / e < 0.03, (f.shear_centre[0], xs_thin))

# --- V3: a section turned to its principal axes -----------------------------
L = [[0, 0], [0.09, 0], [0.09, 0.008], [0.008, 0.008], [0.008, 0.05], [0, 0.05]]
a = ss.SectionField([L], nu=0.3, n_target=6000)
ivw = a.mesh_props["ivw"]
check("V3 L section: Ivw is not zero", abs(ivw) > 1e-8, ivw)
p = a.mesh_props
phi = 0.5 * math.atan2(2 * p["ivw"], p["iww"] - p["ivv"])       # v' axis at phi from v
c, sn = math.cos(phi), math.sin(phi)
turned = [[c * x + sn * y, -sn * x + c * y] for x, y in L]
t = ss.SectionField([turned], nu=0.3, n_target=6000)
check("V3 turned section: Ivw is zero", abs(t.mesh_props["ivw"]) < 1e-3 * abs(ivw),
      t.mesh_props["ivw"])
fa = {"N": 500.0, "Mv": 120.0, "Mw": -80.0, "Vv": 900.0, "Vw": 400.0, "T": 30.0}
ft = {"N": 500.0, "T": 30.0,
      "Mv": c * fa["Mv"] + sn * fa["Mw"], "Mw": -sn * fa["Mv"] + c * fa["Mw"],
      "Vv": c * fa["Vv"] + sn * fa["Vw"], "Vw": -sn * fa["Vv"] + c * fa["Vw"]}
sa, st = a.stress(fa), t.stress(ft)
pts = np.array(L, dtype=float) - a.centroid


def at_point(field, values, pt):
    """Value at the element node on point pt."""
    d = np.hypot(field._node_vw[:, :, 0] - pt[0], field._node_vw[:, :, 1] - pt[1])
    i = np.unravel_index(int(np.argmin(d)), d.shape)
    assert d[i] < 1e-9
    return float(values[i])


worst = 0.0
for q in pts:
    q2 = np.array([c * q[0] + sn * q[1], -sn * q[0] + c * q[1]])
    worst = max(worst, abs(at_point(a, sa["sigma"], q) / at_point(t, st["sigma"], q2) - 1.0))
check("V3 sigma at the corners equal in both axes", worst < 1e-6, worst)
va = a.extremes(ss.measures(sa)["vonmises"])["outside"]["value"]
vt = t.extremes(ss.measures(st)["vonmises"])["outside"]["value"]
check("V3 von Mises maximum equal in both axes", near(va, vt, 1e-2), (va, vt))
r = a.resultants(sa)
check("V3 L section: bending resultants with Ivw",
      near(r["Mv"], fa["Mv"], 1e-6) and near(r["Mw"], fa["Mw"], 1e-6), r)

# --- properties of the solver, measures, separate parts, corners ------------
f = ss.SectionField([box(0, 0, b, h)], nu=0.3, props={"A": 0.04, "ivv": 1e-4, "iww": 2e-5, "ivw": 0.0})
s = f.stress({"N": 400.0})
check("solver properties are used for sigma", near(float(s["sigma"].mean()), 1e4, 1e-9))
s = {"sigma": np.array([[3.0]]), "tau_v": np.array([[4.0]]), "tau_w": np.array([[0.0]])}
m = ss.measures(s)
check("measures: von Mises", near(float(m["vonmises"][0, 0]), math.sqrt(9 + 48), 1e-12))
check("measures: Tresca", near(float(m["tresca"][0, 0]), math.sqrt(9 + 64), 1e-12))
check("measures: principal", near(float(m["principal1"][0, 0]), 1.5 + math.sqrt(2.25 + 16), 1e-12))

f = ss.SectionField([box(0, 0, 0.01, 0.01), box(0.02, 0, 0.03, 0.01)], nu=0.3, n_target=800)
s = f.stress({"N": 1.0, "Vv": 1.0})
check("two parts: sigma, no shear", s["tau_v"] is None and "separate_parts" in f.warnings)
check("two parts: measures gives sigma only", list(ss.measures(s).keys()) == ["sigma"])


def corners_of(loops):
    return ss.reentrant_corners(ss.section_mesh.mesh_section(loops, n_target=300)["loops"])


corners = corners_of(sec)
check("I section: four re-entrant corners", len(corners) == 4, len(corners))
check("I section: local thickness is the web", all(near(t_, TW, 1e-9) for _, t_ in corners),
      [t_ for _, t_ in corners])
split = [box(-B / 2, 0, B / 2, TF), box(-TW / 2, TF, TW / 2, H - TF), box(-B / 2, H - TF, B / 2, H)]
check("I section of three adjacent rectangles: same corners", len(corners_of(split)) == 4,
      len(corners_of(split)))
repeat = [p for p in sec[0][3:] + sec[0][:3]] + [sec[0][3]]
check("loop with a repeated closing point: same corners", len(corners_of([repeat[:-1]])) == 4)
double = [sec[0][0], sec[0][1], sec[0][2], sec[0][3], [TW / 2, TF + 1e-12]] + sec[0][4:]
got = corners_of([double])
check("nearly double point: thickness stays the web",
      len(got) == 4 and all(near(t_, TW, 1e-6) for _, t_ in got), got)
hole = corners_of([box(0, 0, 1, 1), box(0.4, 0.4, 0.6, 0.6)])
check("square hole: its corners are re-entrant, the outside is not", len(hole) == 4, len(hole))
check("circle: no re-entrant corner", corners_of([circle(1.0, 180)]) == [])
check("tube: no re-entrant corner", corners_of([circle(1.0, 90), circle(0.5, 90)]) == [])
for n_ in (4, 9):
    arc = [[0.03 + 0.02 * math.cos(math.radians(270 - 90.0 * i / n_)),
            0.03 + 0.02 * math.sin(math.radians(270 - 90.0 * i / n_))] for i in range(n_ + 1)]
    got = corners_of([[[0, 0], [0.1, 0], [0.1, 0.01]] + arc + [[0.01, 0.1], [0, 0.1]]])
    check("fillet of {0} facets does not count".format(n_), got == [], got)

f = ss.SectionField([box(0, 0, b, h)], nu=0.3, n_target=300)
s = f.stress({"Mv": 1.0, "N": 1.0})
const = f.nodal(np.full(f.elements.shape, 7.0))
check("nodal: a constant stays", np.allclose(const[np.unique(f.elements)], 7.0))
line = f.nodal(s["sigma"])
exact = 1.0 / f.props["A"] + f.xy[:, 1] / f.props["ivv"]
check("nodal: a linear field stays", np.allclose(line[np.unique(f.elements)], exact[np.unique(f.elements)]))
for bad in ({"A": 0.0, "ivv": 1.0, "iww": 1.0, "ivw": 0.0}, {"A": 1.0, "ivv": 1.0, "iww": 1.0, "ivw": 1.0},
            {"A": 1.0}):
    try:
        ss.SectionField([box(0, 0, b, h)], props=bad, n_target=100)
        check("bad properties refused", False, bad)
    except ss.SectionError as exc:
        check("bad properties refused", exc.code == "bad_properties", exc.code)
try:
    ss.SectionField([[[0, 0], [1, 0]]], n_target=100)
    check("no area refused", False)
except ss.SectionError as exc:
    check("no area refused", exc.code == "mesh_failed", exc.code)

f = ss.SectionField(sec, nu=0.3)
s = f.stress({"T": 1.0})
e = f.extremes(ss.measures(s)["tau"])
check("I section: peak in the corner, outside it on the flange",
      e["overall"]["value"] > 1.3 * e["outside"]["value"] and not e["outside_empty"], e)
vals = ss.mesh_sensitivity(f, {"T": 1.0}, measure="tau")
check("I section: maximum outside the corners within 3 % on two meshes", vals[2] < 0.03, vals)
s = f.stress({"Mv": -1.0})
e = f.extremes(s["sigma"])
check("extremes keeps the sign", e["overall"]["value"] != abs(e["overall"]["value"]) or
      e["overall"]["point"][1] < 0.0, e["overall"])

print("{0} failed".format(len(FAILED)) if FAILED else "All checks OK")
sys.exit(1 if FAILED else 0)
