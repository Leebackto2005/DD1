# DD日推 · Onsite Club 会展监控

每日 9:00 自动抓取 [Onsite Club 日历](https://www.onsiteclub.com/calendar) 的当月会展，
识别「今日新增」，生成一段**极简文本** + 一张**看板图片**（活动会展折线图 + 会展类型饼图），
推送到钉钉群，同事直接在群里阅读。另有 9:05 的**分类案例监控**（独立管道，精短文本推送）。

> 📋 所有命令速查见 [COMMANDS.md](COMMANDS.md)。

## 每日流程

```
[每日 9:00]
   ↓
1. 抓取目标网站当月全部会展（含跨月长期展）
   ↓
2. 与本地历史记录对比，识别「今日新增」（首日运行 = 显示全部）
   ↓
3. 生成两个产物：
   ▸ 极简文本（所有会展全部罗列：新增 / 即将结束 / 进行中 / 未来开幕 / 已结束，每个只出现一次）
   ▸ 看板图片（活动会展折线图 + 会展类型饼图）
   ↓
4. 一并推送文本与图片到钉钉（feedCard 卡片「图片在前 + 链接」）
```

## 目录结构

```
DD日推/
├── dd_main.py            # CLI 入口（日历 + 分类监控）
├── dd_monitor.py         # 日历监控主流程（抓取→去重→报告→看板→推送→存状态）
├── category_monitor.py   # 分类案例监控主流程
├── dd_scheduler.py       # 每日 9:00 日历 + 9:05 分类定时任务（APScheduler）
├── dd_report.py          # 日历文本报告（四档分段 + 高频词）
├── dd_dashboard.py       # 看板图片生成（活动会展折线图 + 会展类型饼图）
├── crawlers/             # Onsite Club 日历/分类爬虫
├── notifier_dingtalk.py  # 钉钉推送（markdown + feedCard + 图床上传）
├── dashboard_img.py      # 图表原语（颜色/字体/折线/饼图）
├── onsite_monitor.py     # 状态管理与展示工具（复用）
├── data/                 # 历史状态 json + 中国地图
├── reports/              # 生成的 .md 报告与 .png 看板
└── .env                  # 钉钉 Webhook / 图床配置
```

## 使用

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 .env（钉钉 Webhook、图床 ImgBB Key）
#    复制 .env.example 为 .env 并填写

# 3. 检查配置
python dd_main.py --check-config

# 4. 手动执行一次（不推送，只生成本地产物）
python dd_main.py --once --no-push

# 5. 手动执行一次（推送钉钉）
python dd_main.py --once

# 6. 常驻定时任务（每日 9:00 日历 + 9:05 分类自动执行）
python dd_scheduler.py

# 7. 分类案例监控（独立管道）
python dd_main.py --category                      # 执行一次并推送
python dd_main.py --category --no-push            # 只生成本地
python dd_main.py --init-category <slug>          # 初始化分类基准（一次性）
```

Windows 下可双击 `启动DD日推调度.bat` 启动定时任务，或 `手动运行一次.bat` 立即执行一次。
完整命令见 [COMMANDS.md](COMMANDS.md)。

## 状态说明

历史状态存于 `data/onsiteclub_calendar_state.json`：
- `seen_ids`：历史出现过的会展 id，用于识别「今日新增」
- `cache`：会展详情缓存（城市/品牌/类型/封面等），避免重复抓详情页
- `history`：每日新增记录

**首次运行会显示当月全部会展**（视为「新增」）；之后只显示新出现的会展。
若想重新从首日开始（全部重显），删除 `data/onsiteclub_calendar_state.json` 即可。

## 关键配置（.env）

| 变量 | 说明 |
| --- | --- |
| `DINGTALK_WEBHOOK_URL` | 钉钉群机器人 Webhook |
| `DINGTALK_KEYWORD` | 机器人安全关键词（默认 `会展`，消息必须包含） |
| `DINGTALK_SECRET` | 加签密钥（开启加签时填写） |
| `IMGBB_API_KEY` | 看板图床 ImgBB（免费注册 api.imgbb.com） |
| `GITHUB_TOKEN` / `GITHUB_IMAGE_REPO` | 备选图床（GitHub raw） |
| `ONSITECLUB_SCHEDULE_DAYS` | 日程窗口天数（1-30，默认 7） |
