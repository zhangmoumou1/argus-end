import hashlib
import time
import urllib.parse

from app.core.msg.notification import Notification
from app.middleware.AsyncHttpClient import AsyncRequest


class DingTalk(Notification):
    def __init__(self, webhook_url: str, secret: str = None):
        self.webhook_url = webhook_url
        self.secret = secret

    def _sign(self):
        """钉钉加签：timestamp + \n + secret -> HMAC-SHA256 base64"""
        if not self.secret:
            return {}
        timestamp = str(int(round(time.time() * 1000)))
        sign_str = f"{timestamp}\n{self.secret}"
        hmac_code = hashlib.sha256(sign_str.encode('utf-8')).digest()
        import base64
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return {"timestamp": timestamp, "sign": sign}

    async def send_msg(self, subject, content, attachment=None, *receiver, **kwargs):
        params = self._sign()
        url = self.webhook_url
        if params:
            url += f"?timestamp={params['timestamp']}&sign={params['sign']}"

        data = {
            "msgtype": "actionCard",
            "actionCard": {
                "title": subject,
                "text": "![screenshot](https://static.pity.fun/picture/走势监测.png)\n%s" % content,
                "singleTitle": '👉 查看报告',
                "singleURL": f"""dingtalk://dingtalkclient/page/link?url={urllib.parse.quote(kwargs.get("link", ""))}&pc_slide=false"""
            },
            "at": {
                "atMobiles": list(receiver),
            }
        }
        r = AsyncRequest(url, headers={'Content-Type': 'application/json'}, timeout=15, json=data)
        response = await r.invoke("POST")
        if not response.get("status"):
            raise Exception("发送钉钉通知失败")
