"""
find_unmatched_companies.py
=============================
目的:找出「常上新聞,但目前股票對照表沒涵蓋到」的公司候選名單。

邏輯(不需要中文分詞套件,純統計法):
  1. 撈出資料庫裡「完全沒被比對到任何股票」的新聞標題
  2. 對這些標題做 n-gram(2~5個中文字的連續片段)頻率統計
     -> 同一家公司如果最近常上新聞,不同標題會重複出現同一段文字(例如「XX科技」)
     -> 用簡單的停用詞過濾掉太通用的詞(大盤、股價、財報 這類)
  3. 依出現次數排序,印出候選字串 + 對應的範例標題,讓你人工判斷是不是公司名稱

這只是輔助掃描工具,不會自動幫你加進股票對照表 —— 判斷「這是不是一家
值得追蹤的公司」還是要人來看,自動化這步容易誤判。

執行方式:
  python find_unmatched_companies.py
"""

import sqlite3
from collections import Counter

DB_PATH = "news.db"

# 太通用、幾乎所有財經新聞都會出現的詞,先過濾掉,不然候選清單會被這些洗版
STOPWORDS = {
    "台股", "大盤", "股價", "財報", "法說", "法說會", "外資", "投信",
    "上漲", "下跌", "分析師", "展望", "第三季", "第四季", "第一季", "第二季",
    "營收", "獲利", "毛利率", "台灣", "美股", "美國", "市場", "投資人",
    "台北", "今日", "盤中", "收盤", "開盤", "個股", "類股", "電子股",
    "金融股", "資金", "買超", "賣超", "報導", "新聞", "記者",
}

MIN_NGRAM = 2
MAX_NGRAM = 5
MIN_FREQUENCY = 3  # 至少要在3則不同標題中出現,才算候選(避免單一巧合)


def get_unmatched_titles(db_path: str = DB_PATH) -> list[str]:
    """撈出完全沒被比對到任何股票的新聞標題"""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT title FROM news_raw
            WHERE id NOT IN (SELECT DISTINCT news_id FROM news_stock_map)
            """
        )
        return [row[0] for row in cur.fetchall()]


def is_chinese_char(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def extract_ngrams(title: str) -> set[str]:
    """
    從標題抽出所有長度2~5的連續中文字片段(同一標題內的片段只算一次,
    避免長標題本身就讓某個詞頻率虛高)。
    """
    ngrams = set()
    n = len(title)
    for start in range(n):
        for length in range(MIN_NGRAM, MAX_NGRAM + 1):
            end = start + length
            if end > n:
                break
            segment = title[start:end]
            if all(is_chinese_char(ch) for ch in segment):
                ngrams.add(segment)
    return ngrams


def find_candidates(titles: list[str]) -> list[tuple[str, int, list[str]]]:
    """
    回傳 [(候選字串, 出現在幾則不同標題中, 範例標題列表), ...],依頻率排序
    """
    ngram_to_titles: dict[str, list[str]] = {}

    for title in titles:
        ngrams = extract_ngrams(title)
        for ng in ngrams:
            if ng in STOPWORDS:
                continue
            ngram_to_titles.setdefault(ng, []).append(title)

    candidates = [
        (ng, len(matched_titles), matched_titles[:3])
        for ng, matched_titles in ngram_to_titles.items()
        if len(matched_titles) >= MIN_FREQUENCY
    ]

    # 短片段(2個字)常是常見詞的一部分,容易誤判,但公司簡稱也常是2-3個字,
    # 所以不直接排除,而是依「頻率」排序,讓真正重複出現的名稱浮到前面。
    # 為了減少子字串互相干擾(例如「大立光」跟「立光」都會被算進去),
    # 優先保留較長、且沒有被更長候選完全包含的片段。
    candidates.sort(key=lambda x: (-x[1], -len(x[0])))

    # 去除「已經是另一個更高頻候選子字串」的短片段,減少雜訊
    kept = []
    kept_strings = []
    for ng, freq, examples in candidates:
        if any(ng in longer for longer in kept_strings if ng != longer):
            continue
        kept.append((ng, freq, examples))
        kept_strings.append(ng)

    return kept


def main():
    titles = get_unmatched_titles()
    print(f"未被比對到股票的新聞標題共 {len(titles)} 則")

    if not titles:
        print("目前沒有未比對到的新聞,股票對照表覆蓋率看起來不錯。")
        return

    candidates = find_candidates(titles)

    if not candidates:
        print(f"沒有找到出現次數 >= {MIN_FREQUENCY} 次的重複字串候選。")
        return

    print(f"\n=== 候選公司名稱(出現次數 >= {MIN_FREQUENCY},依頻率排序前30筆) ===\n")
    for ng, freq, examples in candidates[:30]:
        print(f"「{ng}」 出現於 {freq} 則標題")
        for ex in examples:
            print(f"    - {ex}")
        print()

    print(
        "提醒:這份清單是純統計結果,不是每個都是真正的公司名稱\n"
        "(可能夾雜產業術語、人名、地名),請人工挑選確認後,\n"
        "再手動加進 stock_news_analyzer.py 裡的 STOCK_MAPPING_CSV。"
    )


if __name__ == "__main__":
    main()