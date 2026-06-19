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
Snappro rents: Sony/Canon/Nikon/Fujifilm cameras, DJI gimbals/drones, mics, Nanlite/Nanlux/Aputure/Amaran/Godox lights.

ANSWERING RULES:
- When MANUAL DATA is provided below, use ONLY that data to answer. Quote exact numbers (wattage, battery, specs).
- The manual data is from official manufacturer PDFs - trust it completely.
- NEVER mix up different models. If asked about "PavoTube II 30C", only use data labeled "30C", not "15C" or others.
- If manual data is not provided or unclear, say you don't have that specific info - do NOT guess or invent specs.
- For rental prices and stock availability: always direct customer to contact Snappro directly.

PRODUCT NAME CLARIFICATION (very important):
- Customers often use incorrect or incomplete product names. When you detect this, gently clarify and redirect.
- Common confusions to watch for:
  * "ống đèn / tube / đèn ống" -> likely PavoTube series (Nanlite)
  * "evoke" without wattage -> ask which wattage: 150C, 600C, 900C, 1200, 1200B, 2400B, 5000B (Nanlux)
  * Missing "II" in model -> PavoTube II is different from PavoTube (gen 1)
  * "nanlite 30C" -> likely "Nanlite PavoTube II 30C" (not a standalone model called "30C")
  * "30C II" / "15C II" -> likely "PavoTube II 30C" / "PavoTube II 15C" (order is reversed)
  * Confusing Nanlite vs Nanlux: Evoke/Dyno/Matrix/TK = Nanlux; Forza/FS/FC/PavoTube/PavoSlim = Nanlite
  * "RS4" without brand -> DJI RS 4 gimbal
  * Missing suffix B/C: Evoke 2400 -> Evoke 2400B (Bi-color); always clarify which variant
- When clarifying, say: "Ban dang hoi ve [TEN DUNG] phai khong? Day la thong tin..."
- If still unclear after clarifying, list the possible matching products and ask customer to confirm.

RESPONSE FORMAT:
- Reply in Vietnamese with diacritics (tieng Viet co dau). Be friendly and concise.
- Plain text only. No Markdown (no #, **, __, *, ---, |).
- Use numbers (1. 2. 3.) and dashes (-) for lists. Emoji ok."""

STOP = {
    "thi","co","la","va","cho","cua","bao","nhieu","nhu","the","nao","duoc","cai",
    "mot","minh","ban","voi","khi","nay","sao","khong","dung","loai","dong","ma",
    "can","gia","tien","muc","den","may","toi","cac","vua","dang","muon","tren",
    "cach","hai","and","for","what","how","which","are","with","that","the",
    "ong","tube","cong","suat","bao","nhieu","watts","power","pin","battery",
}

def _keywords(question):
    vn  = "aaaaaaaaaaaaaaaaadeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyy"
    src = "àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ"
    q = question.lower().translate(str.maketrans(src, vn))
    raw = re.findall(r"[a-z0-9]+", q)

    merges = []
    for i in range(len(raw)-1):
        a, b = raw[i], raw[i+1]
        if len(a) >= 2 and len(b) >= 2:
            merges.append(a + b)
        if a.isalpha() and 1 <= len(a) <= 8 and any(c.isdigit() for c in b):
            merges += [a + b, a + "-" + b]

    base = [t for t in raw if t not in STOP and (len(t) >= 2 or t.isdigit())]
    seen, ordered = set(), []
    for t in merges + base:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered

def search_ilike(term, limit=8):
    results = []
    for col in ("content", "model", "title"):
        try:
            r = (supabase.table("sony_manuals")
                 .select("model,heading,content,title")
                 .ilike(col, f"%{term}%")
                 .limit(limit).execute())
            results += (r.data or [])
        except Exception as e:
            logger.error(f"Search error ({col}/{term}): {e}")
    return results

def get_context(question):
    keywords = _keywords(question)
    if not keywords:
        return ""

    pool, seen_keys = {}, set()
    kw_set = set(keywords)

    # Uu tien tu dai (ma model cu the) truoc
    sorted_kw = sorted(keywords[:6], key=lambda x: (-len(x), x))

    for kw in sorted_kw:
        for row in search_ilike(kw, limit=6):
            key = (row.get("content","") or "")[:120]
            if key and key not in seen_keys:
                seen_keys.add(key)
                pool[key] = row

    if not pool:
        return ""

    def score(row):
        txt = " ".join([
            row.get("content","") or "",
            row.get("model","") or "",
            row.get("title","") or "",
        ]).lower()
        s = 0
        for k in kw_set:
            if k in txt:
                s += 1 + len(k) * 0.1
        return s

    ranked = sorted(pool.values(), key=score, reverse=True)
    top = [r for r in ranked if score(r) > 0][:6] or ranked[:4]

    ctx = "MANUAL DATA (from manufacturer PDF):\n"
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
        "- May anh Sony, Canon, Nikon, Fujifilm\n"
        "- Gimbal DJI, mic, flycam\n"
        "- Den Nanlite, Nanlux, Aputure, Amaran, Godox\n"
        "- NanLink App, DMX, ket noi khong day\n"
        "- Tu van thue thiet bi theo nhu cau\n\n"
        "Cu hoi thang, minh se tim trong manual hang de tra loi chinh xac! \U0001f680"
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
