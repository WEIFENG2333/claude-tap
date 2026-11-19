import json

import aiohttp
import pytest
from aiohttp import web

from claude_tap.bootstrap import BootstrapResponses
from claude_tap.certs import CertificateAuthority, ensure_ca
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.pipeline import ProxyContext
from claude_tap.protocols import ANTHROPIC
from claude_tap.reverse_proxy import build_app
from claude_tap.trace import EventBus, JsonlSink


def profile(tmp_path):
    path = tmp_path / "bootstrap.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "responses": [
                    {
                        "method": "GET",
                        "url": "https://api.anthropic.com/settings",
                        "status": 200,
                        "body": {"settings": {"example": True}},
                    }
                ],
            }
        )
    )
    return BootstrapResponses.load(path)


def test_profile_matching_and_validation(tmp_path):
    config = profile(tmp_path)
    assert config.response("GET", "https://api.anthropic.com/settings?cache=off")[0] == 200
    for method, url in [
        ("POST", "https://api.anthropic.com/settings"),
        ("GET", "https://other.example/settings"),
        ("GET", "https://api.anthropic.com/settings/extra"),
    ]:
        assert config.response(method, url)[0] == 404
    document = config.document
    document["responses"].append(document["responses"][0])
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="duplicate"):
        BootstrapResponses.load(path)
    with pytest.raises(ValueError, match="capture-only"):
        ProxyContext((ANTHROPIC,), "https://api.anthropic.com", EventBus(), None, bootstrap=config)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["forward", "reverse"])
async def test_bootstrap_and_model_capture_never_call_upstream(tmp_path, mode):
    import ssl

    class NoUpstream:
        async def request(self, *args, **kwargs):
            raise AssertionError("unexpected upstream request")

    bus = EventBus()
    trace = tmp_path / "trace.jsonl"
    bus.subscribe(JsonlSink(trace))
    config = profile(tmp_path)
    config.document["responses"].append(
        {
            "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "status": 503,
            "body": {"error": "must never replace model capture"},
        }
    )
    ctx = ProxyContext((ANTHROPIC,), "https://api.anthropic.com", bus, NoUpstream(), True, bootstrap=config)
    if mode == "forward":
        cert, _ = ensure_ca()
        server = ForwardProxyServer("127.0.0.1", 0, CertificateAuthority(cert, ensure_ca()[1]), ctx)
        port = await server.start()
        base = "https://api.anthropic.com"
        kwargs = {"proxy": f"http://127.0.0.1:{port}", "ssl": ssl.create_default_context(cafile=str(cert))}
        close = server.stop
    else:
        runner = web.AppRunner(build_app(ctx))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        kwargs = {}
        close = runner.cleanup
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(base + "/settings?cache=off", **kwargs) as response:
                assert response.status == 200
                assert await response.json() == {"settings": {"example": True}}
            async with client.get(base + "/unknown", **kwargs) as response:
                assert response.status == 404
            async with client.post(
                base + "/v1/messages?beta=true", json={"model": "test", "system": "hello", "messages": []}, **kwargs
            ) as response:
                assert response.status == 200
                assert (await response.json())["id"] == "msg_claude_tap_capture"
    finally:
        await close()
        await bus.close_all()
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["capture_environment"] == config.provenance()
    assert records[0]["request"]["body"]["system"] == "hello"


def test_cli_requires_export_for_bootstrap(tmp_path):
    from claude_tap.cli import main

    path = tmp_path / "bootstrap.json"
    profile(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["run", "claude", "--bootstrap-responses", str(path)])
    assert exc.value.code == 2
