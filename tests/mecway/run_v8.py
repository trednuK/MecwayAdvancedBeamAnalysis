# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Check V8: own beams through MecwayAsterBridge and Code-Aster, headless, as the
# bridge's own verification does it (TestFiles/Verificatie/_run_one.py), without
# Mecway and without a window. The bridge is an export of a commit in a scratch folder,
# read only; the results go to tests/mecway/results/aster_<case>.json, which
# tests/test_beam_aster.py checks without Code-Aster. Solved models for the end to end
# run in Mecway land in <work>/<case>/solved.liml.
#
#   python tests/mecway/run_v8.py <work folder> <bridge export> <case> [<case> ...]
#
# Run with a Python with numpy and scipy (Code-Aster's); the bridge runs under it too.

from __future__ import print_function

import base64
import io
import json
import os
import re
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)

import gen_aster as ga  # noqa: E402

NO_WINDOW = 0x08000000
# (name, element of the bridge, analysis)
TIP = (100.0, 200.0, -150.0)
TORQUE = (10.0, -20.0, 30.0)
Q = (120.0, -1000.0, 700.0)
G = (3.0, -10.0, 4.0)
E = {"elem_beam": "POU_D_E"}
TG = {"elem_beam": "POU_D_TG", "beam_warping": "free"}
TG_FIX = {"elem_beam": "POU_D_TG"}
T = {"elem_beam": "POU_D_T"}


def cases():
    """{name: (function that writes the model, general settings)}."""
    def tip(key, **kw):
        return lambda n: ga.cantilever(n, key, tip=TIP, moment=TORQUE, **kw)

    def load(key, **kw):
        return lambda n: ga.cantilever(n, key, q=Q, **kw)

    def grav(key, **kw):
        return lambda n: ga.cantilever(n, key, gravity=G, **kw)

    def profile(material, **kw):
        return lambda n: ga.cantilever(n, None, tip=TIP, moment=TORQUE, general=False,
                                       material=material, **kw)

    def profile_q(material, **kw):
        return lambda n: ga.cantilever(n, None, q=Q, general=False, material=material, **kw)

    return {
        # T_S and the section forces, line2
        "u_e": (tip("u"), E), "u_t": (tip("u"), T), "u_tg": (tip("u"), TG),
        "u_tgfix": (tip("u"), TG_FIX), "l_e": (tip("l"), E), "l_tg": (tip("l"), TG),
        "u_shift_e": (tip("u", shift=(0.01, -0.004)), E),
        "rect_e": (tip("rect"), E), "i_tg": (tip("i"), TG), "box_e": (tip("box"), E),
        # Mecway profiles, the bridge computes the rest
        "cp_e": (profile("Cp"), E), "cp_tg": (profile("Cp"), TG),
        "lb_tg": (profile("Lb"), TG),
        # distributed load: one element, four, Timoshenko, warping, line3
        "q1_e": (load("rect", ne=1), E), "q4_e": (load("rect", ne=4), E),
        "q1_t": (load("rect", ne=1), T), "q4_t": (load("rect", ne=4), T),
        "q1_tg": (load("rect", ne=1), TG), "q4_tg": (load("rect", ne=4), TG),
        "q4_tgu": (load("u", ne=4), TG), "q4_eu": (load("u", ne=4), E),
        "q1_3": (load("rect", ne=1, line3=True), E), "q4_3": (load("rect", ne=4, line3=True), E),
        "qp1_e": (profile_q("Ip", ne=1), E), "qp4_tg": (profile_q("Ip", ne=4), TG),
        "g1_e": (grav("rect", ne=1), E), "g4_e": (grav("rect", ne=4), E),
        "g4_t": (grav("rect", ne=4), T), "g4_tg": (grav("rect", ne=4), TG),
        "q4_3t": (load("rect", ne=4, line3=True), T),
        # the end to end picture
        "tip3": (tip("rect", ne=4, line3=True), E),
    }


def fragment_values(path):
    """{variable: {eid: [values]}} and {variable: {nid: value}} of a results fragment,
    per step: the last step only; these are static runs."""
    text = io.open(path, encoding="utf-8").read()
    ids, eids = {}, {}
    for tag, target in (("nodeids", ids), ("elementnodeids", eids)):
        for m in re.finditer(r'<{0}(?: name="([^"]*)")?[^>]*>([^<]*)</{0}>'.format(tag), text):
            data = base64.b64decode(m.group(2))
            target[m.group(1)] = struct.unpack("<%di" % (len(data) // 4), data)
    nodes, elements = {}, {}

    def values(body):
        kind = re.search(r"<values(double|single)>([^<]*)<", body)
        data = base64.b64decode(kind.group(2))
        size = 8 if kind.group(1) == "double" else 4
        return struct.unpack("<%d%s" % (len(data) // size, "d" if size == 8 else "f"), data)

    for m in re.finditer(r'<nodevariable name="([^"]*)"[^>]*>(.*?)</nodevariable>', text, re.S):
        name = m.group(1)
        order = ids.get(name) or ids[list(ids)[0]]
        nodes[name] = dict(zip(order, values(m.group(2))))
    for m in re.finditer(r'<elementnodevariable name="([^"]*)"[^>]*>(.*?)</elementnodevariable>',
                         text, re.S):
        name = m.group(1)
        elements[name] = {"eids": eids.get(name) or eids[list(eids)[0]],
                          "values": values(m.group(2))}
    return nodes, elements, text[:400]


def run_case(work, bridge, name, make, general, analysis="lin_static_3d"):
    folder = os.path.join(work, name)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    model_name = "aster_" + name
    model = make(model_name)
    source = os.path.join(ga.gm.OUT, model_name + ".liml")
    cfg = os.path.join(folder, "cfg.json")
    with open(cfg, "w") as f:
        json.dump({"settings": {}, "general": general, "timeout": 1800}, f)
    run = os.path.join(folder, "run")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    start = time.time()
    code = subprocess.call([sys.executable, "-B", os.path.join(
        bridge, "TestFiles", "Verificatie", "_run_one.py"), run, source, analysis, cfg],
        env=env, creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with io.open(os.path.join(run, "run.json"), encoding="utf-8") as f:
        info = json.load(f)
    spent = time.time() - start
    print(name, "code", code, "%.1f s" % spent, info.get("diagnostic"), info.get("errors"),
          flush=True)
    out = {"case": name, "general": general, "diagnostic": info.get("diagnostic"),
           "errors": info.get("errors"), "skipped": info.get("skipped"),
           "info": model.extra, "beams": model.beams}
    if info.get("results"):
        nodes, elements, head = fragment_values(info["results"])
        out["node_variables"] = sorted(nodes)
        out["element_variables"] = sorted(elements)
        out["nodes"] = dict((k, {str(i): v for i, v in nodes[k].items()}) for k in nodes
                            if k in ("displx", "disply", "displz", "rotx", "roty", "rotz",
                                     "tensileforce", "sheary", "shearz", "torsion",
                                     "bendingmomv", "bendingmomw", "reactionforcey"))
        out["elements"] = dict((k, elements[k]) for k in elements)
        out["head"] = head
        solved = os.path.join(folder, "solved.liml")
        subprocess.call(["powershell", "-ExecutionPolicy", "Bypass", "-File",
                         os.path.join(HERE, "..", "..", "tools", "run_ipy.ps1"), "-Script",
                         os.path.join(HERE, "splice_ipy.py"), "-ScriptArgs", ",".join(
                             [os.path.join(bridge, "Scripts"), source, info["results"], solved])],
                        creationflags=NO_WINDOW, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
        out["solved_size"] = os.path.getsize(solved) if os.path.isfile(solved) else None
    with open(os.path.join(ga.gm.OUT, "..", "results", model_name + ".json"), "w") as f:
        json.dump(out, f, sort_keys=True)
    return info.get("diagnostic")


def main(args):
    work, bridge = os.path.abspath(args[0]), os.path.abspath(args[1])
    table = cases()
    for name in args[2:] or sorted(table):
        make, general = table[name][:2]
        run_case(work, bridge, name, make, general, *table[name][2:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
