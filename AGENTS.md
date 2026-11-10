# AGENTS.md

MecwayAdvancedBeamAnalysis: a window in Mecway that shows the stress at every point of a beam
section, along the length of a straight beam. Research and proposal:
`docs/research/01_vooronderzoek.md`. User docs: `docs/` (Dutch) and `docs/en/` (English), same
structure; keep both up to date. Docs use numbered screenshots that the text explains one by one, and
clear drawings instead of long text.

## Hard rules

- **Never name the commercial reference program** that inspired this tool, anywhere: code, docs,
  commits, file names. No company names, personal names (authors in source references are fine), own
  paths, e-mail addresses, customer or order data.
- Mecway models in this repository are own, self-made examples.
- Do not copy text from Mecway's manual or API reference; API names and short signatures are fine.
- No code from AGPL or LGPL projects, no Triangle (not even through `sectionproperties`).
- Measuring windows: a normal window with border and taskbar button, not `TopMost`, never takes the
  focus (`ShowWithoutActivation`; no `SetForegroundWindow`, `Activate`, `BringToFront`), one window per
  measurement, closes itself in `finally`. A covered window is reported as invalid, never pulled forward.
- Mecway runs: hidden, own models only, safe runner (gate, timeout, kill own PID only), not while the
  user works in Mecway.

## Runtime

- Inside Mecway (IronPython 2.7.9): `MecwayAdvancedBeamAnalysis.py`, `beam_gui.py`, `beam_mecway.py`,
  `beam_utils.py`, `beam_docs.py` (the manual on the window's second tab). Python 2.7: no f-strings,
  type hints or `pathlib`. Standard library plus `clr`.
- `mw` and `solution` exist only in the entry script; `beam_mecway.py` is the only module that calls
  them. All `mw` calls run on Mecway's script thread; the window has its own STA thread.
- The calculation is CPython 3 with numpy and scipy only: `section_stress.py`, `section_fem.py`,
  `section_mesh.py`, `section_profiles.py`, `beam_model.py`, `beam_render.py`, `beam_worker.py`. That
  Python is a prerequisite the user installs, never bundled; found as in MecwaySectionProps
  (`find_python`: Code-Aster's first, never pip in it).
- `beam_utils.py` and `beam_docs.py` work in both.

## Data flow

- From the API (current solution, also unsaved): selection, element and node numbers, section forces
  per element end through `solution.element_node_value(var, eid, k)`, `k` = 1..n. Fallback for solvers
  without element values: `node_value`; then the kinematics (sigma only).
- From the saved `.liml` (no API for these): orientation, materials, `SECTIE-VORM`, `#ASTER-SECTIE`,
  loads. `beam_model` stops reading at `<solution>`. The window compares the file with the API (counts,
  coordinates) and asks to save when they differ.
- Stored only with the field variables `tensile force`, `shear force`, `torsion moment` and
  `bending moment 2`. The tool never writes them: a wrong name gives a modal dialog.
- Panel and worker: `System.Diagnostics.Process` with `BaseStream`, never `subprocess.Popen` pipes;
  one request at a time, only the newest slider position is rendered; the panel never blocks on the
  worker. Drawing with `SetDIBitsToDevice`.

## Interfaces

`section_stress.SectionField(loops, nu, props, offset, n_target, n_across)` and `measures(s)`: see the
file header.

`section_profiles.loops_for(geometric, shape=None)`: `geometric` is the dict of attributes of a
`<geometric>` element, values as strings with units; `shape` the parsed `SECTIE-VORM`. Returns
`{"loops", "props"}`, loops in V, W about the centroid (with Mecway's axes for L and C as measured in
MecwayAsterBridge `bridge_warping.mecway_frame`), or raises `SectionError` (`no_shape`, `stale_shape`, ...).

`beam_model`:

- `read_model(path, configuration=None)` -> `Model` with nodes, elements, materials, loads, section
  shapes, `.solver`, `.analysis`, `.aster`, `.fields`.
- `build_chain(model, element_ids)` -> `Chain`: `.elements`, ordered, each with `eid, s0, s1, length,
  reversed, frame (U, V, W), material`; `.length`; `.warnings` (codes, text in the window); raises
  `ChainError` when the elements do not form one line.
- `forces_at(chain, raw, s)` -> `{"element", "xi", "forces", "element_forces", "warnings"}`: section
  forces at arc length `s`, in the axes of the section as drawn; on a node the downstream element counts.
  `raw` = `{eid: {var: [end 1, end 2]}}` with the Mecway names. Applies the correction for distributed
  loads where it holds (research note par. 5.4).
- `forces_from_kinematics(chain, raw)` -> Mecway end values for `forces_at`, sigma only.
- `section_key(element)` -> hashable key, so that a `SectionField` is built once.
- `section_spec(model, element, aster_offset=False)` -> loops in the axes as drawn (mirrored for reversed
  elements), properties, offset.

Warnings and errors are codes with data; text is in `beam_utils.tr(nl, en)`.

## Checking

```bash
python tests/test_section_stress.py
python tests/test_section_profiles.py
python tests/test_beam_model.py
python tests/test_beam_render.py
python tests/test_beam_render3d.py
python tests/test_beam_worker.py
python tests/test_beam_worker3d.py
python tests/test_beam_aster.py
python tests/test_beam_write.py
python tests/test_beam_docs.py
python tests/test_mecway_e2e.py
python tests/test_section_copies.py
powershell -ExecutionPolicy Bypass -File tools/run_ipy.ps1 -Script tests/test_beam_gui.py
```

`tools/gui_cap.py` and `tests/test_beam_gui.py` make the window offscreen and never show it, unlike
MecwaySectionProps.

Tests rerun themselves with a Python with numpy and scipy. In Mecway's IronPython without Mecway:
`powershell -ExecutionPolicy Bypass -File tools/run_ipy.ps1 -Script <test>`. Every finished part gets
its tests and an independent code review before it counts as done.

## Conventions

- License MPL-2.0; a new source file gets the header of `Scripts/section_stress.py`.
- `section_fem.py` and `section_mesh.py` are copies of MecwaySectionProps; never edit them here.
- MecwayAsterBridge keeps copies of `section_stress.py`, `section_profiles.py`, `section_fem.py` and
  `section_mesh.py`; its `tests/test_bridge_section_stress.py` fails when a copy is behind. After a change
  here, update those copies there too, following that repository's own coordination notes.
- English for code comments, docstrings, log lines and commit messages; user text in both languages
  with `tr(nl, en)` (Dutch first).
- Label a claim about Mecway or Code-Aster as measured, or say NOT verified.
- `WorkDir/` is local.
