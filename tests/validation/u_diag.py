# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# u_diag.py
# Diagnosis of the channel with the load through S: error in sigma along the
# beam, for the plain end traction (ZS) and the Saint-Venant traction (SV).
# bim(x) is the warping stress in the difference; a bimoment from the clamped
# end follows sinh(k (L - x)) / sinh(k L), a disturbance from the loaded end
# decays from x = L instead.
#
#   python u_diag.py <tag> ...

import json
import math
import os
import sys

import numpy as np

import compare


def main():
    for tag in sys.argv[1:]:
        s = compare.Solid(tag)
        tool = compare.tool_for(s.sec, s.info["nu"])
        i = s.info
        g = i["E"] / (2.0 * (1.0 + i["nu"]))
        k = math.sqrt(g * tool.f.j / (i["E"] * tool.wp["gamma"]))
        length, h = i["length"], i["h"]
        e = tool.f.shear_centre[0]
        cases = {"ZS": {3: 1.0, 4: e * i["V0"] / i["T0"]}}
        if i.get("sv"):
            cases["SV"] = {5: 1.0}
        print("---", tag, "dof", i["dof"], "L/h", round(length / h, 2), "k", round(k, 3), "kL",
              round(k * length, 2), "sv", i.get("sv"))
        out = {}
        for name, w in cases.items():
            print(" case x/h   e_sig%  noBim%  bim%    c*1e-9   c/shape*1e-9  e_tau%  T_S/(V e)   syy% szz% tyz%")
            rows = []
            for x in sorted(i["x"]):
                r = compare.compare(s, w, x=x)
                shape = math.sinh(k * (length - x)) / math.sinh(k * length)
                ts = tool.f.torque_at_shear_centre(r["forces"])[0]
                rows.append(dict(x=x, **{q: r[q] for q in ("e_sig", "e_sig_nobim", "bim", "bim_c", "e_tau",
                                                            "sv_syy", "sv_szz", "sv_tyz")}, T_S=ts, shape=shape))
                print(" {0:4s} {1:5.2f} {2:7.3f} {3:7.3f} {4:6.3f} {5:9.3f} {6:9.3f} {7:7.2f} {8:10.2e} "
                      "{9:6.3f} {10:6.3f} {11:6.3f}".format(
                          name, x / h, 100 * r["e_sig"], 100 * r["e_sig_nobim"], 100 * r["bim"],
                          r["bim_c"] * 1e-9, r["bim_c"] * 1e-9 / shape, 100 * r["e_tau"],
                          ts / (i["V0"] * abs(e)), 100 * r["sv_syy"], 100 * r["sv_szz"], 100 * r["sv_tyz"]))
            out[name] = rows
        if "SV" in cases:
            # the disturbance of the plain traction alone: ZS minus SV
            w = dict(cases["ZS"])
            w[5] = -1.0
            xs, amp = [], []
            for x in sorted(i["x"]):
                p, st = s.stress(x, w)
                _, ref = s.stress(x, {5: 1.0})
                keep = tool.corner_distance(np.column_stack(
                    [p["y"] - s.prop["yg"], p["z"] - s.prop["zg"]])) > 1.0
                d = (length - x) / h
                a = float(np.abs(st["SIXX"][keep]).max())
                xs.append(d)
                amp.append(a)
                print("  to loaded end {0:6.2f} h: ZS-SV sigma {1:9.4f} MPa = {2:8.4f} % of sigma".format(
                    d, 1e-6 * a, 100 * a / np.abs(ref["SIXX"][keep]).max()))
            xs, amp = np.array(xs), np.array(amp)
            m = (xs >= 3.0) & (xs <= 9.0)
            slope = np.polyfit(xs[m], np.log(amp[m]), 1)[0]
            print("  decay length of the loaded-end disturbance: {0:.2f} h".format(-1.0 / slope))
            out["decay_h"] = -1.0 / slope
        with open(os.path.join(s.work, "u_diag.json"), "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
