from fakes import FakeConnection, FakeElement, FakeEngine, FakeSession

from igui.commands import parse
from igui.sap.dump import collect, format_dump, relative_id
from igui.sap.executor import execute
from igui.sap.model import Status
from igui.sap.recorder import RecordingConverter
from igui.sap.session import pick_session


def ident(h):
    return h


# ---- session selection --------------------------------------------------------

def test_prefers_last_used_window():
    a, b = FakeSession(handle=1), FakeSession(handle=2)
    engine = FakeEngine([FakeConnection([a, b])])
    assert pick_session(engine, "DEV", 2, ident) == (b, Status.OK)
    assert pick_session(engine, "DEV", 0, ident) == (a, Status.OK)


def test_skips_other_systems_and_busy_sessions():
    other, busy, free = FakeSession(system="QAS"), FakeSession(busy=True), FakeSession(handle=7)
    engine = FakeEngine([FakeConnection([other, busy]), FakeConnection([free])])
    assert pick_session(engine, "DEV", 0, ident) == (free, Status.OK)


def test_reports_all_busy_and_login_required():
    assert pick_session(FakeEngine([FakeConnection([FakeSession(busy=True)])]), "DEV", 0, ident) == (None, Status.ALL_BUSY)
    assert pick_session(FakeEngine([FakeConnection([FakeSession(system="QAS")])]), "DEV", 0, ident) == (None, Status.LOGIN_REQUIRED)


# ---- executor -------------------------------------------------------------------

def session_with_fields():
    field = FakeElement("usr/ctxtZDEMO-NUMBER")
    button = FakeElement("tbar[1]/btn[8]", type="GuiButton")
    return FakeSession(elements=[field, button]), field, button


def test_executes_commands_and_unlocks():
    s, field, button = session_with_fields()
    result = execute(s, parse([("_tr", "ZDEMO"), ("_screen", "100"), ("usr/ctxtZDEMO~NUMBER", "123"),
                               ("tbar[1]/btn[8].press", ""), ("_vkey", "0")]), pause=lambda t: None)
    assert result.status is Status.OK
    assert field.text == "123"
    assert button.calls == [("press",)]
    assert s.log == [("lock",), ("tr", "ZDEMO"), ("vkey", 0), ("unlock",)]


def test_screen_mismatch_stops_and_unlocks():
    s, field, _ = session_with_fields()
    result = execute(s, parse([("_screen", "200"), ("usr/ctxtZDEMO~NUMBER", "1")]), pause=lambda t: None)
    assert result.status is Status.SCREEN_MISMATCH
    assert field.text == "" and not s.locked


def test_failure_stops_and_unlocks():  # 2.x could leave the session UI locked
    s, _, button = session_with_fields()
    result = execute(s, parse([("usr/missing", "1"), ("tbar[1]/btn[8].press", "")]), pause=lambda t: None)
    assert result.status is Status.SAP_ERROR
    assert "could not be found" in result.detail
    assert button.calls == [] and not s.locked


def test_error_message_in_status_bar_stops():
    s, field, _ = session_with_fields()

    def fail(sess, tcode):
        sess.sbar.Text, sess.sbar.MessageType = "Transaction XX does not exist", "E"
    s.on_transaction = fail
    result = execute(s, parse([("_tr", "XX"), ("usr/ctxtZDEMO~NUMBER", "1")]), pause=lambda t: None)
    assert result.status is Status.SAP_ERROR
    assert result.messages == ["Transaction XX does not exist"]
    assert field.text == ""


def test_pause_unlocks_ui_while_waiting():
    s, _, _ = session_with_fields()
    seen = []
    execute(s, parse([("_pause", "Check"), ("_vkey", "0")]), pause=lambda t: seen.append((t, s.locked)))
    assert seen == [("Check", False)]
    assert s.log[-2:] == [("vkey", 0), ("unlock",)]


# ---- dump -------------------------------------------------------------------------

def test_relative_id():
    assert relative_id("/app/con[0]/ses[0]/wnd[0]/usr/ctxtA-B") == "usr/ctxtA~B"
    assert relative_id("/app/con[0]/ses[0]/wnd[0]") == ""


def test_collect_tree():
    tree = FakeElement("/app/con[0]/ses[0]", type="GuiSession", text=None, children=[
        FakeElement("/app/con[0]/ses[0]/wnd[0]", type="GuiMainWindow", text="Main", children=[
            FakeElement("/app/con[0]/ses[0]/wnd[0]/titl", type="GuiTitlebar", text="Demo screen"),
            FakeElement("/app/con[0]/ses[0]/wnd[0]/usr/ctxtZ-NR", type="GuiCTextField", name="Z-NR", text="1"),
            FakeElement("/app/con[0]/ses[0]/wnd[0]/usr/txt%X", type="GuiTextField", name="%X"),
            FakeElement("/app/con[0]/ses[0]/wnd[0]/usr/lbl", type="GuiLabel", text="Label"),
        ])])
    dump = collect(tree)
    assert dump.title == "Demo screen"  # 2.x assigned a string to a gElement here
    assert [e.id for e in dump.inputs] == ["usr/ctxtZ~NR"]
    assert len(dump.lines) == 6  # root included (2.x crashed on it)
    text = format_dump("User: X\n", dump, "/ctx/DEMO?NUMBER=1")
    assert "Macro link: /ctx/DEMO?NUMBER=1" in text and "dumpAll:" in text


# ---- recorder -----------------------------------------------------------------

def test_recording_converter():
    conv = RecordingConverter()
    conv.on_end_request(1000)
    base = "/app/con[0]/ses[0]/wnd[0]"
    conv.on_change(f"{base}/tbar[0]/okcd", [("SP", "text", "/nSE16")])
    conv.on_change(base, [("M", "sendVKey", 0)])
    conv.on_end_request(1000)
    conv.on_change(f"{base}/usr/ctxtDATABROWSE-TABLENAME", (("SP", "text", "MARA"),))
    conv.on_end_request(2000)
    conv.on_change(f"{base}/tbar[1]/btn[8]", [("M", "press")])
    notes = conv.on_change(f"{base}/usr/cntlGRID", [("M", "doubleClickCurrentCell")])
    assert conv.pairs == [
        ("tbar[0]/okcd.text", "/nSE16"), ("_vkey", "0"),
        ("usr/ctxtDATABROWSE~TABLENAME.text", "MARA"),
        ("_screen", "2000"), ("tbar[1]/btn[8].press", ""),
    ]
    assert "not convertible" in notes[0]
    # the replay body is accepted by the command parser
    from urllib.parse import parse_qsl
    assert len(parse(parse_qsl(conv.body(), keep_blank_values=True))) == 5


def test_recording_vbs():
    conv = RecordingConverter()
    base = "/app/con[1]/ses[0]/wnd[0]"
    conv.on_change(f"{base}/usr/ctxtDATABROWSE-TABLENAME", [("SP", "text", 'MA"RA')])
    conv.on_change(base, [("M", "sendVKey", 0)])
    conv.on_change(f"{base}/usr/chkX", [("SP", "selected", True)])
    vbs = conv.vbs()
    assert vbs.startswith("If Not IsObject(application)")
    assert 'session.findById("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").text = "MA""RA"' in vbs
    assert 'session.findById("wnd[0]").sendVKey 0' in vbs
    assert 'session.findById("wnd[0]/usr/chkX").selected = true' in vbs


def test_recording_api_commands_taken_as_sent():
    conv = RecordingConverter()
    conv.on_end_request(1000)
    conv.add_commands(parse([("_tr", "SE38"), ("usr/ctxtRS38M~PROGRAMM", "Z\"X"), ("_screen", "1000")]))
    conv.suspended = True
    assert "not recorded again" in conv.on_change("/app/con[0]/ses[0]/wnd[0]", [("M", "sendVKey", 0)])[0]
    conv.suspended = False
    assert conv.pairs == [("_tr", "SE38"), ("usr/ctxtRS38M~PROGRAMM.text", 'Z"X'), ("_screen", "1000")]
    vbs = conv.vbs()
    assert 'session.StartTransaction "SE38"' in vbs
    assert 'session.ActiveWindow.findById("usr/ctxtRS38M-PROGRAMM").text = "Z""X"' in vbs
    assert "If session.Info.ScreenNumber <> 1000 Then WScript.Quit" in vbs
    assert "sendVKey" not in vbs
