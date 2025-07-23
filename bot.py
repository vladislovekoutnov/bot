import os
import logging
import sqlite3
import random
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher.webhook import get_new_configured_app
from dotenv import load_dotenv
import openai
from textblob import TextBlob

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_PATH = os.getenv("DB_PATH", "/tmp/memory.db")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("TELEGRAM_TOKEN и OPENAI_API_KEY должны быть заданы в .env")

openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO)

# Инициализация базы данных для истории
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS histories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        role TEXT,
        content TEXT,
        sentiment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
)
conn.commit()

def clean_old_histories():
    cursor.execute("DELETE FROM histories WHERE created_at < datetime('now', '-90 days')")
    conn.commit()

# Загружаем полную биографию из внешнего файла temshik.txt
with open("temshik.txt", encoding="utf-8") as f:
    BIOGRAPHY = f.read()
SYSTEM_PROMPT = BIOGRAPHY + "\nГовори на «ты», используй сленг и хаотичные образы."

# Инициализация бота и диспетчера с вебхуками
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot)
app = get_new_configured_app(dispatcher=dp)

async def process_user_message(chat_id: int, user_id: int, text: str) -> list:
    clean_old_histories()
    blob = TextBlob(text)
    polarity = blob.sentiment.polarity
    sentiment = 'positive' if polarity > 0.1 else 'negative' if polarity < -0.1 else 'neutral'
    cursor.execute(
        "INSERT INTO histories (chat_id, user_id, role, content, sentiment) VALUES (?, ?, ?, ?, ?)" ,
        (chat_id, user_id, 'user', text, sentiment)
    )
    conn.commit()
    cursor.execute("SELECT role, content FROM histories WHERE chat_id = ? ORDER BY id DESC LIMIT 12", (chat_id,))
    rows = cursor.fetchall()[::-1]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, content in rows:
        messages.append({"role": role, "content": content})
    return messages

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await bot.send_chat_action(message.chat.id, types.ChatActions.TYPING)
    await asyncio.sleep(1)
    await message.reply("Здорово, братан! Я Толян, слушаю тебя.")

@dp.message_handler(content_types=[types.ContentType.TEXT])
async def handle_message(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    history = await process_user_message(chat_id, user_id, message.text)
    await bot.send_chat_action(chat_id, types.ChatActions.TYPING)
    await asyncio.sleep(min(len(message.text) * 0.05, 2))
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=history,
        temperature=0.9,
        max_tokens=300
    )
    reply = response.choices[0].message.content.strip()
    cursor.execute(
        "INSERT INTO histories (chat_id, user_id, role, content) VALUES (?, ?, ?, ?)" ,
        (chat_id, user_id, 'assistant', reply)
    )
    conn.commit()
    await message.reply(reply)

# Функция-обработчик для Google Cloud Functions
def telegram_webhook(request):
    """
    HTTP-входная точка для GCF.
    Пример запроса: Telegram POST на /telegram_webhook.
    """
    if request.method != 'POST':
        return ('Method Not Allowed', 405)
    update = types.Update.de_json(request.get_json(force=True))
    asyncio.get_event_loop().create_task(dp.process_update(update))
    return ('', 200)
