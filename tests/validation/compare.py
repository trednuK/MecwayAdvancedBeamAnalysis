# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# compare.py
# Compares the solids of solid.py with section_stress.SectionField. The Gauss
# points of the mid plane of a layer are used directly, without extrapolation.
#
# Measures, fixed before the runs. All points outside the corner zones.
#   e_sig    max|sigma_solid - sigma_tool| / max|sigma_solid|
#   e_txy    max|d tau_xy| / max|tau vector of the solid|, e_txz the same
#   e_tau    max|d tau vector| / max|tau vector of the solid|
#   e_vm     max|d von Mises| / max von Mises
#   own_*    tau component against its own peak (combination only)
#   rel30    largest pointwise relative error at points above 30 % of the peak
#   sv_*     the components that are zero in Saint-Venant, against max von Mises
#   bim      warping stress in the difference: fit of sigma_solid - sigma_tool
#            on 1, y, z, omega; max|c omega_n| / max|sigma_solid|, with omega_n
#            the part of omega that is orthogonal to 1, y, z
#   e_sig_nobim  e_sig with that fit taken out: what a bimoment does not explain

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [_HERE, os.path.join(_HERE, "..", "..", "Scripts")]

import numpy as np  # noqa: E402

import section_fem  # noqa: E402
import section_stress as ss  # noqa: E402
import sections  # noqa: E402

PLANE_WEIGHT = 8.0 / 9.0       # weight of the middle point of a 3-point rule
LAYER_WEIGHTS = np.array([5.0, 8.0, 5.0]) / 18.0


# ------------------------------------------------------------------ solid side
class Solid(object):
    def __init__(self, tag):
        self.tag = tag
        self.work = os.path.join(_HERE, "work", tag)
        with open(os.path.join(self.work, "info.json")) as f:
            self.info = json.load(f)
        self.sec = sections.ALL[self.info["section"]]()
        self.prop = sections.properties(self.sec)
        self.res = np.load(os.path.join(self.work, "result.npz"))
        n = np.load(os.path.join(self.work, "nodes.npz"))
        self.coords, self.kx = n["coords"], n["kx"]
        self.dx = self.info["dx"]
        self.check_gauss()

    def check_gauss(self):
        """27 points per element, 9 in the mid plane, weights fit volume and
        first moments of the section."""
        r = self.res
        cnt = np.bincount(r["cells"])
        cnt = cnt[cnt > 0]
        if not np.all(cnt == 27):
            raise RuntimeError("not FPG27")
        a, yg, zg = self.prop["A"], self.prop["yg"], self.prop["zg"]
        for x in self.info["x"]:
            p = self.plane(x)
            if len(p["y"]) != 9 * self.info["n_cells"]:
                raise RuntimeError("points in the plane")
            for val, ref, nm in ((p["w"].sum(), a, "area"), (p["w"].dot(p["y"]), a * yg, "yg"),
                                 (p["w"].dot(p["z"]), a * zg, "zg")):
                if abs(val / ref - 1.0) > 1e-9:
                    raise RuntimeError("Gauss check {0}: {1} {2}".format(nm, val, ref))

    def plane(self, x):
        """Gauss points of the layer with x in its mid plane, as columns of the
        three planes of the 3-point rule along x: idx (9 per cell, 3)."""
        r = self.res
        ids = np.nonzero(np.abs(r["gX"] - x) < 0.5 * self.dx)[0]
        order = np.lexsort((r["gX"][ids], np.round(r["gZ"][ids], 10), np.round(r["gY"][ids], 10),
                            r["cells"][ids]))
        idx = ids[order].reshape(-1, 3)
        if (np.ptp(r["gY"][idx], axis=1).max() > 1e-9 or np.ptp(r["gZ"][idx], axis=1).max() > 1e-9
                or np.abs(r["gX"][idx[:, 1]] - x).max() > 1e-9):
            raise RuntimeError("Gauss points of the layer do not line up")
        m = idx[:, 1]
        return {"idx": idx, "mid": m, "y": r["gY"][m], "z": r["gZ"][m], "cell": r["cells"][m],
                "w": r["gW"][m] / (0.5 * self.dx * PLANE_WEIGHT)}

    def stress(self, x, weights, layer=True):
        """Stress of a combination {1: a, 2: b, ...} of the base cases.

        layer: the Gauss mean over the element layer. The transverse
        displacement of a beam is cubic in x and hex20 is quadratic, so the
        shear on the mid plane alone is off by the third derivative of v times
        dx^2 / 24 (measured: V 0.74 % low on the rectangle, whatever the mesh
        in the section). The mean over the layer has no such error, and for
        sigma, which is linear in x, it is the value on the mid plane."""
        p = self.plane(x)
        out = {}
        for c in ("SIXX", "SIYY", "SIZZ", "SIXY", "SIXZ", "SIYZ"):
            v = sum(w * self.res["{0}_{1}".format(c, it)] for it, w in weights.items())
            out[c] = v[p["idx"]].dot(LAYER_WEIGHTS) if layer else v[p["mid"]]
        return p, out

    def forces(self, p, s):
        w = p["w"]
        y, z = p["y"] - self.prop["yg"], p["z"] - self.prop["zg"]
        sx, ty, tz = s["SIXX"], s["SIXY"], s["SIXZ"]
        return {"N": w.dot(sx), "Vv": w.dot(ty), "Vw": w.dot(tz),
                "T": w.dot(y * tz - z * ty), "Mv": w.dot(z * sx), "Mw": -w.dot(y * sx)}

    def statics(self, x, weights):
        i = self.info
        arm = i["length"] - x
        n = weights.get(1, 0.0) * i["N0"]
        fy = weights.get(2, 0.0) * i["V0"]
        fz = weights.get(3, 0.0) * i["V0"]
        t = weights.get(4, 0.0) * i["T0"]
        return {"N": n, "Vv": fy, "Vw": fz, "T": t, "Mv": -arm * fz, "Mw": arm * fy}

    def rotation(self, x, it):
        """Least-squares rigid motion of the nodes in the plane of a layer:
        u_y = a - th z, u_z = b + th y. Returns th."""
        k = 2 * int(round(x / self.dx - 0.5)) + 1
        m = self.kx == k
        c = self.coords[m]
        d = self.res["depl_{0}".format(it)][m]
        y, z = c[:, 1] - c[:, 1].mean(), c[:, 2] - c[:, 2].mean()
        rhs = np.concatenate([d[:, 1], d[:, 2]])
        n = len(y)
        A = np.zeros((2 * n, 3))
        A[:n, 0] = 1.0
        A[n:, 1] = 1.0
        A[:n, 2] = -z
        A[n:, 2] = y
        return float(np.linalg.lstsq(A, rhs, rcond=None)[0][2])


# ------------------------------------------------------------------- tool side
class Tool(object):
    """A SectionField and evaluation at arbitrary points, by the tri6 shape
    functions inside the element that holds the point."""

    def __init__(self, sec, nu, n_target):
        self.f = ss.SectionField(sec["loops"], nu=nu, n_target=n_target)
        X = self.f.xy[self.f.elements[:, :3]]
        self.x, self.y = X[:, :, 0], X[:, :, 1]
        self.det = ((self.x[:, 1] - self.x[:, 0]) * (self.y[:, 2] - self.y[:, 0]) -
                    (self.x[:, 2] - self.x[:, 0]) * (self.y[:, 1] - self.y[:, 0]))
        self.wp = section_fem.warping_properties(self.f.nodes, self.f.elements, self.f.geo, nu=nu)
        self.omega = self.wp["_omega"]

    def locate(self, pts):
        x, y, det = self.x, self.y, self.det
        el = np.zeros(len(pts), dtype=int)
        zeta = np.zeros((len(pts), 3))
        for s in range(0, len(pts), 500):
            q = pts[s:s + 500]
            l2 = ((q[:, 0, None] - x[:, 0]) * (y[:, 2] - y[:, 0]) -
                  (x[:, 2] - x[:, 0]) * (q[:, 1, None] - y[:, 0])) / det
            l3 = ((x[:, 1] - x[:, 0]) * (q[:, 1, None] - y[:, 0]) -
                  (q[:, 0, None] - x[:, 0]) * (y[:, 1] - y[:, 0])) / det
            l1 = 1.0 - l2 - l3
            best = np.minimum(np.minimum(l1, l2), l3)
            e = np.argmax(best, axis=1)
            i = np.arange(len(q))
            if best[i, e].min() < -1e-7:
                raise RuntimeError("point outside the tool mesh")
            el[s:s + 500] = e
            zeta[s:s + 500] = np.column_stack([l1[i, e], l2[i, e], l3[i, e]])
        return el, zeta

    @staticmethod
    def shape(zeta):
        z1, z2, z3 = zeta.T
        return np.column_stack([z1 * (2 * z1 - 1), z2 * (2 * z2 - 1), z3 * (2 * z3 - 1),
                                4 * z1 * z2, 4 * z2 * z3, 4 * z3 * z1])

    def at(self, values, el, sh):
        return np.sum(values[el] * sh, axis=1)

    def evaluate(self, forces, pts, plain=False):
        el, zeta = self.locate(pts)
        sh = self.shape(zeta)
        s = self.f.stress(forces, about_shear_centre=plain)
        return {"sigma": self.at(s["sigma"], el, sh), "tau_v": self.at(s["tau_v"], el, sh),
                "tau_w": self.at(s["tau_w"], el, sh),
                "omega": np.sum(self.omega[self.f.elements][el] * sh, axis=1), "el": el, "sh": sh}

    def corner_distance(self, pts):
        """Distance to the nearest corner in units of its local thickness."""
        if not self.f.corners:
            return np.full(len(pts), np.inf)
        d = [np.hypot(pts[:, 0] - p[0], pts[:, 1] - p[1]) / t for p, t in self.f.corners]
        return np.min(d, axis=0)


TOOLS = {}


def tool_for(sec, nu, n_target=12000):
    key = (sec["name"], nu, n_target)
    if key not in TOOLS:
        TOOLS[key] = Tool(sec, nu, n_target)
    return TOOLS[key]


def measures(st, sig_t, tv_t, tw_t, keep):
    """Error measures at the kept points. st: stress of the solid."""
    sx, ty, tz = st["SIXX"][keep], st["SIXY"][keep], st["SIXZ"][keep]
    a, b, c = sig_t[keep], tv_t[keep], tw_t[keep]
    tau_s = np.hypot(ty, tz)
    vm_s = np.sqrt(sx ** 2 + 3.0 * tau_s ** 2)
    vm_t = np.sqrt(a ** 2 + 3.0 * (b ** 2 + c ** 2))
    pk_s, pk_t = np.abs(sx).max(), tau_s.max()
    pk_vm = vm_s.max()
    out = {"peak_sig": pk_s, "peak_tau": pk_t, "peak_vm": pk_vm,
           "e_sig": np.abs(sx - a).max() / max(pk_s, 1e-30),
           "e_txy": np.abs(ty - b).max() / max(pk_t, 1e-30),
           "e_txz": np.abs(tz - c).max() / max(pk_t, 1e-30),
           "e_tau": np.hypot(ty - b, tz - c).max() / max(pk_t, 1e-30),
           "e_vm": np.abs(vm_s - vm_t).max() / max(pk_vm, 1e-30),
           "own_txy": np.abs(ty - b).max() / max(np.abs(ty).max(), 1e-30),
           "own_txz": np.abs(tz - c).max() / max(np.abs(tz).max(), 1e-30)}

    def rel(sol, too):
        big = np.abs(sol) > 0.3 * np.abs(sol).max()
        return float(np.max(np.abs(sol[big] - too[big]) / np.abs(sol[big]))) if big.any() else 0.0

    out["rel30_sig"], out["rel30_tau"] = rel(sx, a), rel(tau_s, np.hypot(b, c))
    out["rel30_vm"] = rel(vm_s, vm_t)
    return out


def compare(solid, weights, x=None, n_target=12000, plain=False, zone=1.0, layer=True):
    """All measures for one combination of base cases at station x."""
    x = solid.info["x"][0] if x is None else x
    tool = tool_for(solid.sec, solid.info["nu"], n_target)
    p, st = solid.stress(x, weights, layer=layer)
    pts = np.column_stack([p["y"] - solid.prop["yg"], p["z"] - solid.prop["zg"]])
    fs = solid.forces(p, st)
    tv = tool.evaluate(fs, pts, plain=plain)
    dist = tool.corner_distance(pts)
    keep = dist > zone
    out = measures(st, tv["sigma"], tv["tau_v"], tv["tau_w"], keep)
    out["forces"] = {k: float(v) for k, v in fs.items()}
    stat = solid.statics(x, weights)
    out["statics"] = {k: float(v) for k, v in stat.items()}
    h = solid.info["h"]
    scale_f = max(abs(stat["N"]), abs(stat["Vv"]), abs(stat["Vw"]), abs(stat["T"]) / h, 1e-30)
    scale_m = max(abs(stat["T"]), abs(stat["Mv"]), abs(stat["Mw"]), scale_f * h)
    out["balance"] = max([abs(fs[k] - stat[k]) / scale_f for k in ("N", "Vv", "Vw")] +
                         [abs(fs[k] - stat[k]) / scale_m for k in ("T", "Mv", "Mw")])
    # Saint-Venant zero components
    out["sv_syy"] = float(np.abs(st["SIYY"][keep]).max() / out["peak_vm"])
    out["sv_szz"] = float(np.abs(st["SIZZ"][keep]).max() / out["peak_vm"])
    out["sv_tyz"] = float(np.abs(st["SIYZ"][keep]).max() / out["peak_vm"])
    out["sig_over_vm"] = float(out["peak_sig"] / out["peak_vm"])
    # bimoment in the difference
    d = st["SIXX"][keep] - tv["sigma"][keep]
    A = np.column_stack([np.ones(keep.sum()), pts[keep, 0], pts[keep, 1], tv["omega"][keep]])
    c = np.linalg.lstsq(A, d, rcond=None)[0]
    # omega is about the centroid: its part in 1, y, z is no warping stress
    om = tv["omega"][keep]
    om = om - A[:, :3].dot(np.linalg.lstsq(A[:, :3], om, rcond=None)[0])
    out["bim"] = float(np.abs(c[3] * om).max() / max(out["peak_sig"], 1e-30))
    out["bim_c"] = float(c[3])
    out["e_sig_nobim"] = float(np.abs(d - A.dot(c)).max() / max(out["peak_sig"], 1e-30))
    # where the largest tau error sits
    e = np.hypot(st["SIXY"] - tv["tau_v"], st["SIXZ"] - tv["tau_w"])
    e = np.where(keep, e, -1.0)
    i = int(np.argmax(e))
    out["worst_tau_dist"] = float(dist[i])
    out["worst_tau_at"] = [float(p["y"][i]), float(p["z"][i])]
    e2 = np.where(keep, np.abs(st["SIXX"] - tv["sigma"]), -1.0)
    i = int(np.argmax(e2))
    out["worst_sig_at"] = [float(p["y"][i]), float(p["z"][i])]
    out["n_points"] = int(keep.sum())
    return out


def field_fit(solid, weights, x=None, n_target=12000, zone=1.0):
    """tau_solid = a t_w + b t_t + c t_v by least squares outside the zones.
    a, c test the shear fields, b the arm, the residual the field itself."""
    x = solid.info["x"][0] if x is None else x
    tool = tool_for(solid.sec, solid.info["nu"], n_target)
    p, st = solid.stress(x, weights)
    pts = np.column_stack([p["y"] - solid.prop["yg"], p["z"] - solid.prop["zg"]])
    keep = tool.corner_distance(pts) > zone
    el, zeta = tool.locate(pts)
    sh = tool.shape(zeta)
    f = tool.f.fields
    cols = []
    for key in ("vw", "t", "vv"):
        cols.append(np.concatenate([tool.at(f[key][:, :, 0], el, sh)[keep],
                                    tool.at(f[key][:, :, 1], el, sh)[keep]]))
    A = np.column_stack(cols)
    rhs = np.concatenate([st["SIXY"][keep], st["SIXZ"][keep]])
    c = np.linalg.lstsq(A, rhs, rcond=None)[0]
    res = rhs - A.dot(c)
    return {"a_Vw": float(c[0]), "b_T": float(c[1]), "c_Vv": float(c[2]),
            "resid_rel": float(np.abs(res).max() / np.abs(rhs).max())}


def cases_for(solid, section_key):
    """{case name: weights}; only the cases that are valid for this model."""
    tool = tool_for(solid.sec, solid.info["nu"])
    e = tool.f.shear_centre[0]              # y_S - y_G
    t_to_s = e * solid.info["V0"] / solid.info["T0"]
    n, y, z, tt = 1, 2, 3, 4
    combo = {n: 30.0, y: 1.0, z: -2.0}
    if section_key in ("rect", "box", "l"):
        c = dict(combo)
        c[tt] = 0.3
        return {"N": {n: 1.0}, "Y": {y: 1.0}, "Z": {z: 1.0}, "T": {tt: 1.0}, "combo": c}
    if section_key == "i":
        return {"N": {n: 1.0}, "Y": {y: 1.0}, "Z": {z: 1.0}, "combo": combo}
    if section_key == "u":
        c = {n: 30.0, y: 1.0, z: -2.0, tt: -2.0 * t_to_s}
        return {"N": {n: 1.0}, "Y": {y: 1.0}, "ZS": {z: 1.0, tt: t_to_s}, "combo": c}
    if section_key == "ulong":
        d = {"N": {n: 1.0}, "Y": {y: 1.0}, "Z": {z: 1.0}, "T": {tt: 1.0},
             "ZS": {z: 1.0, tt: t_to_s}}
        return d
    raise KeyError(section_key)


def row(tag, case, r, x_frac=None):
    txt = ("{0:12s} {1:6s} sig {2:6.3f} % txy {3:6.3f} txz {4:6.3f} tau {5:6.3f} vm {6:6.3f} | "
           "own {7:6.2f} {8:6.2f} rel30 s {9:5.2f} t {10:5.2f} | bal {11:.1e} sv {12:.3f} {13:.3f} "
           "{14:.3f} bim {15:.3f} dist {16:.2f}")
    return txt.format(tag, case, 100 * r["e_sig"], 100 * r["e_txy"], 100 * r["e_txz"],
                      100 * r["e_tau"], 100 * r["e_vm"], 100 * r["own_txy"], 100 * r["own_txz"],
                      100 * r["rel30_sig"], 100 * r["rel30_tau"], r["balance"],
                      100 * r["sv_syy"], 100 * r["sv_szz"], 100 * r["sv_tyz"], 100 * r["bim"],
                      r["worst_tau_dist"])


def report(tag, key=None, n_target=12000, zones=(1.0, 2.0), stations=True):
    s = Solid(tag)
    key = key or s.info["section"]
    out = {"info": s.info, "cases": {}}
    print("---", tag, "dof", s.info["dof"], "x/h", [round(x / s.info["h"], 2) for x in s.info["x"]],
          "L", s.info["length"])
    for name, w in cases_for(s, key).items():
        for zone in zones:
            r = compare(s, w, n_target=n_target, zone=zone)
            out["cases"]["{0}@{1}".format(name, zone)] = r
            print(row(tag, name + "@" + str(int(zone)), r))
        if stations:
            for x, lab in zip(s.info["x"][1:], ("-h", "+h")):
                r = compare(s, w, x=x, n_target=n_target)
                out["cases"]["{0}{1}".format(name, lab)] = r
                print(row(tag, name + lab, r))
    with open(os.path.join(s.work, "compare.json"), "w") as f:
        json.dump(out, f, indent=1)
    return out


if __name__ == "__main__":
    for t in sys.argv[1:]:
        tag, _, key = t.partition(":")
        report(tag, key or None)
