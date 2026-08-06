# DD日推 · Onsite Club 会展监控

每日 9:00 自动抓取 [Onsite Club 日历](https://www.onsiteclub.com/calendar) 的当月会展与
[/category](https://www.onsiteclub.com/category) 分类案例，识别「今日新增」，
生成本地报告/看板/H5。钉钉推送**把分类案例动态逐条推成 actionCard 卡片**（企业机器人发到
`DINGTALK_GROUP_NAME` 群：每条新增一张卡片，含封面+介绍+「查看详情」按钮；无新增发
「今日无新增」卡片）；未配群 ID 时回退给 `DINGTALK_RECIPIENT_NAME` 发单聊，未配置企业
应用时回退群 Webhook 推送。另有 9:05 的**分类案例监控**。

> 📋 所有命令速查见 [COMMANDS.md](COMMANDS.md)。

## 每日流程

```
[每日 9:00]
   ↓
1. 抓取目标网站当月全部会展（含跨月长期展）+ /category 分类案例
   ↓
2. 与本地历史记录对比，识别「今日新增」（首日运行 = 显示全部）
   ↓
3. 生成产物：
   ▸ 极简文本报告（.md）：新增 / 即将结束 / 进行中 / 未来开幕 / 已结束
   ▸ 看板图片（.png）：活动会展折线图 + 会展类型饼图
   ▸ H5 详情页（.html，有新增时才生成）：Serif 设计系统排版
   ↓
4. 推送钉钉（默认企业机器人群推送：分类案例**逐条 actionCard 卡片**，无新增发「今日无新增」卡片）
   ▸ 日历报告仅本地归档，不推送
   ▸ 未配群 ID 时回退企业机器人单聊；未配置企业应用凭证时回退群 Webhook：逐条 actionCard / --digest 合并报告
   ▸ actionCard「查看详情」跳转 H5 详情页（GitHub + jsDelivr CDN 公网托管）
   ▸ 超过 20 条时溢出合并为一条 markdown 汇总
```

## 推送模式

| 模式 | 命令 | 说明 |
| --- | --- | --- |
| **企业机器人群推送**（默认） | `python dd_main.py --once` | 配置了 `DINGTALK_APP_KEY`/`APP_SECRET` + `DINGTALK_GROUP_ID` 时，每日把**分类案例新增**逐条推成 actionCard 卡片到群 `DINGTALK_GROUP_NAME`（封面+介绍+查看详情，无新增发「今日无新增」卡片；`--digest`/actionCard 均被忽略） |
| **企业机器人单聊**（回退） | `python dd_main.py --once` | 配置了企业凭证但未配群 ID 时，发给 `DINGTALK_RECIPIENT_NAME` 单聊（旧行为） |
| **逐条 actionCard**（回退） | `python dd_main.py --once` | 未配置企业应用时，走群 Webhook 逐条推送卡片，含封面图+标题+介绍+「查看详情」按钮 |
| **合并报告** | `python dd_main.py --once --digest` | 群 Webhook 合并成一条 markdown 消息，含看板图 |
| **只生成本地** | `python dd_main.py --once --no-push` | 不推送钉钉，只生成报告/看板/H5 本地文件 |

群 Webhook 路径下 actionCard 每条间隔 1 秒推送（避免钉钉限流），超过 20 条时溢出部分合并为一条 markdown 汇总。

## 给个人发单聊消息（企业机器人 O2O）

企业应用机器人在群机器人 Webhook 之外，还能直接给某位员工发**单聊私信**（消息出现在该员工与机器人的单聊里，不是工作通知）。

```bash
python send_dingtalk_user.py                        # 默认发给 .env 的 DINGTALK_RECIPIENT_NAME（周大卓）
python send_dingtalk_user.py --name 周大卓 --text "你好"
python send_dingtalk_user.py --user-id <userId> --text "你好"        # 绕过姓名搜索
python send_dingtalk_user.py --markdown --title "标题" --text "正文"
```

流程：v1.0 换 access_token → 按姓名搜索通讯录拿到 userId → `robot/oToMessages/batchSend` 发送。

**前提**（一次性，钉钉开发者后台 open.dingtalk.com）：
- 应用添加「机器人」能力；
- 授权「企业内机器人发送消息」权限；
- 版本管理发布上线；
- 按姓名搜索需「成员信息读权限」（本应用已具备）。

**配置**（.env）：`DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET`（企业内部应用凭证，AppSecret 不带 SEC 前缀）、`DINGTALK_RECIPIENT_NAME`（默认收件人）。

**每日推送**：配置好企业凭证 + `DINGTALK_GROUP_ID` 后，`dd_main.py --once`（含每日定时任务）每日把**分类案例新增**逐条推成 actionCard 卡片到群 `DINGTALK_GROUP_NAME`（未配群 ID 回退收件人单聊；无新增发「今日无新增」卡片），不发日历报告、不走群 Webhook；`--file` 可发任意文件内容（超长自动分段）。

**群 ID 获取**：企业机器人往群里推消息需要群的 `openConversationId`，没有按群名查的接口。用 `dd_capture_group.py` 一次性捕获——它连上应用机器人 Stream 长连接后，你到群里发一条消息（建议 @机器人），脚本自动把群 `openConversationId` 写入 `data/dingtalk_group_id.json`，按提示填进 `.env` 的 `DINGTALK_GROUP_ID` 即可（前提：应用机器人「消息接收模式」为 Stream 长连接）。

> 说明：旧的自定义机器人 Webhook 方式（`DINGTALK_WEBHOOK_URL`）与这套企业机器人互不影响，各走各的。旧的 `topapi/user/search` 等按姓名搜索接口已下线，姓名→userId 走 v1.0 `contact/users/search`。

## H5 详情页（Serif 设计系统）

有新增会展时，自动生成一个**自包含的 H5 报告页**并上传到 GitHub 仓库，
通过 jsDelivr CDN 公网访问，作为 actionCard「查看详情」的落地页。

**设计风格**：Serif 编辑风 —— Playfair Display 衬线标题 + Source Sans 3 正文 + IBM Plex Mono 小标，
象牙白底 + 金色强调 + 细线分隔 + 纸质纹理 + 环境光晕，响应式适配钉钉移动端/PC端。

**托管原理**：
```
HTML 上传 GitHub 仓库 h5/ 目录
    ↓
jsDelivr CDN 自动镜像
    ↓
actionCard 按钮跳转 https://cdn.jsdelivr.net/gh/{repo}@main/h5/{date}.html#case-{i}
```

未配置 GitHub 时，actionCard 回退到 onsiteclub.com 原详情页 URL。

## 目录结构

```
DD日推/
├── dd_main.py            # CLI 入口（日历 + 分类监控）
├── dd_monitor.py         # 日历监控主流程（抓取→去重→报告→看板→H5→推送→存状态）
├── category_monitor.py   # 分类案例监控主流程
├── dd_scheduler.py       # 每日 9:00 日历 + 9:05 分类定时任务（APScheduler）
├── dd_report.py          # 日历文本报告（四档分段 + 高频词）
├── dd_dashboard.py       # 看板图片生成（活动会展折线图 + 会展类型饼图）
├── dd_h5_report.py       # H5 详情页生成（Serif 设计系统）
├── crawlers/             # Onsite Club 日历/分类爬虫
├── notifier_dingtalk.py  # 钉钉群推送（webhook actionCard + markdown + 图床上传 + H5 上传）
├── send_dingtalk_user.py # 企业机器人发送（单聊/群 actionCard 卡片，--file 支持超长自动分段）
├── dd_capture_group.py    # 一次性工具：捕获机器人所在群的 openConversationId（Stream 长连接）
├── dashboard_img.py      # 图表原语（颜色/字体/折线/饼图）
├── onsite_monitor.py     # 状态管理与展示工具（复用）
├── data/                 # 历史状态 json + 中国地图
├── reports/              # 生成的 .md 报告 / .png 看板 / .html H5
└── .env                  # 钉钉 Webhook / 企业应用凭证 / 图床 / GitHub 配置
```

## 使用

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 .env（钉钉 Webhook、图床 ImgBB Key、GitHub Token/Repo）
#    复制 .env.example 为 .env 并填写

# 3. 检查配置
python dd_main.py --check-config

# 4. 手动执行一次（不推送，只生成本地产物，含 H5）
python dd_main.py --once --no-push

# 5. 手动执行一次（默认逐条 actionCard 推送钉钉）
python dd_main.py --once

# 6. 手动执行一次（合并报告模式，恢复旧的 markdown 汇总推送）
python dd_main.py --once --digest

# 7. 常驻定时任务（每日 9:00 日历 + 9:05 分类自动执行）
python dd_scheduler.py

# 8. 分类案例监控（独立管道）
python dd_main.py --category                      # 执行一次并推送
python dd_main.py --category --no-push            # 只生成本地
python dd_main.py --init-category <slug>          # 初始化分类基准（一次性）

# 9. 用企业机器人给个人发单聊消息（详见上方「给个人发单聊消息」）
python send_dingtalk_user.py                      # 默认发给 DINGTALK_RECIPIENT_NAME
python send_dingtalk_user.py --markdown --title "今日报告" --file reports/dd_report_2026-08.md  # 发报告（超长自动分段）
```

Windows 下可双击 `启动DD日推调度.bat` 启动定时任务，或 `手动运行一次.bat` 立即执行一次。
完整命令见 [COMMANDS.md](COMMANDS.md)。

## 状态说明

历史状态存于 `data/onsiteclub_calendar_state.json`：
- `seen_ids`：历史出现过的会展 id，用于识别「今日新增」
- `cache`：会展详情缓存（城市/品牌/类型/封面/介绍等），避免重复抓详情页
- `history`：每日新增记录

分类案例状态存于 `data/onsiteclub_category_state.json`，字段结构同上（id 用 slug）。

**首次运行会显示当月全部会展**（视为「新增」）；之后只显示新出现的会展。
若想重新从首日开始（全部重显），删除对应的 state.json 即可。

## 日志与排障

运行 `python dd_main.py --once --no-push` 后，通过日志可快速判断系统是否正常工作。

### 三种典型日志对照

| 日志行 | 没抓到数据 | 抓到无新增 | 有新增 |
| :--- | :--- | :--- | :--- |
| `分类案例抓取 X 条` | **0 条** + `WARNING` | 36 条 | 36 条 |
| `diff_new_cases：抓取 X 条` | **不出现** | 出现 | 出现 |
| `本次新增 X 条` | **不出现** | 0 条 | ≥1 条 |

### 如何区分「逻辑正确无新增」vs「没抓到数据」

**情况 A：没抓到数据（爬虫失败/网站改版/网络异常）**
```
[DD日推] 分类案例抓取 0 条
[DD日推] 分类案例抓取 0 条，可能是爬虫失败/网站改版/网络异常；本次跳过新增判定
```
→ 没有 `[分类监控] diff_new_cases:` 日志（`if cases:` 不进入）
→ 需要检查网络 / 网站结构 / 爬虫选择器

**情况 B：抓到数据但无新增（逻辑正确，确实没新案例）**
```
[DD日推] 分类案例抓取 36 条
[分类监控] diff_new_cases：抓取 36 条，seen_ids 共 36 条
[分类监控]   已见过 slug=ASICS-MINGTANG-STREET-POPUP-CD-2026-7-24
[分类监控]   已见过 slug=CHANEL-COCO-CRUSH-HOTEL-EXPERIENCE-SPACE-SH-2026-7-28
...
[分类监控] 本次新增 0 条
[DD日推] 分类案例今日新增 0 条
```
→ 有 `[分类监控] diff_new_cases:` 日志，逐条打印了每条 slug 的判定结果
→ 逻辑正常，确实没新案例

**情况 C：抓到数据且有新增**
```
[DD日推] 分类案例抓取 36 条
[分类监控] diff_new_cases：抓取 36 条，seen_ids 共 36 条
[分类监控]   新增 slug=XXX-NEW-CASE-2026-8-5
[分类监控]   已见过 slug=ASICS-MINGTANG-STREET-POPUP-CD-2026-7-24
...
[分类监控] 本次新增 1 条
[DD日推] 分类案例今日新增 1 条
```
→ 日历监控的 `diff_new_events` 也有同样结构的日志（打印 id + 标题）

### 排障检查清单

| 现象 | 排查方向 |
| :--- | :--- |
| 抓取 0 条 | 网络/网站改版/爬虫选择器失效 |
| 抓到但无新增（日复一日） | 检查 `data/onsiteclub_*_state.json` 的 `seen_ids` 是否覆盖了所有抓到的 slug |
| 新增判定疑似漏判 | 看逐条日志里该 slug 是否被标为「已见过」（可能在历史 seen_ids 里） |
| description 字段为空 | 爬虫未抓到正文（可能详情页结构变化），检查 `_extract_body_description` 选择器 |
| 图片显示裂图 | IMGBB 上传失败，回退原 URL 但钉钉加载不了，检查 IMGBB_API_KEY |
| H5 上传失败 | 检查 GITHUB_TOKEN 权限（需 repo）+ 仓库是否 Public |

## 图片处理

Onsiteclub.com 的图片直链在钉钉手机端有显示 bug（防盗链/SSL），
通过 IMGBB API 转存为公网直链：

1. **优先**：让 IMGBB 服务器抓取远程图片
2. **Fallback**：远程抓取失败（400）时，本地下载（带 Referer 绕防盗链）→ base64 上传 IMGBB
3. **兜底**：IMGBB 全部失败时用原 URL

看板图片同样走 IMGBB 上传，失败可回退 GitHub raw。

## 关键配置（.env）

| 变量 | 说明 |
| --- | --- |
| `DINGTALK_WEBHOOK_URL` | 钉钉群机器人 Webhook |
| `DINGTALK_KEYWORD` | 机器人安全关键词（默认 `会展`，消息必须包含） |
| `DINGTALK_SECRET` | 加签密钥（SEC 开头，开启加签时填写） |
| `DINGTALK_APP_KEY` | 企业内部应用 AppKey（企业机器人单聊用，open.dingtalk.com 获取） |
| `DINGTALK_APP_SECRET` | 企业内部应用 AppSecret（原样填写，不带 SEC 前缀） |
| `DINGTALK_RECIPIENT_NAME` | 单聊默认收件人姓名（`send_dingtalk_user.py` 的 `--name` 默认值） |
| `DINGTALK_RECIPIENT_USER_ID` | 收件人 userId（直连，跳过按姓名搜索；首次运行打印后填入即可永久固定） |
| `DINGTALK_GROUP_ID` | 群推送目标：机器人所在群的 openConversationId（`dd_capture_group.py` 捕获） |
| `DINGTALK_GROUP_NAME` | 群推送目标群名（仅展示用，便于提示确认） |
| `IMGBB_API_KEY` | 图床 ImgBB API Key（免费注册 api.imgbb.com） |
| `GITHUB_TOKEN` | GitHub Personal Access Token（勾选 repo 权限，用于上传 H5） |
| `GITHUB_IMAGE_REPO` | GitHub 仓库（`owner/repo` 格式，需 Public，jsDelivr 才能公网访问） |
| `JSDELIVR_URL_TEMPLATE` | jsDelivr CDN URL 模板（一般不用改） |
| `ONSITECLUB_SCHEDULE_DAYS` | 日程窗口天数（1-30，默认 7） |
| `ONSITECLUB_END_URGENT` / `_NEAR` / `_FAR` | 进行中会展按距结束天数分档（默认 10/20/30） |
| `ONSITECLUB_LONG_CAP` | 长期进行中每月展示上限（默认 10） |
