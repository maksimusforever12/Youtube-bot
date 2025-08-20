```python
import os
import logging
import time
import asyncio
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

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
storage = MemoryStorage()
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=storage)

# Состояния для FSM
class TestStates(StatesGroup):
    waiting_for_test_action = State()

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

def escape_markdown_v2(text: str) -> str:
    """Экранирование зарезервированных символов для MarkdownV2"""
    if not isinstance(text, str):
        text = str(text)
    reserved_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in reserved_chars else char for char in text)

@dp.message(Command("start"))
async def start(message: types.Message):
    """Команда /start"""
    await rate_limiter.wait_if_needed()
    welcome_text = (
        "🎬 *YouTube Downloader Bot \\(тестовый режим\\)*\n\n"
        "📋 *Возможности:*\n"
        "• Тестирование интерфейса бота\n"
        "• Проверка команд и кнопок\n\n"
        "📝 *Команды:*\n"
        "/start \\- Начать работу\n"
        "/help \\- Показать справку\n"
        "/test \\- Тестировать инлайн\\-кнопки"
    )
    await message.reply(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    """Команда /help"""
    await rate_limiter.wait_if_needed()
    help_text = (
        "🆘 *Помощь*\n\n"
        "*Команды:*\n"
        "/start \\- Запуск бота\n"
        "/help \\- Эта справка\n"
        "/test \\- Тестирование инлайн\\-кнопок\n\n"
        "*Примечание:*\n"
        "Это тестовый режим\\. Функции скачивания видео пока не реализованы\\."
    )
    await message.reply(help_text, parse_mode=ParseMode.MARKDOWN_V2)

@dp.message(Command("test"))
async def test_cmd(message: types.Message, state: FSMContext):
    """Команда /test для проверки инлайн-кнопок"""
    await rate_limiter.wait_if_needed()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="test_confirm"),
            InlineKeyboardButton("❌ Отменить", callback_data="test_cancel")
        ]
    ])
    await message.reply(
        "🧪 *Тест интерфейса*\n\n"
        "Нажмите одну из кнопок для проверки:",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(TestStates.waiting_for_test_action)

@dp.callback_query()
async def handle_test_callback(query: types.CallbackQuery, state: FSMContext):
    """Обработка callback для тестовых кнопок"""
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    await query.answer()
    
    await rate_limiter.wait_if_needed()
    if query.data == "test_confirm":
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="✅ *Действие подтверждено!*\n\nТест успешно пройден\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:  # test_cancel
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="❌ *Действие отменено\\.*\n\nТест завершён\\.",
            parse_mode=ParseMode.MARKDOWN_V2
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
        types.BotCommand(command="help", description="Показать справку"),
        types.BotCommand(command="test", description="Тестировать инлайн-кнопки")
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
```
