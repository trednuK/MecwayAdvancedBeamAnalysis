# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of the window (Scripts/beam_gui.py, beam_mecway.py) in Mecway's IronPython,
# without Mecway: fake mw and solution, the real worker, a form that is never
# shown (tools/gui_cap.py). What needs a real window on the screen is not tested
# here: focus, tearing, the true DPI of a display, and the speed inside Mecway.
#
#   python tests/test_beam_gui.py [folder for the PNGs]
#
# Under CPython it reruns itself through tools/run_ipy.ps1.

from __future__ import print_function

import io
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(globals().get("__file__", sys.argv[0])))
REPO = os.path.abspath(os.path.join(_HERE, ".."))
if sys.platform != "cli":
    sys.exit(subprocess.call(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File",
         os.path.join(REPO, "tools", "run_ipy.ps1"), "-Script", os.path.abspath(sys.argv[0]),
         "-ScriptArgs", ",".join(sys.argv[1:] or [""])]))

sys.path.insert(0, os.path.join(REPO, "Scripts"))
sys.path.insert(0, os.path.join(REPO, "tools"))
import beam_gui as gui  # noqa: E402
import beam_mecway as bmw  # noqa: E402
import beam_utils as bu  # noqa: E402
import gui_cap  # noqa: E402

FOLDER = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
FAILED = []
# The settings of this test live in memory: it neither reads what the user chose nor
# writes to WorkDir/beam_settings.json.
bu._CACHE["settings"] = dict(bu._DEFAULTS, language="en")


def _in_memory(key, value):
    bu.settings()[key] = value


bu.save_setting = _in_memory


def check(name, ok, detail=""):
    print(u"{0} {1}{2}".format("OK  " if ok else "FAIL", name,
                               u" ({0})".format(str(detail)) if str(detail) != "" else "")
          .encode("ascii", "replace"))
    if not ok:
        FAILED.append(name)


def near(a, b, tol):
    return abs(a - b) <= tol * max(abs(b), 1e-300)


def png(h, name):
    if FOLDER:
        h.capture(os.path.join(FOLDER, name))


def opened(h):
    return h.pump(lambda: h.window.opened and h.window.painted > 0, 30.0)


# ------------------------------------------------------------------- the basics
h = gui_cap.Harness(REPO)
w = h.window
check("the form is not visible", not w.form.Visible)
check("the title has the version", w.form.Text == gui.PANEL_TITLE == u"{0} {1}".format(
    bu.TOOL, bu.VERSION), w.form.Text)
t0 = time.time()
w.start()
check("start() does not wait for the worker", time.time() - t0 < 0.5, time.time() - t0)
check("the window says Python is starting", h.pump(lambda: "Python" in w.status_label.Text, 15.0),
      w.status_label.Text)
check("a picture arrives", opened(h))
check("still not visible", not w.form.Visible)
size = w.canvas.ClientSize
check("the picture has the size of the canvas", w.canvas.dib[1:] == (size.Width, size.Height),
      (w.canvas.dib[1:], size.Width, size.Height))
check("the first choice: the current selection", w.source_box.SelectedIndex == 0 and
      w.source_box.Items.Count == 4, w.source_box.Items.Count)
check("the combo shows it", u"Current selection" in w.source_box.Text, w.source_box.Text)
check("node selection left out by the file",
      u"Tip" not in [s["name"] for s in w.source_items], [s["name"] for s in w.source_items])
check("face selection listed through element_id",
      [s for s in w.source_items if s["name"] == u"Beam faces"][0]["faces"])
check("result set", w.result_box.Items.Count == 1 and u"Active" in w.result_box.Text, w.result_box.Text)
check("solver shown", u"Mecway internal" in w.solver_label.Text, w.solver_label.Text)
check("eight measures", w.measure_list.Items.Count == 8)
check("warping note", u"B = 0" in w.warping_label.Text)
check("warnings as a readable list", u"Eccentric shear centre" in w.warn_box.Text and
      w.warn_box.Text.startswith(u"\u2022"), w.warn_box.Text[:60])

# whole beam
w.source_box.SelectedIndex = 1
w._apply()
check("another beam: new length", h.pump(lambda: w.chain and near(w.chain["length"], 2.0, 1e-9)))
check("side view has all nodes", h.idle() and len(w.chain["side"]["points"]) == 11)
bmp = h.capture(None)
canvas_at = w.canvas.PointToScreen(h.D.Point(0, 0))
form_at = w.form.PointToScreen(h.D.Point(0, 0))
dx = canvas_at.X - w.form.Location.X
dy = canvas_at.Y - w.form.Location.Y
colours = set()
for i in range(0, size.Width, 7):
    for j in range(0, size.Height, 7):
        c = bmp.GetPixel(dx + i, dy + j)
        colours.add((c.R, c.G, c.B))
check("SetDIBitsToDevice reaches a bitmap: more than a blank canvas", len(colours) > 10, len(colours))
check("the DIB is at the place of the canvas, background in the corner",
      bmp.GetPixel(dx + size.Width - 3, dy + 3).ToArgb() == -1, bmp.GetPixel(dx + size.Width - 3, dy + 3))
bmp.Dispose()
png(h, "window_en_100.png")

# ---------------------------------------------------------------------- the slider
w.slider.Value = 500
w._on_slider()
check("slider: position", h.pump(lambda: w.last and near(w.last["s"], 1.0, 1e-9)), w.last and w.last["s"])
check("position line", u"1000" in w.place_label.Text and u"element 6" in w.place_label.Text, w.place_label.Text)
check("six forces in one line", all(n + u" = " in w.forces_label.Text for n in bu.FORCES), w.forces_label.Text)
check("forces at the place", near(w.last["forces"]["Mw"], 1900.0, 0.2), w.last["forces"]["Mw"])
t0 = time.time()
for i in range(240):
    w.slider.Value = i * 4
    w._on_slider()
    h.F.Application.DoEvents()
spent = time.time() - t0
final = 1000
w.slider.Value = final
w._on_slider()
check("a flood of slider steps does not stop the window", spent < 3.0, spent)
check("the last step is drawn", h.idle() and near(w.last["s"], 2.0, 1e-9), w.last["s"])
w.times = []
for i in range(200):
    w.slider.Value = 400 + i
    w._on_slider()
    h.pump(lambda: w.painted == w.frame_no, 5.0)
warm = [t[0] for t in w.times[30:]]
print(u"speed offscreen, panel side: {0} frames, round trip median {1:.1f} ms, p95 {2:.1f} ms".format(
    len(warm), gui._median(warm), gui._p95(warm)))
check("frames were replaced, none queued", w.link.slot.replaced > 0 and w.painted == w.frame_no,
      (w.link.slot.replaced, w.painted, w.frame_no))

# -------------------------------------------------------------- measures and forces
w.slider.Value = 0
w._on_slider()
for key in ("sigma", "tau", "tau_v", "tau_w", "vonmises", "tresca", "principal1", "principal2"):
    w.measure_list.SelectedIndex = w.measure_keys.index(key)
    if not (h.idle() and w.last["measure"] == key):
        check("measure " + key, False, w.last["measure"])
check("all measures answer", True)
w.measure_list.SelectedIndex = w.measure_keys.index("sigma")
h.idle()
full = abs(w.last["outside"]["value"])
w.force_checks["Mw"].Checked = False
h.idle()
less = abs(w.last["outside"]["value"])
check("unchecking Mw lowers sigma", less < full, (less, full))
check("the unchecked force is in brackets, its value stays", u"(Mw = " in w.forces_label.Text and
      abs(w.last["forces"]["Mw"]) > 0.0, w.forces_label.Text)
w.force_checks["Mw"].Checked = True
w.force_checks["T"].Checked = False
h.idle()
check("T unchecked, shear on: the term stays", abs(w.last["term"]) > 0.0 and w.last["T"] == 0.0,
      w.last["term"])
check("term shown apart", u"shear centre term" in w.torque_label.Text, w.torque_label.Text)
w.force_checks["T"].Checked = True
term = w.last["term"]
w.shear_check.Checked = False
h.idle()
check("switch off: left out, still shown", u"left out" in w.torque_label.Text and
      near(w.last["T_S"], w.last["T"], 1e-12), w.torque_label.Text)
w.shear_check.Checked = True
h.idle()
check("switch on: T_S with the term", near(w.last["T_S"], w.last["T"] + w.last["term"], 1e-9))

# ------------------------------------------------------------------- fixed scale
w.measure_list.SelectedIndex = w.measure_keys.index("vonmises")
h.idle()
lo, hi = w.last["lo"], w.last["hi"]
w.fixed_check.Checked = True
check("fixed scale starts from the current one", h.idle() and near(w.last["hi"], hi, 1e-3) and
      w.fixed_hi.Text != u"", (w.last["hi"], hi, w.fixed_hi.Text))
w.fixed_hi.Text = u"50,5"
check("comma accepted, scale used", h.idle() and near(w.last["hi"], 50.5e6, 1e-9), w.last["hi"])
before = w.frame_no
white = w.fixed_hi.BackColor
w.fixed_hi.Text = u"abc"
h.F.Application.DoEvents()
check("a wrong text sends nothing and shows it", w.frame_no == before and w.fixed_hi.BackColor != white)
w.fixed_hi.Text = u"60"
h.idle()
w.slider.Value = 800
w._on_slider()
check("fixed scale holds along the beam", h.idle() and near(w.last["hi"], 60e6, 1e-9))
w.fixed_check.Checked = False
h.idle()
check("off again: automatic", not near(w.last["hi"], 60e6, 1e-6), w.last["hi"])

# ------------------------------------------------------------------------ arrows
w.arrow_check.Checked = False
h.idle()
check("arrows off", w.last["arrow_max"] is None)
w.arrow_check.Checked = True
h.idle()
check("arrows on, value in the legend", w.last["arrow_max"] > 0.0)
w.arrow_slider.Value = 30
w._request()
check("arrow length goes to the worker", h.idle() and w._frame_request(False)["arrows"]["length"] == 3.0)

# ------------------------------------------------------------------ hover and view
v = w.last["view"]
size = w.canvas.ClientSize
web = (int((0.0 - v["x0"]) * v["s"]), int((v["y1"] + 0.0) * v["s"]))
# the middle of the web of this channel: V = 0, W near the web
seed = None
for key in range(0, size.Height, 4):
    w.hover_label.Text = u""
    w.on_canvas_move(size.Width // 2, key, False)
    if h.pump(lambda: w.hover_label.Text != u"", 0.15):
        seed = key
        break
check("hover shows the values", seed is not None and u"MPa" in w.hover_label.Text, w.hover_label.Text)
w.on_canvas_move(1, 1, False)
check("hover beside the section clears", h.pump(lambda: w.hover_label.Text == u"", 2.0))
s0 = w.last["view"]["s"]
w.on_canvas_wheel(size.Width // 2, size.Height // 2, 120)
check("wheel: coarse first, then the fine picture", h.idle() and not w.last["coarse"] and
      near(w.last["view"]["s"], 1.25 * s0, 1e-9), w.last["view"])
w.on_canvas_press(100, 100)
cx = w.last["view"]["x0"]
w.on_canvas_move(140, 100, True)
check("drag pans", h.idle() and near(w.last["view"]["x0"], cx - 40.0 / w.last["view"]["s"], 1e-6))
w.press = None
w.on_fit()
check("double click: fit", h.idle() and near(w.last["view"]["s"], s0, 1e-9))

# ---------------------------------------------------------------- resize in flight
w.slider.Value = 200
w._on_slider()
w.form.ClientSize = h.D.Size(1000, 700)
w.form.ClientSize = h.D.Size(1100, 760)
check("resize: the frame follows the canvas", h.idle() and
      w.canvas.dib[1:] == (w.canvas.ClientSize.Width, w.canvas.ClientSize.Height),
      (w.canvas.dib[1:], w.canvas.ClientSize))

# low window: the measure list gives way (a view of 600 pixels, below the tab row)
strip = w.tabs.ClientSize.Height - w.tabs.DisplayRectangle.Height
check("the smallest window has room for the tab row", w.form.MinimumSize.Height == w.px(640) + strip, strip)
w.form.ClientSize = h.D.Size(1000, 600 + strip)
check("low window: the messages keep their room", w.warn_box.Height >= 60 and
      w.measure_list.Height < gui.MEASURE_H, (w.warn_box.Height, w.measure_list.Height))
w.form.Size = w.form.MinimumSize
rows = w.measure_list.ClientSize.Height // max(w.measure_list.ItemHeight, 1)
print("smallest window {0} x {1}: measure list {2} px, {3} rows, messages {4} px".format(
    w.form.ClientSize.Width, w.form.ClientSize.Height, w.measure_list.Height, rows, w.warn_box.Height))
check("smallest window: the measure list keeps rows to scroll through", rows >= 3, rows)
check("smallest window: the messages keep their room", w.warn_box.Height >= gui.WARN_MIN, w.warn_box.Height)
check("smallest window: nothing below the panel", w.warping_label.Bottom <= w.left.ClientSize.Height + 2,
      (w.warping_label.Bottom, w.left.ClientSize.Height))
w.form.ClientSize = h.D.Size(1100, 760)
check("resize back", h.idle() and w.measure_list.Height == gui.MEASURE_H)

# contour lines: part of every frame request, on one and on several sections, and kept
check("contour lines start off", not w.contour_check.Checked and not w._frame_request(False)["contours"])
before = bytearray(w.canvas.dib[0])
w.contour_check.Checked = True
check("contour lines: a new frame with them", h.idle() and w._frame_request(False)["contours"])
check("contour lines: the picture changes", bytearray(w.canvas.dib[0]) != before)
check("contour lines: the choice is kept", bu.settings()["contours"] is True)
w.sheet_check.Checked = True
h.idle()
sheet_on = bytearray(w.canvas.dib[0])
w.contour_check.Checked = False
h.idle()
check("contour lines: also on several sections", bytearray(w.canvas.dib[0]) != sheet_on)
w.sheet_check.Checked = False
h.idle()
check("contour lines: back off, kept off", bu.settings()["contours"] is False)

# -------------------------------------------------------------------- language
SAVED = []


def _counted(key, value):
    SAVED.append((key, value))
    bu.settings()[key] = value


bu.save_setting = _counted
ACCENT = h.D.Color.FromArgb(*gui.ACCENT).ToArgb()


def marked(window, code):
    """Only the button of code is filled."""
    return all((b.BackColor.ToArgb() == ACCENT) == (c == code) for c, b in window.lang_buttons.items())


def click(button):
    """What a click on the button does (the form is never shown, so no PerformClick)."""
    button.OnClick(h.F.MouseEventArgs(h.F.MouseButtons.Left, 1, 2, 2, 0))


nl_b, en_b = w.lang_buttons["nl"], w.lang_buttons["en"]
check("two buttons NL and ENG, ENG filled", nl_b.Text == u"NL" and en_b.Text == u"ENG" and
      marked(w, "en"), (nl_b.Text, en_b.Text))
check("small, side by side, at the right end of the tab row",
      (en_b.Width, en_b.Height) == (w.px(gui.LANG_W), w.px(gui.LANG_H)) and nl_b.Right < en_b.Left and
      en_b.Right <= w.form.ClientSize.Width and en_b.Bottom <= w.tabs.DisplayRectangle.Top + 2 and
      nl_b.Left > w.tabs.GetTabRect(1).Right, (nl_b.Bounds, en_b.Bounds, w.tabs.DisplayRectangle))
check("over the tab control, on both tabs", w.form.Controls.GetChildIndex(en_b) <
      w.form.Controls.GetChildIndex(w.tabs) and en_b.Parent == w.form)
before = w.place_label.Text
click(en_b)
check("a click on the active language changes nothing", SAVED == [] and w.place_label.Text == before and
      marked(w, "en"), SAVED)
click(nl_b)
h.pump(lambda: True, 0.05)
check("NL: Dutch texts", w.warping_label.Text.startswith(u"Welving") and
      u"Excentrisch" in w.warn_box.Text and u"Plaats" in w.place_label.Text, w.place_label.Text)
check("NL: Dutch combo and tabs", u"elementen" in w.source_box.Text and
      w.page_view.Text == u"Weergave" and w.page_doc.Text == u"Handleiding", w.source_box.Text)
check("NL: filled, and kept as setting", marked(w, "nl") and SAVED == [("language", "nl")] and
      bu.settings()["language"] == "nl", SAVED)
png(h, "window_nl_100.png")
click(en_b)
check("ENG: back to English", u"Position" in w.place_label.Text and w.page_doc.Text == u"Manual" and
      marked(w, "en") and SAVED[-1] == ("language", "en"), SAVED)
check("the manual is not made before its tab opens", w.doc_viewer is None)

# ------------------------------------------------------------- the manual tab
idle = h.idle()
state = (w.slider.Value, bool(w.sheet_check.Checked), w.frame_no, w.painted, w.last["s"])
link_before = w.link
LOGGED = []
_log = gui.log
gui.log = lambda text: (LOGGED.append(text), _log(text))
check("the manual: the tab opens it", h.open_doc() and w._is_browser() and w.doc_lang == "en")
modes = [l for l in LOGGED if u"document mode" in l]
check("the document mode is logged once, 9 or higher (SVG)", len(modes) == 1 and
      float(modes[0].split(u"document mode ")[1].split(u" ")[0]) >= 9, modes)
gui.log = _log
check("switching tabs sends nothing to the worker", idle and w.frame_no == state[2] and
      w.link is link_before and w.link.alive, (w.frame_no, state[2]))
doc = w.doc_viewer.Document


def tags(name):
    return list(w.doc_viewer.Document.GetElementsByTagName(name))


def text_of(element):
    return u"{0}".format(element.InnerText or u"").strip()


md_en = io.open(os.path.join(REPO, "docs", "en", "MecwayAdvancedBeamAnalysis.md"),
                          encoding="utf-8").read()
check("EN: the title", text_of(tags("h1")[0]).startswith(u"MecwayAdvancedBeamAnalysis: the stress"),
      text_of(tags("h1")[0]))
check("EN: all chapters as headings", len(tags("h2")) >= 10, len(tags("h2")))
rows = [len(list(t.GetElementsByTagName("tr"))) for t in tags("table")]
check("EN: tables with their rows", len(rows) >= 5 and max(rows) >= 30, rows[:6])
check("EN: code blocks", len(tags("pre")) >= 3, len(tags("pre")))


def image_checks(md, lang):
    images = tags("img")
    want = md.count(u"![")
    check(lang + ": every image of the Markdown, from data", len(images) == want and all(
        u"{0}".format(i.GetAttribute("src")).startswith(u"data:image/") for i in images), (len(images), want))
    from System.Drawing import Image
    good = []
    for img in images:
        src = u"{0}".format(img.GetAttribute("src"))
        r = img.OffsetRectangle
        ratio = r.Width / float(max(r.Height, 1))
        alt = u"{0}".format(img.GetAttribute("alt"))
        name = [l for l in md.split(u"\n") if u"![" + alt + u"]" in l][0]
        name = name.split(u"](")[1].split(u")")[0]
        path = os.path.normpath(os.path.join(REPO, "docs", "en" if lang == "en" else "", name))
        if src.startswith(u"data:image/svg"):
            head = io.open(path, encoding="utf-8").read(400)
            fw = float(head.split(u'width="')[1].split(u'"')[0])
            fh = float(head.split(u'height="')[1].split(u'"')[0])
        else:
            pic = Image.FromFile(path)
            fw, fh = float(pic.Width), float(pic.Height)
            pic.Dispose()
        good.append((src[11:14], r.Width > 100 and abs(ratio / (fw / fh) - 1.0) < 0.02))
    check(lang + ": every image drawn at its own aspect ratio, SVG and PNG",
          all(g for _, g in good) and set(k for k, _ in good) == {u"svg", u"png"}, good)


image_checks(md_en, "en")


def html_scroll():
    return tags("html")[0].ScrollTop


def jump(anchor):
    """Click the link to anchor; the top of its target after the jump."""
    link = [a for a in tags("a") if u"{0}".format(a.GetAttribute("href")).endswith(u"#" + anchor)][0]
    link.InvokeMember("click")
    h.pump(lambda: False, 0.3)
    target = w.doc_viewer.Document.GetElementById(anchor)
    return gui_cap.client_rect(target)[1], text_of(target), text_of(link)


chapter7 = [e for e in tags("h2") if text_of(e).startswith(u"7.")][0]
top, title, label = jump(chapter7.Id)
check("a link of the contents jumps to its chapter", html_scroll() > 0 and abs(top) < 30 and
      title == label, (html_scroll(), top, title, label))
corner = [e for e in tags("h3") if text_of(e).startswith(u"5.5")][0]
tags("html")[0].ScrollTop = 0
top, title, label = jump(corner.Id)
check("a § reference jumps to its section", label == u"§5.5" and abs(top) < 30 and html_scroll() > 0,
      (label, top, title))
check("jumping leaves the page as it is", w.doc_ready() and w.doc_lang == "en")

# a refresh (F5) keeps the page (measured: the browser element renders it again)
loads = w.doc_loads
w.doc_viewer.Refresh()
h.pump(lambda: False, 1.5)
check("after a refresh the manual stays, without loading it again", w.doc_ready() and
      w.doc_loads == loads and not w.doc_loading, (loads, w.doc_loads))
# an empty page the window did not ask for: the manual comes back
w.doc_viewer.DocumentText = u"<html><body><p>empty</p></body></html>"
check("an empty page that was not asked for: the manual is loaded again", h.pump(
    lambda: w.doc_loads == loads + 1 and w.doc_ready() and not w.doc_loading, 10.0), (loads, w.doc_loads))

# the language buttons while the manual shows: the manual follows at once
click(nl_b)
check("NL: the manual in Dutch at once", h.pump(lambda: w.doc_ready() and not w.doc_loading and
      text_of(tags("h1")[0]).startswith(u"MecwayAdvancedBeamAnalysis: spanningen"), 10.0) and
      w.doc_lang == "nl")
md_nl = io.open(os.path.join(REPO, "docs", "MecwayAdvancedBeamAnalysis.md"), encoding="utf-8").read()
check("NL: a heading with an accent, as written", u"3.1 Eén doorsnede" in [text_of(e) for e in tags("h3")],
      [text_of(e) for e in tags("h3")][:2])
image_checks(md_nl, "nl")
# the link to the other manual is the language button
other = [a for a in tags("a") if u"{0}".format(a.GetAttribute("href")).endswith(u"#lang-en")][0]
other.InvokeMember("click")
check("the link to the English manual switches the language", h.pump(
    lambda: bu.language() == "en" and w.doc_ready() and not w.doc_loading and
    text_of(tags("h1")[0]).startswith(u"MecwayAdvancedBeamAnalysis: the stress"), 10.0) and
    marked(w, "en") and u"Position" in w.place_label.Text)
png(h, "window_manual_en_100.png")

# frames go on while the manual shows: a slider step gets its picture
w.slider.Value = 700
w._on_slider()
check("slider while the manual shows: the frame arrives", h.idle() and near(w.last["s"], 1.4, 1e-9) and
      w.painted == w.frame_no, w.last["s"])
w.tabs.SelectedIndex = 0
h.pump(lambda: False, 0.2)
check("back on the view: the state as it was, no new frame", h.idle() and w.slider.Value == 700 and
      bool(w.sheet_check.Checked) == state[1] and w.frame_no == w.painted and w.link is link_before and
      w.link.alive and w.canvas.dib[1:] == (w.canvas.ClientSize.Width, w.canvas.ClientSize.Height))
frames = w.frame_no
w.tabs.SelectedIndex = 1
w.tabs.SelectedIndex = 0
h.pump(lambda: False, 0.2)
check("switching without a resize: no frame request", w.frame_no == frames, (w.frame_no, frames))
# a resize while the manual shows: the view gets its size, and a new frame, when it shows
w.tabs.SelectedIndex = 1
w.form.ClientSize = h.D.Size(1150, 790)
h.pump(lambda: False, 0.2)
w.tabs.SelectedIndex = 0
check("resized while hidden: a new frame of the canvas size on return", h.idle() and w.frame_no > frames and
      w.canvas.dib[1:] == (w.canvas.ClientSize.Width, w.canvas.ClientSize.Height) and
      w.page_view.ClientSize == w.page_doc.ClientSize, (w.frame_no, frames, w.canvas.dib[1:]))
w.form.ClientSize = h.D.Size(1100, 760)
h.idle()
SAVED[:] = []
# the setting is kept: a new window starts in the language chosen last
bu.settings()["language"] = "nl"
h2 = gui_cap.Harness(REPO)
check("a new window starts with the language kept", marked(h2.window, "nl") and
      h2.window.page_doc.Text == u"Handleiding" and h2.window.doc_viewer is None)
h2.close()
bu.settings()["language"] = "en"
bu.save_setting = _in_memory

# --------------------------------------------------------------- result sets
h.solution.steps = [1, 2]
w._refresh()
check("time steps listed", h.pump(lambda: w.result_box.Items.Count == 2) and
      u"Step 2 (t = 1.000 s)" in [w.result_box.Items[1]], w.result_box.Items[1])
h.idle()
check("Read again keeps the beam", w.source_box.SelectedIndex == 1)
n0 = w.last["forces"]["N"]
w.result_box.SelectedIndex = 1
w._apply()
check("another step: other forces, same beam",
      h.pump(lambda: w.last and near(w.last["forces"]["N"], 6000.0, 1e-9)),
      (n0, w.last["forces"]["N"]))
h.solution.steps = []
h.solution.mode_list = [1, 2]
w._refresh()
check("modes listed", h.pump(lambda: w.result_box.Items.Count == 2 and u"Mode 1" in w.result_box.Items[0]))
h.idle()
w.result_box.SelectedIndex = 1
w._apply()
check("mode is marked as scaled", h.pump(lambda: u"Mode solution" in w.warn_box.Text), w.warn_box.Text)
h.solution.mode_list = []
w._refresh()
h.pump(lambda: u"Active" in w.result_box.Text)
h.idle()

# ---------------------------------------------------- configurations (review finding)
plain = io.open(h.path, encoding="utf-8").read()


def with_configurations(names):
    lines = u"".join(u'<configuration name="{0}" />'.format(n) for n in names)
    with io.open(h.path, "w", encoding="utf-8") as f:
        f.write(plain.replace(u"  <mat ", u"  " + lines + u"\n  <mat ", 1))


def boxed():
    return [w.result_box.Items[i] for i in range(w.result_box.Items.Count)]


with_configurations([u"A", u"B"])
h.solution.steps = [1, 2]
h.mw.configuration = None
w._refresh()
check("two configurations: every step per configuration",
      h.pump(lambda: w.result_box.Items.Count == 4) and
      [t.split(u" (")[0] for t in boxed()] == [u"Configuration A, Step 1", u"Configuration A, Step 2",
                                              u"Configuration B, Step 1", u"Configuration B, Step 2"],
      boxed())
check("opening sets the configuration itself and says so",
      h.pump(lambda: w.last is not None and h.mw.configuration == u"A" and
             u"Configuration A made active" in w.warn_box.Text), (h.mw.configuration, w.warn_box.Text))
h.idle()
w.result_box.SelectedIndex = 3
w._apply()
check("configuration B, step 2: switched, its forces",
      h.pump(lambda: h.mw.configuration == u"B" and w.last and near(w.last["forces"]["N"], 6000.0, 1e-9)),
      (h.mw.configuration, w.last and w.last["forces"]["N"]))
h.idle()
h.mw.configuration = u"A"           # the user switched in Mecway meanwhile
part = bmw.read_chunk(h.mw, h.solution, [1], w.result_items[3], "element")
check("reading switches to the chosen configuration again", h.mw.configuration == u"B")
w._on_solved(bmw.result_sets(h.solution), w.result_items[3])
check("after a solve the same configuration and step stay chosen",
      w.result_box.SelectedIndex == 3 and w.result_items[3]["configuration"] == u"B", w.result_box.SelectedIndex)
h.idle()


def no_such(name):
    raise ValueError(u'Configuration "{0}" does not exist.'.format(name))


h.mw.set_active_configuration = no_such
w.result_box.SelectedIndex = 0
w._apply()
check("a configuration Mecway lacks: no picture, says to save",
      h.pump(lambda: w.canvas.dib is None and u"Save the model" in w.status_label.Text and
             u"does not exist" in w.status_label.Text), w.status_label.Text)
check("a failed switch does not claim it", u"made active" not in w.warn_box.Text, w.warn_box.Text)
del h.mw.set_active_configuration
h.idle()
h.solution.steps = [1]              # configuration B has fewer steps than listed
w.result_box.SelectedIndex = 3
w._apply()
check("a step the configuration lacks: no picture, says so",
      h.pump(lambda: w.canvas.dib is None and u"does not have this result" in w.status_label.Text),
      w.status_label.Text)
h.solution.steps = [1, 2]
h.idle()
with_configurations([u"A"])
h.solution.steps = []
h.mw.configuration = None
w._refresh()
check("one configuration: used as such, set and named",
      h.pump(lambda: boxed() == [u"Configuration A"] and h.mw.configuration == u"A" and w.last is not None),
      (boxed(), h.mw.configuration))
h.idle()
with io.open(h.path, "w", encoding="utf-8") as f:
    f.write(plain)
h.mw.configuration = None
w._refresh()
check("no configurations: the active solution, nothing switched",
      h.pump(lambda: boxed() == [u"Active solution"]) and h.idle() and h.mw.configuration is None, boxed())

# --------------------------------------------------------------------- the routes
h.solution.mode = "node"
w._refresh()
check("no element values: node values, said so", h.pump(lambda: u"node values" in w.warn_box.Text),
      w.warn_box.Text)
check("node route draws", h.idle() and w.canvas.dib is not None and w.measure_list.Items.Count == 8)
h.solution.mode = "kinematics"
w._refresh()
check("no forces: kinematics, four lines named", h.pump(lambda: u"bending moment 2" in w.warn_box.Text),
      w.warn_box.Text[:80])
check("kinematics: sigma only in the list", h.pump(lambda: w.measure_keys == ["sigma"]) and
      w.measure_list.Items.Count == 1, w.measure_keys)
check("kinematics draws", h.idle() and w.last["measure"] == "sigma" and w.canvas.dib is not None)
h.solution.mode = "nothing"
w._refresh()
check("no results at all: said, no picture", h.pump(lambda: u"no section forces and no rotations"
                                                    in w.warn_box.Text) and
      h.pump(lambda: w.canvas.dib is None), w.warn_box.Text[:60])
h.solution.mode = "element"
w._refresh()
check("back: all measures, picture", h.pump(lambda: w.canvas.dib is not None and w.opened and
                                            len(w.measure_keys) == 8), w.measure_keys)
h.idle()

# ------------------------------------------------------------ file against the API
h.mw.extra_nodes = 3
w._refresh()
check("file differs: asked to save, no picture", h.pump(lambda: u"differs from the model" in w.warn_box.Text)
      and w.canvas.dib is None, w.warn_box.Text[:70])
h.mw.extra_nodes = 0
w._refresh()
check("after saving: picture again", h.pump(lambda: w.canvas.dib is not None))
h.idle()
keep = h.mw.path
h.mw.path = u""
w._refresh()
check("unsaved model: said, no picture", h.pump(lambda: u"not saved" in w.warn_box.Text) and
      h.pump(lambda: w.canvas.dib is None), w.warn_box.Text[:60])
h.mw.path = keep
w._refresh()
h.pump(lambda: w.canvas.dib is not None)
h.mw.selection = []
w.source_box.SelectedIndex = 0
w._refresh()
h.pump(lambda: not w.tickets and w.link.slot.empty(), 5.0)
check("an empty current selection: the first usable beam",
      h.pump(lambda: w.source_box.SelectedIndex == 1), w.source_box.SelectedIndex)
h.idle()
real_sources = bmw.sources
bmw.sources = lambda mw: [{"key": None, "name": None, "count": 0}]
w._refresh()
check("nothing to show: said so", h.pump(lambda: u"Select beam elements" in w.status_label.Text),
      w.status_label.Text)
bmw.sources = real_sources
w._refresh()
h.pump(lambda: w.canvas.dib is not None and w.opened)
h.idle()

# ---------------------------------------------------------------- several sections
w.sheet_check.Checked = True
check("several sections: one composite picture", h.idle() and w.last.get("sheet") and
      w.canvas.dib[1:] == (w.canvas.ClientSize.Width, w.canvas.ClientSize.Height), w.last.get("n"))
check("six by default, labels known to the canvas", len(w.canvas.tiles) == 6)
check("slider and option off in this mode", not w.slider.Enabled and not w.marker_check.Enabled)
check("the line says one colour scale", u"one colour scale" in w.place_label.Text and
      w.forces_label.Text == u"", w.place_label.Text)
png(h, "window_sheet_en.png")
before = w.frame_no
w.sheet_count.Value = 9
check("count changes the sheet", h.pump(lambda: w.last.get("n") == 9) and w.frame_no > before)
w.measure_list.SelectedIndex = w.measure_keys.index("sigma")
check("the measure works on the sheet", h.idle() and w.last["measure"] == "sigma")
w.fixed_check.Checked = True
h.idle()
pair = w.fixed.get("sigma")
check("fixed scale on the sheet", pair and near(w.last["hi"], pair[1], 1e-6))
w.fixed_check.Checked = False
h.idle()
t = w.last["tiles"][4]
w.hover_label.Text = u""
hits = False
for dy in range(t["band"], t["h"], 6):
    w.on_canvas_move(t["x"] + t["w"] // 2, t["y"] + dy, False)
    if h.pump(lambda: w.hover_label.Text != u"", 0.1):
        hits = True
        break
check("hover on a tile names its position", hits and u"mm:" in w.hover_label.Text, w.hover_label.Text)
check("hover on a tile names its number", w.hover_label.Text.startswith(
    u"Section {0}, {1}:".format(t["number"], bu.fmt_length(t["s"]))), w.hover_label.Text)
s0 = w.last.get("view")
w.on_canvas_wheel(20, 20, 120)
w.on_canvas_press(20, 20)
check("no zoom or drag on the sheet", w.press is None and w.view is None)
w.form.ClientSize = h.D.Size(1200, 800)
check("the sheet follows a resize", h.idle() and
      w.canvas.dib[1:] == (w.canvas.ClientSize.Width, w.canvas.ClientSize.Height))
w.sheet_check.Checked = False
check("back to one section", h.idle() and not w.last.get("sheet") and w.slider.Enabled and
      w.marker_check.Enabled and not w.canvas.tiles)
w.measure_list.SelectedIndex = w.measure_keys.index("vonmises")
h.idle()

# -------------------------------------------------------------- position in Mecway
mw = h.mw
clean = mw.trace()
check("off by default", not w.marker_check.Checked and not w.marker_on and mw.view == "solution")
w.slider.Value = 500
w._on_slider()
h.idle()
w.marker_check.Checked = True
check("option on: the element is in the model", h.pump(lambda: w.marker_on) and
      mw.trace()[0] == clean[0] + 4 and mw.trace()[1] == clean[1] + 1, mw.trace())
check("Mecway shows the model", mw.view == "model")
check("the window says so", u"temporary element" in w.warn_box.Text, w.warn_box.Text[:60])
corner = mw.points[-1]
check("at the position, across the beam", near(corner[0], 1.0, 1e-9) and abs(corner[1]) > 0.05,
      corner)
shown = mw.displays
for i in range(40):
    w.slider.Value = 600 + i
    w._on_slider()
    h.F.Application.DoEvents()
check("no move while the slider runs", mw.displays == shown, mw.displays - shown)
want = 2.0 * 639.0 / 1000.0
check("moved after the slider stops", h.pump(lambda: near(mw.points[-1][0], want, 1e-9), 3.0), mw.points[-1])
check("one move for many steps", mw.displays - shown == 1, mw.displays - shown)
w._refresh()
check("Read again with the element in the model: no difference with the file",
      h.pump(lambda: w.opened and not w.tickets) and h.idle() and u"differs" not in w.warn_box.Text,
      w.warn_box.Text[:60])
gone = mw.nodes(mw.component_elements(u"Balkpositie_tijdelijk")[0])
mw.delete_nodes(gone)
w.slider.Value = 700
w._on_slider()
check("removed by hand: the option goes off, no beam node moves",
      h.pump(lambda: not w.marker_on and not w.marker_check.Checked, 3.0))
w.marker_check.Checked = True
h.pump(lambda: not w.tickets, 2.0)
w.marker_check.Checked = False
h.pump(lambda: not w.tickets, 2.0)
w.marker_check.Checked = True
check("on again", h.pump(lambda: w.marker_on))
mw.fail_element = True
w.marker_check.Checked = False
h.pump(lambda: mw.view == "solution" and not w.tickets)
check("option off: no trace, solution view", mw.trace() == clean and mw.view == "solution", mw.trace())
w.marker_check.Checked = True
check("a failure in Mecway: option off, nothing left", h.pump(lambda: not w.marker_check.Checked) and
      mw.trace() == clean and not w.marker_on, mw.trace())
mw.fail_element = False
w.sheet_check.Checked = False
h.idle()
w.marker_check.Checked = True
h.pump(lambda: w.marker_on)
w.sheet_check.Checked = True
check("several sections takes the element away", h.pump(lambda: not w.tickets and mw.trace() == clean))
w.sheet_check.Checked = False
h.idle()

# ---------------------------------------------------------------- worker dies
w.slider.Value = 100
w._on_slider()
first_pid = w.link.proc.Id
for i in range(30):
    w.slider.Value = 100 + i
    w._on_slider()
w.link.proc.Kill()
check("worker killed in flight: the window says so", h.pump(lambda: u"stopped" in w.status_label.Text, 10.0),
      w.status_label.Text)
check("no picture requests pile up", not w.opened)
w._refresh()
check("Read again starts a new worker", h.pump(lambda: w.opened and w.link.alive, 20.0) and
      w.link.proc.Id != first_pid)
h.idle()

# ---------------------------------------------------- close during a frame in flight
proc = w.link.proc
for i in range(50):
    w.slider.Value = 200 + i
    w._on_slider()
t0 = time.time()
w._on_closing(None, None)
check("closing does not wait", time.time() - t0 < 0.2, time.time() - t0)
w.shutdown()
check("the worker is gone within 2 s", proc.HasExited and time.time() - t0 < 2.5, time.time() - t0)
check("shutdown twice is harmless", w.shutdown() is None)
w.form.Dispose()

# ---------------------------------------------------------------- a stuck script thread
h2 = gui_cap.Harness(REPO, caller=gui._MwCaller(False))
w = h2.window
w.start()
t0 = time.time()
for i in range(30):
    h2.F.Application.DoEvents()
check("Mecway busy: posting does not block", time.time() - t0 < 1.0 and w.tickets, time.time() - t0)
check("once served, the picture comes", opened(h2))
h2.close()

# -------------------------------------------------------------- garbage from a worker
bad = os.path.join(h.folder, "bad_worker.py")
with open(bad, "w") as f:
    f.write("import sys\nsys.stdout.write('x' * 100)\nsys.stdout.flush()\n")
h3 = gui_cap.Harness(REPO, worker=bad)
w = h3.window
w.start()
check("a garbled pipe is reported, nothing hangs", h3.pump(lambda: u"stopped" in w.status_label.Text, 15.0),
      w.status_label.Text)
h3.close()

# a worker that says hello and then never answers
hang = os.path.join(h.folder, "hang_worker.py")
with open(hang, "w") as f:
    f.write("import os, sys, time\nsys.path.insert(0, {0!r})\nimport beam_utils as bu\n"
            "import msvcrt\nmsvcrt.setmode(1, os.O_BINARY)\n"
            "os.write(1, bu.pack_reply_head(0, dict(ok=True, cmd='hello')))\ntime.sleep(120)\n"
            .format(os.path.join(REPO, "Scripts")))
h7 = gui_cap.Harness(REPO, worker=hang)
w = h7.window
w.start()
check("a hung worker does not hold the window", h7.pump(lambda: w.link.busy_since is not None, 15.0))
old = w.link
old.busy_since = time.time() - 2 * gui._HUNG_S
w._refresh()
check("Read again replaces a hung worker", w.link is not old and h7.pump(lambda: old.proc.HasExited, 5.0))
h7.close()

# a Python that lost its packages
lost = os.path.join(h.folder, "lost_worker.py")
with open(lost, "w") as f:
    f.write("import os, sys\nsys.path.insert(0, {0!r})\nimport beam_utils as bu\n"
            "import msvcrt\nmsvcrt.setmode(1, os.O_BINARY)\n"
            "os.write(1, bu.pack_reply_head(0, dict(ok=False, code='import_failed', "
            "data=dict(what='No module named scipy'))))\n".format(os.path.join(REPO, "Scripts")))
h8 = gui_cap.Harness(REPO, worker=lost)
w = h8.window
saved_found = bu.settings().get("python_found")
w.start()
check("a Python without packages is said, with the reason",
      h8.pump(lambda: u"No module named scipy" in w.warn_box.Text, 15.0), w.warn_box.Text)
check("and is searched again next time", not bu.settings().get("python_found"))
h8.close()
bu.save_setting("python_found", saved_found or "")

saved = bu.find_python
bu.find_python = lambda refresh=False: {"path": None, "pip": u"C:\\x\\python.exe", "own": ""}
h4 = gui_cap.Harness(REPO)
w = h4.window
w.start()
check("no Python: said, with the pip line", h4.pump(lambda: u"pip install numpy scipy" in w.warn_box.Text, 5.0),
      w.warn_box.Text)
h4.close()
bu.find_python = saved

# ------------------------------------------------------------- the numbers of the sections
def side_marks(harness):
    """Draw the window; the labels of the side view with the dots they belong to."""
    harness.capture(None).Dispose()
    return harness.window.side_labels


def regular(numbers, n):
    """1, n and every k-th number, for some k."""
    return any(numbers == sorted(set([i for i in range(1, n + 1) if (i - 1) % k == 0] + [n]))
               for k in range(1, n + 1))


def label_checks(w, name):
    """Every number readable: inside, apart, off the dots, the line and the end texts,
    next to its own dot, and a regular choice when not all fit."""
    side = w.side.ClientSize
    labels = w.side_labels
    boxes = [i[1:] for i in labels]
    r, gap = float(w.px(5)), float(w.px(6))
    dots = dict((d[0], d[1:]) for d in w.side_dots)
    dot_boxes = [(x - r, y - r, 2 * r, 2 * r) for x, y in dots.values()]
    ends = [b for b in w.side_blocked if b[0] != "line"]
    lines = [((b[1], b[2]), (b[3], b[4])) for b in w.side_blocked if b[0] == "line"]
    check(name + ": inside the control", all(
        i[1] >= 0.0 and i[2] >= 0.0 and i[1] + i[3] <= side.Width and i[2] + i[4] <= side.Height
        for i in labels), labels[:2])
    check(name + ": no two numbers overlap", not any(
        gui._overlap(a, b) for k, a in enumerate(boxes) for b in boxes[k + 1:]))
    check(name + ": no number over a dot or an end text", not any(
        gui._overlap(a, b) for a in boxes for b in dot_boxes + ends))
    check(name + ": no number over the line", not any(
        gui._crosses(a, p, q) for a in boxes for p, q in lines))

    def distance(label):
        x, y = dots[label[0]]
        dx = max(label[1] - x, 0.0, x - label[1] - label[3])
        dy = max(label[2] - y, 0.0, y - label[2] - label[4])
        return (dx * dx + dy * dy) ** 0.5
    far = max([distance(i) for i in labels] or [0.0])
    check(name + ": each number next to its own dot", far <= gap + r + 1.0, far)
    n = len(w.side_dots)
    numbers = [i[0] for i in labels]
    check(name + ": 1, the last and a regular choice between", n == 0 or (
        1 in numbers and n in numbers and regular(numbers, n)), numbers)


def title_checks(harness, name, n):
    """The titles: the longest that fits the tile, the number always first."""
    w = harness.window
    D = harness.D
    bmp = D.Bitmap(8, 8)
    g = D.Graphics.FromImage(bmp)
    fmt = w._title_format()
    font = w._font(8.0)
    texts = w.tile_texts
    tiles = w.canvas.tiles

    def fits(text, tile):
        return g.MeasureString(text, font, D.SizeF(10000.0, 1000.0), fmt).Width <= tile["w"] - w.px(8)
    ok = [fits(t, i) for t, i in zip(texts, tiles)]
    # every longer option does not fit
    longest = all(t in w.tile_texts_for(i) and not any(
        fits(o, i) for o in w.tile_texts_for(i)[:w.tile_texts_for(i).index(t)])
        for t, i in zip(texts, tiles))
    fmt.Dispose()
    g.Dispose()
    bmp.Dispose()
    check(name + ": every title fits its tile", len(texts) == n and all(ok),
          [t for t, f in zip(texts, ok) if not f][:2])
    check(name + ": every title starts with its number", all(
        t.startswith(u"{0}.".format(i["number"])) for t, i in zip(texts, tiles)), texts[:3])
    check(name + ": the longest title that fits", longest, texts[:3])
    return texts


def number_checks(harness, name, n=12):
    w = harness.window
    tiles = w.last["tiles"]
    check(name + ": numbered 1..n along the chain, {0} of them".format(n),
          [i["number"] for i in tiles] == list(range(1, n + 1)) and len(tiles) == n and
          [i["s"] for i in tiles] == sorted(i["s"] for i in tiles), len(tiles))
    check(name + ": number and position in each full title", all(
        w.tile_title(i).startswith(u"{0}.  {1}:".format(i["number"], bu.fmt_length(i["s"])))
        for i in tiles), w.tile_title(tiles[-1]))
    labels = side_marks(harness)
    check(name + ": the same numbers at the side view", [i[0] for i in labels] == list(range(1, n + 1)),
          [i[0] for i in labels])
    side = w.side.ClientSize
    centres = [i[1] + 0.5 * i[3] for i in labels]
    length = w.chain["length"]
    pad = 10.0 * w.k
    # a straight chain has room: every number right above or below its dot
    worst = max([abs(c - (pad + i["s"] / length * (side.Width - 2.0 * pad)))
                 for c, i in zip(centres[1:-1], tiles[1:-1])] or [0.0])
    check(name + ": each number stands above or below its position", worst <= 2.0, worst)
    check(name + ": 1 at the start, n at the end, in order", centres == sorted(centres))
    label_checks(w, name)
    return title_checks(harness, name, n)


def side_pixels(harness, box, without=False):
    """The colours of the side view inside box (left, top, width, height), drawn with
    or without the numbers."""
    w = harness.window
    if without:
        w._draw_side_numbers = lambda *a: None
    try:
        bmp = harness.capture(None)
    finally:
        if without:
            del w._draw_side_numbers
    at = w.side.PointToScreen(harness.D.Point(0, 0))
    dx = at.X - w.form.Location.X
    dy = at.Y - w.form.Location.Y
    out = [bmp.GetPixel(dx + i, dy + j).ToArgb()
           for i in range(int(box[0]), int(box[0] + box[2]))
           for j in range(int(box[1]), int(box[1] + box[3]))]
    bmp.Dispose()
    return out


def held_back(harness, act):
    """act() with the replies of the worker held back: what the window paints between
    a click and the next reply. The replies are handled afterwards."""
    w = harness.window
    w._drain = lambda: None
    try:
        act()
        marks = list(side_marks(harness))
        dots = list(w.side_dots)
    finally:
        del w._drain
    w._drain()
    harness.idle()
    return marks, dots


def bent_chains(harness, scale):
    """Bent chains as the worker hands them over, s along the chain."""
    w = harness.window
    shapes = {
        # the L of the end-to-end model: 0.6 m along, then 0.4 m up
        "L": [[0.0, 0.0], [0.2, 0.0], [0.4, 0.0], [0.6, 0.0], [0.6, 0.4 / 3], [0.6, 0.8 / 3],
              [0.6, 0.4]],
        "U": [[0.0, 0.3], [0.0, 0.0], [0.5, 0.0], [1.0, 0.0], [1.0, 0.3]],
        "hairpin": [[0.0, 0.0], [1.0, 0.0], [1.0, 0.02], [0.05, 0.02]],
        "steep": [[0.0, 0.0], [0.02, 0.5], [0.04, 1.0]]}
    real_chain, real_last = w.chain, w.last
    try:
        for shape, points in sorted(shapes.items()):
            arc = [0.0]
            for a, b in zip(points[:-1], points[1:]):
                arc.append(arc[-1] + ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5)
            w.chain = dict(real_chain, length=arc[-1], side={"points": points, "s": arc})
            w.last = {"sheet": True, "tiles": [{"s": arc[-1] * i / 11.0, "number": i + 1}
                                               for i in range(12)]}
            side_marks(harness)
            name = "scale {0}, {1} chain".format(scale, shape)
            label_checks(w, name)
            print("  {0}: numbers {1}".format(name, [i[0] for i in w.side_labels]))
            check(name + ": more than the two ends", len(w.side_labels) > 2, len(w.side_labels))
    finally:
        w.chain, w.last = real_chain, real_last


for scale in (1.0, 1.25, 1.5):
    hn = gui_cap.Harness(REPO, scale=scale)
    hn.mw.selection = []
    w = hn.window
    w.start()
    opened(hn)
    w.sheet_check.Checked = True
    w.sheet_count.Value = 12
    hn.idle()
    texts = number_checks(hn, "scale {0}".format(scale))
    print(u"  titles at {0}: {1} ... {2}".format(scale, texts[0], texts[-1]).encode("ascii", "replace"))
    w.form.Size = w.form.MinimumSize
    hn.idle()
    texts = number_checks(hn, "scale {0}, smallest window".format(scale))
    print(u"  titles at {0}, smallest window: {1} ... {2}".format(scale, texts[0], texts[-1])
          .encode("ascii", "replace"))
    label = w.side_labels[6]
    drawn, plain = side_pixels(hn, label[1:]), side_pixels(hn, label[1:], True)
    changed = sum(1 for a, b in zip(drawn, plain) if a != b)
    check("scale {0}: the number is really drawn".format(scale), changed > 5, changed)
    for count in (5, 2):
        w.sheet_count.Value = count
        hn.idle()
        number_checks(hn, "scale {0}, {1} sections".format(scale, count), count)
    w.sheet_count.Value = 12
    hn.idle()
    marks, dots = held_back(hn, lambda: setattr(w.sheet_check, "Checked", False))
    check("scale {0}: sheet off, before the reply: no numbers, no dots of the sheet".format(scale),
          marks == [] and dots == [], (marks[:2], len(dots)))
    check("scale {0}: one section has no number".format(scale), side_marks(hn) == [],
          w.side_labels)
    marks, dots = held_back(hn, lambda: setattr(w.sheet_check, "Checked", True))
    check("scale {0}: sheet on, before the reply: no numbers yet".format(scale),
          marks == [] and dots == [], (marks[:2], len(dots)))
    check("scale {0}: then the numbers".format(scale), len(side_marks(hn)) == 12, len(w.side_labels))
    bent_chains(hn, scale)
    if scale == 1.0:
        tile = {"number": 3, "s": 0.5, "no_section": {"code": "no_shape"}}
        check("a tile without a section: number, place, no section",
              w.tile_title(tile) == u"3.  {0}: no section".format(bu.fmt_length(0.5)) and
              all(t.startswith(u"3.") for t in w.tile_texts_for(tile)), w.tile_title(tile))
        w._on_probe({"frame": w.painted, "hit": False, "no_section": True, "number": 4, "s": 0.25})
        check("hover on a tile without a section says so", w.hover_label.Text ==
              u"Section 4, {0}: no section".format(bu.fmt_length(0.25)), w.hover_label.Text)
        w._on_probe({"frame": w.painted, "hit": False})
        check("hover beside the section: nothing", w.hover_label.Text == u"")
    hn.close()

# the placing on its own
check("no dots, no numbers", gui.place_labels([], [], 100.0, 50.0, 6.0, 5.0) == [])
crowd = [(10.0 + 3.0 * i, 25.0) for i in range(12)]
spots = gui.place_labels(crowd, [(14.0, 13.0)] * 12, 200.0, 60.0, 6.0, 5.0,
                         segments=[((0.0, 25.0), (200.0, 25.0))])
check("12 dots on 33 px: a regular choice, the ends kept", regular([i + 1 for i, _l, _t in spots], 12)
      and spots[0][0] == 0 and spots[-1][0] == 11, [i for i, _l, _t in spots])
check("touching boxes do not overlap", not gui._overlap((0, 0, 10, 10), (10, 0, 5, 5)) and
      gui._overlap((0, 0, 10, 10), (9.5, 0, 5, 5)))
check("a line through a box crosses it, one beside it does not",
      gui._crosses((0, 0, 10, 10), (-5, 5), (15, 5)) and
      not gui._crosses((0, 0, 10, 10), (-5, 12), (15, 12)) and
      gui._crosses((0, 0, 10, 10), (5, 5), (5, 6)))

# -------------------------------------------------------------------- device scale
for scale in (1.25, 1.5):
    h5 = gui_cap.Harness(REPO, scale=scale)
    h5.mw.selection = []
    w = h5.window
    w.start()
    check("scale {0}: picture".format(scale), opened(h5))
    h5.idle()
    size = w.canvas.ClientSize
    check("scale {0}: rendered on the pixels of the canvas".format(scale),
          w.canvas.dib[1:] == (size.Width, size.Height), (w.canvas.dib[1:], size.Width, size.Height))
    check("scale {0}: layout grows".format(scale), w.left.Width == int(round((gui.LEFT_W + 18) * scale)),
          w.left.Width)
    check("scale {0}: legend text fits".format(scale), w.legend.Width >= int(gui.LEGEND_W * scale))
    check("scale {0}: messages stay readable".format(scale), w.warn_box.Height >= 40 * scale and
          w.warn_box.Bottom <= w.left.DisplayRectangle.Bottom, (w.warn_box.Height, w.warn_box.Bottom))
    png(h5, "window_en_{0}.png".format(int(scale * 100)))
    h5.close()

# ------------------------------------------------- 2D or 3D, and the detail of one section
# Against the real worker and renderer (beam_worker, beam_render3d).
MULT = u"×"


def peek(w, coarse=False):
    """The frame request the window would send now, without sending or counting it."""
    rq = w._frame_request(coarse)
    w.frame_no -= 1
    return rq


def same_camera(a, b, tol=1e-9):
    """Two camera dicts equal up to rounding."""
    if a is None or b is None or sorted(a) != sorted(b):
        return False
    for key in a:
        x, y = a[key], b[key]
        if isinstance(x, (list, tuple)):
            if len(x) != len(y) or any(abs(float(p) - float(q)) > tol * max(1.0, abs(float(q)))
                                       for p, q in zip(x, y)):
                return False
        elif abs(float(x) - float(y)) > tol * max(1.0, abs(float(y))):
            return False
    return True


def wrap(angle):
    a = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


h9 = gui_cap.Harness(REPO)
h9.mw.selection = []
w = h9.window
w.start()
opened(h9)
h9.idle()
check("3D: one section, no row above the picture", not w.tools_on)
top0 = w.canvas.Top
w.sheet_check.Checked = True
h9.idle()
check("3D: several sections, the row comes and the canvas makes room",
      w.tools_on and w.canvas.Top > top0 and
      w.canvas.dib[1:] == (w.canvas.ClientSize.Width, w.canvas.ClientSize.Height),
      (w.canvas.Top, top0))
rq = peek(w, True)
check("3D: 2D first, said in the request, no camera, no coarse sheet",
      not w.mode3d and rq["mode"] == "2d" and "camera" not in rq and not rq["coarse"], rq.get("mode"))
check("3D: 2D sheet shows 2D/3D only", w.tool_parts == (True, False, False))
w.on_canvas_double(-5, -5)
check("2D: a double click beside the tiles does nothing", w.detail is None and w.sheet_check.Checked)

# detail from a 2D tile
tile = w.last["tiles"][2]
length = w.chain["length"]
w.on_canvas_double(tile["x"] + tile["w"] // 2, tile["y"] + tile["h"] // 2)
h9.idle()
check("2D detail: one section at the exact s of the tile", not w.sheet_check.Checked and
      w.detail is not None and not w.last.get("sheet") and near(w.last["s"], tile["s"], 1e-12) and
      w.slider.Value == int(round(tile["s"] / length * gui._SLIDER_MAX)), (w.last.get("s"), tile["s"]))
check("2D detail: title with number, count and s",
      w.detail_title.Text == u"Section 3 of 6, s = {0}".format(bu.fmt_length(tile["s"])),
      w.detail_title.Text)
check("2D detail: Back names the count", w.back_button.Text == u"Back to 6 sections", w.back_button.Text)
check("2D detail: the row shows title, shared scale and Back", w.tool_parts == (False, False, True))
check("2D detail: slider and option free", w.slider.Enabled and w.marker_check.Enabled)
w.view = {"zoom": 2.0, "cx": 0.0, "cy": 0.0}
w.on_canvas_double(20, 20)
check("detail: a double click stays Fit", h9.idle() and w.view is None and w.detail is not None)
w.slider.Value = w.slider.Value + 7
check("detail: the slider moves, the title goes, the slider rules",
      h9.idle() and w.detail_title.Text == u"" and w.detail_s is None and
      near(w.last["s"], length * w.slider.Value / float(gui._SLIDER_MAX), 1e-12), w.detail_title.Text)
w.on_back()
check("2D Back: six sections in 2D again", h9.idle() and w.sheet_check.Checked and
      w.last.get("sheet") and w.last["n"] == 6 and w.detail is None and not w.mode3d and
      not w.slider.Enabled and w.tool_parts == (True, False, False))

# the switch to 3D: one request per switch, though both radio buttons fire
frames = w.frame_no
w.radio_3d.Checked = True
check("3D chosen: one request", w.frame_no == frames + 1, w.frame_no - frames)
h9.idle()
check("3D chosen: kept as a setting", w.mode3d and bu.settings().get("sheet_view") == "3d")
check("3D: the worker answers in 3D with a camera", w.last.get("mode") == "3d" and "camera" in w.last,
      sorted(w.last))
default_cam = dict(w.last["camera"])
check("3D: the camera of the reply is the one to send", w.camera == w.last["camera"] and
      peek(w)["camera"] == w.last["camera"])
check("3D: no magnify asked, automatic", "magnify" not in peek(w) and w.last.get("magnify_auto") and
      w.magnify_value.Text == u"Auto ({0}{1})".format(gui.fmt_times(w.last["magnify"]), MULT),
      w.magnify_value.Text)
check("3D: the row shows Fit and magnify", w.tool_parts == (True, True, False))
h9.capture(None).Dispose()
texts = [i[0] for i in w.canvas_labels]
with_anchor = [t for t in w.last["tiles"] if t.get("anchor")]
check("3D: every section has an anchor in the default view", len(with_anchor) == 6, len(with_anchor))
check("3D: the numbers drawn at their anchors",
      all(u"{0}".format(t["number"]) in texts for t in with_anchor), texts)
anchor = with_anchor[0]["anchor"]
first = [i for i in w.canvas_labels if i[0] == u"{0}".format(with_anchor[0]["number"])][0]
check("3D: number centred on its anchor", abs(first[1] + 0.5 * first[3] - anchor[0]) < 1.0 and
      abs(first[2] + 0.5 * first[4] - anchor[1]) < 1.0, (first, anchor))
check("3D: axis labels X, Y, Z", all(a in texts for a in (u"X", u"Y", u"Z")), texts)
k = w.last["magnify"]
check("3D: magnification said, not to scale", abs(k - 1.0) < 1e-6 or
      u"sections {0}{1} magnified, not to scale".format(gui.fmt_times(k), MULT) in texts, (k, texts))
check("3D: sheet line and a hint", u"one colour scale" in w.place_label.Text and
      u"double click" in w.torque_label.Text, w.torque_label.Text)
check("3D: coarse requests allowed", peek(w, True)["coarse"])
png(h9, "window_3d_en.png")

# hover: number, position and the value on the material
a3 = w.last["tiles"][2]["anchor"]
found = u""
for dx in range(-40, 41, 4):
    for dy in range(-30, 31, 6):
        w.hover_label.Text = u""
        w.on_canvas_move(int(a3[0]) + dx, int(a3[1]) + dy, None)
        if h9.pump(lambda: u"value" in w.hover_label.Text, 0.05):
            found = w.hover_label.Text
            break
    if found:
        break
check("3D hover: section, position and values on the material",
      found.startswith(u"Section 3, {0}:".format(bu.fmt_length(w.last["tiles"][2]["s"]))) and
      u"MPa" in found, found)
check("3D hover: on the edge, number and position only",
      w.probe_text({"frame": w.painted, "number": 2, "s": 0.4, "hit": False}) == u"Section 2, 400.0 mm")
check("3D hover: an old frame says nothing",
      w.probe_text({"frame": w.painted - 1, "hit": False}) == u"")

# the wheel only after a click in the picture
cam0 = dict(w.camera)
frames = w.frame_no
check("3D: the wheel is not armed", not w.wheel_armed)
w.on_canvas_wheel(200, 150, 120)
check("3D: wheel without a click changes nothing", w.camera == cam0 and w.frame_no == frames)
w.on_canvas_press(200, 150, "left")
check("3D: a click arms the wheel", w.wheel_armed)
w.on_canvas_move(202, 151, "left")
check("3D: a move of a few pixels does not turn", w.camera == cam0 and w.frame_no == frames)
w.on_canvas_release()
check("3D: a click alone sends nothing", w.frame_no == frames)

# rotate with the left button
w.on_canvas_press(200, 150, "left")
w.on_canvas_move(250, 130, "left")
cam = dict(w.camera)
step = gui._ROT_DEG / w.k
check("3D: left drag turns: azimuth and elevation per pixel",
      near(cam["azimuth"], cam0["azimuth"] - 50 * step, 1e-9) and
      near(cam["elevation"], cam0["elevation"] - 20 * step, 1e-9), cam)
check("3D: left drag leaves zoom, pan and the rest of the dict",
      cam["zoom"] == cam0["zoom"] and cam["pan"] == cam0["pan"] and cam["up"] == cam0["up"] and
      cam["target"] == cam0["target"] and sorted(cam) == sorted(cam0), cam)
check("3D: a drag sends a coarse frame", w.frame_no > frames and peek(w, True)["coarse"])
w.on_canvas_move(260, 130, "left")
w.on_canvas_release()
h9.idle()
check("3D: after the drag the fine picture with the camera", not w.last["coarse"] and
      near(w.last["camera"]["azimuth"], wrap(cam0["azimuth"] - 60 * step), 1e-9) and
      w.camera == w.last["camera"], w.last["camera"])

# an old reply does not turn the view back during a drag
w.on_canvas_press(200, 150, "left")
w.on_canvas_move(220, 150, "left")
dragged = dict(w.camera)
w._on_frame({"request": {"gen": w.gen, "frame": w.frame_no}, "array": None,
             "reply": dict(w.last, camera=cam0, frame=w.frame_no, no_section=True)})
check("3D: a reply during the drag does not replace the camera", w.camera == dragged)
w.on_canvas_blur()
frames = w.frame_no
w.on_canvas_move(240, 150, "left")
check("3D: focus lost during a drag: the drag ends, nothing sent", w.drag is None and
      w.frame_no == frames)
h9.idle()
w.on_canvas_press(200, 150, "left")
w.on_canvas_move(230, 150, "left")
h9.idle()
w.on_canvas_release()
check("3D: release takes the normalised camera of the last reply", w.camera == w.last["camera"])

# pan with the right and the middle button
cam1 = dict(w.camera)
w.on_canvas_press(100, 100, "right")
w.on_canvas_move(130, 90, "right")
w.on_canvas_release()
h9.idle()
pan = gui._pan_of(w.last["camera"])
p1 = gui._pan_of(cam1)
check("3D: right drag pans by the pixels", near(pan[0], p1[0] + 30, 1e-9) and
      near(pan[1], p1[1] - 10, 1e-9), (pan, p1))
check("3D: right drag leaves the rotation", w.camera["azimuth"] == cam1["azimuth"] and
      w.camera["elevation"] == cam1["elevation"] and w.camera["zoom"] == cam1["zoom"])
w.on_canvas_press(100, 100, "middle")
w.on_canvas_move(100, 120, "middle")
w.on_canvas_release()
h9.idle()
check("3D: middle drag pans too", near(gui._pan_of(w.camera)[1], pan[1] + 20, 1e-9), w.camera)

# the wheel after the click: towards the mouse
w.on_canvas_press(100, 100, "left")
w.on_canvas_release()
cam2 = dict(w.camera)
size = w.canvas.ClientSize
mx, my = 120, 90
w.on_canvas_wheel(mx, my, 120)
cam3 = dict(w.camera)
c = (0.5 * size.Width, 0.5 * size.Height)
before = [(m - cc - p) / cam2["zoom"] for m, cc, p in zip((mx, my), c, gui._pan_of(cam2))]
after = [(m - cc - p) / cam3["zoom"] for m, cc, p in zip((mx, my), c, gui._pan_of(cam3))]
check("3D: the point under the mouse stays", all(abs(a - b) < 1e-9 for a, b in zip(before, after)),
      (before, after))
check("3D: zoom leaves the rotation", cam3["azimuth"] == cam2["azimuth"] and
      cam3["elevation"] == cam2["elevation"])
check("3D: the worker zooms by 1.25", h9.idle() and near(w.last["camera"]["zoom"], 1.25 * cam2["zoom"], 1e-9),
      (w.last["camera"]["zoom"], cam2["zoom"]))
w.on_canvas_blur()
cam4 = dict(w.camera)
w.on_canvas_wheel(mx, my, 120)
check("3D: focus gone, the wheel is off again", w.camera == cam4 and not w.wheel_armed)

# Fit
w.on_fit()
check("3D: Fit sends no camera", "camera" not in peek(w))
check("3D: Fit gives the default view", h9.idle() and same_camera(w.camera, default_cam),
      (w.camera, default_cam))

# magnify
w.magnify_slider.Value = 1
check("3D: magnify 1x", h9.idle() and near(w.last["magnify"], 1.0, 1e-12) and
      not w.last.get("magnify_auto") and w.magnify_value.Text == u"1{0}".format(MULT),
      w.magnify_value.Text)
h9.capture(None).Dispose()
check("3D: magnify 1x: no text over the picture",
      not any(u"magnified" in i[0] for i in w.canvas_labels), [i[0] for i in w.canvas_labels])
w.magnify_slider.Value = gui._MAG_STEPS
check("3D: the slider ends at 100x", h9.idle() and near(w.last["magnify"], 100.0, 1e-9))
ratios = [gui.magnify_of(i + 1) / gui.magnify_of(i) for i in range(1, gui._MAG_STEPS)]
check("3D: logarithmic, 0 is automatic", gui.magnify_of(0) is None and gui.magnify_of(1) == 1.0 and
      max(ratios) - min(ratios) < 1e-9, (min(ratios), max(ratios)))
check("3D: magnification texts", gui.fmt_times(100.0) == u"100" and gui.fmt_times(2.5) == u"2.5" and
      gui.fmt_times(1.0) == u"1")
w.magnify_slider.Value = 11
h9.idle()
mag11 = gui.magnify_of(11)
check("3D: magnify goes to the worker", near(w.last["magnify"], mag11, 1e-12))

# turn, then a double click: detail without a turn
w.on_canvas_press(200, 150, "left")
w.on_canvas_move(240, 160, "left")
w.on_canvas_release()
h9.idle()
turned = dict(w.camera)
fixed_before = dict(w.fixed)
t4 = w.last["tiles"][3]
x4, y4 = int(t4["anchor"][0]), int(t4["anchor"][1])
sheet_lo, sheet_hi, sheet_measure = w.last["lo"], w.last["hi"], w.last["measure"]
w.on_canvas_press(x4, y4, "left")
w.on_canvas_release()
w.on_canvas_press(x4 + 1, y4, "left")
w.on_canvas_move(x4 + 2, y4 + 1, "left")
w.on_canvas_double(x4 + 1, y4)
w.on_canvas_release()
check("3D: a double click does not turn", w.camera == turned)
check("3D: a double click on a section opens its detail", h9.pump(lambda: w.detail is not None, 5.0) and
      w.detail["number"] == 4 and h9.idle() and near(w.last["s"], t4["s"], 1e-12), w.detail)
check("3D detail: the state of the sheet is saved", w.detail["mode3d"] and w.detail["camera"] == turned and
      w.detail["magnify_at"] == 11 and w.detail["n"] == 6)
check("3D detail: title", w.detail_title.Text.startswith(u"Section 4 of 6, s = "), w.detail_title.Text)

# shared scale
own = w.last["hi"]
check("detail: own scale by default", peek(w)["scale"] is None)
w.shared_check.Checked = True
h9.idle()
check("detail: Shared scale sends the scale of all sections",
      peek(w)["scale"] == [sheet_lo, sheet_hi] and near(w.last["hi"], sheet_hi, 1e-12) and
      sheet_measure == w._measure(), (w.last["hi"], sheet_hi, own))
check("detail: the user's own fixed scale is left alone", not w.fixed_check.Checked and
      w.fixed == fixed_before and w.fixed_lo.Text == u"" and w.fixed_hi.Text == u"")
w.measure_list.SelectedIndex = w.measure_keys.index("sigma")
check("detail: another measure has no shared scale", h9.idle() and not w.shared_check.Enabled and
      peek(w)["scale"] is None)
w.measure_list.SelectedIndex = w.measure_keys.index(sheet_measure)
check("detail: back to the measure, shared again", h9.idle() and w.shared_check.Enabled and
      near(w.last["hi"], sheet_hi, 1e-12))
w.fixed_check.Checked = True
w.fixed_hi.Text = u"77"
check("detail: the user's fixed scale goes first", h9.idle() and near(w.last["hi"], 77e6, 1e-12))
clean = h9.mw.trace()
w.marker_check.Checked = True
check("detail: the position option works", h9.pump(lambda: w.marker_on))

# NL texts
w._on_language("nl")
h9.pump(lambda: True, 0.05)
check("NL: title, shared scale, Back", w.detail_title.Text.startswith(u"Doorsnede 4 van 6, s = ") and
      w.shared_check.Text == u"Schaal van alle doorsneden" and
      w.back_button.Text == u"Terug naar 6 doorsneden", (w.detail_title.Text, w.back_button.Text))
w._on_language("en")
check("EN again", w.shared_check.Text == u"Shared scale" and w.back_button.Text == u"Back to 6 sections")

# Back
w.on_back()
check("Back: six sections in 3D", h9.idle() and h9.pump(lambda: not w.tickets) and w.sheet_check.Checked and
      w.mode3d and int(w.sheet_count.Value) == 6 and w.last.get("sheet") and w.last["n"] == 6 and
      w.last.get("mode") == "3d")
check("Back: the camera and the magnification", same_camera(w.last["camera"], turned) and
      w.magnify_slider.Value == 11 and near(w.last["magnify"], mag11, 1e-12) and
      not w.last.get("magnify_auto"), w.last["camera"])
check("Back: the fixed scale as it was", not w.fixed_check.Checked and w.fixed == fixed_before and
      not w.fixed_lo.Enabled and peek(w)["scale"] is None, (w.fixed, fixed_before))
check("Back: the position option off, nothing left in the model", not w.marker_check.Checked and
      not w.marker_on and h9.mw.trace() == clean, h9.mw.trace())
check("Back: the detail is gone", w.detail is None and w.tool_parts == (True, True, False))

# a fixed scale on the sheet comes back too; a double click beside a section
w.fixed_check.Checked = True
w.fixed_hi.Text = u"55"
h9.idle()
kept = dict(w.fixed)
t2 = w.last["tiles"][1]
w.pick = {"x": t2["anchor"][0] + 3, "y": t2["anchor"][1], "tries": 0, "gen": w.gen, "t": time.time()}
w._on_pick({"frame": w.painted, "hit": False}, {"gen": w.gen, "pick": True})
check("3D: beside a section, the nearest number opens", w.detail is not None and w.detail["number"] == 2)
h9.idle()
w.fixed_check.Checked = False
w.sheet_count.Value = 4
w.on_back()
check("Back: a fixed scale on comes back with its values", h9.idle() and w.fixed_check.Checked and
      w.fixed == kept and w.fixed_hi.Text == bu._num(55.0) and near(w.last["hi"], 55e6, 1e-12) and
      w.last["n"] == 6, (w.fixed, kept, w.fixed_hi.Text))
w.fixed_check.Checked = False
h9.idle()
far = w.last["tiles"][1]["anchor"]
w.pick = {"x": far[0] + 3 * gui._PICK_PX * w.k, "y": far[1] - 3 * gui._PICK_PX * w.k, "tries": 0,
          "gen": w.gen, "t": time.time()}
w._on_pick({"frame": w.painted, "hit": False}, {"gen": w.gen, "pick": True})
check("3D: far from every section a double click opens nothing", w.detail is None and w.pick is None)

# a pick answered for an older frame: once more on the new one
w.pick = {"x": 5, "y": 5, "tries": 0, "gen": w.gen, "t": time.time()}
w._on_pick({"frame": w.painted - 1, "hit": False}, {"gen": w.gen, "pick": True})
check("3D: a stale pick is sent once more", w.pick is not None and w.pick["tries"] == 1)
w._on_pick({"frame": w.painted - 1, "hit": False}, {"gen": w.gen, "pick": True})
check("3D: and then given up", w.pick is None and w.detail is None)
w.pick = {"x": 5, "y": 5, "tries": 0, "gen": w.gen, "t": time.time() - 2 * gui._PICK_S}
check("3D: a lost pick frees the hover", not w._picking())
h9.idle()

# another beam during a detail: the view and Back stay, the old section goes
t5 = w.last["tiles"][4]
w.pick = {"x": t5["anchor"][0], "y": t5["anchor"][1], "tries": 0, "gen": w.gen, "t": time.time()}
w._on_pick({"frame": w.painted, "number": 5, "s": t5["s"], "hit": False}, {"gen": w.gen, "pick": True})
h9.idle()
w.shared_check.Checked = True
h9.idle()
w.source_box.SelectedIndex = 3          # Half: another beam
w._apply()
check("another beam in the detail: one section, Back stays, no old title or scale",
      h9.pump(lambda: w.chain and near(w.chain["length"], 1.0, 1e-9)) and h9.idle() and
      not w.sheet_check.Checked and w.detail is not None and w.detail_title.Text == u"" and
      w.detail["camera"] is None and peek(w)["scale"] is None and w.camera is None,
      (w.detail_title.Text, peek(w)["scale"]))
w.on_back()
check("another beam, then Back: its sections in 3D, default view", h9.idle() and w.sheet_check.Checked and
      w.mode3d and w.last.get("mode") == "3d" and w.last["n"] == 6 and
      w.magnify_slider.Value == 11, w.last.get("n"))
w.source_box.SelectedIndex = 1
w._apply()
h9.pump(lambda: w.chain and near(w.chain["length"], 2.0, 1e-9))
h9.idle()

# several sections chosen by hand ends the detail without restoring
w.radio_2d.Checked = True
h9.idle()
t1 = w.last["tiles"][0]
w.on_canvas_double(t1["x"] + 5, t1["y"] + t1["h"] // 2)
h9.idle()
w.sheet_count.Value = 3
w.sheet_check.Checked = True
check("a hand-made sheet ends the detail, no restore", h9.idle() and w.detail is None and
      w.last["n"] == 3 and not w.mode3d)
check("2D chosen: kept as a setting", bu.settings().get("sheet_view") == "2d")
w.sheet_count.Value = 6
w.radio_3d.Checked = True
h9.idle()
# a resize keeps the camera
w.on_canvas_press(200, 150, "left")
w.on_canvas_move(230, 150, "left")
w.on_canvas_release()
h9.idle()
kept_cam = dict(w.camera)
w.form.ClientSize = h9.D.Size(1150, 780)
check("3D: a resize keeps the camera", h9.idle() and same_camera(w.last["camera"], kept_cam) and
      w.canvas.dib[1:] == (w.canvas.ClientSize.Width, w.canvas.ClientSize.Height))
w.magnify_used = None
w.magnify_slider.Value = 0
check("3D: automatic without a frame yet says Auto", w._magnify_text() == u"Auto")
h9.close()


def tool_boxes(w):
    """Boxes of the parts of the row above the picture that show."""
    sheet, three, detail = w.tool_parts
    shown = ([w.radio_2d, w.radio_3d] if sheet else []) + \
        ([w.fit_button, w.magnify_label, w.magnify_slider, w.magnify_value] if three else []) + \
        ([w.detail_title, w.shared_check, w.back_button] if detail else [])
    return [(c.Left, c.Top, c.Width, c.Height) for c in shown]


for scale in (1.0, 1.5):
    for lang in ("en", "nl"):
        bu.settings()["language"] = lang
        hl = gui_cap.Harness(REPO, scale=scale)
        hl.mw.selection = []
        w = hl.window
        w.form.Size = w.form.MinimumSize
        w.start()
        opened(hl)
        w.sheet_check.Checked = True
        hl.idle()
        name = "row at {0}, {1}, smallest window".format(scale, lang)
        for what in ("3D", "detail"):
            boxes = tool_boxes(w)
            width = w.tools.ClientSize.Width
            check(name + ", " + what + ": nothing overlaps, all inside", boxes and not any(
                gui._overlap(a, b) for k, a in enumerate(boxes) for b in boxes[k + 1:]) and
                all(b[0] >= 0 and b[0] + b[2] <= width for b in boxes), (boxes, width))
            if what == "3D":
                a = w.last["tiles"][0]["anchor"]
                w.on_canvas_double(int(a[0]), int(a[1]))
                hl.pump(lambda: w.detail is not None, 5.0)
                hl.idle()
        hl.close()
bu.settings()["language"] = "en"
bu.save_setting("sheet_view", "2d")

# ---------------------------------------------------------------- one window per Mecway
a = gui.claim_instance()
b = gui.claim_instance()
check("one window per process", a is not None and a is not True and b is None)
check("no window to bring forward here", gui.activate_existing() is False)
gui.release_instance(a)
c = gui.claim_instance()
check("free again after closing", c is not None)
gui.release_instance(c)

# ------------------------------------------------- the temporary element on its own
MARKER = bu.MARKER_PREFIX
PLACE = {"point": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0], "half": 0.1}


def vec(x, y, z):
    return (x, y, z)


fake = gui_cap.FakeMw(u"x.liml")
clean = fake.trace()
bmw.show_marker(fake, vec, PLACE)
check("marker: four nodes, one quad4, own component and material",
      fake.trace()[:2] == (15, 11) and MARKER in fake.comps and MARKER in fake.mats)
check("marker: counts", bmw.marker_counts(fake) == (4, 1) and
      bmw.beam_elements(fake, u"Beam")["n_nodes"] == 11)
check("marker: soft plate without density", fake.mats[MARKER]["types"]["geometric"] == "Plate" and
      "density" not in fake.mats[MARKER]["types"] and fake.mats[MARKER]["props"]["youngsmodulus"] == 1.0e3)
beam_points = list(fake.points[:11])
fake.delete_nodes([1])       # the user deletes something: everything renumbers
beam_points = list(fake.points[:10])
moved = dict(PLACE)
moved["point"] = [1.5, 0.0, 0.0]
check("marker moves by what is in the model now", bmw.move_marker(fake, moved) and
      fake.points[:10] == beam_points and fake.points[-1][0] == 1.5, fake.points[-1])
bmw.remove_marker(fake)
check("removed after a renumbering", MARKER not in fake.comps and MARKER not in fake.mats and
      len(fake.points) == 10)
check("move without a marker says so", bmw.move_marker(fake, PLACE) is False)

fake = gui_cap.FakeMw(u"x.liml")
clean = fake.trace()
fake.fail_element = True
try:
    bmw.show_marker(fake, vec, PLACE)
    check("a failure is raised", False)
except RuntimeError:
    check("a failure rolls back everything", fake.trace() == clean, fake.trace())
fake.fail_element = False

# a leftover in the middle of the numbers, then the beam still reads right
fake = gui_cap.FakeMw(u"x.liml")
bmw.show_marker(fake, vec, PLACE)
ours = fake.elems.pop()
fake.elems.insert(3, ours)
left = bmw.clear_leftover(fake)
check("leftover in the middle removed", left == {"found": True, "removed": True} and
      fake.trace()[:2] == (11, 10), fake.trace())
check("the beam is whole and renumbered", bmw.beam_elements(fake, u"Beam")["ids"] == list(range(1, 11)) and
      [e["nodes"] for e in fake.elems[:2]] == [[1, 2], [2, 3]])
check("nothing to clear next time", bmw.clear_leftover(fake) == {"found": False, "removed": True})

# what looks like ours and is not
fake = gui_cap.FakeMw(u"x.liml")
fake.comps[MARKER] = "U"
fake.elems.append({"shape": "line2", "nodes": [1, 2], "comp": MARKER})
own = fake.new_material(MARKER)
fake.set_material_type(own, "geometric", "RectBar")
keep = fake.trace()
check("a user's look-alike is not ours", bmw.marker_counts(fake) == (0, 0) and bmw.remove_marker(fake))
check("it stays", fake.trace() == keep, fake.trace())
bmw.show_marker(fake, vec, PLACE)
shown_as = u"{0}(2)".format(MARKER)
check("ours takes the next name", shown_as in fake.comps and bmw.marker_counts(fake) == (4, 1))
bmw.remove_marker(fake)
check("only ours goes", fake.trace() == keep, fake.trace())

# closing
fake = gui_cap.FakeMw(u"x.liml")
clean = fake.trace()


class Closed(object):
    switched = True


bmw.show_marker(fake, vec, PLACE)
gui.after_window(fake, Closed())
check("after the window: no trace, solution view", fake.trace() == clean and fake.view == "solution")
fake.view = "model"
gui.after_window(fake, None)
check("a window that never came: the view stays", fake.view == "model")
Closed.switched = False
bmw.show_marker(fake, vec, PLACE)
fake.view = "model"
gui.after_window(fake, Closed())
check("a view the window did not take stays", fake.view == "model" and fake.trace() == clean)


class Broken(gui_cap.FakeMw):
    def components(self):
        raise RuntimeError("mw is gone")


check("after the window never raises", gui.after_window(Broken(u"x"), None) is None and
      bmw.remove_marker(Broken(u"x")) is False)

# a crash: the next window cleans up and says so
h6 = gui_cap.Harness(REPO)
clean = h6.mw.trace()
bmw.show_marker(h6.mw, vec, PLACE)
w = h6.window
w.start()
check("the next window removes a leftover", opened(h6) and h6.mw.trace() == clean, h6.mw.trace())
check("and says so", u"earlier time" in w.warn_box.Text, w.warn_box.Text[:60])
# close with the option on
h6.idle()
w.marker_check.Checked = True
h6.pump(lambda: w.marker_on)
w._on_closing(None, None)
w.shutdown()
gui.after_window(h6.mw, w)
check("close with the option on: no trace, solution view", h6.mw.trace() == clean and
      h6.mw.view == "solution", h6.mw.trace())
w.form.Dispose()

# ------------------------------------------------------ the write button with a solve
hw = gui_cap.Harness(REPO)
w = hw.window
fm, fs = hw.mw, hw.solution
state = {"extra": 3, "solves": 0, "during": None, "asked": [], "answer": "No", "raise": None,
         "gone": False}


def fake_all_nodes():
    return list(range(1, len(fm.all_nodes()) + 1 + state["extra"]))


def fake_set(name, values, **kw):
    state.setdefault("written", {})[name] = list(values)


def fake_solve():
    state["solves"] += 1
    state["during"] = [c.Enabled for c in (w.write_button, w.refresh_button, w.source_box,
                                           w.result_box, w.marker_check)]
    if state["raise"]:
        raise ValueError(state["raise"])
    state["extra"] = 0
    fs.force *= 2.0
    if state["gone"]:
        fs.mode_list = [1]


def fake_ask(text):
    state["asked"].append(text)
    return w.F.DialogResult.Yes if state["answer"] == "Yes" else w.F.DialogResult.No


fs.all_nodes = fake_all_nodes
fs.set_variable = fake_set
fm.solve = fake_solve
w._ask = fake_ask
w.start()
check("write flow: a beam opens", hw.pump(lambda: w.opened and w.painted > 0, 30.0))
w.source_box.SelectedIndex = 1
w._apply()


def settled():
    """The beam is open and nothing is under way."""
    hw.pump(lambda: w.opened and not w.solving and not w.tickets and
            u"Computing" not in w.status_label.Text and u"Reading" not in w.status_label.Text, 30.0)
    hw.idle()


settled()


def press():
    w._on_write()
    settled()


press()
check("no: one question, with sizes, solver and what it replaces", len(state["asked"]) == 1 and
      u"14 nodes" in state["asked"][0] and u"Mecway internal" in state["asked"][0] and
      u"replaced" in state["asked"][0], state["asked"])
check("no: no solve, nothing written", state["solves"] == 0 and "written" not in state and
      u"Nothing written" in w.status_label.Text, w.status_label.Text)

# a ticket waiting while the question stands: it keeps its timer
class Never(object):
    def WaitOne(self, ms):
        return False


waiting = ({"done": Never()}, lambda r: None, None)
w.tickets.append(waiting)
real_ask = w._ask


def ask_inside_poll(text):
    w._poll()
    return real_ask(text)


w._ask = ask_inside_poll
w.timer.Start()
press_box = w.caller.post(lambda: bmw.write_results(fm, fs, {"sectionsigma": {}}, {}))
w.tickets.append((press_box, lambda out: w._on_written(out, False, w.result_items[0]), None))
w._poll()
check("a poll under the question leaves the waiting ticket its timer", waiting in w.tickets and
      w.timer.Enabled, (len(w.tickets), w.timer.Enabled))
w.tickets.remove(waiting)
w._ask = real_ask
hw.idle()

state["answer"] = "Yes"
seen = []
real_apply = w._apply


def apply_seen():
    seen.append((w.solving, w.write_button.Enabled, w.source_box.Enabled))
    real_apply()


w._apply = apply_seen
before_force = fs.force
press()
w._apply = real_apply
check("yes: one solve", state["solves"] == 1, state["solves"])
check("yes: the panel off during the solve", state["during"] == [False] * 5, state["during"])
check("yes: still off when the new solution is read", seen[:1] == [(True, False, False)], seen)
check("yes: free again afterwards", not w.solving and w.write_button.Enabled and
      w.source_box.Enabled and w.result_box.Enabled and w.marker_check.Enabled)
hw.idle()
w.slider.Value = 0
w._on_slider()
hw.idle()
root = state.get("written", {}).get("sectionvonmises", [0.0])[0]
check("yes: written from the forces after the solve", near(root, w.last["outside"]["value"], 1e-6) and
      fs.force == 2.0 * before_force, (root, w.last["outside"]["value"]))

state["extra"] = 2
state["raise"] = "no solve today"
press()
check("a solve that fails: says so and frees the panel", u"no solve today" in w.status_label.Text and
      not w.solving and w.write_button.Enabled, w.status_label.Text)
state["raise"] = None

state["extra"] = 2
state["gone"] = True
fs.mode_list = [1, 2]
w._refresh()
settled()
w.updating = True
w.result_box.SelectedIndex = w.result_box.Items.Count - 1
w.updating = False
w._apply()
settled()
state.pop("written", None)
press()
check("the result gone after the solve: says so, writes nothing", u"no longer exists" in w.status_label.Text and
      "written" not in state and not w.solving, w.status_label.Text)
fs.mode_list = []
state["gone"] = False
w._refresh()
settled()

w._busy(True)
w._refresh()
check("read again ends a waiting write", not w.solving and w.write_button.Enabled)
settled()
w._busy(True)
w._link_failed({"code": "x"})
check("a dead worker ends a waiting write", not w.solving and w.write_button.Enabled)
w._refresh()
settled()

state["extra"] = 2
state["asked"] = []
w.warn_start = [{"code": "marker_stuck"}]
press()
check("a stuck marker: no question", state["asked"] == [] and u"Switch off" in w.status_label.Text,
      w.status_label.Text)
w.warn_start = []
w.head["shells"] = ["Doorsnede"]
press()
check("shells: no question, names them and the file", state["asked"] == [] and
      u"Doorsnede" in w.status_label.Text and u"saved file" in w.status_label.Text, w.status_label.Text)
w.head["shells"] = []
w.head["aster"] = True
press()
check("Code-Aster: no question", state["asked"] == [] and u"Code-Aster" in w.status_label.Text,
      w.status_label.Text)
w.head["aster"] = False
w._busy(True)
w.sheet_check.Checked = True
w.sheet_check.Checked = False
check("several sections cannot free the marker during a solve", not w.marker_check.Enabled)
w._busy(False)
w._on_written({"refused": "text", "written": ["sectionsigma"], "solution": 1, "model": 1}, False, {})
check("a refusal halfway says what was written", u"Partly written: sectionsigma" in w.status_label.Text,
      w.status_label.Text)
w._ask = real_ask
hw.close()

# ------------------------------------- selections made after the last save
# Measured on Mecway 35 (tests/mecway/e2e.py, case selections): named_selection gives
# ints for a node selection and for an element selection alike, FaceIds for faces.
hs = gui_cap.Harness(REPO)
w = hs.window
hs.mw.selection = []
hs.mw.unsaved = {u"A fresh": [1, 2, 3], u"B fresh faces": [gui_cap.FaceId(4), gui_cap.FaceId(5)]}
asked = []
real_elements = bmw.beam_elements


def counting_elements(mw, key):
    asked.append(key)
    return real_elements(mw, key)


bmw.beam_elements = counting_elements
w.start()
check("unsaved: a picture arrives", opened(hs))


def source(name):
    return [s for s in w.source_items if s["name"] == name][0]


def labels():
    return [w.source_box.Items[i] for i in range(w.source_box.Items.Count)]


check("unsaved int selection: uncertain", source(u"A fresh").get("uncertain") == "unsaved",
      source(u"A fresh"))
check("unsaved face selection: certain by the API", not source(u"B fresh faces").get("uncertain"))
check("file's element selection: certain", not source(u"Beam").get("uncertain"))
check("file's node selection still left out", u"Tip" not in [s["name"] for s in w.source_items])
check("the list says not saved yet", u"A fresh (not saved yet)" in labels(), labels())
check("the first choice skips the uncertain one", w.source_box.Text.startswith(u"B fresh faces"),
      w.source_box.Text)
check("an unsaved face selection opens", u"A fresh" not in asked and
      hs.pump(lambda: w.chain and near(w.chain["length"], 0.4, 1e-9)), w.chain and w.chain["length"])
w._on_language("nl")
check("the label in Dutch", u"A fresh (nog niet opgeslagen)" in labels(), labels())
w._on_language("en")
key_before = w.opened_key
del asked[:]
w.updating = True
w.source_box.SelectedIndex = w.source_items.index(source(u"A fresh"))
w.updating = False
w._apply()
hs.pump(lambda: False, 0.5)
check("uncertain: no elements read", asked == [], asked)
check("uncertain: says why, with the name", u"A fresh" in w.warn_box.Text and
      u"Save the model" in w.warn_box.Text, w.warn_box.Text)
check("uncertain: no picture", not w.opened and u"Save the model" in w.status_label.Text,
      w.status_label.Text)
check("uncertain: the picture is cleared", not w.opened and w.opened_key is None, w.opened_key)


def choose(name):
    w.updating = True
    w.source_box.SelectedIndex = w.source_items.index(source(name))
    w.updating = False
    w._apply()


# before the head of the file is read, a named int selection waits for it
w.head = None
choose(u"Beam")
hs.pump(lambda: False, 0.3)
check("no head yet: waits for the file", asked == [] and u"Reading the file" in w.status_label.Text,
      (asked, w.status_label.Text))
w._refresh()
check("after reading again it opens", hs.pump(lambda: w.opened and u"Beam" in asked, 20.0), asked)
hs.idle()
# Read again: until the new head has come, the old one does not decide
w._refresh()
check("read again: the head is stale until the scan", w.head_stale)
check("the scan clears it", hs.pump(lambda: not w.head_stale, 20.0))
hs.idle()
w.head_stale = True             # the moment between the lists and the scan
del asked[:]
choose(u"Half")
hs.pump(lambda: False, 0.3)
check("stale head: waits for the file", asked == [] and u"Reading the file" in w.status_label.Text,
      (asked, w.status_label.Text))
w._refresh()
check("after the new head it opens", hs.pump(lambda: not w.head_stale and w.opened and
                                             u"Half" in asked, 20.0), asked)
hs.idle()
# a file that cannot be read: said so, not "not saved yet"
w.head = dict(w.head, element_selections=None)
w._merge_head()
w._fill_sources()
check("unreadable file: the int selections are uncertain for that reason",
      source(u"Beam").get("uncertain") == "unreadable" and u"Beam (file not readable)" in labels(),
      labels())
check("unreadable file: faces still decided by the API", not source(u"B fresh faces").get("uncertain"))
del asked[:]
choose(u"Beam")
check("unreadable file: nothing read, the reason shown", asked == [] and
      u"cannot be read" in w.warn_box.Text and u"Beam" in w.warn_box.Text, w.warn_box.Text)
bmw.beam_elements = real_elements
hs.close()

# nothing usable but an unsaved selection: the hint names saving
hu = gui_cap.Harness(REPO)
hu.mw.selection = []
hu.mw.unsaved = {u"Only": [1, 2]}
hu.mw.named_selections = lambda: [u"Only"]
hu.window.start()
check("only an unsaved selection: the hint names saving",
      hu.pump(lambda: u"save the model" in hu.window.status_label.Text, 20.0),
      hu.window.status_label.Text)
hu.close()

# ---------------------------------------------------------- beam_mecway on its own


class Solids(gui_cap.FakeMw):
    def selected_elements(self):
        return [1, 2]

    def element_shape(self, eid):
        return "hex8"


check("a selection without line elements counts zero", bmw.sources(Solids(u"x"))[0]["count"] == 0)
fake = gui_cap.FakeMw(u"x.liml")
fake.selection = []


class Faces(gui_cap.FakeMw):
    def selected_faces(self):
        return [gui_cap.FaceId(4), gui_cap.FaceId(4), gui_cap.FaceId(2)]


check("selected faces become elements", bmw.current_selection(Faces(u"x")) == [2, 4])
info = bmw.beam_elements(fake, u"Beam faces")
check("face selection: elements, counts", info["ids"] == list(range(1, 11)) and info["n_nodes"] == 11)
check("no solution: no result sets", bmw.result_sets(None) is None)
base = [{"kind": "active", "number": None, "value": None, "time_step": None, "mode": None,
         "configuration": None}]
check("no configurations: the results as they are", bmw.with_configurations(base, {}) == base)
one = bmw.with_configurations(base, {"configurations": ["A"]})
check("one configuration: named and switched", [(r["configuration"], r["switch"]) for r in one] ==
      [("A", True)])
two = bmw.with_configurations(base * 2, {"configurations": ["A", "B"]})
check("two configurations: the product", [r["configuration"] for r in two] == ["A", "A", "B", "B"])
check("the base results stay untouched", base[0]["configuration"] is None)
fake.configuration = None
bmw.activate(fake, base[0])
check("no configuration: nothing switched", fake.configuration is None)
bmw.activate(fake, one[0])
check("a configuration: switched", fake.configuration == "A")


class Recorder(object):
    def __init__(self):
        self.calls = []

    def set_active_configuration(self, name):
        self.calls.append(name)

    def solve(self):
        self.calls.append("solve")


rec = Recorder()
try:
    bmw.write_results(rec, None, {}, two[2])
except Exception:
    pass
check("writing switches first", rec.calls[:1] == ["B"], rec.calls)
rec.calls = []
bmw.solve_again(rec, None, two[2])
check("solving switches first", rec.calls == ["B", "solve"], rec.calls)
sol = gui_cap.FakeSolution(fake)
part = bmw.read_chunk(fake, sol, [1], {"time_step": 2, "mode": None}, "element")
check("time step goes to the API", near(part["raw"]["1"]["tensileforce"][0], 6000.0, 1e-9),
      part["raw"]["1"]["tensileforce"])
check("element ends are k = 1 and n", near(part["raw"]["1"]["bendingmomw"][0], 2000.0 * 1.2 * 2.0, 1e-9) and
      near(part["raw"]["1"]["bendingmomw"][1], 2000.0 * 1.2 * 1.8, 1e-9))
sol.mode = "nothing"
check("no route", bmw.pick_route(fake, sol, 1, {}) is None)

print("{0} failed".format(len(FAILED)) if FAILED else "All checks OK")
sys.exit(1 if FAILED else 0)
