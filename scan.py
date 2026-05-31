"""
芒格選股腳本 — 直接執行即可

用法：
    cd stock_invest
    python scan.py                    # 掃描美股（預設）
    python scan.py --market tw        # 掃描台股
    python scan.py --market both      # 同時掃描台股 + 美股
    python scan.py --symbol AAPL      # 分析單一美股
    python scan.py --symbol 2330 --market tw   # 分析單一台股
    python scan.py --symbols AAPL MSFT KO      # 分析多支美股
"""

import argparse
import sys

# Windows 終端機預設 CP950，強制改為 UTF-8 以正確顯示中文與符號
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, ".")   # 確保 src/ 在 import 路徑內

from src.scanner.munger_scanner import MungerScanner
from src.strategy.fundamental_screener import FundamentalScreener


def cmd_scan(market: str):
    """掃描整個觀察清單"""
    scanner = MungerScanner()

    if market == "tw":
        result = scanner.scan_tw()
        print(result.report())

    elif market == "us":
        result = scanner.scan_us()
        print(result.report())

    elif market == "both":
        tw_result, us_result = scanner.scan_both()
        print(tw_result.report())
        print(us_result.report())

        # 合併 PASS 清單
        all_pass = tw_result.passed + us_result.passed
        if all_pass:
            print("\n  🏆  所有通過篩選的標的（依分數排序）：")
            for p in sorted(all_pass, key=lambda x: x.munger_score, reverse=True):
                print(f"     {p.symbol:<8} {p.munger_score:.0f}/100  {p.company_name}")
    else:
        print(f"未知市場: {market}，請使用 tw / us / both")
        sys.exit(1)


def cmd_single(symbol: str, market: str):
    """分析單一股票"""
    screener = FundamentalScreener()
    mkt = "tw_stock" if market == "tw" else "us_stock"

    print(f"\n  分析中：{symbol} ({mkt}) ...\n")
    profile = screener.screen(symbol, mkt)
    print(profile.report())


def cmd_multi(symbols: list[str], market: str):
    """分析多支股票"""
    scanner = MungerScanner()
    mkt = "tw_stock" if market == "tw" else "us_stock"
    result = scanner.scan(symbols, market=mkt)
    print(result.report())


def main():
    parser = argparse.ArgumentParser(description="芒格選股掃描器")
    parser.add_argument(
        "--market", default="us",
        choices=["us", "tw", "both"],
        help="市場：us（美股）/ tw（台股）/ both（全部）"
    )
    parser.add_argument(
        "--symbol", default=None,
        help="分析單一股票，例如 AAPL 或 2330"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="分析多支股票，例如 AAPL MSFT KO"
    )
    args = parser.parse_args()

    if args.symbol:
        cmd_single(args.symbol, args.market)
    elif args.symbols:
        cmd_multi(args.symbols, args.market)
    else:
        cmd_scan(args.market)


if __name__ == "__main__":
    main()
