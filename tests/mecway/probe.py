# -*- coding: ascii -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Runs inside Mecway (IronPython): solve, then write the values the tests need
# as JSON. Numbers only, no paths. No modal API. Started by run_mecway.py.
import json
import os
import traceback

EL_VARS = ("tensileforce", "sheary", "shearz", "torsion", "bendingmomv", "bendingmomw",
           "stressptu", "stresspt1", "stresspt2", "stresspt3", "stresspt4")
NODE_VARS = ("displx", "disply", "displz", "rotx", "roty", "rotz", "tensileforce", "sheary",
             "shearz", "torsion", "bendingmomv", "bendingmomw")
PROPS = ("area", "iyy", "izz", "recta", "rectb", "rectthickness", "roundod", "roundid", "ia",
         "ib", "ic", "id", "pointy", "pointz", "youngsmodulus", "poissonratio", "density")
result = {"errors": []}
out = os.environ["PROBE_OUT"]
try:
    result["version"] = str(mw.version())
    mw.solve()
    elements = {}
    for eid in [int(e) for e in solution.all_elements()]:
        row = {}
        for var in EL_VARS:
            vals = []
            for k in (1, 2, 3):
                try:
                    vals.append(float(solution.element_node_value(var, eid, k)))
                except Exception:
                    vals.append(None)
            while vals and vals[-1] is None:
                vals.pop()
            if vals:
                row[var] = vals
        elements[str(eid)] = row
    nodes = {}
    for nid in [int(n) for n in solution.all_nodes()]:
        row = {}
        for var in NODE_VARS:
            try:
                value = solution.node_value(var, nid)
                row[var] = None if value is None else float(value)
            except Exception:
                pass
        nodes[str(nid)] = row
    result["elements"] = elements
    result["nodes"] = nodes
    info = open(os.environ["PROBE_INFO"])
    materials = json.loads(info.read()).get("materials", [])
    info.close()
    props = {}
    for mat in materials:
        row = {}
        for prop in PROPS:
            try:
                row[prop] = float(mw.material_property(mat, prop))
            except Exception:
                pass
        props[mat] = row
    result["material_properties"] = props
    save = os.environ.get("PROBE_SAVE")
    if save:
        mw.save_as(save)
except Exception:
    result["errors"].append(traceback.format_exc().replace("\\", "/").split("\n")[-2:])
f = open(out, "w")
f.write(json.dumps(result, sort_keys=True))
f.close()
