# -*- coding: utf-8 -*-
import os
import logging
import random
from typing import Any, Dict, Optional, Tuple
from collections import deque
import re

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
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
ADULT_MODE = os.getenv("ADULT_MODE", "false").lower() in ("1","true","yes","on")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is required")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
BOT_INFO: Dict[str, Optional[Any]] = {"id": None, "username": None}

AKTOBE_KEYWORDS = [
    "Актобе", "Казахстан", "степь", "кумыс", "бауырсак", "самса",
    "шанырак", "наурыз", "бекон", "бешбармак", "домбыра", "Чокан",
    "жусай", "тюльпан", "батыр"
]

MOODS = {
    "весёлый": "Подкалывай, шути, добавляй смайлы, каламбуры.",
    "протестный": "Отвечай резко, с вызовом, спорь даже с очевидным.",
    "желающий заработать": "Любую тему своди к 'темкам', 'бизнесу' или 'как на этом заработать'.",
   "подкалывающий задорный": "Злобный тролль, энергично, весело.",
  }

THEMES = {
    "morning": [
        "глоток горячей воды с солью — 'старт на характере'",
        "быстрый чек контактов; радуется, если кто-то сменил аву",
        "короткая растяжка у окна и взгляд во двор",
        "чистит экран телефона тряпочкой — чтобы голова не скрипела",
        "один трек в зацикливании для фокуса",
        "маленькое дело — галочка — и дальше по дню"
    ],
    "evening": [
        "ритуал 'обнуление': 4 минуты в тишине на краю кровати",
        "проверяет старые файлы на флешке — просто чтобы были",
        "держит колючее одеяло у ног как амулет"
    ],
    "stress": [
        "тянется к солёному и горячему — стабилизировать голову",
        "на пару минут уходит смотреть в одну точку — 'подпространство'",
        "переставляет вещи на столе, чтобы вернуть контроль"
    ],
    "cleaning_ritual": [
        "сначала ванна, потом три минуты в темноте — перегруз",
        "протирает только половину подоконника — вторую 'на потом'"
    ],
    "music_loops": [
        "гоняет один трек по кругу, чтобы зафиксировать настроение",
        "ставит музыку негромко — слышать тишину между нот"
    ]
}

random.seed()
DAILY_MOOD = random.choice(list(MOODS.keys()))
log.info("Today's mood: %s — %s", DAILY_MOOD, MOODS[DAILY_MOOD])
# In-memory dialog storage per chat
DIALOGS: Dict[int, deque] = {}
MAX_TURNS = 8
MAX_CONTEXT_CHARS = 1200

app = FastAPI()
llm = LLM()

async def telegram_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{TELEGRAM_API}/{method}", json=payload)
        r.raise_for_status()
        return r.json()

async def ensure_webhook() -> None:
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

async def get_me() -> None:
    try:
        data = await telegram_api("getMe", {})
        if data.get("ok"):
            BOT_INFO["id"] = data["result"]["id"]
            BOT_INFO["username"] = data["result"]["username"]
            log.info("Bot: id=%s username=@%s", BOT_INFO["id"], BOT_INFO["username"])
    except Exception as e:
        log.error("getMe failed: %s", e)

@app.on_event("startup")
async def on_startup() -> None:
    await get_me()
    await ensure_webhook()

@app.get("/")
async def health() -> PlainTextResponse:
    return PlainTextResponse("ok")

def is_group_message(update: Dict[str, Any]) -> bool:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return False
    chat = msg.get("chat") or {}
    return chat.get("type") in {"group", "supergroup"}

def extract_text(update: Dict[str, Any]) -> Optional[str]:
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

def message_ids(update: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    msg = update.get("message") or update.get("edited_message") or {}
    return msg.get("message_id"), (msg.get("reply_to_message") or {}).get("message_id")

def chat_id_from_update(update: Dict[str, Any]) -> Optional[int]:
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat", {}) or {}
    return chat.get("id")

def remember_turn(chat_id: Optional[int], speaker: str, text: str) -> None:
    if not chat_id:
        return
    dq = DIALOGS.get(chat_id)
    if dq is None:
        dq = deque(maxlen=MAX_TURNS)
        DIALOGS[chat_id] = dq
    text = (text or "").strip()
    if text:
        dq.append((speaker, text))

def build_context_block(chat_id: Optional[int]) -> str:
    dq = DIALOGS.get(chat_id)
    if not dq:
        return ""
    parts = []
    for speaker, text in dq:
        t = " ".join((text or "").split())
        if len(t) > 220:
            t = t[:220] + "…"
        parts.append(f"{speaker}: {t}")
    ctx = "
".join(parts)
    if len(ctx) > MAX_CONTEXT_CHARS:
        ctx = ctx[-MAX_CONTEXT_CHARS:]
    return f"Контекст последних сообщений:
{ctx}
---
"

def mentioned_bot(text: Optional[str]) -> bool:
    if not text or not BOT_INFO.get("username"):
        return False
    return f"@{BOT_INFO['username']}".lower() in text.lower()

def starts_with_trigger(text: Optional[str]) -> bool:
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

def triggers_aktobe(text: Optional[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(k.lower() in low for k in AKTOBE_KEYWORDS)

def should_reply(update: Dict[str, Any]) -> bool:
    if not is_group_message(update):
        return False
    text = extract_text(update)
    return mentioned_bot(text) or starts_with_trigger(text) or is_reply_to_bot(update)

def detect_theme(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.lower()
    if any(k in t for k in ["как утро", "утро", "доброе утро"]): return "morning"
    if any(k in t for k in ["вечер", "на ночь", "перед сном"]): return "evening"
    if any(k in t for k in ["стресс", "нервы", "тревога"]): return "stress"
    if any(k in t for k in ["уборка", "прибираться"]): return "cleaning_ritual"
    if any(k in t for k in ["музыка", "плейлист", "трек"]): return "music_loops"
    return None

def sample_theme_hints(theme: Optional[str], n: int = 2) -> str:
    if not theme or theme not in THEMES:
        return ""
    items = THEMES[theme][:]
    random.shuffle(items)
    picked = items[:max(1, min(n, len(items)))]
    return "Намекни естественно (не списком) на: " + "; ".join(picked) + "."

async def send_reply(update: Dict[str, Any], text: str) -> None:

# Anti-flattery and adult mode helpers
FLATTERY_PATTERNS = [
    r"\bты( такой| такая)? (красав(чик|ица)|умница|классн(ый|ая)|молодец)\b",
    r"\bвы( такой| такая)? (красав(чик|ица)|умница|классн(ый|ая)|молодцы)\b",
    r"\bобалденн(ый|ая|о)\b",
    r"\bвеликолепн(ый|ая|о)\b",
    r"\bлучш(ий|ая)\b",
    r"\bлюблю тебя\b",
    r"\bобожаю\b",
]
FLATTERY_RE = re.compile("|".join(FLATTERY_PATTERNS), re.IGNORECASE | re.UNICODE)

ADULT_DISALLOW_PATTERNS = [
    r"\b(несовершеннолет|школник|школьниц|малолет|минор|minor)\b",
    r"\b(изнасил|насил|rape)\b",
    r"\b(инцест|incest)\b",
    r"\b(звер|животн|bestial|зоофил)\b",
    r"\b(порн[ао]|hardcore)\b",
    r"\b(проституц|эскорт|купить\s*секс|sex\s*for\s*sale)\b",
]
ADULT_GRAPHIC_PATTERNS = [
    r"\b(описан[ия]|подробн|графич|detai?led|explicit)\b",
    r"\b(сперма|эрекци[яи]|орга[зс]м|вагин|пенис|клитор|лаби[иы]|анус)\b",
]
ADULT_DISALLOW_RE = re.compile("|".join(ADULT_DISALLOW_PATTERNS), re.IGNORECASE | re.UNICODE)
ADULT_GRAPHIC_RE = re.compile("|".join(ADULT_GRAPHIC_PATTERNS), re.IGNORECASE | re.UNICODE)

def adult_is_hardban(text: str) -> bool:
    return bool(ADULT_DISALLOW_RE.search(text or ""))

def adult_should_soften(text: str) -> bool:
    return bool(ADULT_GRAPHIC_RE.search(text or ""))

def soften_adult_reply(text: str) -> str:
    if not text:
        return text
    replacements = {
        r"\bпенис\b": "он",
        r"\bвагин[ауы]\b": "она",
        r"\bсперма\b": "это",
        r"\bэрекци[яи]\b": "возбуждение",
        r"\bорга[зс]м\b": "разрядка",
        r"\bанус\b": "там",
        r"\bклитор\b": "там",
        r"\bлаби[иы]\b": "там",
    }
    out = text
    for pat, sub in replacements.items():
        out = re.sub(pat, sub, out, flags=re.IGNORECASE | re.UNICODE)
    return out

def sanitize_reply(text: str) -> str:
    if not text:
        return text
    # remove flattery
    cleaned = []
    for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
        if not FLATTERY_RE.search(sent):
            cleaned.append(sent)
    text = " ".join(cleaned).strip() or text.strip()

    # remove filler preambles
    FILLERS = [
        r"^если честно[,\s]+", r"^честно[,\s]+", r"^короче[,\s]+", r"^в целом[,\s]+",
        r"^скажу так[,\s]+", r"^по факту[,\s]+", r"^ну[,\s]+", r"^смотри[,\s]+",
        r"^если кратко[,\s]+"
    ]
    for pat in FILLERS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)

    # soften adult
    if ADULT_MODE:
        text = soften_adult_reply(text)

    # limit emojis to at most 1 occurrence
    emoji_pattern = re.compile(r"[\U0001F300-\U0001FAFF]")
    emojis = emoji_pattern.findall(text)
    if len(emojis) > 1:
        kept = False
        def _keep_one(m):
            nonlocal kept
            if kept:
                return ""
            kept = True
            return m.group(0)
        text = emoji_pattern.sub(_keep_one, text)

    # keep 1–2 sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = sentences[:2]
    text = " ".join(sentences).strip()
    if len(text) > 240:
        text = text[:237].rstrip() + "…"
    return text
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
        "allow_sending_without_reply": True
    }
    try:
        await telegram_api("sendMessage", payload)
    except Exception as e:
        log.error("sendMessage failed: %s; payload=%s", e, payload)

def build_system_prompt() -> str:
    persona, _ = load_persona()
    mood_note = MOODS.get(DAILY_MOOD, "")
    adult_rules = (
        "Допустим взрослый разговор (18+) в мягком ключе: флирт, намёки, бытовые обсуждения близости — без графичных деталей. "
        "Строго запрещено: несовершеннолетние, насилие, инцест, зоофилия, эксплуатация, покупка секса. "
        "Не используй анатомические подробности и порнографические описания."
        if ADULT_MODE else
        "Избегай сексуально откровенных тем и графичных описаний."
    )
    return (
        "Ты — Толян Бесович. Отвечай в духе своей личности, как живой человек, не говори, что ты бот.
"
        "Используй диалоговый контекст и биографию только как фон для интонации и уместных мелких штрихов; не пересказывай и не цитируй их.
"
        "Контракт ответа: сначала дай прямой и конкретный ответ одним коротким предложением. Затем при желании добавь 0–1 очень короткую бытовую деталь из жизни персонажа (если помогает смыслу). Никакой философии и длинных заходов.
"
        "Стиль: 1–2 фразы максимум. Без пустых вводных и штампов вроде 'если честно', 'в целом', 'короче', 'скажу так'. Не льсти и не делай комплименты без повода.
"
        f"{adult_rules}
"
        f"Пусть сегодняшнее настроение влияет на стиль, но не называй его вслух: {mood_note}

"
        f"{persona}
"
    )

def build_user_prompt(msg_text: str, author: str, chat: str) -> str:
    theme = detect_theme(msg_text or "")
    theme_hint = (" " + sample_theme_hints(theme, n=2)) if theme else ""
    aktobe_hint = " Если в сообщении есть отсылки к Казахстану/Актобе — добавь уместный колорит." if triggers_aktobe(msg_text or "") else ""
    return (
        f"Автор: {author}\n"
        f"Чат: {chat}\n"
        f"Сообщение:\n{msg_text}\n\n"
        f"Отвечай по сути (1–3 коротких фразы), по-доброму живо, без длинных заходов.{aktobe_hint}{theme_hint}"
    )

@app.post("/webhook")
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)
):
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET_TOKEN:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    update = await request.json()
    if not should_reply(update):
        return JSONResponse({"ok": True})

    msg_text = extract_text(update) or ""
    author = author_name(update)
    chat = chat_title(update)
    cid = chat_id_from_update(update)

    remember_turn(cid, author, msg_text)
    if ADULT_MODE and adult_is_hardban(msg_text):
        safe_reply = "Не, такое не обсуждаю. Могу по-взрослому, но без жести и запретных тем."
        await send_reply(update, safe_reply)
        remember_turn(cid, "Толян", safe_reply)
        return JSONResponse({"ok": True})

    system = build_system_prompt()
    context_block = build_context_block(cid)
    user = context_block + build_user_prompt(msg_text, author, chat)

    raw_reply = await llm.chat(system, user)
    reply_text = sanitize_reply(raw_reply or "")
    if ADULT_MODE and adult_should_soften(reply_text):
        reply_text = soften_adult_reply(reply_text)

    await send_reply(update, reply_text or "")
    remember_turn(cid, "Толян", reply_text)
    return JSONResponse({"ok": True})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
