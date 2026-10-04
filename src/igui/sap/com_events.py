"""COM event subscription without makepy.

pywin32's ``WithEvents`` needs a makepy wrapper of the type library, and makepy
fails on SAP's sapfewse type library (AssertionError in genpy).  Instead the
event interface (IID + DISPIDs) is read from the registered type library and the
handler is connected directly through IConnectionPoint.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

SAPFEWSE_TYPELIB = "{5EA428A0-F2B8-45E7-99FA-0E994E82B5BC}"


def source_interface(coclass_name: str, typelib: str = SAPFEWSE_TYPELIB) -> tuple[Any, dict[int, str]]:
    """(events IID, {dispid: event name}) of the default source interface of a coclass."""
    import pythoncom
    import pywintypes

    tlb = pythoncom.LoadRegTypeLib(pywintypes.IID(typelib), 1, 0, 0)
    for i in range(tlb.GetTypeInfoCount()):
        if tlb.GetTypeInfoType(i) != pythoncom.TKIND_COCLASS or tlb.GetDocumentation(i)[0] != coclass_name:
            continue
        coclass = tlb.GetTypeInfo(i)
        for j in range(coclass.GetTypeAttr().cImplTypes):
            flags = coclass.GetImplTypeFlags(j)
            wanted = pythoncom.IMPLTYPEFLAG_FDEFAULT | pythoncom.IMPLTYPEFLAG_FSOURCE
            if flags & wanted != wanted:
                continue
            events = coclass.GetRefTypeInfo(coclass.GetRefTypeOfImplType(j))
            attr = events.GetTypeAttr()
            names = {}
            for k in range(attr.cFuncs):
                memid = events.GetFuncDesc(k).memid
                names[memid] = events.GetNames(memid)[0]
            return attr.iid, names
    raise LookupError(f"No event interface for {coclass_name} in type library {typelib}")


def make_sink(handler: Any, iid: Any, events: dict[int, str]) -> Any:
    """Wrap ``handler`` (methods named On<Event>) as an IDispatch event sink."""
    from win32com.server.policy import EventHandlerPolicy
    from win32com.server.util import wrap

    handler._public_methods_ = []
    # only events the handler implements; others are answered with "member not found"
    handler._dispid_to_func_ = {d: f"On{n}" for d, n in events.items() if hasattr(handler, f"On{n}")}
    # SAP queries the sink for the events IID.  pythoncom has no gateway for an
    # unregistered dispinterface (listing it in _com_interfaces_ -> E_NOINTERFACE),
    # so answer that query with an IDispatch wrapper, as makepy sinks do.
    handler._query_interface_ = lambda req: wrap(handler, usePolicy=EventHandlerPolicy) if req == iid else 0
    return wrap(handler, usePolicy=EventHandlerPolicy)


class EventConnection:
    """Connects ``handler`` (methods named On<Event>) to ``com_object`` events."""

    def __init__(self, com_object: Any, handler: Any, coclass_name: str) -> None:
        import pythoncom

        iid, events = source_interface(coclass_name)
        oleobj = getattr(com_object, "_oleobj_", com_object)
        container = oleobj.QueryInterface(pythoncom.IID_IConnectionPointContainer)
        self._point = container.FindConnectionPoint(iid)
        self._cookie = self._point.Advise(make_sink(handler, iid, events))
        log.debug("Subscribed to %s events (%d handlers)", coclass_name, len(handler._dispid_to_func_))

    def close(self) -> None:
        if self._point is not None:
            try:
                self._point.Unadvise(self._cookie)
            except Exception as exc:
                log.debug("Unadvise failed: %s", exc)
            self._point = None
