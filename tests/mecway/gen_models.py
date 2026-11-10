# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Generator of the own, self-made Mecway models of the F2 verification
# (research note, V4, V5, V9, V11). Writes tests/mecway/models/<name>.liml and
# <name>.info.json (per beam: the elements from root to tip and the loads as
# applied, signed). Only unit strings that Mecway itself writes.

import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "models")
FIELDS = [("displacement", "m"), ("rotation", "rad"), ("reaction force", "N"),
          ("reaction moment", "N.m"), ("stress in element coordinates", "Pa"),
          ("tensile force", "N"), ("shear force", "N"), ("torsion moment", "N.m"),
          ("bending moment 2", "N.m")]
MECH = u'<mechanical type="Isotropic" youngsmodulus="210 GPa" poissonratio="0.3" />'
# Tip load with all six components, so that all six forces are not zero
F_TIP = (100.0, 200.0, -150.0)
M_TIP = (10.0, -20.0, 30.0)
POINT = u'pointy="10 mm" pointz="5 mm"'
SECTIONS = {
    "Rect": u'type="RectBar" recta="100 mm" rectb="50 mm" ' + POINT,
    "Tube4": u'type="RectTube" recta="100 mm" rectb="60 mm" rectthickness="6 mm" ' + POINT,
    "Circ": u'type="CircularBar" roundod="80 mm" ' + POINT,
    "Tube": u'type="UniformCircularTube" roundod="80 mm" roundid="60 mm" ' + POINT,
    "Ip": u'type="I" ia="100 mm" ib="50 mm" ic="10 mm" id="8 mm" ' + POINT,
    "Tp": u'type="T" ta="50 mm" tb="5 mm" tc="9 mm" td="30 mm" ' + POINT,
    "Cp": u'type="C" ca="80 mm" cb="150 mm" cc="6 mm" cd="5 mm" ' + POINT,
    "La": u'type="L" la="80 mm" lb="100 mm" lc="5 mm" ' + POINT,
    "Lb": u'type="L" la="120 mm" lb="80 mm" lc="10 mm" ' + POINT,
    "Lc": u'type="L" la="100 mm" lb="100 mm" lc="10 mm" ' + POINT,
    "Gen": u'type="UniformGeneralSection" area="2000 mm²" iyy="2000000 mm⁴" '
           u'izz="8000000 mm⁴" torsionconstant="1000000 mm⁴" perimeter="300 mm" ' + POINT,
    "Flat": u'type="UniformGeneralSection" area="2000 mm²" iyy="66666.6666666667 mm⁴" '
            u'izz="1666666.66666667 mm⁴" torsionconstant="200000 mm⁴" perimeter="240 mm" ' + POINT,
}


class Model(object):
    def __init__(self, name, solver=None, density=None, analysis="S30"):
        self.name, self.solver, self.density, self.analysis = name, solver, density, analysis
        self.nodes, self.elsets, self.items, self.selections = [], {}, [], []
        self.custom = []
        self.clamp = []
        self.beams = {}
        self.eid = 0
        self.mats = []
        self.at = {}

    def node(self, p):
        key = tuple(round(c, 12) for c in p)
        if key in self.at:
            return self.at[key]
        self.nodes.append(p)
        self.at[key] = len(self.nodes)
        return len(self.nodes)

    def beam(self, key, mat, pts, ne=4, reverse=(), attr=u"", line3=False, clamp=True):
        """Beam through the points pts, ne elements per leg, root at pts[0].
        reverse: element positions (from the root) with swapped node order, or
        True for all."""
        if mat not in self.mats:
            self.mats.append(mat)
        eids, chain = [], []
        for a, b in zip(pts[:-1], pts[1:]):
            n = 2 * ne if line3 else ne
            ids = [self.node(tuple(a[k] + (b[k] - a[k]) * i / float(n) for k in range(3)))
                   for i in range(n + 1)]
            for i in range(ne):
                self.eid += 1
                c = ids[2 * i:2 * i + 3] if line3 else ids[i:i + 2]
                if reverse is True or len(eids) in tuple(reverse or ()):
                    c = c[::-1]
                self.elsets.setdefault(mat, []).append(
                    u'    <elem eid="%d" shape="%s" nodes="%s"%s />' % (
                        self.eid, "line3" if line3 else "line2",
                        " ".join(str(x) for x in c), attr))
                eids.append(self.eid)
            chain.append(ids)
        root, tip = chain[0][0], chain[-1][-1]
        if clamp:
            self.clamp.append(root)
        self.beams[key] = {"eids": eids, "material": mat, "root": root, "tip": tip,
                           "points": [list(p) for p in pts], "F": [0.0] * 3, "M": [0.0] * 3,
                           "q": []}
        return key

    def tip(self, key, f=F_TIP, m=M_TIP):
        b = self.beams[key]
        sel = "tip_" + key
        self.selections.append(u'  <nodeselection name="%s" direct="true"><node nid="%d" />'
                               u'</nodeselection>' % (sel, b["tip"]))
        self.items.append(u'  <force selection="%s"><x>%r N</x><y>%r N</y><z>%r N</z></force>'
                          % (sel, f[0], f[1], f[2]))
        self.items.append(u'  <moment selection="%s"><x>%r N.m</x><y>%r N.m</y><z>%r N.m</z>'
                          u'</moment>' % (sel, m[0], m[1], m[2]))
        b["F"], b["M"] = list(f), list(m)

    def element_selection(self, sel, eids):
        # Selections to choose a beam from are named ones, in Mecway's tree; those of
        # a load are direct="true", as Mecway writes them.
        direct = u"" if sel.startswith(u"sel_") else u' direct="true"'
        self.selections.append(u'  <elementselection name="%s"%s>' % (sel, direct) + u"".join(
            u'<element eid="%d" />' % e for e in eids) + u"</elementselection>")

    def line_load(self, key, q, part=None, local=False, faces=False):
        """Line load q (N/m), global x, y, z or local u, v, w, on the positions
        part of the beam (default all)."""
        b = self.beams[key]
        eids = b["eids"] if part is None else [b["eids"][i] for i in part]
        sel = "q%d_%s" % (len(self.items), key)
        if faces:
            self.selections.append(u'  <faceselection name="%s" direct="true">' % sel +
                                   u"".join(u'<face eid="%d" faceid="1" />' % e for e in eids) +
                                   u"</faceselection>")
        else:
            self.element_selection(sel, eids)
        names = "uvw" if local else "xyz"
        self.items.append(u'  <linepressure selection="%s">' % sel + u"".join(
            u"<{0}>{1!r} N/m</{0}>".format(n, v) for n, v in zip(names, q)) +
            u"</linepressure>")
        b["q"].append({"q": list(q), "eids": eids, "local": local})

    def gravity(self, g):
        self.items.append(u"  <gravity><x>%r m/s²</x><y>%r m/s²</y><z>%r m/s²</z></gravity>"
                          % tuple(g))

    def card(self, key, v, angle_deg):
        sel = "or_" + key
        self.element_selection(sel, self.beams[key]["eids"])
        self.items.append(
            u'  <orientation selection="%s"><ux>0</ux><uy>0</uy><uz>0</uz><vx>%r</vx><vy>%r</vy>'
            u'<vz>%r</vz><angleu>%r °</angleu></orientation>' % (sel, v[0], v[1], v[2], angle_deg))

    def write(self):
        out = [u'<liml version="33">', u'  <analysis type="%s"%s />' % (
            self.analysis, u' solver="%s"' % self.solver if self.solver else u""),
            u"  <incompatiblehex8>true</incompatiblehex8>"]
        for i, p in enumerate(self.nodes):
            out.append(u'  <node nid="%d" x="%.15g" y="%.15g" z="%.15g" />' % (
                i + 1, p[0], p[1], p[2]))
        for mat, lines in self.elsets.items():
            out.append(u'  <elset name="E_%s" colorrgb="DADADA" material="%s">' % (mat, mat))
            out.extend(lines)
            out.append(u"  </elset>")
        out.extend(self.items)
        if self.clamp:
            out.append(u'  <fixed selection="clamp" />')
            self.selections.append(u'  <nodeselection name="clamp" direct="true">' + u"".join(
                u'<node nid="%d" />' % n for n in self.clamp) + u"</nodeselection>")
        if self.custom:
            out.append(u"  <ccxmodeldefinition>" + u"\n".join(self.custom) +
                       u"</ccxmodeldefinition>")
        for mat in self.mats:
            out.append(u'  <mat name="%s">' % mat)
            out.append(u"    <geometric %s />" % SECTIONS[mat])
            out.append(u"    " + MECH)
            if self.density:
                out.append(u'    <density type="Simple" density="%r kg/m³" />' % self.density)
            out.append(u"  </mat>")
        out.extend(self.selections)
        out.extend(u'  <fieldvariable name="%s" displayunit="%s" />' % f for f in FIELDS)
        out.extend([u"</liml>", u""])
        with io.open(os.path.join(OUT, self.name + ".liml"), "w", encoding="utf8",
                     newline="\n") as f:
            f.write(u"\n".join(out))
        info = {"materials": self.mats, "beams": self.beams, "density": self.density}
        with open(os.path.join(OUT, self.name + ".info.json"), "w") as f:
            json.dump(info, f, indent=1, sort_keys=True)


def along_x(y, length=1.0):
    return [(0.0, y, 0.0), (length, y, 0.0)]


def sections():
    m = Model("sections")
    for k, mat in enumerate(sorted(set(SECTIONS) - set(["Flat"]))):
        m.beam("n_" + mat, mat, along_x(0.5 * k))
        m.tip("n_" + mat)
        # reversed node order, default orientation: V = Z x U flips
        m.beam("r_" + mat, mat, along_x(0.5 * k + 0.25), reverse=True)
        m.tip("r_" + mat)
    # the outline and the extra data of the general section, own numbers
    m.custom = [u"** ASTER-SECTIE Gen: A=0.002, IY=2e-06, IZ=8e-06, JX=1e-06, EY=0.001, EZ=-0.002",
                u"** SECTIE-VORM Gen: N=4, A=0.002",
                u"** SECTIE-VORM Gen: P=-0.05 -0.01 0.05 -0.01 0.05",
                u"** SECTIE-VORM Gen: P=0.01 -0.05 0.01"]
    m.write()


def signs():
    m = Model("signs")
    y = 0.0
    single = {"N": ((100.0, 0.0, 0.0), (0.0, 0.0, 0.0)), "Vy": ((0.0, 100.0, 0.0), (0.0, 0.0, 0.0)),
              "Vz": ((0.0, 0.0, 100.0), (0.0, 0.0, 0.0)), "T": ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
              "My": ((0.0, 0.0, 0.0), (0.0, 10.0, 0.0)), "Mz": ((0.0, 0.0, 0.0), (0.0, 0.0, 10.0))}
    for name in sorted(single):
        f, mm = single[name]
        for pre, rev in (("n_", ()), ("r_", True)):
            m.beam(pre + name, "Rect", along_x(y), reverse=rev)
            m.tip(pre + name, f, mm)
            y += 0.25
    variants = [
        ("vz", along_x(y), u' orientationvz="1"', ()),
        ("vec_angle", along_x(y + 0.25),
         u' orientationvx="0" orientationvy="1" orientationvz="1" angle="30 °"', ()),
        ("rev_vec_angle", along_x(y + 0.75),
         u' orientationvx="0" orientationvy="1" orientationvz="1" angle="30 °"', True),
        ("rev_vec", along_x(y + 1.0), u' orientationvy="1" orientationvz="1"', True),
        ("skew_default", [(0.0, y + 1.25, 0.0), (0.6, y + 1.65, 0.5)], u"", ()),
        ("skew_vec_angle", [(0.0, y + 2.0, 0.0), (0.6, y + 2.4, 0.5)],
         u' orientationvx="1" orientationvy="0" orientationvz="1" angle="-40 °"', ()),
        ("z_up", [(0.0, y + 2.5, 0.0), (0.0, y + 2.5, 1.0)], u"", ()),
        ("z_down", [(0.5, y + 2.5, 0.0), (0.5, y + 2.5, -1.0)], u"", ()),
        ("mid_rev", along_x(y + 2.75), u"", (1, 2)),
        ("mid_rev_vec", along_x(y + 3.0), u' orientationvz="1"', (1,)),
        ("kink", [(0.0, y + 3.25, 0.0), (0.5, y + 3.25, 0.0), (0.5, y + 3.75, 0.0)], u"", (2,)),
    ]
    for key, pts, attr, rev in variants:
        m.beam(key, "Cp", pts, attr=attr, reverse=rev)
        m.tip(key)
    m.beam("card", "Cp", along_x(y + 4.0))
    m.tip("card")
    m.card("card", (0.0, 1.0, 1.0), 30.0)
    m.beam("card_rev", "Cp", along_x(y + 4.25), reverse=True)
    m.tip("card_rev")
    m.card("card_rev", (0.0, 1.0, 1.0), 30.0)
    m.write()


Q = (120.0, -1000.0, 700.0)


def distributed():
    m = Model("dist")
    m.beam("x4", "Rect", along_x(0.0))
    m.line_load("x4", Q)
    m.beam("x1", "Rect", along_x(0.25), ne=1)
    m.line_load("x1", Q)
    m.beam("skew", "Rect", [(0.0, 1.0, 0.0), (0.6, 1.4, 0.5)])
    m.line_load("skew", Q)
    m.beam("skew_vec", "Cp", [(0.0, 2.0, 0.0), (0.6, 2.4, 0.5)],
           attr=u' orientationvx="1" orientationvy="0" orientationvz="1" angle="25 °"')
    m.line_load("skew_vec", Q)
    m.beam("rev_mid", "Rect", along_x(3.0), reverse=(1, 2))
    m.line_load("rev_mid", Q)
    m.beam("rev_vec", "Cp", along_x(3.25), reverse=True, attr=u' orientationvz="1"')
    m.line_load("rev_vec", Q)
    m.beam("part", "Rect", along_x(3.5))
    m.line_load("part", Q, part=(1, 2))
    m.beam("two", "Rect", along_x(3.75))
    m.line_load("two", (0.0, -300.0, 0.0))
    m.line_load("two", (50.0, 0.0, 200.0), part=(0, 1))
    m.beam("tipq", "Rect", along_x(4.0))
    m.line_load("tipq", Q)
    m.tip("tipq")
    m.write()


def gravity():
    m = Model("gravity", density=2000.0)
    m.gravity((3.0, -10.0, 4.0))
    m.beam("x", "Rect", along_x(0.0))
    m.beam("skew", "Rect", [(0.0, 1.0, 0.0), (0.6, 1.4, 0.5)])
    m.beam("skew_vec", "Cp", [(0.0, 2.0, 0.0), (0.6, 2.4, 0.5)],
           attr=u' orientationvx="1" orientationvy="0" orientationvz="1" angle="25 °"')
    m.beam("gen", "Gen", along_x(3.0))
    m.beam("rev", "Rect", along_x(3.25), reverse=(1,))
    m.write()


def local_load():
    """Line load in element axes."""
    m = Model("local_load")
    m.beam("loc", "Rect", [(0.0, 0.0, 0.0), (0.6, 0.4, 0.5)])
    m.line_load("loc", (120.0, -1000.0, 700.0), local=True)
    m.beam("loc_rev", "Rect", [(0.0, 1.0, 0.0), (0.6, 1.4, 0.5)], reverse=True)
    m.line_load("loc_rev", (120.0, -1000.0, 700.0), local=True)
    m.write()


def face_load():
    """Line load on a face selection of beams."""
    m = Model("face_load")
    m.beam("face", "Rect", along_x(0.0))
    m.line_load("face", Q, faces=True)
    m.write()


def direction():
    """A general section made for V = Y, U = X, on beams that fit and beams that do not."""
    m = Model("direction")
    m.beam("ok", "Flat", along_x(0.0))
    m.beam("vz", "Flat", along_x(0.25), attr=u' orientationvz="1"')
    m.beam("flip", "Flat", along_x(0.5), attr=u' orientationvy="-1"')
    m.beam("rev_default", "Flat", along_x(0.75), reverse=True)
    m.beam("rev_vec", "Flat", along_x(1.0), reverse=True, attr=u' orientationvy="1"')
    m.beam("skew", "Flat", [(0.0, 1.25, 0.0), (0.6, 1.65, 0.5)])
    m.beam("card", "Flat", along_x(2.0))
    m.card("card", (0.0, 0.0, 1.0), 0.0)
    for key in sorted(m.beams):
        m.tip(key)
    m.custom = [u"** SECTIE-VORM Flat: N=4, A=0.002",
                u"** SECTIE-VORM Flat: P=-0.05 -0.01 0.05 -0.01 0.05",
                u"** SECTIE-VORM Flat: P=0.01 -0.05 0.01",
                u"** SECTIE-RICHTING Flat: V=0 1 0, U=1 0 0"]
    m.write()


def angle_only():
    """An angle without a vector: is it used?"""
    m = Model("angle_only")
    m.beam("angle_only", "Cp", along_x(0.0), attr=u' angle="30 °"')
    m.tip("angle_only")
    m.write()


def line3():
    m = Model("line3")
    m.beam("tip", "Rect", along_x(0.0), line3=True)
    m.tip("tip")
    m.beam("q", "Rect", along_x(0.25), line3=True)
    m.line_load("q", Q)
    m.beam("q1", "Rect", along_x(0.5), ne=1, line3=True)
    m.line_load("q1", Q)
    m.beam("q_rev", "Rect", along_x(0.75), line3=True, reverse=(1, 2))
    m.line_load("q_rev", Q)
    m.write()


def ccx(name, as_line3):
    m = Model(name, solver="ccx")
    m.beam("tip", "Rect", along_x(0.0), line3=as_line3)
    m.tip("tip")
    m.beam("q", "Rect", along_x(0.25), line3=as_line3)
    m.line_load("q", Q)
    m.write()


def ccx_reversed():
    """Node values of CCX where the node order changes along a beam."""
    m = Model("ccx_rev", solver="ccx")
    m.beam("tip", "Rect", along_x(0.0), line3=True, reverse=(1, 2))
    m.tip("tip")
    m.write()


if __name__ == "__main__":
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    sections()
    signs()
    distributed()
    gravity()
    local_load()
    face_load()
    angle_only()
    line3()
    ccx("ccx_line2", False)
    ccx("ccx_line3", True)
    ccx_reversed()
    direction()
    print("ok")
