"""
Snappro TechBot - Telegram Bot
================================
- Tìm kiếm manual Sony trong Supabase
- Nhớ context hội thoại (memory)
- Trả lời bằng Claude AI dựa trên manual thật
"""

import os
import re
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
from supabase import create_client

# ============================================================
# CONFIG - Lấy từ Railway Environment Variables
# ============================================================
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
CLAUDE_API_KEY  = os.environ.get("CLAUDE_API_KEY", "")
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "https://drmhgklgdegaimcaieau.supabase.co")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "")

# ============================================================
# KHỞI TẠO
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

claude   = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Lưu lịch sử chat theo user_id (memory)
# Format: {user_id: [{"role": "user/assistant", "content": "..."}]}
chat_history = {}
MAX_HISTORY  = 10  # giữ 10 tin nhắn gần nhất


# ============================================================
# TÌM KIẾM MANUAL TRONG SUPABASE
# ============================================================
def search_manual(query: str, limit: int = 5) -> list:
    """Tìm kiếm manual Sony trong Supabase bằng full-text search"""
    try:
        # Tìm theo từ khóa trong content và heading
        result = (
            supabase.table("sony_manuals")
            .select("model, title, heading, content, url")
            .ilike("content", f"%{query}%")
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


def search_multi_keyword(question: str) -> list:
    """Tách keywords từ câu hỏi và tìm kiếm"""
    # Tách các từ khóa quan trọng
    keywords = extract_keywords(question)
    all_results = []
    seen_urls   = set()

    for kw in keywords[:3]:  # tối đa 3 keywords
        results = search_manual(kw, limit=3)
        for r in results:
            if r.get("url") not in seen_urls:
                seen_urls.add(r.get("url"))
                all_results.append(r)

    return all_results[:6]  # trả về tối đa 6 kết quả


def extract_keywords(text: str) -> list:
    """Trích xuất keywords quan trọng từ câu hỏi"""
    # Danh sách từ cần bỏ qua
    stopwords = {"tôi", "mình", "bạn", "cần", "muốn", "làm", "thế", "nào",
                 "như", "sao", "để", "có", "không", "được", "và", "hay",
                 "với", "từ", "khi", "đang", "bị", "cho", "của", "về",
                 "how", "to", "can", "the", "a", "an", "is", "are", "do",
                 "does", "what", "why", "when", "where", "which", "i", "my"}

    words = re.findall(r'\b\w+\b', text.lower())
    keywords = [w for w in words if w not in stopwords and len(w) > 2]

    # Ưu tiên tên máy, tính năng
    priority = []
    for w in keywords:
        if any(x in w.upper() for x in ["A7", "A9", "A1", "FX", "ZV", "6400", "6600", "6700",
                                          "BLUETOOTH", "WIFI", "AF", "ISO", "4K", "FOCUS"]):
            priority.insert(0, w)
        else:
            priority.append(w)

    return priority[:5]


def format_context(results: list) -> str:
    """Format kết quả tìm kiếm thành context cho Claude"""
    if not results:
        return ""

    context = "=== THÔNG TIN TỪ MANUAL SONY ===\n\n"
    for i, r in enumerate(results, 1):
        context += f"[{i}] {r.get('model', '')} - {r.get('heading', r.get('title', ''))}\n"
        context += f"Nguồn: {r.get('url', '')}\n"
        context += f"Nội dung: {r.get('content', '')[:600]}\n"
        context += "---\n"

    return context


# ============================================================
# XỬ LÝ TIN NHẮN
# ============================================================
def get_history(user_id: int) -> list:
    return chat_history.get(user_id, [])


def add_to_history(user_id: int, role: str, content: str):
    if user_id not in chat_history:
        chat_history[user_id] = []
    chat_history[user_id].append({"role": role, "content": content})
    # Giữ chỉ MAX_HISTORY tin nhắn gần nhất
    if len(chat_history[user_id]) > MAX_HISTORY * 2:
        chat_history[user_id] = chat_history[user_id][-MAX_HISTORY * 2:]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    username = update.effective_user.first_name or "bạn"
    question = ""

    # Xử lý text
    if update.message.text:
        question = update.message.text.strip()

    # Xử lý ảnh
    elif update.message.photo or update.message.document:
        caption  = update.message.caption or ""
        question = caption if caption else "Thiết bị trong ảnh này là gì và cách sử dụng như thế nào?"

    if not question:
        return

    logger.info(f"User {user_id} ({username}): {question}")

    # Tìm kiếm manual liên quan
    await update.message.chat.send_action("typing")
    manual_results = search_multi_keyword(question)
    manual_context = format_context(manual_results)

    # Lấy lịch sử chat
    history = get_history(user_id)

    # System prompt
    system_prompt = """Bạn là trợ lý kỹ thuật chuyên nghiệp của Snappro - shop cho thuê thiết bị máy ảnh, quay phim.

NHIỆM VỤ:
- Hỗ trợ khách hàng sử dụng thiết bị Sony (máy ảnh, lens)
- Trả lời dựa trên thông tin manual chính xác được cung cấp
- Hướng dẫn từng bước rõ ràng, dễ hiểu cho người mới
- Trả lời bằng tiếng Việt, thân thiện và chuyên nghiệp

NGUYÊN TẮC:
- Nếu có thông tin từ manual → dùng ngay, trích dẫn rõ nguồn
- Nếu không có trong manual → trả lời theo kiến thức chung nhưng nói rõ
- Hỏi lại nếu chưa rõ thiết bị khách đang dùng
- Giữ câu trả lời ngắn gọn, dùng emoji và bullet points cho dễ đọc trên Telegram"""

    # Xây dựng messages
    messages = list(history)

    # Thêm context manual vào câu hỏi hiện tại
    if manual_context:
        user_content = f"{manual_context}\n\nCâu hỏi của khách: {question}"
    else:
        user_content = question

    messages.append({"role": "user", "content": user_content})

    try:
        # Gọi Claude API
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        answer = response.content[0].text

        # Lưu vào history (lưu câu hỏi gốc, không lưu context manual)
        add_to_history(user_id, "user", question)
        add_to_history(user_id, "assistant", answer)

        # Gửi trả lời
        # Telegram giới hạn 4096 ký tự/tin nhắn
        if len(answer) > 4000:
            parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
            for part in parts:
                await update.message.reply_text(part)
        else:
            await update.message.reply_text(answer)

    except Exception as e:
        logger.error(f"Claude error: {e}")
        await update.message.reply_text(
            "⚠️ Xin lỗi, mình đang gặp sự cố kỹ thuật. Vui lòng thử lại sau ít phút!"
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name    = update.effective_user.first_name or "bạn"

    # Reset history khi /start
    chat_history[user_id] = []

    welcome = f"""👋 Xin chào {name}!

Mình là trợ lý kỹ thuật của **Snappro** 📷

Mình có thể giúp bạn:
📷 Hướng dẫn sử dụng máy ảnh Sony
🔧 Cài đặt tính năng (AF, ISO, White Balance...)
📡 Kết nối Bluetooth, WiFi, gimbal
🎥 Setup quay video 4K, slow motion
⚡ Xử lý lỗi thiết bị

Hỏi mình bất cứ điều gì về thiết bị nhé! 🚀"""

    await update.message.reply_text(welcome, parse_mode="Markdown")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xóa lịch sử chat"""
    user_id = update.effective_user.id
    chat_history[user_id] = []
    await update.message.reply_text("🗑️ Đã xóa lịch sử chat. Bắt đầu cuộc trò chuyện mới nhé!")


# ============================================================
# MAIN
# ============================================================
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("Thiếu TELEGRAM_TOKEN!")
    if not CLAUDE_API_KEY:
        raise ValueError("Thiếu CLAUDE_API_KEY!")
    if not SUPABASE_KEY:
        raise ValueError("Thiếu SUPABASE_KEY!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))

    logger.info("🤖 Bot đang chạy...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
