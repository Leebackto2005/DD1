import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SCHEDULE_HOURS = [8, 20]


def parse_bool(raw, default=False):
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def parse_int(raw, default, minimum=None, maximum=None):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default

    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def parse_schedule_hours(raw, default=None):
    """Parse comma-separated hours and keep only values in 0-23."""
    fallback = list(default or DEFAULT_SCHEDULE_HOURS)
    hours = []

    for part in str(raw or "").split(","):
        value = part.strip()
        if not value:
            continue
        try:
            hour = int(value)
        except ValueError:
            continue

        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)

    return hours or fallback


def parse_keywords(raw):
    return [part.strip() for part in str(raw or "").replace("，", ",").split(",") if part.strip()]


def parse_filter_method(raw, default="keyword"):
    """解析 AI 筛选方式：仅接受 keyword|ai，非法值回退默认。"""
    value = str(raw or "").strip().lower()
    return value if value in {"keyword", "ai"} else default


def resolve_api_key(ai_key, deepseek_key):
    """AI_API_KEY 优先，为空回退 DEEPSEEK_API_KEY。"""
    return (ai_key or "").strip() or (deepseek_key or "").strip()


def load_interests(raw, path="ai_interests.txt"):
    """兴趣描述：env 非空用 env，否则读根目录 ai_interests.txt，文件不存在返回空串。"""
    text = (raw or "").strip()
    if text:
        return text
    try:
        with open(path, encoding="utf-8") as file:
            return file.read().strip()
    except OSError:
        return ""


PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
AI_API_KEY = resolve_api_key(os.getenv("AI_API_KEY", ""), DEEPSEEK_API_KEY)
AI_API_BASE = (os.getenv("AI_API_BASE", "https://api.deepseek.com/v1").strip() or "https://api.deepseek.com/v1")
AI_MODEL = (os.getenv("AI_MODEL", "deepseek-chat").strip() or "deepseek-chat")
AI_FILTER_METHOD = parse_filter_method(os.getenv("AI_FILTER_METHOD", "keyword"))
AI_INTERESTS = load_interests(os.getenv("AI_INTERESTS", ""))
AI_FILTER_MIN_SCORE = parse_int(os.getenv("AI_FILTER_MIN_SCORE"), 6, minimum=1, maximum=10)
CITY = os.getenv("CITY", "眉山").strip()
ZODIAC_SIGN = os.getenv("ZODIAC_SIGN", "天秤座").strip()

SCHEDULE_HOURS = parse_schedule_hours(os.getenv("SCHEDULE_HOURS", "8,20"))
ENABLE_PUSH = parse_bool(os.getenv("ENABLE_PUSH"), True)
ENABLE_AI = parse_bool(os.getenv("ENABLE_AI"), True)
TOP_N = parse_int(os.getenv("TOP_N"), 20, minimum=1, maximum=50)
REQUEST_TIMEOUT = parse_int(os.getenv("REQUEST_TIMEOUT"), 10, minimum=3, maximum=60)

REPORT_DIR = os.getenv("REPORT_DIR", "reports").strip() or "reports"
DATA_DIR = os.getenv("DATA_DIR", "data").strip() or "data"
LOG_DIR = os.getenv("LOG_DIR", "logs").strip() or "logs"
WATCH_KEYWORDS = parse_keywords(os.getenv("WATCH_KEYWORDS", ""))
BLOCK_KEYWORDS = parse_keywords(os.getenv("BLOCK_KEYWORDS", ""))

# ---- Onsite Club 会展监控推送 ----
# 钉钉群机器人 webhook（自定义机器人 → 安全设置 → 复制 Webhook 地址）
DINGTALK_WEBHOOK_URL = os.getenv("DINGTALK_WEBHOOK_URL", "").strip()
# 加签密钥（机器人安全设置开启「加签」时填写，不开启留空）
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "").strip()
# 机器人安全设置里的「自定义关键词」；所有推送消息必须包含该词，feedCard 卡片标题会自动加前缀
DINGTALK_KEYWORD = (os.getenv("DINGTALK_KEYWORD", "会展").strip() or "会展")
# 看板图片图床：ImgBB API Key（https://api.imgbb.com 免费注册）
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "").strip()
# 看板图片图床（备选）：GitHub Token + 仓库（owner/repo），传 raw 直链
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_IMAGE_REPO = os.getenv("GITHUB_IMAGE_REPO", "").strip()
# jsDelivr CDN URL 模板：H5 报告页上传 GitHub 后通过此模板生成公网访问 URL
JSDELIVR_URL_TEMPLATE = os.getenv("JSDELIVR_URL_TEMPLATE", "https://cdn.jsdelivr.net/gh/{repo}@main/h5/{date}.html").strip()
# 监控月份：默认当前月（自动滚动）；指定如 2026-08 可固定
ONSITECLUB_MONTH = os.getenv("ONSITECLUB_MONTH", "").strip()
# 站点证书过期/无效时是否允许自动降级为不校验 SSL（true=降级重试，false=严格校验报错）
ONSITECLUB_ALLOW_INSECURE_SSL = parse_bool(os.getenv("ONSITECLUB_ALLOW_INSECURE_SSL"), True)
# 推送里「未来N天新开」日程窗口天数（1-30，默认 7）
ONSITECLUB_SCHEDULE_DAYS = parse_int(os.getenv("ONSITECLUB_SCHEDULE_DAYS"), 7, minimum=1, maximum=30)
# 进行中会展按「距结束天数」分档的阈值：剩END_URGENT天内 / 剩END_NEAR天内 / 剩END_FAR天内 / 更长期
ONSITECLUB_END_URGENT = parse_int(os.getenv("ONSITECLUB_END_URGENT"), 10, minimum=1, maximum=60)
ONSITECLUB_END_NEAR = parse_int(os.getenv("ONSITECLUB_END_NEAR"), 20, minimum=1, maximum=90)
ONSITECLUB_END_FAR = parse_int(os.getenv("ONSITECLUB_END_FAR"), 30, minimum=1, maximum=180)
# 长期进行中段（>END_FAR天，按结束月份细分）每个月份展示上限
ONSITECLUB_LONG_CAP = parse_int(os.getenv("ONSITECLUB_LONG_CAP"), 10, minimum=1, maximum=50)

# ---- Onsite Club 会展查询机器人（钉钉企业内部应用 + Stream 长连接） ----
# 钉钉开放平台「企业内部应用」的凭证（open.dingtalk.com 创建后获取）
DINGTALK_APP_KEY = os.getenv("DINGTALK_APP_KEY", "").strip()
DINGTALK_APP_SECRET = os.getenv("DINGTALK_APP_SECRET", "").strip()
# 个人单聊默认收件人姓名（send_dingtalk_user.py 用）
DINGTALK_RECIPIENT_NAME = os.getenv("DINGTALK_RECIPIENT_NAME", "").strip()
# 收件人 userId（直连，跳过按姓名搜索；通讯录用户ID里可查）
DINGTALK_RECIPIENT_USER_ID = os.getenv("DINGTALK_RECIPIENT_USER_ID", "").strip()
# 群推送目标：机器人已加入的钉钉群的 openConversationId（由 dd_capture_group.py 捕获）
DINGTALK_GROUP_ID = os.getenv("DINGTALK_GROUP_ID", "").strip()
DINGTALK_GROUP_NAME = os.getenv("DINGTALK_GROUP_NAME", "").strip()
# 机器人名称：群聊时识别并剥离 @ 前缀
DINGTALK_BOT_NAME = (os.getenv("DINGTALK_BOT_NAME", "").strip() or "会展查询")
# 机器人回答最多展示的会展条数（1-30）
ONSITECLUB_QUERY_MAX = parse_int(os.getenv("ONSITECLUB_QUERY_MAX"), 15, minimum=1, maximum=30)
