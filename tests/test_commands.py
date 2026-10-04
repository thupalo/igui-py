import pytest

from igui.commands import Command, CommandError, Kind, parse, to_pairs
from igui.config import SecurityConfig


def test_parses_in_order_with_repeated_keys():
    cmds = parse([("_tr", "ZDEMO"), ("_screen", "100"), ("usr/ctxtZDEMO~NUMBER", "1234567"),
                  ("_vkey", "0"), ("_vkey2", "8")])
    assert cmds == [
        Command(Kind.TRANSACTION, value="ZDEMO"),
        Command(Kind.SCREEN, value="100"),
        Command(Kind.SET_TEXT, "usr/ctxtZDEMO-NUMBER", "1234567"),
        Command(Kind.VKEY, value="0"),
        Command(Kind.VKEY, value="8"),
    ]


@pytest.mark.parametrize("suffix,kind", [(".press", Kind.PRESS), (".select", Kind.SELECT),
                                         (".setFocus", Kind.SET_FOCUS), (".text", Kind.SET_TEXT)])
def test_element_actions(suffix, kind):
    (cmd,) = parse([(f"tbar[1]/btn[8]{suffix}", "x")])
    assert cmd.kind is kind and cmd.target == "tbar[1]/btn[8]"


def test_press_is_not_select():  # iGUI 2.x bug: .press called .select
    assert parse([("btn.press", "")])[0].kind is Kind.PRESS


def test_unknown_action_rejected():  # iGUI 2.x silently ignored it
    with pytest.raises(CommandError, match="unknown element action"):
        parse([("usr/btn.click", "")])


@pytest.mark.parametrize("pair", [
    ("_tr", "SE16;DROP"), ("_tr", ""), ("_vkey", "abc"), ("_vkey", "100"), ("_screen", "-1"),
    ("usr/ctxt A", "x"), ("usr/ctxtA", "line1\nline2"), ("usr/ctxtA", "\x00"), ("_cmd", ""),
])
def test_invalid_values_rejected(pair):
    with pytest.raises(CommandError):
        parse([pair])


def test_limits():
    limits = SecurityConfig(max_commands=2, max_value_length=5)
    with pytest.raises(CommandError, match="Too many"):
        parse([("_vkey", "0")] * 3, limits)
    with pytest.raises(CommandError, match="longer"):
        parse([("usr/ctxtA", "123456")], limits)


def test_pause_default_text_and_token_ignored():
    cmds = parse([("_pause", ""), ("_pause2", "Check data"), ("_token", "secret")])
    assert [c.value for c in cmds] == ["Press OK to continue", "Check data"]


def test_dump_must_be_alone():
    assert parse([("dump", "")]) == [Command(Kind.DUMP)]
    with pytest.raises(CommandError):
        parse([("dump", ""), ("_vkey", "0")])


def test_empty_rejected():
    with pytest.raises(CommandError):
        parse([])


def test_roundtrip():
    pairs = [("_tr", "SE16"), ("usr/ctxtDATABROWSE~TABLENAME.text", "MARA"), ("tbar[1]/btn[8].press", "")]
    assert to_pairs(parse(pairs)) == pairs


def test_describe_masks_field_values():
    from igui.commands import describe
    text = describe(parse([("_tr", "ZDEMO"), ("usr/ctxtZDEMO~NUMBER", "7654321"), ("btn.press", ""), ("_vkey", "0")]))
    assert text == "_tr=ZDEMO, usr/ctxtZDEMO-NUMBER=<7 chars>, btn.press, _vkey=0"
    assert "7654321" not in text
