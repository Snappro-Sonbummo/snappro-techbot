import os, re, logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
from supabase import create_client

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

chat_history = {}

SYSTEM = (
    "Bạn là trợ lý kỹ thuật của Snappro - cửa hàng cho thuê thiết bị nhiếp ảnh và quay phim. "
    "Snappro cho thuê nhiều thương hiệu: máy ảnh Sony/Canon/Fujifilm, gimbal DJI, mic, flycam, "
    "đèn Nanlite, Nanlux, Aputure, Amaran, Godox và nhiều thiết bị khác.\n"
    "CÁCH TRẢ LỜI:\n"
    "- Khi có phần TÀI LIỆU THIẾT BỊ bên dưới, hãy DÙNG thông tin đó để trả lời cụ thể: công suất, "
    "thông số, ngàm, cách dùng, cách xử lý lỗi. Trích dẫn con số rõ ràng.\n"
    "- Chỉ nói 'liên hệ Snappro để biết thêm' khi thật sự không có thông tin. Đừng lạm dụng câu này.\n"
    "- Nếu khách hỏi một dòng máy/đèn cụ thể mà tài liệu có, hãy liệt kê các model và thông số chính.\n"
    "- Trả lời bằng tiếng Việt có dấu, thân thiện, ngắn gọn.\n"
    "ĐỊNH DẠNG: văn bản thuần, KHÔNG dùng Markdown, không dùng # ## ** __ * ``` --- |. "
    "Chỉ dùng số (1. 2. 3.) và gạch (-) để liệt kê. Có thể dùng emoji để làm nổi bật.\n"
)

# Từ đệm tiếng Việt cần bỏ khi tách từ khoá tìm kiếm
STOP = {
    "thì", "có", "là", "và", "cho", "của", "bao", "nhiêu", "như", "thế", "nào",
    "được", "cái", "một", "mình", "bạn", "với", "khi", "này", "sao", "không",
    "dùng", "loại", "the", "and", "for", "what", "how", "which", "are", "này",
}


def search_rows(query, limit=6):
    """Tìm cả trong cột model lẫn content."""
    rows = []
    for col in ("model", "content"):
        try:
            r = (supabase.table("sony_manuals")
                 .select("model,title,heading,content,url")
                 .ilike(col, f"%{query}%")
                 .limit(limit).execute())
            rows += (r.data or [])
        except Exception as e:
            logger.error(f"Search error ({col}/{query}): {e}")
    return rows


def get_context(question):
    q = question.lower()
    words = [w for w in re.findall(r"\w+", q) if len(w) >= 2 and w not in STOP]

    results, seen = [], set()
    for w in words[:6]:
        for r in search_rows(w):
            # Gộp trùng theo nội dung (KHÔNG theo url, vì nhiều manual có url rỗng)
            key = (r.get("content", "") or "")[:80]
            if key and key not in seen:
                seen.add(key)
                results.append(r)

    if not results:
        return ""

    ctx = "TÀI LIỆU THIẾT BỊ (trích từ manual hãng):\n"
    for i, r in enumerate(results[:8], 1):
        model = r.get("model", "") or ""
        heading = r.get("heading", "") or ""
        body = (r.get("content", "") or "")[:600]
        ctx += f"[{i}] {model} - {heading}\n{body}\n---\n"
    return ctx


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    question = update.message.text or update.message.caption or "Thiết bị này là gì?"
    await update.message.chat.send_action("typing")

    manual_ctx = get_context(question)
    history = chat_history.get(uid, [])
    msgs = list(history)
    content = f"{manual_ctx}\nCâu hỏi: {question}" if manual_ctx else question
    msgs.append({"role": "user", "content": content})

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6", max_tokens=1024, system=SYSTEM, messages=msgs
        )
        answer = resp.content[0].text
        chat_history.setdefault(uid, [])
        chat_history[uid].append({"role": "user", "content": question})
        chat_history[uid].append({"role": "assistant", "content": answer})
        if len(chat_history[uid]) > 20:
            chat_history[uid] = chat_history[uid][-20:]
        await update.message.reply_text(answer[:4000])
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("Xin lỗi, có sự cố. Thử lại sau nhé!")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history[update.effective_user.id] = []
    await update.message.reply_text(
        "Xin chào! Mình là trợ lý kỹ thuật Snappro 📷\n\n"
        "Mình có thể giúp bạn về thiết bị Snappro cho thuê:\n"
        "- Máy ảnh Sony, Canon, Fujifilm\n"
        "- Gimbal DJI, mic, flycam\n"
        "- Đèn Nanlite, Nanlux, Aputure, Amaran, Godox\n"
        "- Thông số, cách dùng, ngàm, phụ kiện, xử lý lỗi\n\n"
        "Hỏi mình bất cứ điều gì nhé! 🚀"
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history[update.effective_user.id] = []
    await update.message.reply_text("Đã xoá lịch sử chat! Bạn có thể bắt đầu lại từ đầu 🗑️")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    logger.info("Bot dang chay...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
