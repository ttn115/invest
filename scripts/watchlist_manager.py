#!/usr/bin/env python3
"""
觀察名單管理工具 (Watchlist Manager)
=====================================
統一管理美股 / 台股觀察名單，修改 data/watchlists.json 即可，無需改 Python 程式碼。

使用方式：
  python scripts/watchlist_manager.py list us
  python scripts/watchlist_manager.py list tw
  python scripts/watchlist_manager.py add us PLTR "Palantir AI" --sector 科技
  python scripts/watchlist_manager.py add tw 2379 瑞昱 "網路晶片" --sector IC設計
  python scripts/watchlist_manager.py remove us PLTR
  python scripts/watchlist_manager.py remove tw 2379
  python scripts/watchlist_manager.py universe-add us ARM "ARM Holdings 晶片IP" --sector 半導體
  python scripts/watchlist_manager.py universe-list us
  python scripts/watchlist_manager.py sectors us      # 板塊覆蓋分析
"""

import argparse
import json
import sys
from pathlib import Path

# Windows UTF-8 fix
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

# ── 路徑設定 ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
WATCHLISTS_FILE = ROOT / "data" / "watchlists.json"
UNIVERSE_FILE   = ROOT / "data" / "candidate_universe.json"

VALID_MARKETS = ["us", "tw"]
MARKET_KEY_MAP = {"us": "us_stock", "tw": "tw_stock"}


# ── 工具函數 ─────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已儲存 → {path.relative_to(ROOT)}")


def get_market_key(market: str) -> str:
    return MARKET_KEY_MAP[market]


def find_symbol(symbols: list, ticker: str) -> int:
    """回傳 ticker 在 symbols 清單中的 index，找不到則回傳 -1"""
    ticker_upper = ticker.upper()
    for i, s in enumerate(symbols):
        if s["ticker"].upper() == ticker_upper:
            return i
    return -1


# ── 指令：list ───────────────────────────────────────────────────────────────

def cmd_list(market: str) -> None:
    data = load_json(WATCHLISTS_FILE)
    key  = get_market_key(market)
    symbols = data[key]["symbols"]

    label = "🇺🇸 美股" if market == "us" else "🇹🇼 台股"
    print(f"\n{label} 觀察名單（共 {len(symbols)} 支）")
    print("─" * 60)

    # 按板塊分組
    by_sector: dict[str, list] = {}
    for s in symbols:
        sec = s.get("sector", "其他")
        by_sector.setdefault(sec, []).append(s)

    for sector, items in by_sector.items():
        print(f"\n  [{sector}]")
        for item in items:
            ticker = item["ticker"]
            name   = item.get("name", "")
            note   = item.get("note", "")
            display = f"{ticker} {name}".strip()
            print(f"    {display:<20} {note}")

    print()


# ── 指令：sectors ────────────────────────────────────────────────────────────

def cmd_sectors(market: str) -> None:
    data = load_json(WATCHLISTS_FILE)
    key  = get_market_key(market)
    symbols = data[key]["symbols"]

    by_sector: dict[str, int] = {}
    for s in symbols:
        sec = s.get("sector", "其他")
        by_sector[sec] = by_sector.get(sec, 0) + 1

    label = "🇺🇸 美股" if market == "us" else "🇹🇼 台股"
    print(f"\n{label} 板塊覆蓋分析（共 {len(symbols)} 支）")
    print("─" * 40)
    for sec, count in sorted(by_sector.items(), key=lambda x: -x[1]):
        bar = "█" * count
        print(f"  {sec:<12} {bar} ({count})")

    # 宇宙中有但觀察名單缺席的板塊
    universe = load_json(UNIVERSE_FILE)
    uni_key  = get_market_key(market)
    uni_sectors: set[str] = set()
    for u in universe.get(uni_key, []):
        uni_sectors.add(u.get("sector", "其他"))

    missing = uni_sectors - set(by_sector.keys())
    if missing:
        print(f"\n⚠️  候選宇宙中有但觀察名單尚未覆蓋的板塊：")
        for sec in sorted(missing):
            # 列出該板塊的候選
            cands = [u["ticker"] for u in universe[uni_key] if u.get("sector") == sec]
            print(f"  • {sec}: {', '.join(cands)}")
    print()


# ── 指令：add ────────────────────────────────────────────────────────────────

def cmd_add(market: str, ticker: str, name_or_note: str, note: str, sector: str) -> None:
    data = load_json(WATCHLISTS_FILE)
    key  = get_market_key(market)
    symbols = data[key]["symbols"]

    ticker = ticker.upper() if market == "us" else ticker

    # 重複檢查
    if find_symbol(symbols, ticker) >= 0:
        print(f"⚠️  {ticker} 已在觀察名單中，略過。")
        return

    # 建立新項目
    if market == "us":
        new_entry = {"ticker": ticker, "sector": sector, "note": name_or_note}
    else:
        # 台股：ticker name note sector
        new_entry = {"ticker": ticker, "name": name_or_note, "sector": sector, "note": note}

    symbols.append(new_entry)

    # 同步移除候選宇宙中的同一標的（已升級為觀察名單就不必留在候選了）
    uni_data = load_json(UNIVERSE_FILE)
    uni_key  = get_market_key(market)
    uni_list = uni_data.get(uni_key, [])
    uni_data[uni_key] = [u for u in uni_list if u["ticker"].upper() != ticker.upper()]
    save_json(UNIVERSE_FILE, uni_data)

    save_json(WATCHLISTS_FILE, data)
    display = f"{ticker} {name_or_note}".strip() if market == "tw" else ticker
    print(f"✅ 已新增 {display} → {market.upper()} 觀察名單（板塊：{sector}）")
    print(f"   備注：{note or name_or_note}")
    print(f"\n💡 下次掃描時將自動納入此標的。")


# ── 指令：remove ─────────────────────────────────────────────────────────────

def cmd_remove(market: str, ticker: str) -> None:
    data = load_json(WATCHLISTS_FILE)
    key  = get_market_key(market)
    symbols = data[key]["symbols"]

    idx = find_symbol(symbols, ticker)
    if idx < 0:
        print(f"⚠️  {ticker} 不在觀察名單中。")
        return

    removed = symbols.pop(idx)
    save_json(WATCHLISTS_FILE, data)
    display = removed.get("name", removed["ticker"])
    print(f"✅ 已移除 {removed['ticker']} {display} from {market.upper()} 觀察名單")


# ── 指令：universe-add ───────────────────────────────────────────────────────

def cmd_universe_add(market: str, ticker: str, name_or_note: str, note: str, sector: str) -> None:
    uni_data = load_json(UNIVERSE_FILE)
    key  = get_market_key(market)
    uni_list = uni_data.get(key, [])

    ticker = ticker.upper() if market == "us" else ticker

    # 重複檢查（宇宙 + 觀察名單）
    if find_symbol(uni_list, ticker) >= 0:
        print(f"⚠️  {ticker} 已在候選宇宙中，略過。")
        return

    wl_data = load_json(WATCHLISTS_FILE)
    wl_symbols = wl_data[key]["symbols"]
    if find_symbol(wl_symbols, ticker) >= 0:
        print(f"⚠️  {ticker} 已在觀察名單中，無需加入候選宇宙。")
        return

    if market == "us":
        new_entry = {"ticker": ticker, "sector": sector, "note": name_or_note}
    else:
        new_entry = {"ticker": ticker, "name": name_or_note, "sector": sector, "note": note}

    uni_list.append(new_entry)
    uni_data[key] = uni_list
    save_json(UNIVERSE_FILE, uni_data)
    print(f"✅ 已新增 {ticker} → 候選宇宙（板塊：{sector}）")
    print(f"   執行 candidate_screener.py 即可分析此標的")


# ── 指令：universe-list ──────────────────────────────────────────────────────

def cmd_universe_list(market: str) -> None:
    uni_data = load_json(UNIVERSE_FILE)
    key  = get_market_key(market)
    candidates = uni_data.get(key, [])

    wl_data    = load_json(WATCHLISTS_FILE)
    wl_symbols = wl_data[key]["symbols"]
    wl_tickers = {s["ticker"].upper() for s in wl_symbols}

    label = "🇺🇸 美股" if market == "us" else "🇹🇼 台股"
    print(f"\n{label} 候選宇宙（共 {len(candidates)} 支，✓ 表示已在觀察名單）")
    print("─" * 60)

    by_sector: dict[str, list] = {}
    for c in candidates:
        sec = c.get("sector", "其他")
        by_sector.setdefault(sec, []).append(c)

    for sector, items in by_sector.items():
        print(f"\n  [{sector}]")
        for item in items:
            ticker = item["ticker"]
            name   = item.get("name", "")
            note   = item.get("note", "")
            in_wl  = "✓ 已在名單" if ticker.upper() in wl_tickers else ""
            display = f"{ticker} {name}".strip()
            print(f"    {display:<20} {note:<30} {in_wl}")
    print()


# ── CLI 入口 ─────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="觀察名單管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  python scripts/watchlist_manager.py list us
  python scripts/watchlist_manager.py list tw
  python scripts/watchlist_manager.py sectors us
  python scripts/watchlist_manager.py add us PLTR "Palantir AI政府合約" --sector 科技/AI
  python scripts/watchlist_manager.py add tw 2379 瑞昱 "網路+音訊晶片" --sector IC設計
  python scripts/watchlist_manager.py remove us PLTR
  python scripts/watchlist_manager.py universe-add us ARM "ARM Holdings 晶片IP" --sector 半導體
  python scripts/watchlist_manager.py universe-list us
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="列出觀察名單")
    p_list.add_argument("market", choices=VALID_MARKETS, help="us / tw")

    # sectors
    p_sec = sub.add_parser("sectors", help="板塊覆蓋分析")
    p_sec.add_argument("market", choices=VALID_MARKETS)

    # add
    p_add = sub.add_parser("add", help="新增標的到觀察名單")
    p_add.add_argument("market",  choices=VALID_MARKETS)
    p_add.add_argument("ticker",  help="股票代碼（美股大寫，台股4碼）")
    p_add.add_argument("name_or_note", metavar="name/note",
                       help="美股：備注說明 | 台股：中文名稱")
    p_add.add_argument("note",    nargs="?", default="",
                       help="台股附加備注（可省略）")
    p_add.add_argument("--sector", default="其他", help="板塊分類（例：科技、半導體、金融）")

    # remove
    p_rm = sub.add_parser("remove", help="從觀察名單移除")
    p_rm.add_argument("market",  choices=VALID_MARKETS)
    p_rm.add_argument("ticker",  help="股票代碼")

    # universe-add
    p_ua = sub.add_parser("universe-add", help="新增標的到候選宇宙")
    p_ua.add_argument("market",  choices=VALID_MARKETS)
    p_ua.add_argument("ticker",  help="股票代碼")
    p_ua.add_argument("name_or_note", metavar="name/note")
    p_ua.add_argument("note",    nargs="?", default="")
    p_ua.add_argument("--sector", default="其他")

    # universe-list
    p_ul = sub.add_parser("universe-list", help="列出候選宇宙")
    p_ul.add_argument("market", choices=VALID_MARKETS)

    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if args.command == "list":
        cmd_list(args.market)

    elif args.command == "sectors":
        cmd_sectors(args.market)

    elif args.command == "add":
        cmd_add(args.market, args.ticker, args.name_or_note,
                args.note, args.sector)

    elif args.command == "remove":
        cmd_remove(args.market, args.ticker)

    elif args.command == "universe-add":
        cmd_universe_add(args.market, args.ticker, args.name_or_note,
                         args.note, args.sector)

    elif args.command == "universe-list":
        cmd_universe_list(args.market)


if __name__ == "__main__":
    main()
