# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# convergence.py
# Mesh convergence of the solid: stress at fixed points of the section, from a
# biquadratic fit through the 9 Gauss points of the element in the mid plane.
# Rule fixed before the runs: the measure on the finest mesh is within the
# margin AND it changes by less than margin / 2 between the two finest meshes;
# otherwise the solid, not the tool, is the limit.
#
#   python convergence.py <section>[:case key] <tag> <tag> ...

import sys

import numpy as np

import compare

MM = 1e-3
PROBES = {"rect": [(20, 40), (38, 40), (20, 78), (30, 60)],
          "i": [(30, 50), (30, 20), (15, 4), (5, 96)],
          "box": [(3, 50), (30, 3), (3, 20), (57, 70)],
          "l": [(4, 50), (30, 4), (4, 70)],
          "u": [(3, 50), (30, 4), (30, 96), (3, 25)]}


def probe(solid, weights, y, z):
    p, st = solid.stress(solid.info["x"][0], weights)
    i = int(np.argmin(np.hypot(p["y"] - y, p["z"] - z)))
    m = p["cell"] == p["cell"][i]
    yy = p["y"][m] - y
    zz = p["z"][m] - z
    A = np.column_stack([yy ** a * zz ** b for a in range(3) for b in range(3)])
    return [float(np.linalg.solve(A, st[c][m])[0]) for c in ("SIXX", "SIXY", "SIXZ")]


def main():
    sec, _, key = sys.argv[1].partition(":")
    tags = sys.argv[2:]
    solids = [compare.Solid(t) for t in tags]
    cases = compare.cases_for(solids[0], key or sec)
    print("#", sec, "tags", tags, "dof", [s.info["dof"] for s in solids])
    w = cases["combo"] if "combo" in cases else cases["Z"]
    for y, z in PROBES[sec]:
        vals = [probe(s, w, y * MM, z * MM) for s in solids]
        print("point ({0},{1}) sig/txy/txz MPa:".format(y, z),
              " | ".join("{0:8.3f} {1:7.3f} {2:7.3f}".format(*(1e-6 * np.array(v))) for v in vals))
    for name, wc in cases.items():
        rs = [compare.compare(s, wc) for s in solids]
        print("{0:6s} e_sig %".format(name), " ".join("{0:6.3f}".format(100 * r["e_sig"]) for r in rs),
              "  e_tau %", " ".join("{0:6.3f}".format(100 * r["e_tau"]) for r in rs),
              "  e_vm %", " ".join("{0:6.3f}".format(100 * r["e_vm"]) for r in rs),
              "  balance", " ".join("{0:.1e}".format(r["balance"]) for r in rs))


if __name__ == "__main__":
    main()
