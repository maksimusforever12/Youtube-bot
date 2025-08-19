import os
import re
import math
import logging
from typing import Optional, List

import yt_dlp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, RegexpCommandsFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# Настройки
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
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

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

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

async def progress_hook(d, status_msg_id, chat_id):
    """Progress hook для yt-dlp с обновлением сообщения в Telegram"""
    if d['status'] == 'downloading':
        try:
            if 'total_bytes' in d and d['total_bytes']:
                percent = int(d['downloaded_bytes'] / d['total_bytes'] * 100)
                if percent % 10 == 0:  # Обновляем каждые 10% для снижения нагрузки
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        text=f"📥 Загружено: {percent}%"
                    )
        except Exception as e:
            logger.error(f"Ошибка обновления прогресса: {e}")

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

async def download_video(url: str, chat_id: int, status_msg_id: int) -> tuple[Optional[str], int]:
    """Загрузка видео с YouTube"""
    output_template = os.path.join(DOWNLOAD_DIR, f'video_{chat_id}_%(title)s.%(ext)s')
    
    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[height>=720][height<=1440]+bestaudio/best[height>=720][height<=1440]/best',
        'merge_output_format': 'mp4',
        'writesubtitles': False,
        'writeautomaticsub': False,
        'ignoreerrors': False,
        'progress_hooks': [lambda d: dp.loop.create_task(progress_hook(d, status_msg_id, chat_id))],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
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

@dp.message(Command("start"))
async def start(message: types.Message):
    """Команда /start"""
    welcome_text = (
        "🎬 *YouTube Downloader Bot*\n\n"
        "📋 *Возможности:*\n"
        "• Скачивание видео в HD/2K качестве\n"
        "• Поддержка длинных видео (2+ часа)\n"
        "• Автоматическое разделение больших файлов\n"
        "• Быстрая и стабильная загрузка\n\n"
        "📝 *Как использовать:*\n"
        "Просто отправьте ссылку на YouTube видео!"
    )
    await message.reply(welcome_text, parse_mode=types.ParseMode.MARKDOWN_V2)

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    """Команда /help"""
    help_text = (
        "🆘 *Помощь*\n\n"
        "*Команды:*\n"
        "/start - Запуск бота\n"
        "/help - Эта справка\n\n"
        "*Поддерживаемые форматы ссылок:*\n"
        "• youtube.com/watch?v=...\n"
        "• youtu.be/...\n"
        "• m.youtube.com/...\n\n"
        "*Параметры загрузки:*\n"
        "• Качество: HD (720p) - 2K (1440p)\n"
        "• Максимальный размер части: 1.9 ГБ\n"
        "• Поддержка видео любой длительности"
    )
    await message.reply(help_text, parse_mode=types.ParseMode.MARKDOWN_V2)

@dp.message(RegexpCommandsFilter(regexp_commands=[r'https?://.*']))
async def handle_message(message: types.Message):
    """Обработка сообщений с YouTube ссылками"""
    chat_id = message.chat.id
    url = message.text.strip()
    
    if not is_youtube_url(url):
        await message.reply(
            "❌ Пожалуйста, отправьте корректную ссылку на YouTube видео.\n"
            "Пример: https://youtube.com/watch?v=..."
        )
        return
    
    status_msg = await message.reply("🔍 Анализирую видео...")
    status_msg_id = status_msg.message_id
    
    try:
        video_info = get_video_info(url)
        if not video_info:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text="❌ Не удалось получить информацию о видео."
            )
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
            f"🎬 Начинаю загрузку..."
        )
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=info_text,
            parse_mode=types.ParseMode.MARKDOWN_V2
        )
        
        filepath, filesize = await download_video(url, chat_id, status_msg_id)
        
        if not filepath or not os.path.exists(filepath):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text="❌ Ошибка загрузки видео. Попробуйте позже."
            )
            return
        
        logger.info(f"Загружен файл: {filepath}, размер: {format_filesize(filesize)}")
        
        if filesize <= MAX_FILE_SIZE:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"📤 Отправляю файл ({format_filesize(filesize)})..."
            )
            
            with open(filepath, 'rb') as video_file:
                await bot.send_document(
                    chat_id=chat_id,
                    document=video_file,
                    filename=os.path.basename(filepath),
                    caption=f"🎬 {video_info.get('title', 'video')}"
                )
            
            cleanup_files(filepath)
            await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
            
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton("✅ Да, разделить", callback_data="split_yes"),
                    InlineKeyboardButton("❌ Нет, отменить", callback_data="split_no")
                ]
            ])
            
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=(
                    f"⚠️ *Файл слишком большой*\n\n"
                    f"📁 Размер: {format_filesize(filesize)}\n"
                    f"📏 Будет разделен на ~{math.ceil(filesize / CHUNK_SIZE)} частей\n\n"
                    f"Разделить файл на части?"
                ),
                reply_markup=keyboard,
                parse_mode=types.ParseMode.MARKDOWN
            )
            
            dp.storage_data[chat_id] = {"filepath": filepath, "title": video_info.get('title', 'video')}
    
    except Exception as e:
        logger.error(f"Общая ошибка обработки: {e}")
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=f"❌ Произошла ошибка: {str(e)}"
        )
        if 'filepath' in locals() and filepath:
            cleanup_files(filepath)

@dp.callback_query()
async def handle_split_callback(query: types.CallbackQuery):
    """Обработка callback для разделения файла"""
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    data = dp.storage_data.get(chat_id, {})
    filepath = data.get("filepath")
    title = data.get("title", "video")
    
    await query.answer()
    
    if query.data == "split_yes":
        if not filepath or not os.path.exists(filepath):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ Файл не найден."
            )
            return
        
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="✂️ Разделяю файл на части..."
        )
        
        try:
            parts = split_file(filepath, chat_id)
            
            if not parts:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Ошибка разделения файла."
                )
                cleanup_files(filepath)
                return
            
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"📤 Отправляю {len(parts)} частей..."
            )
            
            for i, part_path in enumerate(parts, 1):
                try:
                    with open(part_path, 'rb') as part_file:
                        await bot.send_document(
                            chat_id=chat_id,
                            document=part_file,
                            filename=os.path.basename(part_path),
                            caption=f"🎬 {title} - Часть {i}/{len(parts)}"
                        )
                except Exception as e:
                    logger.error(f"Ошибка отправки части {i}: {e}")
            
            cleanup_files(filepath, *parts)
            
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"✅ *Загрузка завершена!*\n📁 Отправлено частей: {len(parts)}",
                parse_mode=types.ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка разделения/отправки: {e}")
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ Ошибка: {str(e)}"
            )
            cleanup_files(filepath)
    
    else:  # split_no
        if filepath:
            cleanup_files(filepath)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="❌ Загрузка отменена."
        )
    
    dp.storage_data.pop(chat_id, None)

async def on_startup():
    """Установка webhook при запуске"""
    webhook_path = f"/{TELEGRAM_TOKEN}"
    webhook_url = f"{WEBHOOK_URL}{webhook_path}"
    await bot.set_webhook(url=webhook_url)
    logger.info(f"🚀 Webhook установлен: {webhook_url}")

async def on_shutdown():
    """Очистка при завершении работы"""
    await bot.delete_webhook()
    logger.info("🚀 Webhook удален")

def main():
    """Главная функция"""
    if not TELEGRAM_TOKEN:
        logger.error("❌ ОШИБКА: Токен бота не найден в переменной окружения TELEGRAM_TOKEN")
        return
    
    if not WEBHOOK_URL:
        logger.error("❌ ОШИБКА: WEBHOOK_URL не установлен в переменных окружения")
        return
    
    app = web.Application()
    webhook_path = f"/{TELEGRAM_TOKEN}"
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)
    
    port = int(os.environ.get('PORT', 8443))
    logger.info(f"🚀 Запуск сервера на порту {port}...")
    web.run_app(app, host='0.0.0.0', port=port)

if __name__ == "__main__":
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    main()
