import os
import requests
from loguru import logger

class TelegramNotifier:
    """Telegram 通知器"""
    
    def __init__(self, token: str = None, chat_id: str = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id)
        
        if not self.enabled:
            logger.warning("Telegram Notifier disabled: Missing TOKEN or CHAT_ID")

    def send_message(self, message: str):
        """發送文字訊息"""
        if not self.enabled:
            return
            
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Telegram message sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    def send_report(self, report_path: str):
        """將報告文件內容發送出去"""
        if not os.path.exists(report_path):
            return
            
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Telegram 訊息長度限制約 4096 字符
        if len(content) > 4000:
            content = content[:3900] + "\n... (Report truncated)"
            
        # 包裝在程式碼區塊中
        msg = f"📊 *New Market Scan Report*\n```\n{content}\n```"
        self.send_message(msg)

if __name__ == "__main__":
    # Test
    notifier = TelegramNotifier()
    if notifier.enabled:
        notifier.send_message("🚀 *Antigravity Bot* is online and ready!")
