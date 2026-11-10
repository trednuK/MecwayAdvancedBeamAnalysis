# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# dx_check.py
# dx against dx / 2: the shear on the mid plane of a layer and the layer mean.
#
#   python dx_check.py <tag dx> <tag dx/2> ...
import sys

import compare

for pair in zip(sys.argv[1::2], sys.argv[2::2]):
    a, b = [compare.Solid(t) for t in pair]
    print("#", pair, "dx/h", round(a.dx / a.info["h"], 4), round(b.dx / b.info["h"], 4))
    cases = compare.cases_for(a, a.info["section"])
    for name in cases:
        if name == "N":
            continue
        w = cases[name]
        for layer in (False, True):
            r = [compare.compare(s, w, layer=layer) for s in (a, b)]
            v = [max(abs(q["forces"]["Vv"]), abs(q["forces"]["Vw"])) for q in r]
            print("{0:6s} {1:9s} V {2:9.3f} {3:9.3f}  e_sig {4:6.3f} {5:6.3f}  e_tau {6:6.2f} {7:6.2f}".format(
                name, "layer" if layer else "mid plane", v[0], v[1], 100 * r[0]["e_sig"], 100 * r[1]["e_sig"],
                100 * r[0]["e_tau"], 100 * r[1]["e_tau"]))
