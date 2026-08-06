"""用企业内部应用机器人给指定员工发钉钉单聊消息（O2O）。

用法：
    python send_dingtalk_user.py                                  # 默认发给周大卓，默认测试文本
    python send_dingtalk_user.py --name 周大卓 --text "你好"
    python send_dingtalk_user.py --user-id 17857243944748049 --text "你好"
    python send_dingtalk_user.py --markdown --title "标题" --text "正文"
    python send_dingtalk_user.py --markdown --file reports/dd_report_2026-08.md   # 发文件内容（超长自动分段）

前置条件（钉钉开发者后台 open.dingtalk.com）：
    - 应用已添加「机器人」能力、授权「企业内机器人发送消息」权限并发布上线；
    - 接收方需为企业内员工；按姓名搜索需要「成员信息读权限」。

凭证：.env 里的 DINGTALK_APP_KEY / DINGTALK_APP_SECRET（企业内部应用，AppSecret 不带 SEC 前缀）。
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime

import requests

from config import (
    DINGTALK_APP_KEY,
    DINGTALK_APP_SECRET,
    DINGTALK_RECIPIENT_NAME,
    DINGTALK_RECIPIENT_USER_ID,
    IMGBB_API_KEY,
)

logger = logging.getLogger("dd_oto")

OAUTH_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
SEARCH_URL = "https://api.dingtalk.com/v1.0/contact/users/search"
OTO_SEND_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
GROUP_SEND_URL = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"

_TOKEN_CACHE = {}


def get_access_token(app_key, app_secret):
    """v1.0 oauth2 换 token，带过期缓存（提前 300s 过期）。失败抛 RuntimeError。"""
    now = time.time()
    token = _TOKEN_CACHE.get("access_token")
    if token and _TOKEN_CACHE.get("expires_at", 0) > now:
        return token
    resp = requests.post(OAUTH_URL, json={"appKey": app_key, "appSecret": app_secret}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("accessToken")
    if not token:
        raise RuntimeError(f"获取 accessToken 失败: {data}")
    _TOKEN_CACHE["access_token"] = token
    _TOKEN_CACHE["expires_at"] = now + int(data.get("expireIn", 7200)) - 300
    return token


def _extract_user_ids(items):
    """兼容 list 里是字符串 userId 或对象 {'userId': ...} 两种返回。"""
    ids = []
    for item in items or []:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and item.get("userId"):
            ids.append(item["userId"])
    return ids


def search_user_id(access_token, query_word):
    """按姓名/拼音搜索通讯录用户，返回匹配 userId 列表。失败抛 RuntimeError。"""
    resp = requests.post(
        SEARCH_URL,
        headers={"x-acs-dingtalk-access-token": access_token},
        json={"queryWord": query_word, "offset": 0, "size": 10, "fullMatchField": 1},
        timeout=10,
    )
    data = resp.json()
    if resp.status_code >= 400 or data.get("code"):
        raise RuntimeError(f"用户搜索失败 HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)}")
    return _extract_user_ids(data.get("list"))


def _resolve_user_ids(access_token, recipient_name, user_id=None):
    """解析收件人 userId：优先直传/配置的 DINGTALK_RECIPIENT_USER_ID，否则按姓名搜索。

    返回 (user_ids, note)。失败抛 RuntimeError。
    """
    user_id = (user_id or DINGTALK_RECIPIENT_USER_ID or "").strip()
    if user_id:
        return [user_id], f"使用配置的 userId：{user_id}"
    ids = search_user_id(access_token, recipient_name)
    if not ids:
        raise RuntimeError(f"通讯录中未找到「{recipient_name}」")
    if len(ids) > 1:
        raise RuntimeError(f"找到 {len(ids)} 个同名用户 {ids}，请用 --user-id 指定具体对象")
    return ids, f"按姓名「{recipient_name}」解析到 userId：{ids[0]}"


def send_oto(access_token, robot_code, user_ids, msg_key, msg_param):
    """企业机器人批量发送单聊消息。失败抛 RuntimeError（含响应体）。"""
    payload = {
        "robotCode": robot_code,
        "userIds": user_ids,
        "msgKey": msg_key,
        "msgParam": json.dumps(msg_param, ensure_ascii=False),
    }
    resp = requests.post(
        OTO_SEND_URL,
        headers={"x-acs-dingtalk-access-token": access_token},
        json=payload,
        timeout=10,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}
    if resp.status_code >= 400 or data.get("code"):
        raise RuntimeError(f"发送失败 HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)}")
    return data


def send_group(access_token, robot_code, open_conversation_id, msg_key, msg_param):
    """企业机器人往群会话发消息。失败抛 RuntimeError（含响应体）。"""
    payload = {
        "robotCode": robot_code,
        "openConversationId": open_conversation_id,
        "msgKey": msg_key,
        "msgParam": json.dumps(msg_param, ensure_ascii=False),
    }
    resp = requests.post(
        GROUP_SEND_URL,
        headers={"x-acs-dingtalk-access-token": access_token},
        json=payload,
        timeout=10,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}
    if resp.status_code >= 400 or data.get("code"):
        raise RuntimeError(f"群消息发送失败 HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)}")
    return data


MARKDOWN_MAX = 4500  # 单条 markdown 消息安全上限（钉钉约 5000 字，留缓冲）


def chunk_text(text, size=MARKDOWN_MAX):
    """把长文本按 size 切段（优先在换行处切，避免把链接/列表截断）。"""
    if len(text) <= size:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > size:
        cut = remaining.rfind("\n", 0, size)
        if cut <= size // 2:  # 换行太靠前，硬切
            cut = size
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_oto_report(report_text, title="DD日推", recipient_name=None, user_id=None):
    """把一段报告文本通过企业机器人单聊发给收件人（超长自动分段）。

    返回 (ok, message)。供 dd_main 每日推送复用（不做打印）。
    """
    recipient_name = recipient_name or DINGTALK_RECIPIENT_NAME or "周大卓"
    if not (DINGTALK_APP_KEY and DINGTALK_APP_SECRET):
        return False, "未配置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET"
    try:
        token = get_access_token(DINGTALK_APP_KEY, DINGTALK_APP_SECRET)
    except Exception as exc:
        return False, f"获取 accessToken 失败：{exc}"

    try:
        user_ids, _note = _resolve_user_ids(token, recipient_name, user_id)
    except Exception as exc:
        return False, str(exc)

    chunks = chunk_text(report_text)
    total = len(chunks)
    last_query_key = ""
    for index, part in enumerate(chunks):
        suffix = "" if total == 1 else f"（{index + 1}/{total}）"
        msg_param = {"title": title + suffix, "text": part}
        try:
            result = send_oto(token, DINGTALK_APP_KEY, user_ids, "sampleMarkdown", msg_param)
        except Exception as exc:
            return False, f"第{index + 1}/{total}段发送失败：{exc}"
        last_query_key = result.get("processQueryKey", "")
    message = f"已向 {user_ids} 发送 {total} 段报告"
    if last_query_key:
        message += f"（processQueryKey={last_query_key}）"
    return True, message


def _no_new_card_msg():
    """「今日无新增」actionCard 的 msg_param（单聊/群共用，文案保持一致）。"""
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"✅ 今日无新增分类案例 · {time_str}"
    text = "\n\n".join([
        "🎉 **Onsite Club 案例监控**",
        f"📅 **{time_str}**",
        "✨ **今日没有新增案例**",
    ])
    return {"title": title, "text": text,
            "singleTitle": "查看全部案例", "singleURL": "https://www.onsiteclub.com/category"}


def _case_card_msg_iter(new_cases):
    """把新增案例逐条转成 actionCard msg_param（封面走 IMGBB 转链）。单聊/群共用。"""
    from notifier_dingtalk import build_actioncard_text, upload_actioncard_banner_to_imgbb

    for src_item in new_cases:
        item = dict(src_item)  # 浅拷贝，避免 IMGBB 转链污染源数据
        image_url = item.get("image_url") or ""
        if IMGBB_API_KEY and image_url:
            converted = upload_actioncard_banner_to_imgbb(image_url, IMGBB_API_KEY)
            item["image_url"] = converted if converted else ""
        text = build_actioncard_text(item)
        title = (item.get("title") or "").strip() or "未命名"
        single_url = item.get("url") or "https://www.onsiteclub.com/category"
        yield {"title": title, "text": text, "singleTitle": "查看详情", "singleURL": single_url}


def send_cases_actioncards(new_cases, recipient_name=None, user_id=None):
    """把新增分类案例逐条推成 O2O actionCard 卡片（发到收件人单聊）；无新增时发一张「今日无新增」卡片。

    与旧 Webhook 的 push_new_items_actioncards 对齐：每条 = 一张卡片（封面+标题+介绍+
    「查看详情」按钮），封面走 IMGBB 转链。返回 (ok, message)。供 dd_main 每日推送复用。
    """
    recipient_name = recipient_name or DINGTALK_RECIPIENT_NAME or "周大卓"
    if not (DINGTALK_APP_KEY and DINGTALK_APP_SECRET):
        return False, "未配置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET"
    try:
        token = get_access_token(DINGTALK_APP_KEY, DINGTALK_APP_SECRET)
    except Exception as exc:
        return False, f"获取 accessToken 失败：{exc}"

    try:
        user_ids, _note = _resolve_user_ids(token, recipient_name, user_id)
    except Exception as exc:
        return False, str(exc)

    new_cases = new_cases or []
    if not new_cases:
        try:
            result = send_oto(token, DINGTALK_APP_KEY, user_ids, "sampleActionCard", _no_new_card_msg())
        except Exception as exc:
            return False, f"「今日无新增」卡片发送失败：{exc}"
        return True, f"已发送「今日无新增」卡片（processQueryKey={result.get('processQueryKey', '')}）"

    sent = 0
    for msg_param in _case_card_msg_iter(new_cases):
        try:
            send_oto(token, DINGTALK_APP_KEY, user_ids, "sampleActionCard", msg_param)
        except Exception as exc:
            return False, f"第{sent + 1}张卡片发送失败：{exc}"
        sent += 1
        time.sleep(1)  # 每条间隔 1 秒，避免限流
    return True, f"已发送 {sent} 张 actionCard 卡片"


def send_cases_actioncards_group(new_cases, open_conversation_id):
    """把新增分类案例逐条推成群 actionCard 卡片（发到指定钉钉群）；无新增时发一张「今日无新增」卡片。

    卡片文案与 send_cases_actioncards 完全一致（共用 _no_new_card_msg / _case_card_msg_iter），
    只是目标改为群会话：POST /v1.0/robot/groupMessages/send。
    返回 (ok, message)。供 dd_main 每日推送复用。
    """
    if not (DINGTALK_APP_KEY and DINGTALK_APP_SECRET):
        return False, "未配置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET"
    if not open_conversation_id:
        return False, "未提供群的 openConversationId"
    try:
        token = get_access_token(DINGTALK_APP_KEY, DINGTALK_APP_SECRET)
    except Exception as exc:
        return False, f"获取 accessToken 失败：{exc}"

    new_cases = new_cases or []
    if not new_cases:
        try:
            result = send_group(token, DINGTALK_APP_KEY, open_conversation_id, "sampleActionCard", _no_new_card_msg())
        except Exception as exc:
            return False, f"「今日无新增」群卡片发送失败：{exc}"
        return True, f"已向群发送「今日无新增」卡片（processQueryKey={result.get('processQueryKey', '')}）"

    sent = 0
    for msg_param in _case_card_msg_iter(new_cases):
        try:
            send_group(token, DINGTALK_APP_KEY, open_conversation_id, "sampleActionCard", msg_param)
        except Exception as exc:
            return False, f"第{sent + 1}张群卡片发送失败：{exc}"
        sent += 1
        time.sleep(1)  # 每条间隔 1 秒，避免限流
    return True, f"已向群发送 {sent} 张 actionCard 卡片"


def _hint(result_text):
    lowered = result_text.lower()
    if "robotcode" in lowered or "robot" in lowered:
        print("   提示：应用可能未添加「机器人」能力 / 未发布上线，或缺少「企业内机器人发送消息」权限")
        print("   控制台：open.dingtalk.com → 企业内部应用 → 应用能力加「机器人」→ 权限管理授权 → 版本管理与发布上线")
    if "userids" in lowered:
        print("   提示：userId 可能不正确，可先 --name 用姓名搜索，或用通讯录中的员工 ID")


def main(argv=None):
    parser = argparse.ArgumentParser(description="用企业内部应用机器人给指定员工发单聊消息")
    parser.add_argument("--name", default=DINGTALK_RECIPIENT_NAME or "周大卓", help="收件人姓名（默认 DINGTALK_RECIPIENT_NAME）")
    parser.add_argument("--user-id", help="直接指定 userId，跳过姓名搜索")
    parser.add_argument("--text", default=None, help="消息内容（默认：--file 文件内容或测试文本）")
    parser.add_argument("--title", default="DD日推", help="markdown 标题（仅 --markdown 生效）")
    parser.add_argument("--markdown", action="store_true", help="用 markdown 消息（sampleMarkdown）")
    parser.add_argument("--file", help="从文件读取消息内容（--text 优先于 --file）")
    args = parser.parse_args(argv)

    if not (DINGTALK_APP_KEY and DINGTALK_APP_SECRET):
        print("❌ 请在 .env 配置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET")
        return 1

    try:
        token = get_access_token(DINGTALK_APP_KEY, DINGTALK_APP_SECRET)
    except Exception as exc:
        print(f"❌ 获取 accessToken 失败：{exc}")
        return 1

    # 解析收件人 userId：--user-id 优先 → 配置的 DINGTALK_RECIPIENT_USER_ID → 按姓名搜索
    try:
        user_ids, note = _resolve_user_ids(token, args.name, args.user_id)
    except Exception as exc:
        print(f"❌ {exc}")
        return 1
    print(f"👉 {note}")

    # 解析消息内容：--text 优先，否则 --file 读文件，否则默认测试文本
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as file_handle:
                text = args.text or file_handle.read()
        except OSError as exc:
            print(f"❌ 读取文件失败：{exc}")
            return 1
    else:
        text = args.text or "测试消息：DD日推企业机器人已接通"

    # 组装消息（超长自动分段）
    msg_key = "sampleMarkdown" if args.markdown else "sampleText"
    chunks = chunk_text(text)
    total = len(chunks)
    for index, part in enumerate(chunks):
        if args.markdown:
            suffix = "" if total == 1 else f"（{index + 1}/{total}）"
            msg_param = {"title": args.title + suffix, "text": part}
        else:
            msg_param = {"content": part}
        try:
            result = send_oto(token, DINGTALK_APP_KEY, user_ids, msg_key, msg_param)
        except Exception as exc:
            print(f"❌ 第{index + 1}/{total}段发送失败：{exc}")
            _hint(str(exc))
            return 1
        label = "单聊消息" if total == 1 else f"单聊消息（第{index + 1}/{total}段）"
        print(f"✅ 已通过企业机器人向 {user_ids} 发送{label}")
        if result.get("processQueryKey"):
            print(f"   processQueryKey：{result['processQueryKey']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
