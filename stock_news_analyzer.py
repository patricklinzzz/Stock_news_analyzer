"""
stock_news_analyzer.py
========================
台股即時新聞蒐集與股票比對 —— 單檔整合版

流程:
  多來源RSS抓取 -> URL去重(DB) -> 標題相似度去重 -> 股票比對 -> 寫入SQLite

執行方式:
  python stock_news_analyzer.py

需要套件:
  pip install requests feedparser

之後要換成 Supabase(PostgreSQL)時,把 DB_SCHEMA 的 CREATE TABLE
語法搬到 Supabase SQL Editor 執行,程式邏輯不用改。
"""

import csv
import hashlib
import io
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher

# requests / feedparser 只有在真的要抓RSS時才需要,
# 延後到 fetch_rss() 內才 import,讓其他邏輯(去重/比對/DB)
# 在沒安裝這兩個套件的環境也能單獨測試。


# ============================================================
# 設定區
# ============================================================

DB_PATH = "news.db"
SIMILARITY_THRESHOLD = 0.85  # 標題相似度超過此值視為重複新聞

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# 各新聞來源的 RSS 網址,之後要新增/調整來源直接改這裡
# 注意:鉅亨網、MoneyDJ 的網址要自行確認是否為最新版本
RSS_SOURCES = {
    "Google News": "https://news.google.com/rss/search?q=台股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    "中央社": "https://feeds.feedburner.com/rsscna/finance",
    "Yahoo奇摩台股": "https://tw.stock.yahoo.com/rss?category=tw-stock",
}

# 新聞來源可信度排序,數字愈小愈可信;重複新聞時優先保留可信度高的版本
SOURCE_PRIORITY = {
    "公開資訊觀測站": 0,
    "中央社": 1,
    "Yahoo奇摩台股": 1,
    "Google News": 2,
}

# 股票對照表:已擴充至90檔(涵蓋台灣50主要成分股+陸續補上的常上新聞公司),之後可自行擴充或改成讀外部CSV
# 格式:股票代號, 公司全名, 別名(用 | 分隔), 產業別
STOCK_MAPPING_CSV = """stock_id,stock_name,aliases,industry
2330,台積電,台積電|台積|TSMC,半導體
2317,鴻海,鴻海|鴻海精密|Foxconn,電子代工
2454,聯發科,聯發科|聯發科技|MTK|發哥,IC設計
2308,台達電,台達電|台達電子|Delta Electronics,電子零組件
2382,廣達,廣達|廣達電腦|Quanta,電腦及週邊
2379,瑞昱,瑞昱|瑞昱半導體|Realtek,IC設計
3231,緯創,緯創|緯創資通|Wistron,電腦及週邊
2303,聯電,聯電|聯華電子|UMC,半導體
2412,中華電,中華電|中華電信|Chunghwa Telecom,電信
2881,富邦金,富邦金|富邦金控|富邦,金融
2882,國泰金,國泰金|國泰金控|國泰,金融
2891,中信金,中信金|中國信託金控|中信金控,金融
1301,台塑,台塑|台灣塑膠,塑膠
1303,南亞,南亞|南亞塑膠,塑膠
2002,中鋼,中鋼|中國鋼鐵,鋼鐵
3008,大立光,大立光|大立光電,光電
2201,裕隆,裕隆|裕隆汽車,汽車
6505,台塑化,台塑化|台塑石化,能源
2886,兆豐金,兆豐金|兆豐金控|兆豐,金融
2603,長榮,長榮|長榮海運,航運
6669,緯穎,緯穎|緯穎科技,電腦及週邊
2356,英業達,英業達,電腦及週邊
2376,技嘉,技嘉|技嘉科技,電腦及週邊
2357,華碩,華碩|華碩電腦|ASUS,電腦及週邊
3037,欣興,欣興|欣興電子,電子零組件
2408,南亞科,南亞科|南亞科技,半導體
2609,陽明,陽明|陽明海運,航運
2615,萬海,萬海|萬海航運,航運
3711,日月光投控,日月光投控|日月光|ASE,半導體
2383,台光電,台光電,電子零組件
2345,智邦,智邦|智邦科技,通信網路
2887,台新新光金,台新新光金|台新金|新光金,金融
2327,國巨,國巨|國巨電子,電子零組件
2368,金像電,金像電|金像電子,電子零組件
7769,鴻勁,鴻勁|鴻勁科技,半導體
2449,京元電子,京元電子|京元電,半導體
2344,華邦電,華邦電|華邦電子,半導體
5534,長虹,長虹|長虹建設,建設
6491,晶碩,晶碩|晶碩光學,醫療器材
2301,光寶科,光寶科|光寶科技|Lite-On,電子零組件
2313,華通,華通|華通電腦,電子零組件
2801,彰銀,彰銀|彰化銀行,金融
2059,川湖,川湖,精密機械
2889,國票金,國票金|國票金控,金融
6770,力積電,力積電|力晶積成,半導體
3481,群創,群創|群創光電|Innolux,面板
3026,禾伸堂,禾伸堂,被動元件
6515,穎崴,穎崴|穎崴科技,半導體
6805,富世達,富世達,電子零組件
7855,和運租車,和運租車,運輸
1319,東陽,東陽|東陽實業,汽車零組件
1802,台玻,台玻|台灣玻璃,玻璃
2049,上銀,上銀|上銀科技,工具機
2324,仁寶,仁寶|仁寶電腦|Compal,電腦及週邊
2353,宏碁,宏碁|Acer,電腦及週邊
2395,研華,研華|研華科技|Advantech,工業電腦
2409,友達,友達|友達光電|AUO,面板
2426,鼎元,鼎元|鼎元光電,光電
2464,盟立,盟立|盟立自動化,自動化設備
2884,玉山金,玉山金|玉山金控,金融
3006,晶豪科,晶豪科|晶豪科技,半導體
3045,台灣大,台灣大|台灣大哥大,電信
3416,融程電,融程電,電子零組件
3532,台勝科,台勝科|台灣勝高,半導體
3661,世芯-KY,世芯-KY|世芯,IC設計
6213,聯茂,聯茂|聯茂電子,電子零組件
6239,力成,力成|力成科技,半導體
6446,藥華藥,藥華藥|藥華醫藥,生技醫療
6472,保瑞,保瑞|保瑞藥業,生技醫療
7786,東方風能,東方風能,能源
7795,長廣,長廣|長廣科技,半導體
8021,尖點,尖點|尖點科技,半導體
2360,致茂,致茂|致茂電子,電子零組件
8112,至上,至上|至上實業,電子零組件
2890,永豐金,永豐金|永豐金控,金融
3090,日電貿,日電貿,電子零組件
3450,聯鈞,聯鈞|聯鈞光電,光電
5434,崇越,崇越|崇越科技,半導體
7750,新代,新代|新代科技,自動化設備
8033,雷虎,雷虎|雷虎科技,航太
2883,凱基金,凱基金|凱基金控,金融
1409,新纖,新纖|新光合成纖維,紡織
1459,聯發,聯發|聯發紡織,紡織
1605,華新,華新|華新麗華,金屬
2349,錸德,錸德|錸德科技,光電
2762,世界健身-KY,世界健身-KY,運動休閒
2880,華南金,華南金|華南金控,金融
2885,元大金,元大金|元大金控,金融
6214,精誠,精誠|精誠資訊,資訊服務
6719,力智,力智|力智電子,半導體
"""

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_raw (
    id TEXT PRIMARY KEY,          -- hash(url),天然做URL層級去重
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    content_snippet TEXT
);

CREATE TABLE IF NOT EXISTS news_stock_map (
    news_id TEXT NOT NULL,
    stock_id TEXT NOT NULL,
    match_method TEXT,            -- 'full_name' 或 'alias'
    matched_text TEXT,
    PRIMARY KEY (news_id, stock_id),
    FOREIGN KEY (news_id) REFERENCES news_raw(id)
);
"""


# ============================================================
# 資料庫層
# ============================================================

@contextmanager
def get_conn(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = DB_PATH):
    with get_conn(db_path) as conn:
        conn.executescript(DB_SCHEMA)


def insert_news(conn, news_item: dict) -> bool:
    """插入一則新聞,若id(url的hash)已存在則忽略,回傳是否為新插入"""
    cur = conn.execute("SELECT 1 FROM news_raw WHERE id = ?", (news_item["id"],))
    if cur.fetchone():
        return False
    conn.execute(
        """INSERT INTO news_raw (id, title, url, source, published_at, fetched_at, content_snippet)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            news_item["id"],
            news_item["title"],
            news_item["url"],
            news_item["source"],
            news_item.get("published_at"),
            news_item["fetched_at"],
            news_item.get("content_snippet", ""),
        ),
    )
    return True


def insert_news_stock_map(conn, news_id: str, stock_id: str, match_method: str, matched_text: str):
    conn.execute(
        """INSERT OR IGNORE INTO news_stock_map (news_id, stock_id, match_method, matched_text)
           VALUES (?, ?, ?, ?)""",
        (news_id, stock_id, match_method, matched_text),
    )


# ============================================================
# 股票對照表
# ============================================================

def load_stock_mapping() -> list[dict]:
    """從內建的 STOCK_MAPPING_CSV 讀取股票對照表"""
    stocks = []
    reader = csv.DictReader(io.StringIO(STOCK_MAPPING_CSV))
    for row in reader:
        aliases = [a.strip() for a in row["aliases"].split("|") if a.strip()]
        stocks.append(
            {
                "stock_id": row["stock_id"],
                "stock_name": row["stock_name"],
                "aliases": aliases,
                "industry": row["industry"],
            }
        )
    return stocks


def match_stocks(text: str, stock_mapping: list[dict]) -> list[dict]:
    """
    比對文字中出現哪些股票,回傳 [{stock_id, match_method, matched_text}, ...]
    已知限制:純字串比對沒做分詞,別名清單要避免太短、太通用的字。
    """
    matches = []
    for stock in stock_mapping:
        full_name = stock["stock_name"]
        if full_name in text:
            matches.append(
                {"stock_id": stock["stock_id"], "match_method": "full_name", "matched_text": full_name}
            )
            continue

        for alias in stock["aliases"]:
            if alias == full_name:
                continue
            if alias in text:
                matches.append(
                    {"stock_id": stock["stock_id"], "match_method": "alias", "matched_text": alias}
                )
                break
    return matches


# ============================================================
# 新聞抓取
# ============================================================

def make_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def parse_published_time(entry) -> str:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
    return ""


def fetch_rss(source_name: str, rss_url: str, timeout: int = 10) -> list[dict]:
    """
    用 requests 先抓內容(帶UA避免被擋),再交給 feedparser 解析。
    單一來源失敗時回傳空list,不中斷整批抓取。

    某些台灣網站(如 MoneyDJ)的憑證鏈會被 Python 較新版本的嚴格 X.509
    檢查誤判為「缺少 Subject/Authority Key Identifier」而擋下,但憑證本身
    是有效的(瀏覽器打得開)。這裡用自訂 SSL context 關掉這一項嚴格檢查,
    其餘憑證驗證(是否為受信任CA簽發、是否過期等)照常進行,不是整個關閉驗證。
    """
    import ssl
    import requests
    import feedparser
    from requests.adapters import HTTPAdapter

    class _RelaxedX509Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            ctx = ssl.create_default_context()
            if hasattr(ssl, "VERIFY_X509_STRICT"):
                ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
            kwargs["ssl_context"] = ctx
            return super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.mount("https://", _RelaxedX509Adapter())

    try:
        resp = session.get(rss_url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] 抓取 {source_name} 失敗: {e}")
        return []

    feed = feedparser.parse(resp.content)
    now = datetime.now(timezone.utc).isoformat()

    results = []
    for entry in feed.entries:
        url = entry.get("link", "")
        if not url:
            continue
        results.append(
            {
                "id": make_id(url),
                "title": entry.get("title", "").strip(),
                "url": url,
                "source": source_name,
                "published_at": parse_published_time(entry),
                "fetched_at": now,
                "content_snippet": entry.get("summary", "")[:500],
            }
        )
    return results


def fetch_all_sources() -> list[dict]:
    all_news = []
    for source_name, rss_url in RSS_SOURCES.items():
        news = fetch_rss(source_name, rss_url)
        print(f"[INFO] {source_name}: 抓到 {len(news)} 則")
        all_news.extend(news)
    return all_news


# ============================================================
# 去重
# ============================================================

def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def dedup_by_title(news_list: list[dict]) -> list[dict]:
    """
    跨來源標題相似度去重。保留策略:
    同一群重複新聞中,保留來源可信度最高者;可信度相同則保留發布時間較早者。
    """
    kept: list[dict] = []

    for item in news_list:
        duplicate_idx = None
        for idx, existing in enumerate(kept):
            if title_similarity(item["title"], existing["title"]) >= SIMILARITY_THRESHOLD:
                duplicate_idx = idx
                break

        if duplicate_idx is None:
            kept.append(item)
            continue

        existing = kept[duplicate_idx]
        item_priority = SOURCE_PRIORITY.get(item["source"], 99)
        existing_priority = SOURCE_PRIORITY.get(existing["source"], 99)

        if item_priority < existing_priority:
            kept[duplicate_idx] = item
        elif item_priority == existing_priority:
            try:
                item_time = datetime.fromisoformat(item.get("published_at", ""))
                existing_time = datetime.fromisoformat(existing.get("published_at", ""))
                if item_time < existing_time:
                    kept[duplicate_idx] = item
            except (ValueError, TypeError):
                pass

    return kept


# ============================================================
# 主流程
# ============================================================

def run():
    print("[1/4] 抓取多來源新聞...")
    raw_news = fetch_all_sources()
    print(f"  共抓到 {len(raw_news)} 則(尚未去重)")

    print("[2/4] 標題相似度去重...")
    deduped_news = dedup_by_title(raw_news)
    print(f"  去重後剩 {len(deduped_news)} 則")

    print("[3/4] 載入股票對照表...")
    stock_mapping = load_stock_mapping()

    print("[4/4] 股票比對 + 寫入資料庫...")
    init_db()
    new_count = 0
    match_count = 0
    with get_conn() as conn:
        for item in deduped_news:
            is_new = insert_news(conn, item)
            if is_new:
                new_count += 1

            full_text = item["title"] + " " + item.get("content_snippet", "")
            matches = match_stocks(full_text, stock_mapping)
            for m in matches:
                insert_news_stock_map(
                    conn, item["id"], m["stock_id"], m["match_method"], m["matched_text"]
                )
                match_count += 1

    print(f"\n完成。新增 {new_count} 則新聞,產生 {match_count} 筆股票比對紀錄。")
    print(f"資料庫位置: {DB_PATH}")


if __name__ == "__main__":
    run()