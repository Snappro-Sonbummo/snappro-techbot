import os
import base64
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")

# Lưu lịch sử chat theo chat_id
chat_histories = {}
MAX_HISTORY = 10  # Giữ tối đa 10 tin nhắn gần nhất

SONY_MODELS = {
    "a7iv": "2110", "a7 iv": "2110", "a7m4": "2110",
    "a7cii": "2310", "a7c ii": "2310",
    "a6700": "2350", "a7siii": "2030",
    "a7rv": "2410", "a9iii": "2450",
    "zv-e10": "2140", "zv-e1": "2330",
    "fx3": "2070", "fx30": "2360",
    "a6400": "1920", "a6500": "1950",
    "a73": "1710", "a7 iii": "1710",
    "a7r3": "1820", "a7r iii": "1820",
}

BRAND_SEARCH = {
    "canon": "https://www.usa.canon.com/support",
    "nikon": "https://www.nikon.com/support",
    "fujifilm": "https://fujifilm-x.com/support/",
    "dji": "https://www.dji.com/support",
    "godox": "https://www.godox.com/support.html",
}

WELCOME_MESSAGE = """👋 Xin chào! Mình là *SNAPPRO Hỗ Trợ Kỹ Thuật* 🤖

Mình có thể giúp bạn:
📷 Hướng dẫn setup máy ảnh Sony, Canon, Nikon, Fujifilm
⚡ Kết nối flash, trigger Godox
🎥 Cài đặt quay 4K, slow motion
🔧 Xử lý lỗi thiết bị
🎬 Setup gimbal DJI

Để hỗ trợ chính xác, bạn cho mình biết:
1️⃣ *Dòng máy* đang dùng (VD: Sony A7IV, Canon R6...)
2️⃣ *Thiết bị kèm theo* nếu có (flash, trigger, gimbal...)
3️⃣ *Vấn đề* đang gặp phải

Hoặc *chụp ảnh thiết bị* gửi lên nếu không nhớ tên! 📸

💡 Mình nhớ toàn bộ cuộc trò chuyện — bạn không cần nhắc lại thông tin đã nói!"""

async def fetch_sony_helpguide(model_code):
    try:
        url = f"https://helpguide.sony.net/ilc/{model_code}/v1/en/index.html"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text[:6000]
    except:
        pass
    return ""

async def fetch_brand_support(brand, query):
    try:
        url = BRAND_SEARCH.get(brand, "")
        if not url:
            return ""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text[:4000]
    except:
        pass
    return ""

def get_history(chat_id):
    return chat_histories.get(chat_id, [])

def add_to_history(chat_id, role, content):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    chat_histories[chat_id].append({"role": role, "content": content})
    # Giữ tối đa MAX_HISTORY tin nhắn
    if len(chat_histories[chat_id]) > MAX_HISTORY * 2:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY * 2:]

def clear_history(chat_id):
    chat_histories[chat_id] = []

async def ask_claude(chat_id, user_message, image_data=None, image_type=None):
    # Xác định brand/model từ toàn bộ lịch sử
    full_context = " ".join([m["content"] if isinstance(m["content"], str) else "" 
                             for m in get_history(chat_id)] + [user_message]).lower()
    
    sony_code = None
    for keyword, code in SONY_MODELS.items():
        if keyword in full_context:
            sony_code = code
            break

    brand = None
    for b in BRAND_SEARCH.keys():
        if b in full_context:
            brand = b
            break

    # Fetch tài liệu kỹ thuật
    helpguide = ""
    if sony_code:
        helpguide = await fetch_sony_helpguide(sony_code)
    elif brand and brand != "sony":
        helpguide = await fetch_brand_support(brand, user_message)

    system = """Bạn là chuyên gia kỹ thuật của SnapPro - dịch vụ cho thuê thiết bị quay phim chụp ảnh tại HCM.

QUAN TRỌNG: Bạn có thể nhớ toàn bộ lịch sử cuộc trò chuyện. Hãy sử dụng thông tin khách đã cung cấp trước đó (dòng máy, thiết bị, vấn đề) để trả lời chính xác mà không cần hỏi lại.

Nhiệm vụ:
- Trả lời bằng tiếng Việt, thân thiện, hướng dẫn từng bước rõ ràng
- Nếu khách gửi ảnh, nhận diện thiết bị và tư vấn cách sử dụng/kết nối
- Nếu câu hỏi liên quan nhiều thiết bị, hãy kết nối thông tin từ các thiết bị đó
- Nếu THỰC SỰ không có đủ thông tin, mới hỏi lại — đừng hỏi thông tin khách đã cung cấp
- Nếu không chắc chắn về thông số kỹ thuật, hãy nói thật thay vì đoán mò
""" + (f"\nTài liệu kỹ thuật tham khảo:\n{helpguide[:4000]}" if helpguide else "")

    # Chuẩn bị tin nhắn hiện tại
    if image_data:
        current_content = [
            {"type": "image", "source": {"type": "base64", "media_type": image_type, "data": image_data}},
            {"type": "text", "text": user_message or "Nhận diện thiết bị trong ảnh và hướng dẫn kết nối/sử dụng?"}
        ]
    else:
        current_content = user_message

    # Lấy lịch sử + thêm tin nhắn hiện tại
    history = get_history(chat_id)
    messages = history + [{"role": "user", "content": current_content}]

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1000,
                "system": system,
                "messages": messages
            }
        )
        response_text = r.json()["content"][0]["text"]
    
    # Lưu vào lịch sử
    add_to_history(chat_id, "user", user_message or "[Gửi ảnh thiết bị]")
    add_to_history(chat_id, "assistant", response_text)
    
    return response_text

async def download_and_encode(file_obj, context):
    file = await context.bot.get_file(file_obj.file_id)
    async with httpx.AsyncClient() as client:
        img = await client.get(file.file_path)
        return base64.b64encode(img.content).decode()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    msg = update.message.text or ""
    
    # Reset history
    if msg.lower() in ["/start", "/reset", "/new"]:
        clear_history(chat_id)
        await update.message.reply_text(WELCOME_MESSAGE, parse_mode="Markdown")
        return
    
    # Chào hỏi
    if any(g in msg.lower() for g in ["hi", "hello", "xin chào", "chào", "hey"]) and len(msg) < 20:
        clear_history(chat_id)
        await update.message.reply_text(WELCOME_MESSAGE, parse_mode="Markdown")
        return

    await update.message.reply_text("⏳ Đang tìm kiếm thông tin...")
    try:
        reply = await ask_claude(chat_id, msg)
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text("❌ Xin lỗi, mình đang gặp sự cố. Vui lòng thử lại sau!")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    await update.message.reply_text("📸 Đang phân tích ảnh thiết bị...")
    try:
        photo = update.message.photo[-1]
        image_data = await download_and_encode(photo, context)
        caption = update.message.caption or ""
        reply = await ask_claude(chat_id, caption, image_data, "image/jpeg")
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text("❌ Không đọc được ảnh. Bạn thử mô tả bằng text nhé!")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("📎 Mình chỉ đọc được file ảnh thôi! Bạn thử gửi ảnh trực tiếp hoặc mô tả bằng text.")
        return
    await update.message.reply_text("📸 Đang phân tích ảnh thiết bị...")
    try:
        image_data = await download_and_encode(doc, context)
        caption = update.message.caption or ""
        reply = await ask_claude(chat_id, caption, image_data, doc.mime_type)
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text("❌ Không đọc được ảnh. Bạn thử mô tả bằng text nhé!")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    print("Bot đang chạy với memory support...")
    app.run_polling()

if __name__ == "__main__":
    main()
