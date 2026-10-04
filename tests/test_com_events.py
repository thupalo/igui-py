"""Event sink wiring against SAP's registered type library (skipped without SAP GUI)."""

import pytest

pythoncom = pytest.importorskip("pythoncom")

from igui.sap.com_events import make_sink, source_interface  # noqa: E402
from igui.sap.recorder import RecordingConverter, SessionEvents  # noqa: E402

try:
    IID, EVENTS = source_interface("GuiSession")
except Exception:  # SAP GUI not installed
    IID, EVENTS = None, {}

pytestmark = pytest.mark.skipif(IID is None, reason="SAP GUI type library not registered")


class FakeRecorder:
    def __init__(self):
        self.converter = RecordingConverter()
        self.lines = []
        self.destroyed = False

    def event(self, text, notes=None):
        self.lines.append((text, notes))

    def on_destroyed(self):
        self.destroyed = True


def test_all_handlers_exist_in_typelib():
    names = set(EVENTS.values())
    handled = {m[2:] for m in dir(SessionEvents) if m.startswith("On")}
    assert handled <= names, handled - names


def test_invoke_through_idispatch():
    by_name = {n: d for d, n in EVENTS.items()}
    handler = SessionEvents()
    handler.recorder = FakeRecorder()
    sink = make_sink(handler, IID, EVENTS)
    # SAP's Advise queries the sink for the events IID (E_NOINTERFACE before the fix)
    disp = sink.QueryInterface(IID, pythoncom.IID_IDispatch)

    # Change(Session, Component, CommandArray) - component without Id -> "?"
    disp.Invoke(by_name["Change"], 0, pythoncom.DISPATCH_METHOD, False,
                None, None, (("SP", "text", "MARA"),))
    disp.Invoke(by_name["AutomationFCode"], 0, pythoncom.DISPATCH_METHOD, False, None, "BACK")
    disp.Invoke(by_name["Destroy"], 0, pythoncom.DISPATCH_METHOD, False, None)
    texts = [t for t, _ in handler.recorder.lines]
    assert texts[0].startswith("Change") and "AutomationFCode BACK" in texts
    assert handler.recorder.destroyed
    # an event without a handler is answered with DISP_E_MEMBERNOTFOUND (no Python exception leaks)
    with pytest.raises(pythoncom.com_error):
        disp.Invoke(by_name["HistoryOpened"], 0, pythoncom.DISPATCH_METHOD, False, None, None)
