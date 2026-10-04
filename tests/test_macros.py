import json

import pytest

from igui import paths
from igui.macros import Macro, MacroError, MacroSet


def make_macro(**kw):
    data = dict(id="DEMO", transaction="ZDEMO", screen=100,
                link="_tr=ZDEMO&usr/ctxtZDEMO~YEAR.text=&usr/ctxtZDEMO~NUMBER.text=%NUMBER%&_vkey=0",
                params=["usr/ctxtZDEMO~NUMBER"])
    data.update(kw)
    return Macro(**data)


def test_render_keeps_order():
    assert make_macro().render({"NUMBER": "1234567"}) == [
        ("_tr", "ZDEMO"), ("usr/ctxtZDEMO~YEAR.text", ""),
        ("usr/ctxtZDEMO~NUMBER.text", "1234567"), ("_vkey", "0")]


def test_render_values_with_special_characters_are_not_split():  # iGUI 2.x split on & and =
    pairs = make_macro().render({"NUMBER": "a&_tr=SE38=x%41"})
    assert ("usr/ctxtZDEMO~NUMBER.text", "a&_tr=SE38=x%41") in pairs
    assert [k for k, _ in pairs].count("_tr") == 1


def test_missing_parameter():
    with pytest.raises(MacroError, match="NUMBER"):
        make_macro().render({})


def test_link_for_values_uses_placeholder_names():  # fixed Context.getLink
    link = make_macro().link_for_values(lambda el: {"usr/ctxtZDEMO-NUMBER": "12 34"}[el])
    assert link == "/ctx/DEMO?NUMBER=12+34"


def test_link_for_values_falls_back_to_p_numbers():
    macro = make_macro(link="_tr=X&usr/a.text=%p1%", params=["usr/a", "usr/b"])
    assert macro.link_for_values(lambda el: el[-1]) == "/ctx/DEMO?p1=a&p2=b"


def test_set_lookup_and_json_roundtrip():
    ms = MacroSet.from_json(paths.resource("macros.default.json").read_text(encoding="utf-8"))
    assert len(ms) == 2
    assert ms.get("PROGRAM").transaction == "SE38"
    assert ms.for_screen("SE16", "230").id == "TABLE"  # screen as string (2.x compared str/int)
    assert ms.for_screen("SE16", 200) is None
    assert MacroSet.from_json(ms.to_json()).get("TABLE") == ms.get("TABLE")


@pytest.mark.parametrize("text", ["", "[]", '{"l": [{"name": "x"}]}', '{"l": [{"id": "a b", "link": "x"}]}'])
def test_invalid_definitions(text):
    with pytest.raises(MacroError):
        MacroSet.from_json(text)
