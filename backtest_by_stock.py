"""
backtest_by_stock.py
======================
把整體相關係數拆解到「每一檔股票」,看看有沒有哪幾檔的訊號
被其他沒訊號的股票稀釋掉了。

*** 這支腳本是「探索用」,不是「下結論用」***
拆到個股層級之後,樣本數只會比整體分析更少,結果只能拿來產生假設、
決定之後要多注意哪幾檔股票,絕對不能把任何單一個股「看起來很強」的
相關係數當成真的訊號 —— 樣本數太小的時候,巧合出現極端相關係數
是家常便飯。

資料來源:直接讀取 backtest_results/ 底下最新一份的完整快照
(backtest_YYYY-MM-DD.csv),不重新抓股價,節省時間也避免重複打API。

執行方式:
  python backtest_by_stock.py
"""

import glob
import os

import pandas as pd
from scipy.stats import pearsonr

BACKTEST_RESULTS_DIR = "backtest_results"
MIN_SAMPLES_PER_STOCK = 5  # 少於這個樣本數,連算都不算,直接跳過


def find_latest_snapshot() -> str | None:
    """找 backtest_results/ 底下最新一份的完整快照檔案"""
    pattern = os.path.join(BACKTEST_RESULTS_DIR, "backtest_*.csv")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def analyze_by_stock(df: pd.DataFrame) -> pd.DataFrame:
    """對每一檔股票分別計算相關係數與p-value"""
    results = []

    for stock_id, group in df.groupby("stock_id"):
        group = group.dropna(subset=["news_count", "next_day_return"])
        n = len(group)

        if n < MIN_SAMPLES_PER_STOCK:
            results.append(
                {
                    "stock_id": stock_id,
                    "n_samples": n,
                    "correlation": None,
                    "p_value": None,
                    "note": f"樣本數<{MIN_SAMPLES_PER_STOCK},略過計算",
                }
            )
            continue

        try:
            corr, p = pearsonr(group["news_count"], group["next_day_return"])
        except ValueError:
            corr, p = float("nan"), float("nan")

        results.append(
            {
                "stock_id": stock_id,
                "n_samples": n,
                "correlation": round(corr, 4) if pd.notna(corr) else None,
                "p_value": round(p, 4) if pd.notna(p) else None,
                "note": "",
            }
        )

    result_df = pd.DataFrame(results)
    # 排序:有算出p-value的排前面(依p-value由小到大),沒算出來的排最後
    result_df["_sort_key"] = result_df["p_value"].fillna(999)
    result_df = result_df.sort_values("_sort_key").drop(columns="_sort_key")
    return result_df


def main():
    print("=" * 60)
    print("⚠️  提醒:這是探索用工具,拆到個股層級樣本數會更少")
    print("    結果只能拿來產生假設,不能拿來下結論或當投資依據")
    print("=" * 60)

    snapshot_path = find_latest_snapshot()
    if snapshot_path is None:
        print(f"\n找不到 {BACKTEST_RESULTS_DIR}/ 底下的快照檔案,"
              "請先跑過 backtest_analysis.py 至少一次。")
        return

    print(f"\n讀取快照: {snapshot_path}")
    df = pd.read_csv(snapshot_path)
    print(f"共 {len(df)} 筆資料,涵蓋 {df['stock_id'].nunique()} 檔股票")

    result_df = analyze_by_stock(df)

    print(f"\n=== 個股層級相關係數(依p-value排序,越前面越「看似」顯著) ===\n")
    print(result_df.to_string(index=False))

    n_with_result = result_df["p_value"].notna().sum()
    n_significant = (result_df["p_value"] < 0.05).sum()
    print(f"\n有算出p-value的股票數: {n_with_result} / {len(result_df)}")
    print(f"其中 p < 0.05 的股票數: {n_significant}")

    if n_significant > 0:
        max_n = result_df["n_samples"].max()
        print(
            f"\n注意:就算看到 p < 0.05,現階段每檔股票的樣本數最多也只有 {max_n} 筆,"
            "\n遠低於能真正信任的門檻(建議至少30筆以上)。"
            "\n這些「看似顯著」的結果目前應該當成『之後要多觀察的候選』,而不是答案。"
        )
    else:
        print("\n目前沒有任何一檔股票的p-value < 0.05,符合資料量還太少的預期。")

    # 存檔,方便之後追蹤同一批股票的排名有沒有隨資料增加而改變
    out_path = os.path.join(BACKTEST_RESULTS_DIR, "by_stock_latest.csv")
    result_df.to_csv(out_path, index=False)
    print(f"\n完整結果已存到 {out_path}")


if __name__ == "__main__":
    main()