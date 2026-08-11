---
name: Qt desktop apps (PySide6 / PyQt6)
category: Coding
description: Building Qt GUIs in Python — threading rules, signals, and the mistakes that cause silent bugs.
when: working on a PySide6 or PyQt6 desktop application.
default: false
---

## Pick the binding and stay in it

**PySide6** and **PyQt6** wrap the same Qt but differ in spelling. Check what
the project already uses and match it — mixing them in one process breaks.

| | PySide6 | PyQt6 |
|---|---|---|
| Custom signal | `Signal(str)` | `pyqtSignal(str)` |
| Slot decorator | `@Slot()` | `@pyqtSlot()` |
| Import root | `PySide6.QtWidgets` | `PyQt6.QtWidgets` |

Everything else — widget names, layouts, enums — is the same. In PyQt6 and
PySide6 alike, enums are fully scoped: `Qt.AlignmentFlag.AlignLeft`, not
`Qt.AlignLeft`.

## The threading rule, which is the whole ballgame

**Only the thread that created the QApplication may touch widgets.** Calling
`label.setText()` from a worker thread does not raise — it corrupts state or
crashes later, somewhere unrelated. Every Qt bug that looks like heisenbehaviour
is this.

Do long work in a `QThread` (or a worker moved with `moveToThread`) and send
results back through signals. Qt queues a signal that crosses a thread boundary
and delivers it on the receiving thread's event loop, which is what makes it
safe.

```python
class Worker(QThread):
    progress = Signal(int)          # PyQt6: pyqtSignal(int)
    done = Signal(object)

    def run(self):                  # runs off the GUI thread
        result = expensive()
        self.done.emit(result)      # safe: delivered on the GUI thread
```

**Connect to a bound method, not a bare function or lambda that touches
widgets.** Connecting `worker.finished` to something that is not a bound method
of a QObject living on the GUI thread can run the handler on the worker's
thread — and then you are mutating widgets off-thread again, with no error to
tell you. If a handler must update the UI, make it a method on the window and
connect `worker.done.connect(self.on_done)`.

Keep a reference to the thread (`self._worker = worker`). A `QThread` that goes
out of scope is garbage collected mid-run and the app dies with
`QThread: Destroyed while thread is still running`.

## Blocking a worker on the GUI thread

When a worker needs an answer only the user can give — a confirmation dialog —
it cannot show one itself. Emit a signal to the GUI thread, show the dialog
there, and hand the answer back through a `threading.Event` the worker waits
on. That round trip is the only safe shape.

## Layout and widgets

Use layouts, never absolute positions — fixed geometry breaks on every other
DPI and font size. `QVBoxLayout` / `QHBoxLayout` nest; `QGridLayout` for forms;
`QSplitter` when the user should control proportions.

Style with a stylesheet and object names (`widget.setObjectName("sidebar")`,
then `QFrame#sidebar { ... }`) rather than per-widget colour calls. One place
to change the theme.

For a long scrolling list of widgets, put them in a `QVBoxLayout` inside a
`QScrollArea` with `setWidgetResizable(True)`, and add a trailing `addStretch(1)`
so items pack to the top.

## Pitfalls that cost hours

- A dialog created without a parent can appear behind the main window or leak.
  Pass `self`.
- `QTimer` and `QThread` need a parent or a kept reference, or they vanish.
- Widgets rebuilt on every update flicker and leak; update the existing widget.
- Signals fired during construction reach handlers before state exists — connect
  after the widget tree is built, or guard with `blockSignals(True)`.
- `setChecked()` emits `toggled`. When syncing a checkbox to state
  programmatically, wrap it in `blockSignals` or you re-enter your own handler.
