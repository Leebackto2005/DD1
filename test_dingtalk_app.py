"""钉钉企业应用凭证验证与机器人查询。

用法：
    python test_dingtalk_app.py

用 .env 里的 AppKey/AppSecret 换 access_token，验证凭证是否有效，
并尝试查询企业内部机器人列表（需要应用有机器人权限）。
"""
import json
import os
import sys

import requests

# 读取 .env 里的值（避免引入 config 依赖）
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def _load_env():
    """简单解析 .env，返回 dict。"""
    env = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    return env


def main():
    env = _load_env()
    # 从 .env 读取——根据之前的分析，ding 开头的是 AppKey，64 位的是 AppSecret
    app_key = env.get("DINGTALK_SECRET", "").removeprefix("SEC")  # 去掉误加的 SEC 前缀
    app_secret = env.get("DINGTALK_WEBHOOK_URL", "").split("access_token=")[-1]

    # 如果上面解析的不对，尝试反过来
    # ding 开头的 20 位 = AppKey，64 位 = AppSecret
    raw_secret = env.get("DINGTALK_SECRET", "")
    raw_token = env.get("DINGTALK_WEBHOOK_URL", "").split("access_token=")[-1]
    if raw_token.startswith("ding"):
        app_key = raw_token
        app_secret = raw_secret.removeprefix("SEC")
    if raw_secret.removeprefix("SEC").startswith("ding"):
        app_key = raw_secret.removeprefix("SEC")
        app_secret = raw_token

    print(f"AppKey:    {app_key}")
    print(f"AppSecret: {app_secret[:10]}...{app_secret[-6:]}")
    print()

    # Step 1: 换 access_token
    print("=" * 50)
    print("Step 1: 获取 access_token")
    print("=" * 50)
    resp = requests.get(
        "https://oapi.dingtalk.com/gettoken",
        params={"appkey": app_key, "appsecret": app_secret},
        timeout=10,
    )
    data = resp.json()
    print(json.dumps(data, ensure_ascii=False, indent=2))

    if data.get("errcode") != 0:
        print(f"\n[失败] 凭证无效: {data.get('errmsg')}")
        print("\n可能原因:")
        print("  - AppKey/AppSecret 错误或已失效")
        print("  - 应用已被删除")
        return 1

    access_token = data["access_token"]
    print(f"\n[成功] access_token: {access_token[:20]}...")

    # Step 2: 查询企业内部机器人列表
    print()
    print("=" * 50)
    print("Step 2: 查询机器人列表")
    print("=" * 50)
    resp2 = requests.post(
        "https://oapi.dingtalk.com/topapi/robot/list",
        params={"access_token": access_token},
        json={},
        timeout=10,
    )
    data2 = resp2.json()
    print(json.dumps(data2, ensure_ascii=False, indent=2))

    if data2.get("errcode") == 0 and data2.get("result"):
        robots = data2["result"]
        print(f"\n[成功] 找到 {len(robots)} 个机器人:")
        for r in robots:
            print(f"  - 名称: {r.get('name', '?')}")
            print(f"    robotCode: {r.get('robotCode', '?')}")
            print()

    # Step 3: 尝试查询群列表（chatbot 接口）
    print("=" * 50)
    print("Step 3: 查询机器人所在群")
    print("=" * 50)
    resp3 = requests.post(
        "https://oapi.dingtalk.com/topapi/chat/listbyrobot",
        params={"access_token": access_token},
        json={"chatbotId": data2["result"][0]["robotCode"] if data2.get("result") else ""},
        timeout=10,
    )
    data3 = resp3.json()
    print(json.dumps(data3, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
