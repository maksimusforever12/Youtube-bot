import os
import re
import math
import logging
from typing import Optional, List

import yt_dlp
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Настройки
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN_HERE"
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
CHUNK_SIZE = 1.9 * 1024 * 1024 * 1024  # 1.9 GB для безопасности
DOWNLOAD_DIR = "downloads"

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Создаем директорию для загрузок
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def is_youtube_url(url: str) -> bool:
    """Проверка валидности YouTube URL"""
    patterns = [
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/',
        r'(https?://)?youtu\.be/',
        r'(https?://)?m\.youtube\.com/',
        r'(https?://)?gaming\.youtube\.com/'
    ]
    return any(re.match(pattern, url) for pattern in patterns)

def format_duration(seconds: int) -> str:
    """Форматирование длительности в читаемый вид"""
    if not seconds:
        return "Неизвестно"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"

def format_filesize(size_bytes: int) -> str:
    """Форматирование размера файла"""
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"

def progress_hook(d):
    """Простой progress hook для yt-dlp"""
    if d['status'] == 'downloading':
        try:
            if 'total_bytes' in d and d['total_bytes']:
                percent = int(d['downloaded_bytes'] / d['total_bytes'] * 100)
                if percent % 25 == 0:  # Показываем каждые 25%
                    print(f"📥 Загружено: {percent}%")
        except:
            pass

def get_video_info(url: str) -> Optional[dict]:
    """Получение информации о видео"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.error(f"Ошибка получения информации о видео: {e}")
        return None

def download_video(url: str, chat_id: int) -> tuple[Optional[str], int]:
    """Загрузка видео с YouTube"""
    
    # Создаем уникальное имя файла
    output_template = os.path.join(DOWNLOAD_DIR, f'video_{chat_id}_%(title)s.%(ext)s')
    
    # Настройки для yt-dlp с приоритетом HD/2K качества
    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[height>=720][height<=1440]+bestaudio/best[height>=720][height<=1440]/best',
        'merge_output_format': 'mp4',
        'writesubtitles': False,
        'writeautomaticsub': False,
        'ignoreerrors': False,
        'progress_hooks': [progress_hook],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Загружаем видео
            ydl.download([url])
            
            # Находим загруженный файл
            for file in os.listdir(DOWNLOAD_DIR):
                if file.startswith(f'video_{chat_id}_') and file.endswith('.mp4'):
                    filepath = os.path.join(DOWNLOAD_DIR, file)
                    filesize = os.path.getsize(filepath)
                    return filepath, filesize
            
            return None, 0
            
    except Exception as e:
        logger.error(f"Ошибка загрузки видео: {e}")
        return None, 0

def split_file(filepath: str, chat_id: int) -> List[str]:
    """Разделение файла на части"""
    parts = []
    part_num = 1
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    
    try:
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(int(CHUNK_SIZE))
                if not chunk:
                    break
                
                part_filename = os.path.join(DOWNLOAD_DIR, f"{base_name}_part{part_num:02d}.mp4")
                with open(part_filename, "wb") as part_file:
                    part_file.write(chunk)
                
                parts.append(part_filename)
                part_num += 1
                
        logger.info(f"Файл разделен на {len(parts)} частей")
        return parts
        
    except Exception as e:
        logger.error(f"Ошибка разделения файла: {e}")
        # Очищаем созданные части при ошибке
        for part in parts:
            if os.path.exists(part):
                os.remove(part)
        return []

def cleanup_files(*filepaths: str):
    """Безопасная очистка файлов"""
    for filepath in filepaths:
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Удален файл: {filepath}")
        except Exception as e:
            logger.error(f"Ошибка удаления файла {filepath}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    welcome_text = (
        "🎬 *YouTube Downloader Bot*\n\n"
        "📋 *Возможности:*\n"
        "• Скачивание видео в HD/2K качестве\n"
        "• Поддержка длинных видео (2\\+ часа)\n"
        "• Автоматическое разделение больших файлов\n"
        "• Быстрая и стабильная загрузка\n\n"
        "📝 *Как использовать:*\n"
        "Просто отправьте ссылку на YouTube видео\\!"
    )
    await update.message.reply_text(welcome_text, parse_mode='MarkdownV2')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = (
        "🆘 *Помощь*\n\n"
        "*Команды:*\n"
        "/start \\- Запуск бота\n"
        "/help \\- Эта справка\n\n"
        "*Поддерживаемые форматы ссылок:*\n"
        "• youtube\\.com/watch?v=\\.\\.\\.\n"
        "• youtu\\.be/\\.\\.\\.\n"
        "• m\\.youtube\\.com/\\.\\.\\.\n\n"
        "*Параметры загрузки:*\n"
        "• Качество: HD \\(720p\\) \\- 2K \\(1440p\\)\n"
        "• Максимальный размер части: 1\\.9 ГБ\n"
        "• Поддержка видео любой длительности"
    )
    await update.message.reply_text(help_text, parse_mode='MarkdownV2')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений с YouTube ссылками"""
    chat_id = update.message.chat_id
    url = update.message.text.strip()
    
    # Проверяем валидность URL
    if not is_youtube_url(url):
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте корректную ссылку на YouTube видео.\n"
            "Пример: https://youtube.com/watch?v=..."
        )
        return
    
    # Отправляем сообщение о начале обработки
    status_msg = await update.message.reply_text("🔍 Анализирую видео...")
    
    try:
        # Получаем информацию о видео
        video_info = get_video_info(url)
        if not video_info:
            await status_msg.edit_text("❌ Не удалось получить информацию о видео.")
            return
        
        title = video_info.get('title', 'Unknown')
        if len(title) > 50:
            title = title[:50] + '...'
        duration = video_info.get('duration', 0)
        uploader = video_info.get('uploader', 'Unknown')
        
        info_text = (
            f"📹 *{title.replace('*', '\\*').replace('_', '\\_').replace('`', '\\`')}*\n"
            f"👤 {uploader.replace('*', '\\*').replace('_', '\\_').replace('`', '\\`')}\n"
            f"⏱️ {format_duration(duration)}\n\n"
            f"🎬 Начинаю загрузку\\.\\.\\."
        )
        await status_msg.edit_text(info_text, parse_mode='MarkdownV2')
        
        # Загружаем видео
        filepath, filesize = download_video(url, chat_id)
        
        if not filepath or not os.path.exists(filepath):
            await status_msg.edit_text("❌ Ошибка загрузки видео. Попробуйте позже.")
            return
        
        logger.info(f"Загружен файл: {filepath}, размер: {format_filesize(filesize)}")
        
        # Проверяем размер файла
        if filesize <= MAX_FILE_SIZE:
            # Отправляем как один файл
            await status_msg.edit_text(f"📤 Отправляю файл ({format_filesize(filesize)})...")
            
            with open(filepath, 'rb') as video_file:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=video_file,
                    filename=os.path.basename(filepath),
                    caption=f"🎬 {video_info.get('title', 'video')}"
                )
            
            cleanup_files(filepath)
            await status_msg.delete()
            
        else:
            # Файл слишком большой - предлагаем разделить
            keyboard = [
                [
                    InlineKeyboardButton("✅ Да, разделить", callback_data="split_yes"),
                    InlineKeyboardButton("❌ Нет, отменить", callback_data="split_no")
                ]
            ]
            
            await status_msg.edit_text(
                f"⚠️ *Файл слишком большой*\n\n"
                f"📁 Размер: {format_filesize(filesize)}\n"
                f"📏 Будет разделен на ~{math.ceil(filesize / CHUNK_SIZE)} частей\n\n"
                f"Разделить файл на части?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
            # Сохраняем путь к файлу в контексте
            context.user_data["filepath"] = filepath
            context.user_data["title"] = video_info.get('title', 'video')
    
    except Exception as e:
        logger.error(f"Общая ошибка обработки: {e}")
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")
        # Очищаем файлы при ошибке
        if 'filepath' in locals() and filepath:
            cleanup_files(filepath)

async def handle_split_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка callback для разделения файла"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    filepath = context.user_data.get("filepath")
    title = context.user_data.get("title", "video")
    
    if query.data == "split_yes":
        if not filepath or not os.path.exists(filepath):
            await query.edit_message_text("❌ Файл не найден.")
            return
        
        await query.edit_message_text("✂️ Разделяю файл на части...")
        
        try:
            # Разделяем файл
            parts = split_file(filepath, chat_id)
            
            if not parts:
                await query.edit_message_text("❌ Ошибка разделения файла.")
                cleanup_files(filepath)
                return
            
            # Отправляем части
            await query.edit_message_text(f"📤 Отправляю {len(parts)} частей...")
            
            for i, part_path in enumerate(parts, 1):
                try:
                    with open(part_path, 'rb') as part_file:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=part_file,
                            filename=os.path.basename(part_path),
                            caption=f"🎬 {title} - Часть {i}/{len(parts)}"
                        )
                except Exception as e:
                    logger.error(f"Ошибка отправки части {i}: {e}")
            
            # Очищаем файлы
            cleanup_files(filepath, *parts)
            
            await query.edit_message_text(
                f"✅ *Загрузка завершена!*\n"
                f"📁 Отправлено частей: {len(parts)}",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Ошибка разделения/отправки: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)}")
            cleanup_files(filepath)
    
    else:  # split_no
        if filepath:
            cleanup_files(filepath)
        await query.edit_message_text("❌ Загрузка отменена.")
    
    # Очищаем данные пользователя
    context.user_data.clear()

def main():
    """Главная функция"""
    if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ОШИБКА: Установите токен бота в переменной TELEGRAM_TOKEN")
        print("Получить токен можно у @BotFather в Telegram")
        return
    
    try:
        # Создаем приложение
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Добавляем обработчики
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(CallbackQueryHandler(handle_split_callback))
        
        # Запускаем бота
        print("🚀 Бот запущен...")
        print("Для остановки нажмите Ctrl+C")
        app.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        print(f"❌ Ошибка запуска: {e}")

if __name__ == "__main__":
    main()
