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

# Настройки
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 ГБ
CHUNK_SIZE = 1.9 * 1024 * 1024 * 1024  # 1.9 ГБ для безопасности
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
    waiting_for_url = State()
    waiting_for_split = State()

# Лимитер запросов
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

def check_dependencies() -> bool:
    """Проверка наличия yt-dlp и ffmpeg"""
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"Ошибка зависимостей: {e}. Убедитесь, что yt-dlp и ffmpeg установлены и добавлены в PATH.")
        return False

def escape_markdown_v2(text: str) -> str:
    """Экранирование зарезервированных символов для MarkdownV2"""
    if not isinstance(text, str):
        text = str(text)
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
        return escape_markdown_v2("Неизвестно")
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return escape_markdown_v2(f"{hours}ч {minutes}м")
    return escape_markdown_v2(f"{minutes}м")

def format_filesize(size_bytes: int) -> str:
    """Форматирование размера файла"""
    if size_bytes == 0:
        return escape_markdown_v2("0 B")
    size_names = ["B", "KB", "MB", "GB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return escape_markdown_v2(f"{s} {size_names[i]}")

async def progress_hook(process: subprocess.Popen, status_msg_id: int, chat_id: int):
    """Progress hook для yt-dlp с обновлением сообщения"""
    while process.poll() is None:
        try:
            line = process.stdout.readline().strip()
            if "download" in line.lower():
                try:
                    percent = float(line.split()[1].strip('%'))
                    if percent % 10 == 0:  # Обновляем каждые 10%
                        await rate_limiter.wait_if_needed()
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_msg_id,
                            text=escape_markdown_v2(f"📥 Загружено: {percent:.1f}%"),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                except (IndexError, ValueError):
                    pass
        except Exception as e:
            logger.error(f"Ошибка в progress_hook: {e}")
        await asyncio.sleep(0.1)

def get_video_info(url: str) -> Optional[dict]:
    """Получение информации о видео через subprocess"""
    if not check_dependencies():
        return None
    
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-warnings",
        "--ignore-errors",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--sleep-requests", "1",
        "--extractor-retries", "5",
        "--socket-timeout", "30"
    ]
    if os.path.exists("cookies.txt"):
        cmd.extend(["--cookies", "cookies.txt"])
    cmd.append(url)
    
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
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"Ошибка получения информации: {e}")
        return None

async def download_video(url: str, chat_id: int, status_msg_id: int) -> tuple[Optional[str], int]:
    """Загрузка видео через subprocess"""
    if not check_dependencies():
        return None, 0
    
    output_template = os.path.join(DOWNLOAD_DIR, f'video_{chat_id}_%(title)s.%(ext)s')
    
    cmd = [
        "yt-dlp",
        "--output", output_template,
        "--format", "bestvideo[height>=1080][height<=1440]+bestaudio/best[height>=1080][height<=1440]/best",
        "--merge-output-format", "mp4",
        "--no-warnings",
        "--ignore-errors",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--sleep-requests", "1",
        "--extractor-retries", "5",
        "--socket-timeout", "30",
        "--progress", "dot"
    ]
    if os.path.exists("cookies.txt"):
        cmd.extend(["--cookies", "cookies.txt"])
    cmd.append(url)
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
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
async def start(message: types.Message, state: FSMContext):
    """Команда /start"""
    await rate_limiter.wait_if_needed()
    welcome_text = escape_markdown_v2(
        "🎬 *YouTube Downloader Bot*\n\n"
        "📋 *Возможности:*\n"
        "• Скачивание видео в 1080p–2K качестве\n"
        "• Поддержка длинных видео (2+ часа)\n"
        "• Автоматическое разделение больших файлов\n\n"
        "📝 *Как использовать:*\n"
        "Отправьте ссылку на YouTube видео или используйте команды:\n"
        "/start - Начать работу\n"
        "/help - Показать справку"
    )
    await message.reply(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)
    await state.set_state(VideoStates.waiting_for_url)

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    """Команда /help"""
    await rate_limiter.wait_if_needed()
    help_text = escape_markdown_v2(
        "🆘 *Помощь*\n\n"
        "*Команды:*\n"
        "/start - Запуск бота\n"
        "/help - Эта справка\n\n"
        "*Как скачать видео:*\n"
        "Отправьте ссылку на YouTube видео. Видео будет загружено в 1080p–2K качестве. Если размер превысит 2 ГБ, бот предложит разделить файл.\n\n"
        "*Поддерживаемые форматы ссылок:*\n"
        "• youtube.com/watch?v=...\n"
        "• youtu.be/...\n"
        "• m.youtube.com/..."
    )
    await message.reply(help_text, parse_mode=ParseMode.MARKDOWN_V2)

@dp.message(VideoStates.waiting_for_url)
async def handle_message(message: types.Message, state: FSMContext):
    """Обработка YouTube URL"""
    url = message.text.strip()
    if not is_youtube_url(url):
        await rate_limiter.wait_if_needed()
        await message.reply(
            escape_markdown_v2("❌ Пожалуйста, отправьте корректную ссылку на YouTube видео."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    await rate_limiter.wait_if_needed()
    status_msg = await message.reply(
        escape_markdown_v2("🔍 Проверяю видео..."),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    if not check_dependencies():
        await rate_limiter.wait_if_needed()
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=escape_markdown_v2("❌ Ошибка: yt-dlp или ffmpeg не установлены. Установите их и добавьте в PATH."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    video_info = get_video_info(url)
    if not video_info:
        await rate_limiter.wait_if_needed()
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=escape_markdown_v2("❌ Не удалось получить информацию о видео. Попробуйте другую ссылку или проверьте cookies.txt."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    title = video_info.get('title', 'Неизвестное видео')
    duration = video_info.get('duration', 0)
    filesize_approx = video_info.get('filesize_approx', 0)
    
    await rate_limiter.wait_if_needed()
    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        text=escape_markdown_v2(
            f"📹 *Название:* {title}\n"
            f"⏱ *Длительность:* {format_duration(duration)}\n"
            f"📦 *Примерный размер:* {format_filesize(filesize_approx)}\n\n"
            "📥 Начинаю загрузку..."
        ),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    filepath, filesize = await download_video(url, message.chat.id, status_msg.message_id)
    if not filepath:
        await rate_limiter.wait_if_needed()
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=escape_markdown_v2("❌ Ошибка загрузки видео. Попробуйте позже или другую ссылку."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    if filesize <= MAX_FILE_SIZE:
        await rate_limiter.wait_if_needed()
        try:
            with open(filepath, 'rb') as video_file:
                await message.reply_video(
                    video=types.FSInputFile(filepath),
                    caption=escape_markdown_v2(f"🎥 {title}"),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    duration=duration if duration else None
                )
            cleanup_files(filepath)
            await bot.delete_message(message.chat.id, status_msg.message_id)
        except Exception as e:
            logger.error(f"Ошибка отправки видео: {e}")
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=escape_markdown_v2("❌ Ошибка отправки видео. Попробуйте позже."),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            cleanup_files(filepath)
        return
    
    await rate_limiter.wait_if_needed()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Разделить", callback_data=f"split_{filepath}"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")
        ]
    ])
    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        text=escape_markdown_v2(
            f"⚠️ Видео слишком большое ({format_filesize(filesize)}). Хотите разделить на части?"
        ),
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(VideoStates.waiting_for_split)
    await state.update_data(filepath=filepath, original_message_id=message.message_id)

@dp.callback_query()
async def handle_callback(query: types.CallbackQuery, state: FSMContext):
    """Обработка callback для разделения"""
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    await query.answer()
    
    await rate_limiter.wait_if_needed()
    if query.data == "cancel":
        data = await state.get_data()
        filepath = data.get('filepath')
        cleanup_files(filepath)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=escape_markdown_v2("❌ Загрузка отменена. Файл удалён."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.clear()
    elif query.data.startswith("split_"):
        filepath = query.data[len("split_"):]
        if not os.path.exists(filepath):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=escape_markdown_v2("❌ Файл не найден. Попробуйте загрузить видео заново."),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.clear()
            return
        
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=escape_markdown_v2("✂️ Разделяю видео на части..."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        parts = split_file(filepath, chat_id)
        if not parts:
            cleanup_files(filepath)
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=escape_markdown_v2("❌ Ошибка при разделении видео. Попробуйте позже."),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.clear()
            return
        
        await rate_limiter.wait_if_needed()
        for i, part in enumerate(parts, 1):
            try:
                with open(part, 'rb') as part_file:
                    await bot.send_video(
                        chat_id=chat_id,
                        video=types.FSInputFile(part),
                        caption=escape_markdown_v2(f"🎥 Часть {i} из {len(parts)}"),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки части {i}: {e}")
                await bot.send_message(
                    chat_id=chat_id,
                    text=escape_markdown_v2(f"❌ Ошибка отправки части {i}."),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                # Fallback: отправка как документ
                try:
                    with open(part, 'rb') as part_file:
                        await bot.send_document(
                            chat_id=chat_id,
                            document=types.FSInputFile(part),
                            caption=escape_markdown_v2(f"🎥 Часть {i} из {len(parts)} (документ)"),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                except Exception as e2:
                    logger.error(f"Ошибка отправки части {i} как документа: {e2}")

        cleanup_files(filepath, *parts)
        await bot.delete_message(chat_id, message_id)
        await state.clear()

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
    
    if not check_dependencies():
        logger.error("❌ ОШИБКА: Не установлены yt-dlp или ffmpeg. Установите их и добавьте в PATH.")
        return
    
    try:
        global rate_limiter
        rate_limiter = TelegramRateLimiter()
        await set_bot_commands()
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())
