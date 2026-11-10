# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Check V8 proper: the same cantilevers as the solids of V6 and V7, now as beams through
# MecwayAsterBridge and Code-Aster, POU_D_E and POU_D_TG. The section forces of that
# solution go through beam_model and section_stress, with EY, EZ of #ASTER-SECTIE,
# and the stress is compared on the Gauss points of the solid, with the measures of
# tests/validation/compare.py. Needs the solids in tests/validation/work (not in git);
# writes tests/mecway/results/v8_solids.json.
#
#   python tests/mecway/v8_solids.py <work folder> <bridge export>

from __future__ import print_function

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)

import gen_aster as ga  # noqa: E402
import run_v8  # noqa: E402

import numpy as np  # noqa: E402

import beam_model as bm  # noqa: E402
import compare  # noqa: E402
import section_fem  # noqa: E402
import section_stress as ss  # noqa: E402

SOLIDS = (("rect_n4", "rect", "rect"), ("i16_c4", "i", "i"), ("box16_c4", "box", "box"),
          ("l_c6", "l", "l"), ("ulong_c4", "ulong", "u"))
ELEMENTS = (("e", run_v8.E), ("tg", run_v8.TG))
MARGIN_SIGMA, MARGIN_TAU = 0.01, 0.05
ELEMENTS_PER_BEAM = 8


def tool_of(field, nu):
    """compare.Tool around a field made from the model."""
    tool = compare.Tool.__new__(compare.Tool)
    tool.f = field
    x = field.xy[field.elements[:, :3]]
    tool.x, tool.y = x[:, :, 0], x[:, :, 1]
    tool.det = ((tool.x[:, 1] - tool.x[:, 0]) * (tool.y[:, 2] - tool.y[:, 0]) -
                (tool.x[:, 2] - tool.x[:, 0]) * (tool.y[:, 1] - tool.y[:, 0]))
    tool.wp = section_fem.warping_properties(field.nodes, field.elements, field.geo, nu=nu)
    tool.omega = tool.wp["_omega"]
    return tool


def raw_of(result):
    out = {}
    for var, data in result["elements"].items():
        eids, values = data["eids"], data["values"]
        size = len(values) // len(eids)
        for i, eid in enumerate(eids):
            out.setdefault(eid, {})[var] = [values[i * size], values[(i + 1) * size - 1]]
    return out


def one(solid, key, name, weights, element, general, work, bridge):
    i = solid.info
    length = i["length"]
    tip = (weights.get(1, 0.0) * i["N0"], weights.get(2, 0.0) * i["V0"],
           weights.get(3, 0.0) * i["V0"])
    torque = (weights.get(4, 0.0) * i["T0"], 0.0, 0.0)
    case = "v8_{0}_{1}_{2}".format(solid.tag, name, element)
    run_v8.run_case(work, bridge, case, lambda n: ga.cantilever(
        n, key, tip=tip, moment=torque, length=length, ne=ELEMENTS_PER_BEAM), general)
    with open(os.path.join(HERE, "results", "aster_" + case + ".json")) as f:
        result = json.load(f)
    os.remove(os.path.join(HERE, "results", "aster_" + case + ".json"))
    path = os.path.join(ga.gm.OUT, "aster_" + case + ".liml")
    model = bm.read_model(path)
    model.solver = bm.ASTER
    chain = bm.build_chain(model, result["beams"]["b"]["eids"])
    for ext in (".liml", ".info.json"):
        os.remove(os.path.join(ga.gm.OUT, "aster_" + case + ext))
    x = i["x"][0]
    at = bm.forces_at(chain, raw_of(result), x)
    spec = bm.section_spec(model, at["element"], aster_offset=True)
    field = ss.SectionField(spec["loops"], nu=spec["nu"], props=spec["props"],
                            offset=spec["offset"], n_target=12000)
    turn = result["info"]["section"]["turn"]
    c, s = math.cos(turn), math.sin(turn)
    p, st = solid.stress(x, weights)
    dy, dz = p["y"] - solid.prop["yg"], p["z"] - solid.prop["zg"]
    pts = np.column_stack([c * dy + s * dz, -s * dy + c * dz])
    tool = tool_of(field, spec["nu"])
    values = tool.evaluate(at["forces"], pts)
    keep = tool.corner_distance(pts) > 1.0
    ty = c * values["tau_v"] - s * values["tau_w"]
    tz = s * values["tau_v"] + c * values["tau_w"]
    out = compare.measures(st, values["sigma"], ty, tz, keep)
    # the same forces from the solid itself, in the same axes
    fs = solid.forces(p, st)
    fv = c * fs["Vv"] + s * fs["Vw"]
    fw = -s * fs["Vv"] + c * fs["Vw"]
    mv = c * fs["Mv"] + s * fs["Mw"]
    mw = -s * fs["Mv"] + c * fs["Mw"]
    solid_forces = {"N": fs["N"], "Vv": fv, "Vw": fw, "T": fs["T"], "Mv": mv, "Mw": mw}
    row = {"solid": solid.tag, "case": name, "element": element, "x": x, "length": length,
           "e_sig": float(out["e_sig"]), "e_tau": float(out["e_tau"]),
           "e_vm": float(out["e_vm"]), "e_txy": float(out["e_txy"]), "e_txz": float(out["e_txz"]),
           "peak_sig": float(out["peak_sig"]), "peak_tau": float(out["peak_tau"]),
           "forces": {k: float(v) for k, v in at["forces"].items()},
           "solid_forces": {k: float(v) for k, v in solid_forces.items()},
           "EY": result["info"]["section"]["EY"], "EZ": result["info"]["section"]["EZ"],
           "diagnostic": result["diagnostic"]}
    print(case, "sig %.3f tau %.3f vm %.3f %%" % (100 * row["e_sig"], 100 * row["e_tau"],
                                                  100 * row["e_vm"]), flush=True)
    return row


def main(args):
    work, bridge = os.path.abspath(args[0]), os.path.abspath(args[1])
    rows = []
    for tag, key, section in SOLIDS:
        solid = compare.Solid(tag)
        for name, weights in compare.cases_for(solid, key).items():
            for element, general in ELEMENTS:
                rows.append(one(solid, section, name, weights, element, general, work, bridge))
    with open(os.path.join(HERE, "results", "v8_solids.json"), "w") as f:
        json.dump(rows, f, indent=0, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
