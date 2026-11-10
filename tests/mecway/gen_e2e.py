# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# The own, self-made models of the end to end runs in real Mecway (run_e2e.py,
# e2e.py). Writes tests/mecway/models/e2e_<name>.liml and .info.json. The large
# model of the marker measurement goes to a scratch folder (`big` below), never
# into the repository.

import io
import json
import os
import re
import sys

import gen_models as gm

SHAPE = u"** SECTIE-VORM {0}: "


def shape_lines(name, loop, area):
    """The SECTIE-VORM of one loop, in the file format of MecwaySectionProps."""
    out = [SHAPE.format(name) + u"N={0}, A={1!r}".format(len(loop), area)]
    flat = [u"%.10g" % c for p in loop for c in p]
    for i in range(0, len(flat), 6):
        out.append(SHAPE.format(name) + u"P=" + u" ".join(flat[i:i + 6]))
    return out


def polygon(loop):
    """(A, centroid v, centroid w, int w^2, int v^2, int v w about the centroid)."""
    a = cv = cw = vv = ww = vw = 0.0
    n = len(loop)
    for i in range(n):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % n]
        c = x0 * y1 - x1 * y0
        a += c
        cv += (x0 + x1) * c
        cw += (y0 + y1) * c
        vv += (y0 * y0 + y0 * y1 + y1 * y1) * c
        ww += (x0 * x0 + x0 * x1 + x1 * x1) * c
        vw += (x0 * y1 + 2.0 * x0 * y0 + 2.0 * x1 * y1 + x1 * y0) * c
    a /= 2.0
    cv /= 6.0 * a
    cw /= 6.0 * a
    return (a, cv, cw, vv / 12.0 - a * cw * cw, ww / 12.0 - a * cv * cv,
            vw / 24.0 - a * cv * cw)


def general(name, loop, torsion, perimeter=0.3):
    """Registers a general section; returns its SECTIE-VORM lines and properties."""
    a, cv, cw, ivv, iww, ivw = polygon(loop)
    moved = [(v - cv, w - cw) for v, w in loop]
    gm.SECTIONS[name] = (
        u'type="UniformGeneralSection" area="{0!r} m²" iyy="{1!r} m⁴" izz="{2!r} m⁴" '
        u'torsionconstant="{3!r} m⁴" perimeter="{4!r} m" '.format(
            a, ivv, iww, torsion, perimeter) + gm.POINT)
    return shape_lines(name, moved, a), {"A": a, "ivv": ivv, "iww": iww, "ivw": ivw,
                                         "j": torsion, "loop": moved}


RECT = [(-0.05, -0.025), (0.05, -0.025), (0.05, 0.025), (-0.05, 0.025)]
# Channel, height 100 along V, flanges 50 along W, 5 thick, counter-clockwise
CHANNEL = [(-0.05, 0.0), (-0.045, 0.0), (-0.045, 0.045), (0.045, 0.045), (0.045, 0.0),
           (0.05, 0.0), (0.05, 0.05), (-0.05, 0.05)]


class Model(gm.Model):
    """gm.Model with a <timesteps> line and extra analysis attributes."""

    def __init__(self, name, solver=None, density=None, analysis="S30", attrs=u"",
                 steps=None):
        gm.Model.__init__(self, name, solver, density, analysis)
        self.attrs, self.steps = attrs, steps
        self.extra = {}

    def face_selection(self, sel, eids):
        self.selections.append(u'  <faceselection name="%s">' % sel + u"".join(
            u'<face eid="%d" faceid="1" />' % e for e in eids) + u"</faceselection>")

    def write(self):
        gm.Model.write(self)
        path = os.path.join(gm.OUT, self.name + ".liml")
        with io.open(path, encoding="utf8") as f:
            text = f.read()
        if self.attrs:
            head = u'<analysis type="%s"' % self.analysis
            text = text.replace(head, head + u" " + self.attrs, 1)
        if self.steps:
            text = text.replace(u"  <incompatiblehex8>", u"  " + self.steps +
                                u"\n  <incompatiblehex8>", 1)
        with io.open(path, "w", encoding="utf8", newline="\n") as f:
            f.write(text)
        info = os.path.join(gm.OUT, self.name + ".info.json")
        with open(info) as f:
            data = json.load(f)
        data.update(self.extra)
        with open(info, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)


def internal():
    """Mecway's own solver, line2: a general section with SECTIE-VORM, its eccentric
    twin, an I profile by element and by face selection, a line load, reversed
    elements in a shuffled selection."""
    m = Model("e2e_internal")
    lines_r, rect = general("GenR", RECT, 2.86e-6)
    lines_u, chan = general("GenU", CHANNEL, 8.0e-8)
    m.custom = lines_r + lines_u
    m.beam("gen_r", "GenR", gm.along_x(0.0))
    m.tip("gen_r")
    m.element_selection("sel_gen_r", m.beams["gen_r"]["eids"])
    m.beam("gen_u", "GenU", gm.along_x(0.25))
    m.tip("gen_u")
    m.element_selection("sel_gen_u", m.beams["gen_u"]["eids"])
    m.beam("ip", "Ip", gm.along_x(0.5))
    m.tip("ip")
    m.element_selection("sel_ip", m.beams["ip"]["eids"])
    m.face_selection("face_ip", m.beams["ip"]["eids"])
    m.beam("ip_q", "Ip", gm.along_x(0.75))
    m.line_load("ip_q", gm.Q)
    m.element_selection("sel_ip_q", m.beams["ip_q"]["eids"])
    m.beam("ip_rev", "Ip", gm.along_x(1.0), reverse=(1, 2))
    m.tip("ip_rev")
    eids = m.beams["ip_rev"]["eids"]
    m.element_selection("sel_ip_rev", [eids[2], eids[0], eids[3], eids[1]])
    m.extra = {"sections": {"GenR": rect, "GenU": chan}}
    m.write()


def ccx_line3():
    """CCX, line3: node values only. CCX takes no I profile through Mecway (measured: an
    empty solution, no error), so a rectangle."""
    m = Model("e2e_ccx", solver="ccx")
    m.beam("ip", "Rect", gm.along_x(0.0), line3=True)
    m.tip("ip")
    m.element_selection("sel_ip", m.beams["ip"]["eids"])
    m.face_selection("face_ip", m.beams["ip"]["eids"])
    m.beam("rect_q", "Rect", gm.along_x(0.25), line3=True)
    m.line_load("rect_q", gm.Q)
    m.element_selection("sel_rect_q", m.beams["rect_q"]["eids"])
    m.write()


def modal():
    """Frequencies of a cantilever: what the panel offers as modes."""
    m = Model("e2e_modal", density=7850.0, analysis="V30", attrs=u'maxmode="3"')
    m.beam("ip", "Ip", gm.along_x(0.0), ne=16)
    m.element_selection("sel_ip", m.beams["ip"]["eids"])
    m.write()


def steps(name, solver, analysis, attrs=u""):
    """Time steps: what the panel offers as steps."""
    m = Model(name, solver=solver, analysis=analysis, density=7850.0, attrs=attrs,
              steps=u'<timesteps timeperiod="1 s" stepsize="0.25 s" />')
    m.beam("ip", "Rect", gm.along_x(0.0), line3=solver == "ccx")
    # a force that grows with time, so that the steps differ: 100 N at t = 1 s
    m.selections.append(u'  <nodeselection name="tip_ip" direct="true"><node nid="%d" />'
                        u'</nodeselection>' % m.beams["ip"]["tip"])
    m.items.append(u'  <force selection="tip_ip"><y time="0 s">0 N</y><y time="1 s">100 N</y>'
                   u'</force>')
    m.element_selection("sel_ip", m.beams["ip"]["eids"])
    m.write()


def configurations():
    """Two configurations: another tip force in each."""
    m = Model("e2e_config")
    m.beam("ip", "Rect", gm.along_x(0.0))
    m.selections.append(u'  <nodeselection name="tip_ip" direct="true"><node nid="%d" />'
                        u'</nodeselection>' % m.beams["ip"]["tip"])
    m.items.append(u'  <configuration name="A" />')
    m.items.append(u'  <configuration name="B" />')
    m.items.append(u'  <force selection="tip_ip"><x>0 N</x><y>100 N</y><z>0 N</z>'
                   u'<suppressed configuration="B" /></force>')
    m.items.append(u'  <force selection="tip_ip"><x>0 N</x><y>300 N</y><z>0 N</z>'
                   u'<suppressed configuration="A" /></force>')
    m.element_selection("sel_ip", m.beams["ip"]["eids"])
    # a second beam: q_y and a tip force in both, an orientation card V = Z suppressed in
    # B, q_z suppressed in A (the card and the loads per configuration, review finding)
    m.beam("oc", "Rect", gm.along_x(0.5))
    eids = m.beams["oc"]["eids"]
    m.selections.append(u'  <nodeselection name="tip_oc" direct="true"><node nid="%d" />'
                        u'</nodeselection>' % m.beams["oc"]["tip"])
    m.items.append(u'  <force selection="tip_oc"><x>0 N</x><y>100 N</y><z>0 N</z></force>')
    m.element_selection("q_oc", eids)
    m.items.append(u'  <linepressure selection="q_oc"><x>0 N/m</x><y>-1000 N/m</y><z>0 N/m</z>'
                   u'</linepressure>')
    m.items.append(u'  <linepressure selection="q_oc"><x>0 N/m</x><y>0 N/m</y><z>-500 N/m</z>'
                   u'<suppressed configuration="A" /></linepressure>')
    m.element_selection("or_oc", eids)
    m.items.append(u'  <orientation selection="or_oc"><ux>0</ux><uy>0</uy><uz>0</uz><vx>0</vx>'
                   u'<vy>0</vy><vz>1</vz><angleu>0 °</angleu><suppressed configuration="B" />'
                   u'</orientation>')
    m.element_selection("sel_oc", eids)
    m.write()


def buckling():
    """Buckling: what the panel offers as buckling modes."""
    m = Model("e2e_buckling", analysis="B30", attrs=u'maxmode="2"')
    m.beam("ip", "Rect", gm.along_x(0.0), ne=8)
    m.tip("ip", (-1000.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    m.element_selection("sel_ip", m.beams["ip"]["eids"])
    m.write()


def renumbered(name, new):
    """A cantilever of four elements with the nodes along it numbered new, and in the
    file from tip to root: which node gets which value from set_variable."""
    m = Model(name)
    m.beam("b", "Rect", gm.along_x(0.0), ne=4)
    m.tip("b")
    m.element_selection("sel_b", m.beams["b"]["eids"])
    m.write()
    path = os.path.join(gm.OUT, m.name + ".liml")
    with io.open(path, encoding="utf8") as f:
        text = f.read()
    new = dict((i + 1, n) for i, n in enumerate(new))
    lines = text.split(u"\n")
    nodes = [l for l in lines if l.startswith(u"  <node nid=")]
    first = lines.index(nodes[0])
    moved = []
    for line in nodes:
        old = int(line.split(u'"')[1])
        moved.append(line.replace(u'nid="%d"' % old, u'nid="%d"' % new[old]))
    lines[first:first + len(nodes)] = moved[::-1]
    text = u"\n".join(lines)

    def mapped(found):
        return found.group(1) + u" ".join(str(new[int(n)]) for n in found.group(2).split()) + u'"'
    text = re.sub(r'(<elem eid="\d+" shape="line2" nodes=")([\d ]+)"', mapped, text)
    for name in (u"tip_b", u"clamp"):
        text = re.sub(u'(<nodeselection name="%s" direct="true"><node nid=")(\\d+)"' % name,
                      lambda f: f.group(1) + str(new[int(f.group(2))]) + u'"', text)
    with io.open(path, "w", encoding="utf8", newline="\n") as f:
        f.write(text)
    info = os.path.join(gm.OUT, m.name + ".info.json")
    with open(info) as f:
        data = json.load(f)
    data["along"] = [new[i] for i in range(1, 6)]
    data["beams"]["b"]["root"], data["beams"]["b"]["tip"] = new[1], new[5]
    with open(info, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


FLAT = [(-0.05, -0.01), (0.05, -0.01), (0.05, 0.01), (-0.05, 0.01)]


def numbers():
    """Several sections and the checks of a general section: one that fits its
    SECTIE-RICHTING, the same turned and reversed, one with iyy and izz swapped, one
    without SECTIE-VORM, a straight chain half without shape, and a bent chain."""
    m = Model("e2e_numbers")
    lines_e, flat = general("FlatE", FLAT, 2.3e-7)
    general("Swap", FLAT, 2.3e-7)
    swapped = gm.SECTIONS["Swap"]
    iyy = re.search(u'iyy="([^"]*)"', swapped).group(1)
    izz = re.search(u'izz="([^"]*)"', swapped).group(1)
    gm.SECTIONS["Swap"] = swapped.replace(u'iyy="%s"' % iyy, u'iyy="@"').replace(
        u'izz="%s"' % izz, u'izz="%s"' % iyy).replace(u'iyy="@"', u'iyy="%s"' % izz)
    lines_s = shape_lines("Swap", flat["loop"], flat["A"])
    general("NoShape", FLAT, 2.3e-7)
    m.custom = lines_e + [u"** SECTIE-RICHTING FlatE: V=0 1 0, U=1 0 0"] + lines_s
    beams = (("dir_ok", "FlatE", gm.along_x(0.0), {}),
             ("dir_vz", "FlatE", gm.along_x(0.25), {"attr": u' orientationvz="1"'}),
             ("dir_rev", "FlatE", gm.along_x(0.5), {"reverse": True}),
             ("swap", "Swap", gm.along_x(0.75), {}),
             ("noshape", "NoShape", gm.along_x(1.0), {}))
    for key, mat, pts, kw in beams:
        m.beam(key, mat, pts, **kw)
        m.tip(key)
        m.element_selection("sel_" + key, m.beams[key]["eids"])
    m.beam("mixed_a", "Rect", [(0.0, 1.25, 0.0), (0.5, 1.25, 0.0)], ne=3)
    m.beam("mixed_b", "NoShape", [(0.5, 1.25, 0.0), (1.0, 1.25, 0.0)], ne=3, clamp=False)
    m.tip("mixed_b")
    m.element_selection("sel_mixed", m.beams["mixed_a"]["eids"] + m.beams["mixed_b"]["eids"])
    m.beam("bent", "Rect", [(0.0, 1.5, 0.0), (0.6, 1.5, 0.0), (0.6, 1.9, 0.0)], ne=3)
    m.tip("bent")
    m.element_selection("sel_bent", m.beams["bent"]["eids"])
    m.extra = {"sections": {"FlatE": flat}}
    m.write()


def view3d():
    """The 3D view and the detail of one section: a straight C profile of six elements,
    and an L profile in a Z shape of three equal legs with elements 2, 3 and 6 reversed,
    so that four sections fall on the root, both kinks and the tip."""
    m = Model("e2e_view3d")
    m.beam("straight", "Cp", [(0.0, 0.0, 0.0), (1.2, 0.0, 0.0)], ne=6)
    m.tip("straight")
    m.element_selection("sel_straight", m.beams["straight"]["eids"])
    m.beam("kinked", "Lb", [(0.0, 0.6, 0.0), (0.5, 0.6, 0.0), (0.5, 1.1, 0.0), (1.0, 1.1, 0.0)],
           ne=2, reverse=(1, 2, 5))
    m.tip("kinked")
    m.element_selection("sel_kinked", m.beams["kinked"]["eids"])
    eids = m.beams["kinked"]["eids"]
    m.extra = {"reversed": [eids[i] for i in (1, 2, 5)]}
    m.write()


def big(folder, n_plate=316):
    """A plate of about 100 000 quad4 and a beam beside it, for the cost of a
    marker on a large model. Written to folder, solved never."""
    out = [u'<liml version="33">', u'  <analysis type="S30" />']
    k = n_plate + 1
    side = 1.0 / n_plate
    for j in range(k):
        for i in range(k):
            out.append(u'  <node nid="%d" x="%.9g" y="%.9g" z="0" />' % (
                j * k + i + 1, i * side, j * side))
    nid = k * k
    beam_nodes = []
    for i in range(5):
        nid += 1
        beam_nodes.append(nid)
        out.append(u'  <node nid="%d" x="%.9g" y="-0.1" z="0" />' % (nid, 0.25 * i))
    out.append(u'  <elset name="E_Plate" colorrgb="DADADA" material="Plate">')
    eid = 0
    for j in range(n_plate):
        for i in range(n_plate):
            eid += 1
            a = j * k + i + 1
            out.append(u'    <elem eid="%d" shape="quad4" nodes="%d %d %d %d" />' % (
                eid, a, a + 1, a + k + 1, a + k))
    out.append(u"  </elset>")
    out.append(u'  <elset name="E_Rect" colorrgb="DADADA" material="Rect">')
    beam = []
    for a, b in zip(beam_nodes[:-1], beam_nodes[1:]):
        eid += 1
        beam.append(eid)
        out.append(u'    <elem eid="%d" shape="line2" nodes="%d %d" />' % (eid, a, b))
    out.append(u"  </elset>")
    out.append(u'  <mat name="Plate"><geometric type="Plate" thickness="1 mm" />' + gm.MECH +
               u"</mat>")
    out.append(u'  <mat name="Rect"><geometric %s />' % gm.SECTIONS["Rect"] + gm.MECH + u"</mat>")
    out.append(u'  <elementselection name="sel_beam">' + u"".join(
        u'<element eid="%d" />' % e for e in beam) + u"</elementselection>")
    out.extend(u'  <fieldvariable name="%s" displayunit="%s" />' % f for f in gm.FIELDS)
    out.extend([u"</liml>", u""])
    path = os.path.join(folder, "e2e_big.liml")
    with io.open(path, "w", encoding="utf8", newline="\n") as f:
        f.write(u"\n".join(out))
    return path, eid


def main():
    if not os.path.isdir(gm.OUT):
        os.makedirs(gm.OUT)
    internal()
    ccx_line3()
    modal()
    steps("e2e_steps_ccx", "ccx", "L30")
    configurations()
    buckling()
    numbers()
    view3d()
    renumbered("e2e_gap", (3, 1, 5, 2, 4))          # out of order, no gaps
    renumbered("e2e_gap2", (1, 2, 4, 7, 9))         # with gaps, node 1 there
    renumbered("e2e_gap3", (30, 10, 50, 20, 40))    # with gaps, no node 1
    if len(sys.argv) > 1:
        print(big(sys.argv[1]))


if __name__ == "__main__":
    main()
