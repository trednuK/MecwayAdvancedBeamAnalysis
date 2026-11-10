# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# End to end runs in real, hidden Mecway: e2e.py inside Mecway on a copy of an own
# model in a work folder, through the safe runner of run_mecway.py (private desktop,
# gate, timeout, kill own PID only). Waits while another Mecway runs and never
# touches it. The results land in tests/mecway/results/e2e_*.json, which
# tests/test_mecway_e2e.py checks against hand values without Mecway. Those results stay
# on this PC: the folder is not in git.
#
#   python tests/mecway/run_e2e.py <work folder> <case> [<case> ...]
#   python tests/mecway/run_e2e.py <work folder> prepare <V8 work folder> <bridge export>
#
# prepare makes <work folder>/diff_sources for the cases diff:<file>:<kind> and
# shells:<file>:<answer>: after the cases marker and marker_stale here and the case u_e
# of run_v8.py, plain, stale and added files (a beam element added after the solve), and
# with section_ipy.py the same with the shells of the bridge's "Section in results".

from __future__ import print_function

import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import run_mecway as rm

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(HERE, "e2e.py")
WAIT = 7200.0
# case: (model, extra job data, timeout)
CASES = {
    "api": ("e2e_internal", {}, 180.0),
    "config": ("e2e_config", {}, 120.0),
    "selections": ("e2e_internal", {}, 200.0),
    "gap": ("e2e_gap", {}, 200.0),
    "gap_open": ("e2e_crash", {}, 200.0),
    "gap2": ("e2e_gap2", {"case": "gap"}, 200.0),
    "gap3": ("e2e_gap3", {"case": "gap"}, 200.0),
    "setvar": ("e2e_internal", {}, 200.0),
    "setvar_open": ("e2e_crash", {}, 200.0),
    "buckling": ("e2e_buckling", {}, 300.0),
    "marker_modal": ("e2e_modal", {}, 300.0),
    "api_modal": ("e2e_modal", {}, 180.0),
    "api_ccx": ("e2e_ccx", {}, 240.0),
    "api_steps_ccx": ("e2e_steps_ccx", {}, 240.0),
    "internal": ("e2e_internal", {}, 600.0),
    "ccx": ("e2e_ccx", {}, 600.0),
    "modal": ("e2e_modal", {}, 600.0),
    "steps": ("e2e_steps_ccx", {}, 600.0),
    "marker": ("e2e_internal", {}, 600.0),
    "marker_solve": ("e2e_internal", {}, 300.0),
    "marker_solve_ccx": ("e2e_ccx", {}, 300.0),
    "marker_stale": ("e2e_internal", {}, 300.0),
    "stale_open": ("e2e_crash", {}, 300.0),
    "crash_save": ("e2e_internal", {}, 300.0),
    "crash_open": ("e2e_crash", {}, 300.0),
    "big": ("e2e_big", {}, 900.0),
    "numbers": ("e2e_numbers", {}, 600.0),
    "view3d": ("e2e_view3d", {"beams": [["sel_straight", "straight"], ["sel_kinked", "kinked"]]},
               900.0),
}
# cases where a window on the private desktop belongs to what is measured: s it may stay
# selections: two untitled windows stood there while the run went on and ended by
# itself (measured, Mecway 35; seen in the case internal too, cause NOT verified)
WINDOWS = {"config": 80.0, "selections": 60.0}
FORBIDDEN = re.compile(r"mw\.message|mw\.input|mw\.open\(|\.Show\(|\.ShowDialog\(|"
                       r"Application\.Run\(")


def gate_script():
    with open(SCRIPT, encoding="ascii") as f:
        text = f.read()
    compile(text, SCRIPT, "exec")
    if FORBIDDEN.search(text):
        return "the script holds a call that may show something"
    return None


def wait_free():
    """True when no Mecway runs; waits, because that may be the user or a test.
    E2E_BESIDE=1 starts beside it: only when the user allowed that for this run. The
    run stays on its own desktop and only its own process is ever touched."""
    if os.environ.get("E2E_BESIDE") == "1":
        return True
    end = time.time() + WAIT
    while rm.mecway_running():
        if time.time() > end:
            return False
        print("a Mecway is running, waiting", flush=True)
        time.sleep(30.0)
    return True


def run_case(work, name):
    model_name, extra, timeout = CASES.get(name, ("", {}, 0.0))
    job_case = name
    source = os.path.join(os.environ.get("E2E_MODELS", rm.MODELS), model_name + ".liml")
    crash = os.path.join(work, "crash_file.liml")
    if name.startswith("aster:"):
        model_name, timeout = "solved", 300.0
        source = os.path.join(work, "..", "aster", name[6:], "solved.liml")
        extra = {}
        job_case = "aster"
    elif name.startswith("view3d_aster:"):
        # the 3D view on an opened Code-Aster solution: view3d_aster:<name> as aster:<name>
        stem = name[13:]
        model_name, timeout = "solved", 900.0
        source = os.path.join(work, "..", "aster", stem, "solved.liml")
        extra = {"solve": False, "beams": [["sel_b", "b"]],
                 "info3d": os.path.join(rm.MODELS, "aster_" + stem + ".info.json")}
        job_case = "view3d"
    elif name.startswith("diff:"):
        # a solved file in the work folder with what differs from it: diff:<file>:<kind>
        _, stem, kind = name.split(":")
        model_name, timeout = "diff", 200.0
        source = os.path.join(work, "diff_sources", stem + ".liml")
        extra = {"case": "diff", "kind": kind}
        job_case = "diff"
    elif name.startswith("shells:"):
        # the button on a solved file in the work folder: shells:<file>:<answer>
        _, stem, answer = name.split(":")
        model_name, timeout = "shells", 200.0
        source = os.path.join(work, "diff_sources", stem + ".liml")
        extra = {"case": "shells", "answer": answer}
        job_case = "shells"
    elif name.startswith("resaved:"):
        model_name, timeout = "resaved", 300.0
        source = os.path.join(work, "aster_" + name[8:], "resaved.liml")
        extra = {}
        job_case = "aster"
    elif model_name == "e2e_big":
        source = os.path.join(work, "e2e_big.liml")
    elif model_name == "e2e_crash":
        source = crash
    folder = os.path.join(work, name.replace(":", "_"))
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    model = os.path.join(folder, model_name + ".liml")
    shutil.copy(source, model)
    info = os.path.join(rm.MODELS, model_name + ".info.json")
    out = os.path.join(folder, "result.json")
    job = {"case": job_case, "repo": REPO, "folder": folder, "out": out, "model": model,
           "crash_file": crash, "info": info if os.path.isfile(info) else None}
    job.update(extra)
    path = os.path.join(folder, "job.json")
    with open(path, "w") as f:
        json.dump(job, f)
    try:
        model.encode("ascii")
        ET.parse(model)
    except Exception as exc:
        return "gate: {0}".format(exc)
    reason = gate_script()
    if reason:
        return reason
    if not wait_free():
        return "a Mecway keeps running"
    start = time.time()
    status = rm.run_hidden(model, SCRIPT, dict(os.environ, E2E_JOB=path), folder, timeout,
                           WINDOWS.get(name, rm.WINDOW_LIMIT))
    print(name, status, "%.1f s" % (time.time() - start), "result:", os.path.exists(out),
          flush=True)
    if not os.path.exists(out):
        # what stopped it is the measurement, a dialog text for instance
        with open(os.path.join(rm.RESULTS, "e2e_{0}.json".format(name.replace(":", "_"))), "w",
                  encoding="utf-8") as f:
            json.dump({"case": name, "run_status": status, "errors": []}, f, sort_keys=True)
        return status if status != "ok" else "no result"
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    data["run_status"] = status
    with open(os.path.join(rm.RESULTS, "e2e_{0}.json".format(name.replace(":", "_"))), "w", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True, ensure_ascii=False)
    for line in data.get("errors") or []:
        print("  error:", line.encode("ascii", "replace").decode(), flush=True)
    if status != "ok":
        return status
    return "script error" if data.get("errors") else None


def added(source, target):
    """A solved file with one beam element and node added after the solve, as a user
    would: the model has a node more than the solution, for another reason than the
    marker or the shells of the bridge."""
    with open(source, encoding="utf-8") as f:
        text = f.read()
    head, sep, rest = text.partition("<solution")
    last = max(int(n) for n in re.findall(r'<node nid="(\d+)" x=', head))
    eid = max(int(n) for n in re.findall(r'<elem eid="(\d+)"', head))
    node = re.search(r'  <node nid="%d" x="([^"]*)" y="([^"]*)" z="([^"]*)" />\n' % last, head)
    head = head[:node.end()] + '  <node nid="%d" x="%r" y="%s" z="%s" />\n' % (
        last + 1, float(node.group(1)) + 0.25, node.group(2), node.group(3)) + head[node.end():]
    elem = re.search(r'    <elem eid="\d+" shape="line2" nodes="(\d+ )?%d( \d+)?" />\n' % last,
                     head)
    head = head[:elem.end()] + '    <elem eid="%d" shape="line2" nodes="%d %d" />\n' % (
        eid + 1, last, last + 1) + head[elem.end():]
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(head + sep + rest)


def prepare(work, v8_work, bridge):
    folder = os.path.join(work, "diff_sources")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    sources = {"stale": os.path.join(work, "crash_file.liml"),
               "internal_plain": os.path.join(work, "marker", "a_no_marker.liml"),
               "internal_shells": os.path.join(work, "marker", "a_no_marker.liml"),
               "aster_plain": os.path.join(v8_work, "u_e", "solved.liml"),
               "aster_shells": os.path.join(v8_work, "u_e", "solved.liml")}
    for stem, source in sources.items():
        shutil.copy(source, os.path.join(folder, stem + ".liml"))
    added(os.path.join(folder, "internal_plain.liml"), os.path.join(folder, "added.liml"))
    view = os.path.join(work, "view")
    if not os.path.isdir(view):
        os.makedirs(view)
    for stem in ("internal_shells", "aster_shells"):
        code = subprocess.call(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File",
             os.path.join(REPO, "tools", "run_ipy.ps1"), "-Script",
             os.path.join(HERE, "section_ipy.py"), "-ScriptArgs", ",".join(
                 [os.path.join(bridge, "Scripts"), os.path.join(folder, stem + ".liml"), view])],
            creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if code:
            print("STOP: shells of", stem, flush=True)
            return 1
    return 0


def main(args):
    work = os.path.abspath(args[0])
    if len(args) > 1 and args[1] == "prepare":
        return prepare(work, os.path.abspath(args[2]), os.path.abspath(args[3]))
    for name in args[1:]:
        reason = run_case(work, name)
        if reason:
            print("STOP:", name, reason, flush=True)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
