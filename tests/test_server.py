import json

import pytest

from igui import paths
from igui.config import Config, SecurityConfig, config_from_dict
from igui.macros import MacroSet
from igui.server import create_app

PORT = 50501
HOST = f"localhost:{PORT}"


class Sink:
    def __init__(self, accept=True):
        self.calls = []
        self.accept = accept

    def run_commands(self, commands, source=""):
        self.calls.append(commands)
        return self.accept


MACROS = MacroSet.from_json(paths.resource("macros.default.json").read_text(encoding="utf-8"))


def client(sink=None, **security):
    cfg = Config(port=PORT, security=SecurityConfig(**security))
    app = create_app(cfg, sink or Sink(), lambda: MACROS, "help")
    return app.test_client()


def post(c, path, data="", **headers):
    headers.setdefault("Host", HOST)
    return c.post(path, data=data, headers=headers, content_type="application/x-www-form-urlencoded")


def test_post_go_queues_commands_in_order():
    sink = Sink()
    r = post(client(sink), "/go", "_tr=SE16&usr/ctxtA~B.text=x&_vkey=0")
    assert r.status_code == 202
    assert [c.kind.value for c in sink.calls[0]] == ["_tr", "text", "_vkey"]
    assert r.headers["Cache-Control"].startswith("no-store")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in r.headers


def test_json_body():
    sink = Sink()
    r = client(sink).post("/go", headers={"Host": HOST},
                          json={"commands": [["_tr", "SE16"], ["_vkey", "0"], ["_vkey", "8"]]})
    assert r.status_code == 202 and len(sink.calls[0]) == 3


def test_remote_client_rejected():
    r = client().post("/go", data="_vkey=0", headers={"Host": HOST},
                      environ_base={"REMOTE_ADDR": "10.1.2.3"})
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["evil.example:50501", "localhost:8080", "attacker.localhost"])
def test_dns_rebinding_host_rejected(host):
    assert client().get("/iGUI/context.json", headers={"Host": host}).status_code == 403


def test_ipv6_loopback_accepted():
    r = client().get("/version", headers={"Host": f"[::1]:{PORT}"}, environ_base={"REMOTE_ADDR": "::1"})
    assert r.status_code == 200 and "iGUI v" in r.text


def test_get_disabled_by_default_and_legacy_mode():
    assert client().get("/go?_vkey=0", headers={"Host": HOST}).status_code == 405
    sink = Sink()
    r = client(sink, allow_get_actions=True).get("/go?_vkey=0", headers={"Host": HOST})
    assert r.status_code == 202 and sink.calls


def test_cross_site_browser_requests_blocked():
    sink = Sink()
    c = client(sink, allow_get_actions=True)
    assert post(c, "/go", "_vkey=0", Origin="https://evil.example").status_code == 403
    assert post(c, "/go", "_vkey=0", Origin="null").status_code == 403
    r = c.get("/go?_vkey=0", headers={"Host": HOST, "Sec-Fetch-Site": "cross-site",
                                      "Referer": "https://evil.example/page"})
    assert r.status_code == 403
    r = c.get("/go?_vkey=0", headers={"Host": HOST, "Referer": "https://evil.example/page"})
    assert r.status_code == 403
    assert sink.calls == []


def test_allowed_origin_gets_cors_and_access():
    sink = Sink()
    c = client(sink, allowed_origins=frozenset({"https://portal.example"}))
    r = post(c, "/go", "_vkey=0", Origin="https://portal.example")
    assert r.status_code == 202
    assert r.headers["Access-Control-Allow-Origin"] == "https://portal.example"
    pre = c.options("/go", headers={"Host": HOST, "Origin": "https://portal.example",
                                    "Access-Control-Request-Method": "POST",
                                    "Access-Control-Request-Private-Network": "true"})
    assert pre.status_code == 204 and pre.headers["Access-Control-Allow-Private-Network"] == "true"
    assert c.options("/go", headers={"Host": HOST, "Origin": "https://evil.example"}).status_code == 403


def test_typed_url_navigation_allowed_in_legacy_mode():
    sink = Sink()
    r = client(sink, allow_get_actions=True).get("/go?_vkey=0", headers={"Host": HOST, "Sec-Fetch-Site": "none"})
    assert r.status_code == 202


def test_token():
    token = "0123456789abcdef0123"
    c = client(api_token=token)
    assert post(c, "/go", "_vkey=0").status_code == 401
    assert post(c, "/go", "_vkey=0", **{"X-iGui-Token": "wrong-token-value!!"}).status_code == 401
    assert post(c, "/go", "_vkey=0", **{"X-iGui-Token": token}).status_code == 202
    assert post(c, "/go", "_vkey=0", Authorization=f"Bearer {token}").status_code == 202
    assert post(c, "/go", f"_vkey=0&_token={token}").status_code == 202
    assert post(c, f"/go?_token={token}", "_vkey=0").status_code == 401  # query token only in legacy mode


def test_invalid_commands_rejected_before_sap():
    sink = Sink()
    r = post(client(sink), "/go", "_tr=SE16%3BDROP&_vkey=0")
    assert r.status_code == 400 and sink.calls == []


def test_body_size_limit():
    r = post(client(max_body_bytes=100), "/go", "usr/ctxtA=" + "x" * 200)
    assert r.status_code == 413


def test_busy_returns_503():
    assert post(client(Sink(accept=False)), "/go", "_vkey=0").status_code == 503


def test_ctx_macro():
    sink = Sink()
    c = client(sink)
    r = post(c, "/ctx/TABLE", "TABLE=MARA")
    assert r.status_code == 202
    texts = [(cmd.target, cmd.value) for cmd in sink.calls[0] if cmd.kind.value == "text"]
    assert ("usr/ctxtDATABROWSE-TABLENAME", "MARA") in texts
    assert post(c, "/ctx/NOPE", "x=1").status_code == 404
    assert post(c, "/ctx/TABLE", "").status_code == 400  # missing parameter


def test_context_json_and_echo_are_json():
    c = client()
    data = json.loads(c.get("/iGUI/context.json", headers={"Host": HOST}).text)
    assert [m["id"] for m in data["l"]] == ["TABLE", "PROGRAM"]
    r = c.get("/get?a=<script>&b=2", headers={"Host": HOST})
    assert r.mimetype == "application/json" and r.json == {"params": [["a", "<script>"], ["b", "2"]]}


def test_config_validation():
    cfg = config_from_dict({"security": {"allowed_origins": ["HTTPS://Portal.Example:443/"]}})
    assert cfg.security.allowed_origins == frozenset({"https://portal.example"})
    from igui.config import ConfigError
    with pytest.raises(ConfigError):
        config_from_dict({"security": {"api_token": "short"}})
    with pytest.raises(ConfigError):
        config_from_dict({"macros": {"url": "file:///c:/x.json"}})


def test_relative_macros_file_is_relative_to_its_config(tmp_path):
    from igui.config import load_config
    site = tmp_path / "site"
    site.mkdir()
    (site / "igui.toml").write_text('[macros]\nfile = "macros.json"\n', encoding="utf-8")
    (tmp_path / "user.toml").write_text('[sap]\nsid = "QAS"\n', encoding="utf-8")
    cfg = load_config([site / "igui.toml", tmp_path / "user.toml"])
    assert cfg.macros_file == site / "macros.json" and cfg.sap_sid == "QAS"
