# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# v7.py
# V7: T_S = T + EY Vw - EZ Vv against plain T, on the solids of solid.py.
# Passes when the tool with T_S is within 5 % on tau AND the tool with plain T
# is at least 25 % off (five times the margin), so that the test shows that the
# term is needed. Also a separate test of the shear centre, which the stress
# comparison cannot give: for given Vv, Vw, T about G the tool's stress does
# not depend on which S is used, because S moves the field and the term alike.
#
#   python v7.py <tag of the L model> <tag of the long U model>

import json
import math
import os
import sys

import compare

MARGIN_TAU = 0.05
MIN_PLAIN = 0.25


def t_s_row(solid, name, weights):
    a = compare.compare(solid, weights)
    b = compare.compare(solid, weights, plain=True)
    ok = a["e_tau"] <= MARGIN_TAU and b["e_tau"] >= MIN_PLAIN
    print("{0:8s} {1:6s} T_S: tau {2:6.2f} % sig {3:5.2f} % vm {4:6.2f} %   plain T: tau {5:7.2f} % "
          "vm {6:7.2f} %   {7}".format(solid.tag, name, 100 * a["e_tau"], 100 * a["e_sig"],
                                       100 * a["e_vm"], 100 * b["e_tau"], 100 * b["e_vm"],
                                       "OK" if ok else "FAIL"))
    print("         forces", {k: round(v, 3) for k, v in a["forces"].items()},
          "term", round(compare.tool_for(solid.sec, solid.info["nu"]).f
                        .torque_at_shear_centre(a["forces"])[1], 3))
    return {"T_S": a, "plain": b, "ok": ok}


def vlasov(solid):
    """Warping share at the station, cantilever with a tip torque."""
    tool = compare.tool_for(solid.sec, solid.info["nu"])
    f = tool.f
    e_mod, nu = solid.info["E"], solid.info["nu"]
    g = e_mod / (2.0 * (1.0 + nu))
    gamma = tool.wp["gamma"]
    k = math.sqrt(g * f.j / (e_mod * gamma))
    length, x = solid.info["length"], solid.info["x"][0]
    return {"k": k, "L": length, "kL": k * length, "10_over_k": 10.0 / k,
            "T_omega_over_T": math.cosh(k * (length - x)) / math.cosh(k * length),
            "B_over_Tk": math.sinh(k * (length - x)) / math.cosh(k * length),
            "Iw": gamma, "J": f.j}


def shear_centre_test(solid):
    """Solid's shear centre from the twist rate between L/2 - h and L/2 + h:
    twist(e) = twist_Z + e V0 / T0 twist_T is linear in e, so e = zero of it."""
    tool = compare.tool_for(solid.sec, solid.info["nu"])
    x0, x1 = solid.info["x"][1], solid.info["x"][2]
    rate = {it: (solid.rotation(x1, it) - solid.rotation(x0, it)) / (x1 - x0) for it in (3, 4)}
    e_solid = -rate[3] / rate[4] * solid.info["T0"] / solid.info["V0"]
    wp = tool.wp
    e_tr = wp["shear_centre_trefftz"][0] - tool.f.centroid[0]
    e_el = wp["shear_centre"][0] - tool.f.centroid[0]
    fit = compare.field_fit(solid, {3: 1.0})
    v = solid.info["V0"]
    out = {"e_solid_mm": 1e3 * e_solid, "e_elastic_mm": 1e3 * e_el, "e_trefftz_mm": 1e3 * e_tr,
           "fit_a_over_V": fit["a_Vw"] / v, "fit_arm_mm": 1e3 * fit["b_T"] / v,
           "tool_EY_mm": 1e3 * tool.f.offset[0], "fit_resid": fit["resid_rel"]}
    print("shear centre, y_S - y_G in mm:", {k: round(u, 4) for k, u in out.items()})
    return out


def main():
    out = {}
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        s = compare.Solid(sys.argv[1])
        print("L section", {k: round(v, 5) for k, v in vlasov(s).items()})
        out["l"] = {"vlasov": vlasov(s), "Z": t_s_row(s, "Z", {3: 1.0}), "Y": t_s_row(s, "Y", {2: 1.0}),
                    "YZ": t_s_row(s, "Y+Z", {2: 1.0, 3: 1.0})}
    if len(sys.argv) > 2:
        s = compare.Solid(sys.argv[2])
        v = vlasov(s)
        print("U section", {k: round(u, 5) for k, u in v.items()})
        z = t_s_row(s, "Z", {3: 1.0})
        tool = compare.tool_for(s.sec, s.info["nu"])
        ts = abs(tool.f.torque_at_shear_centre(z["T_S"]["forces"])[0])
        c_ref = ts / v["k"] * v["B_over_Tk"] / v["Iw"]
        print("bimoment: fit |c| {0:.4e}, Vlasov {1:.4e}, ratio {2:.3f}; sigma_omega share {3:.3f} %"
              .format(abs(z["T_S"]["bim_c"]), c_ref, abs(z["T_S"]["bim_c"]) / c_ref,
                      100 * z["T_S"]["bim"]))
        out["u"] = {"vlasov": v, "Z": z, "S": shear_centre_test(s), "bim_ref": c_ref}
    path = os.path.join(compare._HERE, "work", "v7.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1, default=float)


if __name__ == "__main__":
    main()
