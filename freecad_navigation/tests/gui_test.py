# SPDX-License-Identifier: LGPL-2.1-or-later
"""End-to-end test, run inside FreeCAD's GUI (see tests/run.sh).

Drives a real 3D view with synthetic mouse events and checks how the camera
moves. Results go to the file named by $MODO_TEST_OUT; exit code 0 = pass.
"""

import faulthandler
import os
import traceback

import FreeCAD
import FreeCADGui
from pivy import coin  # noqa: F401  (needed for getCameraNode)
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

import modo_navigation as mn

Qt = QtCore.Qt
out = open(os.environ["MODO_TEST_OUT"], "w")
faulthandler.enable(file=out)
faulthandler.dump_traceback_later(60, exit=True, file=out)  # hang guard
failures = []


def log(*args):
    print(*args, file=out, flush=True)


def check(name, cond, detail=""):
    log("{} {} {}".format("PASS" if cond else "FAIL", name, detail))
    if not cond:
        failures.append(name)


def pump(n=5):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def camera_state(view):
    cam = view.getCameraNode()
    pos = cam.position.getValue().getValue()
    rot = cam.orientation.getValue().getValue()
    size = cam.height.getValue() if hasattr(cam, "height") else cam.heightAngle.getValue()
    return pos, rot, size


def diff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def mouse(viewport, etype, pos, button, buttons, mods):
    p = QtCore.QPointF(pos)
    g = QtCore.QPointF(viewport.mapToGlobal(pos))
    ev = QtGui.QMouseEvent(etype, p, p, g, button, buttons, mods)
    QtCore.QCoreApplication.sendEvent(viewport, ev)
    pump(1)


def drag(viewport, mods, button=Qt.LeftButton, delta=(60, 40)):
    c = viewport.rect().center()
    mouse(viewport, QtCore.QEvent.MouseMove, c, Qt.NoButton, Qt.NoButton, mods)
    mouse(viewport, QtCore.QEvent.MouseButtonPress, c, button, button, mods)
    steps = 10
    for i in range(1, steps + 1):
        pt = c + QtCore.QPoint(delta[0] * i // steps, delta[1] * i // steps)
        mouse(viewport, QtCore.QEvent.MouseMove, pt, Qt.NoButton, button, mods)
    mouse(viewport, QtCore.QEvent.MouseButtonRelease, pt, button, Qt.NoButton, mods)
    pump()


def measure(view, viewport, mods, button=Qt.LeftButton):
    view.viewIsometric()
    view.fitAll()
    pump(10)
    before = camera_state(view)
    drag(viewport, mods, button)
    log("   spinning after drag:", view.getViewer().isSpinning())
    after = camera_state(view)
    return diff(before[0], after[0]), diff(before[1], after[1]), abs(before[2] - after[2])


def run():
    view_params = FreeCAD.ParamGet(mn.VIEW_PARAM_PATH)
    view_params.SetBool("UseNavigationAnimations", False)  # throwaway test config

    check("filter installed", mn._filter is not None)
    check("style switched to MayaGesture", mn.is_maya_style_active(),
          view_params.GetString("NavigationStyle"))
    check("command registered", mn.CMD_NAME in FreeCADGui.listCommands())

    doc = FreeCAD.newDocument("ModoTest")
    doc.addObject("Part::Box", "Box")
    doc.recompute()
    pump(20)
    view = FreeCADGui.ActiveDocument.ActiveView
    view.setCameraType("Orthographic")
    viewer = None
    for w in FreeCADGui.getMainWindow().findChildren(QtWidgets.QWidget):
        if mn._is_3d_viewport(w) and w.isVisible():
            viewer = w
    check("found 3D viewport", viewer is not None)
    log("   viewport size:", viewer.size())
    check("view uses MayaGesture", view.getNavigationType() == mn.MAYA_STYLE,
          view.getNavigationType())

    tools = [a.objectName() for a in FreeCADGui.getMainWindow().findChildren(QtGui.QAction)]
    check("toggle action in GUI", mn.CMD_NAME in tools)
    acts = FreeCADGui.getMainWindow().findChildren(QtGui.QAction, mn.CMD_NAME)
    check("toggle action is checked", bool(acts) and all(a.isCheckable() and a.isChecked() for a in acts))
    check("status bar indicator entry", mn._indicator_action is not None)

    alt = Qt.AltModifier
    shift_alt = Qt.KeyboardModifier(mn._flag(Qt.AltModifier) | mn._flag(Qt.ShiftModifier))
    ctrl_alt = Qt.KeyboardModifier(mn._flag(Qt.AltModifier) | mn._flag(Qt.ControlModifier))

    dpos, drot, dsize = measure(view, viewer, alt)
    check("Alt+LMB rotates", drot > 1e-3, (dpos, drot, dsize))

    dpos, drot, dsize = measure(view, viewer, shift_alt)
    check("Alt+Shift+LMB pans", dpos > 1e-3 and drot < 1e-6 and dsize < 1e-6, (dpos, drot, dsize))

    dpos, drot, dsize = measure(view, viewer, ctrl_alt)
    check("Alt+Ctrl+LMB zooms", dsize > 1e-3 and drot < 1e-6, (dpos, drot, dsize))

    # Reference: what native Alt+MMB / Alt+RMB do, to be sure the remap matches.
    ref = measure(view, viewer, alt, Qt.MiddleButton)
    check("native Alt+MMB pans (reference)", ref[0] > 1e-3 and ref[1] < 1e-6, ref)
    ref = measure(view, viewer, alt, Qt.RightButton)
    check("native Alt+RMB zooms (reference)", ref[2] > 1e-3 and ref[1] < 1e-6, ref)

    check("no drag left dangling", mn._filter._drag is None)

    # Disabling restores the previous style and stops remapping.
    previous = FreeCAD.ParamGet(mn.PARAM_PATH).GetString("PreviousStyle")
    mn.disable()
    pump()
    check("disable restores previous style",
          view_params.GetString("NavigationStyle") == previous, previous)
    c = viewer.rect().center()
    mouse(viewer, QtCore.QEvent.MouseButtonPress, c, Qt.LeftButton, Qt.LeftButton, shift_alt)
    check("disabled: Alt+Shift+LMB not remapped", mn._filter._drag is None)
    mouse(viewer, QtCore.QEvent.MouseButtonRelease, c, Qt.LeftButton, Qt.NoButton, shift_alt)
    mn.enable()
    pump()
    check("re-enable switches back", mn.is_active() and view.getNavigationType() == mn.MAYA_STYLE)

    FreeCAD.closeDocument("ModoTest")


try:
    run()
except Exception:
    log(traceback.format_exc())
    failures.append("exception")
log("RESULT", "FAIL " + ", ".join(failures) if failures else "OK")
out.close()
QtCore.QTimer.singleShot(0, FreeCADGui.getMainWindow().close)
