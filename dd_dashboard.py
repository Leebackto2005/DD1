"""DD日推 · Onsite Club 会展看板图片生成器。

输出 1600×900 深色看板 PNG，**只包含两张图**：
- 左侧：活动会展折线图（当月每日进行中会展数，标注峰值）
- 右侧：会展类型饼图（类型分布 + 图例）

复用 dashboard_img 的颜色、字体、折线与饼图原语，避免重复实现。
"""
import os
from datetime import date

import matplotlib.pyplot as plt

from dashboard_img import (
    ACCENT,
    BG,
    FG,
    MUTED,
    _daily_active,
    _panel_title,
    draw_line,
    draw_pie,
    find_chinese_font,
    setup_chinese_font,
)

# 类型简写（与文本报告、饼图标签保持一致）
from dd_report import short_type


def build_dd_dashboard(events, new_events, today=None, output_path=None,
                       month_label="8月", font_path=None):
    """生成看板 PNG，返回输出路径。"""
    today = today or date.today()
    find_chinese_font()
    setup_chinese_font()

    month = today.strftime("%Y-%m")
    year, mon = map(int, month.split("-"))
    first = date(year, mon, 1)
    last = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    days = (last - first).days

    counts = _daily_active(events, first, days)

    type_counts = {}
    for e in events:
        t = short_type(e.get("type") or "其他")
        type_counts[t] = type_counts.get(t, 0) + 1

    # 固定画布 16:9 @110dpi，坐标精确映射，避免 bbox_inches=tight 布局漂移
    fig = plt.figure(figsize=(16, 9), dpi=110)
    fig.patch.set_facecolor(BG)

    # 标题
    fig.text(0.05, 0.955, f"Onsite Club {month_label}会展看板", ha="left", va="center",
             color=FG, fontsize=26, fontweight="bold")
    fig.text(0.95, 0.955, f"{today.strftime('%Y-%m-%d')} · onsiteclub.com/calendar",
             ha="right", va="center", color=MUTED, fontsize=11)
    title_bar = fig.add_axes([0.05, 0.922, 0.90, 0.003])
    title_bar.set_facecolor(ACCENT)
    title_bar.axis("off")

    # 左侧：活动会展折线图
    _panel_title(fig, 0.27, 0.885, "活动会展折线图 · 每日进行中会展数")
    ax_line = fig.add_axes([0.04, 0.08, 0.44, 0.76])
    draw_line(ax_line, counts, month_label, annotate_peak=True)
    # 显示修复：y 轴从 0 起（fill_between 原会把下限拉到负数），并画「当月总数」参照线
    ax_line.set_ylim(bottom=0)
    total = len(events)
    if total > 0:
        ax_line.axhline(total, color=MUTED, ls=":", lw=1.0, alpha=0.7, zorder=2)
        ax_line.text(0.4, total, f"当月总 {total}场", color=MUTED, fontsize=8,
                     va="bottom", ha="left")

    # 右侧：会展类型饼图
    _panel_title(fig, 0.74, 0.885, "会展类型饼图")
    ax_pie = fig.add_axes([0.52, 0.08, 0.44, 0.76])
    draw_pie(ax_pie, type_counts)

    # 底部说明
    fig.text(0.5, 0.03, "自动生成 · 含跨月长期展 · 全部会展见文字报告", ha="center",
             va="center", color=MUTED, fontsize=10)

    output_path = output_path or os.path.join("reports", "dd_dashboard.png")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=110, facecolor=BG)
    plt.close(fig)
    return output_path
