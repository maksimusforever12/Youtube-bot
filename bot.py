import os
import re
import math
import logging
import subprocess
import json
import time
import asyncio
from typing import Optional, List

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# Настройки
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')  # Используется только для health check
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
storage = MemoryStorage()
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=storage)

# Состояния для FSM
class VideoStates(StatesGroup):
    waiting_for_split = State()

# Лимитер запросов (из примера плейлист-бота)
class TelegramRateLimiter:
    def __init__(self):
        self.message_count = 0
        self.last_minute = int(time.time() // 60)
        self.messages_per_minute = 0
        
    async def wait_if_needed(self):
        current_minute = int(time.time() // 60)
        if current_minute > self.last_minute:
            self.messages_per_minute = 0
            self.last_minute = current_minute
        self.message_count += 1
        self.messages_per_minute += 1
        if self.messages_per_minute >= 1200:
            wait_time = 60 - (time.time() % 60)
            logger.info(f"Достигнут лимит сообщений в минуту. Ожидание {wait_time:.1f} секунд...")
            await asyncio.sleep(wait_time)
            self.messages_per_minute = 0
            self.last_minute = int(time.time() // 60)
        elif self.message_count % 20 == 0:
            await asyncio.sleep(1)
        elif self.message_count % 5 == 0:
            await asyncio.sleep(0.2)

rate_limiter = TelegramRateLimiter()

def escape_markdown_v2(text: str) -> str:
    """Экранирование зарезервированных символов для MarkdownV2"""
    reserved_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in reserved_chars else char for char in text)

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

async def progress_hook(process: subprocess.Popen, status_msg_id: int, chat_id: int):
    """Progress hook для yt-dlp с обновлением сообщения"""
    while process.poll() is None:
        line = process.stdout.readline().strip()
        if "download" in line.lower():
            try:
                percent = float(line.split()[1].strip('%'))
                if percent % 10 == 0:  # Обновляем каждые 10%
                    await rate_limiter.wait_if_needed()
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        text=f"📥 Загружено: {percent:.1f}%"
                    )
            except (IndexError, ValueError):
                pass
        await asyncio.sleep(0.1)

def get_video_info(url: str) -> Optional[dict]:
    """Получение информации о видео через subprocess"""
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-warnings",
        "--ignore-errors",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--sleep-requests", "1",
        "--extractor-retries", "5",
        "--socket-timeout", "30",
        url
    ]
    
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300
        )
        if result.returncode != 0:
            logger.error(f"Ошибка получения информации: {result.stderr.strip()}")
            return None
        data_json = json.loads(result.stdout.strip())
        return data_json
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        logger.error(f"Ошибка получения информации: {e}")
        return None

async def download_video(url: str, chat_id: int, status_msg_id: int) -> tuple[Optional[str], int]:
    """Загрузка видео через subprocess"""
    output_template = os.path.join(DOWNLOAD_DIR, f'video_{chat_id}_%(title)s.%(ext)s')
    
    cmd = [
        "yt-dlp",
        "--output", output_template,
        "--format", "bestvideo[height>=720][height<=1440]+bestaudio/best[height>=720][height<=1440]/best",
        "--merge-output-format", "mp4",
        "--no-warnings",
        "--ignore-errors",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--sleep-requests", "1",
        "--extractor-retries", "5",
        "--socket-timeout", "30",
        "--progress", "dot",
        url
    ]
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Запускаем прогресс в отдельной задаче
        asyncio.create_task(progress_hook(process, status_msg_id, chat_id))
        
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(f"Ошибка загрузки: {stderr}")
            return None, 0
        
        for file in os.listdir(DOWNLOAD_DIR):
            if file.startswith(f'video_{chat_id}_') and file.endswith('.mp4'):
                filepath = os.path.join(DOWNLOAD_DIR, file)
                filesize = os.path.getsize(filepath)
                return filepath, filesize
        
        return None, 0
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
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
    await rate_limiter.wait_if_needed()
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
    await message.reply(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    """Команда /help"""
    await rate_limiter.wait_if_needed()
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
    await message.reply(help_text, parse_mode=ParseMode.MARKDOWN_V2)

@dp.message(lambda message: is_youtube_url(message.text.strip()))
async def handle_message(message: types.Message, state: FSMContext):
    """Обработка сообщений с YouTube ссылками"""
    await rate_limiter.wait_if_needed()
    chat_id = message.chat.id
    url = message.text.strip()
    
    status_msg = await message.reply("🔍 Анализирую видео...")
    status_msg_id = status_msg.message_id
    
    try:
        video_info = get_video_info(url)
        if not video_info:
            await rate_limiter.wait_if_needed()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text="❌ Не удалось получить информацию о видео."
            )
            return
        
        title = video_info.get('title', 'Unknown')
        if len(title) > 50:
            title = title[:50] + '...'
        title = escape_markdown_v2(title)
        uploader = escape_markdown_v2(video_info.get('uploader', 'Unknown'))
        duration = video_info.get('duration', 0)
        
        info_text = (
            f"📹 *{title}*\n"
            f"👤 {uploader}\n"
            f"⏱️ {format_duration(duration)}\n\n"
            f"🎬 Начинаю загрузку..."
        )
        await rate_limiter.wait_if_needed()
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=info_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        filepath, filesize = await download_video(url, chat_id, status_msg_id)
        
        if not filepath or not os.path.exists(filepath):
            await rate_limiter.wait_if_needed()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text="❌ Ошибка загрузки видео. Попробуйте позже."
            )
            return
        
        logger.info(f"Загружен файл: {filepath}, размер: {format_filesize(filesize)}")
        
        if filesize <= MAX_FILE_SIZE:
            await rate_limiter.wait_if_needed()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"📤 Отправляю файл ({escape_markdown_v2(format_filesize(filesize))})..."
            )
            
            with open(filepath, 'rb') as video_file:
                await rate_limiter.wait_if_needed()
                await bot.send_document(
                    chat_id=chat_id,
                    document=video_file,
                    filename=os.path.basename(filepath),
                    caption=f"🎬 {escape_markdown_v2(video_info.get('title', 'video'))}",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            
            cleanup_files(filepath)
            await rate_limiter.wait_if_needed()
            await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton("✅ Да, разделить", callback_data="split_yes"),
                    InlineKeyboardButton("❌ Нет, отменить", callback_data="split_no")
                ]
            ])
            
            await rate_limiter.wait_if_needed()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=(
                    f"⚠️ *Файл слишком большой*\n\n"
                    f"📁 Размер: {escape_markdown_v2(format_filesize(filesize))}\n"
                    f"📏 Будет разделен на ~{math.ceil(filesize / CHUNK_SIZE)} частей\n\n"
                    f"Разделить файл на части?"
                ),
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
            await state.update_data(filepath=filepath, title=video_info.get('title', 'video'), status_msg_id=status_msg_id)
            await state.set_state(VideoStates.waiting_for_split)
    
    except Exception as e:
        logger.error(f"Общая ошибка обработки: {e}")
        await rate_limiter.wait_if_needed()
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=f"❌ Произошла ошибка: {escape_markdown_v2(str(e))}"
        )
        if 'filepath' in locals() and filepath:
            cleanup_files(filepath)

@dp.callback_query()
async def handle_split_callback(query: types.CallbackQuery, state: FSMContext):
    """Обработка callback для разделения файла"""
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    data = await state.get_data()
    filepath = data.get("filepath")
    title = escape_markdown_v2(data.get("title", "video"))
    
    await query.answer()
    
    if query.data == "split_yes":
        if not filepath or not os.path.exists(filepath):
            await rate_limiter.wait_if_needed()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ Файл не найден."
            )
            await state.clear()
            return
        
        await rate_limiter.wait_if_needed()
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="✂️ Разделяю файл на части..."
        )
        
        try:
            parts = split_file(filepath, chat_id)
            
            if not parts:
                await rate_limiter.wait_if_needed()
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Ошибка разделения файла."
                )
                cleanup_files(filepath)
                await state.clear()
                return
            
            await rate_limiter.wait_if_needed()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"📤 Отправляю {len(parts)} частей..."
            )
            
            for i, part_path in enumerate(parts, 1):
                try:
                    with open(part_path, 'rb') as part_file:
                        await rate_limiter.wait_if_needed()
                        await bot.send_document(
                            chat_id=chat_id,
                            document=part_file,
                            filename=os.path.basename(part_path),
                            caption=f"🎬 {title} - Часть {i}/{len(parts)}",
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                except Exception as e:
                    logger.error(f"Ошибка отправки части {i}: {e}")
            
            cleanup_files(filepath, *parts)
            
            await rate_limiter.wait_if_needed()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"✅ *Загрузка завершена!*\n📁 Отправлено частей: {len(parts)}",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
        except Exception as e:
            logger.error(f"Ошибка разделения/отправки: {e}")
            await rate_limiter.wait_if_needed()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ Ошибка: {escape_markdown_v2(str(e))}"
            )
            cleanup_files(filepath)
    else:  # split_no
        if filepath:
            cleanup_files(filepath)
        await rate_limiter.wait_if_needed()
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="❌ Загрузка отменена."
        )
    
    await state.clear()

async def health_check(request):
    """Простой health check для Render.com"""
    return web.Response(text="Bot is running")

async def start_web_server():
    """Запускаем простой веб-сервер для Render.com"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    port = int(os.environ.get('PORT', 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Веб-сервер запущен на порту {port}")

async def set_bot_commands():
    """Устанавливаем команды в меню бота"""
    commands = [
        types.BotCommand(command="start", description="Запустить бота"),
        types.BotCommand(command="help", description="Показать справку")
    ]
    await bot.set_my_commands(commands)
    logger.info("Команды меню установлены")

async def main():
    """Главная функция"""
    if not TELEGRAM_TOKEN:
        logger.error("❌ ОШИБКА: Токен бота не найден в переменной окружения TELEGRAM_TOKEN")
        return
    
    try:
        global rate_limiter
        rate_limiter = TelegramRateLimiter()
        await start_web_server()
        await set_bot_commands()
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())
