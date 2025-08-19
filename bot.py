import os
import zipfile
from pytube import YouTube
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
from tqdm import tqdm

TELEGRAM_TOKEN = "YOUR_BOT_TOKEN_HERE"
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB

# --- Меню ---
main_menu = [["/start", "/help", "/cancel"]]

# --- Скачивание видео с прогрессбаром ---
def download_video(link, chat_id):
    yt = YouTube(link)

    # выбираем лучшее качество (HD или 2K)
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

# --- Архивация ---
def make_zip(filename, chat_id):
    zip_filename = f"video_{chat_id}.zip"
    with zipfile.ZipFile(zip_filename, "w") as zipf:
        zipf.write(filename, os.path.basename(filename))
    return zip_filename

# --- Разделение больших файлов ---
def split_file(filename, chat_id):
    parts = []
    part_num = 1
    with open(filename, "rb") as f:
        while True:
            chunk = f.read(MAX_FILE_SIZE - 10 * 1024 * 1024)  # запас 10МБ
            if not chunk:
                break
            part_filename = f"video_{chat_id}_part{part_num}.mp4"
            with open(part_filename, "wb") as part_file:
                part_file.write(chunk)
            parts.append(part_filename)
            part_num += 1
    return parts

# --- Команды ---
def start(update, context):
    update.message.reply_text(
        "Привет! 🎬 Отправь мне ссылку на YouTube, и я скачаю видео.\n\n"
        "Если файл слишком большой, я предложу разделить его.",
        reply_markup=ReplyKeyboardMarkup(main_menu, resize_keyboard=True)
    )

def help_cmd(update, context):
    update.message.reply_text(
        "📌 Доступные команды:\n"
        "/start - начать\n"
        "/help - помощь\n"
        "/cancel - отменить"
    )

def cancel(update, context):
    update.message.reply_text("❌ Действие отменено.", reply_markup=ReplyKeyboardRemove())

# --- Обработка ссылки ---
def handle_message(update, context):
    chat_id = update.message.chat_id
    link = update.message.text.strip()

    update.message.reply_text("⏳ Скачиваю видео, подожди...")

    try:
        filename, filesize = download_video(link, chat_id)

        if filesize <= MAX_FILE_SIZE:
            # Если помещается — архивируем
            zip_filename = make_zip(filename, chat_id)
            with open(zip_filename, "rb") as f:
                context.bot.send_document(chat_id=chat_id, document=f, filename=zip_filename)
            os.remove(filename)
            os.remove(zip_filename)
        else:
            # Если слишком большое — спрашиваем через inline кнопки
            keyboard = [
                [
                    InlineKeyboardButton("✅ Да, разделить", callback_data="split_yes"),
                    InlineKeyboardButton("❌ Нет, отмена", callback_data="split_no")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            update.message.reply_text(
                "⚠️ Видео слишком большое для отправки целиком.\n"
                "Хочешь, я разделю его на части по 2 ГБ и отправлю?",
                reply_markup=reply_markup
            )
            context.user_data["filename"] = filename

    except Exception as e:
        update.message.reply_text(f"❌ Ошибка: {e}")

# --- Callback для inline кнопок ---
def ask_split(update, context):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    filename = context.user_data.get("filename")

    if query.data == "split_yes":
        parts = split_file(filename, chat_id)
        for part in parts:
            with open(part, "rb") as f:
                context.bot.send_document(chat_id=chat_id, document=f, filename=part)
            os.remove(part)
        os.remove(filename)
        query.edit_message_text("✅ Видео отправлено частями.", reply_markup=None)

    elif query.data == "split_no":
        os.remove(filename)
        query.edit_message_text("❌ Видео слишком большое, отменено.", reply_markup=None)

# --- Основная функция ---
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_cmd))
    dp.add_handler(CommandHandler("cancel", cancel))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(CallbackQueryHandler(ask_split))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
