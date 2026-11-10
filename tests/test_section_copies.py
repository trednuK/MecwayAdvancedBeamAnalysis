# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test that Scripts/section_fem.py and Scripts/section_mesh.py are unchanged copies
# of MecwaySectionProps (AGENTS.md). The source is the committed branch main of that
# repository next to this one (..\MecwaySectionProps), read with git show, never its
# working tree; line endings do not count. Without that repository or without git
# the test says so in one line and passes. Standard library only.
#
#   python tests/test_section_copies.py
#   MECWAY_SECTIONPROPS_REPO=<folder> python tests/test_section_copies.py

from __future__ import print_function

import io
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(globals().get("__file__", sys.argv[0])))
SCRIPTS = os.path.join(_HERE, "..", "Scripts")
COPIES = ("section_fem.py", "section_mesh.py")
BRANCH = "main"
FAILED = []


def check(name, ok, detail=""):
    print((u"OK   " if ok else u"FAIL ") + name +
          (u" ({0})".format(detail) if detail else u""))
    if not ok:
        FAILED.append(name)


def source_repo():
    env = os.environ.get("MECWAY_SECTIONPROPS_REPO")
    path = env or os.path.join(_HERE, "..", "..", "MecwaySectionProps")
    return os.path.normcase(os.path.realpath(path))


# Variables of a running git (a hook) that would point git -C elsewhere
_GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_COMMON_DIR", "GIT_NAMESPACE")


def git(repo, *args):
    """(exit code, stdout bytes, stderr text); raises OSError without git."""
    env = dict((k, v) for k, v in os.environ.items() if k not in _GIT_VARS)
    proc = subprocess.Popen(("git", "-C", repo) + args, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    out, err = proc.communicate()
    return proc.returncode, out, err.decode("utf-8", "replace").strip()


def why_not_repository(repo):
    """None when repo is the top of a git repository, else the reason; git -C
    also finds a repository above the folder, which is not the one meant."""
    if not os.path.isdir(repo):
        return "no folder"
    try:
        code, out, err = git(repo, "rev-parse", "--show-toplevel")
    except OSError:
        return "git not found"
    if code != 0:
        return err.splitlines()[0] if err else "not a git repository"
    top = out.decode("utf-8", "replace").strip()
    if os.path.normcase(os.path.realpath(top)) != repo:
        return "not the top of a git repository"
    return None


def same(ours, theirs):
    return ours.replace(b"\r\n", b"\n") == theirs.replace(b"\r\n", b"\n")


def main():
    # the comparison itself: line endings do not count, a changed line does
    check("comparison ignores line endings", same(b"a\r\nb\r\n", b"a\nb\n"))
    check("comparison sees a changed line", not same(b"a\nb\n", b"a\nc\n"))
    repo = source_repo()
    reason = why_not_repository(repo)
    if reason is not None:
        print(u"MecwaySectionProps not usable next to this repository ({0}): copies not "
              u"compared".format(reason))
        return 1 if FAILED else 0
    for name in COPIES:
        with io.open(os.path.join(SCRIPTS, name), "rb") as f:
            ours = f.read()
        code, theirs, err = git(repo, "show", "{0}:Scripts/{1}".format(BRANCH, name))
        if code != 0:
            check(name + ": source read from MecwaySectionProps " + BRANCH, False,
                  u"git show {0}:Scripts/{1}: {2}".format(BRANCH, name, err))
            continue
        check(name + " is an unchanged copy", same(ours, theirs),
              u"" if same(ours, theirs) else
              u"Scripts/{0}: copy is behind its source in MecwaySectionProps ({1}); "
              u"copy it over".format(name, BRANCH))
    print("{0} failed".format(len(FAILED)) if FAILED else "All checks OK")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
