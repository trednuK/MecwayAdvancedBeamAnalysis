# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# solid.py
# Structured hex20 cantilever in Code-Aster 17.4 for validation V6 and V7.
# Run with the Python of Code-Aster (numpy, scipy):
#
#   python solid.py <section> <n_wall> [--len-h 8] [--len 2.86] [--layers-h 10]
#                   [--nu 0.3] [--tag x] [--patch] [--ncpus 8] [--mem 16000]
#
# Mesh: a tensor grid of the section, extruded along X. x = 0 is clamped; the
# end x = L carries distributed traction only, so that it is free to warp.
# Four load cases on one factorisation, each unit:
#   N   FX = 1 kN / A
#   Y   FY = 1 kN / A        through the centroid
#   Z   FZ = 1 kN / A        through the centroid
#   T   (FY, FZ) = T (-(z - zG), y - yG) / Ip, T = 100 N m, about the centroid
# With --sv a fifth case: on the end the Saint-Venant traction of the tool for
# Vw = 1 kN through the shear centre, constant per end face. It starts the
# solid without disturbance from the loaded end, and separates that end from
# the clamped end in the diagnosis of the channel.
# Output: work/<tag>/result.npz with the Gauss points of the station planes
# and the displacements of all nodes.

import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.dont_write_bytecode = True

import sections  # noqa: E402

ASTER = os.path.join(os.environ.get("LOCALAPPDATA", ""), "code_aster", "v2025", "17.4", "bin",
                     "run_aster.bat")
E_MOD = 210e9
GL3 = math.sqrt(0.6)
N0, V0, T0 = 1000.0, 1000.0, 100.0


def grid_lines(lines, size, aspect, wall, cosine=False):
    """Grid lines. cosine: long spans are refined towards both ends, where the
    wall ends and the flange-web junctions sit (a boundary layer of about one
    wall thickness in torsion)."""
    out = [lines[0]]
    for a, b in zip(lines[:-1], lines[1:]):
        span = b - a
        long_span = span > 2.0 * wall
        ratio = aspect if long_span else 1.0
        n = max(1, int(math.ceil(span / (size * ratio) - 1e-9)))
        if cosine and long_span:
            out.extend(a + span * 0.5 * (1.0 - math.cos(math.pi * (i + 1) / n)) for i in range(n))
        else:
            out.extend(a + span * (i + 1) / n for i in range(n))
    return np.array(out)


# lattice offsets (y, z, x) of the 20 nodes in code_aster's HEXA20 order, in
# half cells; y, z, x is right-handed because Y x Z = X
HEX20 = np.array([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0), (0, 0, 2), (2, 0, 2), (2, 2, 2),
                  (0, 2, 2), (1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0), (0, 0, 1), (2, 0, 1),
                  (2, 2, 1), (0, 2, 1), (1, 0, 2), (2, 1, 2), (1, 2, 2), (0, 1, 2)])
QUAD8 = np.array([(0, 0), (2, 0), (2, 2), (0, 2), (1, 0), (2, 1), (1, 2), (0, 1)])


def build_mesh(sec, n_wall, length, layers, cosine=False):
    size = sec["wall"] / float(n_wall)
    ys = grid_lines(sec["lines_y"], size, sec["aspect"], sec["wall"], cosine)
    zs = grid_lines(sec["lines_z"], size, sec["aspect"], sec["wall"], cosine)
    ym, zm = 0.5 * (ys[:-1] + ys[1:]), 0.5 * (zs[:-1] + zs[1:])
    keep = np.zeros((len(ym), len(zm)), dtype=bool)
    for y0, z0, y1, z1 in sec["rects"]:
        keep |= ((ym[:, None] > y0) & (ym[:, None] < y1) &
                 (zm[None, :] > z0) & (zm[None, :] < z1))
    cy, cz = np.nonzero(keep)
    ny, nz, nx = 2 * len(ym) + 1, 2 * len(zm) + 1, 2 * layers + 1

    def key(i, j, k):
        return i + ny * (j + nz * k)

    # cells (E), layers (L): lattice keys of all element nodes (L*E, 20)
    con = np.empty((layers * len(cy), 20), dtype=np.int64)
    for a, (oy, oz, ox) in enumerate(HEX20):
        c = key(2 * cy[None, :] + oy, 2 * cz[None, :] + oz,
                2 * np.arange(layers)[:, None] + ox)
        con[:, a] = c.ravel()
    uniq, inv = np.unique(con, return_inverse=True)
    con = inv.reshape(con.shape)
    iy = uniq % ny
    jz = (uniq // ny) % nz
    kx = uniq // (ny * nz)
    # coordinates of the half cells
    yh = np.zeros(ny)
    yh[::2], yh[1::2] = ys, ym
    zh = np.zeros(nz)
    zh[::2], zh[1::2] = zs, zm
    coords = np.column_stack([np.linspace(0.0, length, nx)[kx], yh[iy], zh[jz]])
    # the end face: QUAD8 at x = L; lattice keys back to node numbers
    pos = {int(u): i for i, u in enumerate(uniq)}
    faces = np.empty((len(cy), 8), dtype=np.int64)
    for f, (i, j) in enumerate(zip(cy, cz)):
        for a, (oy, oz) in enumerate(QUAD8):
            faces[f, a] = pos[int(key(2 * i + oy, 2 * j + oz, 2 * layers))]
    return {"coords": coords, "hex": con, "faces": faces, "kx": kx, "n_cells": len(cy),
            "ny": len(ym), "nz": len(zm), "layers": layers, "dx": length / layers,
            "ys": ys, "zs": zs}


def write_mail(path, mesh, stations, per_face=False):
    """Code-Aster mesh file. Node and cell numbers are 1-based names."""
    hexa = mesh["hex"]
    faces = mesh["faces"]
    n_hex = len(hexa)
    with open(path, "w") as f:
        f.write("TITRE\n validation hex20 solid\nFINSF\n\nCOOR_3D\n")
        for i, (x, y, z) in enumerate(mesh["coords"]):
            f.write("N{0} {1:.15e} {2:.15e} {3:.15e}\n".format(i + 1, x, y, z))
        f.write("FINSF\n\nHEXA20\n")
        for i, row in enumerate(hexa):
            names = ["N{0}".format(n + 1) for n in row]
            f.write("M{0} {1}\n  {2}\n".format(i + 1, " ".join(names[:7]), " ".join(names[7:14])))
            f.write("  {0}\n".format(" ".join(names[14:])))
        f.write("FINSF\n\nQUAD8\n")
        for i, row in enumerate(faces):
            names = ["N{0}".format(n + 1) for n in row]
            f.write("M{0} {1}\n".format(n_hex + i + 1, " ".join(names)))
        f.write("FINSF\n\n")

        def group(kind, name, items, prefix):
            f.write("{0}\n {1}\n".format(kind, name))
            for s in range(0, len(items), 7):
                f.write(" " + " ".join(prefix + str(i + 1) for i in items[s:s + 7]) + "\n")
            f.write("FINSF\n\n")

        group("GROUP_MA", "LOADF", list(range(n_hex, n_hex + len(faces))), "M")
        if per_face:
            for i in range(len(faces)):
                group("GROUP_MA", "F{0}".format(i), [n_hex + i], "M")
        cells = mesh["n_cells"]
        for s, layer in enumerate(stations):
            group("GROUP_MA", "ST{0}".format(s), list(range(layer * cells, (layer + 1) * cells)), "M")
        group("GROUP_NO", "CLAMP", np.nonzero(mesh["kx"] == 0)[0].tolist(), "N")
        f.write("FIN\n")


COMM = r'''
DEBUT(LANG='EN')
import json
import numpy as np
P = json.load(open(r"@PARAMS@"))
mesh = LIRE_MAILLAGE(FORMAT='ASTER', UNITE=20)
mod = AFFE_MODELE(MAILLAGE=mesh, AFFE=_F(TOUT='OUI', PHENOMENE='MECANIQUE', MODELISATION='3D'))
mat = DEFI_MATERIAU(ELAS=_F(E=P['E'], NU=P['nu']))
cm = AFFE_MATERIAU(MAILLAGE=mesh, AFFE=_F(TOUT='OUI', MATER=mat))
if P['patch']:
    # minimal support: u_x = 0 on the face, plus three points against the rigid motions
    nd = P['patch_nodes']
    mesh = DEFI_GROUP(reuse=mesh, MAILLAGE=mesh, CREA_GROUP_NO=(
        _F(NOM='P1', OPTION='ENV_SPHERE', POINT=nd[0], RAYON=1e-9, PRECISION=1e-9),
        _F(NOM='P2', OPTION='ENV_SPHERE', POINT=nd[1], RAYON=1e-9, PRECISION=1e-9),))
    clamp = AFFE_CHAR_MECA(MODELE=mod, DDL_IMPO=(_F(GROUP_NO='CLAMP', DX=0.0),
                                                _F(GROUP_NO='P1', DY=0.0, DZ=0.0),
                                                _F(GROUP_NO='P2', DZ=0.0)))
else:
    clamp = AFFE_CHAR_MECA(MODELE=mod, DDL_IMPO=_F(GROUP_NO='CLAMP', DX=0.0, DY=0.0, DZ=0.0))
A, yg, zg, ip = P['A'], P['yg'], P['zg'], P['ip']
fac = P['T0'] / ip
fy = FORMULE(VALE='-fac*(Z-zg)', NOM_PARA=('X', 'Y', 'Z'), fac=fac, zg=zg)
fz = FORMULE(VALE='fac*(Y-yg)', NOM_PARA=('X', 'Y', 'Z'), fac=fac, yg=yg)
chN = AFFE_CHAR_MECA(MODELE=mod, FORCE_FACE=_F(GROUP_MA='LOADF', FX=P['N0'] / A))
chY = AFFE_CHAR_MECA(MODELE=mod, FORCE_FACE=_F(GROUP_MA='LOADF', FY=P['V0'] / A))
chZ = AFFE_CHAR_MECA(MODELE=mod, FORCE_FACE=_F(GROUP_MA='LOADF', FZ=P['V0'] / A))
chT = AFFE_CHAR_MECA_F(MODELE=mod, FORCE_FACE=_F(GROUP_MA='LOADF', FY=fy, FZ=fz))
ncase = 5 if P['sv'] else 4
times = DEFI_LIST_REEL(VALE=tuple(float(i + 1) for i in range(ncase)))


def pulse(i):
    v = [0.0, 0.0, 1.0, 0.0, 2.0, 0.0, 3.0, 0.0, 4.0, 0.0, 5.0, 0.0, 6.0, 0.0]
    v[2 * i + 1] = 1.0
    return DEFI_FONCTION(NOM_PARA='INST', VALE=v, PROL_DROITE='CONSTANT', PROL_GAUCHE='CONSTANT')


f1 = pulse(1)
f2 = pulse(2)
f3 = pulse(3)
f4 = pulse(4)
excit = [_F(CHARGE=clamp), _F(CHARGE=chN, FONC_MULT=f1), _F(CHARGE=chY, FONC_MULT=f2),
         _F(CHARGE=chZ, FONC_MULT=f3), _F(CHARGE=chT, FONC_MULT=f4)]
if P['sv']:
    f5 = pulse(5)
    tab = np.load(r"@SV@")
    chS = AFFE_CHAR_MECA(MODELE=mod, FORCE_FACE=[
        _F(GROUP_MA='F%d' % i, FY=float(tab['fy'][i]), FZ=float(tab['fz'][i]))
        for i in range(len(tab['fy']))])
    excit.append(_F(CHARGE=chS, FONC_MULT=f5))
res = MECA_STATIQUE(MODELE=mod, CHAM_MATER=cm, LIST_INST=times, OPTION='SANS',
                    SOLVEUR=_F(METHODE='MUMPS', RENUM='SCOTCH', MATR_DISTRIBUEE='NON',
                               GESTION_MEMOIRE='AUTO', NPREC=8, RESI_RELA=P['resi'],
                               POSTTRAITEMENTS='FORCE'),
                    EXCIT=excit)
grp = ['ST%d' % i for i in range(P['nst'])]
res = CALC_CHAMP(reuse=res, RESULTAT=res, GROUP_MA=grp, CONTRAINTE='SIEF_ELGA')

coor = CALC_CHAM_ELEM(MODELE=mod, OPTION='COOR_ELGA', GROUP_MA=grp)
out = {}
scoor = coor.toSimpleFieldOnCells()
xyzw = {}
for c in ('X', 'Y', 'Z', 'W'):
    v, (cells, _cmp, pts, sub) = scoor.getValuesWithDescription([c], grp)
    xyzw[c] = np.array(v)
    out['cells'], out['pts'] = np.array(cells), np.array(pts)
out.update({'g' + c: v for c, v in xyzw.items()})
for it in range(1, ncase + 1):
    fld = res.getField('SIEF_ELGA', it).toSimpleFieldOnCells()
    for c in ('SIXX', 'SIYY', 'SIZZ', 'SIXY', 'SIXZ', 'SIYZ'):
        v, (cells, _cmp, pts, sub) = fld.getValuesWithDescription([c], grp)
        cells, pts = np.array(cells), np.array(pts)
        if not (np.array_equal(cells, out['cells']) and np.array_equal(pts, out['pts'])):
            raise RuntimeError('order of the ELGA field differs')
        out['%s_%d' % (c, it)] = np.array(v)
    d = res.getField('DEPL', it).toSimpleFieldOnNodes()
    arr, msk = d.getValues()
    names = list(d.getComponents())
    out['depl_%d' % it] = np.array(arr)[:, [names.index(c) for c in ('DX', 'DY', 'DZ')]]
np.savez(r"@OUT@", **out)
FIN()
'''


def sv_traction(sec, mesh, nu, path):
    """Traction per end face: the tool's Saint-Venant field for Vw = V0 through
    the shear centre, averaged over the face, then corrected by a uniform and a
    linear traction so that the resultant is exact."""
    import compare
    p = sections.properties(sec)
    tool = compare.tool_for(sec, nu)
    e = tool.f.shear_centre[0]
    faces = mesh["faces"]
    corner = mesh["coords"][faces[:, 0]]
    far = mesh["coords"][faces[:, 2]]
    gl = np.array([-GL3, 0.0, GL3])
    gw = np.array([5.0, 8.0, 5.0]) / 18.0
    area = (far[:, 1] - corner[:, 1]) * (far[:, 2] - corner[:, 2])
    ym, zm = 0.5 * (far[:, 1] + corner[:, 1]), 0.5 * (far[:, 2] + corner[:, 2])
    fy, fz = np.zeros(len(faces)), np.zeros(len(faces))
    for a, wa in zip(gl, gw):
        for b, wb in zip(gl, gw):
            pts = np.column_stack([ym + 0.5 * a * (far[:, 1] - corner[:, 1]) - p["yg"],
                                   zm + 0.5 * b * (far[:, 2] - corner[:, 2]) - p["zg"]])
            v = tool.evaluate({"Vw": V0, "T": e * V0}, pts)
            fy += wa * wb * v["tau_v"]
            fz += wa * wb * v["tau_w"]
    y, z = ym - p["yg"], zm - p["zg"]
    res_y, res_z = V0 * 0.0 - area.dot(fy), V0 - area.dot(fz)
    tq = e * V0 - area.dot(y * fz - z * fy)
    fy += res_y / p["A"]
    fz += res_z / p["A"]
    tq2 = tq - res_z * (area.dot(y) / p["A"]) + res_y * (area.dot(z) / p["A"])
    ipd = area.dot(y * y + z * z)
    fy += -tq2 * z / ipd
    fz += tq2 * y / ipd
    np.savez(path, fy=fy, fz=fz)
    return {"Fy": float(area.dot(fy)), "Fz": float(area.dot(fz)),
            "T": float(area.dot(y * fz - z * fy)), "e": float(e)}


def run_aster(work, ncpus, mem, tlimit):
    export = ("P time_limit {0}\nP memory_limit {1}\nP ncpus {2}\nP mpi_nbcpu 1\n"
              "F comm job.comm D 1\nF libr job.mail D 20\nF mess job.mess R 6\n"
              "F resu job.resu R 8\n").format(tlimit, mem, ncpus)
    with open(os.path.join(work, "job.export"), "w") as f:
        f.write(export)
    with open(os.path.join(work, "run.log"), "w") as log:
        code = subprocess.call(["cmd.exe", "/c", ASTER, "job.export"], cwd=work, stdout=log,
                               stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("section")
    ap.add_argument("n_wall", type=int)
    ap.add_argument("--len-h", type=float, default=8.0, help="length in section heights")
    ap.add_argument("--len", type=float, default=None, help="length in metres")
    ap.add_argument("--layers-h", type=float, default=10.0, help="element layers per height")
    ap.add_argument("--nu", type=float, default=0.3)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--patch", action="store_true", help="minimal support, for the patch test")
    ap.add_argument("--sv", action="store_true", help="fifth case, Saint-Venant traction")
    ap.add_argument("--stride-h", type=float, default=0.5, help="station spacing in heights")
    ap.add_argument("--cos", action="store_true", help="refine long spans towards their ends")
    ap.add_argument("--resi", type=float, default=1e-6, help="SOLVEUR/RESI_RELA")
    ap.add_argument("--ncpus", type=int, default=8)
    ap.add_argument("--mem", type=int, default=16000)
    ap.add_argument("--tlimit", type=int, default=7200)
    a = ap.parse_args()
    sec = sections.ALL[a.section]()
    h = sec["h"]
    length = a.len if a.len else a.len_h * h
    layers = int(round(length / h * a.layers_h)) | 1      # odd: L/2 in the middle of a layer
    mid = layers // 2
    off = int(round(h / (length / layers)))
    stations = [mid, mid - off, mid + off]
    step = max(1, int(round(a.stride_h * h / (length / layers))))
    margin = int(round(0.5 * h / (length / layers)))
    for layer in range(mid % step, layers, step):
        if margin <= layer < layers - margin and layer not in stations:
            stations.append(layer)
    tag = a.tag or "{0}_n{1}".format(a.section, a.n_wall)
    work = os.path.join(_HERE, "work", tag)
    if not os.path.isdir(work):
        os.makedirs(work)
    mesh = build_mesh(sec, a.n_wall, length, layers, a.cos)
    write_mail(os.path.join(work, "job.mail"), mesh, stations, per_face=a.sv)
    p = sections.properties(sec)
    params = dict(p, E=E_MOD, nu=a.nu, N0=N0, V0=V0, T0=T0, nst=len(stations), patch=a.patch, sv=a.sv, resi=a.resi)
    if a.patch:
        rect = sec["rects"][0]
        params["patch_nodes"] = [[0.0, rect[0], rect[1]], [0.0, rect[2], rect[1]]]
    with open(os.path.join(work, "params.json"), "w") as f:
        json.dump(params, f)
    out = os.path.join(work, "result.npz")
    sv_path = os.path.join(work, "sv.npz")
    comm = (COMM.replace("@PARAMS@", os.path.join(work, "params.json")).replace("@OUT@", out)
            .replace("@SV@", sv_path))
    with open(os.path.join(work, "job.comm"), "w") as f:
        f.write(comm)
    sv_check = sv_traction(sec, mesh, a.nu, sv_path) if a.sv else None
    info = dict(sv=sv_check, cosine=a.cos, section=a.section, n_wall=a.n_wall, length=length, layers=layers, dx=mesh["dx"],
                stations=stations, x=[(s + 0.5) * mesh["dx"] for s in stations],
                h=h, nu=a.nu, n_nodes=len(mesh["coords"]), n_hex=len(mesh["hex"]),
                n_cells=mesh["n_cells"], ny=mesh["ny"], nz=mesh["nz"], dof=3 * len(mesh["coords"]),
                N0=N0, V0=V0, T0=T0, E=E_MOD)
    with open(os.path.join(work, "info.json"), "w") as f:
        json.dump(info, f, indent=1)
    np.savez(os.path.join(work, "nodes.npz"), coords=mesh["coords"], kx=mesh["kx"])
    print(info, flush=True)
    code = run_aster(work, a.ncpus, a.mem, a.tlimit)
    print("exit", code, "result", os.path.exists(out), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
