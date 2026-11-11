# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# beam_gui.py
# The window of MecwayAdvancedBeamAnalysis. Compatible with IronPython 2.7.9.
#
# THREADS. Three, and none waits on another:
#   script thread  Mecway's thread: runs every mw call (_MwCaller) and pumps
#                  Mecway's messages. The window posts a function and polls the
#                  ticket with a timer, so a Mecway dialog cannot stop the window.
#   window thread  own STA thread with its own message loop (Application.Run),
#                  as in a separate Mecway tool: with the window on Mecway's
#                  thread, clicks stopped working after a box selection in
#                  the viewport.
#   link thread    owns the pipe to the worker (_Link): one request at a time,
#                  blocking reads, the reply goes to the window by BeginInvoke.
#
# FRAMES. A frame request is the complete state of the window; RequestSlot keeps
# only the newest. The link thread reads the pixels into its back buffer and
# waits for the window's ack before the next request, so frames cannot pile up
# and the buffer on screen is never overwritten. The link thread owns both
# byte arrays; the window pins one only inside OnPaint.
#
# CLOSING. FormClosing sets the stop event and closes stdin, which ends the
# worker (it also ends when Mecway disappears). The window thread then waits at
# most 1.5 s and kills the worker's own process, once more in the `finally` of
# run_window.
#
# SCALING. Layout in device pixels with an own factor (dots per inch / 96), and
# the picture is rendered on device pixels: width and height of a frame are
# the size of the canvas.
#
# SEVERAL SECTIONS. A frame request with "sheet" gives one composite picture of
# canvas size, so pipe, buffers and painting do not change; the worker holds the
# one colour scale. Text on the tiles is drawn here, after the DIB. The sections are
# numbered 1..n along the chain; tile, side view and hover use the number of the worker.
#
# 3D AND DETAIL. With several sections a row above the picture chooses 2D or 3D
# (kept as setting "sheet_view"). In 3D the request has "mode": "3d", "camera" (the
# dict of the last reply; absent: the default view), "z_top" (Mecway's up axis for
# the default view, read from Mecway's settings only when there is no camera to
# send, see beam_utils.UpAxis) and "magnify" (absent: automatic). The window
# changes only azimuth and elevation (degrees), zoom (a
# factor) and pan (device pixels, x right, y down) and leaves the rest of the
# dict as the worker gave it; the worker normalises. Zoom towards the mouse
# assumes screen = canvas centre + pan + zoom * projection. The wheel works only
# after a click in the picture, and a drag starts only after a few pixels, so a
# double click does not rotate. Numbers, axis labels and the magnification text
# are drawn here at the anchors of the reply. A double click on a section opens it
# alone (detail) at its exact s; Back restores the saved state. The shared scale
# of the detail goes through the "scale" of the request and never touches the
# user's own fixed scale.
#
# TABS AND LANGUAGE. A native TabControl: the first tab holds the whole view (its
# controls lie out on that page's size), the second the manual (beam_docs) in a
# browser element, made and loaded only when the user opens that tab, and again
# after a language change. Switching tabs sends nothing to the worker: frames go on
# arriving and are put on the hidden canvas. A page hidden during a resize gets
# its new size, and so a new frame, when it shows again. The two small buttons NL
# and ENG stand over the right end of the tab row, on both tabs; the active one
# is filled. Links in the manual never navigate the browser element: anchors
# scroll, the other manual switches the language, web addresses open in the
# user's own browser.
#
# POSITION IN MECWAY. Optional, a temporary element (beam_mecway). It moves 300 ms
# after the last slider step: a slider moves by mouse, keyboard or wheel, and
# Mecway's display may take 0.5 s. It is removed at the start (a crash), when the
# option goes off, and in after_window, on the script thread, once the window
# thread has ended. After closing the view goes to the solution when this window
# left it; the active view cannot be read.

import ctypes
import json
import os
import threading
import time
import traceback

import beam_docs as bdoc
import beam_mecway as bmw
import beam_utils as bu
from beam_utils import log

# Also how a second start finds the open window (find_panel_window)
PANEL_TITLE = bu.TOOL + u" " + bu.VERSION
_INSTANCE_NAME = u"Local\\MecwayAdvancedBeamAnalysis_{0}"
_POLL_MS = 30
_SLIDER_MAX = 1000
_SW_RESTORE = 9
_SWP_FLAGS = 0x0001 | 0x0002 | 0x0010     # NOSIZE | NOMOVE | NOACTIVATE
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_MAX_ZOOM = 400.0
_MAX_TIMES = 20000
_MARKER_MS = 300
# A request open this long counts as a hung worker when the user reads again.
_HUNG_S = 60.0
# 3D: degrees of rotation per pixel of drag (at a layout factor of 1; the same as
# beam_render3d.DEG_PER_PX), the steps of the magnify slider (0 = automatic,
# 1..steps = 1x..100x on a log scale), the radius for a double click beside a
# section, and how long a double click in 3D waits for its probe. The zoom of the
# camera is pixels per metre; its range is the worker's.
_ROT_DEG = 0.4
_MAG_STEPS = 40
_MAG_MAX = 100.0
_PICK_PX = 16
_PICK_S = 2.0


def instance_busy_text():
    return bu.tr(u"Het venster is al open.", u"The window is already open.")


# ==========================================
# ONE WINDOW PER MECWAY PROCESS (as in a separate Mecway tool)
# ==========================================
def claim_instance():
    """Token for release_instance(), True without .NET, or None when the
    window is already open in this Mecway process."""
    try:
        from System.Threading import Semaphore
        from System.Diagnostics import Process
        name = _INSTANCE_NAME.format(Process.GetCurrentProcess().Id)
    except Exception:
        return True
    semaphore = Semaphore(1, 1, name)
    if not semaphore.WaitOne(0):
        semaphore.Dispose()
        return None
    return semaphore


def release_instance(token):
    if token is None or token is True:
        return
    try:
        token.Release()
    finally:
        token.Dispose()


def _user32():
    user32 = ctypes.windll.user32
    user32.FindWindowExW.restype = ctypes.c_void_p
    user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.IsIconic.argtypes = [ctypes.c_void_p]
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    return user32


def find_panel_window():
    """Handle of the open window in this process, or None. By title through
    Win32: a second start may run in a fresh script engine without Windows
    Forms, and module variables do not survive (scripting-api.md par. 1)."""
    try:
        user32 = _user32()
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        hwnd = None
        while True:
            hwnd = user32.FindWindowExW(None, hwnd, None, PANEL_TITLE)
            if not hwnd:
                return None
            owner = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                return hwnd
    except Exception:
        return None


def activate_existing():
    """Bring the open window to the front; only for a second start, which is
    the user's own click in Mecway. Returns True when the window was found."""
    hwnd = find_panel_window()
    if not hwnd:
        return False
    try:
        user32 = _user32()
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)
        user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_FLAGS)
        user32.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, _SWP_FLAGS)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    return True


# ==========================================
# MW CALLS FROM THE WINDOW THREAD (as in a separate Mecway tool)
# ==========================================
class _MwCaller(object):
    """The window hands a function that uses mw to the script thread without
    waiting; post() gives a ticket for result(). direct=True: call at once
    (tests, without a script thread)."""

    def __init__(self, direct=False):
        self.direct = direct
        self._lock = threading.Lock()
        self._queue = []

    def post(self, fn):
        if self.direct:
            box = {"done": None}
            try:
                box["result"] = fn()
            except Exception:
                box["error"] = traceback.format_exc()
            return box
        from System.Threading import ManualResetEvent
        box = {"fn": fn, "done": ManualResetEvent(False)}
        with self._lock:
            self._queue.append(box)
        return box

    def result(self, box):
        """(done, result, error text) of a posted request; never waits."""
        if box["done"] is not None and not box["done"].WaitOne(0):
            return False, None, None
        return True, box.get("result"), box.get("error")

    def serve_once(self):
        with self._lock:
            box = self._queue.pop(0) if self._queue else None
        if box is None:
            return
        try:
            box["result"] = box["fn"]()
        except Exception:
            box["error"] = traceback.format_exc()
            log("ERROR: mw call: {0}".format(box["error"]))
        box["done"].Set()

    def serve(self, thread, pump=None):
        """Script thread: run requests and pump Mecway's messages until the
        window thread ends."""
        while thread.IsAlive:
            self.serve_once()
            if pump is not None:
                pump()
            thread.Join(10)
        self.serve_once()


def after_window(mw, window):
    """Script thread, the window thread has ended: no temporary element stays,
    and the view returns if the window took it. Never raises."""
    try:
        bmw.remove_marker(mw)
        if window is not None and window.switched:
            bmw.solution_view(mw)
    except Exception:
        log("ERROR: after the window: {0}".format(traceback.format_exc()))


def run_window(mw, solution, vector=None):
    """Show the window until the user closes it. vector: Mecway's Vector, which
    exists only in the main script."""
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Threading import Thread, ThreadStart, ApartmentState
        from System.Windows.Forms import Application
    except Exception as e:
        raise ImportError(u"Windows Forms: {0}".format(e))
    state = {"error": None, "window": None}
    caller = _MwCaller(False)
    log(u"INFO: {0} started".format(PANEL_TITLE))
    # on the script thread, once: .NET reads the whole configuration for it
    up_axis = bu.UpAxis(bmw.config_paths())

    def build_and_run():
        try:
            from System.Windows.Forms import Application as App
            state["window"] = _Window(mw, solution, caller, vector=vector, up_axis=up_axis)
            App.Run(state["window"].form)
        except Exception:
            state["error"] = traceback.format_exc()
        finally:
            if state["window"] is not None:
                state["window"].shutdown()

    thread = Thread(ThreadStart(build_and_run))
    thread.SetApartmentState(ApartmentState.STA)
    thread.IsBackground = True
    thread.Start()
    on_ui_thread = (Thread.CurrentThread.GetApartmentState() ==
                    ApartmentState.STA)
    try:
        caller.serve(thread, Application.DoEvents if on_ui_thread else None)
    finally:
        if state["window"] is not None:
            state["window"].shutdown()
        after_window(mw, state["window"])
    if state["error"]:
        raise RuntimeError(state["error"])


# ==========================================
# THE LINK TO THE WORKER
# ==========================================
class _Link(object):
    """Owns the worker process and the pipe, on a thread of its own. Events go
    to on_event(kind, data) on that thread: "starting", "no_python", "hello",
    "failed" (the worker could not start or died), "reply"."""

    def __init__(self, on_event, worker=None):
        from System.Threading import AutoResetEvent, ManualResetEvent
        self.on_event = on_event
        self.worker = worker or os.path.join(bu.SCRIPTS_DIR, "beam_worker.py")
        self.slot = bu.RequestSlot()
        self.proc = None
        self.stdin = None
        self.stdout = None
        self.stopping = False
        self.alive = False
        self.thread = None
        self.busy_since = None
        self._wake = AutoResetEvent(False)
        self._ack = AutoResetEvent(False)
        self._stop = ManualResetEvent(False)
        self._buffers = [None, None]
        self._back = 0
        self._json = None
        self._seq = 0
        self._finished = False

    # ---- any thread ----
    def start(self):
        from System.Threading import Thread, ThreadStart
        self.thread = Thread(ThreadStart(self._run))
        self.thread.IsBackground = True
        self.thread.Start()

    def control(self, request):
        self.slot.put_control(request)
        self._wake.Set()

    def frame(self, request):
        self.slot.put_frame(request)
        self._wake.Set()

    def probe(self, request):
        self.slot.put_probe(request)
        self._wake.Set()

    def ack(self, used):
        """The window took the frame (used) or dropped it. Only a frame in use
        moves the link to the other buffer."""
        if used:
            self._back = 1 - self._back
        self._ack.Set()

    def stop(self):
        """Ask to stop; never blocks. Closing stdin ends the worker."""
        self.stopping = True
        self._stop.Set()
        stdin, self.stdin = self.stdin, None
        if stdin is not None:
            try:
                stdin.Close()
            except Exception:
                pass

    def kill(self):
        """Stop at once: for a worker that no longer answers. Never blocks."""
        self.stop()
        proc = self.proc
        if proc is not None:
            try:
                proc.Kill()
            except Exception:
                pass

    def hung(self):
        since = self.busy_since
        return since is not None and time.time() - since > _HUNG_S

    def finish(self):
        """Stop and wait, at most 1.5 s, then kill this worker (its own PID
        only). Safe to call twice."""
        self.stop()
        if self._finished:
            return
        self._finished = True
        self._end_process()
        if self.thread is not None:
            try:
                self.thread.Join(2000)
            except Exception:
                pass

    def _end_process(self):
        proc = self.proc
        if proc is not None:
            try:
                if not proc.WaitForExit(1500):
                    proc.Kill()
                    proc.WaitForExit(500)
            except Exception:
                pass

    # ---- link thread ----
    def _launch(self, path):
        from System.Diagnostics import Process, ProcessStartInfo
        info = ProcessStartInfo(path)
        info.Arguments = u'{0} "{1}" "{2}"'.format(
            bu.PYTHON_FLAGS, self.worker,
            os.path.join(bu.work_dir(), bu.WORKER_LOG))
        info.UseShellExecute = False
        info.CreateNoWindow = True
        info.RedirectStandardInput = True
        info.RedirectStandardOutput = True
        self.proc = Process.Start(info)
        try:
            # measured offscreen: with a console in code page 65001, .NET writes a
            # UTF-8 byte order mark into the worker's stdin before any request
            from System import Console
            if Console.InputEncoding.GetPreamble().Length:
                log(u"WARNING: console input encoding {0} puts a byte order mark "
                    u"before the first request".format(Console.InputEncoding.WebName))
        except Exception:
            pass
        self.stdin = self.proc.StandardInput.BaseStream
        self.stdout = self.proc.StandardOutput.BaseStream

    def _recv(self, array, n):
        stream = self.stdout
        got = 0
        while got < n:
            r = stream.Read(array, got, n - got)
            if r <= 0:
                raise IOError("worker closed the pipe")
            got += r

    def _read_reply(self):
        """(seq, reply, pixel array, width, height)."""
        from System import Array, BitConverter, Byte
        from System.Text import Encoding
        size = bu.REPLY.size
        if self._json is None:
            self._json = Array.CreateInstance(Byte, 65536)
        self._recv(self._json, size)
        h = self._json
        magic, seq, n_json, width, height, n_pixels = [
            int(BitConverter.ToUInt32(h, i)) for i in range(0, size, 4)]
        if not bu.valid_reply_head(magic, n_json, width, height, n_pixels):
            raise IOError("bad reply header")
        if n_json > self._json.Length:
            self._json = Array.CreateInstance(Byte, n_json)
        self._recv(self._json, n_json)
        reply = json.loads(Encoding.UTF8.GetString(self._json, 0, n_json))
        if not n_pixels:
            return seq, reply, None, 0, 0
        k = self._back
        buf = self._buffers[k]
        if buf is None or buf.Length < n_pixels:
            buf = Array.CreateInstance(Byte, n_pixels)
            self._buffers[k] = buf
        self._recv(buf, n_pixels)
        return seq, reply, buf, width, height

    def _send(self, request):
        from System.Text import Encoding
        self._seq += 1
        data = bu.pack_request(self._seq, request)
        array = Encoding.GetEncoding(28591).GetBytes(data)
        stream = self.stdin
        if stream is None:
            raise IOError("stopping")
        stream.Write(array, 0, array.Length)
        stream.Flush()

    def _exchange(self, request):
        from System import Array
        from System.Diagnostics import Stopwatch
        from System.Threading import WaitHandle
        t0 = Stopwatch.GetTimestamp()
        self.busy_since = time.time()
        self._send(request)
        seq, reply, array, width, height = self._read_reply()
        self.busy_since = None
        if seq != self._seq:
            raise IOError("reply out of step")
        ticks = float(Stopwatch.GetTimestamp() - t0) * 1000.0 / Stopwatch.Frequency
        self.on_event("reply", {"request": request, "reply": reply,
                                "array": array, "width": width, "height": height,
                                "ms": ticks})
        if array is not None:
            WaitHandle.WaitAny(Array[WaitHandle]([self._ack, self._stop]))

    def _run(self):
        from System import Array
        from System.Threading import WaitHandle
        try:
            found = bu.find_python()
            if not found["path"]:
                self.on_event("no_python", found)
                return
            self.on_event("starting", found["path"])
            try:
                self._launch(found["path"])
            except Exception:
                bu.forget_python()
                found = bu.find_python(refresh=True)
                if not found["path"]:
                    self.on_event("no_python", found)
                    return
                self._launch(found["path"])
            if self.stopping:
                return
            _, hello, _, _, _ = self._read_reply()
            if not hello.get("ok"):
                self.on_event("failed", hello)
                return
            self.alive = True
            self.on_event("hello", hello)
            handles = Array[WaitHandle]([self._wake, self._stop])
            while not self.stopping:
                request = self.slot.take()
                if request is None:
                    WaitHandle.WaitAny(handles)
                else:
                    self._exchange(request)
        except Exception:
            if not self.stopping:
                log("ERROR: worker link: {0}".format(traceback.format_exc()))
                self.kill()     # a garbled worker may still run
                self.on_event("failed", {"code": "worker_died"})
        finally:
            self.alive = False
            if self.stopping:
                # the stop may have come while Python was still being found
                self.stop()
                self._end_process()


# ==========================================
# WINDOW PARTS
# ==========================================
def _forms():
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    import System.Drawing as D
    import System.Windows.Forms as F
    return D, F


def _no_version(self):
    return u""


class _BMI(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def paint_dib(hdc, pointer, width, height, bmi):
    """Measured route: SetDIBitsToDevice took 1 ms where DrawImage took 11 to
    40 ms (research note par. 6.1)."""
    ctypes.windll.gdi32.SetDIBitsToDevice(
        ctypes.c_void_p(hdc), 0, 0, width, height, 0, 0, 0, height,
        ctypes.c_void_p(pointer), ctypes.byref(bmi), 0)


_CLASSES = {}


def _classes():
    """The subclassed controls, made once per module load. Each answers the
    three version properties in Python: IronPython reads them through
    TypeDescriptor and they fail on a class made at run time (found in a
    separate Mecway tool)."""
    if _CLASSES:
        return _CLASSES
    D, F = _forms()
    from System.Runtime.InteropServices import GCHandle, GCHandleType
    left = F.MouseButtons.Left
    buttons = ((F.MouseButtons.Left, "left"), (F.MouseButtons.Right, "right"),
               (F.MouseButtons.Middle, "middle"))
    native = F.FlatStyle.System

    def button_of(e):
        """"left", "right", "middle" or None (no button, or another one)."""
        for flag, name in buttons:
            if (e.Button & flag) == flag:
                return name
        return None

    class Canvas(F.Control):
        """The picture of the worker; mouse events go to the window."""
        CompanyName = property(_no_version)
        ProductName = property(_no_version)
        ProductVersion = property(_no_version)

        def __init__(self):
            self.SetStyle(F.ControlStyles.UserPaint |
                          F.ControlStyles.AllPaintingInWmPaint |
                          F.ControlStyles.Opaque |
                          F.ControlStyles.Selectable, True)
            self.owner = None
            self.dib = None
            self.tiles = None
            self.shown = None       # the reply of the frame on the canvas
            self._bmi = _BMI()
            self._bmi.biSize = ctypes.sizeof(_BMI)
            self._bmi.biPlanes = 1
            self._bmi.biBitCount = 32
            self.paint_ms = 0.0
            self.Cursor = F.Cursors.Cross

        def OnPaint(self, e):
            g = e.Graphics
            size = self.ClientSize
            if self.dib is None:
                g.Clear(D.Color.White)
                return
            array, width, height = self.dib
            white = D.Brushes.White
            if width < size.Width:
                g.FillRectangle(white, width, 0, size.Width - width, size.Height)
            if height < size.Height:
                g.FillRectangle(white, 0, height, size.Width, size.Height - height)
            self._bmi.biWidth = width
            self._bmi.biHeight = -height
            pin = GCHandle.Alloc(array, GCHandleType.Pinned)
            try:
                hdc = g.GetHdc()
                try:
                    paint_dib(hdc.ToInt64(), pin.AddrOfPinnedObject().ToInt64(),
                              width, height, self._bmi)
                finally:
                    g.ReleaseHdc(hdc)
            finally:
                pin.Free()
            if self.owner is not None and (self.tiles or self.shown):
                self.owner.guarded(self.owner.draw_overlay, g)

        def OnMouseMove(self, e):
            F.Control.OnMouseMove(self, e)
            if self.owner is not None:
                self.owner.guarded(self.owner.on_canvas_move, e.X, e.Y,
                                   button_of(e))

        def OnMouseDown(self, e):
            F.Control.OnMouseDown(self, e)
            # the user's own click inside this window: focus for the wheel, and
            # only within the window (never Activate or SetForegroundWindow)
            self.Focus()
            button = button_of(e)
            if self.owner is not None and button is not None:
                self.owner.guarded(self.owner.on_canvas_press, e.X, e.Y, button)

        def OnMouseUp(self, e):
            F.Control.OnMouseUp(self, e)
            if self.owner is not None:
                self.owner.guarded(self.owner.on_canvas_release)

        def OnLostFocus(self, e):
            F.Control.OnLostFocus(self, e)
            if self.owner is not None:
                self.owner.guarded(self.owner.on_canvas_blur)

        def OnMouseLeave(self, e):
            F.Control.OnMouseLeave(self, e)
            if self.owner is not None:
                self.owner.guarded(self.owner.on_canvas_leave)

        def OnMouseWheel(self, e):
            F.Control.OnMouseWheel(self, e)
            if self.owner is not None:
                self.owner.guarded(self.owner.on_canvas_wheel, e.X, e.Y, e.Delta)

        def OnMouseDoubleClick(self, e):
            F.Control.OnMouseDoubleClick(self, e)
            if self.owner is not None and e.Button == left:
                self.owner.guarded(self.owner.on_canvas_double, e.X, e.Y)

    class Drawn(F.Control):
        """A control drawn with GDI+ by a function of the window."""
        CompanyName = property(_no_version)
        ProductName = property(_no_version)
        ProductVersion = property(_no_version)

        def __init__(self):
            self.SetStyle(F.ControlStyles.UserPaint |
                          F.ControlStyles.AllPaintingInWmPaint |
                          F.ControlStyles.OptimizedDoubleBuffer |
                          F.ControlStyles.ResizeRedraw, True)
            self.draw = None

        def OnPaint(self, e):
            if self.draw is not None:
                self.draw(e.Graphics, self.ClientSize.Width,
                          self.ClientSize.Height)

    class SafeButton(F.Button):
        """Clicks on a release inside its own area after a press on it: on a
        panel thread a window of Mecway's thread over the spot can swallow
        the click (found in a separate Mecway tool)."""
        CompanyName = property(_no_version)
        ProductName = property(_no_version)
        ProductVersion = property(_no_version)

        def OnMouseDown(self, e):
            self._down = e.Button == left
            F.Button.OnMouseDown(self, e)

        def OnClick(self, e):
            self._clicked = True
            F.Button.OnClick(self, e)

        def OnMouseUp(self, e):
            down = getattr(self, "_down", False)
            self._down = False
            self._clicked = False
            F.Button.OnMouseUp(self, e)
            if down and not self._clicked and e.Button == left and \
                    self.FlatStyle != native and self.Enabled and \
                    self.ClientRectangle.Contains(e.Location):
                self.OnClick(e)

    _CLASSES.update({"Canvas": Canvas, "Drawn": Drawn, "Button": SafeButton})
    return _CLASSES


def ui_scale():
    """Layout factor of this display: 1.0, 1.25, 1.5 ..."""
    dpi = 96.0
    try:
        from System import IntPtr
        from System.Drawing import Graphics
        g = Graphics.FromHwnd(IntPtr.Zero)
        try:
            dpi = float(g.DpiY)
        finally:
            g.Dispose()
    except Exception:
        pass
    return max(dpi / 96.0, 1.0)


def _overlap(a, b):
    """Do two boxes (left, top, width, height) overlap?"""
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and
            a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def _crosses(box, a, b):
    """Does the segment a-b touch the box (left, top, width, height)? Clipping as by
    Liang and Barsky."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - box[0]), (dx, box[0] + box[2] - a[0]),
                 (-dy, a[1] - box[1]), (dy, box[1] + box[3] - a[1])):
        if p == 0.0:
            if q < 0.0:
                return False
            continue
        t = q / p
        if p < 0.0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def place_labels(dots, sizes, w, h, gap, radius, blocked=(), segments=()):
    """Places for the numbers at the dots of the side view. A number stands above its
    dot if it can, else below, right or left of it, else at the free place nearest to
    the dot, at most gap + radius from it; never over another number, a dot, the line,
    a blocked box or the edge. When not every number finds a place, only 1, the last
    and every k-th are written, the smallest k that fits; the dots stay. When even 1
    and the last do not both fit, only one of them.
    Returns [(index, left, top)], index into dots."""
    n = len(dots)
    dot_boxes = [(x - radius, y - radius, 2.0 * radius, 2.0 * radius) for x, y in dots]
    fixed = dot_boxes + list(blocked)
    step = max(0.25 * gap, 1.0)

    def clear(box):
        if box[0] < 0.0 or box[1] < 0.0 or box[0] + box[2] > w or box[1] + box[3] > h:
            return False
        if any(_overlap(box, other) for other in fixed):
            return False
        wide = (box[0] - 1.0, box[1] - 1.0, box[2] + 2.0, box[3] + 2.0)
        return not any(_crosses(wide, a, b) for a, b in segments)

    def places(i):
        """The free places of one number, the best first; only the others still
        have to be avoided."""
        (x, y), (bw, bh) = dots[i], sizes[i]
        first = [(x - 0.5 * bw, y - gap - bh), (x - 0.5 * bw, y + gap),
                 (x + gap, y - 0.5 * bh), (x - gap - bw, y - 0.5 * bh)]
        near = []
        top = y - gap - bh
        while top <= y + gap:
            left = x - gap - bw
            while left <= x + gap:
                dx = max(left - x, 0.0, x - left - bw)
                dy = max(top - y, 0.0, y - top - bh)
                if dx * dx + dy * dy <= (gap + radius) ** 2:
                    centre = (left + 0.5 * bw - x) ** 2 + (top + 0.5 * bh - y) ** 2
                    near.append((centre, left, top))
                left += step
            top += step
        near.sort()
        out = []
        for left, top in first + [(x0, y0) for _c, x0, y0 in near]:
            # pushed inside the control, then checked like any other
            left = min(max(left, 0.0), w - bw)
            top = min(max(top, 0.0), h - bh)
            box = (left, top, bw, bh)
            if box not in out and clear(box):
                out.append(box)
        return out

    options = [places(i) for i in range(n)]

    def spot(i, taken):
        for box in options[i]:
            if not any(_overlap(box, other) for other in taken):
                return box
        return None

    for k in range(1, n + 1):
        keep = [i for i in range(n) if i % k == 0 or i == n - 1]
        # the number with the fewest places first, so a free one does not take them
        order = sorted(keep, key=lambda i: (len(options[i]), i))
        taken, out = [], []
        for i in order:
            box = spot(i, taken)
            if box is None:
                break
            taken.append(box)
            out.append((i, box[0], box[1]))
        else:
            return sorted(out)
    for i in (0, n - 1)[:n]:
        box = spot(i, [])
        if box is not None:
            return [(i, box[0], box[1])]
    return []


def _median(values):
    s = sorted(values)
    return s[len(s) // 2] if s else 0.0


def _p95(values):
    s = sorted(values)
    return s[min(int(0.95 * len(s)), len(s) - 1)] if s else 0.0


def magnify_of(position):
    """Magnification of a position of the magnify slider; None = automatic."""
    if position <= 0:
        return None
    if _MAG_STEPS <= 1:
        return 1.0
    return _MAG_MAX ** (float(position - 1) / (_MAG_STEPS - 1))


def fmt_times(k):
    """A magnification in two digits, without a trailing .0: 1, 2.5, 12, 100."""
    text = bu._num(k, 2)
    return text[:-2] if text.endswith(u".0") else text


def _pan_of(camera):
    """(x, y) of the pan of a camera dict, a list or a dict with x and y."""
    pan = (camera or {}).get("pan")
    try:
        if isinstance(pan, dict):
            return float(pan.get("x", 0.0)), float(pan.get("y", 0.0))
        if pan:
            return float(pan[0]), float(pan[1])
    except (TypeError, IndexError, ValueError):
        pass
    return 0.0, 0.0


def _with_pan(camera, x, y):
    """The camera with another pan, in the form the worker gave it."""
    out = dict(camera)
    pan = camera.get("pan")
    if isinstance(pan, dict):
        pan = dict(pan)
        pan["x"], pan["y"] = x, y
        out["pan"] = pan
    else:
        out["pan"] = [x, y]
    return out


def _anchor(a):
    """(x, y) of an anchor of the reply, or None."""
    if isinstance(a, dict):
        a = (a.get("x"), a.get("y"))
    try:
        if a is None or a[0] is None or a[1] is None:
            return None
        return float(a[0]), float(a[1])
    except (TypeError, IndexError, KeyError, ValueError):
        return None


def axis_anchors(axes, beyond=0.0):
    """[(label, (x, y))] of the "axes" of a reply: a dict by label (with the origin
    "O" of the axes, as beam_render3d gives it) or a list in the order X, Y, Z;
    missing anchors are left out. beyond: pixels the label stands past the end of
    its axis, away from the origin, so that it does not cover the line."""
    origin = None
    if isinstance(axes, dict):
        items = [(k, axes.get(k, axes.get(k.lower()))) for k in ("X", "Y", "Z")]
        origin = _anchor(axes.get("O"))
    elif isinstance(axes, (list, tuple)):
        items = list(zip(("X", "Y", "Z"), axes))
    else:
        return []
    out = []
    for label, a in items:
        p = _anchor(a)
        if p is None:
            continue
        if origin is not None and beyond:
            dx, dy = p[0] - origin[0], p[1] - origin[1]
            d = (dx * dx + dy * dy) ** 0.5
            if d > 1e-9:
                p = (p[0] + beyond * dx / d, p[1] + beyond * dy / d)
        out.append((label, p))
    return out


LEFT_W = 288
TOOL_H = 28
TILE_BAND = 20
MEASURE_H = 142
MEASURE_MIN = 62
WARN_MIN = 64
LEGEND_W = 150
SIDE_H = 62
SLIDER_H = 30
LINE_H = 22
PAD = 8
TAB_H = 30              # height of the tab headers; the language buttons fit in it
LANG_W, LANG_H = 48, 26  # the buttons NL and ENG, as in MecwaySectionProps
LANGS = (("nl", u"NL"), ("en", u"ENG"))
ACCENT = (0, 120, 212)


class _Window(object):
    """Builds the window and follows it. scale: layout factor for offscreen
    tests (fonts follow it too, as Windows does for a real display); worker: other
    worker script for tests; up_axis: Mecway's up axis (beam_utils.UpAxis), read
    only for a default 3D camera."""

    def __init__(self, mw, solution, caller, scale=None, worker=None, vector=None,
                 up_axis=None):
        self.mw = mw
        self.up_axis = up_axis if up_axis is not None else bu.UpAxis()
        self.vector = vector
        self.solution = solution
        self.caller = caller
        self.worker = worker
        self.link = None
        D, F = _forms()
        self.D, self.F = D, F
        self.classes = _classes()
        self.k = float(scale) if scale else ui_scale()
        self.font_k = self.k if scale else 1.0
        self.texts = []
        self.fonts = {}
        self.tickets = []
        self.inbox = []
        self.inbox_lock = threading.Lock()
        self.gen = 0
        self.frame_no = 0
        self.painted = 0
        self.opened = False
        self.opened_key = None
        self.head = None
        self.head_stale = False         # Read again: the head is of the file before it
        self.file = u""
        self.chain = None
        self.source_items = []
        self.result_items = []
        self.measure_keys = [m[0] for m in bu.MEASURES]
        self.last = None
        self.view = None
        self.press = None
        self.fixed = {}
        self.warn_worker = []
        self.warn_local = []
        self.warn_notes = []
        self.warn_start = []
        self.marker_on = False
        self.marker_name = bu.MARKER_PREFIX
        self.marker_busy = False
        self.marker_next = None
        self.marker_due = False
        self.switched = False
        self.t_start = None
        self.times = []
        self.closed = False
        self.base_results = None
        self.solving = False            # from the user's yes to the write after it
        self.side_labels = []           # [(number, x, y, width, height)], as drawn
        self.tile_texts = []            # the titles of the tiles, as drawn
        self.side_dots = []             # [(number, x, y)] of the side view
        self._label_cache = None        # (geometry, places) of the last numbers
        self.side_blocked = []          # the end texts, ("line", x0, y0, x1, y1) per segment
        self.pending_key = None
        self.updating = False
        self.mode3d = bu.settings().get("sheet_view") == "3d"
        self.camera = None              # 3D camera dict to send; None: the default
        self.magnify = None             # chosen magnification; None: automatic
        self.magnify_used = None        # what the last 3D frame used
        self.drag = None                # 3D drag: press point, button, base camera
        self.wheel_armed = False        # the wheel zooms 3D only after a click here
        self.pick = None                # a double click in 3D waiting for its probe
        self.detail = None              # the saved state of several sections
        self.detail_s = None            # exact s of the detail until the slider moves
        self.beam_key = None            # key of the beam the camera and detail are of
        self.canvas_labels = []         # [(text, x, y, width, height)], as drawn
        self.tools_on = False           # the row above the picture shows
        self.tool_parts = (False, False, False)     # several, 3D, detail
        self._build()

    # ------------------------------------------------------------- helpers
    def t(self, nl, en):
        return bu.tr(nl, en)

    def px(self, v):
        return int(round(v * self.k))

    def guarded(self, fn, *args):
        """Call an event function; an error is logged and the window stays."""
        try:
            return fn(*args)
        except Exception:
            log("ERROR: window: {0}".format(traceback.format_exc()))
            self._status(u"{0}: {1}".format(
                self.t(u"Fout, zie het logboek", u"Error, see the log"),
                traceback.format_exc().strip().split("\n")[-1]), True)

    def _handler(self, fn):
        def handler(sender, args):
            self.guarded(fn)
        return handler

    def _font(self, size=9.0, bold=False):
        """A shared font (drawing makes many, on every frame)."""
        D = self.D
        font = self.fonts.get((size, bold))
        if font is None:
            font = D.Font("Segoe UI", size * self.font_k,
                          D.FontStyle.Bold if bold else D.FontStyle.Regular)
            self.fonts[(size, bold)] = font
        return font

    def _tx(self, setter, nl, en):
        """A static text in both languages; set again on a language change."""
        self.texts.append((setter, nl, en))
        setter(bu.tr(nl, en))

    def _label(self, parent, nl=u"", en=u"", bold=False, color=None):
        D, F = self.D, self.F
        lb = F.Label()
        lb.Font = self._font(9.0, bold)
        lb.AutoEllipsis = True
        lb.TextAlign = D.ContentAlignment.MiddleLeft
        lb.BackColor = D.Color.Transparent
        if color is not None:
            lb.ForeColor = color
        parent.Controls.Add(lb)
        if nl or en:
            self._tx(lambda t, c=lb: setattr(c, "Text", t), nl, en)
        return lb

    def _check(self, parent, nl, en, checked, fn):
        F = self.F
        cb = F.CheckBox()
        cb.FlatStyle = F.FlatStyle.System     # native: no swallowed clicks
        cb.Font = self._font()
        cb.Checked = checked
        parent.Controls.Add(cb)
        self._tx(lambda t, c=cb: setattr(c, "Text", t), nl, en)
        cb.CheckedChanged += self._handler(fn)
        return cb

    def _button(self, parent, nl, en, fn):
        F, D = self.F, self.D
        b = self.classes["Button"]()
        b.FlatStyle = F.FlatStyle.Flat
        b.Font = self._font()
        b.BackColor = D.Color.White
        b.FlatAppearance.BorderColor = D.Color.FromArgb(200, 205, 212)
        b.Click += self._handler(fn)
        parent.Controls.Add(b)
        if nl or en:        # without texts: set by its owner (it holds a number)
            self._tx(lambda t, c=b: setattr(c, "Text", t), nl, en)
        return b

    # --------------------------------------------------------------- build
    def _build(self):
        D, F = self.D, self.F
        form = F.Form()
        form.AutoScaleMode = getattr(F.AutoScaleMode, "None")
        form.Text = PANEL_TITLE
        form.BackColor = D.Color.White
        form.FormBorderStyle = F.FormBorderStyle.Sizable
        form.ShowInTaskbar = True
        form.StartPosition = F.FormStartPosition.CenterScreen
        form.Font = self._font()
        area = F.Screen.PrimaryScreen.WorkingArea
        form.ClientSize = D.Size(min(self.px(1240), area.Width - 40),
                                 min(self.px(840), area.Height - 60))
        self.form = form
        # high enough for the whole left panel with the measure list at its least;
        # the height of the tab row is added once the tabs have a handle
        self._set_minimum(self.px(TAB_H + 4))
        self.timer = F.Timer()
        self.timer.Interval = _POLL_MS
        self.timer.Tick += self._handler(self._poll)
        self.marker_timer = F.Timer()
        self.marker_timer.Interval = _MARKER_MS
        self.marker_timer.Tick += self._handler(self._on_marker_timer)
        tabs = F.TabControl()
        tabs.Font = self._font()
        tabs.ItemSize = D.Size(0, self.px(TAB_H))
        tabs.Padding = D.Point(self.px(12), self.px(4))
        view = F.TabPage()
        manual = F.TabPage()
        for page, nl, en in ((view, u"Weergave", u"View"),
                             (manual, u"Handleiding", u"Manual")):
            page.BackColor = D.Color.White
            page.UseVisualStyleBackColor = False
            page.Padding = F.Padding(0)
            tabs.TabPages.Add(page)
            self._tx(lambda t, c=page: setattr(c, "Text", t), nl, en)
        tabs.Dock = F.DockStyle.Fill
        form.Controls.Add(tabs)
        self.tabs, self.page_view, self.page_doc = tabs, view, manual
        self.doc_viewer = None          # WebBrowser, or a TextBox without one
        self.doc_lang = None            # the language of the manual loaded
        self.doc_loading = False        # DocumentText set, the page not there yet
        self.doc_loads = 0              # how often the manual was loaded (tests)
        self.doc_mode_logged = False
        self.lang_buttons = {}
        x = form.ClientSize.Width - self.px(6 + 2 * LANG_W + 4)
        for code, text in LANGS:
            b = self.classes["Button"]()
            b.FlatStyle = F.FlatStyle.Flat
            b.Font = self._font(9.0, True)
            b.Text = text
            b.Tag = code
            b.Cursor = F.Cursors.Hand
            b.Location = D.Point(x, self.px(2))
            b.Size = D.Size(self.px(LANG_W), self.px(LANG_H))
            b.Anchor = F.AnchorStyles.Top | F.AnchorStyles.Right
            b.Click += self._handler(lambda c=code: self._on_language(c))
            form.Controls.Add(b)
            form.Controls.SetChildIndex(b, 0)      # over the tabs (z-order only)
            self.lang_buttons[code] = b
            x += self.px(LANG_W + 4)
        left = F.Panel()
        left.AutoScroll = True
        left.BackColor = D.Color.FromArgb(247, 249, 251)
        view.Controls.Add(left)
        self.left = left
        self._build_left(left)
        canvas = self.classes["Canvas"]()
        canvas.owner = self
        view.Controls.Add(canvas)
        self.canvas = canvas
        self._build_tools(view)
        legend = self.classes["Drawn"]()
        legend.BackColor = D.Color.White
        legend.draw = self._draw_legend
        view.Controls.Add(legend)
        self.legend = legend
        self.hover_label = self._label(view)
        side = self.classes["Drawn"]()
        side.BackColor = D.Color.White
        side.draw = self._draw_side
        view.Controls.Add(side)
        self.side = side
        slider = F.TrackBar()
        slider.AutoSize = False
        slider.Minimum = 0
        slider.Maximum = _SLIDER_MAX
        slider.SmallChange = 1
        slider.LargeChange = 50
        slider.TickStyle = getattr(F.TickStyle, "None")
        slider.ValueChanged += self._handler(self._on_slider)    # mouse, keys, wheel
        view.Controls.Add(slider)
        self.slider = slider
        self.place_label = self._label(view)
        self.forces_label = self._label(view, bold=True)
        self.torque_label = self._label(view)
        self.status_label = self._label(view, color=D.Color.FromArgb(105, 112, 120))
        canvas.Resize += self._handler(self._on_canvas_resize)
        view.Resize += self._handler(self._layout)
        tabs.SelectedIndexChanged += self._handler(self._on_tab)
        form.Shown += self._handler(self.start)
        form.FormClosing += self._on_closing
        self._layout()
        self._retext()

    def _set_minimum(self, strip):
        """The smallest window: the old one plus the tab row (strip pixels)."""
        size = self.D.Size(self.px(860), self.px(640) + int(strip))
        if self.form.MinimumSize != size:
            self.form.MinimumSize = size

    def _tab_strip(self):
        """The true height of the tab row and its frame, once the tabs have a
        handle and their headers (at HandleCreated they have neither: measured)."""
        tabs = self.tabs
        if tabs.IsHandleCreated and tabs.TabPages.Count:
            strip = tabs.ClientSize.Height - tabs.DisplayRectangle.Height
            if strip > 0:
                self._set_minimum(strip)

    def _build_left(self, p):
        D, F = self.D, self.F
        grey = D.Color.FromArgb(105, 112, 120)
        y = PAD

        def place(ctrl, h, x=PAD, w=None):
            ctrl.Location = D.Point(self.px(x), self.px(y))
            ctrl.Size = D.Size(self.px((LEFT_W - 2 * PAD) if w is None else w),
                               self.px(h))

        def title(nl, en):
            lb = self._label(p, nl, en, bold=True)
            place(lb, 20)
            return 22

        y += title(u"Balk", u"Beam")
        self.source_box = F.ComboBox()
        self.source_box.DropDownStyle = F.ComboBoxStyle.DropDownList
        self.source_box.Font = self._font()
        p.Controls.Add(self.source_box)
        place(self.source_box, 24)
        self.source_box.SelectionChangeCommitted += self._handler(self._apply)
        y += 28
        self.refresh_button = self._button(p, u"Opnieuw lezen", u"Read again",
                                           self._refresh)
        place(self.refresh_button, 26, PAD, 130)
        y += 34
        y += title(u"Resultaat", u"Result")
        self.result_box = F.ComboBox()
        self.result_box.DropDownStyle = F.ComboBoxStyle.DropDownList
        self.result_box.Font = self._font()
        p.Controls.Add(self.result_box)
        place(self.result_box, 24)
        self.result_box.SelectionChangeCommitted += self._handler(self._apply)
        y += 28
        self.solver_label = self._label(p, color=grey)
        place(self.solver_label, 20)
        y += 26
        y += title(u"Spanning", u"Stress")
        self.measure_list = F.ListBox()
        self.measure_list.Font = self._font()
        self.measure_list.IntegralHeight = False
        p.Controls.Add(self.measure_list)
        place(self.measure_list, MEASURE_H)
        self.measure_list.SelectedIndexChanged += self._handler(self._on_measure)
        y += MEASURE_H + 6
        fixed_part = p.Controls.Count
        y += title(u"Snedekrachten", u"Section forces")
        self.force_checks = {}
        for i, name in enumerate(bu.FORCES):
            cb = self._check(p, name, name, True, self._request)
            cb.Location = D.Point(self.px(PAD + i * 46), self.px(y))
            cb.Size = D.Size(self.px(46), self.px(22))
            self.force_checks[name] = cb
        y += 26
        self.shear_check = self._check(
            p, u"Omrekenen naar schuifcentrum", u"Convert to shear centre",
            True, self._request)
        place(self.shear_check, 22)
        y += 24
        self.arrow_check = self._check(p, u"Pijlen schuifspanning",
                                       u"Shear stress arrows", True, self._request)
        place(self.arrow_check, 22, PAD, 150)
        self.arrow_slider = F.TrackBar()
        self.arrow_slider.AutoSize = False
        self.arrow_slider.Minimum = 2
        self.arrow_slider.Maximum = 40
        self.arrow_slider.Value = 10
        self.arrow_slider.TickStyle = getattr(F.TickStyle, "None")
        self.arrow_slider.ValueChanged += self._handler(self._request)
        p.Controls.Add(self.arrow_slider)
        place(self.arrow_slider, 24, PAD + 152, LEFT_W - 2 * PAD - 152)
        y += 24
        self.contour_check = self._check(p, u"Contourlijnen", u"Contour lines",
                                         bool(bu.settings().get("contours")), self._on_contours)
        place(self.contour_check, 22)
        y += 24
        self.fixed_check = self._check(p, u"Vaste schaal (MPa)", u"Fixed scale (MPa)",
                                       False, self._on_fixed)
        place(self.fixed_check, 22, PAD, 134)
        self.fixed_lo = F.TextBox()
        self.fixed_hi = F.TextBox()
        for box, x in ((self.fixed_lo, PAD + 136), (self.fixed_hi, PAD + 204)):
            box.Font = self._font()
            box.Enabled = False
            p.Controls.Add(box)
            place(box, 22, x, 62)
            box.TextChanged += self._handler(self._on_fixed_text)
        y += 26
        self.sheet_check = self._check(p, u"Meerdere doorsneden", u"Several sections",
                                       False, self._on_sheet)
        place(self.sheet_check, 22, PAD, 170)
        from System import Decimal
        self.sheet_count = F.NumericUpDown()
        self.sheet_count.Font = self._font()
        self.sheet_count.Minimum = Decimal(2)
        self.sheet_count.Maximum = Decimal(12)
        self.sheet_count.Value = Decimal(6)
        self.sheet_count.Enabled = False
        p.Controls.Add(self.sheet_count)
        place(self.sheet_count, 22, PAD + 176, 60)
        self.sheet_count.ValueChanged += self._handler(self._request)
        y += 26
        self.marker_check = self._check(p, u"Toon positie in Mecway",
                                        u"Show position in Mecway", False,
                                        self._on_marker)
        place(self.marker_check, 22)
        y += 24
        self.write_button = self._button(p, u"Schrijf naar Mecway-resultaten",
                                         u"Write to Mecway results", self._on_write)
        place(self.write_button, 24)
        y += 28
        y += title(u"Meldingen", u"Messages")
        self.warn_box = F.TextBox()
        self.warn_box.Multiline = True
        self.warn_box.ReadOnly = True
        self.warn_box.ScrollBars = F.ScrollBars.Vertical
        self.warn_box.BorderStyle = getattr(F.BorderStyle, "None")
        self.warn_box.BackColor = D.Color.FromArgb(247, 249, 251)
        self.warn_box.Font = self._font()
        p.Controls.Add(self.warn_box)
        place(self.warn_box, 120)
        self.warn_y = y
        y += 124
        self.warping_label = self._label(
            p, u"Welving vrij: B = 0, geen welvingsspanning.",
            u"Warping free: B = 0, no warping stress.", color=grey)
        place(self.warping_label, 20)
        self.below_list = [(p.Controls[i], p.Controls[i].Top)
                           for i in range(fixed_part, p.Controls.Count)]

    def _build_tools(self, parent):
        """The row above the picture: 2D or 3D, Fit and magnify with several
        sections; title, shared scale and Back in the detail of one."""
        D, F = self.D, self.F
        tools = F.Panel()
        tools.BackColor = D.Color.White
        tools.Visible = False
        parent.Controls.Add(tools)
        self.tools = tools

        def radio(text, checked):
            rb = F.RadioButton()
            rb.FlatStyle = F.FlatStyle.System     # native: no swallowed clicks
            rb.Font = self._font()
            rb.Text = text
            rb.Checked = checked
            tools.Controls.Add(rb)
            rb.CheckedChanged += self._handler(self._on_mode)
            return rb
        self.radio_2d = radio(u"2D", not self.mode3d)
        self.radio_3d = radio(u"3D", self.mode3d)
        self.fit_button = self._button(tools, u"Passend", u"Fit", self.on_fit)
        self.magnify_label = self._label(tools, u"Vergroten", u"Magnify")
        slider = F.TrackBar()
        slider.AutoSize = False
        slider.Minimum = 0
        slider.Maximum = _MAG_STEPS
        slider.SmallChange = 1
        slider.LargeChange = 4
        slider.Value = 0
        slider.TickStyle = getattr(F.TickStyle, "None")
        slider.ValueChanged += self._handler(self._on_magnify)
        tools.Controls.Add(slider)
        self.magnify_slider = slider
        self.magnify_value = self._label(tools)
        self.detail_title = self._label(tools, bold=True)
        self.shared_check = self._check(tools, u"Schaal van alle doorsneden",
                                        u"Shared scale", False, self._on_shared)
        self.back_button = self._button(tools, u"", u"", self.on_back)   # _show_tools

    def _show_tools(self):
        """Which parts of the row show, their texts and places. Returns True when
        the row came or went (the canvas changes size)."""
        D, F = self.D, self.F
        sheet = bool(self.sheet_check.Checked)
        detail = self.detail is not None and not sheet
        three = sheet and self.mode3d
        for control, on in ((self.radio_2d, sheet), (self.radio_3d, sheet),
                            (self.fit_button, three), (self.magnify_label, three),
                            (self.magnify_slider, three), (self.magnify_value, three),
                            (self.detail_title, detail), (self.shared_check, detail),
                            (self.back_button, detail)):
            control.Visible = on
        self.magnify_value.Text = self._magnify_text()
        if detail:
            saved = self.detail
            self.back_button.Text = self.t(u"Terug naar {0} doorsneden",
                                           u"Back to {0} sections").format(saved["n"])
            self.detail_title.Text = u"" if self.detail_s is None else self.t(
                u"Doorsnede {0} van {1}, s = {2}", u"Section {0} of {1}, s = {2}").format(
                saved["number"], saved["count"], bu.fmt_length(self.detail_s))
            self.shared_check.Enabled = self._measure() in saved["shared"]
        # Visible reads False on a form never shown (the tests): own state instead
        self.tool_parts = (sheet, three, detail)
        changed = self.tools_on != (sheet or detail)
        self.tools_on = sheet or detail
        self.tools.Visible = self.tools_on
        if changed:
            self._layout()
        else:
            self._place_tools()
        return changed

    def _place_tools(self):
        """Left to right at their text widths; the title takes what is left."""
        D, F = self.D, self.F
        px = self.px
        sheet, three, detail = self.tool_parts
        h = self.tools.ClientSize.Height
        width = self.tools.ClientSize.Width

        def text_w(control, extra):
            return F.TextRenderer.MeasureText(control.Text, control.Font).Width + px(extra)

        def put(control, x, w):
            ch = min(px(24), h)
            control.Location = D.Point(int(x), int(max((h - ch) // 2, 0)))
            control.Size = D.Size(int(max(w, 1)), int(ch))
            return x + w + px(6)

        x = px(2)
        if sheet:
            x = put(self.radio_2d, x, text_w(self.radio_2d, 24))
            x = put(self.radio_3d, x, text_w(self.radio_3d, 24))
        if three:
            x = put(self.fit_button, x, text_w(self.fit_button, 18))
            x = put(self.magnify_label, x, text_w(self.magnify_label, 4))
            value_w = F.TextRenderer.MeasureText(u"Auto (100×)",
                                                 self.magnify_value.Font).Width + px(4)
            slider_w = min(max(width - x - value_w - px(8), px(60)), px(160))
            x = put(self.magnify_slider, x, slider_w)
            put(self.magnify_value, x, value_w)
        if detail:
            back_w = text_w(self.back_button, 18)
            shared_w = text_w(self.shared_check, 22)
            right = width - px(2) - back_w
            put(self.back_button, right, back_w)
            right -= shared_w + px(6)
            put(self.shared_check, right, shared_w)
            put(self.detail_title, x, max(right - x - px(6), px(20)))

    def _magnify_text(self):
        used = self.magnify_used
        if self.magnify is None:
            if used:
                return self.t(u"Auto ({0}×)", u"Auto ({0}×)").format(fmt_times(used))
            return self.t(u"Auto", u"Auto")
        return u"{0}×".format(fmt_times(self.magnify))

    def _layout(self):
        D = self.D
        self._tab_strip()
        size = self.page_view.ClientSize
        px = self.px
        left_w = px(LEFT_W + 18)
        self.left.Location = D.Point(0, 0)
        self.left.Size = D.Size(left_w, size.Height)
        x = left_w + px(PAD)
        w = max(size.Width - x - px(PAD), 40)
        y = size.Height - px(LINE_H)
        self.status_label.Location = D.Point(x, y)
        self.status_label.Size = D.Size(w, px(LINE_H))
        y -= px(LINE_H)
        self.torque_label.Location = D.Point(x, y)
        self.torque_label.Size = D.Size(w, px(LINE_H))
        y -= px(LINE_H)
        self.forces_label.Location = D.Point(x, y)
        self.forces_label.Size = D.Size(w, px(LINE_H))
        y -= px(LINE_H)
        self.place_label.Location = D.Point(x, y)
        self.place_label.Size = D.Size(w, px(LINE_H))
        y -= px(SLIDER_H)
        self.slider.Location = D.Point(x, y)
        self.slider.Size = D.Size(w, px(SLIDER_H))
        y -= px(SIDE_H)
        self.side.Location = D.Point(x, y)
        self.side.Size = D.Size(w, px(SIDE_H))
        y -= px(LINE_H)
        self.hover_label.Location = D.Point(x, y)
        self.hover_label.Size = D.Size(w, px(LINE_H))
        top = px(PAD)
        self.tools.Location = D.Point(x, top)
        self.tools.Size = D.Size(w, px(TOOL_H))
        if self.tools_on:
            top += px(TOOL_H + 4)
        self._place_tools()
        h = max(y - top, 40)
        lw = px(LEGEND_W)
        self.canvas.Location = D.Point(x, top)
        self.canvas.Size = D.Size(max(w - lw - px(PAD), 40), h)
        self.legend.Location = D.Point(self.canvas.Right + px(PAD), top)
        self.legend.Size = D.Size(lw, h)
        # A low window takes rows from the measure list before the messages:
        # those must stay readable.
        room = self.left.ClientSize.Height - px(self.warn_y + PAD + 24)
        cut = min(max(px(WARN_MIN) - room, 0), px(MEASURE_H - MEASURE_MIN))
        self.measure_list.Height = px(MEASURE_H) - cut
        for ctrl, top in self.below_list:
            ctrl.Top = top - cut
        self.warn_box.Height = max(room + cut, px(40))
        self.warping_label.Top = self.warn_box.Bottom + px(4)

    def _retext(self):
        for setter, nl, en in self.texts:
            setter(bu.tr(nl, en))
        self._mark_language()
        self._fill_measures()
        self._fill_sources()
        self._fill_results()
        self._show_warnings()
        self._show_solver()
        self._show_numbers()
        self._show_tools()
        self.legend.Invalidate()
        self.canvas.Invalidate()

    def _status(self, text, error=False):
        D = self.D
        self.status_label.ForeColor = (D.Color.FromArgb(180, 30, 30) if error
                                       else D.Color.FromArgb(105, 112, 120))
        self.status_label.Text = text

    # ------------------------------------------------------------ fill lists
    def _fill_measures(self):
        was, self.updating = self.updating, True
        try:
            keep = self._measure()
            names = dict((m[0], m) for m in bu.MEASURES)
            self.measure_list.Items.Clear()
            for key in self.measure_keys:
                m = names[key]
                self.measure_list.Items.Add(bu.tr(m[1], m[2]))
            if keep in self.measure_keys:
                self.measure_list.SelectedIndex = self.measure_keys.index(keep)
            elif self.measure_keys:
                self.measure_list.SelectedIndex = 0
        finally:
            self.updating = was

    def _measure(self):
        i = self.measure_list.SelectedIndex
        if 0 <= i < self.measure_list.Items.Count and i < len(self.measure_keys):
            return self.measure_keys[i]
        return "vonmises"

    def _fill_box(self, box, labels, index):
        was, self.updating = self.updating, True
        try:
            box.Items.Clear()
            for text in labels:
                box.Items.Add(text)
            if labels:
                box.SelectedIndex = min(max(index, 0), len(labels) - 1)
        finally:
            self.updating = was

    def _source_label(self, item):
        n = item["count"]
        base = item["name"] if item["key"] is not None else \
            self.t(u"Huidige selectie", u"Current selection")
        if item.get("uncertain") == "unreadable":
            return self.t(u"{0} (bestand niet te lezen)", u"{0} (file not readable)").format(base)
        if item.get("uncertain"):
            return self.t(u"{0} (nog niet opgeslagen)", u"{0} (not saved yet)").format(base)
        return self.t(u"{0} ({1} elementen)", u"{0} ({1} elements)").format(base, n)

    def _fill_sources(self):
        i = self.source_box.SelectedIndex
        self._fill_box(self.source_box,
                       [self._source_label(s) for s in self.source_items], i)

    def _result_label(self, r):
        kind = r["kind"]
        if kind == "step":
            text = self.t(u"Stap {0} (t = {1} s)", u"Step {0} (t = {1} s)").format(
                r["number"], bu._num(r["value"]))
        elif kind == "mode":
            text = self.t(u"Mode {0} ({1} Hz)", u"Mode {0} ({1} Hz)").format(
                r["number"], bu._num(r["value"]))
        elif kind == "buckling":
            text = self.t(u"Knikmode {0} (factor {1})",
                          u"Buckling mode {0} (factor {1})").format(
                r["number"], bu._num(r["value"]))
        else:
            text = self.t(u"Actieve oplossing", u"Active solution")
        if r.get("switch"):
            name = self.t(u"Configuratie {0}", u"Configuration {0}").format(r["configuration"])
            return name if kind == "active" else name + u", " + text
        return text

    def _fill_results(self):
        i = self.result_box.SelectedIndex
        self._fill_box(self.result_box,
                       [self._result_label(r) for r in self.result_items], i)

    def _solver_name(self):
        head = self.head or {}
        if head.get("aster"):
            return u"Code-Aster"
        return {"internal": self.t(u"Mecway intern", u"Mecway internal"),
                "ccx": u"CalculiX"}.get(head.get("solver"), head.get("solver"))

    def _show_solver(self):
        head = self.head or {}
        name = self._solver_name()
        if head.get("aster"):
            self.warping_label.Text = self.t(u"Bimoment B niet meegerekend.",
                                             u"Bimoment B not included.")
        if name:
            self.solver_label.Text = self.t(u"Solver: {0}", u"Solver: {0}").format(name)
        else:
            self.solver_label.Text = u""

    def _show_warnings(self):
        seen, lines = set(), []
        items = self.warn_start + self.warn_local + self.warn_worker + self.warn_notes
        if self.marker_on:
            items = [{"code": "marker_on", "data": {"name": self.marker_name}}] + items
        for w in items:
            text = bu.warning_text(w.get("code"), w.get("data"))
            if text not in seen:
                seen.add(text)
                lines.append(u"• " + text)
        self.warn_box.Text = u"\r\n".join(lines)

    def _show_numbers(self):
        """The lines under the slider from the last frame."""
        last = self.last
        if last is None:
            self.place_label.Text = u""
            self.forces_label.Text = u""
            self.torque_label.Text = u""
            return
        if last.get("sheet"):
            self.place_label.Text = self.t(
                u"{0} doorsneden gelijk verdeeld over {1}, op \u00e9\u00e9n kleurschaal",
                u"{0} sections evenly along {1}, on one colour scale").format(
                last["n"], bu.fmt_length((self.chain or {}).get("length")))
            self.forces_label.Text = u""
            if "camera" in last:
                self.torque_label.Text = self.t(
                    u"Slepen: draaien; rechts slepen: verschuiven; wiel na een klik: zoomen; "
                    u"dubbelklik op een doorsnede: detail.",
                    u"Drag: turn; right drag: move; wheel after a click: zoom; "
                    u"double click on a section: detail.")
            else:
                self.torque_label.Text = self.t(u"Dubbelklik op een doorsnede: detail.",
                                                u"Double click on a section: detail.")
            return
        force = last["forces"]
        parts = []
        for name in bu.FORCES:
            v = bu.fmt_moment(force[name]) if name in ("T", "Mv", "Mw") else \
                bu.fmt_force(force[name])
            text = u"{0} = {1}".format(name, v)
            if not self.force_checks[name].Checked:
                text = u"({0})".format(text)
            parts.append(text)
        self.forces_label.Text = u"    ".join(parts)
        self.place_label.Text = self.t(
            u"Plaats {0} van {1}, element {2}, ξ = {3:.2f}",
            u"Position {0} of {1}, element {2}, ξ = {3:.2f}").format(
            bu.fmt_length(last["s"]), bu.fmt_length((self.chain or {}).get("length")),
            last["eid"], last["xi"])
        if "term" not in last:
            self.torque_label.Text = u""
        elif last.get("converted"):
            self.torque_label.Text = self.t(
                u"T om de balk-as {0}  +  schuifcentrumterm {1}  =  T_S {2}",
                u"T about the beam line {0}  +  shear centre term {1}  =  T_S {2}"
            ).format(bu.fmt_moment(last["T"]), bu.fmt_moment(last["term"]),
                     bu.fmt_moment(last["T_S"]))
        else:
            self.torque_label.Text = self.t(
                u"T_S = T = {0}; schuifcentrumterm {1} niet meegeteld",
                u"T_S = T = {0}; shear centre term {1} left out"
            ).format(bu.fmt_moment(last["T"]), bu.fmt_moment(last["term"]))

    # ---------------------------------------------------------------- mw calls
    def _post(self, fn, done, failed=None):
        """Run fn on the script thread; done(result) on the window thread,
        failed() as well on an error."""
        self.tickets.append((self.caller.post(fn), done, failed))
        self.timer.Start()

    def _poll(self):
        pending, self.tickets = self.tickets, []
        keep = []
        for ticket, done, failed in pending:
            ready, result, error = self.caller.result(ticket)
            if not ready:
                keep.append((ticket, done, failed))
            elif error:
                log("ERROR: mw: {0}".format(error))
                self._status(error.strip().split("\n")[-1], True)
                if failed is not None:
                    failed()
            else:
                done(result)        # may post more
        self.tickets = keep + self.tickets
        # by state, not only down: the question to the user runs inside done(), and a
        # poll inside it must not leave waiting tickets without a timer
        self.timer.Enabled = bool(self.tickets)

    # --------------------------------------------------------------- start, read
    def start(self):
        """The window is there: start the worker and read the lists."""
        self._new_link()
        mw = self.mw
        self._post(lambda: bmw.clear_leftover(mw), self._on_leftover)
        self._refresh()

    def _on_leftover(self, result):
        if result["found"]:
            self.warn_start = [{"code": "marker_left" if result["removed"]
                                else "marker_stuck"}]
            self._show_warnings()

    def _new_link(self):
        self.link = _Link(self._from_link, self.worker)
        self.link.start()

    def _from_link(self, kind, data):
        """Link thread: hand over to the window thread. A closing form may
        refuse: then nobody waits for this event any more."""
        with self.inbox_lock:
            self.inbox.append((kind, data))
        try:
            self.form.BeginInvoke(self.F.MethodInvoker(self._drain))
        except Exception:
            pass

    def _drain(self):
        while True:
            with self.inbox_lock:
                item = self.inbox.pop(0) if self.inbox else None
            if item is None:
                return
            if not self.closed:
                self.guarded(self._on_link, item[0], item[1])
            elif item[0] == "reply" and item[1]["array"] is not None:
                link = self.link
                if link is not None:
                    link.ack(False)

    def _on_link(self, kind, data):
        if kind == "starting":
            self.t_start = time.time()
            self._status(self.t(u"Python starten, de eerste keer ongeveer 2 s ...",
                                u"Starting Python, about 2 s the first time ..."))
        elif kind == "hello":
            self._status(self.t(u"Python klaar ({0} s).", u"Python ready ({0} s).")
                         .format(bu._num(time.time() - (self.t_start or time.time()), 2)))
        elif kind == "no_python":
            self._no_python(data)
        elif kind == "failed":
            self._link_failed(data)
        elif kind == "reply":
            self._on_reply(data)

    def _no_python(self, found):
        text = self.t(u"Geen Python met numpy en scipy gevonden.",
                      u"No Python with numpy and scipy found.")
        if found.get("pip"):
            text += self.t(u" Installeer ze met: {0}", u" Install them with: {0}").format(
                bu.pip_command(found["pip"]))
        self.warn_local = [{"code": text}]
        self._show_warnings()
        self._status(self.t(u"Python ontbreekt.", u"Python is missing."), True)

    def _link_failed(self, data):
        self._end_solve()
        self.opened = False
        self.opened_key = None
        if data.get("code") == "import_failed":
            # this Python lost numpy or scipy: search again next time
            bu.forget_python()
            self.warn_local = [{"code": "import_failed", "data": data.get("data")}]
            self._show_warnings()
        self._status(self.t(u"Het rekenproces is gestopt ({0}); kies Opnieuw lezen. "
                            u"Details: {1}",
                            u"The calculation process stopped ({0}); choose Read "
                            u"again. Details: {1}").format(
            data.get("code"), bu.WORKER_LOG), True)

    def _refresh(self):
        """Read lists and the current choice from Mecway, then show the beam."""
        self._end_solve()
        link = self.link
        if link is None or link.stopping or not link.thread.IsAlive or link.hung():
            if link is not None:
                link.kill()
            self._new_link()
        self.opened_key = None      # the file may have been saved meanwhile
        self.head_stale = True
        self.warn_worker = []
        mw, solution = self.mw, self.solution

        def work():
            return {"file": bmw.file_path(mw), "sources": bmw.sources(mw),
                    "results": bmw.result_sets(solution)}
        self._post(work, self._on_lists)

    def _on_lists(self, lists):
        self.file = lists["file"]
        self.source_items = lists["sources"]
        self.base_results = lists["results"]
        if lists["results"] is None:
            self.result_items = []
            self._status(self.t(u"Geen oplossing: los het model eerst op.",
                                u"No solution: solve the model first."), True)
        else:
            self.result_items = list(lists["results"])
        if self.head:
            self._merge_head()
        self._fill_sources()
        self._fill_results()
        if self.file:
            self.link.control({"cmd": "scan", "path": self.file})
        else:
            self.warn_local = [{"code": "no_file"}]
            self._show_warnings()
            self._no_picture(self.t(u"Model niet opgeslagen.", u"Model not saved."))

    def _merge_head(self):
        """Node selections out by the file; then a named int selection the file does
        not hold as element or face selection is uncertain: the API gives ints for
        node and element selections alike (measured), so it is not opened.
        uncertain: False, "unsaved", or "unreadable" when the file could not be read."""
        head = self.head
        skip = set(head.get("node_selections") or [])
        self.source_items = [s for s in self.source_items if s["name"] not in skip]
        known = head.get("element_selections")
        why = "unsaved" if known is not None else "unreadable"
        known = set(known or [])
        for s in self.source_items:
            s["uncertain"] = why if (s["key"] is not None and not s.get("faces") and
                                     s["name"] not in known) else False
        self.result_items = list(self.base_results or [])
        if self.base_results is not None:
            self.result_items = bmw.with_configurations(self.result_items, head)

    def _on_scan(self, reply):
        self.head = reply.get("head") or {}
        self.head_stale = False
        self._merge_head()
        self._fill_sources()
        self._fill_results()
        self._show_solver()
        usable = [s for s in self.source_items if s["count"] and not s.get("uncertain")]
        if usable:
            i = self.source_box.SelectedIndex
            if i < 0 or not self.source_items[i]["count"]:
                self.updating = True
                self.source_box.SelectedIndex = self.source_items.index(usable[0])
                self.updating = False
            self._apply()
        elif any(s.get("uncertain") for s in self.source_items):
            self._no_picture(self.t(u"Selecteer balkelementen in Mecway, of sla het model "
                                    u"op, en kies Opnieuw lezen.",
                                    u"Select beam elements in Mecway, or save the model, "
                                    u"and choose Read again."))
        else:
            self._no_picture(self.t(u"Selecteer balkelementen in Mecway en kies "
                                    u"Opnieuw lezen.",
                                    u"Select beam elements in Mecway and choose "
                                    u"Read again."))

    def _apply(self):
        """Read the chosen beam and result from Mecway and hand them over."""
        if self.updating or not self.source_items or not self.result_items:
            return
        self.gen += 1
        gen = self.gen
        source = self.source_items[max(self.source_box.SelectedIndex, 0)]
        result = self.result_items[max(self.result_box.SelectedIndex, 0)]
        mw, solution = self.mw, self.solution
        if source["key"] is not None and not source.get("faces"):
            if (self.head is None or self.head_stale) and self.file:
                # a node or an element selection: the file tells, _on_scan applies
                self._status(self.t(u"Bestand lezen ...", u"Reading the file ..."))
                return
            if source.get("uncertain"):
                code = ("selection_unreadable" if source["uncertain"] == "unreadable"
                        else "selection_unsaved")
                self.warn_local = [{"code": code, "data": {"name": source["name"]}}]
                self._show_warnings()
                self._no_picture(self.t(u"Sla het model op en kies Opnieuw lezen.",
                                        u"Save the model and choose Read again."))
                return
        self.warn_local = []
        if result["kind"] in ("mode", "buckling"):
            self.warn_local.append({"code": "mode_scaled"})
        if result.get("switch"):
            self.warn_local.append({"code": "configuration_active",
                                    "data": {"name": result["configuration"]}})
        self._status(self.t(u"Lezen uit Mecway ...", u"Reading from Mecway ..."))

        def work():
            bmw.activate(mw, result, solution)
            info = bmw.beam_elements(mw, source["key"])
            info["route"] = None
            if info["ids"] and solution is not None:
                info["route"] = bmw.pick_route(mw, solution, info["ids"][0], result)
            return info
        self._post(work, lambda info: self._on_elements(gen, source, result, info),
                   lambda: self._read_failed(gen))

    def _read_failed(self, gen):
        """A read from Mecway failed, its error in the status line: no picture, and no
        claim that the configuration was switched."""
        if gen != self.gen:
            return
        self.warn_local = [w for w in self.warn_local if w.get("code") != "configuration_active"]
        self._show_warnings()
        self._no_picture(self.status_label.Text)

    def _on_elements(self, gen, source, result, info):
        if gen != self.gen:
            return
        if not info["ids"]:
            self._no_picture(self.t(u"Deze keuze bevat geen balkelementen.",
                                    u"This choice holds no beam elements."))
            return
        if not info["file"]:
            self.warn_local.append({"code": "no_file"})
            self._show_warnings()
            self._no_picture(self.t(u"Model niet opgeslagen.", u"Model not saved."))
            return
        if info["route"] is None:
            self.warn_local.append({"code": "no_route"})
            self._show_warnings()
            self._no_picture(self.t(u"Geen resultaten voor deze balk.",
                                    u"No results for this beam."))
            return
        if info["route"] == "node":
            self.warn_local.append({"code": "route_node"})
        elif info["route"] == "kinematics":
            fields = (self.head or {}).get("fields") or []
            missing = [f for f in bu.FORCE_FIELDS if f not in fields]
            self.warn_local.append({"code": "route_kinematics" if missing else
                                    "route_kinematics_result"})
        self._show_warnings()
        data = {"raw": {}, "nodes": {}, "missing": []}
        self._read_chunk(gen, source, result, info, 0, data)

    def _no_picture(self, text):
        self._end_solve()
        self.opened = False
        self.opened_key = None
        self.canvas.dib = None
        self.canvas.tiles = None
        self.canvas.shown = None
        self.canvas.Invalidate()
        self.last = None
        self._show_numbers()
        self._status(text, True)

    def _read_chunk(self, gen, source, result, info, start, data):
        ids = info["ids"]
        part = ids[start:start + bmw.CHUNK]
        mw, solution, route = self.mw, self.solution, info["route"]

        def work():
            return bmw.read_chunk(mw, solution, part, result, route)

        def done(chunk):
            if gen != self.gen:
                return
            for key in ("raw", "nodes"):
                data[key].update(chunk[key])
            data["missing"].extend(chunk["missing"])
            next_start = start + bmw.CHUNK
            if next_start < len(ids):
                self._status(self.t(u"Lezen uit Mecway: {0} van {1} elementen ...",
                                    u"Reading from Mecway: {0} of {1} elements ...")
                             .format(next_start, len(ids)))
                self._read_chunk(gen, source, result, info, next_start, data)
            else:
                self._hand_over(gen, source, result, info, data)
        self._post(work, done, lambda: self._read_failed(gen))

    def _hand_over(self, gen, source, result, info, data):
        if data["missing"]:
            self.warn_local.append({"code": "no_route"})
            self._show_warnings()
            self._no_picture(self.t(u"Element {0} heeft geen waarden.",
                                    u"Element {0} has no values.").format(
                data["missing"][0]))
            return
        configuration = result.get("configuration")
        key = (source["key"], tuple(info["ids"]), info["file"], configuration)
        if self.opened and key == self.opened_key:
            self.link.control({"cmd": "forces", "gen": gen, "raw": data["raw"],
                               "route": info["route"]})
            return
        self.opened = False
        self.chain = None
        self._status(self.t(u"Model en doorsnede lezen ...",
                            u"Reading model and section ..."))
        self.link.control({
            "cmd": "open", "gen": gen, "path": info["file"],
            "element_ids": info["ids"], "raw": data["raw"],
            "route": info["route"], "configuration": configuration,
            "aster": bool((self.head or {}).get("aster")),
            "api": {"n_nodes": info["n_nodes"], "n_elements": info["n_elements"],
                    "nodes": data["nodes"]}})
        self.pending_key = key

    # ----------------------------------------------------------- worker replies
    def _on_reply(self, item):
        request, reply = item["request"], item["reply"]
        cmd = request.get("cmd")
        used = False
        try:
            if cmd == "frame":
                used = self._on_frame(item)
            elif cmd == "probe":
                self._on_probe(reply, request)
            elif cmd == "scan":
                self._on_scan(reply)
            elif request.get("gen") != self.gen:
                pass
            elif not reply.get("ok"):
                self._on_failure(reply)
            elif cmd == "open":
                self._on_open(reply)
                self._write_if_due()
            elif cmd == "node_maxima":
                self._on_node_maxima(reply, request.get("solved", False))
            elif cmd == "forces":
                self._set_measures(reply.get("measures"))
                self._status(u"")
                self._request()
                self._write_if_due()
        finally:
            if item["array"] is not None:
                self.link.ack(used)

    def _on_failure(self, reply):
        code = reply.get("code")
        self.warn_worker = [{"code": code, "data": reply.get("data")}]
        self._show_warnings()
        self._no_picture(self.t(u"Geen beeld, zie de meldingen.",
                                u"No picture, see the messages."))

    def _on_open(self, reply):
        self.chain = {"length": reply["length"], "side": reply["side"],
                      "elements": reply["elements"]}
        self.warn_worker = reply.get("warnings") or []
        self._show_warnings()
        self._set_measures(reply.get("measures"))
        self.opened = True
        self.marker_due = self.marker_on
        self.opened_key = self.pending_key
        self.view = None
        self.last = None                # the dots and numbers of another beam
        self._show_numbers()
        if self.opened_key != self.beam_key:
            # another beam: its own default camera. A detail keeps its view and Back
            # (count, 2D or 3D, magnification, fixed scale), but its section, s, camera
            # and shared scale were of the old beam.
            self.beam_key = self.opened_key
            self.camera = None
            self.drag = None
            self.pick = None
            self.wheel_armed = False
            if self.detail is not None:
                self.detail["camera"] = None
                self.detail["shared"] = {}
                self.detail_s = None
                self._show_tools()
        self._status(self.t(u"{0} elementen, lengte {1}.",
                            u"{0} elements, length {1}.").format(
            reply["n_elements"], bu.fmt_length(reply["length"])))
        self.side.Invalidate()
        self._request()

    def _set_measures(self, keys):
        if keys and keys != self.measure_keys:
            self.measure_keys = list(keys)
            self._fill_measures()

    def _on_frame(self, item):
        reply = item["reply"]
        if item["request"].get("gen") != self.gen:
            return False
        if not reply.get("ok"):
            if reply.get("code") != "not_open":
                self._on_failure(reply)
            return False
        self.last = reply
        if "camera" in reply:
            # the normalised camera of the newest request, not while a drag runs
            # from its own base: an older reply would turn the view back
            if item["request"].get("frame") == self.frame_no and \
                    not (self.drag and self.drag["moved"]):
                self.camera = reply["camera"]
            if reply.get("magnify") != self.magnify_used:
                self.magnify_used = reply.get("magnify")
                self.magnify_value.Text = self._magnify_text()
        notes = reply.get("notes") or []
        if notes != self.warn_notes:
            self.warn_notes = notes
            self._show_warnings()
        self._show_numbers()
        self.side.Invalidate()
        self.legend.Invalidate()
        self._marker_if_due()
        if reply.get("no_section"):
            self.canvas.dib = None
            self.canvas.tiles = None
            self.canvas.shown = None
            self.canvas.Invalidate()
            return False
        return self._show_frame(item)

    def _show_frame(self, item):
        """Put a frame on the canvas. Returns True: the array is now in use."""
        from System.Diagnostics import Stopwatch
        reply = item["reply"]
        t0 = Stopwatch.GetTimestamp()
        self.canvas.dib = (item["array"], item["width"], item["height"])
        self.canvas.tiles = reply.get("tiles")
        self.canvas.shown = reply
        self.painted = reply.get("frame", 0)
        self.canvas.Invalidate()
        self.canvas.Update()
        ms = float(Stopwatch.GetTimestamp() - t0) * 1000.0 / Stopwatch.Frequency
        if len(self.times) < _MAX_TIMES and not reply.get("coarse"):
            spent = reply.get("ms") or {}
            self.times.append((item["ms"], spent.get("compute", 0.0) +
                               spent.get("paint", 0.0), ms))
        return True

    def _on_probe(self, reply, request=None):
        if request is not None and request.get("pick"):
            self._on_pick(reply, request)
            return
        self.hover_label.Text = self.probe_text(reply)

    def _shown_3d(self):
        """Is the picture on the canvas a 3D one?"""
        return "camera" in (self.canvas.shown or {})

    def probe_text(self, reply):
        """The hover line of a probe reply. In 3D a section seen on its edge gives
        number and position only; the value comes when the worker has one."""
        if reply.get("frame") != self.painted:
            return u""
        if reply.get("no_section"):
            return self.t(u"Doorsnede {0}, {1}: geen doorsnede",
                          u"Section {0}, {1}: no section").format(
                reply.get("number"), bu.fmt_length(reply.get("s")))
        three = self._shown_3d()
        head = None
        if reply.get("number") is not None and reply.get("s") is not None:
            head = self.t(u"Doorsnede {0}, {1}", u"Section {0}, {1}").format(
                reply["number"], bu.fmt_length(reply["s"]))
        hit = reply.get("hit", three)
        if hit and reply.get("v") is not None:
            tau = None
            if reply.get("tau_v") is not None:
                tau = (reply["tau_v"] ** 2 + reply["tau_w"] ** 2) ** 0.5
            body = self.t(
                u"V = {0}, W = {1}:  waarde {2},  σ {3},  τ {4}",
                u"V = {0}, W = {1}:  value {2},  σ {3},  τ {4}").format(
                bu.fmt_length(reply["v"]), bu.fmt_length(reply["w"]),
                bu.fmt_stress(reply.get("value")), bu.fmt_stress(reply.get("sigma")),
                bu.fmt_stress(tau))
        elif (hit or three) and reply.get("value") is not None:
            body = self.t(u"waarde {0}", u"value {0}").format(bu.fmt_stress(reply["value"]))
        elif three and head:
            return head
        else:
            return u""
        return body if head is None else u"{0}:  {1}".format(head, body)

    # --------------------------------------------------------------- user input
    def _position(self):
        length = (self.chain or {}).get("length") or 0.0
        if self.detail_s is not None and not self.sheet_check.Checked:
            return min(max(self.detail_s, 0.0), length)
        return length * self.slider.Value / float(_SLIDER_MAX)

    def _scale(self):
        """The user's own fixed scale; else, in the detail with Shared scale, the
        scale all sections had; else automatic."""
        if self.fixed_check.Checked:
            pair = self.fixed.get(self._measure())
            return list(pair) if pair else None
        if self.detail is not None and self.shared_check.Checked and \
                not self.sheet_check.Checked:
            pair = self.detail["shared"].get(self._measure())
            return list(pair) if pair else None
        return None

    def _frame_request(self, coarse):
        self.frame_no += 1
        size = self.canvas.ClientSize
        sheet = bool(self.sheet_check.Checked)
        three = sheet and self.mode3d
        request = {"cmd": "frame", "gen": self.gen, "frame": self.frame_no,
                   "s": self._position(), "measure": self._measure(),
                   "on": dict((k, bool(c.Checked)) for k, c in self.force_checks.items()),
                   "shear_centre": bool(self.shear_check.Checked),
                   "width": size.Width, "height": size.Height, "view": self.view,
                   "coarse": bool(coarse) and (three or not sheet), "scale": self._scale(),
                   "sheet": {"n": int(self.sheet_count.Value)} if sheet else None,
                   "band": self.px(TILE_BAND),
                   "arrows": {"on": bool(self.arrow_check.Checked),
                              "length": self.arrow_slider.Value / 10.0},
                   "contours": bool(self.contour_check.Checked),
                   "ui_scale": self.k}
        if sheet:
            request["mode"] = "3d" if three else "2d"
        if three:
            if self.camera is not None:
                request["camera"] = dict(self.camera)
                request["z_top"] = self.up_axis.known()
            else:
                # the default view: Mecway's up axis as it is now saved
                request["z_top"] = self.up_axis.z_top()
            if self.magnify is not None:
                request["magnify"] = self.magnify
        return request

    def _request(self, coarse=False):
        if self.opened and not self.updating and self.link is not None:
            self.link.frame(self._frame_request(coarse))

    def _on_slider(self):
        if self.updating:
            return
        if self.detail_s is not None:
            # the slider rules again: the title of the detail goes
            self.detail_s = None
            self._show_tools()
        self.side.Invalidate()
        self._request()
        if self.marker_on:
            self.marker_timer.Stop()
            self.marker_timer.Start()

    def _on_contours(self):
        bu.save_setting("contours", bool(self.contour_check.Checked))
        self._request()

    def _on_sheet(self):
        on = bool(self.sheet_check.Checked)
        if on and self.detail is not None:
            # several sections chosen by hand: the detail ends without restoring
            self.detail = None
            self.detail_s = None
        self.sheet_count.Enabled = on
        self.slider.Enabled = not on
        self.marker_check.Enabled = not on and not self.solving
        if on and self.marker_check.Checked:
            self.marker_check.Checked = False
        self.view = None
        self.drag = None
        self.wheel_armed = False
        self._show_tools()        # first: a resize request then has the final size
        self.side.Invalidate()
        self._request()

    def _on_mode(self):
        """2D or 3D, from the radio buttons; both fire, the checked one counts."""
        three = bool(self.radio_3d.Checked)
        if three == self.mode3d:
            return
        self.mode3d = three
        if not self.updating:
            bu.save_setting("sheet_view", "3d" if three else "2d")
        self.drag = None
        self.wheel_armed = False
        self.pick = None
        self._show_tools()
        self.canvas.Invalidate()
        self._request()

    def _on_magnify(self):
        self.magnify = magnify_of(self.magnify_slider.Value)
        self.magnify_value.Text = self._magnify_text()
        self._request(True)

    def _on_shared(self):
        self._request()
        self.legend.Invalidate()

    def _set_mode(self, three):
        """Set the radio buttons without saving the choice (a restore)."""
        was, self.updating = self.updating, True
        try:
            self.mode3d = three             # first: _on_mode then has nothing to do
            (self.radio_3d if three else self.radio_2d).Checked = True
        finally:
            self.updating = was

    # ---- the detail of one section ----
    def open_detail(self, tile):
        """One section of several, alone at its exact s; the state of the several
        is kept for Back."""
        shown = self.canvas.shown or self.last or {}
        if not self.sheet_check.Checked or not shown.get("sheet"):
            return
        shared = {}
        if shown.get("measure") and "lo" in shown:
            shared[shown["measure"]] = (shown["lo"], shown["hi"])
        self.detail = {
            "n": int(self.sheet_count.Value), "mode3d": self.mode3d,
            "camera": None if self.camera is None else dict(self.camera),
            "magnify_at": self.magnify_slider.Value,
            "fixed_on": bool(self.fixed_check.Checked), "fixed": dict(self.fixed),
            "marker": bool(self.marker_check.Checked),
            "number": tile["number"], "count": shown.get("n") or len(shown.get("tiles") or []),
            "shared": shared}
        s = float(tile["s"])
        length = (self.chain or {}).get("length") or 0.0
        self.drag = None
        self.pick = None
        self.wheel_armed = False
        was, self.updating = self.updating, True
        try:
            self.sheet_check.Checked = False        # _on_sheet: slider and option free
            if length > 0.0:
                self.slider.Value = int(min(max(round(s / length * _SLIDER_MAX), 0),
                                            _SLIDER_MAX))
        finally:
            self.updating = was
        self.detail_s = s
        self.hover_label.Text = u""
        self._show_tools()
        self.side.Invalidate()
        self.legend.Invalidate()
        self._request()

    def on_back(self):
        """Back to the several sections as they were: count, 2D or 3D, camera,
        magnification, fixed scale and the position option."""
        saved = self.detail
        if saved is None:
            return
        from System import Decimal
        self.detail = None
        self.detail_s = None
        was, self.updating = self.updating, True
        try:
            # the dict before the check: _on_fixed fills a missing scale from the
            # last frame, which is the detail's
            self.fixed = dict(saved["fixed"])
            self.fixed_check.Checked = saved["fixed_on"]
            self.marker_check.Checked = saved["marker"]
            self.sheet_count.Value = Decimal(saved["n"])
            self._set_mode(saved["mode3d"])
            self.camera = None if saved["camera"] is None else dict(saved["camera"])
            self.magnify_slider.Value = saved["magnify_at"]
            self.magnify = magnify_of(saved["magnify_at"])
            self.sheet_check.Checked = True
        finally:
            self.updating = was
        self._fill_fixed()
        self.fixed_lo.Enabled = self.fixed_hi.Enabled = bool(self.fixed_check.Checked)
        self._show_tools()
        self.side.Invalidate()
        self.legend.Invalidate()
        self._request()

    # ---- the maxima as results of Mecway ----
    def _on_write(self, solved=False):
        """solved: this is the write after a solve the user agreed to."""
        if not self.opened or self.link is None or self.solution is None:
            self._status(self.t(u"Geen balk geopend.", u"No beam opened."), True)
            return
        self._status(self.t(u"Maxima per knoop rekenen ...", u"Computing the maxima per node ..."))
        self.link.control({"cmd": "node_maxima", "gen": self.gen, "solved": solved,
                           "on": dict((k, bool(c.Checked)) for k, c in self.force_checks.items()),
                           "shear_centre": bool(self.shear_check.Checked)})

    def _on_node_maxima(self, reply, solved):
        mw, solution = self.mw, self.solution
        result = self.result_items[max(self.result_box.SelectedIndex, 0)]
        values = reply["values"]
        if not values:
            self._status(self.t(u"Niets om te schrijven.", u"Nothing to write."), True)
            return

        self._post(lambda: bmw.write_results(mw, solution, values, result),
                   lambda out: self._on_written(out, solved, result))

    def _on_written(self, out, solved, result):
        if "refused" not in out:
            self._status(self.t(u"Geschreven naar Mecway: {0}. Sla het model op om ze te bewaren.",
                                u"Written to Mecway: {0}. Save the model to keep them.").format(
                u", ".join(out["names"])))
            return
        self._write_refused(out, solved, result)

    def _write_refused(self, out, solved, result):
        """Not written, see beam_mecway.write_results. A solve is the user's choice, and
        never on a solution of the bridge: that would replace it, and the bridge has
        these results on the beam and its shells already."""
        head = self.head or {}
        if out.get("written"):
            self._status(self.t(u"Deels geschreven: {0}. Daarna weigerde Mecway: {1}.",
                                u"Partly written: {0}. Then Mecway refused: {1}.").format(
                u", ".join(out["written"]), out["refused"]), True)
            return
        if head.get("aster") or head.get("shells"):
            self._status(self.t(u"Niets geschreven: volgens het opgeslagen bestand is dit een "
                                u"oplossing van de bridge ({0}). Die schrijft zijn resultaten "
                                u"zelf: snedekrachten op de balk, spanning op de schillen.",
                                u"Nothing written: by the saved file this is a solution of the "
                                u"bridge ({0}). It writes its results itself: section forces on "
                                u"the beam, stress on the shells.").format(
                u", ".join(head.get("shells") or []) or u"Code-Aster"), True)
            return
        if solved:
            self._status(self.t(u"Mecway weigert de resultaten ook na opnieuw oplossen ({0}).",
                                u"Mecway refuses the results after solving again too ({0})."
                                ).format(out["refused"]), True)
            return
        stuck = [w for w in self.warn_start if w.get("code") == "marker_stuck"]
        if self.marker_on or self.marker_check.Checked or stuck:
            self._status(self.t(u"Zet eerst Toon positie in Mecway uit.",
                                u"Switch off Show position in Mecway first."), True)
            return
        reason = self.t(u"Deze oplossing heeft {0} knopen en het model {1}.",
                        u"This solution has {0} nodes and the model {1}.").format(
            out["solution"], out["model"])
        if out["refused"] != "size":
            reason = self.t(u"Mecway neemt deze resultaten niet aan op deze oplossing.",
                            u"Mecway does not take these results on this solution.")
        answer = self._ask(self.t(
            u"{0}\n\nOpnieuw oplossen met {1} en daarna schrijven? De huidige oplossing wordt "
            u"vervangen, en Mecway is bezet zo lang de berekening duurt.",
            u"{0}\n\nSolve again with {1} and then write? The current solution is replaced, "
            u"and Mecway is busy for as long as the calculation takes.").format(
            reason, self._solver_name()))
        if answer != self.F.DialogResult.Yes:
            self._status(self.t(u"Niets geschreven.", u"Nothing written."))
            return
        self._solve_then_write(result)

    def _busy(self, on):
        """No choices from the solve until the write after it: they would read a solution
        in the making, or change what the user agreed to."""
        self.solving = on
        for control in (self.write_button, self.refresh_button, self.source_box,
                        self.result_box):
            control.Enabled = not on
        self.marker_check.Enabled = not on and not self.sheet_check.Checked

    def _end_solve(self):
        if self.solving:
            self._busy(False)

    def _solve_then_write(self, result):
        self._busy(True)
        self._status(self.t(u"Mecway lost op ...", u"Mecway is solving ..."))
        mw, solution = self.mw, self.solution
        self._post(lambda: bmw.solve_again(mw, solution, result),
                   lambda sets: self._on_solved(sets, result), self._on_solve_failed)

    def _on_solve_failed(self):
        self._end_solve()

    def _on_solved(self, sets, result):
        """Read the new solution, and write when that is done: _write_if_due."""
        self.base_results = sets
        self.result_items = list(sets or [])
        if self.head:
            self._merge_head()
        self._fill_results()
        same = [r for r in self.result_items
                if (r["kind"], r["number"], r.get("configuration")) ==
                (result["kind"], result["number"], result.get("configuration"))]
        if not same:
            self._end_solve()
            self._status(self.t(u"Het gekozen resultaat bestaat niet meer na het oplossen. "
                                u"Kies er een en schrijf opnieuw.",
                                u"The chosen result no longer exists after solving. Choose "
                                u"one and write again."), True)
            return
        self.updating = True
        self.result_box.SelectedIndex = self.result_items.index(same[0])
        self.updating = False
        self._apply()

    def _write_if_due(self):
        if self.solving:
            self._end_solve()
            self._on_write(True)

    def _ask(self, text):
        F = self.F
        return F.MessageBox.Show(self.form, text, u"MecwayAdvancedBeamAnalysis",
                                 F.MessageBoxButtons.YesNo, F.MessageBoxIcon.Question)

    # ---- the position in Mecway ----
    def _on_marker(self):
        mw = self.mw
        if self.marker_check.Checked:
            place = (self.last or {}).get("place")
            if not place or self.vector is None:
                self.marker_check.Checked = False
                return
            vector = self.vector
            self._post(lambda: bmw.show_marker(mw, vector, place), self._marker_shown,
                       self._marker_failed)
            return
        self.marker_timer.Stop()
        self.marker_on = False
        self.marker_due = False
        self.marker_next = None
        switched, self.switched = self.switched, False

        def work():
            removed = bmw.remove_marker(mw)
            if switched:
                bmw.solution_view(mw)
            return removed
        self._post(work, self._marker_removed)

    def _marker_shown(self, info):
        if not self.marker_check.Checked:   # switched off meanwhile
            mw = self.mw

            def work():
                removed = bmw.remove_marker(mw)
                bmw.solution_view(mw)
                return removed
            self._post(work, self._marker_removed)
            return
        self.marker_on = True
        self.marker_name = info["component"]
        self.switched = True
        self._show_warnings()
        self._status(self.t(u"Mecway toont nu het model met de positie.",
                            u"Mecway now shows the model with the position."))

    def _marker_failed(self):
        self.marker_check.Checked = False

    def _marker_removed(self, removed):
        self.warn_start = [] if removed else [{"code": "marker_stuck"}]
        self._show_warnings()

    def _on_marker_timer(self):
        self.marker_timer.Stop()
        self.marker_due = self.marker_on
        self._marker_if_due()

    def _marker_if_due(self):
        """Move when the picture has caught up with the slider."""
        last = self.last
        if self.marker_due and last and last.get("frame") == self.frame_no and \
                last.get("place"):
            self.marker_due = False
            self._move_marker(last["place"])

    def _move_marker(self, place):
        """At most one move waits for Mecway, the newest."""
        if self.marker_busy:
            self.marker_next = place
            return
        self.marker_busy = True
        mw = self.mw

        def done(moved):
            self.marker_busy = False
            place_next, self.marker_next = self.marker_next, None
            if not moved:
                # gone: removed by hand, or the option went off meanwhile
                if self.marker_on:
                    self.marker_on = False
                    self.marker_check.Checked = False
                    self._show_warnings()
            elif place_next is not None and self.marker_on:
                self._move_marker(place_next)

        def failed():
            self.marker_busy = False
        self._post(lambda: bmw.move_marker(mw, place), done, failed)

    def _on_measure(self):
        if self.updating:
            return
        if self.fixed_check.Checked:
            self._fill_fixed()
        if self.detail is not None:
            self._show_tools()      # the shared scale exists for one measure
        self._request()
        self.legend.Invalidate()

    def _on_fixed(self):
        on = self.fixed_check.Checked
        self.fixed_lo.Enabled = self.fixed_hi.Enabled = on
        if on:
            last = self.last
            key = self._measure()
            # not on a restore (Back): the last frame is then the detail's
            if key not in self.fixed and last and "lo" in last and not self.updating:
                self.fixed[key] = (last["lo"], last["hi"])
            self._fill_fixed()
        self._request()

    def _fill_fixed(self):
        pair = self.fixed.get(self._measure())
        was, self.updating = self.updating, True
        try:
            self.fixed_lo.Text = bu._num(pair[0] / 1e6) if pair else u""
            self.fixed_hi.Text = bu._num(pair[1] / 1e6) if pair else u""
        finally:
            self.updating = was

    def _on_fixed_text(self):
        if self.updating:
            return
        D = self.D
        lo = bu.parse_number(self.fixed_lo.Text)
        hi = bu.parse_number(self.fixed_hi.Text)
        good = lo is not None and hi is not None and hi > lo
        back = D.Color.White if good else D.Color.FromArgb(255, 225, 225)
        self.fixed_lo.BackColor = self.fixed_hi.BackColor = back
        if good:
            self.fixed[self._measure()] = (lo * 1e6, hi * 1e6)
            self._request()

    def _on_language(self, code):
        """NL or ENG: all texts and the manual in that language, kept as setting.
        The active one does nothing."""
        if code == bu.language() or code not in dict(LANGS):
            return
        bu.save_setting("language", code)
        self._retext()
        if self._doc_shown():
            self._load_doc()

    def _mark_language(self):
        """The active language filled, the other plain."""
        D = self.D
        active = bu.language()
        accent = D.Color.FromArgb(*ACCENT)
        for code, b in self.lang_buttons.items():
            on = code == active
            b.BackColor = accent if on else D.Color.White
            b.ForeColor = D.Color.White if on else D.Color.FromArgb(31, 41, 51)
            b.FlatAppearance.BorderColor = accent if on else D.Color.FromArgb(200, 205, 212)
            b.FlatAppearance.MouseOverBackColor = (D.Color.FromArgb(0, 102, 180) if on
                                                   else D.Color.FromArgb(235, 241, 248))

    # ------------------------------------------------------------- the manual
    def _doc_shown(self):
        return self.tabs.SelectedIndex == 1

    def _on_tab(self):
        """The user chose a tab. Nothing goes to the worker; the manual is made
        and loaded on its first showing, again after a language change."""
        if self._doc_shown() and self.doc_lang != bu.language():
            self._load_doc()

    def _make_viewer(self):
        F = self.F
        try:
            viewer = F.WebBrowser()
            viewer.ScriptErrorsSuppressed = True
            viewer.AllowWebBrowserDrop = False
            viewer.IsWebBrowserContextMenuEnabled = False
            viewer.Navigating += self._on_doc_navigating
            # shift or middle click, Ctrl+N: no window of its own, ever
            viewer.NewWindow += self._on_doc_new_window
            viewer.DocumentCompleted += self._on_doc_completed
        except Exception:
            # no browser element: the Markdown as plain text
            log("ERROR: no browser element for the manual: {0}".format(traceback.format_exc()))
            viewer = F.TextBox()
            viewer.Multiline = True
            viewer.ReadOnly = True
            viewer.ScrollBars = F.ScrollBars.Vertical
            viewer.Font = self._font()
        viewer.Dock = F.DockStyle.Fill
        self.page_doc.Controls.Add(viewer)
        self.doc_viewer = viewer
        return viewer

    def _is_browser(self):
        return self.doc_viewer is not None and not isinstance(self.doc_viewer, self.F.TextBox)

    def _load_doc(self):
        viewer = self.doc_viewer or self._make_viewer()
        lang = bu.language()
        html, text = bdoc.doc_html(lang)
        self.doc_lang = lang
        self.doc_loads += 1
        if self._is_browser():
            self.doc_loading = True
            viewer.DocumentText = html
        else:
            viewer.Text = text.replace(u"\r\n", u"\n").replace(u"\n", u"\r\n")

    def doc_ready(self):
        """Is the manual on the page (not an empty one)?"""
        try:
            document = self.doc_viewer.Document
            return document is not None and \
                document.GetElementById(bdoc.MARKER_ID) is not None
        except Exception:
            return False

    def _on_doc_completed(self, sender, e):
        """Wait for the manual on the page, not for the event: DocumentText may
        complete twice. A page without the manual that the window did not ask for
        loads the manual again (a safety net: a refresh, F5, keeps the page,
        measured offscreen)."""
        def work():
            if self.closed:
                return
            if self.doc_ready():
                self.doc_loading = False
                self._log_doc_mode()
            elif not self.doc_loading and self.doc_lang is not None:
                # after this event: never set DocumentText inside the browser's own
                self.doc_loading = True
                self._later(self._load_doc)
        self.guarded(work)

    def _later(self, fn):
        """fn on the window thread once the current event is over; not after close."""
        def run():
            if not self.closed and not self.form.IsDisposed:
                self.guarded(fn)
        if not self.closed and not self.form.IsDisposed:
            self.form.BeginInvoke(self.F.MethodInvoker(run))

    def _log_doc_mode(self):
        """Once: the document mode in this program (SVG drawings need 9 or more)."""
        if self.doc_mode_logged:
            return
        self.doc_mode_logged = True
        try:
            # through IDispatch: IronPython's own COM binding gives a method object
            from System.Reflection import BindingFlags
            dom = self.doc_viewer.Document.DomDocument
            mode = dom.GetType().InvokeMember("documentMode", BindingFlags.GetProperty,
                                              None, dom, None)
            log(u"INFO: manual shown, document mode {0} (measured here; SVG needs 9+)".format(mode))
        except Exception as e:
            log(u"INFO: manual shown, document mode not read: {0}".format(e))

    def _on_doc_navigating(self, sender, e):
        """Only the page itself loads; a click is handled here (beam_docs.resolve_link)."""
        url = u"{0}".format(e.Url)
        if url in (u"about:blank", u""):
            return
        e.Cancel = True
        self.guarded(self._follow, url)

    def _follow(self, url):
        kind, target = bdoc.resolve_link(url)
        if kind == "anchor":
            self.doc_scroll_to(target)
        elif kind == "lang":
            # after this event: the language change loads a new page
            self._later(lambda: self._on_language(target))
        elif kind == "web":
            # http or https only (resolve_link): the user's own browser
            from System.Diagnostics import Process
            Process.Start(target)

    def _on_doc_new_window(self, sender, e):
        e.Cancel = True

    def doc_scroll_to(self, anchor):
        """Scroll the manual to the element with id anchor; False when absent."""
        element = self.doc_viewer.Document.GetElementById(anchor)
        if element is None:
            return False
        element.ScrollIntoView(True)
        return True

    def _on_canvas_resize(self):
        self._request(True)

    def _three_d(self):
        return bool(self.sheet_check.Checked) and self.mode3d

    def _camera_base(self):
        """The camera to change: the one to send, else that of the picture."""
        if self.camera is not None:
            return dict(self.camera)
        camera = (self.canvas.shown or {}).get("camera")
        return dict(camera) if camera else None

    def _drag_size(self):
        try:
            size = self.F.SystemInformation.DragSize
            return max(size.Width // 2, self.px(4))
        except Exception:
            return self.px(4)

    def on_canvas_press(self, x, y, button="left"):
        if self._three_d():
            # the user's click in the picture: from now on the wheel zooms
            self.wheel_armed = True
            base = self._camera_base()
            self.drag = None if base is None else {
                "x": x, "y": y, "button": button, "base": base, "moved": False}
            return
        if button == "left" and not self.sheet_check.Checked:
            self.press = (x, y) + self._centre()

    def on_canvas_release(self):
        """End of a drag; the fine picture follows the coarse ones by itself. A
        reply of the newest request that came during the drag gives its
        normalised camera now."""
        moved = self.drag is not None and self.drag["moved"]
        self.drag = None
        last = self.last or {}
        if moved and "camera" in last and last.get("frame") == self.frame_no:
            self.camera = last["camera"]

    def on_canvas_blur(self):
        self.wheel_armed = False
        self.drag = None

    def _drag_3d(self, x, y, button):
        """Rotate (left) or pan (right, middle) from the camera at the press; only
        after a few pixels, so a double click does not turn the view."""
        d = self.drag
        if button != d["button"]:
            return
        dx, dy = x - d["x"], y - d["y"]
        if not d["moved"]:
            limit = self._drag_size()
            if abs(dx) <= limit and abs(dy) <= limit:
                return
            d["moved"] = True
        base = d["base"]
        if button == "left":
            camera = dict(base)
            camera["azimuth"] = float(base.get("azimuth", 0.0)) - dx * _ROT_DEG / self.k
            camera["elevation"] = float(base.get("elevation", 0.0)) + dy * _ROT_DEG / self.k
        else:
            px0, py0 = _pan_of(base)
            camera = _with_pan(base, px0 + dx, py0 + dy)
        self.camera = camera
        self._request(True)

    def _wheel_3d(self, x, y, delta):
        """Zoom towards the mouse: the point under it stays."""
        if not self.wheel_armed or not self.opened:
            return
        base = self._camera_base()
        if base is None:
            return
        try:
            zoom0 = float(base.get("zoom", 1.0))
        except (TypeError, ValueError):
            return
        if not zoom0 > 0.0:
            return
        f = 1.25 if delta > 0 else 0.8
        zoom = zoom0 * f
        size = self.canvas.ClientSize
        cx, cy = 0.5 * size.Width, 0.5 * size.Height
        pan_x, pan_y = _pan_of(base)
        camera = _with_pan(base, x - cx - f * (x - cx - pan_x), y - cy - f * (y - cy - pan_y))
        camera["zoom"] = zoom
        self.camera = camera
        self._request(True)

    def on_canvas_double(self, x, y):
        """One section: Fit. Several: the section under the mouse in detail."""
        if not self.sheet_check.Checked:
            self.on_fit()
            return
        if not self.opened or self.link is None:
            return
        self.drag = None
        if self._shown_3d():
            self.pick = {"x": x, "y": y, "tries": 0, "gen": self.gen,
                         "t": time.time()}
            self._send_pick()
            return
        for tile in self.canvas.tiles or []:
            if "x" in tile and tile["x"] <= x < tile["x"] + tile["w"] and \
                    tile["y"] <= y < tile["y"] + tile["h"]:
                self.open_detail(tile)
                return

    def _send_pick(self):
        pick = self.pick
        self.link.probe({"cmd": "probe", "gen": self.gen, "frame": self.painted,
                         "x": pick["x"], "y": pick["y"], "pick": True})

    def _picking(self):
        """A double click in 3D waits for its probe (hover probes would replace
        it); a lost reply frees the hover after a while."""
        pick = self.pick
        if pick is not None and (pick["gen"] != self.gen or
                                 time.time() - pick["t"] > _PICK_S):
            self.pick = pick = None
        return pick is not None

    def _on_pick(self, reply, request):
        pick = self.pick
        if pick is None or request.get("gen") != self.gen:
            return
        number = reply.get("number")
        if number is None and reply.get("frame") != self.painted and pick["tries"] < 1:
            pick["tries"] += 1          # a newer frame came between: once more on it
            pick["t"] = time.time()
            self._send_pick()
            return
        self.pick = None
        if not self.sheet_check.Checked or not self._shown_3d():
            return
        tiles = [t for t in (self.canvas.tiles or []) if "number" in t]
        tile = None
        if number is not None:
            tile = ([t for t in tiles if t["number"] == number] or [None])[0]
        if tile is None:
            # beside the section or on its edge: the nearest number, if close
            best = None
            for t in tiles:
                a = _anchor(t.get("anchor"))
                if a is None:
                    continue
                d = ((a[0] - pick["x"]) ** 2 + (a[1] - pick["y"]) ** 2) ** 0.5
                if d <= self.px(_PICK_PX) and (best is None or d < best[0]):
                    best = (d, t)
            tile = best[1] if best else None
        if tile is not None:
            self.open_detail(tile)

    def _centre(self):
        """Model point at the centre of the canvas, and pixel scale."""
        v = (self.last or {}).get("view")
        if not v:
            return (0.0, 0.0, 1.0, 1.0)
        size = self.canvas.ClientSize
        return (v["x0"] + 0.5 * size.Width / v["s"],
                v["y1"] - 0.5 * size.Height / v["s"], v["s"], v["zoom"])

    def on_canvas_move(self, x, y, dragging):
        """dragging: the button held ("left", "right", "middle"), True for left,
        or None."""
        if dragging is True:
            dragging = "left"
        if self._three_d():
            if dragging:
                if self.drag is not None and self.opened:
                    self._drag_3d(x, y, dragging)
                return
            if self._picking():
                return
        elif dragging != "left":
            dragging = None
        if dragging and self.press is not None and self.opened:
            x0, y0, cx, cy, s, zoom = self.press
            if zoom > 1.0:
                self.view = {"zoom": zoom, "cx": cx - (x - x0) / s,
                             "cy": cy + (y - y0) / s}
                self._request(True)
            return
        if self.opened and self.link is not None and self.painted:
            self.link.probe({"cmd": "probe", "gen": self.gen, "frame": self.painted,
                             "x": x, "y": y})

    def on_canvas_leave(self):
        self.hover_label.Text = u""

    def on_canvas_wheel(self, x, y, delta):
        if self._three_d():
            self._wheel_3d(x, y, delta)
            return
        v = (self.last or {}).get("view")
        if not v or not self.opened or self.sheet_check.Checked:
            return
        zoom = min(max(v["zoom"] * (1.25 if delta > 0 else 0.8), 1.0), _MAX_ZOOM)
        if zoom <= 1.0:
            self.on_fit()
            return
        s = v["s"] / v["zoom"] * zoom
        size = self.canvas.ClientSize
        mv, mw = v["x0"] + x / v["s"], v["y1"] - y / v["s"]
        self.view = {"zoom": zoom, "cx": mv - (x - 0.5 * size.Width) / s,
                     "cy": mw + (y - 0.5 * size.Height) / s}
        self._request(True)

    def on_fit(self):
        """One section: the whole section. 3D: the default camera (Fit button)."""
        if self._three_d():
            self.camera = None
            self.drag = None
            self._request()
            return
        self.view = None
        self._request(True)

    # ------------------------------------------------------------ drawn parts
    def tile_title(self, tile):
        """Number, position and largest value of one picture of a sheet."""
        if "no_section" in tile:
            return self.t(u"{0}.  {1}: geen doorsnede", u"{0}.  {1}: no section").format(
                tile["number"], bu.fmt_length(tile["s"]))
        return u"{0}.  {1}:  max {2}".format(tile["number"], bu.fmt_length(tile["s"]),
                                             bu.fmt_stress(tile["outside"]))

    def tile_texts_for(self, tile):
        """The titles of a tile from long to short; the number is in every one."""
        number, place = tile["number"], bu.fmt_length(tile["s"])
        return [self.tile_title(tile), u"{0}.  {1}".format(number, place),
                u"{0}.".format(number)]

    def _title_format(self):
        fmt = self.D.StringFormat(self.D.StringFormat.GenericDefault)
        fmt.FormatFlags = self.D.StringFormatFlags.NoWrap
        fmt.Trimming = self.D.StringTrimming.EllipsisCharacter
        return fmt

    def draw_tile_labels(self, g):
        """Number, position and largest value above each picture of a sheet: the
        longest title that fits the tile, so a narrow tile keeps at least its number."""
        D = self.D
        black = D.Brushes.Black
        font = self._font(8.0)
        fmt = self._title_format()
        self.tile_texts = []
        try:
            for tile in self.canvas.tiles:
                if "x" not in tile:
                    continue
                room = float(tile["w"] - self.px(8))
                texts = self.tile_texts_for(tile)
                text = texts[-1]
                for option in texts:
                    if g.MeasureString(option, font, D.SizeF(10000.0, 1000.0), fmt).Width <= room:
                        text = option
                        break
                self.tile_texts.append(text)
                box = D.RectangleF(float(tile["x"] + self.px(4)), float(tile["y"] + self.px(2)),
                                   max(room, 1.0), float(tile["band"]))
                g.DrawString(text, font, black, box, fmt)
        finally:
            fmt.Dispose()

    def draw_overlay(self, g):
        """Text over the picture of the worker: the tile titles in 2D; in 3D the
        numbers at their anchors, the axis labels and the magnification."""
        shown = self.canvas.shown or {}
        if "camera" not in shown:
            self.canvas_labels = []
            if self.canvas.tiles:
                self.draw_tile_labels(g)
            return
        D = self.D
        px = self.px
        labels = []
        back = D.SolidBrush(D.Color.FromArgb(200, 255, 255, 255))
        try:
            def boxed(text, font, brush, x, y, centre=True):
                size = g.MeasureString(text, font)
                left = x - 0.5 * size.Width if centre else x
                top = y - 0.5 * size.Height if centre else y
                g.FillRectangle(back, float(left), float(top), float(size.Width),
                                float(size.Height))
                g.DrawString(text, font, brush, float(left), float(top))
                labels.append((text, left, top, size.Width, size.Height))
            bold = self._font(8.0, True)
            for tile in shown.get("tiles") or []:
                a = _anchor(tile.get("anchor"))
                if a is not None and tile.get("number") is not None:
                    boxed(u"{0}".format(tile["number"]), bold, D.Brushes.Black, a[0], a[1])
            axis = self._font(9.0, True)
            grey = D.Brushes.DimGray
            for label, a in axis_anchors(shown.get("axes"), px(8)):
                boxed(label, axis, grey, a[0], a[1])
            k = shown.get("magnify")
            if k is not None and abs(float(k) - 1.0) > 1e-6:
                text = self.t(u"doorsneden {0}× vergroot, niet op schaal",
                              u"sections {0}× magnified, not to scale").format(
                    fmt_times(k))
                # bottom right: the axes usually stand in the bottom left corner
                small = self._font(8.0)
                size = g.MeasureString(text, small)
                area = self.canvas.ClientSize
                boxed(text, small, grey, float(max(area.Width - size.Width - px(6), 0)),
                      float(area.Height - size.Height - px(6)), centre=False)
        finally:
            back.Dispose()
        self.canvas_labels = labels

    def _draw_legend(self, g, w, h):
        from System import Array, Single
        from System.Drawing.Drawing2D import ColorBlend, LinearGradientBrush
        D = self.D
        g.Clear(D.Color.White)
        last = self.last
        if not last or "lo" not in last:
            return
        px = self.px
        black = D.Brushes.Black
        grey = D.Brushes.DimGray
        font = self._font(9.0)
        small = self._font(8.0)
        names = dict((m[0], m) for m in bu.MEASURES)
        m = names.get(last.get("measure"))
        if m:
            g.DrawString(bu.tr(m[1], m[2]) + u" (MPa)", self._font(9.0, True), black,
                         0.0, 0.0)
        top = px(44)
        bar_w = px(22)
        info_h = px(112)
        bar_h = max(h - top - px(12) - info_h, px(40))
        rect = D.Rectangle(0, top, bar_w, bar_h)
        blend = ColorBlend(len(bu.RAMP))
        blend.Colors = Array[D.Color]([D.Color.FromArgb(r[1], r[2], r[3])
                                       for r in reversed(bu.RAMP)])
        blend.Positions = Array[Single]([float(1.0 - r[0]) for r in reversed(bu.RAMP)])
        brush = LinearGradientBrush(rect, D.Color.Black, D.Color.White, 90.0)
        brush.InterpolationColors = blend
        g.FillRectangle(brush, D.Rectangle(0, top, bar_w, bar_h + 1))
        brush.Dispose()
        g.DrawRectangle(D.Pens.Gray, 0, top, bar_w, bar_h)
        lo, hi = last["lo"], last["hi"]
        steps = float(bu.LEGEND_STEPS)
        for i in range(bu.LEGEND_STEPS + 1):
            y = top + bar_h * i / steps
            value = hi - (hi - lo) * i / steps
            g.DrawLine(D.Pens.Gray, float(bar_w), float(y), float(bar_w + px(4)), float(y))
            g.DrawString(bu._num(value / 1e6), small, black, float(bar_w + px(6)),
                         float(y - px(8)))
        if last.get("clip_hi"):
            g.DrawString(self.t(u"▲ hoger bestaat", u"▲ higher exists"), small,
                         grey, 0.0, float(top - px(18)))
        if last.get("clip_lo"):
            g.DrawString(self.t(u"▼ lager bestaat", u"▼ lower exists"), small,
                         grey, 0.0, float(top + bar_h + px(4)))
        y = top + bar_h + px(24)
        lines = []
        if not last.get("outside_empty"):
            lines.append((u"○", self.t(u"max buiten hoeken", u"max outside corners"),
                          last["outside"]["value"]))
        if last.get("peak"):
            lines.append((u"✕", self.t(u"piek in hoek", u"peak in corner"),
                          last["peak"]["value"]))
        if last.get("arrow_max"):
            lines.append((u"→", self.t(u"langste pijl", u"longest arrow"),
                          last["arrow_max"]))
        for mark, text, value in lines:
            g.DrawString(u"{0} {1}".format(mark, text), small, grey, 0.0, float(y))
            g.DrawString(bu.fmt_stress(value), font, black, float(px(10)), float(y + px(14)))
            y += px(32)

    def _draw_side(self, g, w, h):
        D = self.D
        g.Clear(D.Color.White)
        self.side_labels, self.side_dots, self.side_blocked = [], [], []
        chain = self.chain
        if not chain or not chain.get("side"):
            return
        px = self.px
        pts = chain["side"]["points"]
        arc = chain["side"]["s"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = px(10)
        scale = (w - 2 * pad) / max(x1 - x0, 1e-12)
        if y1 > y0:
            scale = min(scale, (h - px(22)) / (y1 - y0))
        mx = 0.5 * (w - scale * (x1 - x0))
        my = 0.5 * (h - px(14)) + 0.5 * scale * (y1 - y0)

        def at(p):
            return D.PointF(float(mx + scale * (p[0] - x0)),
                            float(my - scale * (p[1] - y0)))
        pen = D.Pen(D.Color.FromArgb(31, 59, 87), max(2.0 * self.k, 1.0))
        tick = D.Pens.Gray
        for a, b in zip(pts[:-1], pts[1:]):
            g.DrawLine(pen, at(a), at(b))
        for p in pts:
            q = at(p)
            g.DrawLine(tick, q.X, q.Y - px(4), q.X, q.Y + px(4))
        pen.Dispose()
        # the checkbox decides, the reply only gives the places: between a click and
        # the next reply there is no dot and no number of the other mode
        last = self.last or {}
        if self.sheet_check.Checked:
            places = ([(tile["s"], tile["number"]) for tile in last["tiles"]]
                      if last.get("sheet") else [])
        else:
            places = [(self._position(), None)]
        dots = []
        for s, number in places:
            for i in range(len(arc) - 1):
                if s <= arc[i + 1] or i == len(arc) - 2:
                    d = max(arc[i + 1] - arc[i], 1e-12)
                    t = min(max((s - arc[i]) / d, 0.0), 1.0)
                    p = [pts[i][0] + t * (pts[i + 1][0] - pts[i][0]),
                         pts[i][1] + t * (pts[i + 1][1] - pts[i][1])]
                    q = at(p)
                    r = float(px(5))
                    g.FillEllipse(D.Brushes.Crimson, q.X - r, q.Y - r, 2 * r, 2 * r)
                    dots.append((number, q.X, q.Y))
                    break
        small = self._font(8.0)
        text = bu.fmt_length(chain["length"])
        zero, size = g.MeasureString(u"0", small), g.MeasureString(text, small)
        ends = [(0.0, float(h - px(15)), zero.Width, zero.Height),
                (float(w - size.Width), float(h - px(15)), size.Width, size.Height)]
        lines = [((at(a).X, at(a).Y), (at(b).X, at(b).Y)) for a, b in zip(pts[:-1], pts[1:])]
        self._draw_side_numbers(g, dots, w, h, ends, lines)
        g.DrawString(u"0", small, D.Brushes.Gray, ends[0][0], ends[0][1])
        g.DrawString(text, small, D.Brushes.Gray, ends[1][0], ends[1][1])

    def _draw_side_numbers(self, g, dots, w, h, blocked, lines):
        """The numbers of the sections at their dots, as on the tiles (place_labels)."""
        black = self.D.Brushes.Black
        font = self._font(8.0, True)
        self.side_dots = [d for d in dots if d[0] is not None]
        self.side_blocked = list(blocked) + [("line",) + a + b for a, b in lines]
        self.side_labels = []
        if not self.side_dots:
            return
        texts = [u"{0}".format(d[0]) for d in self.side_dots]
        sizes = [(m.Width, m.Height) for m in (g.MeasureString(t, font) for t in texts)]
        args = ([(float(d[1]), float(d[2])) for d in self.side_dots],
                [(float(a), float(b)) for a, b in sizes], float(w), float(h),
                float(self.px(6)), float(self.px(5)),
                [tuple(float(v) for v in box) for box in blocked],
                [((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))) for a, b in lines])
        key = repr(args)
        # a frame repaints the side view; the places change only with the geometry
        if self._label_cache is None or self._label_cache[0] != key:
            self._label_cache = (key, place_labels(*args))
        spots = self._label_cache[1]
        for i, left, top in spots:
            self.side_labels.append((self.side_dots[i][0], left, top) + sizes[i])
            g.DrawString(texts[i], font, black, float(left), float(top))

    # ------------------------------------------------------------------ close
    def _on_closing(self, sender, e):
        self.closed = True
        self.timer.Stop()
        self.marker_timer.Stop()
        if self.link is not None:
            self.link.stop()

    def shutdown(self):
        """After the window: end the worker. Safe to call twice."""
        self.closed = True
        link, self.link = self.link, None
        if link is not None:
            link.finish()
            self._log_times(link.slot.replaced)

    def _log_times(self, replaced):
        """The numbers of check V12 (speed inside Mecway)."""
        times, self.times = self.times, []
        if not times:
            return
        round_trip = [t[0] + t[2] for t in times]
        worker = [t[1] for t in times]
        pipe = [t[0] - t[1] for t in times]
        paint = [t[2] for t in times]
        log(u"INFO: frames {0}; replaced {1}; ms median/p95: round trip {2:.1f}/{3:.1f}, "
            u"worker {4:.1f}/{5:.1f}, pipe and panel {6:.1f}/{7:.1f}, paint {8:.1f}/{9:.1f}"
            .format(len(times), replaced, _median(round_trip), _p95(round_trip),
                    _median(worker), _p95(worker), _median(pipe), _p95(pipe),
                    _median(paint), _p95(paint)))
