# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# patch_test.py
# Harness check: a rectangle with minimal support under uniform axial traction
# must give sigma_xx = N/A exactly, and the other components zero. This tests
# the HEXA20 node order, the mesh file and the extraction. Run after
#   python solid.py rect 2 --tag patch --patch --layers-h 3
import compare
import numpy as np

s = compare.Solid("patch")
p, st = s.stress(s.info["x"][0], {1: 1.0})
ref = s.info["N0"] / s.prop["A"]
print("sigma_xx / (N/A) min max", st["SIXX"].min() / ref, st["SIXX"].max() / ref)
print("others / (N/A)", [float(np.abs(st[c]).max() / ref) for c in ("SIYY", "SIZZ", "SIXY", "SIXZ", "SIYZ")])
print("forces", s.forces(p, st))
d = s.res["depl_1"]
c = s.coords
print("u_x / (N x / EA) at the end", d[c[:, 0] > 0.999 * s.info["length"], 0].mean() /
      (s.info["N0"] * s.info["length"] / (s.info["E"] * s.prop["A"])))
