# Changelog

## V2026.1 (2026-11-10)

First release.

- **Stress at every point of a beam section** from the six section forces and the shape of the section: σ, τ, von Mises,
  Tresca and the principal stresses in colour, the shear stress as arrows. Standard profiles and general sections with a
  `SECTIE-VORM` of MecwaySectionProps.
- **Window in Mecway**: a beam from the selection or a named selection, a slider along the beam, each section force on and
  off, a fixed colour scale, the values under the mouse, contour lines on the levels of the scale.
- **Beams**: one continuous chain of line2 or line3 elements, also with kinks (each section in its own element) and reversed
  elements (drawn mirrored). The window compares the saved file with the model and asks to save when they differ.
- **Several sections**: up to twelve, evenly along the beam, on one colour scale, numbered along the chain.
- **3D view**: the same sections at their place along the beam; turn, move, zoom and *Fit*; the default view is meant to
  match Mecway's (Y up; NOT verified next to Mecway).
  The sections can be magnified about their centroid; the hover gives true values.
- **Detail**: a double click opens one section alone at its exact position; *Back* restores the overview.
- **Torsion about the shear centre**: T_S = T + EY·Vw − EZ·Vv, with a switch; the term is shown apart.
- **Sharp re-entrant corners**: the main number is the maximum outside the corner zones; the corner peak is shown as such.
- **Distributed load and gravity**: the course within an element is restored for Mecway's line2, CCX line3 and Code-Aster.
- **Solvers**: Mecway internal, CCX and Code-Aster through MecwayAsterBridge; a solver without section forces, such as
  Mystran, gives σ only. EY and EZ of an `#ASTER-SECTIE` line count only when its A, IY, IZ and JX still fit the section,
  as in the bridge.
- **Configurations**: every result per configuration; the tool makes that configuration active in Mecway itself, because
  the active one cannot be read. A load or orientation suppressed in a configuration is never used unnoticed.
- **Show position in Mecway**: an optional temporary element in the model view.
- **Write to Mecway results**: the maxima outside the corners per node of the beam as `sectionvonmises`, `sectionsigma`
  and `sectiontau`.
- **Language**: NL and ENG buttons; the choice is kept. The version is in the window title and the log.
- **Documentation** in Dutch and English, also on the window's second tab *Manual*, with numbered window images,
  drawings and a list of all messages of the window.
- **Validation** against own solid models in Code-Aster on five sections: `tests/validation/RESULTS.md`.
- **Requirements**: Mecway 35, Python 3 with numpy and scipy, MecwaySectionProps V2026.4 or newer for general sections,
  optionally MecwayAsterBridge V2026.9 or newer for Code-Aster solutions.
