# Modo Navigation for FreeCAD

Modo / Blender-style ("emulate 3-button mouse") navigation for FreeCAD's 3D view:

| Action | Mouse |
|---|---|
| Rotate | **Alt + LMB** drag |
| Pan    | **Alt + Shift + LMB** drag |
| Zoom   | **Alt + Ctrl + LMB** drag, or scroll wheel |
| Select | LMB (unchanged) |

Works with a two-button mouse, a laptop touchpad with clicks, or a pen tablet.

## How it works

FreeCAD's built-in **MayaGesture** navigation style already uses Alt+LMB rotate,
Alt+MMB pan, Alt+RMB zoom and wheel zoom. This add-on switches to that style and
installs a small Qt event filter that rewrites, inside 3D views only:

- Alt + Shift + LMB → Alt + MMB (pan)
- Alt + Ctrl + LMB → Alt + RMB (zoom)

Once a drag starts, it keeps going until you release the left button, even if
you let go of the modifier keys first. Everything else (selection, NaviCube,
wheel, context menu) is MayaGesture's normal behaviour.

FreeCAD's navigation styles are C++ classes, and Python can't add a new entry to
the *Preferences → Navigation* dropdown. That dropdown will show **MayaGesture**
while this add-on is active.

## Toggling

- **Tools → Modo navigation** (checkable, in every workbench), or
- the navigation-style menu on the status bar (bottom right) → **Modo**.

Turning it off restores the navigation style you had before. Picking any other
style from the status bar menu also turns it off.

## Linux: free up Alt + drag first

Most Linux desktops use Alt + LMB drag to move windows, so FreeCAD never sees it.

- **KDE Plasma:** System Settings → Window Management → Window Behavior →
  Window Actions → *Modifier key*: change **Alt** to **Meta**.
- **GNOME:** `gsettings set org.gnome.desktop.wm.preferences mouse-button-modifier '<Super>'`
- **XFCE:** Window Manager Tweaks → Accessibility → *Key used to grab and move windows*.

## Install

### Addon Manager (custom repository)

1. *Edit → Preferences → Addon Manager → Custom repositories* → add
   `https://github.com/CHANGE_ME/freecad-modo-navigation`, branch `main`.
2. Open the Addon Manager, find **Modo Navigation**, install, restart FreeCAD.

### Manual / development

Link (or copy) this folder into FreeCAD's user `Mod` directory and restart:

```bash
# snap
ln -s "$PWD" ~/snap/freecad/common/Mod/ModoNavigation
# regular install (FreeCAD 1.x)
ln -s "$PWD" ~/.local/share/FreeCAD/Mod/ModoNavigation
```

## Tests

`tests/run.sh` starts a headless FreeCAD (`QT_QPA_PLATFORM=offscreen`) with a
throwaway copy of your user config. It sends real mouse events to a 3D view and
checks that each gesture rotates, pans or zooms the camera, that the remapped
gestures match native Alt+MMB / Alt+RMB exactly, and that disabling restores
the previous style. The add-on must be linked into `Mod` first. For a
non-snap install, set `FREECAD=/path/to/freecad` and `FREECAD_USER_DIR=...`.

## License

LGPL-2.1-or-later
