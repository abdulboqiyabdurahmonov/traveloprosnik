# -*- coding: utf-8 -*-
"""
TripleA Travel Survey Bot — короткий опрос для турфирм (RU/UZ).
Стек: FastAPI (webhook), Aiogram v3, gspread (Google Sheets), Render-ready.

ENV VARS
BOT_TOKEN=...
WEBHOOK_URL=https://your-service.onrender.com/webhook
GOOGLE_SERVICE_ACCOUNT_JSON=...   # raw JSON в одну строку
SHEET_ID=...
ADMINS=123,456 (опц.)
TZ=Asia/Tashkent (опц.)

Запуск: uvicorn bot:app --host 0.0.0.0 --port 10000
"""
import os, json, logging
from typing import Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove,
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

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("triplea.survey")

# ---------- Google Sheets ----------
sa_info = json.loads(SERVICE_JSON_RAW)
gc = gspread.service_account_from_dict(sa_info)
sh = gc.open_by_key(SHEET_ID)
try:
    ws = sh.worksheet("Survey")
except gspread.WorksheetNotFound:
    ws = sh.add_worksheet(title="Survey", rows=2000, cols=20)
    ws.append_row([
        "timestamp", "user_id", "username", "lang",
        "agg_interest", "agg_values",
        "pain_points", "expectations",
        "contact"
    ])

# ---------- Тексты/опции ----------
RU, UZ = "ru", "uz"

TEXT: Dict[str, Dict[str, str]] = {
    "choose_lang": {RU: "Выберите язык ⤵️", UZ: "Tilni tanlang ⤵️"},
    "lang_ru": {RU: "Русский", UZ: "Ruscha"},
    "lang_uz": {RU: "Узбекский", UZ: "O‘zbekcha"},
    "welcome": {
        RU: (
            "<b>Мы — TripleA Travel</b> (стартап из Узбекистана). "
            "Делаем <b>Telegram-агрегатор заявок</b> для турфирм: единая лента лидов, быстрые шаблоны ответов, "
            "онлайн-оплата и базовая аналитика. Ищем 10–15 пилотных агентств, чтобы собрать обратную связь и "
            "запустить бесплатный MVP на месяц.\n\n"
            "Короткий опрос (5 вопросов) — интерес, боли, ожидания. Можно остановиться /cancel.\n\n"
            "<b>1/5 — Интересен ли агрегатор в Telegram?</b>"
        ),
        UZ: (
            "<b>Biz — TripleA Travel</b> (O‘zbekiston startapi). "
            "Turfirmalar uchun <b>Telegramda arizalar agregatori</b>: yagona lead-lenta, tezkor javob shablonlari, "
            "onlayn to‘lov va oddiy analitika. Pilot uchun 10–15 ta agentlik izlaymiz va 1 oylik bepul MVP ishga tushiramiz.\n\n"
            "Qisqa so‘rov (5 ta savol): qiziqish, og‘riqlar, kutilyotgan natijalar. /cancel bilan to‘xtatish mumkin.\n\n"
            "<b>1/5 — Telegramdagi agregator sizga qiziqmi?</b>"
        ),
    },
    "cancelled": {
        RU: "Ок, опрос отменён. Можно перезапустить через /start.",
        UZ: "Yaxshi, so‘rovnoma bekor qilindi. /start bilan qayta boshlashingiz mumkin."
    },
    "q_agg_int": {
        RU: "<b>1/5 — Интересен ли агрегатор в Telegram?</b>",
        UZ: "<b>1/5 — Telegramdagi agregator sizga qiziqmi?</b>",
    },
    "q_vals_open": {
        RU: "<b>2/5 — Что важнее всего в агрегаторе?</b>\nВыберите вариант(ы) и нажмите «Готово».",
        UZ: "<b>2/5 — Agregatorda eng muhim narsa nima?</b>\nVariant(lar)ni tanlab «Tayyor» tugmasini bosing.",
    },
    "q_vals_tap": {
        RU: "<b>2/5 — Что важнее всего в агрегаторе?</b>\nТапайте по вариантам, затем «Готово».",
        UZ: "<b>2/5 — Agregatorda eng muhim narsa nima?</b>\nVariantlarga bosing, so‘ng «Tayyor».",
    },
    "q_pain": {
        RU: "<b>3/5 — Какие главные боли/узкие места сейчас?</b>\n(свободный ответ)",
        UZ: "<b>3/5 — Hozirgi asosiy muammolar/tor joylar nimalar?</b>\n(ozod javob)",
    },
    "q_expect": {
        RU: "<b>4/5 — Что для вас будет успехом через месяц использования?</b>\n(свободный ответ)",
        UZ: "<b>4/5 — Bir oyda qanday natija muvaffaqiyat deb hisoblaysiz?</b>\n(ozod javob)",
    },
    "q_contact": {
        RU: "<b>5/5 — Контакт для связи</b> (телефон или @username)",
        UZ: "<b>5/5 — Aloqa uchun kontakt</b> (telefon yoki @username)",
    },
    "thanks": {
        RU: "Спасибо! Анкета сохранена. Мы свяжемся с вами для демо 🚀",
        UZ: "Rahmat! So‘rovnoma saqlandi. Demosi uchun siz bilan bog‘lanamiz 🚀",
    },
    "open_variants": {RU: "Открыть варианты", UZ: "Variantlarni ochish"},
    "done": {RU: "✅ Готово", UZ: "✅ Tayyor"},
    "saved_choice": {RU: "Выбор сохранён", UZ: "Tanlov saqlandi"},
    "cancel_btn": {RU: "Отменить", UZ: "Bekor qilish"},
    "need_text": {
        RU: "Пожалуйста, введите текст (не стикер/фото).",
        UZ: "Iltimos, matn yuboring (stiker/foto emas).",
    },
}

OPTIONS = {
    "AGG_INT": {
        RU: ["Да, очень", "Возможно", "Нет"],
        UZ: ["Ha, juda qiziq", "Balki", "Yo‘q"],
    },
    "AGG_VALUES": {
        RU: ["Больше клиентов", "Управление турами", "Онлайн-оплата",
             "Отчёты и аналитика", "Отзывы клиентов"],
        UZ: ["Ko‘proq mijozlar", "Turlarni boshqarish", "Onlayn to‘lov",
             "Hisobot va analitika", "Mijozlar fikrlari"],
    },
}

# ДОБАВЬ К СУЩЕСТВУЮЩЕМУ OPTIONS
OPTIONS.update({
    "PAINS": {
        RU: [
            "Мало заявок", "Дорогие лиды", "Долгие ответы менеджеров",
            "Хаос в переписках", "Нет онлайн-оплаты", "Нет аналитики"
        ],
        UZ: [
            "Arizalar kam", "Lidlar qimmat", "Menejer javobi sekin",
            "Chatlar tartibsiz", "Onlayn to‘lov yo‘q", "Analitika yo‘q"
        ],
    },
    "EXPECTS": {
        RU: [
            "+30% лидов/мес", "Сократить время ответа < 5 мин",
            "Единая лента без хаоса", "Онлайн-оплата работает",
            "Базовые отчёты/дашборд"
        ],
        UZ: [
            "Oyiga +30% lid", "Javob vaqti < 5 daqiqa",
            "Yagona lenta, tartib", "Onlayn to‘lov ishlaydi",
            "Oddiy hisobot/dashbord"
        ],
    },
})


AGG_INT_ALL = tuple(OPTIONS["AGG_INT"][RU] + OPTIONS["AGG_INT"][UZ])
HIDE_KB = ReplyKeyboardRemove(remove_keyboard=True)

# ---------- FSM ----------
class Survey(StatesGroup):
    lang = State()
    agg_interest = State()
    agg_values = State()
    pain = State()
    expect = State()
    contact = State()

# ---------- Бот ----------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
rt = Router()
dp.include_router(rt)

# ---------- Утилиты ----------
def t(key: str, lang: str) -> str: return TEXT[key][lang]
def opts(name: str, lang: str) -> List[str]: return OPTIONS[name][lang]
def now_ts() -> str: return datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")

async def get_lang(state: FSMContext) -> str:
    data = await state.get_data()
    return data.get("lang", RU)

def kb_rows(options: List[str], lang: str, row: int = 2) -> ReplyKeyboardMarkup:
    btns, chunk = [], []
    for i, o in enumerate(options, 1):
        chunk.append(KeyboardButton(text=o))
        if i % row == 0:
            btns.append(chunk); chunk = []
    if chunk: btns.append(chunk)
    btns.append([KeyboardButton(text=t("cancel_btn", lang))])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def inline_multi(options: List[str], lang: str, prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"▫️ {o}", callback_data=f"{prefix}:toggle:{i}")]
            for i, o in enumerate(options)]
    rows.append([InlineKeyboardButton(text=t("done", lang), callback_data=f"{prefix}:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def normalize_contact(txt: str) -> str:
    t_ = (txt or "").strip()
    if t_.startswith("+") and t_[1:].replace(" ", "").isdigit(): return t_
    if t_.startswith("@") and len(t_) > 1: return t_
    digits = "".join(ch for ch in t_ if ch.isdigit() or ch == "+")
    return digits if digits else t_

def share_phone_kb(lang: str) -> ReplyKeyboardMarkup:
    btn = KeyboardButton(text="📱 Поделиться телефоном" if lang==RU else "📱 Telefonni ulashish",
                         request_contact=True)
    return ReplyKeyboardMarkup(keyboard=[[btn], [KeyboardButton(text=t("cancel_btn", lang))]],
                               resize_keyboard=True)

def inline_multi_with_other(options: List[str], lang: str, prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"▫️ {o}", callback_data=f"{prefix}:toggle:{i}")]
            for i, o in enumerate(options)]
    rows.append([InlineKeyboardButton(text=("Другое ✍️" if lang==RU else "Boshqa ✍️"),
                                      callback_data=f"{prefix}:other")])
    rows.append([InlineKeyboardButton(text=t("done", lang), callback_data=f"{prefix}:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- Команды ----------
@rt.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    ikb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXT["lang_ru"][RU], callback_data="lang:ru"),
        InlineKeyboardButton(text=TEXT["lang_uz"][UZ], callback_data="lang:uz"),
    ]])
    await m.answer(TEXT["choose_lang"][RU], reply_markup=HIDE_KB)
    await m.answer(TEXT["choose_lang"][UZ], reply_markup=ikb)
    await state.set_state(Survey.lang)

@rt.callback_query(Survey.lang, F.data.in_(["lang:ru", "lang:uz"]))
async def pick_lang(cb: CallbackQuery, state: FSMContext):
    lang = RU if cb.data.endswith("ru") else UZ
    await state.update_data(lang=lang)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t("welcome", lang), reply_markup=kb_rows(opts("AGG_INT", lang), lang))
    await state.set_state(Survey.agg_interest)
    await cb.answer()

@rt.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(TEXT["cancelled"][await get_lang(state)], reply_markup=HIDE_KB)

# ---------- Шаг 1: Интерес ----------
@rt.message(Survey.agg_interest, F.text.in_(AGG_INT_ALL))
async def step_agg_interest(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(agg_interest=m.text)
    open_btn = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("open_variants", lang), callback_data="vals:open")
    ]])
    await m.answer(t("q_vals_open", lang), reply_markup=open_btn)
    await state.set_state(Survey.agg_values)

@rt.message(Survey.agg_interest)
async def step_agg_interest_retry(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await m.answer(t("q_agg_int", lang), reply_markup=kb_rows(opts("AGG_INT", lang), lang))

# ---------- Шаг 2: Ценности (мультивыбор) ----------
async def toggle_multi(cb: CallbackQuery, state: FSMContext, key: str, options: List[str], prefix: str):
    data = await state.get_data()
    picked: List[int] = data.get(key, [])
    parts = cb.data.split(":")
    _, action, idx = parts[0], parts[1], (int(parts[2]) if len(parts) > 2 else None)

    if action == "toggle" and idx is not None:
        if idx in picked: picked.remove(idx)
        else: picked.append(idx)
        await state.update_data(**{key: picked})
        lang = (await state.get_data()).get("lang", RU)
        rows = [[InlineKeyboardButton(text=f"{'✅' if i in picked else '▫️'} {o}", callback_data=f"{prefix}:toggle:{i}")]
                for i, o in enumerate(options)]
        rows.append([InlineKeyboardButton(text=t("done", lang), callback_data=f"{prefix}:done")])
        await cb.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await cb.answer()
    elif action == "done":
        lang = (await state.get_data()).get("lang", RU)
        await cb.answer(t("saved_choice", lang), show_alert=False)

@rt.callback_query(Survey.agg_values, F.data == "vals:open")
async def vals_open(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_text(t("q_vals_tap", lang),
                               reply_markup=inline_multi(opts("AGG_VALUES", lang), lang, "vals"))
    await cb.answer()

@rt.callback_query(Survey.agg_values, F.data.startswith("vals:toggle:"))
async def vals_toggle(cb: CallbackQuery, state: FSMContext):
    await toggle_multi(cb, state, "vals_idx", opts("AGG_VALUES", await get_lang(state)), "vals")

@rt.callback_query(Survey.agg_values, F.data == "vals:done")
async def vals_done(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_reply_markup(reply_markup=None)
    await state.set_state(Survey.pain)
    await cb.message.answer(
        t("q_pain", lang).split("\n")[0],  # заголовок без "(свободный ответ)"
        reply_markup=inline_multi_with_other(opts("PAINS", lang), lang, "pain")
    )
    await cb.answer()


# ---------- Шаг 3: Боли ----------
@rt.message(Survey.pain, F.text.len() > 0)
async def step_pain(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(pain_points=m.text.strip())
    await state.set_state(Survey.expect)                 # <-- сначала состояние
    await m.answer(t("q_expect", lang), reply_markup=HIDE_KB)  # затем вопрос

@rt.message(Survey.pain)
async def step_pain_need_text(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await m.answer(t("need_text", lang), reply_markup=HIDE_KB)

@rt.callback_query(Survey.pain, F.data.startswith("pain:toggle:"))
async def pain_toggle(cb: CallbackQuery, state: FSMContext):
    await toggle_multi(cb, state, "pain_idx", opts("PAINS", await get_lang(state)), "pain")

@rt.callback_query(Survey.pain, F.data == "pain:other")
async def pain_other(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(waiting_pain_other=True)
    await cb.message.answer("Опишите кратко «Другое»:" if lang==RU else "Qisqa «Boshqa» yozing:")
    await cb.answer()

@rt.message(Survey.pain, F.text.len() > 0)
async def pain_other_text(m: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("waiting_pain_other"):
        await state.update_data(waiting_pain_other=False, pain_other=m.text.strip())
        # остаёмся на pain, ждём «Готово»
        return
    # если вдруг человек просто пишет — сохраним как pain_other

@rt.callback_query(Survey.pain, F.data == "pain:done")
async def pain_done(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_reply_markup(reply_markup=None)
    await state.set_state(Survey.expect)
    await cb.message.answer(
        t("q_expect", lang).split("\n")[0],
        reply_markup=inline_multi_with_other(opts("EXPECTS", lang), lang, "exp")
    )
    await cb.answer()

# ---------- Шаг 4: Ожидания ----------
@rt.message(Survey.expect, F.text.len() > 0)
async def step_expect(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(expectations_free=m.text.strip())
    await state.set_state(Survey.contact)
    await m.answer(t("q_contact", lang), reply_markup=share_phone_kb(lang))

@rt.message(Survey.expect)
async def step_expect_need_text(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await m.answer(t("need_text", lang), reply_markup=HIDE_KB)

@rt.callback_query(Survey.expect, F.data.startswith("exp:toggle:"))
async def exp_toggle(cb: CallbackQuery, state: FSMContext):
    await toggle_multi(cb, state, "exp_idx", opts("EXPECTS", await get_lang(state)), "exp")

@rt.callback_query(Survey.expect, F.data == "exp:other")
async def exp_other(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(waiting_exp_other=True)
    await cb.message.answer("Опишите «Другое» ожидание:" if lang==RU else "«Boshqa» kutilmalarni yozing:")
    await cb.answer()

@rt.message(Survey.expect, F.text.len() > 0)
async def exp_other_text(m: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("waiting_exp_other"):
        await state.update_data(waiting_exp_other=False, exp_other=m.text.strip())
        return

@rt.callback_query(Survey.expect, F.data == "exp:done")
async def exp_done(cb: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await cb.message.edit_reply_markup(reply_markup=None)
    await state.set_state(Survey.contact)
    await cb.message.answer(t("q_contact", lang), reply_markup=share_phone_kb(lang))
    await cb.answer()

# ---------- Шаг 5: Контакт + запись ----------
@rt.message(Survey.contact, F.text.len() > 0)
async def step_contact(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await state.update_data(contact=normalize_contact(m.text or ""))
    await finalize_and_save(m, state, lang)

    data = await state.get_data()
    user = m.from_user

    def map_multi(key_idx: str, options: List[str]) -> str:
        idxs = data.get(key_idx, [])
        return ", ".join(options[i] for i in sorted(set(idxs))) if idxs else ""

    row = [
        now_ts(), str(user.id), (user.username or ""), lang,
        data.get("agg_interest", ""),
        map_multi("vals_idx", opts("AGG_VALUES", lang)),
        data.get("pain_points", ""),
        data.get("expectations", ""),
        data.get("contact", ""),
    ]
    try:
        ws.append_row(row)
    except Exception:
        log.exception("Append to sheet failed")
        warn = "⚠️ Ошибка сохранения в таблицу. Попробуйте позже." if lang == RU \
            else "⚠️ Jadvalga saqlashda xatolik. Keyinroq urinib ko‘ring."
        await m.answer(warn)
        return

    summary = (
        f"<b>[{lang.upper()}] Новая анкета</b>\n"
        f"Интерес: {data.get('agg_interest')}\n"
        f"Ценности: {map_multi('vals_idx', opts('AGG_VALUES', lang))}\n"
        f"Боли: {data.get('pain_points')}\n"
        f"Ожидания: {data.get('expectations')}\n"
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

@rt.message(Survey.contact)
async def step_contact_need_text(m: Message, state: FSMContext):
    lang = await get_lang(state)
    await m.answer(t("need_text", lang), reply_markup=HIDE_KB)

from aiogram.types import ContentType

@rt.message(Survey.contact, F.contact)
async def step_contact_shared(m: Message, state: FSMContext):
    lang = await get_lang(state)
    phone = m.contact.phone_number
    await state.update_data(contact=phone)
    await finalize_and_save(m, state, lang)

async def finalize_and_save(m: Message, state: FSMContext, lang: str):
    data = await state.get_data()
    user = m.from_user

    def join_multi(idx_key: str, options: List[str], other_key: str) -> str:
        idxs = data.get(idx_key, [])
        base = [options[i] for i in sorted(set(idxs))] if idxs else []
        if data.get(other_key):
            base.append(f"Другое: {data[other_key]}")
        return ", ".join(base)

    # Если контакт не задан — авто-подхватим username
    contact = data.get("contact") or (m.text if getattr(m, "text", None) else None) \
              or (("@"+user.username) if user.username else "")
    await state.update_data(contact=normalize_contact(contact))

    row = [
        now_ts(), str(user.id), (user.username or ""), lang,
        data.get("agg_interest", ""),
        join_multi("vals_idx", opts("AGG_VALUES", lang), other_key="vals_other"),  # если добавишь «Другое» и сюда
        join_multi("pain_idx", opts("PAINS", lang), other_key="pain_other"),
        join_multi("exp_idx",  opts("EXPECTS", lang), other_key="exp_other"),
        data.get("contact", ""),
    ]
    try:
        ws.append_row(row)
    except Exception:
        log.exception("Append to sheet failed")
        warn = "⚠️ Ошибка сохранения в таблицу. Попробуйте позже." if lang == RU \
            else "⚠️ Jadvalga saqlashda xatolik. Keyinroq urinib ko‘ring."
        await m.answer(warn)
        return

    summary = (
        f"<b>[{lang.upper()}] Новая анкета</b>\n"
        f"Интерес: {data.get('agg_interest')}\n"
        f"Ценности: {join_multi('vals_idx', opts('AGG_VALUES', lang), 'vals_other')}\n"
        f"Боли: {join_multi('pain_idx', opts('PAINS', lang), 'pain_other')}\n"
        f"Ожидания: {join_multi('exp_idx', opts('EXPECTS', lang), 'exp_other')}\n"
        f"Контакт: {data.get('contact')}\n"
        f"— @{user.username or '—'} | {user.id}"
    )
    for admin_id in ADMINS:
        try: await bot.send_message(admin_id, summary)
        except Exception: pass

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

@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    log.info("Webhook set: %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass

@app.head("/", include_in_schema=False)
async def root_head():
    return PlainTextResponse("", status_code=200)





