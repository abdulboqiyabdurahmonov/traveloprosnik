# -*- coding: utf-8 -*-
"""
TripleA Travel Survey Bot — опросник для турфирм.
Стек: FastAPI (webhook), Aiogram v3, gspread (Google Sheets), Render-ready.

ENV VARS REQUIRED
-----------------
BOT_TOKEN=...                           # Telegram bot token
WEBHOOK_URL=https://your-service.onrender.com/webhook
GOOGLE_SERVICE_ACCOUNT_JSON=...         # raw JSON of service account (в одну строку)
SHEET_ID=...                            # Google Sheet spreadsheet ID

OPTIONAL
--------
ADMINS=123456789,987654321              # кому слать алерты о новых анкетах
TZ=Asia/Tashkent                        # таймзона для меток времени (по умолчанию Asia/Tashkent)

Render: Старт-команда
---------------------
uvicorn bot:app --host 0.0.0.0 --port 10000
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

import gspread
from datetime import datetime
from zoneinfo import ZoneInfo

# ---------- Конфиг ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
SHEET_ID = os.environ["SHEET_ID"]
SERVICE_JSON_RAW = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
ADMINS = [int(x) for x in os.getenv("ADMINS", "").replace(" ", "").split(",") if x]
TZ = os.getenv("TZ", "Asia/Tashkent")

# ---------- Логи ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("triplea.survey")

# ---------- Google Sheets ----------
sa_info = json.loads(SERVICE_JSON_RAW)
gc = gspread.service_account_from_dict(sa_info)
sh = gc.open_by_key(SHEET_ID)
# Лист создадим/возьмём "Survey"
try:
    ws = sh.worksheet("Survey")
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title="Survey", rows=2000, cols=30)
    ws.append_row([
        "timestamp", "user_id", "username",
        "company_name", "city", "years_on_market", "team_size",
        "lead_channels", "leads_per_week", "crm_usage",
        "payment_methods", "online_booking", "docs_delivery",
        "interested_in_aggregator", "aggregator_values",
        "monetization_preference",
        "free_insight_1", "free_insight_2", "free_insight_3",
        "contact"
    ])

# ---------- Вопросы/опции ----------
TEAM_SIZES = ["1–3", "4–10", "11–30", "30+"]
LEAD_CHANNELS = ["Instagram", "Telegram", "WhatsApp", "Сайт", "Через агентов", "Другое"]
LEADS_PER_WEEK = ["1–10", "10–50", "50–100", "100+"]
CRM_USAGE = ["CRM", "Excel", "Только мессенджеры", "Нет системы"]
PAYMENT_METHODS = ["Наличные", "Перевод на карту", "Click", "Payme", "Apelsin", "Через юрлицо", "Другое"]
ONLINE_BOOKING = ["Есть, через сайт", "Только вручную", "Частично"]
DOCS_DELIVERY = ["Вручную в мессенджере", "Через систему/CRM", "Другое"]
AGG_INT = ["Да, очень", "Возможно", "Нет, своя система"]
AGG_VALUES = ["Больше клиентов", "Управление турами", "Онлайн-оплата", "Отчёты и аналитика", "Отзывы клиентов"]
MONETIZATION = ["Комиссия за заявку (5–10%)", "Фиксированная подписка", "Только бесплатно"]

# ---------- FSM ----------
class Survey(StatesGroup):
    company = State()
    city = State()
    years = State()
    team = State()
    leads_from = State()
    leads_week = State()
    crm = State()
    pay = State()
    booking = State()
    docs = State()
    agg_interest = State()
    agg_values = State()
    monetization = State()
    insight1 = State()
    insight2 = State()
    insight3 = State()
    contact = State()

# ---------- Телега ----------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher()
rt = Router()
dp.include_router(rt)

def kb_rows(options: List[str], row: int = 2) -> ReplyKeyboardMarkup:
    btns: List[List[KeyboardButton]] = []
    chunk: List[KeyboardButton] = []
    for i, o in enumerate(options, 1):
        chunk.append(KeyboardButton(text=o))
        if i % row == 0:
            btns.append(chunk)
            chunk = []
    if chunk:
        btns.append(chunk)
    # добавить кнопку отмены
    btns.append([KeyboardButton(text="Отменить")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def inline_multi(options: List[str], prefix: str = "mv") -> InlineKeyboardMarkup:
    # мультивыбор через инлайн-кнопки с toggle и кнопкой "Далее"
    rows: List[List[InlineKeyboardButton]] = []
    for i, o in enumerate(options):
        rows.append([InlineKeyboardButton(text=f"▫️ {o}", callback_data=f"{prefix}:toggle:{i}")])
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"{prefix}:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def now_ts() -> str:
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")

# ---------- Команды ----------
@rt.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "Привет! Это опрос для турфирм об их процессах и интересе к агрегатору.\n"
        "Займёт 2–3 минуты. Можно остановиться командой /cancel.\n\n"
        "<b>1/17 — Название вашей компании?</b>"
    )
    await state.set_state(Survey.company)

@rt.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Ок, опрос отменён. Можно перезапустить через /start.")

@rt.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer("Команды: /start — начать опрос, /cancel — отменить, /help — помощь.")

# ---------- Шаги опроса ----------
@rt.message(Survey.company)
async def q_company(m: Message, state: FSMContext):
    if not m.text or m.text.lower() == "отменить":
        return await cancel(m, state)
    await state.update_data(company_name=m.text.strip())
    await m.answer("<b>2/17 — Город?</b>")
    await state.set_state(Survey.city)

@rt.message(Survey.city)
async def q_city(m: Message, state: FSMContext):
    if not m.text or m.text.lower() == "отменить":
        return await cancel(m, state)
    await state.update_data(city=m.text.strip())
    await m.answer("<b>3/17 — Сколько лет на рынке?</b>")
    await state.set_state(Survey.years)

@rt.message(Survey.years)
async def q_years(m: Message, state: FSMContext):
    if not m.text or m.text.lower() == "отменить":
        return await cancel(m, state)
    await state.update_data(years=m.text.strip())
    await m.answer("<b>4/17 — Размер команды?</b>", reply_markup=kb_rows(TEAM_SIZES))
    await state.set_state(Survey.team)

@rt.message(Survey.team, F.text.in_(TEAM_SIZES))
async def q_team(m: Message, state: FSMContext):
    await state.update_data(team_size=m.text)
    await m.answer("<b>5/17 — Откуда получаете заявки?</b>\nВыберите вариант(ы) и нажмите «Готово».",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть варианты", callback_data="mv:open")]]))
    await state.set_state(Survey.leads_from)

@rt.callback_query(Survey.leads_from, F.data == "mv:open")
async def leads_open(cb: CallbackQuery):
    await cb.message.edit_text(
        "<b>5/17 — Откуда получаете заявки?</b>\nТапайте по вариантам, затем «Готово».",
        reply_markup=inline_multi(LEAD_CHANNELS, "lead")
    )
    await cb.answer()

# универсальный обработчик мультивыбора
async def toggle_multi(cb: CallbackQuery, state: FSMContext, key: str, options: List[str], prefix: str):
    data = await state.get_data()
    picked: List[int] = data.get(key, [])
    parts = cb.data.split(":")
    _, action, idx = parts[0], parts[1], (int(parts[2]) if len(parts) > 2 else None)

    if action == "toggle" and idx is not None:
        if idx in picked:
            picked.remove(idx)
        else:
            picked.append(idx)
        await state.update_data(**{key: picked})
        # обновим визуально
        rows = []
        for i, o in enumerate(options):
            mark = "✅" if i in picked else "▫️"
            rows.append([InlineKeyboardButton(text=f"{mark} {o}", callback_data=f"{prefix}:toggle:{i}")])
        rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"{prefix}:done")])
        await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await cb.answer()
    elif action == "done":
        await cb.answer("Выбор сохранён", show_alert=False)

@rt.callback_query(Survey.leads_from, F.data.startswith("lead:"))
async def leads_toggle(cb: CallbackQuery, state: FSMContext):
    await toggle_multi(cb, state, "leads_from_idx", LEAD_CHANNELS, "lead")

@rt.callback_query(Survey.leads_from, F.data == "lead:done")
async def leads_done(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("<b>6/17 — Сколько заявок в неделю?</b>", reply_markup=kb_rows(LEADS_PER_WEEK))
    await state.set_state(Survey.leads_week)
    await cb.answer()

@rt.message(Survey.leads_week, F.text.in_(LEADS_PER_WEEK))
async def q_leads_week(m: Message, state: FSMContext):
    await state.update_data(leads_per_week=m.text)
    await m.answer("<b>7/17 — Чем ведёте клиентов?</b>", reply_markup=kb_rows(CRM_USAGE))
    await state.set_state(Survey.crm)

@rt.message(Survey.crm, F.text.in_(CRM_USAGE))
async def q_crm(m: Message, state: FSMContext):
    await state.update_data(crm_usage=m.text)
    await m.answer("<b>8/17 — Как принимаете оплаты?</b>\nВыберите и нажмите «Готово».",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть варианты", callback_data="pay:open")]]))
    await state.set_state(Survey.pay)

@rt.callback_query(Survey.pay, F.data == "pay:open")
async def pay_open(cb: CallbackQuery):
    await cb.message.edit_text("<b>8/17 — Как принимаете оплаты?</b>", reply_markup=inline_multi(PAYMENT_METHODS, "paym"))
    await cb.answer()

@rt.callback_query(Survey.pay, F.data.startswith("paym:"))
async def pay_toggle(cb: CallbackQuery, state: FSMContext):
    await toggle_multi(cb, state, "pay_idx", PAYMENT_METHODS, "paym")

@rt.callback_query(Survey.pay, F.data == "paym:done")
async def pay_done(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("<b>9/17 — Онлайн-бронь есть?</b>", reply_markup=kb_rows(ONLINE_BOOKING))
    await state.set_state(Survey.booking)
    await cb.answer()

@rt.message(Survey.booking, F.text.in_(ONLINE_BOOKING))
async def q_booking(m: Message, state: FSMContext):
    await state.update_data(online_booking=m.text)
    await m.answer("<b>10/17 — Как отправляете клиенту документы?</b>", reply_markup=kb_rows(DOCS_DELIVERY))
    await state.set_state(Survey.docs)

@rt.message(Survey.docs, F.text.in_(DOCS_DELIVERY))
async def q_docs(m: Message, state: FSMContext):
    await state.update_data(docs_delivery=m.text)
    await m.answer("<b>11/17 — Интересен ли агрегатор в Telegram?</b>", reply_markup=kb_rows(AGG_INT))
    await state.set_state(Survey.agg_interest)

@rt.message(Survey.agg_interest, F.text.in_(AGG_INT))
async def q_agg_int(m: Message, state: FSMContext):
    await state.update_data(agg_interest=m.text)
    await m.answer("<b>12/17 — Что важнее всего в агрегаторе?</b>\nВыберите и нажмите «Готово».",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть варианты", callback_data="vals:open")]]))
    await state.set_state(Survey.agg_values)

@rt.callback_query(Survey.agg_values, F.data == "vals:open")
async def vals_open(cb: CallbackQuery):
    await cb.message.edit_text("<b>12/17 — Что важнее всего в агрегаторе?</b>", reply_markup=inline_multi(AGG_VALUES, "vals"))
    await cb.answer()

@rt.callback_query(Survey.agg_values, F.data.startswith("vals:"))
async def vals_toggle(cb: CallbackQuery, state: FSMContext):
    await toggle_multi(cb, state, "vals_idx", AGG_VALUES, "vals")

@rt.callback_query(Survey.agg_values, F.data == "vals:done")
async def vals_done(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("<b>13/17 — Монетизация: что вам ок?</b>", reply_markup=kb_rows(MONETIZATION))
    await state.set_state(Survey.monetization)
    await cb.answer()

@rt.message(Survey.monetization, F.text.in_(MONETIZATION))
async def q_monet(m: Message, state: FSMContext):
    await state.update_data(monetization=m.text)
    await m.answer("<b>14/17 — Если бы была «волшебная кнопка» автоматизации — что бы она делала?</b>\n(свободный ответ)")
    await state.set_state(Survey.insight1)

@rt.message(Survey.insight1)
async def q_ins1(m: Message, state: FSMContext):
    if not m.text or m.text.lower() == "отменить":
        return await cancel(m, state)
    await state.update_data(free_insight_1=m.text.strip())
    await m.answer("<b>15/17 — Что больше всего раздражает в работе с клиентами?</b>")
    await state.set_state(Survey.insight2)

@rt.message(Survey.insight2)
async def q_ins2(m: Message, state: FSMContext):
    if not m.text or m.text.lower() == "отменить":
        return await cancel(m, state)
    await state.update_data(free_insight_2=m.text.strip())
    await m.answer("<b>16/17 — Какой самый частый вопрос от клиента?</b>")
    await state.set_state(Survey.insight3)

@rt.message(Survey.insight3)
async def q_ins3(m: Message, state: FSMContext):
    if not m.text or m.text.lower() == "отменить":
        return await cancel(m, state)
    await state.update_data(free_insight_3=m.text.strip())
    await m.answer("<b>17/17 — Контакт для связи</b> (телефон или @username)")
    await state.set_state(Survey.contact)

def normalize_contact(txt: str) -> str:
    t = (txt or "").strip()
    if t.startswith("+") and t[1:].replace(" ", "").isdigit():
        return t
    if t.startswith("@") and len(t) > 1:
        return t
    # простая эвристика
    digits = "".join(ch for ch in t if ch.isdigit() or ch == "+")
    return digits if digits else t

@rt.message(Survey.contact)
async def q_contact(m: Message, state: FSMContext):
    contact = normalize_contact(m.text or "")
    await state.update_data(contact=contact)

    data = await state.get_data()
    user = m.from_user

    # Преобразуем мультивыборы из индексов в строки
    def map_multi(key_idx: str, options: List[str]) -> str:
        idxs = data.get(key_idx, [])
        return ", ".join(options[i] for i in sorted(set(idxs))) if idxs else ""

    row = [
        now_ts(),
        str(user.id),
        (user.username or ""),
        data.get("company_name", ""),
        data.get("city", ""),
        data.get("years", ""),
        data.get("team_size", ""),
        map_multi("leads_from_idx", LEAD_CHANNELS),
        data.get("leads_per_week", ""),
        data.get("crm_usage", ""),
        map_multi("pay_idx", PAYMENT_METHODS),
        data.get("online_booking", ""),
        data.get("docs_delivery", ""),
        data.get("agg_interest", ""),
        map_multi("vals_idx", AGG_VALUES),
        data.get("monetization", ""),
        data.get("free_insight_1", ""),
        data.get("free_insight_2", ""),
        data.get("free_insight_3", ""),
        data.get("contact", ""),
    ]
    try:
        ws.append_row(row)
    except Exception as e:
        log.exception("Append to sheet failed")
        await m.answer("⚠️ Ошибка сохранения в таблицу. Ответы не потеряны, попробуйте ещё раз позже.")
        return

    # Уведомление админам
    summary = (
        f"<b>Новая анкета турфирмы</b>\n"
        f"Компания: {data.get('company_name')}\n"
        f"Город: {data.get('city')}\n"
        f"Команда: {data.get('team_size')}\n"
        f"Лиды: {data.get('leads_per_week')} / Источники: {map_multi('leads_from_idx', LEAD_CHANNELS)}\n"
        f"CRM: {data.get('crm_usage')}\n"
        f"Оплаты: {map_multi('pay_idx', PAYMENT_METHODS)}\n"
        f"Онлайн-бронь: {data.get('online_booking')}\n"
        f"Документы: {data.get('docs_delivery')}\n"
        f"Интерес к агрегатору: {data.get('agg_interest')}\n"
        f"Ценности: {map_multi('vals_idx', AGG_VALUES)}\n"
        f"Монетизация: {data.get('monetization')}\n"
        f"Контакт: {contact}\n"
        f"— @{user.username or '—'} | {user.id}"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, summary)
        except Exception:
            pass

    await m.answer("Спасибо! Анкета сохранена. Мы свяжемся с вами для демо агрегатора 🚀")
    await state.clear()

# ---------- FastAPI / webhook ----------
app = FastAPI()

@app.get("/")
def root():
    return PlainTextResponse("TripleA Travel Survey Bot: OK")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    await dp.feed_webhook_update(bot, update)
    return PlainTextResponse("ok")

# при старте выставим вебхук
@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(
        url=WEBHOOK_URL,
        drop_pending_updates=True,
    )
    log.info("Webhook set: %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass

# --- health endpoints ---
from fastapi.responses import PlainTextResponse, JSONResponse
from datetime import datetime

@app.get("/", include_in_schema=False)
async def root_health():
    # HEAD тоже начнёт работать автоматически
    return JSONResponse({"status": "ok", "service": "TravelOprosnik", "time": datetime.utcnow().isoformat()})

@app.get("/healthz", include_in_schema=False)
async def healthz():
    return PlainTextResponse("ok", status_code=200)

@app.get("/webhook", include_in_schema=False)
async def webhook_ping():
    return PlainTextResponse("ok", status_code=200)

