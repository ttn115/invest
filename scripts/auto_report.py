import os
import sys
import subprocess
import datetime as dt
from pathlib import Path

# Windows UTF-8 stdout fix
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass


# 將專案根目錄加入路徑
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.monitor.notifier import TelegramNotifier
from dotenv import load_dotenv

# 嘗試載入 .env
load_dotenv()

_BASE = Path(__file__).parent.parent
PROMPT_PATH = _BASE / "ROUND_TABLE_PROMPT.md"
DASHBOARD_PATH = _BASE / "data" / "market_dashboard.md"

def run_scanners():
    print("=" * 60)
    print("🚀 [1/4] 開始掃描虛擬幣 (top_20_scanner.py)...")
    subprocess.run([sys.executable, str(_BASE / "scripts" / "top_20_scanner.py")], check=False)
    
    print("=" * 60)
    print("🚀 [2/4] 開始掃描美股 (us_stock_scanner.py)...")
    subprocess.run([sys.executable, str(_BASE / "scripts" / "us_stock_scanner.py")], check=False)
    
    print("=" * 60)
    print("🚀 [3/4] 開始掃描台股 (tw_stock_scanner.py)...")
    subprocess.run([sys.executable, str(_BASE / "scripts" / "tw_stock_scanner.py")], check=False)
    print("=" * 60)
    print("✅ 三大市場掃描完成，已更新 data/market_dashboard.md！")

def call_llm(system_prompt: str, user_content: str) -> str:
    """呼叫大模型 API 來產生報告 (支援 Gemini, Anthropic, OpenAI)"""
    
    # 1. 優先嘗試 Gemini API (Google)
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        print("🤖 使用 Gemini API 產生報告...")
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel(
                model_name='gemini-2.5-pro',
                system_instruction=system_prompt
            )
            response = model.generate_content(f"以下是最新的市場數據，請根據你的系統設定產出圓桌會議報告：\n\n{user_content}")
            return response.text
        except ImportError:
            print("⚠️ 找不到 google-generativeai 套件，請執行: pip install google-generativeai")
            sys.exit(1)

    # 2. 嘗試 Anthropic API (Claude)
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        print("🤖 使用 Anthropic API (Claude) 產生報告...")
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            message = client.messages.create(
                model="claude-3-7-sonnet-20250219",
                max_tokens=4000,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": f"以下是最新的市場數據，請根據你的系統設定產出圓桌會議報告：\n\n{user_content}"}
                ]
            )
            return message.content[0].text
        except ImportError:
            print("⚠️ 找不到 anthropic 套件，請執行: pip install anthropic")
            sys.exit(1)
            
    # 3. 嘗試 OpenAI API
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        print("🤖 使用 OpenAI API 產生報告...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"以下是最新的市場數據，請根據你的系統設定產出圓桌會議報告：\n\n{user_content}"}
                ]
            )
            return response.choices[0].message.content
        except ImportError:
            print("⚠️ 找不到 openai 套件，請執行: pip install openai")
            sys.exit(1)

    print("❌ 找不到任何 LLM API Key！請在 .env 中設定 GEMINI_API_KEY, ANTHROPIC_API_KEY 或 OPENAI_API_KEY")
    sys.exit(1)


def main():
    # 1. 執行三大市場掃描
    run_scanners()
    
    # 2. 讀取所需文件
    print("\n🚀 [4/4] 準備呼叫 AI 產生圓桌會議報告...")
    if not PROMPT_PATH.exists() or not DASHBOARD_PATH.exists():
        print("❌ 找不到 ROUND_TABLE_PROMPT.md 或 market_dashboard.md！")
        return
        
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    dashboard_content = DASHBOARD_PATH.read_text(encoding="utf-8")
    
    # 3. 呼叫 LLM
    try:
        report_text = call_llm(system_prompt, dashboard_content)
    except Exception as e:
        print(f"❌ API 呼叫失敗: {e}")
        return
        
    # 4. 儲存報告
    today = dt.datetime.now().strftime("%Y-%m-%d")
    output_filename = f"round_table_advisory_{today}.md"
    output_path = _BASE / "data" / output_filename
    
    output_path.write_text(report_text, encoding="utf-8")
    print(f"\n✅ 圓桌會議報告已成功產生並儲存至：{output_path}")
    
    # 5. 發送 Telegram 通知
    notifier = TelegramNotifier()
    if notifier.enabled:
        # 只傳送前 4000 字元避免 Telegram 長度限制
        msg = f"📝 *今日圓桌會議報告已出爐 ({today})*\n\n{report_text[:3800]}"
        if len(report_text) > 3800:
            msg += "\n\n... (報告過長已截斷，請查看完整檔案)"
        notifier.send_message(msg)
        notifier.send_report(str(output_path))
        print("📱 報告已推送至 Telegram！")

if __name__ == "__main__":
    main()
