# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of Scripts/beam_docs.py: the Markdown of the manuals as one HTML page, and a
# check of both manuals (every image found, every link inside the document and every
# § reference with a target). The page in the browser element of the window is in
# tests/test_beam_gui.py. Runs in CPython and in Mecway's IronPython.
#
#   python tests/test_beam_docs.py

from __future__ import print_function

import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(globals().get("__file__", sys.argv[0])))
sys.path.insert(0, os.path.join(_HERE, "..", "Scripts"))
sys.dont_write_bytecode = True

import beam_docs as bd  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print((u"OK   " if ok else u"FAIL ") + name +
          (u" ({0})".format(detail) if u"{0}".format(detail) != u"" else u""))
    if not ok:
        FAILED.append(name)


# ------------------------------------------------------------------ the converter
check("slug as GitHub", bd.slug(u"3.1 Eén doorsnede") == u"31-eén-doorsnede" and
      bd.slug(u"5.3 Schuifspanning en het `schuifcentrum`") == u"53-schuifspanning-en-het-schuifcentrum",
      bd.slug(u"3.1 Eén doorsnede"))
folder = os.path.join(bd.docs_dir())
md = u"""# Title

<!-- a comment that stays out -->

[Two](#2-twee) and [same](#same) and [again](#same-1); see §2 and §2.1. Not §9.

## 2. Twee

### 2.1 Sub

## Same

## Same

| A | B |
|---|---|
| `x|` | **b** |

1. Item with code:

   ```xml
   <a b="1" />
   ```

2. Next, *italic* and `*not*` and **`bold code`-rule**.

![picture](images/fig_axes_nl.svg) ![gone](images/none.png)

[English](en/MecwayAdvancedBeamAnalysis.md) [Nederlands](../MecwayAdvancedBeamAnalysis.md)
[web](https://example.org/x)
"""
html = bd.markdown_to_html(md, folder, u"t")
check("charset is the first thing in head", html.index(u'<meta charset="utf-8">') < html.index(u"X-UA"))
check("ASCII ids on the headings", all(u'id="h-{0}"'.format(k) in html for k in range(1, 6)))
check("a GitHub anchor goes to the id", u'<a href="#h-2">Two</a>' in html, html[:0])
check("a repeated heading: -1 as GitHub", u'<a href="#h-4">same</a>' in html and
      u'<a href="#h-5">again</a>' in html)
check("§ references link to the numbered heading", u'<a href="#h-2">§2</a>' in html and
      u'<a href="#h-3">§2.1</a>.' in html)
check("a § without a heading stays text", u"Not §9." in html)
check("comments left out", u"comment" not in html)
check("a table with a header and a row", u"<th>A</th>" in html and u"<td><b>b</b></td>" in html)
check("code is escaped", u"&lt;a b=\"1\" /&gt;" in html and u"<pre>" in html)
check("the indented block stays in its list item",
      html.index(u"<pre>") < html.index(u"Next") and html.count(u"<ol") == 1, html.count(u"<ol"))
check("italic, code not italic, bold around code", u"<i>italic</i>" in html and
      u"<code>*not*</code>" in html and u"<b><code>bold code</code>-rule</b>" in html)
check("an SVG as a data URI", u'src="data:image/svg+xml;base64,' in html)
check("a missing image as its text", u"[gone]" in html)
check("the other manual becomes a language fragment", u'href="#lang-en"' in html and
      u'href="#lang-nl"' in html)
check("a web link stays", u'href="https://example.org/x"' in html)
check("the marker element", u'id="{0}"'.format(bd.MARKER_ID) in html)

# ------------------------------------------------------------------ the links
check("resolve: anchor", bd.resolve_link(u"about:blank#h-12") == ("anchor", u"h-12"))
check("resolve: language", bd.resolve_link(u"about:blank#lang-en") == ("lang", u"en"))
check("resolve: web", bd.resolve_link(u"https://example.org") == ("web", u"https://example.org"))
check("resolve: nothing else", bd.resolve_link(u"about:images/x.png") == (None, None) and
      bd.resolve_link(u"about:blank") == (None, None) and bd.resolve_link(None) == (None, None))
check("resolve: an unknown language is not one", bd.resolve_link(u"about:blank#lang-fr")[0] == "anchor")

# ------------------------------------------------------------------ both manuals
for lang in ("nl", "en"):
    path = bd.doc_path(lang)
    text = bd.read_text(path)
    page, raw = bd.doc_html(lang)
    images = len(re.findall(u"!\\[", text))
    found = page.count(u'<img alt="')
    check(lang + ": every image found", images > 0 and found == images and u'class="noimg"' not in page,
          (found, images))
    ids = set(re.findall(u'id="(h-\\d+)"', page))
    targets = re.findall(u'href="#([^"]+)"', page)
    inside = [t for t in targets if not t.startswith(bd.LANG_PREFIX)]
    check(lang + ": every link inside the document has a target", inside and
          all(t in ids for t in inside), [t for t in inside if t not in ids][:5])
    refs = re.findall(u"§\\s?(\\d+(?:\\.\\d+)*)", re.sub(u"`[^`]*`", u"", text))
    anchors = bd._Anchors(text)
    check(lang + ": every § reference has a heading", refs and
          all(r in anchors.by_number for r in refs),
          sorted(set(r for r in refs if r not in anchors.by_number)))
    check(lang + ": the other manual is a language link",
          u'href="#lang-{0}"'.format("en" if lang == "nl" else "nl") in page)
    check(lang + ": a table and code", u"<table>" in page and u"<pre>" in page)
    contents = re.findall(u"\\]\\(#([^)]+)\\)", text)
    check(lang + ": a contents list with links to the chapters", len(contents) >= 10, len(contents))
    check(lang + ": nothing from the Markdown left over", u"](" not in re.sub(
        u"<pre>.*?</pre>|<code>.*?</code>", u"", page, flags=re.DOTALL) and u"```" not in page)
# ------------------------------------------------------------------ the release
import beam_utils as bu  # noqa: E402


def _read(*parts):
    with io.open(os.path.join(_HERE, u"..", *parts), encoding="utf-8") as f:
        return f.read()


heading = re.search(u"^## (.+)$", _read(u"CHANGELOG.md"), re.MULTILINE)
check("the newest CHANGELOG entry is this version, with a date", heading is not None and re.match(
    u"^{0} \\(\\d{{4}}-\\d{{2}}-\\d{{2}}\\)$".format(re.escape(bu.VERSION)), heading.group(1)),
    heading.group(1) if heading else u"no entry")
versions = [set(re.findall(u"V\\d{4}\\.\\d+", _read(*p))) for p in (
    (u"README.md",), (u"docs", u"MecwayAdvancedBeamAnalysis.md"),
    (u"docs", u"en", u"MecwayAdvancedBeamAnalysis.md"))]
check("README and both manuals name the same versions", versions[0] == versions[1] == versions[2],
      versions)
saved = bd.docs_dir
bd.docs_dir = lambda: os.path.join(_HERE, u"no_such_folder")
page, raw = bd.doc_html("en")
bd.docs_dir = saved
check("a missing manual gives a page that says so", u"Manual not found" in page and raw == u"")

print("{0} failed".format(len(FAILED)) if FAILED else "All checks OK")
sys.exit(1 if FAILED else 0)
