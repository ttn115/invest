import ccxt
import pandas as pd

def get_top_20_by_volume():
    exchange = ccxt.binance()
    tickers = exchange.fetch_tickers()
    
    # Filter for USDT pairs and sort by baseVolume or quoteVolume? 
    # Usually quoteVolume (USDT volume) is better for ranking liquidity.
    data = []
    for symbol, ticker in tickers.items():
        if symbol.endswith("/USDT"):
            data.append({
                "symbol": symbol,
                "volume": ticker["quoteVolume"]
            })
            
    df = pd.DataFrame(data)
    df = df.sort_values(by="volume", ascending=False)
    
    top_20 = df.head(20)
    print("Top 20 USDT Pairs by 24h Volume:")
    print(top_20)
    return top_20["symbol"].tolist()

if __name__ == "__main__":
    get_top_20_by_volume()
