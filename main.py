python3 << 'PYEOF'
code = """import os, re, logging
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
    "Ban la tro ly ky thuat chuyen nghiep cua Snappro - shop cho thue may anh Sony.\\n"
    "DINH DANG BAT BUOC:\\n"
    "- Viet van ban thuan tuy, KHONG dung Markdown\\n"
    "- KHONG dung cac ky hieu: # ## ### ** __ * _ ``` --- |\\n"
    "- Chi dung so (1. 2. 3.) va gach (-) de liet ke\\n"
    "- Dung emoji de lam noi bat\\n"
    "- Tra loi bang tieng Viet co dau, than thien, ngan gon\\n"
    "- Neu co manual Sony duoc cung cap, su dung thong tin do de tra loi chinh xac\\n"
)

def search_manual(query):
    try:
        r = supabase.table("sony_manuals").select("model,title,heading,content,url").ilike("content", f"%{query}%").limit(4).execute()
        return r.data or []
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

def get_context(question):
    words = [w for w in re.findall(r"\\w+", question.lower()) if len(w) > 3]
    results, seen = [], set()
    for w in words[:3]:
        for r in search_manual(w):
            if r.get("url") not in seen:
                seen.add(r.get("url"))
                results.append(r)
    if not results:
        return ""
    ctx = "MANUAL SONY:\\n"
    for i, r in enumerate(results[:5], 1):
        ctx += f"[{i}] {r.get('model','')} - {r.get('heading','')}\\n{r.get('content','')[:500]}\\n---\\n"
    return ctx

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    question = update.message.text or update.message.caption or "Thiet bi nay la gi?"
    await update.message.chat.send_action("typing")
    manual_ctx = get_context(question)
    history = chat_history.get(uid, [])
    msgs = list(history)
    content = f"{manual_ctx}\\nCau hoi: {question}" if manual_ctx else question
    msgs.append({"role": "user", "content": content})
    try:
        resp = claude.messages.create(model="claude-sonnet-4-6", max_tokens=1024, system=SYSTEM, messages=msgs)
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
        "Xin chao! Minh la tro ly ky thuat Snappro 📷\\n\\n"
        "Minh co the giup ban:\\n"
        "- Huong dan su dung may anh Sony\\n"
        "- Cai dat tinh nang AF, ISO, White Balance\\n"
        "- Ket noi Bluetooth, WiFi, gimbal\\n"
        "- Setup quay video 4K, slow motion\\n"
        "- Xu ly loi thiet bi\\n\\n"
        "Hoi minh bat cu dieu gi nhe! 🚀"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history[update.effective_user.id] = []
    await update.message.reply_text("Da xoa lich su chat! Ban co the bat dau lai tu dau 🗑️")

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
"""
open('/Users/admin/sony-bot/main.py', 'w').write(code)
print("Xong!")
PYEOF
