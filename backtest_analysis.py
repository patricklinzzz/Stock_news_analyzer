"""
backtest_analysis.py
======================
驗證「新聞訊號」是否有用的第一步:不做情緒分析,
先看最陽春的訊號 —— 「當天某檔股票的新聞則數」跟「隔日報酬率」有沒有相關性。

邏輯:
  1. 從 news.db 撈出每檔股票每天被提到幾次(新聞量)
  2. 用 yfinance 抓歷史股價,算出每日報酬率
  3. 把「當天新聞量」對齊「隔日報酬率」,算相關係數

注意:
  - 這支腳本需要 news.db 累積至少 2-3 週的資料才有意義,
    資料筆數太少的話相關係數不可靠,只是雜訊。
  - 需要額外安裝: pip install yfinance

執行方式:
  python backtest_analysis.py
"""

import sqlite3
from datetime import datetime

import pandas as pd

DB_PATH = "news.db"


def load_daily_news_counts(db_path: str = DB_PATH) -> pd.DataFrame:
    """
    從資料庫算出「每檔股票、每天」被提到的新聞則數。
    日期以新聞的 published_at 為主,沒有的話 fallback 用 fetched_at。
    """
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT
                m.stock_id,
                COALESCE(NULLIF(n.published_at, ''), n.fetched_at) AS ts,
                n.id AS news_id
            FROM news_stock_map m
            JOIN news_raw n ON m.news_id = n.id
            """,
            conn,
        )

    if df.empty:
        return df

    # 只取日期部分(不管時分秒),當作「新聞發生日」
    df["date"] = pd.to_datetime(df["ts"], utc=True, errors="coerce").dt.date
    df = df.dropna(subset=["date"])

    daily_counts = (
        df.groupby(["stock_id", "date"])["news_id"]
        .count()
        .reset_index()
        .rename(columns={"news_id": "news_count"})
    )
    return daily_counts


def fetch_price_returns(stock_ids: list[str], start_date, end_date) -> pd.DataFrame:
    """
    用 yfinance 抓台股歷史股價,算出每日報酬率。
    台股在 yfinance 要加 .TW(上市)或 .TWO(上櫃)後綴,這裡預設用 .TW,
    如果抓不到某檔股票,代表它可能是上櫃股,要自己改成 .TWO 再試一次。
    """
    import yfinance as yf

    all_returns = []
    for stock_id in stock_ids:
        ticker = f"{stock_id}.TW"
        try:
            hist = yf.download(
                ticker, start=start_date, end=end_date, progress=False, auto_adjust=True
            )
        except Exception as e:
            print(f"[WARN] 抓取 {ticker} 股價失敗: {e}")
            continue

        if hist.empty:
            print(f"[WARN] {ticker} 沒有資料(可能是上櫃股,試試 .TWO)")
            continue

        # 新版 yfinance 對單一股票下載時,欄位可能回傳成 MultiIndex
        # (例如 ('Close', '2330.TW')),要攤平成單層才能正常合併資料。
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)

        hist = hist.reset_index()
        hist["stock_id"] = stock_id
        hist["date"] = pd.to_datetime(hist["Date"]).dt.date
        # 隔日報酬率:明天收盤 / 今天收盤 - 1
        hist["next_day_return"] = hist["Close"].pct_change().shift(-1)
        all_returns.append(hist[["stock_id", "date", "Close", "next_day_return"]])

    if not all_returns:
        return pd.DataFrame()
    return pd.concat(all_returns, ignore_index=True)


def run_backtest():
    print("[1/3] 讀取新聞資料庫,計算每日新聞量...")
    daily_counts = load_daily_news_counts()

    if daily_counts.empty:
        print("資料庫還沒有資料,先跑 stock_news_analyzer.py 累積幾天新聞再回來看。")
        return

    print(f"  共有 {len(daily_counts)} 筆(股票, 日期)組合")
    print(f"  涵蓋日期範圍: {daily_counts['date'].min()} ~ {daily_counts['date'].max()}")

    n_days = (daily_counts["date"].max() - daily_counts["date"].min()).days
    if n_days < 14:
        print(
            f"\n⚠️ 目前資料只涵蓋 {n_days} 天,建議至少累積 2-3 週(14-21天)以上"
            "再做這個分析,不然相關係數會很不穩定、容易被雜訊主導。"
            "\n  (可以先跑,但結果先當參考就好)\n"
        )

    stock_ids = daily_counts["stock_id"].unique().tolist()
    start_date = daily_counts["date"].min()
    end_date = datetime.now().date()

    print(f"[2/3] 用 yfinance 抓 {len(stock_ids)} 檔股票的歷史股價...")
    price_returns = fetch_price_returns(stock_ids, start_date, end_date)

    if price_returns.empty:
        print("股價資料抓取失敗,無法繼續分析。")
        return

    print("[3/3] 合併新聞量與隔日報酬,計算相關係數...")
    merged = pd.merge(daily_counts, price_returns, on=["stock_id", "date"], how="inner")
    merged = merged.dropna(subset=["next_day_return"])

    if len(merged) < 5:
        print(f"合併後只剩 {len(merged)} 筆資料,樣本數太少,無法算出有意義的相關係數。")
        print(merged)
        return

    overall_corr = merged["news_count"].corr(merged["next_day_return"])
    print(f"\n=== 整體相關係數(所有股票合併計算) ===")
    print(f"新聞量 vs 隔日報酬率: {overall_corr:.4f}")
    print("(數值接近 0 代表沒什麼關聯,越接近 ±1 代表關聯越強;")
    print(" 但樣本數少的時候,這個數字本身不太可信,只能當方向性參考)")

    print(f"\n=== 各股票明細(依新聞量排序前10筆) ===")
    print(merged.sort_values("news_count", ascending=False).head(10).to_string(index=False))

    # 存檔:每次執行存一份帶日期的完整快照,方便回頭比對某一週的細節
    import os

    os.makedirs("backtest_results", exist_ok=True)
    today_str = datetime.now().date().isoformat()
    snapshot_path = f"backtest_results/backtest_{today_str}.csv"
    merged.to_csv(snapshot_path, index=False)
    print(f"\n本次完整資料已存到 {snapshot_path},共 {len(merged)} 筆")

    # 同時維護一份「每次執行的相關係數」歷史記錄,方便長期追蹤訊號是否穩定
    summary_path = "backtest_results/summary_log.csv"
    summary_row = pd.DataFrame(
        [
            {
                "run_date": today_str,
                "data_start_date": str(daily_counts["date"].min()),
                "data_end_date": str(daily_counts["date"].max()),
                "n_days_covered": n_days,
                "n_stocks": len(stock_ids),
                "n_samples": len(merged),
                "overall_correlation": round(overall_corr, 4),
            }
        ]
    )
    if os.path.exists(summary_path):
        existing = pd.read_csv(summary_path)
        combined = pd.concat([existing, summary_row], ignore_index=True)
    else:
        combined = summary_row
    combined.to_csv(summary_path, index=False, na_rep="NaN")
    print(f"歷史相關係數趨勢已更新到 {summary_path}")
    if pd.isna(overall_corr):
        print(
            "  (本次 overall_correlation 為 NaN:通常是因為 news_count 目前都相同、"
            "沒有變異,數學上算不出相關係數。資料累積更多天後會自然改善。)"
        )


if __name__ == "__main__":
    run_backtest()