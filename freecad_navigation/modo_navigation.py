# SPDX-License-Identifier: LGPL-2.1-or-later
"""Modo-style 3D view navigation for FreeCAD.

FreeCAD's MayaGesture navigation style already does most of what Modo does:

    Alt + LMB   rotate
    Alt + MMB   pan
    Alt + RMB   zoom
    wheel       zoom

This module adds the missing "3-button mouse emulation" part, the same way
Blender does it: an application-wide Qt event filter rewrites

    Alt + Shift + LMB  ->  Alt + MMB   (pan)
    Alt + Ctrl  + LMB  ->  Alt + RMB   (zoom)

before the 3D viewer sees the events. Nothing else is touched, so selection,
the NaviCube, the wheel and every other mouse binding keep working.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:  # PySide2 shim in older FreeCAD builds exposes widgets via QtGui
    QtWidgets = QtGui

translate = FreeCAD.Qt.translate

MAYA_STYLE = "Gui::MayaGestureNavigationStyle"
VIEWER_CLASS = "Gui::View3DInventorViewer"
PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/ModoNavigation"
VIEW_PARAM_PATH = "User parameter:BaseApp/Preferences/View"
CMD_NAME = "ModoNavigation_Toggle"

Qt = QtCore.Qt
QEvent = QtCore.QEvent
MOUSE_EVENTS = (
    QEvent.MouseButtonPress,
    QEvent.MouseButtonRelease,
    QEvent.MouseButtonDblClick,
    QEvent.MouseMove,
)


def _params():
    return FreeCAD.ParamGet(PARAM_PATH)


def _view_params():
    return FreeCAD.ParamGet(VIEW_PARAM_PATH)


def _flag(value):
    """Qt enum/flags -> int, for PySide2/PySide6 compatible bit twiddling."""
    try:
        return int(value)
    except TypeError:
        return value.value


def _has(mods, modifier):
    return bool(_flag(mods) & _flag(modifier))


def is_enabled():
    return _params().GetBool("Enabled", True)


def is_maya_style_active():
    return _view_params().GetString("NavigationStyle") == MAYA_STYLE


def _is_3d_viewport(obj):
    """True if obj is the GL viewport widget of a FreeCAD 3D view."""
    if not isinstance(obj, QtWidgets.QWidget):
        return False
    parent = obj.parentWidget()
    return parent is not None and parent.metaObject().className() == VIEWER_CLASS


def _make_mouse_event(event, etype, button, buttons, modifiers):
    if hasattr(event, "position"):  # Qt 6
        return QtGui.QMouseEvent(
            etype,
            event.position(),
            event.scenePosition(),
            event.globalPosition(),
            button,
            buttons,
            modifiers,
            event.pointingDevice(),
        )
    return QtGui.QMouseEvent(  # Qt 5
        etype, event.localPos(), event.windowPos(), event.screenPos(), button, buttons, modifiers
    )


def _buttons(bits):
    flags_type = getattr(Qt, "MouseButtons", Qt.MouseButton)  # PySide2 / PySide6
    return flags_type(bits)


class ModoEventFilter(QtCore.QObject):
    """Rewrites Alt+Shift+LMB / Alt+Ctrl+LMB drags into Alt+MMB / Alt+RMB drags."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # (viewport widget, emulated button) while a remapped drag is in progress
        self._drag = None
        # True while we deliver our own synthetic event, which passes through
        # this (application-wide) filter again and must be left alone.
        self._sending = False

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype not in MOUSE_EVENTS or self._sending:
            return False
        try:
            if self._drag is not None:
                return self._continue_drag(obj, event, etype)
            if etype in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                return self._maybe_start_drag(obj, event, etype)
        except Exception as exc:  # never let a Python error eat the user's mouse
            self._drag = None
            FreeCAD.Console.PrintError("ModoNavigation: {}\n".format(exc))
        return False

    def _maybe_start_drag(self, obj, event, etype):
        if event.button() != Qt.LeftButton:
            return False
        mods = event.modifiers()
        if not _has(mods, Qt.AltModifier):
            return False
        shift = _has(mods, Qt.ShiftModifier)
        ctrl = _has(mods, Qt.ControlModifier)
        if shift == ctrl:  # plain Alt+LMB is already rotate; Alt+Shift+Ctrl is left alone
            return False
        if not (is_enabled() and is_maya_style_active() and _is_3d_viewport(obj)):
            return False

        target = Qt.MiddleButton if shift else Qt.RightButton
        self._drag = (obj, target)
        # Double clicks are sent as a plain press so the style never sees a dblclick.
        self._send(obj, event, QEvent.MouseButtonPress, target, target)
        return True

    def _continue_drag(self, obj, event, etype):
        viewport, target = self._drag
        if obj is not viewport:
            # Events for other widgets (or parent propagation) pass through untouched.
            return False

        others = _flag(event.buttons()) & ~_flag(Qt.LeftButton)
        if etype == QEvent.MouseMove:
            self._send(obj, event, etype, Qt.NoButton, others | _flag(target))
            return True
        if etype == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self._drag = None
            self._send(obj, event, etype, target, others)
            return True
        # Other buttons pressed/released mid-drag go through as-is.
        return False

    def _send(self, obj, event, etype, button, buttons):
        # Keep Alt held for the whole drag, even if the user lets go of it early:
        # the drag ends when the left button is released, like in Modo.
        new = _make_mouse_event(
            event, etype, Qt.MouseButton(_flag(button)), _buttons(buttons), Qt.AltModifier
        )
        self._sending = True
        try:
            QtCore.QCoreApplication.sendEvent(obj, new)
        finally:
            self._sending = False


_filter = None


def install_filter():
    global _filter
    if _filter is None:
        _filter = ModoEventFilter(QtWidgets.QApplication.instance())
        QtWidgets.QApplication.instance().installEventFilter(_filter)


def _all_3d_views():
    for name in FreeCAD.listDocuments():
        gdoc = FreeCADGui.getDocument(name)
        if gdoc is None:
            continue
        for view in gdoc.mdiViewsOfType("Gui::View3DInventor"):
            yield view


def _apply_style(style):
    _view_params().SetString("NavigationStyle", style)
    # With "same style for all views" off, existing views keep their own style.
    for view in _all_3d_views():
        if hasattr(view, "setNavigationType"):
            view.setNavigationType(style)


def enable():
    params = _params()
    current = _view_params().GetString("NavigationStyle")
    if current and current != MAYA_STYLE:
        params.SetString("PreviousStyle", current)
    params.SetBool("Enabled", True)
    _apply_style(MAYA_STYLE)
    _sync_ui()


def disable():
    params = _params()
    params.SetBool("Enabled", False)
    previous = params.GetString("PreviousStyle")
    if previous and is_maya_style_active():
        _apply_style(previous)
    _sync_ui()


def is_active():
    return is_enabled() and is_maya_style_active()


def toggle():
    disable() if is_active() else enable()


class ToggleCommand:
    def GetResources(self):
        return {
            "Pixmap": _icon_path(),
            "MenuText": translate("ModoNavigation", "Modo navigation"),
            "ToolTip": translate(
                "ModoNavigation",
                "Alt+LMB rotate, Alt+Shift+LMB pan, Alt+Ctrl+LMB zoom, wheel zoom.\n"
                "Uses the MayaGesture navigation style underneath.",
            ),
            "Checkable": is_active(),
        }

    def Activated(self, checked=None):
        if checked is None:
            toggle()
        elif checked:
            enable()
        else:
            disable()

    def IsActive(self):
        return True


class MenuManipulator:
    """Puts the toggle command into the Tools menu of every workbench."""

    def modifyMenuBar(self):
        return [{"insert": CMD_NAME, "menuItem": "Std_DlgParameter", "after": ""}]


def _icon_path():
    import os

    return os.path.join(os.path.dirname(__file__), "Resources", "icons", "ModoNavigation.svg")


# --- status bar navigation indicator (Tux) -----------------------------------

_indicator_action = None


def _attach_to_indicator():
    """Add a 'Modo' entry to the status bar navigation style menu, if it exists."""
    global _indicator_action
    import sys

    nav = sys.modules.get("NavigationIndicatorGui")
    if nav is None or _indicator_action is not None:
        return
    action = QtGui.QAction(QtGui.QIcon(_icon_path()), "Modo  ", nav.menu)
    action.setObjectName("Indicator_NavigationModo")
    action.setCheckable(True)
    action.setToolTip(
        translate("ModoNavigation", "MayaGesture + Alt+Shift/Ctrl+LMB pan/zoom emulation")
    )
    action.triggered.connect(lambda checked: enable() if checked else disable())
    nav.menu.insertAction(nav.a5, action)  # right below MayaGesture
    # Picking any built-in style turns the emulation off, so choosing
    # "MayaGesture" afterwards gives plain MayaGesture.
    nav.gStyle.triggered.connect(_on_builtin_style_picked)
    nav.menu.aboutToShow.connect(_sync_ui)
    _indicator_action = action
    _sync_ui()


def _on_builtin_style_picked(_action):
    _params().SetBool("Enabled", False)
    _sync_ui()


def _sync_ui():
    active = is_active()
    if _indicator_action is not None:
        _indicator_action.setChecked(active)
    mw = FreeCADGui.getMainWindow()
    if mw is not None:
        for action in mw.findChildren(QtGui.QAction, CMD_NAME):
            action.setChecked(active)


def setup():
    install_filter()
    FreeCADGui.addCommand(CMD_NAME, ToggleCommand())
    if hasattr(FreeCADGui, "addWorkbenchManipulator"):
        FreeCADGui.addWorkbenchManipulator(MenuManipulator())
    if is_enabled() and not is_maya_style_active():
        enable()
    # The Tux status bar indicator may load after us; attach once the GUI is up.
    QtCore.QTimer.singleShot(0, _attach_to_indicator)
