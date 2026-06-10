from app.core.msg.notification import Notification
from app.middleware.AsyncHttpClient import AsyncRequest


class WeCom(Notification):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send_msg(self, subject, content, attachment=None, *receiver, **kwargs):
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": content
            }
        }
        r = AsyncRequest(self.webhook_url, headers={'Content-Type': 'application/json'}, timeout=15, json=data)
        response = await r.invoke("POST")
        if not response.get("status"):
            raise Exception("发送企业微信通知失败")
