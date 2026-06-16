# encoding: utf-8
import os, re, logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
from supabase import create_client

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CLAUDE_API_KEY  = os.environ.get("CLAUDE_API_KEY", "")
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

claude   = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

chat_history = {}

SYSTEM = """You are a technical support assistant for Snappro, a camera and lighting equipment rental shop in Vietnam.
Snappro rents: Sony/Canon/Fujifilm cameras, DJI gimbals, mics, drones, Nanlite/Nanlux/Aputure/Amaran/Godox lights.

RULES:
- When MANUAL DATA is provided below, use it to answer specifically: specs, wattage, mount type, how-to steps, error fixes. Quote numbers directly.
- When no manual data is provided, use your general knowledge about cameras, lighting and photo/video gear to answer.
- NEVER invent specs or features. If truly uncertain, say so clearly.
- For rental prices and stock availability, always direct to Snappro.
- Always reply in Vietnamese with diacritics (tieng Viet co dau). Be friendly and concise.
- FORMAT: plain text only. No Markdown, no # ## ** __ * --- |. Use numbers (1. 2. 3.) and dashes (-) for lists. Emoji ok."""

STOP = {
    "thi","co","la","va","cho","cua","bao","nhieu","nhu","the","nao","duoc","cai",
    "mot","minh","ban","voi","khi","nay","sao","khong","dung","loai","dong","ma",
    "can","gia","tien","muc","den","may","toi","cac","vua","dang","muon","tren",
    "cach","hai","nay","and","for","what","how","which","are","with","that","the",
}

def _keywords(question):
    vn  = "aaaaaaaaaaaaaaaaadeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyy"
    src = "àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ"
    q = question.lower().translate(str.maketrans(src, vn))
    raw = re.findall(r"[a-z0-9]+", q)

    # Ghep ma model: "fc 720" -> "fc720", "fc-720"
    merges = []
    for i in range(len(raw)-1):
        a, b = raw[i], raw[i+1]
        if a.isalpha() and 1 <= len(a) <= 6 and any(c.isdigit() for c in b):
            merges += [a+b, a+"-"+b]

    base = [t for t in raw if t not in STOP and (len(t) >= 3 or t.isdigit())]
    seen, ordered = set(), []
    for t in merges + base:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered

def fts_query(term):
    """Full-text search dung index GIN -- nhanh hon ilike 10-50x."""
    try:
        r = (supabase.table("sony_manuals")
             .select("model,heading,content")
             .text_search("content", term, config="english")
             .limit(6).execute())
        return r.data or []
    except Exception:
        # Fallback sang ilike neu fts loi (term co ky tu dac biet)
        try:
            r = (supabase.table("sony_manuals")
                 .select("model,heading,content")
                 .ilike("content", f"%{term}%")
                 .limit(6).execute())
            return r.data or []
        except Exception as e:
            logger.error(f"Search failed ({term}): {e}")
            return []

def get_context(question):
    keywords = _keywords(question)
    if not keywords:
        return ""

    pool, seen_keys = {}, set()
    kw_set = set(keywords)

    for kw in keywords[:5]:
        for row in fts_query(kw):
            key = (row.get("content","") or "")[:100]
            if key and key not in seen_keys:
                seen_keys.add(key)
                pool[key] = row

    if not pool:
        return ""

    def score(row):
        txt = ((row.get("content","") or "") + " " + (row.get("model","") or "")).lower()
        return sum(1 for k in kw_set if k in txt)

    ranked = sorted(pool.values(), key=score, reverse=True)
    top = [r for r in ranked if score(r) > 0][:6] or ranked[:4]

    ctx = "MANUAL DATA (from manufacturer):\n"
    for i, r in enumerate(top, 1):
        model   = r.get("model","") or ""
        heading = r.get("heading","") or ""
        body    = (r.get("content","") or "")[:500]
        ctx += f"[{i}] {model} | {heading}\n{body}\n---\n"
    return ctx

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    question = update.message.text or update.message.caption or ""
    if not question.strip():
        return
    await update.message.chat.send_action("typing")

    manual_ctx = get_context(question)
    history    = chat_history.get(uid, [])
    msgs       = list(history)
    content    = f"{manual_ctx}\nCau hoi: {question}" if manual_ctx else question
    msgs.append({"role":"user","content":content})

    try:
        resp   = claude.messages.create(
            model="claude-sonnet-4-6", max_tokens=800, system=SYSTEM, messages=msgs
        )
        answer = resp.content[0].text
        chat_history.setdefault(uid, [])
        chat_history[uid].append({"role":"user",      "content": question})
        chat_history[uid].append({"role":"assistant", "content": answer})
        if len(chat_history[uid]) > 20:
            chat_history[uid] = chat_history[uid][-20:]
        await update.message.reply_text(answer[:4000])
    except Exception as e:
        logger.error(f"Claude error: {e}")
        await update.message.reply_text("Xin loi, co su co ky thuat. Thu lai sau nhe!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history[update.effective_user.id] = []
    await update.message.reply_text(
        "Xin chao! Minh la tro ly ky thuat Snappro \U0001f4f7\n\n"
        "Minh co the giup:\n"
        "- May anh Sony, Canon, Fujifilm (cai dat, the nho, ket noi...)\n"
        "- Gimbal DJI, mic, flycam\n"
        "- Den Nanlite, Nanlux, Aputure, Amaran, Godox\n"
        "- NanLink App, DMX, ket noi khong day\n"
        "- Tu van thue thiet bi theo nhu cau\n\n"
        "Cu hoi thang, minh se tim trong manual hang de tra loi chinh xac nhe! \U0001f680"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_history[update.effective_user.id] = []
    await update.message.reply_text("Da xoa lich su. Ban co the bat dau lai \U0001f5d1")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    logger.info("Snappro bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
