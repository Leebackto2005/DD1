# -*- coding: utf-8 -*-
"""一次性工具：捕获企业机器人在钉钉群里的 openConversationId（群 ID）。

用法：
    python dd_capture_group.py

流程：
    1. 探测应用机器人的 Stream 长连接是否可用；
    2. 连上后，在机器人已加入的那个群里发一条消息（建议 @一下机器人）；
    3. 捕获该群的 conversationId（即 openConversationId）+ 群名，
       写入 data/dingtalk_group_id.json，然后自动退出。

前置条件（open.dingtalk.com → 企业内部应用）：
    - 应用已添加「机器人」能力；
    - 机器人「消息接收模式」为 Stream 长连接；
    - 机器人已加入目标群。
"""
import json
import os
import sys
import time

import requests

import dingtalk_stream
from dingtalk_stream import AckMessage

from config import DINGTALK_APP_KEY, DINGTALK_APP_SECRET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUT_FILE = os.path.join(DATA_DIR, "dingtalk_group_id.json")
OPEN_CONNECTION_API = "https://api.dingtalk.com/v1.0/gateway/connections/open"

EXPECT_GROUP_NAME = sys.argv[1] if len(sys.argv) > 1 else "HAI 平台 Bug 反馈群"  # 目标群名（可命令行覆盖，用于提示确认，不强制匹配）


def _preflight():
    """探测 Stream 连接是否可用；不可用直接报错退出，避免挂机干等。"""
    if not (DINGTALK_APP_KEY and DINGTALK_APP_SECRET):
        print("❌ 请在 .env 配置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET")
        sys.exit(1)
    print("① 探测应用机器人 Stream 长连接…", flush=True)
    try:
        resp = requests.post(
            OPEN_CONNECTION_API,
            json={
                "clientId": DINGTALK_APP_KEY,
                "clientSecret": DINGTALK_APP_SECRET,
                "subscriptions": [{"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}],
                "ua": "dd-capture-group/1.0",
                "localIp": "127.0.0.1",
            },
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Stream 探测失败：{exc}")
        sys.exit(1)
    if resp.status_code >= 400 or data.get("code"):
        print(f"❌ Stream 探测失败 HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)}")
        print("   → 应用机器人「消息接收模式」可能不是 Stream。请在 open.dingtalk.com")
        print("     → 企业内部应用 → 机器人 → 消息接收模式 选「Stream 模式」后重试。")
        sys.exit(1)
    print(f"✅ Stream 可用（endpoint={data.get('endpoint', '?')}）", flush=True)


def _save_group(conversation_id, title, sender_nick, text):
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "openConversationId": conversation_id,
        "groupTitle": title or "",
        "capturedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "lastMessage": (text or "")[:200],
        "senderNick": sender_nick or "",
    }
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"✅ 已捕获群 openConversationId：{conversation_id}", flush=True)
    print(f"   群名：{title or '（未提供）'}", flush=True)
    print(f"   已写入 {OUT_FILE}", flush=True)


class _CaptureHandler(dingtalk_stream.ChatbotHandler):
    async def process(self, callback: dingtalk_stream.CallbackMessage):
        data = callback.data or {}
        conv_type = str(data.get("conversationType") or "")
        conv_id = str(data.get("conversationId") or "").strip()
        title = data.get("conversationTitle") or ""
        sender = data.get("senderNick") or ""
        text = ""
        raw_text = data.get("text")
        if isinstance(raw_text, dict):
            text = raw_text.get("content") or ""
        elif isinstance(raw_text, str):
            text = raw_text
        if conv_type == "2" and conv_id:
            print(f"📨 [群消息] 群名={title!r}", flush=True)
            print(f"   conversationId={conv_id}", flush=True)
            print(f"   发送者={sender} 内容={text[:60]!r}", flush=True)
            if EXPECT_GROUP_NAME and EXPECT_GROUP_NAME not in title:
                print(f"   ⚠️ 群名不是「{EXPECT_GROUP_NAME}」，但仍按收到的第一个群保存；如不对请 Ctrl-C 后重试", flush=True)
            _save_group(conv_id, title, sender, text)
            os._exit(0)  # 一次性工具：捕获到群 ID 即退出
        else:
            print(f"📨 [单聊/其他] conversationType={conv_type} sender={sender} 内容={text[:40]!r}（忽略）", flush=True)
        return AckMessage.STATUS_OK, "OK"


def main():
    _preflight()
    print("② 启动 Stream 长连接…", flush=True)
    print(f"   请现在去群「{EXPECT_GROUP_NAME}」里发一条消息（建议 @机器人），", flush=True)
    print("   捕获到群 ID 后本脚本会自动退出。", flush=True)
    credential = dingtalk_stream.Credential(DINGTALK_APP_KEY, DINGTALK_APP_SECRET)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(
        dingtalk_stream.ChatbotMessage.TOPIC, _CaptureHandler()
    )
    client.start_forever()


if __name__ == "__main__":
    main()
