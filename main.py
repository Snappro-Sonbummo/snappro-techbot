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
    "- KHÔNG bịa thông số. Nếu tài liệu không có, nói thẳng là chưa có thông tin model đó.\n"
    "- Về giá thuê và tình trạng còn hàng thì luôn mời khách liên hệ Snappro (vì tài liệu không có giá).\n"
    "- Trả lời bằng tiếng Việt có dấu, thân thiện, ngắn gọn.\n"
    "ĐỊNH DẠNG: văn bản thuần, KHÔNG dùng Markdown, không dùng # ## ** __ * ``` --- |. "
    "Chỉ dùng số (1. 2. 3.) và gạch (-) để liệt kê. Có thể dùng emoji để làm nổi bật.\n"
)

# Từ đệm tiếng Việt + tiếng Anh cần bỏ khi tách từ khoá (tránh tìm rác)
STOP = {
    "thì", "có", "là", "và", "cho", "của", "bao", "nhiêu", "như", "thế", "nào",
    "được", "cái", "một", "mình", "bạn", "với", "khi", "này", "sao", "không",
    "dùng", "loại", "dòng", "mã", "cây", "cần", "giá", "tiền", "mức", "túi",
    "đèn", "máy", "tôi", "các", "vừa", "khoẻ", "khỏe", "đang", "muốn",
    "the", "and", "for", "what", "how", "which", "are", "with", "that",
}


def _terms(question):
    """Tách từ khoá + ghép token thành mã model (fc 720 -> fc720, fc-720, fc 720)."""
    q = question.lower()
    raw = re.findall(r"[a-z0-9]+", q)
    base = [t for t in raw if t not in STOP and (len(t) >= 3 or t.isdigit())]
    merges = []
    for i in range(len(raw) - 1):
        a, b = raw[i], raw[i + 1]
        if a.isalpha() and 1 <= len(a) <= 6 and any(ch.isdigit() for ch in b):
            merges += [a + b, a + "-" + b, a + " " + b]
    ordered = []
    for t in merges + base:          # mã model (cụ thể) ưu tiên trước
        if t not in ordered:
            ordered.append(t)
    return ordered, set(base) | set(merges)


def get_context(question):
    terms, qset = _terms(question)
    if not terms:
        return ""

    pool = {}
    for term in terms[:6]:
        for col in ("content", "model"):
            try:
                r = (supabase.table("sony_manuals")
                     .select("model,heading,content")
                     .ilike(col, f"%{term}%").limit(20).execute())
                for row in (r.data or []):
                    key = (row.get("content", "") or "")[:120]
                    if key:
                        pool.setdefault(key, row)
            except Exception as e:
                logger.error(f"Search error ({col}/{term}): {e}")

    if not pool:
        return ""

    def score(row):
        text = ((row.get("content", "") or "") + " " + (row.get("model", "") or "")).lower()
        return sum(1 for t in qset if t in text)

    ranked = sorted(pool.values(), key=score, reverse=True)
    top = [r for r in ranked if score(r) > 0][:8] or ranked[:6]

    ctx = "TÀI LIỆU THIẾT BỊ (trích từ manual hãng):\n"
    for i, r in enumerate(top, 1):
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
# deploy trigger
