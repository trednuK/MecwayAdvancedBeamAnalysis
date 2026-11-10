# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# The own models of check V8: beams through MecwayAsterBridge and Code-Aster
# (run_v8.py). Every model is a .liml as Mecway would hold it: a general section
# with SECTIE-VORM and #ASTER-SECTIE, or a Mecway profile. Run with a Python with
# numpy and scipy. The section numbers come from section_stress, so the model
# carries what MecwaySectionProps would write.

import math
import os
import sys

import gen_e2e as ge
import gen_models as gm

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [os.path.join(HERE, "..", "..", "Scripts"),
                os.path.join(HERE, "..", "validation")]

import section_fem  # noqa: E402
import section_stress as ss  # noqa: E402
import sections  # noqa: E402

# The shear factors go to the Timoshenko elements. Not 1, on purpose: with AY != 1
# SIEF_ELGA of POU_D_TG in MECA_STATIQUE is wrong (research note par. 9).
AY, AZ = 2.0, 1.5


def principal(loop):
    """(loop about the centroid in principal axes, turn in radians, centroid):
    v' = c v + s w, w' = -s v + c w."""
    a, cv, cw, ivv, iww, ivw = ge.polygon(loop)
    # ivv = int w^2, iww = int v^2
    t = 0.5 * math.atan2(-2.0 * ivw, ivv - iww) if abs(ivw) > 1e-14 * ivv else 0.0
    c, s = math.cos(t), math.sin(t)
    moved = [(c * (v - cv) + s * (w - cw), -s * (v - cv) + c * (w - cw)) for v, w in loop]
    return moved, t, (cv, cw)


def section_lines(name, sec, nu=0.3, shift=(0.0, 0.0)):
    """Registers the validation section sec under name as a general section.
    Returns (lines, data); data has the turn to principal axes and EY, EZ. shift:
    added to EY, EZ of the #ASTER-SECTIE line, away from the shape."""
    loop = sec["loops"][0]
    if len(sec["loops"]) > 1:
        moved, turn, centre = principal(loop)
        loops = [[(a - centre[0], b - centre[1]) for a, b in lp] for lp in sec["loops"]]
    else:
        moved, turn, centre = principal(loop)
        loops = [moved]
    field = ss.SectionField(loops, nu=nu, n_target=12000)
    wp = section_fem.warping_properties(field.nodes, field.elements, field.geo, nu=nu)
    a = field.mesh_props["A"]
    ivv, iww = field.mesh_props["ivv"], field.mesh_props["iww"]
    ey, ez = field.offset
    ey, ez = float(ey) + shift[0], float(ez) + shift[1]
    gm.SECTIONS[name] = (
        u'type="UniformGeneralSection" area="{0!r} m²" iyy="{1!r} m⁴" izz="{2!r} m⁴" '
        u'torsionconstant="{3!r} m⁴" perimeter="0.3 m" '.format(a, ivv, iww, field.j) + gm.POINT)
    lines = []
    counts = u" ".join(str(len(lp)) for lp in loops)
    lines.append(ge.SHAPE.format(name) + u"N={0}, A={1!r}".format(counts, a))
    flat = [u"%.10g" % c for lp in loops for p in lp for c in p]
    for i in range(0, len(flat), 6):
        lines.append(ge.SHAPE.format(name) + u"P=" + u" ".join(flat[i:i + 6]))
    lines.append(u"** ASTER-SECTIE {0}: A={1!r}, IY={2!r}, IZ={3!r}, JX={4!r}, AY={5}, AZ={6}, "
                 u"EY={7!r}, EZ={8!r}, JG={9!r}, IYR2=0, IZR2=0".format(
                     name, a, ivv, iww, field.j, AY, AZ, ey, ez, float(abs(wp["gamma"]))))
    return lines, {"turn": turn, "centre": list(centre), "EY": ey, "EZ": ez,
                   "A": a, "ivv": ivv, "iww": iww, "j": field.j, "jg": float(abs(wp["gamma"])),
                   "k_inverse": field.warping_length}


def cantilever(name, section_key, tip=None, moment=None, q=None, length=None, ne=8, line3=False,
               general=True, material=None, gravity=None, shift=(0.0, 0.0)):
    """A cantilever along X with V along the principal axis of the section."""
    m = ge.Model(name, density=7850.0)
    m.custom = []
    data = {}
    attr = u""
    if general:
        lines, data = section_lines(section_key, sections.ALL[section_key](), shift=shift)
        m.custom = lines
        length = length or 8.0 * sections.ALL[section_key]()["h"]
        c, s = math.cos(data["turn"]), math.sin(data["turn"])
        if abs(s) > 1e-12:
            attr = u' orientationvx="0" orientationvy="%r" orientationvz="%r"' % (c, s)
        key = section_key
    else:
        key = material
        length = length or 1.0
    m.beam("b", key, gm.along_x(0.0, length), ne=ne, attr=attr, line3=line3)
    if tip is not None or moment is not None:
        m.tip("b", tuple(tip or (0.0, 0.0, 0.0)), tuple(moment or (0.0, 0.0, 0.0)))
    if q is not None:
        m.line_load("b", q)
    if gravity is not None:
        m.gravity(gravity)
    m.element_selection("sel_b", m.beams["b"]["eids"])
    m.extra = {"section": data, "length": length, "tip": list(tip or (0, 0, 0)),
               "moment": list(moment or (0, 0, 0)), "q": list(q) if q else None,
               "gravity": list(gravity) if gravity else None}
    m.write()
    return m
