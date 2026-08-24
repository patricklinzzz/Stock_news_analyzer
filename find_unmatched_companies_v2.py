"""
find_unmatched_companies_v2.py
=================================
v1(純詞頻統計)的問題:台股新聞標題充滿聳動的行情評論用語
(「反彈」「史上最大」「分析師」),這些詞出現的頻率跟真正的公司名稱
一樣高甚至更高,單靠頻率統計濾不掉,清單被雜訊淹沒。

v2 改用「跟證交所官方公司名單比對」的方式:
  1. 從證交所免費、不需金鑰的 OpenAPI 抓「全部上市公司基本資料」
     (這是官方公司登記名稱,不是猜測出來的字串)
  2. 用官方的「公司簡稱」去比對未被比對到股票的新聞標題
  3. 只有真實存在的公司名稱才可能比對到,不會再出現「反彈」這種雜訊
  4. 排除掉已經在你 stock_news_analyzer.py 對照表裡的股票,只列出「還沒被涵蓋、
     但常上新聞」的公司候選

限制:
  - 目前只涵蓋「上市」公司,不含「上櫃」(要另外接 TPEx OpenAPI,之後可以擴充)
  - 資料來源:https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv (免費,不需申請金鑰)
  - 這支腳本需要能連外網才能跑(GitHub Actions環境沒問題,本機也可以)

執行方式:
  python find_unmatched_companies_v2.py
"""

import sqlite3
import io

import requests
import pandas as pd

DB_PATH = "news.db"
TWSE_COMPANY_LIST_URL = "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv"
MIN_FREQUENCY = 2  # 至少要在幾則不同標題中出現,才算候選

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# 已涵蓋的股票代號直接從 stock_news_analyzer.py 動態載入,
# 不再手動維護重複清單,避免像這次一樣兩邊對不上、要手動同步。
from stock_news_analyzer import load_stock_mapping

ALREADY_COVERED_IDS = {s["stock_id"] for s in load_stock_mapping()}

# 公司簡稱剛好是日常生活常用詞,純字串比對容易大量誤判,先擋掉。
# 遇到新的類似案例(某公司簡稱剛好撞到常用詞),往這裡加即可。
GENERIC_WORD_BLOCKLIST = {
    "統一",  # 統一(1216) vs. "統一發票"、"撮合統一"等一般用語
    "全新",  # 全新(2455) vs. "全新的"這種形容詞用法
    "冠軍",  # 冠軍電子(1806) vs. "銷量冠軍"這種一般用語
    "全台",  # 全台食品(3038) vs. "全台灣"這種地理範圍用語
    "中華",  # 中華(2204) vs. "中華郵政"等其他機構名稱
    "大量",  # 大量(3167) vs. "大量鎖漲停"這種成交量用語
    "國產",  # 國產(2504) vs. "國產替代"、"國產化"這種常見詞
    "幸福",  # 幸福(1108) vs. "很幸福"這種形容詞用法
    "達新",  # 達新(1315) vs. "馬達新產能"這種兩詞交界處巧合拼出的字串
    "勝一",  # 勝一(1773) vs. "更勝一籌"這個成語被切一半
    "新產",  # 新產(2850) vs. "新產能"、"新產線"這種常見詞組合
}

# 已經人工review過、確認「不該加進對照表」的股票代號。
# 跟 ALREADY_COVERED_IDS 不一樣:那邊是「已經加了」,這裡是「看過但故意不加」,
# 沒有這份清單的話,同一檔股票會一直重複出現在候選名單裡,每次都要重新判斷一次。
REJECTED_STOCK_IDS = {
    "5007",  # 三星:新聞裡指的幾乎都是韓國三星電子,不是台股這檔同名公司
}


def fetch_twse_company_list() -> pd.DataFrame:
    """
    抓證交所官方上市公司基本資料清單。
    這份CSV常見編碼是 big5,直接用 utf-8 讀會亂碼,這裡兩種都試。
    """
    resp = requests.get(TWSE_COMPANY_LIST_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    for encoding in ("utf-8-sig", "big5", "cp950"):
        try:
            df = pd.read_csv(io.BytesIO(resp.content), encoding=encoding)
            # 欄位名稱可能是「公司代號」「公司簡稱」,不同期公告偶爾欄名會微調,
            # 這裡做寬鬆比對,找出看起來像代號/簡稱的欄位。
            code_col = next((c for c in df.columns if "代號" in c), None)
            name_col = next((c for c in df.columns if "簡稱" in c), None)
            if code_col and name_col:
                return df[[code_col, name_col]].rename(
                    columns={code_col: "stock_id", name_col: "company_name"}
                )
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue

    raise RuntimeError("無法解析證交所公司清單,欄位格式可能已變更,需要人工檢查")


def strip_source_suffix(title: str) -> str:
    """
    新聞標題結尾常帶著來源名稱,例如「... - Yahoo股市」「...｜豐雲學堂」,
    如果來源名稱剛好撞到某檔股票簡稱(例如「東森」),比對時會誤判成
    這則新聞在講該公司。這裡把結尾的「來源標記」去掉,只留正文比對。

    規則:找標題裡最後一個分隔符( - 或 ｜ ),如果分隔符後面的文字夠短
    (<=20字,通常來源名稱都很短),就視為來源標記並移除。
    """
    separators = [" - ", "｜", " | "]
    best_idx = -1
    best_sep_len = 0
    for sep in separators:
        idx = title.rfind(sep)
        if idx > best_idx:
            best_idx = idx
            best_sep_len = len(sep)

    if best_idx == -1:
        return title

    suffix = title[best_idx + best_sep_len:]
    if 0 < len(suffix) <= 20:
        return title[:best_idx]
    return title


def get_unmatched_titles(db_path: str = DB_PATH) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT title FROM news_raw
            WHERE id NOT IN (SELECT DISTINCT news_id FROM news_stock_map)
            """
        )
        return [row[0] for row in cur.fetchall()]


def find_candidates(titles: list[str], company_df: pd.DataFrame) -> list[tuple[str, str, int, list[str]]]:
    """
    回傳 [(股票代號, 公司簡稱, 出現則數, 範例標題), ...],依頻率排序。
    只檢查「還沒在對照表裡」的公司,並過濾掉名稱太短(<=1字,容易誤判)
    或撞到通用詞(見 GENERIC_WORD_BLOCKLIST)的資料。
    比對前會先去除標題結尾的來源名稱,避免來源名稱本身撞到股票簡稱。
    """
    candidates = []
    cleaned_titles = [(t, strip_source_suffix(t)) for t in titles]

    for _, row in company_df.iterrows():
        stock_id = str(row["stock_id"]).strip()
        name = str(row["company_name"]).strip()

        if stock_id in ALREADY_COVERED_IDS:
            continue
        if stock_id in REJECTED_STOCK_IDS:
            continue
        if len(name) < 2:  # 太短的公司簡稱容易到處誤判,跳過
            continue
        if name in GENERIC_WORD_BLOCKLIST:
            continue

        matched_titles = [orig for orig, cleaned in cleaned_titles if name in cleaned]
        if len(matched_titles) >= MIN_FREQUENCY:
            candidates.append((stock_id, name, len(matched_titles), matched_titles[:3]))

    candidates.sort(key=lambda x: -x[2])
    return candidates


def main():
    print("[1/3] 從證交所抓官方上市公司名單...")
    try:
        company_df = fetch_twse_company_list()
    except requests.RequestException as e:
        print(f"抓取失敗: {e}")
        return
    print(f"  共 {len(company_df)} 家上市公司")

    print("[2/3] 讀取資料庫,找出未被比對到股票的新聞標題...")
    titles = get_unmatched_titles()
    print(f"  共 {len(titles)} 則")

    if not titles:
        print("目前沒有未比對到的新聞。")
        return

    print("[3/3] 用官方公司名單比對候選公司...")
    candidates = find_candidates(titles, company_df)

    if not candidates:
        print(f"沒有找到出現次數 >= {MIN_FREQUENCY} 次、且尚未涵蓋的公司。")
        return

    print(f"\n=== 候選公司(尚未在對照表裡,依出現次數排序前30筆) ===\n")
    for stock_id, name, freq, examples in candidates[:30]:
        print(f"{stock_id} {name}  出現於 {freq} 則標題")
        for ex in examples:
            print(f"    - {ex}")
        print()

    print(
        "這份清單裡的公司都是證交所官方登記的真實公司,不會有雜訊字串,\n"
        "但仍建議挑幾則範例標題確認一下情境是否合理(例如避免公司簡稱\n"
        "剛好是另一個常用詞的子字串),確認後再加進 stock_news_analyzer.py。\n"
        "\n"
        "注意:這份清單目前只涵蓋『上市』公司,不含『上櫃』,如果常上新聞的\n"
        "公司是上櫃股(例如某些生技、IC設計公司),這裡不會抓到。"
    )


if __name__ == "__main__":
    main()