"""Minimal fakes of SAP GUI scripting objects."""

from __future__ import annotations


class FakeCollection:
    def __init__(self, items):
        self.items = list(items)

    @property
    def Count(self):  # noqa: N802
        return len(self.items)

    def ElementAt(self, i):  # noqa: N802
        return self.items[i]


class FakeElement:
    def __init__(self, id, type="GuiTextField", name="", text="", children=None, fail=None):
        self.Id = id
        self.Type = type
        self.Name = name or id.rsplit("/", 1)[-1]
        self._text = text
        self.calls = []
        self.fail = fail
        self._children = children
        self.ContainerType = children is not None

    @property
    def Children(self):  # noqa: N802
        if self._children is None:
            raise AttributeError("not a container")
        return FakeCollection(self._children)

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        if self.fail:
            raise RuntimeError(self.fail)
        self._text = value
        self.calls.append(("text", value))

    Text = text

    def press(self):
        self.calls.append(("press",))

    def select(self):
        self.calls.append(("select",))

    def setFocus(self):  # noqa: N802
        self.calls.append(("setFocus",))


class FakeInfo:
    def __init__(self, system="DEV", screen=100, transaction="ZDEMO"):
        self.SystemName = system
        self.ScreenNumber = screen
        self.Transaction = transaction
        self.User = "USER"
        self.Client = "100"
        self.SessionNumber = 1
        self.Program = "SAPMZ"
        self.ApplicationServer = "srv"
        self.SystemNumber = 0


class FakeStatusBar:
    def __init__(self):
        self.Text = ""
        self.MessageType = ""


class FakeWindow:
    def __init__(self, session, handle=1000):
        self.session = session
        self.Handle = handle
        self.Text = "SAP"

    def findById(self, id):  # noqa: N802
        try:
            return self.session.elements[id]
        except KeyError:
            raise RuntimeError(f"The control could not be found by id. ({id})") from None

    def sendVKey(self, key):  # noqa: N802
        self.session.log.append(("vkey", key))


class FakeSession:
    def __init__(self, system="DEV", busy=False, handle=1000, elements=None, screen=100):
        self.Busy = busy
        self.Info = FakeInfo(system, screen)
        self.elements = {e.Id: e for e in (elements or [])}
        self.sbar = FakeStatusBar()
        self.ActiveWindow = FakeWindow(self, handle)
        self.log = []
        self.locked = False
        self.on_transaction = None

    def LockSessionUI(self):  # noqa: N802
        self.locked = True
        self.log.append(("lock",))

    def UnlockSessionUI(self):  # noqa: N802
        self.locked = False
        self.log.append(("unlock",))

    def StartTransaction(self, tcode):  # noqa: N802
        self.log.append(("tr", tcode))
        if self.on_transaction:
            self.on_transaction(self, tcode)

    def SendCommand(self, cmd):  # noqa: N802
        self.log.append(("cmd", cmd))

    def findById(self, id):  # noqa: N802
        if id == "wnd[0]/sbar":
            return self.sbar
        raise RuntimeError("not found")


class FakeConnection:
    def __init__(self, sessions):
        self.Children = FakeCollection(sessions)


class FakeEngine:
    def __init__(self, connections):
        self.Children = FakeCollection(connections)
