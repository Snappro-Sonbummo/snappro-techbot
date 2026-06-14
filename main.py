import os
import base64
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")

SONY_MODELS = {
    "a7iv": "2110", "a7 iv": "2110", "a7m4": "2110",
    "a7cii": "2310", "a7c ii": "2310",
    "a6700": "2350", "a7siii": "2030",
    "a7rv": "2410", "a9iii": "2450",
    "zv-e10": "2140", "zv-e1": "2330",
    "fx3": "2070", "fx30": "2360",
}

WELCOME_MESSAGE = """👋 Xin chào! Mình là *SNAPPRO Hỗ Trợ Kỹ Thuật* 🤖

Mình có thể giúp bạn:
📷 Hướng dẫn setup máy ảnh Sony
⚡ Kết nối flash, trigger Godox
🎥 Cài đặt quay 4K, slow motion
🔧 Xử lý lỗi thiết bị

Để hỗ trợ chính xác, bạn cho mình biết:
1️⃣ *Dòng máy* đang dùng (VD: Sony A7IV...)
2️⃣ *Thiết bị kèm theo* nếu có (flash, trigger...)
3️⃣ *Vấn đề* đang gặp phải

Hoặc *chụp ảnh thiết bị* gửi lên nếu không nhớ tên! 📸"""

async def fetch_sony_helpguide(model_code):
    try:
        url = f"https://helpguide.sony.net/ilc/{model_code}/v1/en/index.html"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text[:8000]
    except:
        pass
    return ""

async def ask_claude(user_message, image_data=None, image_type=None):
    sony_code = None
    for keyword, code in SONY_MODELS.items():
        if keyword in user_message.lower():
            sony_code = code
            break

    helpguide = ""
    if sony_code:
        helpguide = await fetch_sony_helpguide(sony_code)

    system = """Bạn là chuyên gia kỹ thuật của SnapPro - cho thuê thiết bị quay phim chụp ảnh tại HCM.
Trả lời bằng tiếng Việt, thân thiện, hướng dẫn từng bước rõ ràng.
Nếu khách gửi ảnh, hãy nhận diện thiết bị từ ảnh và tư vấn cách sử dụng/kết nối.
Nếu chưa đủ thông tin, hỏi lại: dòng máy gì, thiết bị kèm theo, vấn đề cụ thể?
""" + (f"\nTài liệu Sony:\n{helpguide[:4000]}" if helpguide else "")

    if image_data:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": image_type, "data": image_data}},
            {"type": "text", "text": user_message or "Nhận diện thiết bị trong ảnh và hướng dẫn kết nối/sử dụng?"}
        ]
    else:
        content = user_message

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1000, "system": system, "messages": [{"role": "user", "content": content}]}
        )
        return r.json()["content"][0]["text"]

async def download_and_encode(file_obj, context):
    file = await context.bot.get_file(file_obj.file_id)
    async with httpx.AsyncClient() as client:
        img = await client.get(file.file_path)
        return base64.b64encode(img.content).decode()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text or ""
    if any(g in msg.lower() for g in ["hi","hello","xin chào","chào","/start"]) and len(msg) < 20:
        await update.message.reply_text(WELCOME_MESSAGE, parse_mode="Markdown")
        return
    await update.message.reply_text("⏳ Đang tìm kiếm thông tin...")
    try:
        reply = await ask_claude(msg)
        await update.message.reply_text(reply)
    except:
        await update.message.reply_text("❌ Xin lỗi, mình đang gặp sự cố. Vui lòng thử lại sau!")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Đang phân tích ảnh thiết bị...")
    try:
        photo = update.message.photo[-1]
        image_data = await download_and_encode(photo, context)
        caption = update.message.caption or ""
        reply = await ask_claude(caption, image_data, "image/jpeg")
        await update.message.reply_text(reply)
    except:
        await update.message.reply_text("❌ Không đọc được ảnh. Bạn thử mô tả bằng text nhé!")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("📎 Mình chỉ đọc được file ảnh thôi! Bạn thử gửi ảnh trực tiếp hoặc mô tả bằng text.")
        return
    await update.message.reply_text("📸 Đang phân tích ảnh thiết bị...")
    try:
        image_data = await download_and_encode(doc, context)
        caption = update.message.caption or ""
        reply = await ask_claude(caption, image_data, doc.mime_type)
        await update.message.reply_text(reply)
    except:
        await update.message.reply_text("❌ Không đọc được ảnh. Bạn thử mô tả bằng text nhé!")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    print("Bot đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
