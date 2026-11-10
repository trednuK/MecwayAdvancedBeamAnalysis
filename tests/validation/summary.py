# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# summary.py
# One line per model and load case at mid span, for RESULTS.md, plus the error
# against the distance to the ends.
#
#   python summary.py <tag>[:key] ...

import sys

import compare


def line(tag, name, r, r2):
    return ("{0:11s} {1:6s} sig {2:6.3f} | txy {3:5.2f} txz {4:5.2f} tau {5:5.2f} vm {6:5.2f} | "
            "own {7:5.2f} {8:5.2f} | rel30 {9:5.1f} | r2t tau {10:5.2f} sig {11:6.3f} | "
            "syy {12:5.3f} szz {13:5.3f} tyz {14:5.3f} bim {15:6.3f} | bal {16:.0e} dist {17:5.2f}"
            ).format(tag, name, 100 * r["e_sig"], 100 * r["e_txy"], 100 * r["e_txz"],
                     100 * r["e_tau"], 100 * r["e_vm"], 100 * r["own_txy"], 100 * r["own_txz"],
                     100 * r["rel30_tau"], 100 * r2["e_tau"], 100 * r2["e_sig"],
                     100 * r["sv_syy"], 100 * r["sv_szz"], 100 * r["sv_tyz"], 100 * r["bim"],
                     r["balance"], min(r["worst_tau_dist"], 99.0))


def main():
    for arg in sys.argv[1:]:
        tag, _, key = arg.partition(":")
        s = compare.Solid(tag)
        i = s.info
        print("#", tag, "dof", i["dof"], "L/h", round(i["length"] / i["h"], 2), "dx/h",
              round(i["dx"] / i["h"], 3), "cells", i["n_cells"], "layers", i["layers"], "nu", i["nu"],
              flush=True)
        for name, w in compare.cases_for(s, key or i["section"]).items():
            print(line(tag, name, compare.compare(s, w), compare.compare(s, w, zone=2.0)), flush=True)


if __name__ == "__main__":
    main()
