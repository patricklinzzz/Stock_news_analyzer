"""
generate_dashboard.py
========================
把 backtest_results/summary_log.csv 和 by_stock_latest.csv
產生成一份靜態網頁(docs/index.html),方便透過 GitHub Pages
直接用網址查看,不用每次都點進 repo 翻表格。

執行方式:
  python generate_dashboard.py

部署(只需設定一次):
  1. 把這支腳本產生的 docs/index.html 一起 commit、push 上去
  2. GitHub repo 頁面 → Settings → Pages → Source 選 "Deploy from a branch"
     → Branch 選 main,資料夾選 /docs → Save
  3. 等個1-2分鐘,GitHub會給一個網址,格式是:
     https://<你的帳號>.github.io/<repo名稱>/
  4. 之後每次 weekly_backtest.yml 跑完,這個網址的內容會自動更新
"""

import json
import os
from datetime import datetime

import pandas as pd

BACKTEST_RESULTS_DIR = "backtest_results"
OUTPUT_DIR = "docs"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "index.html")

SAMPLE_TARGET = 300  # 個股層級開始有參考價值的樣本數門檻(之前討論過的經驗法則)


def load_data():
    summary_path = os.path.join(BACKTEST_RESULTS_DIR, "summary_log.csv")
    by_stock_path = os.path.join(BACKTEST_RESULTS_DIR, "by_stock_latest.csv")

    summary = pd.read_csv(summary_path) if os.path.exists(summary_path) else pd.DataFrame()
    by_stock = pd.read_csv(by_stock_path) if os.path.exists(by_stock_path) else pd.DataFrame()
    return summary, by_stock


def build_headline(latest: dict) -> tuple[str, str]:
    """
    依最新一筆資料,產生一句白話的現況說明 + 對應的語意顏色(正/負/中性)。
    不做任何投資建議,純粹描述統計狀態。
    """
    n = latest.get("n_samples")
    p = latest.get("p_value")

    if n is None or pd.isna(n) or n < 30:
        return f"樣本數還太少(n={n}),目前任何相關係數都只能當雜訊看待", "neutral"

    if p is None or pd.isna(p):
        return f"樣本數 {n} 筆,但這次算不出 p 值,可能資料裡有極端情況", "neutral"

    if p < 0.05:
        return f"樣本數 {n} 筆,p-value={p:.4f},出現統計上顯著的關聯,但仍需持續觀察是否穩定", "positive"

    return f"樣本數 {n} 筆,p-value={p:.4f},目前沒有統計上顯著的關聯", "negative"


def render_html(summary: pd.DataFrame, by_stock: pd.DataFrame) -> str:
    has_summary = not summary.empty
    latest = summary.iloc[-1].to_dict() if has_summary else {}

    n_samples = latest.get("n_samples", 0) if has_summary else 0
    n_samples = 0 if pd.isna(n_samples) else int(n_samples)
    progress_pct = min(100, round(n_samples / SAMPLE_TARGET * 100))

    headline_text, headline_tone = (
        build_headline(latest) if has_summary else ("尚未有回測資料,請先跑 backtest_analysis.py", "neutral")
    )

    corr_val = latest.get("overall_correlation")
    p_val = latest.get("p_value")
    corr_display = f"{corr_val:.4f}" if pd.notna(corr_val) else "—"
    p_display = f"{p_val:.4f}" if pd.notna(p_val) else "—"

    # 圖表資料
    chart_labels = summary["run_date"].tolist() if has_summary else []
    chart_corr = [None if pd.isna(v) else round(v, 4) for v in summary["overall_correlation"]] if has_summary else []
    chart_samples = [None if pd.isna(v) else int(v) for v in summary["n_samples"]] if has_summary else []

    # 個股表格
    stock_rows = ""
    if not by_stock.empty:
        for _, row in by_stock.iterrows():
            corr = row.get("correlation")
            p = row.get("p_value")
            n = row.get("n_samples")
            note = row.get("note", "")

            if pd.isna(corr):
                corr_cell = f'<td class="num muted">— <span class="note">{note}</span></td>'
                p_cell = '<td class="num muted">—</td>'
                row_class = "muted-row"
            else:
                tone = "pos" if corr > 0 else "neg"
                sig = ' class="sig"' if pd.notna(p) and p < 0.05 else ""
                corr_cell = f'<td class="num {tone}"{sig}>{corr:+.4f}</td>'
                p_cell = f'<td class="num{" sig" if pd.notna(p) and p < 0.05 else ""}">{p:.4f}</td>'
                row_class = ""

            stock_rows += f"""
            <tr class="{row_class}">
                <td class="mono">{row['stock_id']}</td>
                <td class="num">{int(n)}</td>
                {corr_cell}
                {p_cell}
            </tr>"""
    else:
        stock_rows = '<tr><td colspan="4" class="muted">尚未有個股層級資料,請先跑 backtest_by_stock.py</td></tr>'

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>台股新聞訊號儀表板</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #12151C;
    --bg-raised: #191D27;
    --border: #262B38;
    --text: #E8E6E1;
    --text-muted: #8B92A0;
    --gold: #D4A24C;
    --green: #4CAF7D;
    --red: #E2574C;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, "Segoe UI", "Noto Sans TC", "PingFang TC", sans-serif;
    line-height: 1.6;
  }}
  .mono {{ font-family: "SF Mono", "Consolas", "Roboto Mono", monospace; }}
  .wrap {{
    max-width: 760px;
    margin: 0 auto;
    padding: 32px 20px 80px;
  }}
  header {{
    margin-bottom: 36px;
  }}
  h1 {{
    font-size: 1.5rem;
    font-weight: 600;
    margin: 0 0 4px;
    letter-spacing: -0.01em;
  }}
  .updated {{
    color: var(--text-muted);
    font-size: 0.85rem;
  }}
  .hero {{
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 24px;
    margin-bottom: 28px;
  }}
  .hero-headline {{
    font-size: 1.05rem;
    margin-bottom: 20px;
    padding-left: 12px;
    border-left: 3px solid var(--gold);
  }}
  .hero-headline.positive {{ border-left-color: var(--green); }}
  .hero-headline.negative {{ border-left-color: var(--text-muted); }}
  .stat-row {{
    display: flex;
    gap: 32px;
    flex-wrap: wrap;
  }}
  .stat {{
    flex: 1;
    min-width: 120px;
  }}
  .stat-label {{
    color: var(--text-muted);
    font-size: 0.78rem;
    margin-bottom: 4px;
  }}
  .stat-value {{
    font-size: 1.6rem;
    font-weight: 600;
  }}
  .progress-bar {{
    margin-top: 20px;
  }}
  .progress-track {{
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    height: 8px;
    overflow: hidden;
  }}
  .progress-fill {{
    background: var(--gold);
    height: 100%;
    transition: width 0.3s;
  }}
  .progress-label {{
    color: var(--text-muted);
    font-size: 0.78rem;
    margin-top: 6px;
  }}
  section {{
    margin-bottom: 32px;
  }}
  h2 {{
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: none;
    margin: 0 0 14px;
  }}
  .chart-box {{
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 20px 16px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
  }}
  th {{
    text-align: right;
    color: var(--text-muted);
    font-weight: 500;
    font-size: 0.78rem;
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
  }}
  th:first-child, td:first-child {{ text-align: left; }}
  td {{
    padding: 9px 10px;
    border-bottom: 1px solid var(--border);
  }}
  .num {{ text-align: right; font-family: "SF Mono", "Consolas", "Roboto Mono", monospace; }}
  .pos {{ color: var(--green); }}
  .neg {{ color: var(--red); }}
  .muted {{ color: var(--text-muted); }}
  .muted-row td {{ color: var(--text-muted); }}
  .sig {{ font-weight: 700; }}
  .note {{ font-size: 0.75rem; }}
  footer {{
    color: var(--text-muted);
    font-size: 0.78rem;
    border-top: 1px solid var(--border);
    padding-top: 16px;
  }}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <h1>台股新聞訊號儀表板</h1>
    <div class="updated mono">最後更新 {generated_at}</div>
  </header>

  <div class="hero">
    <div class="hero-headline {headline_tone}">{headline_text}</div>
    <div class="stat-row">
      <div class="stat">
        <div class="stat-label">整體相關係數</div>
        <div class="stat-value mono">{corr_display}</div>
      </div>
      <div class="stat">
        <div class="stat-label">p-value</div>
        <div class="stat-value mono">{p_display}</div>
      </div>
      <div class="stat">
        <div class="stat-label">樣本數</div>
        <div class="stat-value mono">{n_samples}</div>
      </div>
    </div>
    <div class="progress-bar">
      <div class="progress-track">
        <div class="progress-fill" style="width: {progress_pct}%"></div>
      </div>
      <div class="progress-label">個股層級可信度門檻進度:{n_samples} / {SAMPLE_TARGET}({progress_pct}%)</div>
    </div>
  </div>

  <section>
    <h2>相關係數趨勢(每週)</h2>
    <div class="chart-box">
      <canvas id="trendChart" height="140"></canvas>
    </div>
  </section>

  <section>
    <h2>個股層級明細(依 p-value 排序,粗體代表 p&lt;0.05,但樣本數普遍偏少,勿直接採信)</h2>
    <table>
      <thead>
        <tr>
          <th>股票代號</th>
          <th>樣本數</th>
          <th>相關係數</th>
          <th>p-value</th>
        </tr>
      </thead>
      <tbody>
        {stock_rows}
      </tbody>
    </table>
  </section>

  <footer>
    資料每週自動更新(GitHub Actions)。本頁面僅供研究與學習用途,任何相關係數、統計結果不構成投資建議。
  </footer>

</div>

<script>
const ctx = document.getElementById('trendChart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: {json.dumps(chart_labels)},
    datasets: [{{
      label: '相關係數',
      data: {json.dumps(chart_corr)},
      borderColor: '#D4A24C',
      backgroundColor: 'rgba(212,162,76,0.08)',
      tension: 0.25,
      pointRadius: 4,
      pointBackgroundColor: '#D4A24C',
      spanGaps: true,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          afterLabel: function(context) {{
            const samples = {json.dumps(chart_samples)};
            return 'n_samples: ' + samples[context.dataIndex];
          }}
        }}
      }}
    }},
    scales: {{
      y: {{
        grid: {{ color: '#262B38' }},
        ticks: {{ color: '#8B92A0' }},
      }},
      x: {{
        grid: {{ display: false }},
        ticks: {{ color: '#8B92A0' }},
      }}
    }}
  }}
}});
</script>

</body>
</html>
"""


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    summary, by_stock = load_data()
    html = render_html(summary, by_stock)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"儀表板已產生: {OUTPUT_PATH}")
    if summary.empty:
        print("提醒:目前還沒有 summary_log.csv,先跑過 backtest_analysis.py 才會有內容。")


if __name__ == "__main__":
    main()
