# DD日推 · 指令速查

所有命令在项目根目录 `F:\claude 工作集\DD日推` 下运行。

## 📦 安装

```bash
pip install -r requirements.txt          # 安装依赖
```

## 📅 日历会展监控（`dd_main.py`）

```bash
python dd_main.py --once                        # 执行一次日历监控 + 推送钉钉
python dd_main.py --once --no-push              # 只生成本地报告/看板，不推送
python dd_main.py --once --month 2026-08        # 指定月份（默认当前月）
python dd_main.py --check-config                # 检查钉钉 Webhook / 图床配置是否就绪
```

## 🏷️ 分类案例监控（`dd_main.py --category`，独立管道）

```bash
python dd_main.py --category                        # 执行一次分类案例监控 + 推送钉钉（精短文本）
python dd_main.py --category --no-push              # 分类监控只生成本地
python dd_main.py --category --max-pages 5          # 抓取前 5 页（默认 3）
python dd_main.py --init-category <slug>            # 一次性初始化基准：把该案例及更旧的标记为已见过
                                                    # （之后只报告比它更新的为「新增」）
```

## ⏰ 每日定时调度（`dd_scheduler.py`）

```bash
python dd_scheduler.py              # 常驻后台：每日 9:00 日历监控 + 9:05 分类监控（自动推送）
python dd_scheduler.py --once       # 立即执行全部（日历 + 分类）
python dd_scheduler.py --once --category   # 立即只跑分类
```

## 🖥️ Windows 一键启动

- `启动DD日推调度.bat` → 双击常驻调度（每日 9:00 + 9:05 自动推送钉钉）
- `手动运行一次.bat` → 双击立即执行一次日历监控并推送

## 🗂️ 状态与产物

```text
# 状态文件（删掉可重置）
data\onsiteclub_calendar_state.json    # 日历：删除后下次运行显示「当月全部」= 首日效果
data\onsiteclub_category_state.json    # 分类：删除后下次运行全部视为新增

# 产物
reports\dd_report_2026-08.md           # 日历文本报告
reports\dd_dashboard_2026-08.png       # 日历看板图
logs\                                  # 运行日志
```

## 🔧 常用配置（`.env`）

```bash
DINGTALK_WEBHOOK_URL          # 钉钉机器人 webhook
DINGTALK_KEYWORD=会展          # 机器人安全关键词
IMGBB_API_KEY                 # 看板图床
ONSITECLUB_SCHEDULE_DAYS=7    # 未来N天开幕窗口
ONSITECLUB_END_URGENT=10      # 四档分段阈值：≤10天 / ≤20天 / ≤30天 / >30天
ONSITECLUB_END_NEAR=20
ONSITECLUB_END_FAR=30
ONSITECLUB_LONG_CAP=10        # 长期段每月展示上限
```

## 📁 代码结构

```text
dd_main.py           # CLI 入口（日历 + 分类）
dd_monitor.py        # 日历监控主流程
category_monitor.py  # 分类案例监控主流程
dd_report.py         # 日历文本报告（四档分段 + 高频词）
dd_dashboard.py      # 看板图片生成（折线图 + 饼图）
dashboard_img.py     # 图表绘制原语（颜色/字体/折线/饼图）
notifier_dingtalk.py # 钉钉推送（markdown + feedCard + 图床上传）
crawlers/            # onsiteclub 日历/分类爬虫
config.py            # 环境配置
```
