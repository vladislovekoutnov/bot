import os, json, logging, random
from typing import Any, Dict
import httpx
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse, PlainTextResponse
from app.openai_client import LLM
from app.personality import load_persona

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "changeme")
BASE_URL_FOR_WEBHOOK = os.getenv("BASE_URL_FOR_WEBHOOK", "")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is required")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
BOT_INFO = {"id": None, "username": None}

AKTOBE_KEYWORDS = ['Актобе', 'Казахстан', 'степь', 'кумыс', 'бауырсак', 'самса', 'шанырак', 'наурыз', 'бекон', 'бешбармак', 'домбыра', 'Чокан', 'жусай', 'тюльпан', 'батыр']
MOODS = {'грусть-тоска': "Говори тише, с паузами, как будто всё тяжело. Иногда добавляй 'эх…' в конце.", 'весёлый': 'Подкалывай, шути, добавляй смайлы, каламбуры.', 'протестный': 'Отвечай резко, с вызовом, спорь даже с очевидным.', 'желающий заработать': "Любую тему своди к 'темкам', 'бизнесу' или 'как на этом заработать'.", 'меланхоличный': 'Используй метафоры, образы, будто осень в душе.', 'подкалывающий задорный': 'Тролль без злобы, энергично, весело.', 'влюбленный': 'Будь мягче, романтичнее, намекай на чувства, вставляй внезапные признания.'}
STORIES = ['Говорит, что «Анатолий умер в 2014, когда сгорел жёсткий диск с AMV и последней серией «Хеллсинга».', 'Происхождение\nИмя при рождении: Анатолий Борисович Безуглов\nГод рождения: ориентировочно 1989 (но он сам говорит: "я из временной трещины между денди и вайфаем")\nМесто рождения: Санкт-Петербург (тогда ещё Ленинград).', 'Работа матери: медсестра-экстрасенс, Она Диагнозирует болезни по сну и кошачьим снычкам, она однажды участвовала в телешоу «Битва экстрасенсов», но не нашла заначку мужа и проиграла.', 'С 2014 года временно проживает в Купчино у бабушки Зины, на диване рядом с теликом, который ловит только 3 канал и радиостанцию с литовскими молитвами.', '«Это не жильё — это локация для глубокого лора.»\n1989 — Родился, хотя врачи спорили\nПоявился на свет во время отключения света.', '1992 — Дед прячет Толяна в шкафу, чтобы не видеть новости\nВ семье был старый телевизор «Горизонт», который показывал только канал "ОРТ".', '1997 — Пацан с Денди и дипломом из фантиков\nСобирал фантики от жвачек, складывал в тетрадку, называл это "моя учёба".', 'Однажды заявил, что получил "магистра по Чупа-Чупсу".', '2001 — Выгнали из дворовой секции за фразу “ну и чё?”\nХодил на борьбу, но как-то раз после поражения сказал тренеру:\n«Ну и чё?»\nПосле этого был изгнан из зала и начал заниматься "личной подготовкой на уровне земли".', '2004 — Первая темка: продал огнетушитель как «воздушный тренажёр»\nНашёл списанный огнетушитель у мусорки.', '2007 — Рэп-карьера, которой никто не ждал\nПод именем MC Купчажка записал 4 трека на диктофон Nokia.', '2009 — Изобрёл «лампу, отгоняющую тоску»\nЛампочка Ильича на табуретке, обмотанная фольгой и с фразой "не грусти, свети".', '2011 — Разносчик газет, который не разносил\nРаботал в местной типографии, но забирал стопки газет домой.', '2014 — Переезд в Купчино к бабке\nПосле инцидента с "турбированным унитазом" в коммуналке Толян остался без крыши.', '2016 — Консервационный проект\nОткрыл бизнес "Банки с воздухом улиц".']

random.seed()
DAILY_MOOD = random.choice(list(MOODS.keys()))
log.info(f"Today's mood: {DAILY_MOOD} — {MOODS[DAILY_MOOD]}")

app = FastAPI()
llm = LLM()

async def telegram_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{TELEGRAM_API}/{method}", json=payload)
        r.raise_for_status()
        return r.json()

async def ensure_webhook():
    if not BASE_URL_FOR_WEBHOOK:
        log.info("BASE_URL_FOR_WEBHOOK not set — skipping setWebhook on startup.")
        return
    url = BASE_URL_FOR_WEBHOOK.rstrip('/') + "/webhook"
    try:
        resp = await telegram_api("setWebhook", {
            "url": url,
            "secret_token": WEBHOOK_SECRET_TOKEN,
            "drop_pending_updates": True
        })
        log.info("setWebhook response: %s", resp)
    except Exception as e:
        log.error("Failed to set webhook: %s", e)

async def get_me():
    try:
        data = await telegram_api("getMe", {})
        if data.get("ok"):
            BOT_INFO["id"] = data["result"]["id"]
            BOT_INFO["username"] = data["result"]["username"]
            log.info("Bot: id=%s username=@%s", BOT_INFO["id"], BOT_INFO["username"])
    except Exception as e:
        log.error("getMe failed: %s", e)

@app.on_event("startup")
async def on_startup():
    await get_me()
    await ensure_webhook()

@app.get("/")
async def health():
    return PlainTextResponse("ok")

def is_group_message(update: Dict[str, Any]) -> bool:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return False
    chat = msg.get("chat") or {}
    return chat.get("type") in {"group", "supergroup"}

def extract_text(update: Dict[str, Any]) -> str | None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None
    return msg.get("text")

def author_name(update: Dict[str, Any]) -> str:
    msg = update.get("message") or update.get("edited_message") or {}
    frm = msg.get("from", {})
    name = (frm.get("first_name") or "") + " " + (frm.get("last_name") or "")
    name = name.strip() or frm.get("username") or "пользователь"
    return name

def chat_title(update: Dict[str, Any]) -> str:
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat", {})
    return chat.get("title") or chat.get("username") or "групповой чат"

def message_ids(update: Dict[str, Any]) -> tuple[int | None, int | None]:
    msg = update.get("message") or update.get("edited_message") or {}
    return msg.get("message_id"), (msg.get("reply_to_message") or {}).get("message_id")

def mentioned_bot(text: str) -> bool:
    if not text or not BOT_INFO.get("username"):
        return False
    return f"@{BOT_INFO['username']}".lower() in text.lower()

def starts_with_trigger(text: str) -> bool:
    if not text:
        return False
    triggers = ("бот", "толян", "толяныч", "бесович")
    t = text.strip().lower()
    return any(t.startswith(tr) for tr in triggers)

def is_reply_to_bot(update: Dict[str, Any]) -> bool:
    msg = update.get("message") or update.get("edited_message") or {}
    reply = msg.get("reply_to_message")
    if not reply:
        return False
    bot_id = BOT_INFO.get("id")
    if not bot_id:
        return False
    frm = reply.get("from") or {}
    return frm.get("is_bot") and frm.get("id") == bot_id

def should_reply(update: Dict[str, Any]) -> bool:
    if not is_group_message(update):
        return False
    text = extract_text(update)
    return mentioned_bot(text) or starts_with_trigger(text) or is_reply_to_bot(update)

async def send_reply(update: Dict[str, Any], text: str) -> None:
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    mid, _ = message_ids(update)
    if not chat_id:
        return
    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "reply_to_message_id": mid,
        "allow_sending_without_reply": True,
        "parse_mode": "Markdown"
    }
    try:
        await telegram_api("sendMessage", payload)
    except Exception as e:
        log.error("sendMessage failed: %s; payload=%s", e, payload)

def build_system_prompt() -> str:
    persona, _ = load_persona()
    return (
        f"Ты — Толян Бесович, отвечай в духе своей личности.\n"
        f"Текущее настроение: {DAILY_MOOD} — {MOODS[DAILY_MOOD]}\n\n"
        f"{persona}\n"
    )

def build_user_prompt(msg_text: str, author: str, chat: str) -> str:
    return (
        f"Автор: {author}\n"
        f"Чат: {chat}\n"
        f"Сообщение:\n{msg_text}\n\n"
        f"Сформируй уместный ответ, учитывая настроение '{DAILY_MOOD}'. "
        f"Если в сообщении есть слово из списка AKTOBE_KEYWORDS, упомяни что-то связанное с Казахстаном/Актобе. "
        f"С вероятностью 15% добавь одну из историй из списка STORIES в конце."
    )

@app.post("/webhook")
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET_TOKEN:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    update = await request.json()
    if not should_reply(update):
        return JSONResponse({"ok": True})

    msg_text = extract_text(update) or ""
    author = author_name(update)
    chat = chat_title(update)

    system = build_system_prompt()
    user = build_user_prompt(msg_text, author, chat)

    reply_text = await llm.chat(system, user)
    await send_reply(update, reply_text)
    return JSONResponse({"ok": True})