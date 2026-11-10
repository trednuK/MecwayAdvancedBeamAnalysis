# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# End to end in real Mecway 35, hidden: the results of tests/mecway/run_e2e.py against
# hand values. Every number below is what the real window showed, with the real mw and
# solution, the real worker over its pipe, on the threads of run_window. V10 included.
# The results stay on this PC, not in git; with them this test needs no Mecway. Run with a Python with
# numpy and scipy:
#
#   python tests/test_mecway_e2e.py
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

import section_stress as ss  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
FAILED = []
RESULTS = os.path.join(_HERE, "mecway", "results")
MODELS = os.path.join(_HERE, "mecway", "models")
F = (100.0, 200.0, -150.0)
M = (10.0, -20.0, 30.0)
Q = (120.0, -1000.0, 700.0)
L = 1.0


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


SKIPPED = []
RUNNERS = "tests/mecway/run_e2e.py"


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
    with open(result_file("e2e_{0}.json".format(name)), encoding="utf-8") as f:
        return json.load(f)


def clean(name):
    d = load(name)
    check(name + ": hidden run clean", not d["errors"] and d.get("run_status") == "ok",
          (d["errors"][:1], d.get("run_status")))
    return d


def tip_forces(s):
    """Section forces of a cantilever along X with F and M at the tip (research note E2)."""
    arm = L - s
    return {"N": F[0], "Vv": F[1], "Vw": F[2], "T": M[0], "Mv": M[1] - F[2] * arm,
            "Mw": M[2] + F[1] * arm}


def line_forces(s):
    """The same under the line load Q."""
    a = L - s
    return {"N": Q[0] * a, "Vv": Q[1] * a, "Vw": Q[2] * a, "T": 0.0, "Mv": -Q[2] * a * a / 2.0,
            "Mw": Q[1] * a * a / 2.0}


def same_forces(name, shown, want, tol, scale):
    worst = max(abs(shown.get(k, 0.0) - v) for k, v in want.items()) / scale
    check(name, worst <= tol, "{0:.1e}".format(worst))


def views(name, beam, expected, tol=1e-9):
    scale = max(abs(v) for v in expected(0.0).values())
    for v in beam["views"]:
        last = v["last"]
        same_forces("{0} s={1:.3f}".format(name, last["s"]), last["forces"], expected(last["s"]),
                    tol, scale)


def test_internal():
    print("-- internal solver, line2")
    d = clean("internal")
    lists = d["lists"]
    keys = [s["key"] for s in lists["sources"]]
    check("the file path is the model", lists["file"])
    check("element and face selections listed", "sel_ip" in keys and "face_ip" in keys, keys)
    check("node selection dropped by the file", "clamp" not in keys and
          "clamp" in [s["key"] for s in lists["api_sources"]], keys)
    check("face selection marked", [s for s in lists["sources"] if s["key"] == "face_ip"][0]["faces"])
    check("nothing selected in a hidden run", lists["current_selection"] == [])
    check("one result set", lists["result_box"] == [u"Active solution"], lists["result_box"])
    check("the window is on Mecway's thread model", d["script_thread_sta"] is True)
    beams = d["beams"]
    for name in ("sel_gen_r", "sel_gen_u", "sel_ip", "face_ip", "sel_ip_rev"):
        b = beams[name]
        check(name + ": opened", b["select_ok"] and b["route"] == "element" and
              b["chain"]["length"] == 1.0, b["route"])
        views(name, b, tip_forces)
    views("sel_ip_q", beams["sel_ip_q"], line_forces)
    check("element and face selection give the same raw values",
          beams["face_ip"]["chunk"]["raw"] == beams["sel_ip"]["chunk"]["raw"])
    rev = beams["sel_ip_rev"]
    check("a shuffled selection is ordered along the chain",
          [e[0] for e in rev["chain"]["elements"]] == [17, 18, 19, 20], rev["chain"]["elements"])
    check("reversed elements: named as mirrored, not as another section",
          u"drawn mirrored" in rev["views"][0]["warnings"] and
          u"Other section" not in rev["views"][0]["warnings"], rev["views"][0]["warnings"])
    # the lines under the slider
    last = beams["sel_gen_r"]["views"][2]
    check("forces in the window text", u"Mv = 55.00 N" in last["forces_label"] and
          u"Mw = 130.0 N" in last["forces_label"], last["forces_label"])
    check("place in the window text", u"Position 500.0 mm of 1000 mm" in last["place_label"],
          last["place_label"])
    check("solver shown", last["solver"] == u"Solver: Mecway internal", last["solver"])
    # the stress of the rectangle by hand, at the root: sigma at a corner
    a, ivv, iww = 0.1 * 0.05, 0.1 * 0.05 ** 3 / 12.0, 0.05 * 0.1 ** 3 / 12.0
    f = tip_forces(0.0)
    peak = max(abs(f["N"] / a + f["Mv"] * w / ivv - f["Mw"] * v / iww)
               for v in (-0.05, 0.05) for w in (-0.025, 0.025))
    sigma = beams["sel_gen_r"]["views"][0]["last"]
    check("von Mises of the rectangle at the root is about sigma", 1.0 <=
          sigma["outside"]["value"] / peak <= 1.05, sigma["outside"]["value"] / peak)
    # T_S of the channel against the section itself
    with open(os.path.join(MODELS, "e2e_internal.info.json")) as fp:
        info = json.load(fp)
    field = ss.SectionField([info["sections"]["GenU"]["loop"]], nu=0.3)
    ey, ez = field.offset
    want = ey * F[2] - ez * F[1]
    row = beams["sel_gen_u"]["views"][1]["last"]
    check("T_S term of the channel", near(row["term"], want, 1e-9), (row["term"], want))
    check("T_S in the window", u"T_S" in beams["sel_gen_u"]["views"][1]["torque_label"])
    check("eccentric warning", u"Eccentric shear centre" in beams["sel_gen_u"]["views"][1]["warnings"])
    # thin wall by hand: e = 3 b^2 / (h + 6 b) from the mid line of the web, on mid lines
    b, h = 0.0475, 0.095
    e_hand = 3.0 * b * b / (h + 6.0 * b)
    g = (2.0 * b * b / 2.0) / (2.0 * b + h)
    check("shear centre of the channel against thin wall theory",
          near(math.hypot(ey, ez), e_hand + g, 0.05), (math.hypot(ey, ez), e_hand + g))
    n = d["no_shear_centre"]
    check("the switch leaves the term out", u"left out" in n["torque_label"], n["torque_label"])
    pngs = d["png"]
    check("rendered to PNG, off screen", all(p["size"] > 20000 and p["colours"] > 100 for p in pngs), pngs)
    check("sheet: six sections", d["sheet"]["last"]["n"] == 6)
    m = d["measures_gen_u"]
    check("all eight measures arrive", len(m) == 8 and all(v["measure"] == k for k, v in m.items()),
          sorted(m))
    vm, sg, tau = m["vonmises"]["outside"], m["sigma"]["outside"], m["tau"]["outside"]
    check("von Mises above sigma and tau", vm["value"] >= abs(sg["value"]) - 1.0 and
          vm["value"] >= math.sqrt(3.0) * tau["value"] * 0.5, (vm["value"], sg["value"], tau["value"]))
    check("hover answers", u"MPa" in d["hover"], d["hover"].encode("ascii", "replace"))


def test_ccx():
    print("-- CCX, line3, node values")
    d = clean("ccx")
    beams = d["beams"]
    for name in ("sel_ip", "face_ip"):
        b = beams[name]
        check(name + ": node route", b["route"] == "node", b["route"])
        views(name, b, tip_forces, 1e-4)
    views("sel_rect_q", beams["sel_rect_q"], line_forces, 2e-4)
    v = beams["sel_ip"]["views"][0]
    check("the window says node values", u"No element values" in v["warnings"], v["warnings"])
    check("solver shown", v["solver"] == u"Solver: CalculiX", v["solver"])


def test_modes():
    print("-- modes")
    d = clean("modal")
    check("three modes offered", len(d["lists"]["result_box"]) == 3, d["lists"]["result_box"])
    ivv = (2 * 0.05 * 0.01 ** 3 / 12.0 + 2 * 0.01 * 0.05 ** 3 / 12.0 +
           0.08 * 0.008 ** 3 / 12.0)
    a = 2 * 0.05 * 0.01 + 0.08 * 0.008
    f1 = 1.8751 ** 2 / (2.0 * math.pi) * math.sqrt(210e9 * ivv / (7850.0 * a))
    got = float(d["frequencies"][0][0])
    check("first frequency against the cantilever formula", near(got, f1, 0.02), (got, f1))
    root, tip = d["sets"][0]["root"], d["sets"][0]["tip"]
    forces = root["last"]["forces"]
    check("a mode has no section forces: kinematics", u"holds no section forces" in root["warnings"],
          root["warnings"])
    check("the text does not ask for field variables that are there",
          u"Add the field variables" not in root["warnings"])
    check("mode: scaled, says so", u"scaled arbitrarily" in root["warnings"])
    check("mode 1 bends about V, tip free", abs(forces["Mv"]) > 1e3 * abs(forces["Mw"]) and
          abs(tip["last"]["forces"]["Mv"]) < 0.01 * abs(forces["Mv"]), forces)
    check("only sigma in a kinematic result", root["last"]["measure"] == "sigma")
    third = d["sets"][2]["root"]["last"]["forces"]
    check("the mode keyword reaches Mecway: mode 3 bends about W",
          abs(third["Mw"]) > 1e3 * abs(third["Mv"]), third)
    d = clean("buckling")
    check("buckling modes offered as such", d["lists"]["result_box"][0].startswith(u"Buckling mode 1"),
          d["lists"]["result_box"])


def test_steps():
    print("-- time steps")
    d = clean("steps")
    check("four steps offered, with their times", d["lists"]["result_box"][-1].startswith(u"Step 4 (t = 1.000"),
          d["lists"]["result_box"])
    for k, row in enumerate(d["sets"]):
        t = 0.25 * (k + 1)
        got = row["root"]["last"]["forces"]
        check("step {0}: force grows with time".format(k + 1), near(got["Vv"], 100.0 * t, 1e-3) and
              near(got["Mw"], 100.0 * t, 1e-3), got)


def test_configurations():
    print("-- configurations")
    d = clean("config")
    box = d["lists"]["result_box"]
    # Mecway's active configuration cannot be read: every entry names one, the tool sets it
    check("configurations offered, no Active solution", box == [u"Configuration A", u"Configuration B"],
          box)
    rows = dict((r["text"], r) for r in d["sets"])
    check("configuration A: the solution follows",
          near(rows[u"Configuration A"]["root"]["last"]["forces"]["Mw"], 100.0, 1e-9))
    check("configuration B: the solution follows",
          near(rows[u"Configuration B"]["root"]["last"]["forces"]["Mw"], 300.0, 1e-9))
    check("the switch is said", all(u"Configuration {0} made active".format(n) in rows[u"Configuration " + n]
                                    ["root"]["warnings"] for n in u"AB"))
    # the second beam: q_y and the tip force in both, card V = Z suppressed in B, q_z
    # suppressed in A. Hand values, cantilever L = 1 m, F = 100 N, q_y = -1000 N/m,
    # q_z = -500 N/m: in A (V = Z, W = -Y) Mv = F (L-s) - 500 (L-s)^2, Mw = 0; in B (V = Y)
    # Mw the same, Mv = 250 (L-s)^2. At s = 0.125 inside an element: so with the
    # correction for the distributed load, in the right axes.
    oc = dict((r["text"], r) for r in d.get("sets_oc") or [])
    hand = {u"Configuration A": {"Mv": (-400.0, -295.3125), "Mw": (0.0, 0.0)},
            u"Configuration B": {"Mv": (250.0, 191.40625), "Mw": (-400.0, -295.3125)}}
    for text, want in sorted(hand.items()):
        row = oc.get(text)
        if row is None:
            check(text + ": second beam read", False, sorted(oc))
            continue
        for key, (root, inside) in sorted(want.items()):
            got = (row["root"]["last"]["forces"][key], row["inside"]["last"]["forces"][key])
            check(u"{0}: {1} at the root and inside, hand values".format(text, key),
                  abs(got[0] - root) <= 1e-9 * 400.0 and abs(got[1] - inside) <= 1e-9 * 400.0, got)
    check("A: the card counts (Mecway, measured), so no V shear", bool(oc) and
          oc[u"Configuration A"]["root"]["last"]["forces"]["Vv"] == 0.0)
    # set_active_configuration on a name the model lacks: raises, the active one stays
    bogus = d.get("bogus") or {}
    check("an unknown configuration name raises", u"does not exist" in (bogus.get("raised") or u""), bogus)
    check("an unknown name leaves the active configuration", near(bogus.get("Mw_after", 0.0), 300.0, 1e-9),
          bogus)


def test_selections():
    print("-- selections made after the last save")
    d = clean("selections")
    s = d["selections"]
    per = s["per_selection"]
    ints = u"System.Collections.Generic.List`1[[System.Int32"
    for name in (s["new_node"], s["new_element"], "tip_ip", "sel_ip"):
        check("measured: {0} gives a list of ints".format(name),
              per[name]["value"]["net"].startswith(ints) and per[name]["item"]["py"] == "int",
              per[name]["value"])
    check("measured: a face selection gives FaceIds",
          per[s["new_face"]]["item"]["net"] == u"Mecway.Api.FaceId", per[s["new_face"]]["item"])
    found = set(s["mw_selection_names"])
    check("measured: the selection calls, none reads the kind of a selection", set([
        "add_to_named_selection", "delete_named_selection", "named_selection", "named_selections",
        "new_element_selection", "new_face_selection", "new_node_selection",
        "remove_from_named_selection", "rename_named_selection"]) <= found and
        not [n for n in found if "kind" in n or "type" in n], sorted(found))
    check("measured: a node that does not exist is refused",
          not s["node_beyond"]["ok"] and u"does not exist" in s["node_beyond"]["error"],
          s["node_beyond"])
    win = d["window"]
    sources = dict((x["key"], x) for x in win["sources"])
    check("the file's direct node selection stays left out", "tip_ip" not in sources, list(sources))
    for kind in ("node", "element"):
        row = win[kind]
        check("unsaved {0} selection: uncertain, not opened".format(kind),
              sources[row["name"]]["uncertain"] and not row["opened"] and row["ids"] is None, row)
        check("unsaved {0} selection: says so with the name".format(kind),
              row["name"] in row["warnings"] and u"Save the model" in row["warnings"], row["warnings"])
    check("unsaved face selection: certain by the API, opened",
          not sources[win["face"]["name"]]["uncertain"] and win["face"]["opened"] and
          win["face"]["ids"] == [9, 10], win["face"])
    check("the file's element selection: opened", not sources["sel_ip"]["uncertain"] and
          win["file"]["opened"] and win["file"]["ids"] == [9, 10, 11, 12], win["file"])


def test_marker():
    print("-- V10, the marker")
    d = clean("marker")
    m = d["marker"]
    before = m["state_before"]
    show, move, gone = m["after_show"], m["after_move"], m["after_remove"]
    check("through the window: one element, four nodes more", show["state"]["elements"] ==
          before["elements"] + 1 and show["state"]["nodes"] == before["nodes"] + 4, show["state"])
    check("the solution keeps its size", show["state"]["solution_nodes"] == before["nodes"])
    check("a plate across the beam, at the slider", all(abs(p[0] - 0.5) < 1e-12 for p in show["marker_nodes"]) and
          len(show["marker_nodes"]) == 4, show["marker_nodes"])
    check("moved by the slider", all(abs(p[0] - 0.8) < 1e-12 for p in move["marker_nodes"]))
    check("the window went to the model view", m["switched"] is True)
    check("the window says it", u"temporary element" in m["marker_on_text"])
    for name in ("after_show", "after_move", "after_remove", "after_direct"):
        c = m[name]["compare"]
        check("solution values unchanged " + name, c["differ"] == 0 and c["missing"] == 0 and
              c["values"] > 300, c)
    check("gone again", gone["state"] == before and m["counts"] == [0, 0], gone["state"])
    check("saved with the marker: in the file", m["file_m"]["marker"] and m["file_m"]["solution"])
    check("byte equal after the marker", m["file_a"]["sha256"] == m["file_b"]["sha256"] and
          m["file_a"]["sha256"] == m["file_c"]["sha256"], (m["file_a"], m["file_b"]))
    check("the saved file with marker reads", m["reread_ok"] and m["view_reread"]["opened"])
    check("direct calls", m["direct_move"] and m["direct_remove"] and m["direct_remove_nothing"] and
          m["direct_move_nothing"] is False)
    check("a second show replaces the first", m["direct_show_again"]["component"] ==
          m["direct_show"]["component"] and len(m["direct_nodes"]) == 4)

    names = load("api")["api"]["mw_names"]
    check("the script object lists no undo: Undo stays for a test in the visible program",
          not [n for n in names if "undo" in n.lower() or "redo" in n.lower()])

    d = clean("marker_solve")
    s = d["marker_solve"]
    check("static solve with the marker: section forces the same", s["worst_relative"] < 1e-10,
          s["worst_relative"])
    check("the plate is in that solution", s["state_after_solve"]["solution_elements"] ==
          s["state_with"]["elements"])
    check("removed, solved again: exact", s["compare_after"]["differ"] == 0 and s["removed"])
    d = clean("marker_solve_ccx")
    s = d["marker_solve"]
    check("CCX with the marker: the same", s["worst_relative"] < 1e-10, s["worst_relative"])
    d = clean("marker_modal")
    s = d["marker_solve"]
    check("frequency analysis with the marker: empty solution, no error, no dialog",
          s["modes_with"] == [] and s["state_after_solve"]["solution_elements"] == 0 and
          len(s["modes_before"]) == 3, s["modes_with"])
    check("and back after removing", s["state_end"]["solution_elements"] == 16)

    d = clean("crash_save")
    check("crash file holds the marker", d["file"]["marker"] and d["state"]["elements"] == 21)
    a = clean("crash_open")
    c = a["crash"]
    check("next start finds and cleans it", c["state_open"]["elements"] == 21 and
          c["state_clean"]["elements"] == 20 and c["counts"] == [0, 0], c["state_clean"])
    check("and says so", [w["code"] for w in c["warn_start"]] == ["marker_left"])
    check("the beam opens after it", c["select_ok"] and c["view"]["opened"])
    check("saved after cleaning: byte equal to never having had it",
          c["file"]["sha256"] == m["file_a"]["sha256"], c["file"])
    d = clean("stale_open")
    c = d["crash"]
    check("a solution that still holds the plate opens", c["view"]["opened"] and
          c["state_open"]["solution_elements"] == 21 and not c["warn_start"])
    d = clean("big")
    t = d["big"]
    check("large model", t["elements"] > 99000, t["elements"])
    check("100 000 elements: moving is the display, below 0.5 s here", t["move_marker"] < 0.5 and
          t["move_without_display"] < 0.05 and t["one_node_and_display"] < 0.5,
          (t["move_marker"], t["move_without_display"], t["one_node_and_display"]))
    check("100 000 elements: show and remove below 1 s", t["show_marker"] < 1.0 and
          t["remove_marker"] < 1.0, (t["show_marker"], t["remove_marker"]))
    check("nothing left", t["state"]["elements"] == t["elements"] and
          t["state"]["components"] == ["E_Plate", "E_Rect"], t["state"])
    print("big, s (display without a change does nothing): {0}".format(dict((k, round(v, 3)) for k, v in t.items() if isinstance(v, float))))


def test_results():
    print("-- own variables in the results of Mecway")
    s = clean("setvar")["setvar"]
    check("before: the name does not exist", all(v.startswith("raises") for v in s["before"]), s["before"])
    check("a new name is taken, as a plain list", s["python_list"]["ok"], s["python_list"])
    check("read back with node_value", s["after"] == ["1000.0", "1001.0", "1002.0"] and
          s["last_node"] == "1024.0", s["after"])
    check("the other variables stay", s["compare_new"]["differ"] == 0 and s["compare_new"]["values"] > 300,
          s["compare_new"])
    check("one value per node, nothing else", not s["short_list"]["ok"] and not s["element_values"]["ok"],
          s["element_values"])
    check("no value per element end", s["element_node"] == "None", s["element_node"])
    check("an existing variable is overwritten, only that one",
          s["compare_overwrite"]["differ"] == s["n_nodes"], s["compare_overwrite"])
    check("in the saved file", s["in_file"] == ["sectionvonmises"], s["in_file"])
    o = clean("setvar_open")["setvar"]
    check("still there after opening again", o["read"] == ["1000.0", "1001.0", "1002.0"] and
          o["elements"] == s["n_elements"], o)
    w = clean("internal")["written"]
    check("the button: says what it wrote", u"sectionvonmises" in w["status"], w["status"])
    check("the button: other fields stay", w["compare"]["differ"] == 0)
    check("the button: 0 outside the chain", w["other_node"] == 0.0, w["other_node"])
    check("the button: the three in the file", len(w["in_file"]) == 3, w["in_file"])
    root = w["chain"].index(11)
    check("the button: at the root what the window shows", near(
        w["values"]["sectionvonmises"][root], w["root"]["value"], 1e-6) and near(
        w["values"]["sectionsigma"][root], abs(w["root_sigma"]["value"]), 1e-6) and near(
        w["values"]["sectiontau"][root], w["root_tau"]["value"], 1e-6), w["values"])
    sig = w["values"]["sectionsigma"]
    check("the button: sigma falls towards the tip", sig == sorted(sig, reverse=True), sig)
    print("-- per time step and per mode")
    for name, key, tol in (("steps", "sectionvonmises", 1e-6), ("modal", "sectionsigma", 1e-6)):
        d = clean(name)
        check("the button works on " + name, u"sectionsigma" in d["write_status"], d["write_status"])
        p = d["per_set"]
        rows = p["after_first"][key]
        check(name + ": the written set holds it, the others have nothing",
              rows[0] is not None and rows[0] > 0.0 and all(v is None or v == 0.0 for v in rows[1:]), rows)
        window = d["sets"][0]["root"]["last"]["outside"]["value"]
        check(name + ": the value of the window for that set", near(rows[0], abs(window), tol), (rows[0], window))
        rows2 = p["after_second"][key]
        check(name + ": a second write leaves the first", rows2[0] == rows[0] and rows2[1] > 0.0 and
              rows2[1] != rows[0], rows2)
        window2 = d["sets"][1]["root"]["last"]["outside"]["value"]
        check(name + ": the second is that of its own set", near(rows2[1], abs(window2), tol), (rows2[1], window2))
        check(name + ": displacements of all sets stay", p["motion_before"] == p["motion_after"])
    c = clean("internal")["contours"]
    print("contour lines in Mecway, round trip: {0:.1f} ms off, {1:.1f} ms on".format(c["off_ms"], c["on_ms"]))
    check("contour lines in Mecway: the frame time stays about equal", c["on_ms"] < c["off_ms"] + 8.0, c)
    check("contour lines in Mecway: drawn", c["png"]["size"] > 20000)
    print("-- node numbers out of order")
    g = clean("gap")["gap"]
    check("the model keeps its numbers", sorted(g["mw_nodes"]) == [1, 2, 3, 4, 5],
          g["mw_nodes"])
    check("all_nodes is 1..n", g["all_nodes"] == [1, 2, 3, 4, 5], g["all_nodes"])
    for name in ("gap2", "gap3"):
        status = load(name)["run_status"]
        check(name + ": Mecway 35 refuses a file with gaps, with a dialog", u"sequential" in status, status)
    check("a value lands on its own node", g["named"] == [10.0 * n for n in g["along"]], g["named"])
    sig = g["written"]["sectionsigma"]
    check("the button: root largest, along the beam", sig == sorted(sig, reverse=True) and sig[0] > 0.0, sig)
    check("the button: the root is what the window shows", near(
        g["written"]["sectionvonmises"][0], g["root"]["last"]["outside"]["value"], 1e-6))
    o = clean("gap_open")["gap_open"]
    check("after opening again: still on its node", o["named"] == [10.0 * n for n in o["along"]], o["named"])


def test_differences():
    print("-- which difference between model and solution Mecway refuses")
    expected = (("diff_internal_shells_none", True), ("diff_aster_shells_none", False),
                ("diff_internal_plain_none", True), ("diff_aster_plain_none", True),
                ("diff_stale_none", True), ("diff_internal_plain_node_added", True),
                ("diff_internal_plain_marker", True),
                ("diff_internal_plain_last_element_deleted", True),
                ("diff_internal_plain_node_moved", True), ("diff_internal_shells_solved", True),
                ("diff_stale_solved", True))
    for name, ok in expected:
        d = clean(name)["diff"]
        check(name + ": {0}".format("taken" if ok else "refused"), d["write"]["ok"] is ok, d["write"])
    w = clean("diff_aster_shells_none")["diff"]
    check("the real refusal: a null reference as SystemError", w["write"]["python"] == "SystemError" and
          w["write"]["net"] == "NullReferenceException", w["write"])
    check("shells: more nodes in the solution than in the model", w["before"]["solution"] > w["before"]["model"],
          w["before"])
    print("-- the button on a solution with shells of the bridge")
    for name in ("shells_aster_shells_yes", "shells_internal_shells_yes"):
        s = clean(name)["shells"]
        check(name + ": seen as shells", s["head"]["shells"] == ["Doorsnede"], s["head"])
        check(name + ": no question, no solve", s["asked"] == [] and s["after"] == s["before"], s["asked"])
        check(name + ": says why", u"bridge" in s["status"], s["status"])
        check(name + ": nothing written", s["written"].startswith("raises") or s["written"] == "None", s["written"])
        check(name + ": says from where", u"saved file" in s["status"], s["status"])
        check(name + ": the panel stays usable", all(s["enabled"]), s["enabled"])
    s = clean("shells_aster_shells_yes")["shells"]
    check("the real error through set_all: a refusal with its text", "refused" in s["set_all"] and
          len(s["set_all"]["refused"]) > 5, s["set_all"])
    s = clean("shells_aster_plain_yes")["shells"]
    check("a plain Code-Aster solution: written", u"sectionsigma" in s["status"] and not s["asked"], s["status"])
    print("-- the button on a solution that does not fit, not of the bridge")
    no = clean("shells_stale_no")["shells"]
    check("the question names sizes and solver and that it replaces", len(no["asked"]) == 1 and
          u"29" in no["asked"][0] and u"25" in no["asked"][0] and u"Mecway internal" in no["asked"][0] and
          u"replaced" in no["asked"][0], no["asked"])
    check("no: nothing happens", no["after"] == no["before"] and u"Nothing written" in no["status"], no["status"])
    yes = clean("shells_stale_yes")["shells"]
    check("yes: solved anew", yes["after"]["solution"] == 25 and yes["before"]["solution"] == 29, yes["after"])
    check("yes: written", u"sectionsigma" in yes["status"], yes["status"])
    check("yes: at the root what the window shows", yes["root"] > 1e3 and
          near(yes["root"], yes["window"], 1e-6), (yes["root"], yes["window"]))
    check("yes: the panel is free again", all(yes["enabled"]), yes["enabled"])


def main():
    tests = [test_internal, test_ccx, test_modes, test_steps, test_configurations, test_selections,
             test_marker,
             test_results, test_differences]
    for test in tests:
        run(test)
    print("")
    return summary(len(tests))


if __name__ == "__main__":
    sys.exit(main())
