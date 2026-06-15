import asyncio

from mitmproxy import http
from pymock import Mock

from app.core.mock_rule import list_mock_rules_for_proxy, match_mock_rule, safe_json_loads


class ArgusMock(object):
    def __init__(self):
        self.mock = Mock()

    async def request(self, flow):
        rules = await list_mock_rules_for_proxy()
        matched = match_mock_rule(flow, rules)
        if matched is None:
            return
        delay_ms = int(matched.get("response_delay_ms") or 0)
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
        headers = safe_json_loads(matched.get("response_headers"), {})
        headers["x-argux-mock"] = "hit"
        headers["x-argux-mock-rule"] = str(matched.get("id"))
        flow.response = http.Response.make(
            int(matched.get("response_status") or 200),
            str(matched.get("response_body") or ""),
            headers,
        )
