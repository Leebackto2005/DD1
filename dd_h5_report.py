"""DD日推 · Serif 设计系统 H5 报告页生成器。

每日生成一个自包含 HTML 报告页，完整落地 Serif 设计系统 DNA：
- 颜色 token：象牙白背景 / 深色文字 / 金色 accent / warm gray 细线
- 字体：Playfair Display（标题）/ Source Sans 3（正文）/ IBM Plex Mono（小标）
- 留白与细线系统：section padding 4rem 0，article padding 2.5rem 0，1px #E8E4DF 分隔
- 响应式：PC max-width 768px 居中，移动端 < 768px 字号缩减、单列
"""
import html
from datetime import date


# Serif 设计系统 token
COLOR_BACKGROUND = "#FAFAF8"
COLOR_FOREGROUND = "#1A1A1A"
COLOR_MUTED = "#F5F3F0"
COLOR_MUTED_FOREGROUND = "#6B6B6B"
COLOR_ACCENT = "#B8860B"
COLOR_ACCENT_SECONDARY = "#D4A84B"
COLOR_BORDER = "#E8E4DF"
COLOR_CARD = "#FFFFFF"

GOOGLE_FONTS_LINK = (
    '<link href="https://fonts.googleapis.com/css2'
    '?family=Playfair+Display:wght@400;600'
    '&family=Source+Sans+3:wght@400;600'
    '&family=IBM+Plex+Mono:wght@500&display=swap" rel="stylesheet">'
)

# Serif 设计系统 Bold Factor #10：纸质噪点纹理（inline SVG，保持单文件自包含）
# feTurbulence 生成细密噪点，30% 透明度叠加，营造印刷质感
NOISE_TEXTURE_DATA_URI = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E"
    "%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E"
    "%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E"
)

DATA_SOURCE = "onsiteclub.com"


def _format_date_span(item):
    """格式化日期区间：MM-DD 或 MM-DD~MM-DD；start==end 只显示单日。

    兼容日历会展的 start_date/end_date 与分类案例的 start/end 两种键名。
    """
    start = str(item.get("start_date") or item.get("start") or "").strip()
    end = str(item.get("end_date") or item.get("end") or "").strip()

    def _mmdd(d):
        # YYYY-MM-DD 取后 5 位（MM-DD）；不足 10 位则原样返回
        return d[5:] if len(d) >= 10 else d

    if not start and not end:
        return ""
    if start and end and start != end:
        return f"{_mmdd(start)}~{_mmdd(end)}"
    # 只有一端或两端相同：显示单日
    return _mmdd(start or end)


def _build_meta_line(item):
    """拼接非空字段：城市 · 品牌 · 日期。品牌为"待定"或空则跳过。"""
    parts = []
    city = (item.get("city") or "").strip()
    if city:
        parts.append(html.escape(city))
    brand = (item.get("brand") or "").strip()
    if brand and brand != "待定":
        parts.append(html.escape(brand))
    span = _format_date_span(item)
    if span:
        parts.append(html.escape(span))
    return " · ".join(parts)


def _build_css():
    """内联 CSS：Serif 设计系统 + 响应式。"""
    return f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{
  background: {COLOR_BACKGROUND};
  color: {COLOR_FOREGROUND};
  font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 16px;
  line-height: 1.75;
  font-weight: 400;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  position: relative;
  overflow-x: hidden;
}}
/* Serif Bold Factor #11：环境光晕——顶部居中的金色径向渐变，2% 透明度营造温暖氛围深度 */
body::before {{
  content: '';
  position: fixed;
  top: -20vh;
  left: 50%;
  transform: translateX(-50%);
  width: 90vw;
  height: 70vh;
  background: radial-gradient(ellipse at center, {COLOR_ACCENT} 0%, transparent 70%);
  opacity: 0.04;
  pointer-events: none;
  z-index: 0;
}}
/* Serif Bold Factor #10：纸质纹理——inline SVG 噪点叠加，营造印刷品般的触感 */
body::after {{
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("{NOISE_TEXTURE_DATA_URI}");
  background-repeat: repeat;
  opacity: 0.03;
  pointer-events: none;
  z-index: 0;
  mix-blend-mode: multiply;
}}
.container {{
  max-width: 768px;
  margin: 0 auto;
  padding: 0 1.5rem;
  position: relative;
  z-index: 1;
}}

/* 页头 */
.site-header {{ padding: 4rem 0 2.5rem; text-align: center; }}
.site-header .eyebrow {{
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: {COLOR_MUTED_FOREGROUND};
  margin-bottom: 1rem;
}}
.site-header h1 {{
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 2.5rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: {COLOR_FOREGROUND};
  line-height: 1.2;
  margin-bottom: 0.75rem;
}}
.site-header .date-stamp {{
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: {COLOR_MUTED_FOREGROUND};
  margin-top: 0.5rem;
}}
.site-header .gold-rule {{
  width: 64px;
  height: 2px;
  background: {COLOR_ACCENT};
  margin: 1.5rem auto 0;
  border: 0;
}}

/* 统计带 */
.stats-band {{ padding: 2.5rem 0 3rem; text-align: center; }}
.stats-band .stat-number {{
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 3.5rem;
  font-weight: 600;
  color: {COLOR_ACCENT};
  line-height: 1;
  letter-spacing: -0.02em;
}}
.stats-band .stat-label {{
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: {COLOR_MUTED_FOREGROUND};
  margin-top: 0.75rem;
}}

/* 资讯列表 */
.report-main {{ padding: 1rem 0 4rem; }}
.report-main section {{ padding: 0; }}

article.case {{
  padding: 2.5rem 0;
  border-top: 1px solid {COLOR_BORDER};
}}
article.case:first-child {{ border-top: 2px solid {COLOR_ACCENT}; }}
article.case .cover {{
  width: 100%;
  display: block;
  margin-bottom: 1.5rem;
  border-top: 2px solid {COLOR_ACCENT};
}}
article.case h2 {{
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 1.75rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: {COLOR_FOREGROUND};
  line-height: 1.3;
  margin-bottom: 1rem;
}}
article.case .description {{
  font-family: 'Source Sans 3', sans-serif;
  font-size: 1rem;
  line-height: 1.75;
  color: {COLOR_MUTED_FOREGROUND};
  margin-bottom: 1.25rem;
}}
article.case .meta {{
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: {COLOR_MUTED_FOREGROUND};
  margin-bottom: 1.5rem;
  word-break: break-word;
}}
article.case .btn-original {{
  display: inline-block;
  height: 44px;
  line-height: 44px;
  padding: 0 1.5rem;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: {COLOR_FOREGROUND};
  background: transparent;
  border: 1px solid {COLOR_FOREGROUND};
  text-decoration: none;
  transition: color 0.2s ease, border-color 0.2s ease;
}}
article.case .btn-original:hover {{
  color: {COLOR_ACCENT};
  border-color: {COLOR_ACCENT};
}}

/* 空状态 */
.empty-state {{
  padding: 5rem 0;
  text-align: center;
}}
.empty-state p {{
  font-family: 'Playfair Display', Georgia, serif;
  font-style: italic;
  font-size: 1.5rem;
  font-weight: 400;
  color: {COLOR_MUTED_FOREGROUND};
  letter-spacing: -0.01em;
}}

/* 页脚 */
.site-footer {{
  padding: 3rem 0 4rem;
  border-top: 1px solid {COLOR_BORDER};
  text-align: center;
}}
.site-footer .footer-meta {{
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: {COLOR_MUTED_FOREGROUND};
  line-height: 1.8;
}}
.site-footer .footer-meta a {{
  color: {COLOR_MUTED_FOREGROUND};
  text-decoration: none;
  border-bottom: 1px solid {COLOR_BORDER};
  transition: color 0.2s ease, border-color 0.2s ease;
}}
.site-footer .footer-meta a:hover {{
  color: {COLOR_ACCENT};
  border-color: {COLOR_ACCENT};
}}

/* 响应式：移动端 < 768px */
@media (max-width: 767px) {{
  body {{ font-size: 15px; }}
  .container {{ padding: 0 1.25rem; }}
  .site-header {{ padding: 3rem 0 2rem; }}
  .site-header h1 {{ font-size: 1.75rem; }}
  .stats-band {{ padding: 2rem 0 2.5rem; }}
  .stats-band .stat-number {{ font-size: 2.5rem; }}
  .report-main {{ padding: 0.5rem 0 3rem; }}
  article.case {{ padding: 1.75rem 0; }}
  article.case h2 {{ font-size: 1.4rem; }}
  article.case .btn-original {{
    display: block;
    width: 100%;
    text-align: center;
  }}
  .empty-state {{ padding: 4rem 0; }}
  .empty-state p {{ font-size: 1.25rem; }}
  .site-footer {{ padding: 2.5rem 0 3rem; }}
}}
"""


def _build_header(today):
    """页头：站点小标 + 大标题 + 日期小标 + 金色细线。"""
    weekday_cn = "一二三四五六日"
    date_text = f"{today.year}-{today.month:02d}-{today.day:02d} 周{weekday_cn[today.weekday()]}"
    return f"""
    <header class="site-header">
      <div class="container">
        <div class="eyebrow">Onsite Club · Daily Digest</div>
        <h1>会展日推</h1>
        <div class="date-stamp">{html.escape(date_text)}</div>
        <hr class="gold-rule">
      </div>
    </header>
    """


def _build_stats_band(count):
    """统计带：今日新增 N 项。"""
    return f"""
    <section class="stats-band">
      <div class="container">
        <div class="stat-number">{count}</div>
        <div class="stat-label">今日新增 / New Today</div>
      </div>
    </section>
    """


def _build_article(item, index):
    """单条资讯 article：封面 / 标题 / 介绍 / 元信息 / 查看原页按钮。"""
    title = html.escape(item.get("title") or "未命名")
    url = item.get("url") or ""
    url_escaped = html.escape(url, quote=True)

    parts = [f'    <article class="case" id="case-{index}">']

    # 封面图（有 image_url 才显示）
    image_url = (item.get("image_url") or "").strip()
    if image_url:
        img_escaped = html.escape(image_url, quote=True)
        parts.append(
            f'      <img class="cover" src="{img_escaped}" alt="{title}" loading="lazy">'
        )

    # 标题
    parts.append(f"      <h2>{title}</h2>")

    # 介绍段落（有 description 才显示）
    description = (item.get("description") or "").strip()
    if description:
        parts.append(f'      <p class="description">{html.escape(description)}</p>')

    # 元信息行（拼接非空字段）
    meta_line = _build_meta_line(item)
    if meta_line:
        parts.append(f'      <div class="meta">{meta_line}</div>')

    # 查看原页按钮
    if url:
        parts.append(
            f'      <a class="btn-original" href="{url_escaped}" target="_blank" rel="noopener noreferrer">查看原页</a>'
        )

    parts.append("    </article>")
    return "\n".join(parts)


def _build_empty_state():
    """空状态：今日暂无新增。"""
    return """
    <section class="empty-state">
      <div class="container">
        <p>今日暂无新增</p>
      </div>
    </section>
    """


def _build_footer(today):
    """页脚：数据源 + 生成时间。"""
    gen_time = f"{today.year}-{today.month:02d}-{today.day:02d}"
    return f"""
    <footer class="site-footer">
      <div class="container">
        <div class="footer-meta">
          数据源 · <a href="https://www.onsiteclub.com" target="_blank" rel="noopener noreferrer">{DATA_SOURCE}</a><br>
          生成于 {html.escape(gen_time)}
        </div>
      </div>
    </footer>
    """


def build_h5_report(items, today=None):
    """生成自包含 HTML 报告页字符串。

    Args:
        items: list[dict]，每条资讯可含 title / url / image_url / description /
            city / brand / start_date / end_date（或 start / end）字段。
        today: date 对象，默认 date.today()，用于页头日期与页脚生成时间。

    Returns:
        str: 自包含 HTML 字符串（CSS 内联，字体走 Google Fonts CDN）。
    """
    today = today or date.today()
    items = items or []

    count = len(items)
    header = _build_header(today)
    stats_band = _build_stats_band(count)

    if items:
        articles = "\n".join(_build_article(item, i + 1) for i, item in enumerate(items))
        main_body = f"""
    <main class="report-main">
      <div class="container">
        <section>
{articles}
        </section>
      </div>
    </main>
    """
    else:
        main_body = f"""
    <main class="report-main">
{_build_empty_state()}
    </main>
    """

    footer = _build_footer(today)
    css = _build_css()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Onsite Club 会展日推 · {today.year}-{today.month:02d}-{today.day:02d}</title>
  {GOOGLE_FONTS_LINK}
  <style>{css}
  </style>
</head>
<body>
{header}
{stats_band}
{main_body}
{footer}
</body>
</html>
"""
