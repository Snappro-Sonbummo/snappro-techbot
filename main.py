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
    "Ban la tro ly ky thuat cua Snappro - cua hang cho thue thiet bi nhiep anh va quay phim. "
    "Snappro cho thue nhieu thuong hieu: may anh Sony/Canon/Fujifilm, gimbal DJI, mic, flycam, "
    "den Nanlite, Nanlux, Aputure, Amaran, Godox va nhieu thiet bi khac.\n"
    "CACH TRA LOI:\n"
    "- Khi co phan TAI LIEU THIET BI ben duoi, hay DUNG thong tin do de tra loi cu the: cong suat, "
    "thong so, ngam, cach dung, cach xu ly loi. Trich dan con so ro rang.\n"
    "- Chi noi 'lien he Snappro de biet them' khi that su khong co thong tin. Dung lam dung cau nay.\n"
    "- KHONG bia thong so. Neu tai lieu khong co, noi thang la chua co thong tin model do.\n"
    "- Ve gia thue va tinh trang con hang thi luon moi khach lien he Snappro.\n"
    "- Tra loi bang tieng Viet co dau, than thien, ngan gon.\n"
    "DINH DANG: van ban thuan, KHONG dung Markdown, khong dung # ## ** __ * ``` --- |. "
    "Chi dung so (1. 2. 3.) va gach (-) de liet ke. Co the dung emoji de lam noi bat.\n"
)

STOP = {
    "thi", "co", "la", "va", "cho", "cua", "bao", "nhieu", "nhu", "the", "nao",
    "duoc", "cai", "mot", "minh", "ban", "voi", "khi", "nay", "sao", "khong",
    "dung", "loai", "dong", "ma", "cay", "can", "gia", "tien", "muc", "tui",
    "den", "may", "toi", "cac", "vua", "khoe", "dang", "muon", "huong", "dan",
    "ket", "noi", "chia", "nhom", "tren", "voi", "cach",
    "the", "and", "for", "what", "how", "which", "are", "with", "that",
}

# Từ khoá quan trọng cần ưu tiên tìm chính xác
PRIORITY_TERMS = {"nanlink", "bluetooth", "ws-tb-1", "wstb1", "group", "scene", "2.4g"}


def _terms(question):
    q = question.lower()
    # Chuẩn hoá tiếng Việt không dấu để match
    vn_map = str.maketrans("àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ",
                           "aaaaaaaaaaaaaaadeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyy")
    q_norm = q.translate(vn_map)

    raw = re.findall(r"[a-z0-9]+", q_norm)
    base = [t for t in raw if t not in STOP and (len(t) >= 3 or t.isdigit())]

    merges = []
    for i in range(len(raw) - 1):
        a, b = raw[i], raw[i + 1]
        if a.isalpha() and 1 <= len(a) <= 6 and any(ch.isdigit() for ch in b):
            merges += [a + b, a + "-" + b]

    ordered = []
    for t in merges + base:
        if t not in ordered:
            ordered.append(t)
    return ordered, set(base) | set(merges)


def get_context(question):
    terms, qset = _terms(question)
    if not terms:
        return ""

    pool = {}
    # Chỉ dùng tối đa 4 terms, mỗi term lấy tối đa 8 rows (thay vì 20)
    for term in terms[:4]:
        for col in ("content", "model"):
            try:
                r = (supabase.table("sony_manuals")
                     .select("model,heading,content")
                     .ilike(col, f"%{term}%")
                     .limit(8).execute())
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
    top = [r for r in ranked if score(r) > 0][:6] or ranked[:4]

    ctx = "TAI LIEU THIET BI (trich tu manual hang):\n"
    for i, r in enumerate(top, 1):
        model = r.get("model", "") or ""
        heading = r.get("heading", "") or ""
        body = (r.get("content", "") or "")[:500]
        ctx += f"[{i}] {model} - {heading}\n{body}\n---\n"
    return ctx


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    question = update.message.text or update.message.caption or "Thiet bi nay la gi?"
    await update.message.chat.send_action("typing")

    manual_ctx = get_context(question)
    history = chat_history.get(uid, [])
    msgs = list(history)
    content = f"{manual_ctx}\nCau hoi: {question}" if manual_ctx else question
    msgs.append({"role": "user", "content": content})

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-6", max_tokens=800, system=SYSTEM, messages=msgs
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
        await update.message.reply_text("Xin loi, co su co. Thu lai sau nhe!")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history[update.effective_user.id] = []
    await update.message.reply_text(
        "Xin chao! Minh la tro ly ky thuat Snappro\n\n"
        "Minh co the giup ban ve thiet bi Snappro cho thue:\n"
        "- May anh Sony, Canon, Fujifilm\n"
        "- Gimbal DJI, mic, flycam\n"
        "- Den Nanlite, Nanlux, Aputure, Amaran, Godox\n"
        "- Thong so, cach dung, ngam, phu kien, xu ly loi\n"
        "- Ket noi NanLink App, tao group den\n\n"
        "Hoi minh bat cu dieu gi nhe!"
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history[update.effective_user.id] = []
    await update.message.reply_text("Da xoa lich su chat! Ban co the bat dau lai tu dau")


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
