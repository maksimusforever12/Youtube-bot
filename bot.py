import os
import zipfile
from pytube import YouTube
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from tqdm import tqdm

TELEGRAM_TOKEN = "YOUR_BOT_TOKEN_HERE"
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB

def download_video(link, chat_id):
    yt = YouTube(link)
    stream = yt.streams.filter(progressive=True, file_extension="mp4", res="1440p").first()
    if not stream:
        stream = yt.streams.filter(progressive=True, file_extension="mp4", res="1080p").first()
    if not stream:
        stream = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc().first()

    filesize = stream.filesize
    filename = f"video_{chat_id}.mp4"

    with tqdm(total=filesize, unit='B', unit_scale=True, desc="Downloading") as pbar:
        def progress(stream, chunk, bytes_remaining):
            pbar.update(len(chunk))

        yt.register_on_progress_callback(progress)
        stream.download(filename=filename)

    return filename, filesize

def make_zip(filename, chat_id):
    zip_filename = f"video_{chat_id}.zip"
    with zipfile.ZipFile(zip_filename, "w") as zipf:
        zipf.write(filename, os.path.basename(filename))
    return zip_filename

def split_file(filename, chat_id):
    parts = []
    part_num = 1
    with open(filename, "rb") as f:
        while True:
            chunk = f.read(MAX_FILE_SIZE - 10 * 1024 * 1024)
            if not chunk:
                break
            part_filename = f"video_{chat_id}_part{part_num}.mp4"
            with open(part_filename, "wb") as part_file:
                part_file.write(chunk)
            parts.append(part_filename)
            part_num += 1
    return parts

async def start(update, context):
    await update.message.reply_text("Привет! 🎬 Отправь ссылку на YouTube.")

async def help_cmd(update, context):
    await update.message.reply_text("📌 Команды: /start /help")

async def handle_message(update, context):
    chat_id = update.message.chat_id
    link = update.message.text.strip()
    await update.message.reply_text("⏳ Скачиваю видео...")

    try:
        filename, filesize = download_video(link, chat_id)

        if filesize <= MAX_FILE_SIZE:
            zip_filename = make_zip(filename, chat_id)
            await context.bot.send_document(chat_id=chat_id, document=open(zip_filename, "rb"))
            os.remove(filename)
            os.remove(zip_filename)
        else:
            keyboard = [[
                InlineKeyboardButton("✅ Да", callback_data="split_yes"),
                InlineKeyboardButton("❌ Нет", callback_data="split_no")
            ]]
            await update.message.reply_text("⚠️ Видео слишком большое, разделить?", reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data["filename"] = filename

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def ask_split(update, context):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    filename = context.user_data.get("filename")

    if query.data == "split_yes":
        parts = split_file(filename, chat_id)
        for part in parts:
            await context.bot.send_document(chat_id=chat_id, document=open(part, "rb"))
            os.remove(part)
        os.remove(filename)
        await query.edit_message_text("✅ Отправлено частями.")
    else:
        os.remove(filename)
        await query.edit_message_text("❌ Отменено.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(ask_split))

    app.run_polling()

if __name__ == "__main__":
    main()
