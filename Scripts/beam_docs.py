# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# beam_docs.py
# The manual on the tab Handleiding/Manual of the window (beam_gui): the Markdown
# of docs/ (Dutch) or docs/en/ (English) as one HTML page for the built-in browser
# element. IronPython 2.7.9 and CPython 3, standard library only.
#
# The conversion knows what the manuals use: headings (# .. ####), paragraphs,
# lists with indentation (also a ``` block inside a list item), tables, ```
# blocks, inline code, bold, italic, links, images and HTML comments (left out).
# Not known: a | inside a table cell (also in inline code); HTML comments are
# left out also inside a ``` block.
# The same approach as the documentation page of MecwayAsterBridge (same author).
#
# PAGE. Loaded through DocumentText, so its address is about:blank: images go in
# as data URIs (PNG and SVG; an SVG <img> needs document mode 9 or higher, hence
# the IE=edge meta, measured offscreen in Mecway's IronPython), and every link is
# handled by the window (resolve_link), never by the browser element.
#
# ANCHORS. Every heading gets an ASCII id "h-<n>" (n counts the headings): a
# fragment with accents does not come back unchanged from the browser element. A
# Markdown link "#slug" (the anchor GitHub makes) points to that id, "§3.2" in
# the text links to the heading numbered 3.2, and the link to the manual in the
# other language becomes "#lang-en" or "#lang-nl" (the window switches language).

import base64
import io
import os
import re

DOC_NAME = u"MecwayAdvancedBeamAnalysis.md"
# the element whose presence says the page is the manual (not an empty page)
MARKER_ID = u"beam-doc"
LANG_PREFIX = u"lang-"

_CSS = (u"body{font-family:'Segoe UI',Arial,sans-serif;font-size:10pt;"
        u"margin:12px 18px;color:#1f2933;line-height:1.45;background:#ffffff}"
        u"h1{font-size:16pt;color:#24364b}"
        u"h2{font-size:13pt;color:#24364b;border-bottom:2px solid #dadfe6;"
        u"padding-bottom:2px;margin-top:20px}"
        u"h3{font-size:11pt;color:#1f6fb2}h4{font-size:10pt}"
        u"a{color:#1f6fb2}"
        u"table{border-collapse:collapse;margin:8px 0}"
        u"td,th{border:1px solid #dadfe6;padding:4px 8px;vertical-align:top}"
        u"th{background:#eef2f6;text-align:left}"
        u"code{background:#eef2f6;padding:0 3px;font-family:Consolas,monospace}"
        u"pre{background:#f3f5f8;border-left:3px solid #1f6fb2;padding:8px;"
        u"font-family:Consolas,monospace;white-space:pre-wrap}"
        u"blockquote{border-left:3px solid #dadfe6;margin-left:0;padding-left:10px}"
        u"img{max-width:100%;height:auto;border:1px solid #dadfe6;margin:6px 0}")

_IMG_TYPES = {u".png": u"image/png", u".svg": u"image/svg+xml",
              u".jpg": u"image/jpeg", u".gif": u"image/gif"}


def docs_dir():
    """docs/ next to the folder Scripts/."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), u"docs")


def doc_path(lang):
    """The manual in "nl" or "en"."""
    if lang == "en":
        return os.path.join(docs_dir(), u"en", DOC_NAME)
    return os.path.join(docs_dir(), DOC_NAME)


def _esc(text):
    return (text.replace(u"&", u"&amp;").replace(u"<", u"&lt;")
            .replace(u">", u"&gt;"))


def _attr(text):
    return _esc(text).replace(u'"', u"&quot;")


_CODE_RE = re.compile(u"`([^`]+)`")
_IMG_RE = re.compile(u"!\\[([^\\]]*)\\]\\(([^)\\s]+)\\)")
_LINK_RE = re.compile(u"\\[([^\\]]+)\\]\\(([^)\\s]+)\\)")
_BOLD_RE = re.compile(u"\\*\\*(.+?)\\*\\*")
_ITAL_RE = re.compile(u"(?<![\\w*])\\*([^*\\s][^*]*?)\\*(?![\\w*])", re.UNICODE)
# "§3.2" or "§ 7": the number without a dot at the end of a sentence
_PAR_RE = re.compile(u"§\\s?(\\d+(?:\\.\\d+)*)")
_HEAD_RE = re.compile(u"^(#{1,4})\\s+(.*)$")
_NUMBER_RE = re.compile(u"^(\\d+(?:\\.\\d+)*)\\.?\\s")
_LIST_RE = re.compile(u"^(\\s*)([-*]|\\d+\\.)\\s+(.*)$")
_FENCE = u"```"


def slug(heading):
    """The anchor GitHub gives a heading: Markdown left out, lower case, without
    punctuation, spaces -> '-' ("3.1 Eén doorsnede" -> "31-eén-doorsnede")."""
    text = _LINK_RE.sub(lambda m: m.group(1), heading)
    text = text.replace(u"`", u"").replace(u"*", u"").strip().lower()
    text = re.sub(u"[^\\w\\- ]", u"", text, flags=re.UNICODE)
    return text.replace(u" ", u"-")


def headings(text):
    """[(level, text)] of the headings outside ``` blocks."""
    out = []
    fenced = False
    for line in _lines(text):
        if line.strip().startswith(_FENCE):
            fenced = not fenced
            continue
        m = None if fenced else _HEAD_RE.match(line.strip())
        if m:
            out.append((len(m.group(1)), m.group(2).strip()))
    return out


def _lines(text):
    text = re.sub(u"<!--.*?-->", u"", text, flags=re.DOTALL)
    return text.replace(u"\r\n", u"\n").split(u"\n")


class _Anchors(object):
    """Ids of the headings, by GitHub slug and by section number."""

    def __init__(self, text):
        self.ids = []
        self.by_slug = {}
        self.by_number = {}
        for k, (level, title) in enumerate(headings(text)):
            hid = u"h-{0}".format(k + 1)
            self.ids.append(hid)
            base = slug(title)
            name, n = base, 0
            while name in self.by_slug:          # GitHub: a repeated slug gets -1, -2
                n += 1
                name = u"{0}-{1}".format(base, n)
            self.by_slug[name] = hid
            m = _NUMBER_RE.match(title)
            if m and m.group(1) not in self.by_number:
                self.by_number[m.group(1)] = hid


def _other_language(href):
    """"en" or "nl" when href is the manual in the other language."""
    name = href.split(u"#")[0].replace(u"\\", u"/")
    if name == u"en/" + DOC_NAME:
        return u"en"
    if name == u"../" + DOC_NAME:
        return u"nl"
    return None


def _href(href, anchors):
    if href.startswith(u"#"):
        return u"#" + anchors.by_slug.get(href[1:], href[1:])
    lang = _other_language(href)
    if lang:
        return u"#" + LANG_PREFIX + lang
    return href


def _image_html(base_dir):
    """image(alt, src): the file relative to base_dir as a data URI; a missing
    file or an unknown type gives the alt text in brackets."""
    def image(alt, src):
        path = os.path.normpath(os.path.join(base_dir, src.replace(u"/", os.sep)))
        kind = _IMG_TYPES.get(os.path.splitext(path)[1].lower())
        data = None
        if kind is not None:
            try:
                f = io.open(path, "rb")
                try:
                    data = f.read()
                finally:
                    f.close()
            except (IOError, OSError):
                data = None
        if data is None:
            return u'<span class="noimg">[{0}]</span>'.format(_esc(alt))
        return u'<img alt="{0}" src="data:{1};base64,{2}">'.format(
            _attr(alt), kind, base64.b64encode(data).decode("ascii"))
    return image


def _inline(text, anchors, image):
    """Inline markup. Code and images first as placeholders, so that * in code is
    not italic and a § in code stays text."""
    keep = []

    def hold(html):
        keep.append(html)
        return u"\u0001{0}\u0001".format(len(keep) - 1)

    text = _IMG_RE.sub(lambda m: hold(image(m.group(1), m.group(2))), text)
    text = _CODE_RE.sub(lambda m: hold(u"<code>{0}</code>".format(_esc(m.group(1)))), text)
    text = _LINK_RE.sub(lambda m: hold(u'<a href="{0}">{1}</a>'.format(
        _attr(_href(m.group(2), anchors)), _inline(m.group(1), anchors, image))), text)
    text = _esc(text)

    def par(m):
        hid = anchors.by_number.get(m.group(1))
        if hid is None:
            return m.group(0)
        return hold(u'<a href="#{0}">{1}</a>'.format(hid, m.group(0)))
    text = _PAR_RE.sub(par, text)
    text = _BOLD_RE.sub(u"<b>\\1</b>", text)
    text = _ITAL_RE.sub(u"<i>\\1</i>", text)
    for _ in range(len(keep) + 1):          # held parts may hold others
        text, n = re.subn(u"\u0001(\\d+)\u0001", lambda m: keep[int(m.group(1))], text)
        if not n:
            break
    return text


def _cells(line):
    line = line.strip()
    if line.startswith(u"|"):
        line = line[1:]
    if line.endswith(u"|"):
        line = line[:-1]
    return [c.strip() for c in line.split(u"|")]


def markdown_to_html(text, base_dir, title=u""):
    """The HTML page of a manual. base_dir: folder of the document, for images."""
    if text.startswith(u"\ufeff"):
        text = text[1:]
    anchors = _Anchors(text)
    image = _image_html(base_dir)

    def inl(t):
        return _inline(t, anchors, image)

    lines = _lines(text)
    out = []
    para = []
    item = []           # text of the current list item (several lines)
    lists = []          # stack of (indentation, "ul" or "ol")
    heads = iter(anchors.ids)

    def flush_item():
        if item:
            out.append(inl(u" ".join(item)))
            del item[:]

    def flush_para():
        flush_item()
        if para:
            out.append(u"<p>{0}</p>".format(inl(u" ".join(para))))
            del para[:]

    def close_lists(indent=-1):
        flush_item()
        while lists and lists[-1][0] > indent:
            out.append(u"</li></{0}>".format(lists.pop()[1]))

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith(_FENCE):
            flush_para()
            # an indented block belongs to the list item it stands in
            if not (lists and line.startswith(u" ")):
                close_lists()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(_FENCE):
                code.append(lines[i])
                i += 1
            indent = len(line) - len(line.lstrip())
            code = [c[indent:] if c[:indent].strip() == u"" else c for c in code]
            out.append(u"<pre>{0}</pre>".format(_esc(u"\n".join(code))))
            i += 1
            continue
        if not stripped:
            flush_para()
            i += 1
            continue
        m = _HEAD_RE.match(stripped)
        if m:
            flush_para()
            close_lists()
            n = len(m.group(1))
            out.append(u'<h{0} id="{1}">{2}</h{0}>'.format(n, next(heads), inl(m.group(2))))
            i += 1
            continue
        if re.match(u"^-{3,}$", stripped):
            flush_para()
            close_lists()
            out.append(u"<hr>")
            i += 1
            continue
        if stripped.startswith(u"|") and i + 1 < len(lines) and \
                re.match(u"^\\|?[\\s:|-]+\\|?$", lines[i + 1].strip()) and u"-" in lines[i + 1]:
            flush_para()
            close_lists()
            out.append(u"<table><tr>{0}</tr>".format(u"".join(
                u"<th>{0}</th>".format(inl(c)) for c in _cells(line))))
            i += 2
            while i < len(lines) and lines[i].strip().startswith(u"|"):
                out.append(u"<tr>{0}</tr>".format(u"".join(
                    u"<td>{0}</td>".format(inl(c)) for c in _cells(lines[i]))))
                i += 1
            out.append(u"</table>")
            continue
        m = _LIST_RE.match(line)
        if m:
            flush_para()
            indent = len(m.group(1).replace(u"\t", u"    "))
            kind = u"ol" if m.group(2)[0].isdigit() else u"ul"
            if lists and indent < lists[-1][0]:
                close_lists(indent)
            if not lists or indent > lists[-1][0]:
                start = u""
                if kind == u"ol" and m.group(2) != u"1.":
                    start = u' start="{0}"'.format(m.group(2)[:-1])
                out.append(u"<{0}{1}><li>".format(kind, start))
                lists.append((indent, kind))
            else:
                flush_item()
                out.append(u"</li><li>")
            item.append(m.group(3))
            i += 1
            continue
        if lists and line.startswith(u" "):
            if item:
                item.append(stripped)       # continuation of a list item
            else:
                para.append(stripped)       # a paragraph inside the item
            i += 1
            continue
        if stripped.startswith(u">"):
            flush_para()
            close_lists()
            out.append(u"<blockquote>{0}</blockquote>".format(inl(stripped.lstrip(u"> "))))
            i += 1
            continue
        close_lists()
        para.append(stripped)
        i += 1
    flush_para()
    close_lists()
    return _page(title, u"\n".join(out))


def _page(title, body):
    # charset first: the page reaches the browser element as bytes
    return (u'<!DOCTYPE html><html><head><meta charset="utf-8">'
            u'<meta http-equiv="X-UA-Compatible" content="IE=edge">'
            u"<title>{0}</title><style>{1}</style></head>"
            u'<body><div id="{2}">{3}</div></body></html>').format(
                _esc(title), _CSS, MARKER_ID, body)


def read_text(path):
    f = io.open(path, encoding="utf-8-sig")       # without a byte order mark
    try:
        return f.read()
    finally:
        f.close()


def doc_html(lang):
    """(html, markdown text) of the manual in lang; a missing file gives a page
    that says so, and the text u""."""
    path = doc_path(lang)
    try:
        text = read_text(path)
    except (IOError, OSError) as e:
        body = (u"Handleiding niet gevonden: {0} ({1})" if lang == "nl" else
                u"Manual not found: {0} ({1})").format(path, e)
        return _page(DOC_NAME, u"<p>{0}</p>".format(_esc(body))), u""
    return markdown_to_html(text, os.path.dirname(path), DOC_NAME), text


def resolve_link(url):
    """What a click in the page asks, from the address the browser element
    reports: ("anchor", id), ("lang", "nl" or "en"), ("web", url) or
    (None, None). The page itself is about:blank."""
    url = u"{0}".format(url or u"")
    if url.startswith(u"http://") or url.startswith(u"https://"):
        return "web", url
    if u"#" in url:
        fragment = url.split(u"#", 1)[1]
        if fragment.startswith(LANG_PREFIX) and fragment[len(LANG_PREFIX):] in ("nl", "en"):
            return "lang", fragment[len(LANG_PREFIX):]
        if fragment:
            return "anchor", fragment
    return None, None
