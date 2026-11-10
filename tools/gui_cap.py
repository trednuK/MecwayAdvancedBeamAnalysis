# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# gui_cap.py
# The window as a PNG, without Mecway and without showing anything: the form is
# never shown (no Show, no Application.Run), it gets its handles and is drawn
# with DrawToBitmap. A fake mw and solution over an own small model of a
# cantilever with a channel section; the worker is the real one.
#
#   powershell -ExecutionPolicy Bypass -File tools/run_ipy.ps1 \
#       -Script tools/gui_cap.py -ScriptArgs <repo>,<out.png>,nl,1.25
#
# The images of the documentation, with numbered markers drawn at the place of the
# real controls, in Dutch and English:
#
#   powershell -ExecutionPolicy Bypass -File tools/run_ipy.ps1 \
#       -Script tools/gui_cap.py -ScriptArgs <repo>,doc,<folder>
#
# tests/test_beam_gui.py uses the fakes and the harness of this file.
import io
import os
import shutil
import sys
import tempfile
import time

MODEL = u"""<liml version="33">
  <analysis type="S30" />
  {nodes}
  <elset name="Beam" material="U">
    {elements}
  </elset>
  <mat name="U">
    <geometric type="C" ca="60 mm" cb="160 mm" cc="8 mm" cd="6 mm" pointy="0 m" pointz="0 m" />
    <mechanical type="Isotropic" youngsmodulus="210 GPa" poissonratio="0.3" />
  </mat>
  <nodeselection name="Tip"><node nid="{tip}" /></nodeselection>
  <elementselection name="Beam">{beam}</elementselection>
  <elementselection name="Half">{half}</elementselection>
  <faceselection name="Beam faces">{faces}</faceselection>
</liml>
"""


def write_model(path, n=10, length=2.0):
    nodes = u"".join(u'<node nid="{0}" x="{1:.6f}" y="0" z="0" />'.format(
        i + 1, length * i / n) for i in range(n + 1))
    elements = u"".join(u'<elem eid="{0}" shape="line2" nodes="{0} {1}" />'.format(
        i + 1, i + 2) for i in range(n))
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(MODEL.format(
            nodes=nodes, elements=elements, tip=n + 1,
            beam=u"".join(u'<element eid="{0}" />'.format(i + 1) for i in range(n)),
            half=u"".join(u'<element eid="{0}" />'.format(i + 1) for i in range(n // 2)),
            faces=u"".join(u'<face eid="{0}" faceid="1" />'.format(i + 1) for i in range(n))))


class FaceId(object):
    def __init__(self, element_id, face_number=1):
        self.element_id = element_id
        self.face_number = face_number


class FakeMw(object):
    """The calls of beam_mecway on an own model, with the rules measured on Mecway
    35 (bridge_section_model.py of MecwayAsterBridge): numbers 1..n, renumbered
    after every delete; delete_nodes takes the elements along; material_type raises
    on a missing material and gives 'None' for an unset group; material() gives
    None on a component without one; a name in use gets (2)."""

    def __init__(self, path, n=10, length=2.0):
        self.path = path
        self.n = n
        self.length = length
        self.selection = []
        self.extra_nodes = 0
        self.configuration = None
        self.points = [(length * i / n, 0.0, 0.0) for i in range(n + 1)]
        self.elems = [{"shape": "line2", "nodes": [i + 1, i + 2], "comp": "Beam"}
                      for i in range(n)]
        self.comps = {"Beam": "U"}
        self.mats = {"U": {"types": {"geometric": "C"}, "props": {}}}
        self.displays = 0
        self.view = "solution"
        self.fail_element = False
        # named selections made after the last save: name -> items, not in the file
        self.unsaved = {}

    def file_name(self):
        return self.path

    def named_selections(self):
        return sorted(self.unsaved) + [u"Beam", u"Beam faces", u"Half", u"Tip"]

    def _beam(self):
        return [i + 1 for i, e in enumerate(self.elems) if e["comp"] == "Beam"]

    def named_selection(self, name):
        if name in self.unsaved:
            return list(self.unsaved[name])
        beam = self._beam()
        if name == u"Beam faces":
            return [FaceId(i) for i in beam]
        if name == u"Half":
            return beam[:len(beam) // 2]
        if name == u"Tip":
            return [self.n + 1]
        return beam

    def selected_elements(self):
        return list(self.selection)

    def selected_faces(self):
        return []

    def element_shape(self, eid):
        if not 1 <= eid <= len(self.elems):
            raise ValueError(eid)
        return self.elems[eid - 1]["shape"]

    def nodes(self, eid):
        return list(self.elems[eid - 1]["nodes"])

    def node(self, nid):
        return self.points[nid - 1]

    def all_nodes(self):
        return list(range(1, len(self.points) + 1 + self.extra_nodes))

    def all_elements(self):
        return list(range(1, len(self.elems) + 1))

    def set_active_configuration(self, name):
        self.configuration = name

    # the part the marker uses
    def components(self):
        return list(self.comps)

    def component_elements(self, name):
        return [i + 1 for i, e in enumerate(self.elems) if e["comp"] == name]

    def material(self, comp):
        return self.comps[comp]

    def material_type(self, name, group):
        return self.mats[name]["types"].get(group, "None")

    def material_property(self, name, prop):
        return self.mats[name]["props"][prop]

    def _free(self, name, taken):
        k, out = 1, name
        while out in taken:
            k += 1
            out = u"{0}({1})".format(name, k)
        return out

    def new_material(self, name):
        name = self._free(name, self.mats)
        self.mats[name] = {"types": {}, "props": {}}
        return name

    def set_material_type(self, material, group, value):
        self.mats[material]["types"][group] = value

    def set_material_property(self, material, prop, value):
        self.mats[material]["props"][prop] = value

    def new_component(self, name):
        name = self._free(name, self.comps)
        self.comps[name] = None
        return name

    def set_material(self, comp, material):
        self.comps[comp] = material

    def new_node(self, position):
        self.points.append(tuple(position))
        return len(self.points)

    def new_element(self, shape, nodes, comp):
        if self.fail_element:
            raise RuntimeError("no element")
        self.elems.append({"shape": shape, "nodes": list(nodes), "comp": comp})
        return len(self.elems)

    def delete_elements(self, ids):
        gone = set(ids)
        self.elems = [e for i, e in enumerate(self.elems) if i + 1 not in gone]

    def delete_nodes(self, ids):
        gone = set(ids)
        self.elems = [e for e in self.elems if not gone & set(e["nodes"])]
        order = [i + 1 for i in range(len(self.points)) if i + 1 not in gone]
        new = dict((old, k + 1) for k, old in enumerate(order))
        self.points = [self.points[i - 1] for i in order]
        for e in self.elems:
            e["nodes"] = [new[n] for n in e["nodes"]]

    def delete_component(self, name):
        self.comps.pop(name, None)

    def delete_material(self, name):
        del self.mats[name]

    def set_node_x(self, nid, value):
        p = self.points[nid - 1]
        self.points[nid - 1] = (value, p[1], p[2])

    def set_node_y(self, nid, value):
        p = self.points[nid - 1]
        self.points[nid - 1] = (p[0], value, p[2])

    def set_node_z(self, nid, value):
        p = self.points[nid - 1]
        self.points[nid - 1] = (p[0], p[1], value)

    def set_model_active(self):
        self.view = "model"

    def set_solution_active(self):
        self.view = "solution"

    def display(self):
        self.displays += 1

    def trace(self):
        """What the marker could leave behind."""
        return (len(self.points), len(self.elems), sorted(self.comps), sorted(self.mats))


class FakeSolution(object):
    """A cantilever, root at x = 0, with a tip force and torque. mode: "element",
    "node" (a solver without element values), "kinematics" (no forces stored) or
    "nothing"."""

    def __init__(self, mw, mode="element", force=2000.0):
        self.mw = mw
        self.mode = mode
        self.force = force
        self.steps = []
        self.mode_list = []

    def time_steps(self):
        return self.steps

    def time(self, step):
        return 0.5 * step

    def modes(self):
        return self.mode_list

    def frequency(self, mode):
        return 12.5 * mode

    def _value(self, var, x, scale):
        f = self.force * scale
        length = self.mw.length
        return {"tensileforce": 5000.0 * scale, "sheary": f, "shearz": 0.3 * f,
                "torsion": 20.0 * scale, "bendingmomw": f * (length - x),
                "bendingmomv": -0.3 * f * (length - x)}.get(var)

    def element_node_value(self, var, eid, k, time_step=None, mode=None):
        if self.mode != "element":
            raise ValueError("no such variable")
        if k < 1 or k > 2:
            raise IndexError(k)
        h = self.mw.length / self.mw.n
        scale = 1.0 + 0.1 * (time_step or 0) + 0.2 * (mode or 0)
        return self._value(var, h * (eid - 1 + (k - 1)), scale)

    def node_value(self, var, nid, time_step=None, mode=None):
        x = self.mw.length * (nid - 1) / self.mw.n
        if self.mode == "node":
            return self._value(var, x, 1.0)
        if self.mode == "kinematics":
            izz = 1.0e-5
            e = 210.0e9
            length = self.mw.length
            value = {"displx": 0.0, "displz": 0.0, "rotx": 0.0, "roty": 0.0,
                     "disply": self.force * x * x * (3 * length - x) / (6 * e * izz),
                     "rotz": self.force * x * (2 * length - x) / (2 * e * izz)}
            return value.get(var)
        return None


class Harness(object):
    """A window that is never shown, with its fakes, in a temp folder."""

    def __init__(self, repo, scale=1.0, mode="element", size=None, caller=None,
                 worker=None):
        sys.path.insert(0, os.path.join(repo, "Scripts"))
        import beam_gui as gui
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        import System.Drawing as D
        import System.Windows.Forms as F
        self.gui, self.D, self.F = gui, D, F
        self.folder = tempfile.mkdtemp(prefix="beam_gui_")
        self.path = os.path.join(self.folder, u"cantilever.liml")
        write_model(self.path)
        self.mw = FakeMw(self.path)
        self.mw.selection = [1, 2, 3]
        self.solution = FakeSolution(self.mw, mode)
        self.caller = caller or gui._MwCaller(True)
        self.window = gui._Window(self.mw, self.solution, self.caller, scale=scale,
                                  worker=worker, vector=lambda x, y, z: (x, y, z))
        form = self.window.form
        form.StartPosition = F.FormStartPosition.Manual
        form.Location = D.Point(-8000, -8000)
        if size:
            form.ClientSize = D.Size(size[0], size[1])
        self.handles(form)

    def handles(self, control):
        """Real window handles, without showing anything."""
        h = control.Handle
        for child in control.Controls:
            self.handles(child)
        return h

    def pump(self, condition, seconds=20.0):
        """Run events until condition() holds; False on a timeout."""
        from System.Threading import Thread
        from System.Windows.Forms import Application
        end = time.time() + seconds
        while time.time() < end:
            Application.DoEvents()
            if isinstance(self.caller, self.gui._MwCaller) and not self.caller.direct:
                self.caller.serve_once()
            if condition():
                return True
            Thread.CurrentThread.Join(2)
        return False

    def idle(self, frames=1):
        """Wait until the worker has nothing left to do."""
        w = self.window
        return self.pump(lambda: w.link.slot.empty() and not w.tickets and
                         w.painted == w.frame_no and
                         not (w.last or {}).get("coarse"))

    def open_doc(self, seconds=20.0):
        """The tab with the manual, until the manual stands on it."""
        w = self.window
        w.tabs.SelectedIndex = 1
        self.handles(w.page_doc)
        ok = self.pump(lambda: w.doc_ready() and not w.doc_loading, seconds)
        # the browser element paints a moment after the page is complete (measured:
        # a capture straight after it was blank)
        self.pump(lambda: False, 0.8)
        return ok

    def capture(self, path):
        from System.Drawing.Imaging import ImageFormat, PixelFormat
        D = self.D
        form = self.window.form
        self.F.Application.DoEvents()
        bmp = D.Bitmap(form.Width, form.Height, PixelFormat.Format32bppArgb)
        form.DrawToBitmap(bmp, D.Rectangle(0, 0, form.Width, form.Height))
        viewer = self.window.doc_viewer
        if self.window._doc_shown() and self.window._is_browser():
            # the form leaves the browser element blank; it draws itself (measured)
            page = D.Bitmap(viewer.Width, viewer.Height, PixelFormat.Format32bppArgb)
            viewer.DrawToBitmap(page, D.Rectangle(0, 0, viewer.Width, viewer.Height))
            # its place through the tab page: PointToScreen of the browser element
            # leaves out the offscreen position of the form (measured)
            at = self.window.page_doc.PointToScreen(viewer.Location)
            g = D.Graphics.FromImage(bmp)
            g.DrawImage(page, at.X - form.Left, at.Y - form.Top, viewer.Width, viewer.Height)
            g.Dispose()
            page.Dispose()
        # DrawToBitmap prints siblings in reverse z-order, so the tab control covers
        # the language buttons that stand over it; on the screen they are on top
        g = D.Graphics.FromImage(bmp)
        for control in self.window.lang_buttons.values():
            part = D.Bitmap(control.Width, control.Height, PixelFormat.Format32bppArgb)
            control.DrawToBitmap(part, D.Rectangle(0, 0, control.Width, control.Height))
            at = control.PointToScreen(D.Point(0, 0))
            g.DrawImage(part, at.X - form.Left, at.Y - form.Top, control.Width, control.Height)
            part.Dispose()
        g.Dispose()
        if path:
            bmp.Save(path, ImageFormat.Png)
        return bmp

    def close(self):
        w = self.window
        w._on_closing(None, None)
        w.shutdown()
        self.gui.after_window(self.mw, w)
        w.form.Dispose()
        shutil.rmtree(self.folder, ignore_errors=True)


# ---------------------------------------------------------------- documentation
MARGIN = 30
DISC = 20
MARK_COLOUR = (196, 60, 0)
# The example section of MODEL: shear centre in V, W about the centroid (metres)
SHEAR_CENTRE = (0.0, 0.03533)


class Marks(object):
    """Numbered discs on a capture. The form is drawn between two white margins and
    each disc goes where the real control is, so the numbers follow the layout."""

    def __init__(self, harness):
        self.h = harness
        self.D = harness.D
        self.items = []

    def corner(self, control):
        """Top left of a control in the final image."""
        form = self.h.window.form
        p = control.PointToScreen(self.D.Point(0, 0))
        return p.X - form.Left + MARGIN, p.Y - form.Top

    def left(self, number, control):
        """In the left margin, level with a control."""
        x, y = self.corner(control)
        y += control.Height // 2
        self.items.append((number, (MARGIN // 2, y), (x, y)))

    def right(self, number, control, text=None):
        """In the right margin, level with a control; the line ends at the text."""
        x, y = self.corner(control)
        end = x + control.Width
        if text:
            g = self.h.window.canvas.CreateGraphics()
            end = x + self.h.window.px(10) + int(g.MeasureString(text, control.Font).Width)
            g.Dispose()
        y += control.Height // 2
        width = self.h.window.form.Width
        self.items.append((number, (width + MARGIN + MARGIN // 2, y), (end, y)))

    def beside(self, number, control, dx=0, dy=0):
        """On the top right corner of a control, moved by dx, dy."""
        x, y = self.corner(control)
        self.items.append((number, (x + control.Width + dx, y + dy), None))

    def at(self, number, control, x, y, dx, dy):
        """Next to pixel x, y of a control, with a line to that pixel."""
        cx, cy = self.corner(control)
        self.items.append((number, (cx + int(x) + dx, cy + int(y) + dy),
                           (cx + int(x), cy + int(y))))

    def picture_at(self, number, v, w, dx, dy):
        """Next to the point v, w of the single section on the canvas."""
        view = self.h.window.last["view"]
        self.at(number, self.h.window.canvas, (v - view["x0"]) * view["s"],
                (view["y1"] - w) * view["s"], dx, dy)

    def save(self, path, controls):
        from System.Drawing.Drawing2D import SmoothingMode
        from System.Drawing.Imaging import ImageFormat, PixelFormat
        D = self.D
        shot = self.h.capture(None)
        out = D.Bitmap(shot.Width + 2 * MARGIN, shot.Height, PixelFormat.Format32bppArgb)
        g = D.Graphics.FromImage(out)
        g.Clear(D.Color.White)
        g.DrawImage(shot, MARGIN, 0, shot.Width, shot.Height)
        g.SmoothingMode = SmoothingMode.AntiAlias
        # The text of these controls is in a native part that DrawToBitmap leaves out
        # on a form that was never shown; the strings are the controls' own.
        for control in controls:
            x, y = self.corner(control)
            brush = D.Brushes.Black if control.Enabled else D.Brushes.Gray
            g.DrawString(control.Text, control.Font, brush, float(x + 3), float(y + 3))
        colour = D.Color.FromArgb(*MARK_COLOUR)
        pen = D.Pen(colour, 1.5)
        brush = D.SolidBrush(colour)
        ring = D.Pen(D.Color.White, 1.5)
        font = D.Font("Segoe UI", 8.5, D.FontStyle.Bold)
        centre = D.StringFormat()
        centre.Alignment = D.StringAlignment.Center
        centre.LineAlignment = D.StringAlignment.Center
        for number, spot, target in self.items:
            if target:
                g.DrawLine(pen, spot[0], spot[1], target[0], target[1])
                g.FillEllipse(brush, target[0] - 2, target[1] - 2, 4, 4)
        for number, spot, target in self.items:
            r = D.Rectangle(spot[0] - DISC // 2, spot[1] - DISC // 2, DISC, DISC)
            g.FillEllipse(brush, r)
            g.DrawEllipse(ring, r)
            g.DrawString(str(number), font, D.Brushes.White, D.RectangleF(
                float(r.X), float(r.Y) + 0.5, float(DISC), float(DISC)), centre)
        g.Dispose()
        out.Save(path, ImageFormat.Png)


def _one_section(h, path):
    w = h.window
    # a position where the peak in the corner rises above the maximum outside it
    w.slider.Value = 400
    w._on_slider()
    h.idle()
    last = w.last
    view = last["view"]
    # hover over the web
    w.on_canvas_move(int((0.04 - view["x0"]) * view["s"]),
                     int((view["y1"] - 0.0142) * view["s"]), False)
    h.pump(lambda: w.hover_label.Text != u"", 10.0)
    m = Marks(h)
    for number, control in ((1, w.source_box), (2, w.refresh_button), (4, w.result_box),
                            (5, w.solver_label), (6, w.measure_list),
                            (7, w.force_checks["N"]), (8, w.shear_check), (9, w.arrow_check),
                            (10, w.fixed_check), (11, w.sheet_check), (12, w.marker_check),
                            (13, w.warn_box), (14, w.warping_label),
                            (31, w.contour_check), (32, w.write_button)):
        m.left(number, control)
    m.right(3, w.lang_buttons["en"])
    m.picture_at(15, -0.03, 0.0142, 0, -46)
    m.picture_at(16, 0.0, 0.0, 34, 22)
    m.picture_at(17, SHEAR_CENTRE[0], SHEAR_CENTRE[1], 34, -14)
    m.picture_at(18, 0.0755, -0.012, -44, 16)
    m.picture_at(19, last["outside"]["point"][0], last["outside"]["point"][1], 34, 8)
    m.picture_at(20, last["peak"]["point"][0], last["peak"]["point"][1], 34, 34)
    m.at(21, w.canvas, w.px(30), w.canvas.Height - w.px(36), w.px(48), -w.px(20))
    m.at(22, w.legend, w.px(11), w.px(120), w.px(90), -w.px(30))
    m.at(23, w.legend, w.px(120), w.legend.Height - w.px(112), w.px(14), -w.px(20))
    m.right(24, w.hover_label, w.hover_label.Text)
    m.right(25, w.side)
    m.right(26, w.slider)
    m.right(27, w.place_label, w.place_label.Text)
    m.right(28, w.forces_label, w.forces_label.Text)
    m.right(29, w.torque_label, w.torque_label.Text)
    m.right(30, w.status_label, w.status_label.Text)
    m.save(path, [w.source_box, w.result_box, w.sheet_count])


def _several_sections(h, path):
    w = h.window
    w.sheet_check.Checked = True
    h.idle()
    w.on_canvas_leave()
    tiles = w.canvas.tiles
    m = Marks(h)
    m.left(1, w.sheet_check)
    first = tiles[0]
    m.at(2, w.canvas, first["x"] + w.px(120), first["y"] + w.px(10), w.px(46), w.px(6))
    # the marks of a tile, in the corner and at the end of a flange of this section
    m.at(3, w.canvas, tiles[2]["x"] + w.px(25), tiles[2]["y"] + w.px(80), w.px(56), w.px(30))
    m.at(4, w.canvas, tiles[4]["x"] + w.px(25), tiles[4]["y"] + w.px(177), w.px(56), -w.px(20))
    m.at(5, w.legend, w.px(11), w.px(120), w.px(90), -w.px(30))
    m.right(6, w.side)
    m.right(7, w.place_label, w.place_label.Text)
    # the row above the tiles: to the right of 3D, beside the head of the first tile
    m.at(8, w.tools, w.radio_3d.Right, w.radio_3d.Top + w.radio_3d.Height // 2,
         w.px(30), 0)
    m.right(9, w.torque_label, w.torque_label.Text)
    m.save(path, [w.source_box, w.result_box, w.sheet_count])


def _three_d(h, path):
    """Several sections in 3D, turned a little, with its controls. Magnified about
    4x by the slider: at the automatic 1.8x of this short beam the walls are only a
    few pixels wide and their colours do not show."""
    w = h.window
    w.radio_3d.Checked = True
    h.idle()
    w.magnify_slider.Value = 13
    h.idle()
    w.on_canvas_press(300, 200, "left")
    w.on_canvas_move(300 - w.px(40), 200 - w.px(15), "left")
    w.on_canvas_release()
    h.idle()
    w.on_canvas_leave()
    # hover over a wall of section 4 (the one the detail opens): from its number
    # outwards, left, right, up and down, until the probe gives values
    tiles = [t for t in w.last["tiles"] if t.get("anchor")]
    ax, ay = tiles[3]["anchor"]
    hover = None
    steps = [(dx * d, dy * d) for d in range(2, 160, 2)
             for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
    for dx, dy in steps:
        w.hover_label.Text = u""
        w.on_canvas_move(int(ax + dx), int(ay + dy), None)
        h.pump(lambda: w.hover_label.Text != u"", 1.0)
        if u"V =" in w.hover_label.Text:
            hover = (int(ax + dx), int(ay + dy))
            break
    if hover is None:
        w.hover_label.Text = u""
        print("3D image: no point with values found for the hover")
    m = Marks(h)
    m.at(1, w.tools, w.radio_3d.Left + w.radio_3d.Width // 2, w.radio_3d.Bottom,
         0, w.px(26))
    m.at(2, w.tools, w.fit_button.Left + w.fit_button.Width // 2, w.fit_button.Bottom,
         0, w.px(26))
    m.at(3, w.tools, w.magnify_slider.Left + w.magnify_slider.Width // 2,
         w.magnify_slider.Bottom, 0, w.px(26))
    tile = tiles[2]
    m.at(4, w.canvas, tile["anchor"][0], tile["anchor"][1] - w.px(8), w.px(30), -w.px(40))
    axes = w.last.get("axes") or {}
    if axes.get("O"):
        m.at(5, w.canvas, axes["O"][0], axes["O"][1], w.px(50), w.px(10))
    h.capture(None).Dispose()       # draws the texts over the picture: their places
    for text, left, top, width, height in w.canvas_labels:
        if u"×" in text:
            m.at(6, w.canvas, left, top + height // 2, -w.px(30), -w.px(24))
    if hover is not None:
        # down into the open side of the channel, clear of the next section
        m.at(7, w.canvas, hover[0], hover[1], -w.px(20), w.px(70))
        m.right(8, w.hover_label, w.hover_label.Text)
    m.right(9, w.torque_label, w.torque_label.Text)
    m.save(path, [w.source_box, w.result_box, w.sheet_count])


def _detail(h, path):
    """The detail of one of the sections in 3D, with Back."""
    w = h.window
    tile = [t for t in w.last["tiles"] if t.get("anchor")][3]
    w.on_canvas_double(int(tile["anchor"][0]), int(tile["anchor"][1]))
    h.pump(lambda: w.detail is not None, 5.0)
    h.idle()
    m = Marks(h)
    for number, control in ((1, w.detail_title), (2, w.shared_check)):
        m.at(number, w.tools, control.Left + w.px(12), control.Bottom, 0, w.px(26))
    # in the right margin: below the button stands the title of the colour scale
    m.right(3, w.back_button)
    m.save(path, [w.source_box, w.result_box, w.sheet_count])
    w.on_back()
    h.idle()


def client_rect(element):
    """(left, top, width, height) of an element of the manual in the pixels of the
    browser element."""
    try:
        r = element.DomElement.getBoundingClientRect()
        return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)
    except Exception:
        o = element.OffsetRectangle
        return o.X, o.Y, o.Width, o.Height


def _manual(h, path):
    """The tab with the manual, at its top: the tabs, the language buttons and the
    contents line with its links."""
    w = h.window
    ok = h.open_doc()
    viewer = w.doc_viewer
    # the last link of the contents line: the tenth to a heading
    links = [a for a in viewer.Document.GetElementsByTagName("a")
             if u"#h-" in u"{0}".format(a.GetAttribute("href"))]
    m = Marks(h)
    # the tabs: 1 in the left margin, 2 in the empty tab row beside its tab
    x0, y0 = m.corner(w.tabs)
    first, second = w.tabs.GetTabRect(0), w.tabs.GetTabRect(1)
    mid = y0 + first.Y + first.Height // 2
    m.items.append((1, (MARGIN // 2, mid), (x0 + first.X + 4, mid)))
    m.items.append((2, (x0 + second.Right + w.px(26), mid), (x0 + second.Right, mid)))
    m.right(3, w.lang_buttons["en"])
    if len(links) >= 10:
        x, y, lw, lh = client_rect(links[9])
        # through the tab page, as in Harness.capture
        m.at(4, w.page_doc, viewer.Left + x + lw, viewer.Top + y + lh // 2, w.px(24), 0)
    m.save(path, [])
    w.tabs.SelectedIndex = 0
    h.pump(lambda: True, 0.05)
    return ok


def doc_images(repo, folder):
    sys.path.insert(0, os.path.join(repo, "Scripts"))
    import beam_utils as bu
    # choices made for the images (2D or 3D) are not kept as the user's settings
    bu.save_setting = lambda key, value: bu.settings().__setitem__(key, value)
    for lang in ("nl", "en"):
        # the images do not follow the choices of whoever makes them
        bu.settings().update({"language": lang, "contours": False, "sheet_view": "2d"})
        h = Harness(repo)
        h.mw.selection = []
        try:
            h.window.start()
            ok = h.pump(lambda: h.window.painted > 0, 30.0)
            _one_section(h, os.path.join(folder, "window_one_{0}.png".format(lang)))
            _several_sections(h, os.path.join(folder, "window_several_{0}.png".format(lang)))
            _three_d(h, os.path.join(folder, "window_3d_{0}.png".format(lang)))
            _detail(h, os.path.join(folder, "window_detail_{0}.png".format(lang)))
            manual = _manual(h, os.path.join(folder, "window_manual_{0}.png".format(lang)))
            print(u"{0}: frame {1}, manual {2}".format(lang, ok, manual).encode("ascii", "replace"))
        finally:
            h.close()


def main():
    repo, out = sys.argv[1], sys.argv[2]
    if out == "doc":
        doc_images(repo, sys.argv[3])
        return
    lang = sys.argv[3] if len(sys.argv) > 3 else "en"
    scale = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
    sys.path.insert(0, os.path.join(repo, "Scripts"))
    import beam_utils as bu
    bu.settings()["language"] = lang
    h = Harness(repo, scale)
    h.mw.selection = []
    try:
        h.window.start()
        ok = h.pump(lambda: h.window.painted > 0, 30.0)
        h.window.slider.Value = 300
        h.window._on_slider()
        ok = ok and h.idle()
        h.capture(out)
        print(u"saved {0}; frame {1}; status: {2}".format(
            out, ok, h.window.status_label.Text).encode("ascii", "replace"))
    finally:
        h.close()


if sys.argv and os.path.basename(sys.argv[0]) == "gui_cap.py":
    main()
