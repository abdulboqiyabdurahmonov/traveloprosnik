# -*- coding: utf-8 -*-
"""
TripleA Travel Survey Bot — опросник для турфирм (RU/UZ).
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

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
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
        "timestamp", "user_id", "username", "lang",
        "company_name", "city", "years_on_market", "team_size",
        "lead_channels", "leads_per_week", "crm_usage",
        "payment_methods", "online_booking", "docs_delivery",
        "interested_in_aggregator", "aggregator_values",
        "monetization_preference",
        "free_insight_1", "free_insight_2", "free_insight_3",
        "contact"
    ])

# ---------- Тексты и опции ----------
RU = "ru"
UZ = "uz"

TEXT: Dict[str, Dict[str, str]] = {
    "choose_lang": {
        RU: "Выберите язык ⤵️",
        UZ: "Tilni tanlang ⤵️",
    },
    "lang_ru": {RU: "Русский", UZ: "Ruscha"},
    "lang_uz": {RU: "Узбекский", UZ: "O‘zbekcha"},
    "welcome": {
        RU: "Привет! Это опрос для турфирм об их процессах и интересе к агрегатору.\n"
            "Займёт 2–3 минуты. Можно остановиться командой /cancel.\n\n<b>1/17 — Название вашей компании?</b>",
        UZ: "Salom! Bu turfirmalar uchun jarayonlari va agregatorga qiziqishi haqida so‘rovnoma.\n"
            "2–3 daqiqa oladi. /cancel buyrug‘i bilan to‘xtatish mumkin.\n\n<b>1/17 — Kompaniyangiz nomi?</b>",
    },
    "cancelled": {
        RU: "Ок, опрос отменён. Можно перезапустить через /start.",
        UZ: "Yaxshi, so‘rovnoma bekor qilindi. /start bilan qayta boshlashingiz mumkin."
    },
    "q_city": {RU: "<b>2/17 — Город?</b>", UZ: "<b>2/17 — Shahar?</b>"},
    "q_years": {RU: "<b>3/17 — Сколько лет на рынке?</b>", UZ: "<b>3/17 — Bozorda nechchi yil?</b>"},
    "q_team": {RU: "<b>4/17 — Размер команды?</b>", UZ: "<b>4/17 — Jamoa hajmi?</b>"},
    "q_leads_from_open": {
        RU: "<b>5/17 — Откуда получаете заявки?</b>\nВыберите вариант(ы) и нажмите «Готово».",
        UZ: "<b>5/17 — Murojaatlar qayerdan keladi?</b>\nVariant(lar)ni tanlab «Tayyor» tugmasini bosing."
    },
    "q_leads_from_tap": {
        RU: "<b>5/17 — Откуда получаете заявки?</b>\nТапайте по вариантам, затем «Готово».",
        UZ: "<b>5/17 — Murojaatlar qayerdan keladi?</b>\nVariantlarga bosing, so‘ng «Tayyor»."
    },
    "q_leads_week": {RU: "<b>6/17 — Сколько заявок в неделю?</b>", UZ: "<b>6/17 — Haftasiga nechta murojaat?</b>"},
    "q_crm": {RU: "<b>7/17 — Чем ведёте клиентов?</b>", UZ: "<b>7/17 — Mijozlarni qaysi tizimda yuritasiz?</b>"},
    "q_pay_open": {
        RU: "<b>8/17 — Как принимаете оплаты?</b>\nВыберите и нажмите «Готово».",
        UZ: "<b>8/17 — To‘lovlarni qanday qabul qilasiz?</b>\nTanlang va «Tayyor» bosing."
    },
    "q_booking": {RU: "<b>9/17 — Онлайн-бронь есть?</b>", UZ: "<b>9/17 — Onlayn bron bormi?</b>"},
    "q_docs": {RU: "<b>10/17 — Как отправляете клиенту документы?</b>", UZ: "<b>10/17 — Hujjatlarni mijozga qanday jo‘natasiz?</b>"},
    "q_agg_int": {RU: "<b>11/17 — Интересен ли агрегатор в Telegram?</b>", UZ: "<b>11/17 — Telegramda agregator qiziqmi?</b>"},
    "q_vals_open": {
        RU: "<b>12/17 — Что важнее всего в агрегаторе?</b>",
        UZ: "<b>12/17 — Agregatorda eng muhim narsa nima?</b>"
    },
    "q_monet": {RU: "<b>13/17 — Монетизация: что вам ок?</b>", UZ: "<b>13/17 — Monetizatsiya: nimasi maqul?</b>"},
    "q_ins1": {
        RU: "<b>14/17 — Если бы была «волшебная кнопка» автоматизации — что бы она делала?</b>\n(свободный ответ)",
        UZ: "<b>14/17 — «Sehrli tugma» avtomatlashtirish bo‘lsa, nima qilganini xohlar edingiz?</b>\n(ozod javob)"
    },
    "q_ins2": {
        RU: "<b>15/17 — Что больше всего раздражает в работе с клиентами?</b>",
        UZ: "<b>15/17 — Mijozlar bilan ishlashda eng hafsalani pir qiladigan narsa nima?</b>"
    },
    "q_ins3": {
        RU: "<b>16/17 — Какой самый частый вопрос от клиента?</b>",
        UZ: "<b>16/17 — Mijozlardan eng ko‘p beriladigan savol qanday?</b>"
    },
    "q_contact": {
        RU: "<b>17/17 — Контакт для связи</b> (телефон или @username)",
        UZ: "<b>17/17 — Aloqa uchun kontakt</b> (telefon yoki @username)"
    },
    "thanks": {
        RU: "Спасибо! Анкета сохранена. Мы свяжемся с вами для демо агрегатора 🚀",
        UZ: "Rahmat! So‘rovnoma saqlandi. Agregator demosi uchun siz bilan bog‘lanamiz 🚀"
    },
    "open_variants": {RU: "Открыть варианты", UZ: "Variantlarni ochish"},
    "done": {RU: "✅ Готово", UZ: "✅ Tayyor"},
    "saved_choice": {RU: "Выбор сохранён", UZ: "Tanlov saqlandi"},
    "cancel_btn": {RU: "Отменить", UZ: "Bekor qilish"},
}

OPTIONS = {
    "TEAM_SIZES": {
        RU: ["1–3", "4–10", "11–30", "30+"],
        UZ: ["1–3", "4–10", "11–30", "30+"],
    },
    "LEAD_CHANNELS": {
        RU: ["Instagram", "Telegram", "WhatsApp", "Сайт", "Через агентов", "Другое"],
        UZ: ["Instagram", "Telegram", "WhatsApp", "Sayt", "Agentlar orqali", "Boshqa"],
    },
    "LEADS_PER_WEEK": {
        RU: ["1–10", "10–50", "50–100", "100+"],
        UZ: ["1–10", "10–50", "50–100", "100+"],
    },
    "CRM_USAGE": {
        RU: ["CRM", "Excel", "Только мессенджеры", "Нет системы"],
        UZ: ["CRM", "Excel", "Faqat messenjerlar", "Tizim yo‘q"],
    },
    "PAYMENT_METHODS": {
        RU: ["Наличные", "Перевод на карту", "Click", "Payme", "Apelsin", "Через юрлицо", "Другое"],
        UZ: ["Naqd", "Kartaga o‘tkazma", "Click", "Payme", "Apelsin", "Yuridik shaxs orqali", "Boshqa"],
    },
    "ONLINE_BOOKING": {
        RU: ["Есть, через сайт", "Только вручную", "Частично"],
        UZ: ["Bor, sayt orqali", "Faqat qo‘lda", "Qisman"],
    },
    "DOCS_DELIVERY": {
        RU: ["Вручную в мессенджере", "Через систему/CRM", "Другое"],
        UZ: ["Messenjerda qo‘lda", "Tizim/CRM orqali", "Boshqa"],
    },
    "AGG_INT": {
        RU: ["Да, очень", "Возможно", "Нет, своя система"],
        UZ: ["Ha, juda qiziq", "Balki", "Yo‘q, o‘z tizimimiz bor"],
    },
    "AGG_VALUES": {
        RU: ["Больше клиентов", "Управление турами", "Онлайн-оплата", "Отчёты и аналитика", "Отзывы клиентов"],
        UZ: ["Ko‘proq mijozlar", "Turlarni boshqarish", "Onlayn to‘lov", "Hisobot va analitika", "Mijozlar fikrlari"],
    },
    "MONETIZATION": {
        RU: ["Комиссия за заявку (5–10%)", "Фиксированная подписка", "Только бесплатно"],
        UZ: ["Ariza uchun komissiya (5–10%)", "Fiks obuna", "Faqat bepul"],
    },
}

# ---------- FSM ----------
class Survey(StatesGroup):
    lang = State()
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

# ---------- Утилиты ----------
def t(key: str, lang: str) -> str:
    return TEXT[key][lang]

def opts(name: str, lang: str) -> List[str]:
    return OPTIONS[name][lang]

def kb_rows(options: List[str], lang: str, row: int = 2) -> ReplyKeyboardMarkup:
    btns: List[List[KeyboardButton]] = []
    chunk: List[KeyboardButton] = []
    for i, o in enumerate(options, 1):
        chunk.append(KeyboardButton(text=o))
        if i % row == 0:
            btns.append(chunk); chunk = []
    if chunk:
        btns.append(chunk)
    btns.append([KeyboardButton(text=t("cancel_btn", lang))])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def inline_multi(options: List[str], lang: str, prefix: str = "mv") -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for i, o in enumerate(options):
        rows.append([InlineKeyboardButton(text=f"▫️ {o}", callback_data=f"{prefix}:toggle:{i}")])
    rows.append([InlineKeyboardButton(text=t("done", lang), callback_data=f"{prefix}:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def now_ts() -> str:
    return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")

async def get_lang(state: FSMContext) -> str:
    data = await state.get_data()
    return data.get("lang", RU)

# ---------- Команды ----------
@rt.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("lang_ru", RU), callback_data="lang:ru"),
        InlineKeyboardButton(text=t("lang_uz", UZ), callback_data="lang:uz"),
    ]])
    # Показываем на двух языках приглашение к выбору
    await m.answer(f"{t('choose_lang', RU)}\n{t('choose_lang', UZ)}", reply_markup=kb)
    await state.set_state(Survey.lang)

@rt.callback_query(Survey.lang, F.data.in_(["lang:ru", "lang:uz"]))
async def pick_lang(cb: CallbackQuery, state: FSMContext):
    lang = RU if cb.data.endswith("ru") else UZ
    await state.update_data(lang=lang)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t("welcome", lang))
    await state.set_state(Survey.company)
    await cb.answer()

@rt.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.clear()
    await m.answer(t("cancelled", lang), reply_markup=ReplyKeyboardRemove())

@rt.message(Command("help"))
async def help_cmd(m: Message, state: FSMContext):
    lang = await get_lang(state)
    txt = "Команды: /start — начать, /cancel — отменить." if lang == RU else "Buyruqlar: /start — boshlash, /cancel — bekor qilish."
    await m.answer(txt)

# ---------- Шаги опроса ----------
@rt.message(Survey.company)
async def q_company(m: Message, state: FSMContext):
    lang = await get_lang(state)
    if not m.text or m.text.lower() in {"отменить", "bekor qilish"}:
        return await cancel(m, state)
    await state.update_data(company_name=m.text.strip())
    await m.answer(t("q_city", lang))
    await state.set_state(Survey.city)

@rt.message(Survey.city)
async def q_city(m: Message, state: FSMContext):
    lang = await get_lang(state)
    if not m.text or m.text.lower() in {"отменить", "bekor qilish"}:
        return await cancel(m, state)
    await state.update_data(city=m.text.strip())
    await m.answer(t("q_years", lang))
    await state.set_state(Survey.years)

@rt.message(Survey.years)
async def q_years(m: Message, state: FSMContext):
    lang = await get_lang(state)
    if not m.text or m.text.lower() in {"отменить", "bekor qilish"}:
        return await cancel(m, state)
    await state.update_data(years=m.text.strip())
    await m.answer(t("q_team", lang), reply_markup=kb_rows(opts("TEAM_SIZES", lang), lang))
    await state.set_state(Survey.team)

@rt.message(Survey.team, F.text.func(lambda v: v in OPTIONS["TEAM_SIZES"][RU] + OPTIONS["TEAM_SIZES"][UZ]))
async def q_team(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(team_size=m.text)
    open_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("open_variants", lang), callback_data="mv:open")]
    ])
    await m.answer(t("q_leads_from_open", lang), reply_markup=open_btn)
    await state.set_state(Survey.leads_from)

@rt.callback_query(Survey.leads_from, F.data == "mv:open")
async def leads_open(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_text(t("q_leads_from_tap", lang),
                               reply_markup=inline_multi(opts("LEAD_CHANNELS", lang), lang, "lead"))
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
        lang = (await state.get_data()).get("lang", RU)
        rows.append([InlineKeyboardButton(text=t("done", lang), callback_data=f"{prefix}:done")])
        await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await cb.answer()
    elif action == "done":
        lang = (await state.get_data()).get("lang", RU)
        await cb.answer(t("saved_choice", lang), show_alert=False)

@rt.callback_query(Survey.leads_from, F.data == "lead:done")
async def leads_done(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t("q_leads_week", lang), reply_markup=kb_rows(opts("LEADS_PER_WEEK", lang), lang))
    await state.set_state(Survey.leads_week)
    await cb.answer()

@rt.callback_query(Survey.leads_from, F.data.startswith("lead:toggle:"))
async def leads_toggle(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await toggle_multi(cb, state, "leads_from_idx", opts("LEAD_CHANNELS", lang), "lead")

@rt.message(Survey.leads_week, F.text.func(lambda v: v in OPTIONS["LEADS_PER_WEEK"][RU] + OPTIONS["LEADS_PER_WEEK"][UZ]))
async def q_leads_week(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(leads_per_week=m.text)
    await m.answer(t("q_crm", lang), reply_markup=kb_rows(opts("CRM_USAGE", lang), lang))
    await state.set_state(Survey.crm)

@rt.message(Survey.crm, F.text.func(lambda v: v in OPTIONS["CRM_USAGE"][RU] + OPTIONS["CRM_USAGE"][UZ]))
async def q_crm(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(crm_usage=m.text)
    open_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("open_variants", lang), callback_data="pay:open")]
    ])
    await m.answer(t("q_pay_open", lang), reply_markup=open_btn)
    await state.set_state(Survey.pay)

@rt.callback_query(Survey.pay, F.data == "pay:open")
async def pay_open(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_text(t("q_pay_open", lang),
                               reply_markup=inline_multi(opts("PAYMENT_METHODS", lang), lang, "paym"))
    await cb.answer()

@rt.callback_query(Survey.pay, F.data == "paym:done")
async def pay_done(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t("q_booking", lang), reply_markup=kb_rows(opts("ONLINE_BOOKING", lang), lang))
    await state.set_state(Survey.booking)
    await cb.answer()

@rt.callback_query(Survey.pay, F.data.startswith("paym:toggle:"))
async def pay_toggle(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await toggle_multi(cb, state, "pay_idx", opts("PAYMENT_METHODS", lang), "paym")

@rt.message(Survey.booking, F.text.func(lambda v: v in OPTIONS["ONLINE_BOOKING"][RU] + OPTIONS["ONLINE_BOOKING"][UZ]))
async def q_booking(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(online_booking=m.text)
    await m.answer(t("q_docs", lang), reply_markup=kb_rows(opts("DOCS_DELIVERY", lang), lang))
    await state.set_state(Survey.docs)

@rt.message(Survey.docs, F.text.func(lambda v: v in OPTIONS["DOCS_DELIVERY"][RU] + OPTIONS["DOCS_DELIVERY"][UZ]))
async def q_docs(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(docs_delivery=m.text)
    await m.answer(t("q_agg_int", lang), reply_markup=kb_rows(opts("AGG_INT", lang), lang))
    await state.set_state(Survey.agg_interest)

@rt.message(Survey.agg_interest, F.text.func(lambda v: v in OPTIONS["AGG_INT"][RU] + OPTIONS["AGG_INT"][UZ]))
async def q_agg_int(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(agg_interest=m.text)
    open_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("open_variants", lang), callback_data="vals:open")]
    ])
    await m.answer(t("q_vals_open", lang), reply_markup=open_btn)
    await state.set_state(Survey.agg_values)

@rt.callback_query(Survey.agg_values, F.data == "vals:open")
async def vals_open(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_text(t("q_vals_open", lang),
                               reply_markup=inline_multi(opts("AGG_VALUES", lang), lang, "vals"))
    await cb.answer()

@rt.callback_query(Survey.agg_values, F.data == "vals:done")
async def vals_done(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t("q_monet", lang), reply_markup=kb_rows(opts("MONETIZATION", lang), lang))
    await state.set_state(Survey.monetization)
    await cb.answer()

@rt.callback_query(Survey.agg_values, F.data.startswith("vals:toggle:"))
async def vals_toggle(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await toggle_multi(cb, state, "vals_idx", opts("AGG_VALUES", lang), "vals")

@rt.message(Survey.monetization, F.text.func(lambda v: v in OPTIONS["MONETIZATION"][RU] + OPTIONS["MONETIZATION"][UZ]))
async def q_monet(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(monetization=m.text)
    await m.answer(t("q_ins1", lang))
    await state.set_state(Survey.insight1)

@rt.message(Survey.insight1)
async def q_ins1(m: Message, state: FSMContext):
    lang = await get_lang(state)
    if not m.text or m.text.lower() in {"отменить", "bekor qilish"}:
        return await cancel(m, state)
    await state.update_data(free_insight_1=m.text.strip())
    await m.answer(t("q_ins2", lang))
    await state.set_state(Survey.insight2)

@rt.message(Survey.insight2)
async def q_ins2(m: Message, state: FSMContext):
    lang = await get_lang(state)
    if not m.text or m.text.lower() in {"отменить", "bekor qilish"}:
        return await cancel(m, state)
    await state.update_data(free_insight_2=m.text.strip())
    await m.answer(t("q_ins3", lang))
    await state.set_state(Survey.insight3)

@rt.message(Survey.insight3)
async def q_ins3(m: Message, state: FSMContext):
    lang = await get_lang(state)
    if not m.text or m.text.lower() in {"отменить", "bekor qilish"}:
        return await cancel(m, state)
    await state.update_data(free_insight_3=m.text.strip())
    await m.answer(t("q_contact", lang))
    await state.set_state(Survey.contact)

def normalize_contact(txt: str) -> str:
    t = (txt or "").strip()
    if t.startswith("+") and t[1:].replace(" ", "").isdigit():
        return t
    if t.startswith("@") and len(t) > 1:
        return t
    digits = "".join(ch for ch in t if ch.isdigit() or ch == "+")
    return digits if digits else t

@rt.message(Survey.contact)
async def q_contact(m: Message, state: FSMContext):
    lang = await get_lang(state)
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
        lang,
        data.get("company_name", ""),
        data.get("city", ""),
        data.get("years", ""),
        data.get("team_size", ""),
        map_multi("leads_from_idx", opts("LEAD_CHANNELS", lang)),
        data.get("leads_per_week", ""),
        data.get("crm_usage", ""),
        map_multi("pay_idx", opts("PAYMENT_METHODS", lang)),
        data.get("online_booking", ""),
        data.get("docs_delivery", ""),
        data.get("agg_interest", ""),
        map_multi("vals_idx", opts("AGG_VALUES", lang)),
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
        warn = "⚠️ Ошибка сохранения в таблицу. Попробуйте позже." if lang == RU else "⚠️ Jadvalga saqlashda xatolik. Keyinroq urinib ko‘ring."
        await m.answer(warn)
        return

    # Уведомление админам (кратко, на RU, помечаем язык)
    summary = (
        f"<b>[{lang.upper()}] Новая анкета</b>\n"
        f"Компания: {data.get('company_name')}\n"
        f"Город: {data.get('city')}\n"
        f"Команда: {data.get('team_size')}\n"
        f"Лиды: {data.get('leads_per_week')} / Источники: {map_multi('leads_from_idx', opts('LEAD_CHANNELS', lang))}\n"
        f"CRM: {data.get('crm_usage')}\n"
        f"Оплаты: {map_multi('pay_idx', opts('PAYMENT_METHODS', lang))}\n"
        f"Онлайн-бронь: {data.get('online_booking')}\n"
        f"Документы: {data.get('docs_delivery')}\n"
        f"Интерес к агрегатору: {data.get('agg_interest')}\n"
        f"Ценности: {map_multi('vals_idx', opts('AGG_VALUES', lang))}\n"
        f"Монетизация: {data.get('monetization')}\n"
        f"Контакт: {contact}\n"
        f"— @{user.username or '—'} | {user.id}"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, summary)
        except Exception:
            pass

    await m.answer(t("thanks", lang), reply_markup=ReplyKeyboardRemove())
    await state.clear()

# ---------- FastAPI / webhook ----------
app = FastAPI()

@app.get("/", include_in_schema=False)
async def root_health():
    return JSONResponse({"status": "ok", "service": "TravelOprosnik", "time": datetime.utcnow().isoformat()})

@app.get("/healthz", include_in_schema=False)
async def healthz():
    return PlainTextResponse("ok", status_code=200)

@app.get("/webhook", include_in_schema=False)
async def webhook_ping():
    return PlainTextResponse("ok", status_code=200)

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
