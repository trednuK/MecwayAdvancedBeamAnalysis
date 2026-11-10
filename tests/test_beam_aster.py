# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Check V8: Code-Aster solutions through MecwayAsterBridge (develop, commit 6efd62a or
# newer), measured by tests/mecway/run_v8.py, v8_solids.py and run_e2e.py; the results
# stay on this PC, not in git; with them this test needs neither Code-Aster nor Mecway.
#   - beam_forces arrives, with POU_D_E and POU_D_TG, as element values
#   - section forces against statics, T_S against the twist of Code-Aster
#   - FORCE_POUTRE and gravity: forces_at against equilibrium, inside the elements
#   - the window on these solutions in real Mecway, line3 included
#   - the stress against the hex20 solids of V6 and V7, margins of V6
# Run with a Python with numpy and scipy:
#
#   python tests/test_beam_aster.py
#
# Reruns itself with the Python of a Code-Aster installation if needed.
# The parts that need tests/mecway/results/ (not in git) are skipped when it is missing;
# BEAM_REQUIRE_RESULTS=1 makes that a failure.

from __future__ import print_function

import glob
import json
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
import beam_utils as bu  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
FAILED = []
RESULTS = os.path.join(_HERE, "mecway", "results")
MODELS = os.path.join(_HERE, "mecway", "models")
TIP = (100.0, 200.0, -150.0)
TORQUE = (10.0, -20.0, 30.0)
E_MOD, NU = 210e9, 0.3
G_MOD = E_MOD / (2.0 * (1.0 + NU))
MARGIN_SIGMA, MARGIN_TAU = 0.01, 0.05


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


SKIPPED = []
RUNNERS = "tests/mecway/run_v8.py, v8_solids.py and run_e2e.py"


class NoResults(Exception):
    """A result file of a Mecway run is missing. tests/mecway/results/ is not in git (the
    files hold local paths), so on a fresh checkout the parts that need it are skipped."""


def result_file(name):
    """The path of a result file; NoResults when it is not on this PC. A file that is
    there but cannot be read stays a failure."""
    path = os.path.join(RESULTS, name)
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


def near(a, b, tol):
    return abs(a - b) <= tol * max(abs(b), 1e-300)


def load(name):
    with open(result_file("aster_{0}.json".format(name))) as f:
        d = json.load(f)
    check(name + ": solved", d["diagnostic"] == "OK" and not d["errors"], (d["diagnostic"], d["errors"]))
    return d


def raw_of(d):
    out = {}
    for var, data in d["elements"].items():
        eids, values = data["eids"], data["values"]
        size = len(values) // len(eids)
        for i, eid in enumerate(eids):
            out.setdefault(eid, {})[var] = [values[i * size], values[(i + 1) * size - 1]]
    return out


def chain_of(name, d):
    model = bm.read_model(os.path.join(MODELS, "aster_{0}.liml".format(name)))
    model.solver = bm.ASTER
    return model, bm.build_chain(model, d["beams"]["b"]["eids"])


def rotated(d, f):
    """Tip load in global axes to principal axes: (vv, vw)."""
    import math
    t = ((d["info"].get("section") or {}).get("turn")) or 0.0
    c, s = math.cos(t), math.sin(t)
    return c * f[1] + s * f[2], -s * f[1] + c * f[2]


def statics(d, x):
    """Section forces in the axes of the section under tip load and torque."""
    length = d["info"]["length"]
    arm = length - x
    f, m = d["info"]["tip"], d["info"]["moment"]
    my = m[1] - f[2] * arm
    mz = m[2] + f[1] * arm
    vv, vw = rotated(d, f)
    mv, mw = rotated(d, (0.0, my, mz))
    return {"N": f[0], "Vv": vv, "Vw": vw, "T": m[0], "Mv": mv, "Mw": mw}


def test_forces_arrive():
    print("-- beam_forces with POU_D_E and POU_D_TG, against statics")
    for name in ("u_e", "u_t", "u_tg", "u_tgfix", "l_e", "l_tg", "rect_e", "i_tg", "box_e",
                 "cp_e", "cp_tg", "lb_tg", "u_shift_e"):
        d = load(name)
        need = ("tensileforce", "sheary", "shearz", "torsion", "bendingmomv", "bendingmomw")
        check(name + ": element values", all(v in d["element_variables"] for v in need),
              d["element_variables"])
        _model, chain = chain_of(name, d)
        raw = raw_of(d)
        worst = 0.0
        for s in [chain.length * k / 8.0 for k in range(9)]:
            at = bm.forces_at(chain, raw, s)
            want = statics(d, s)
            scale = max(abs(v) for v in want.values())
            worst = max([worst] + [abs(at["forces"][k] - want[k]) / scale for k in want])
        check(name + ": forces at nine places", worst < 1e-9, "{0:.1e}".format(worst))


def test_efge_with_tg():
    print("-- POU_D_TG: EFGE_ELNO, one elastic step of STAT_NON_LINE for the rest")
    d = load("u_tg")
    forces = d["elements"]["sheary"]["values"]
    check("VY = 200 at both ends of every element, not AY^2 F", all(near(v, 200.0, 1e-9) for v in forces),
          forces[:2])
    root = int(d["beams"]["b"]["root"])
    check("reaction at the clamp in equilibrium (the correction of the bridge)",
          near(d["nodes"]["reactionforcey"][str(root)], -200.0, 1e-6),
          d["nodes"]["reactionforcey"][str(root)])
    d = load("u_tgfix")
    t = d["elements"]["torsion"]["values"]
    check("MT is the total torque, also with restrained warping", all(near(v, 10.0, 1e-9) for v in t), t[:2])


def twist_rate(d):
    """dDRX/dx in the part of the beam without warping effects: the mean over all."""
    b = d["beams"]["b"]
    rot = d["nodes"]["rotx"]
    return (rot[str(b["tip"])] - rot[str(b["root"])]) / d["info"]["length"]


def test_t_s():
    print("-- T_S = MT + EY VZ - EZ VY against G J dDRX/dx")
    for name in ("u_e", "u_t", "u_tg", "l_e", "l_tg", "u_shift_e"):
        d = load(name)
        s = d["info"]["section"]
        _, chain = chain_of(name, d)
        at = bm.forces_at(chain, raw_of(d), 0.0)["forces"]
        ts = at["T"] + s["EY"] * at["Vw"] - s["EZ"] * at["Vv"]
        check(name + ": twist rate", near(G_MOD * s["j"] * twist_rate(d), ts, 1e-7),
              (G_MOD * s["j"] * twist_rate(d), ts))
    d = load("u_e")
    s = d["info"]["section"]
    plain = G_MOD * s["j"] * twist_rate(d)
    check("without the term the torque is 1.9 times too large", near(10.0 / plain, 1.92, 0.01), 10.0 / plain)


def equilibrium(d, s):
    """Exact section forces of a cantilever under q (or rho A g) at s."""
    a = d["info"]["length"] - s
    q = list(d["info"]["q"] or (0.0, 0.0, 0.0))
    if d["info"]["gravity"]:
        area = d["info"]["section"]["A"]
        q = [7850.0 * area * g for g in d["info"]["gravity"]]
    return {"N": q[0] * a, "Vv": q[1] * a, "Vw": q[2] * a, "T": 0.0, "Mv": -q[2] * a * a / 2.0,
            "Mw": q[1] * a * a / 2.0}


# What the end values of a load mean, measured per element and load
EXPECTED_LOADS = {
    "q1_e": "end", "q4_e": "end", "q1_t": "end", "q4_t": "end", "q1_tg": "end", "q4_tg": "end",
    "q4_eu": "end", "q4_tgu": "end", "q1_3": "end", "q4_3": "end", "q4_3t": "end",
    "qp1_e": "end", "qp4_tg": "end", "g1_e": "end", "g4_e": "end", "g4_t": "end",
    "g4_tg": "midpoint"}


def test_distributed():
    print("-- FORCE_POUTRE and gravity through forces_at, inside the elements")
    for name, kind in sorted(EXPECTED_LOADS.items()):
        d = load(name)
        _model, chain = chain_of(name, d)
        raw = raw_of(d)
        votes = bm.load_convention(chain, raw)
        check(name + ": read as " + kind, list(votes.values()) == [kind], votes)
        worst = 0.0
        for k in range(33):
            s = chain.length * k / 32.0
            at = bm.forces_at(chain, raw, s)
            want = equilibrium(d, s)
            scale = max(abs(v) for v in equilibrium(d, 0.0).values())
            worst = max([worst] + [abs(at["forces"][key] - want[key]) / scale for key in want])
            if at["warnings"]:
                check(name + " notes", False, at["warnings"])
        check(name + ": against equilibrium at 33 places", worst < 1e-9, "{0:.1e}".format(worst))
    # one element with a load: the raw end values are exact, without any correction
    d = load("q1_e")
    length = d["info"]["length"]
    q = d["info"]["q"]
    check("POU_D_E, one element: end values exact", near(d["elements"]["bendingmomw"]["values"][0],
                                                         q[1] * length ** 2 / 2.0, 1e-9))
    d = load("q1_tg")
    check("POU_D_TG, one element: end values exact", near(d["elements"]["bendingmomw"]["values"][0],
                                                          q[1] * length ** 2 / 2.0, 1e-9))
    d = load("q1_3")
    mid = d["elements"]["bendingmomw"]["values"][1]
    check("line3: the mid value is exact too", near(mid, q[1] * (length / 2.0) ** 2 / 2.0, 1e-9), mid)
    d = load("g4_tg")
    h = d["info"]["length"] / 4.0
    v = d["elements"]["sheary"]["values"]
    check("gravity on POU_D_TG: V constant in an element, unlike FORCE_POUTRE", v[0] == v[1], v[:2])
    q_v = d["info"]["section"]["A"] * 7850.0 * d["info"]["gravity"][1]
    m0 = d["elements"]["bendingmomw"]["values"][0]
    check("and M carries q h^2/12", near(m0 - q_v * d["info"]["length"] ** 2 / 2.0, -q_v * h * h / 12.0, 1e-9),
          (m0, q_v))


def test_kinematics():
    print("-- no beam_forces: the kinematics on POU_D_E")
    d = load("q4_e")
    model, chain = chain_of("q4_e", d)
    raw = {}
    for e in chain.elements:
        first, last = str(e.nodes[0]), str(e.nodes[-1])
        raw[e.eid] = dict((v, [d["nodes"][v][first], d["nodes"][v][last]]) for v in bm.MOTION_VARS)
    forces = bm.forces_from_kinematics(chain, raw)
    worst = 0.0
    for k in range(9):
        s = chain.length * k / 8.0
        at = bm.forces_at(chain, forces, s)
        want = equilibrium(d, s)
        worst = max([worst] + [abs(at["forces"][key] - want[key]) / 200.0 for key in ("N", "Mv", "Mw")])
    # N is constant per element in this route
    check("M and N from displacement and rotation, within N steps", worst < 0.15, worst)
    at = bm.forces_at(chain, forces, 0.0)
    check("M at the root exact, as in Mecway's line2", near(at["forces"]["Mw"], equilibrium(d, 0.0)["Mw"], 1e-6),
          at["forces"]["Mw"])


def e2e(name):
    with open(result_file("e2e_{0}.json".format(name)), encoding="utf-8") as f:
        d = json.load(f)
    check(name + ": hidden run clean", not d["errors"] and d["run_status"] == "ok", d["errors"][:1])
    return d


def test_window():
    print("-- the window on a Code-Aster solution, real Mecway, hidden")
    for name in ("u_e", "u_tg", "u_tgfix", "u_shift_e", "l_tg", "cp_tg", "lb_tg", "q4_3", "tip3",
                 "q4_e", "q4_tg", "g4_tg"):
        run = e2e("aster_" + name)
        beam = run["beams"]["sel_b"]
        d = load(name)
        check(name + ": element route", beam["route"] == "element", beam["route"])
        check(name + ": solver named", beam["views"][0]["solver"] == u"Solver: Code-Aster",
              beam["views"][0]["solver"])
        check(name + ": no warning about Mecway's own beam",
              u"Mecway's beam" not in beam["views"][0]["warnings"])
        check(name + ": warping text fits this solver", run["warping_label"] ==
              u"Bimoment B not included.", run["warping_label"])
        for v in beam["views"]:
            last = v["last"]
            if d["info"]["q"] or d["info"]["gravity"]:
                want, root = equilibrium(d, last["s"]), equilibrium(d, 0.0)
            else:
                want, root = statics(d, last["s"]), statics(d, 0.0)
            scale = max(abs(x) for x in root.values())
            worst = max(abs(last["forces"][k] - want[k]) / scale for k in want)
            check("{0} shown at s={1:.3f}".format(name, last["s"]), worst < 1e-8, "{0:.1e}".format(worst))
    run = e2e("aster_u_shift_e")
    last = run["beams"]["sel_b"]["views"][1]["last"]
    d = load("u_shift_e")
    s = d["info"]["section"]
    check("#ASTER-SECTIE counts: the term", near(last["term"], s["EY"] * last["forces"]["Vw"] -
                                                 s["EZ"] * last["forces"]["Vv"], 1e-9), last["term"])
    check("and the window says that it differs", u"#ASTER-SECTIE counts" in run["beams"]["sel_b"]["views"][0]["warnings"])
    run = e2e("resaved_u_e")
    v = run["beams"]["sel_b"]["views"][0]
    check("saved again by Mecway: the trace is gone, read as Mecway's own (known limit)",
          v["solver"] == u"Solver: Mecway internal", v["solver"])
    run = e2e("aster_u_tg")
    check("the button works on a Code-Aster solution", u"sectionvonmises" in run["write_status"] and
          run["written_root"] > 1e6, (run["write_status"], run["written_root"]))
    check("warping zone named", u"951.9 mm (4/k)" in run["beams"]["sel_b"]["views"][0]["warnings"],
          run["beams"]["sel_b"]["views"][0]["warnings"])


def test_solids():
    print("-- V8: stress against the solids of V6 and V7")
    with open(result_file("v8_solids.json")) as f:
        rows = json.load(f)
    check("all solids, both elements", len(rows) == 48, len(rows))
    for r in rows:
        name = "{solid} {case} {element}".format(**r)
        # as in V6: under pure T beam theory has no sigma, under pure N no tau
        has_sigma, has_tau = r["case"] != "T", r["case"] != "N"
        check(name, (not has_sigma or r["e_sig"] <= MARGIN_SIGMA) and
              (not has_tau or r["e_tau"] <= MARGIN_TAU) and r["diagnostic"] == "OK",
              "sigma {0:.3f} % tau {1:.3f} %".format(100.0 * r["e_sig"], 100.0 * r["e_tau"]))
    sig = [r["e_sig"] for r in rows if r["case"] != "T"]
    tau = [r["e_tau"] for r in rows if r["case"] != "N"]
    print("largest: sigma {0:.3f} %, tau {1:.3f} %".format(100.0 * max(sig), 100.0 * max(tau)))


def test_detection():
    print("-- which solver: the trace of the bridge in a file")
    import tempfile
    folder = tempfile.mkdtemp(prefix="beam_aster_")
    head = (u'<liml version="33">\n  <analysis type="S30" />\n  <fieldvariable name="shear force" '
            u'displayunit="N" />\n  <solution>\n    <results precision="double">\n'
            u'      <nodeids name="displx">AQ==\n</nodeids>\n'
            u'      <nodevariable name="displx">\n        <valuesdouble>AA==\n</valuesdouble>\n')
    for name, tail, want in (("bridge.liml", u'        <nodes />\n        <values />\n', True),
                             ("mecway.liml", u"", False)):
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(head + tail + u"      </nodevariable>\n    </results>\n  </solution>\n</liml>\n")
        got = bu.scan_head(path)
        check(name, got["aster"] is want and got["fields"] == [u"shear force"], got)
    path = os.path.join(folder, "none.liml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(u'<liml version="33">\n  <analysis type="S30" />\n</liml>\n')
    check("no solution", bu.scan_head(path)["aster"] is False)
    path = os.path.join(folder, "ccx.liml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head.replace(u'type="S30"', u'type="S30" solver="ccx"') + u"  </results>\n")
    got = bu.scan_head(path)
    check("CCX keeps its name", got["solver"] == "ccx" and got["aster"] is False, got)


def main():
    tests = [test_forces_arrive, test_efge_with_tg, test_t_s, test_distributed, test_kinematics,
             test_window, test_solids, test_detection]
    for test in tests:
        run(test)
    print("")
    return summary(len(tests))


if __name__ == "__main__":
    sys.exit(main())
