from app.core.msg.notification import Notification
from app.middleware.AsyncHttpClient import AsyncRequest


class FeiShu(Notification):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send_msg(self, subject, content, attachment=None, *receiver, **kwargs):
        data = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": subject}
                },
                "elements": [
                    {"tag": "markdown", "content": content}
                ]
            }
        }
        r = AsyncRequest(self.webhook_url, headers={'Content-Type': 'application/json'}, timeout=15, json=data)
        response = await r.invoke("POST")
        if not response.get("status"):
            raise Exception("发送飞书通知失败")
