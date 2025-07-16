#!/usr/bin/env python3
"""
MateFinder — Telegram anonymous dating bot
Features:
    /start    – guided profile onboarding
    /find     – enter matchmaking queue  (alias /next)
/next      – leave current chat & match again
    /stop     – quit current chat
    /profile  – view / edit profile
    /settings – set gender preference
    /report   – report partner while chatting
    /help     – command list
Fully async, no external services: uses SQLite via aiosqlite.
"""

import asyncio
import logging
import os
from datetime import datetime
from enum import StrEnum, auto
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

###############################################################################
#                         0.  BASIC CONFIG & LOGGING                          #
###############################################################################
load_dotenv()
BOT_TOKEN = os.getenv("7620053279:AAFfGVyoXsL5nOL0U5DcmDG4QDsW3XYb6t4")           # put your token in .env
ADMIN_IDS = {int(x) for x in os.getenv("ADMINS", "").split(",") if x}  # optional

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("MateFinder")

###############################################################################
#                                 1.  DB LAYER                                #
###############################################################################
DB_FILE = "matefinder.db"

CREATE_SQL = [
    """CREATE TABLE IF NOT EXISTS users(
            user_id     INTEGER PRIMARY KEY,
            name        TEXT,
            gender      TEXT,
            age         INTEGER,
            bio         TEXT,
            photo_id    TEXT,
            pref        TEXT DEFAULT 'any'
        )""",
    """CREATE TABLE IF NOT EXISTS queue(
            user_id     INTEGER UNIQUE,
            queued_at   TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )""",
    """CREATE TABLE IF NOT EXISTS chats(
            user_id     INTEGER UNIQUE,
            partner_id  INTEGER,
            since       TIMESTAMP,
            FOREIGN KEY(user_id)    REFERENCES users(user_id),
            FOREIGN KEY(partner_id) REFERENCES users(user_id)
        )""",
    """CREATE TABLE IF NOT EXISTS reports(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter    INTEGER,
            reported    INTEGER,
            reason      TEXT,
            ts          TIMESTAMP
        )""",
]

class Gender(StrEnum):
    male = "male"
    female = "female"
    other = "other"
    any = "any"

class DB:
    """Lightweight async DB helper"""
    def __init__(self, db_file: str):
        self.file = db_file
        self._pool: Optional[aiosqlite.Connection] = None

    async def init(self):
        self._pool = await aiosqlite.connect(self.file)
        await self._pool.execute("PRAGMA foreign_keys=ON")
        for stmt in CREATE_SQL:
            await self._pool.execute(stmt)
        await self._pool.commit()
        log.info("DB ready")

    async def add_user_if_not_exists(self, user_id: int):
        async with self._pool.execute(
            "SELECT 1 FROM users WHERE user_id=?", (user_id,)
        ) as cur:
            if await cur.fetchone() is None:
                await self._pool.execute("INSERT INTO users(user_id) VALUES(?)", (user_id,))
                await self._pool.commit()

    async def update_profile(self, user_id: int, **fields):
        keys, vals = zip(*fields.items())
        sets = ",".join(f"{k}=?" for k in keys)
        await self._pool.execute(
            f"UPDATE users SET {sets} WHERE user_id=?", (*vals, user_id)
        )
        await self._pool.commit()

    async def get_profile(self, user_id: int):
        async with self._pool.execute(
            "SELECT name,gender,age,bio,photo_id,pref FROM users WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return dict(zip(("name","gender","age","bio","photo","pref"), row))

    # ---------- matchmaking ----------
    async def enqueue(self, user_id: int):
        await self._pool.execute(
            "INSERT OR IGNORE INTO queue(user_id,queued_at) VALUES(?,?)",
            (user_id, datetime.utcnow()),
        )
        await self._pool.commit()

    async def dequeue(self, user_id: int):
        await self._pool.execute("DELETE FROM queue WHERE user_id=?", (user_id,))
        await self._pool.commit()

    async def pop_match(self, user_id: int):
        """Find a partner respecting gender preference."""
        async with self._pool.execute(
            "SELECT pref FROM users WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        my_pref = row[0] or Gender.any
        # iterate queue for first compatible partner
        async with self._pool.execute("SELECT user_id FROM queue WHERE user_id!=?", (user_id,)) as cur:
            async for (candidate,) in cur:
                # check candidate's pref reciprocity
                async with self._pool.execute(
                    "SELECT gender,pref FROM users WHERE user_id=?", (candidate,)
                ) as c2:
                    gender2, pref2 = await c2.fetchone()
                async with self._pool.execute(
                    "SELECT gender FROM users WHERE user_id=?", (user_id,)
                ) as c3:
                    gender1 = (await c3.fetchone())[0]
                if (my_pref in (Gender.any, gender2)) and (pref2 in (Gender.any, gender1)):
                    # compatible
                    await self._pool.execute("DELETE FROM queue WHERE user_id IN (?,?)", (user_id,candidate))
                    await self._pool.execute(
                        "INSERT OR REPLACE INTO chats(user_id,partner_id,since) VALUES(?,?,?)",
                        (user_id, candidate, datetime.utcnow()),
                    )
                    await self._pool.execute(
                        "INSERT OR REPLACE INTO chats(user_id,partner_id,since) VALUES(?,?,?)",
                        (candidate, user_id, datetime.utcnow()),
                    )
                    await self._pool.commit()
                    return candidate
        return None

    # ---------- chat helpers ----------
    async def get_partner(self, user_id: int):
        async with self._pool.execute(
            "SELECT partner_id FROM chats WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    async def end_chat(self, user_id: int, notify_partner=True):
        partner = await self.get_partner(user_id)
        await self._pool.execute("DELETE FROM chats WHERE user_id IN (?,?)", (user_id, partner or -1))
        await self._pool.commit()
        return partner

    # ---------- reports ----------
    async def add_report(self, reporter:int, reported:int, reason:str):
        await self._pool.execute(
            "INSERT INTO reports(reporter,reported,reason,ts) VALUES(?,?,?,?)",
            (reporter, reported, reason, datetime.utcnow())
        )
        await self._pool.commit()

db = DB(DB_FILE)

###############################################################################
#                             2.  FSM STATES                                  #
###############################################################################
class Onboard(StatesGroup):
    name = State()
    gender = State()
    age = State()
    bio = State()
    photo = State()

class ReportState(StatesGroup):
    reason = State()

###############################################################################
#                         3.  HELPER FUNCTIONS                                #
###############################################################################
def gender_kb(selected: Optional[str] = None):
    kb = InlineKeyboardBuilder()
    for g in (Gender.male, Gender.female, Gender.other):
        label = ("✅ " if g == selected else "") + g.capitalize()
        kb.button(text=label, callback_data=f"gender:{g}")
    kb.adjust(3)
    return kb.as_markup()

async def send_system(chat_id: int, text: str, bot: Bot):
    await bot.send_message(chat_id, f"⚠️ *System*: {text}", parse_mode="Markdown")

async def is_in_chat(user_id:int)->bool:
    return await db.get_partner(user_id) is not None

###############################################################################
#                          4.  MAIN ROUTER & HANDLERS                          #
###############################################################################
router = Router(name="matefinder")

# ----- /start -----
@router.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    await db.add_user_if_not_exists(msg.from_user.id)
    profile = await db.get_profile(msg.from_user.id)
    if profile["name"]:
        await msg.answer("👋 Welcome back to *MateFinder*!\nUse /find to get matched.",
                         parse_mode="Markdown")
    else:
        await msg.answer("👋 Hi! Let's create your profile.\nWhat's your *name*?",
                         parse_mode="Markdown")
        await state.set_state(Onboard.name)

# ----- Onboarding flow -----
@router.message(Onboard.name)
async def onboard_name(msg: types.Message, state: FSMContext):
    await state.update_data(name=msg.text.strip()[:32])
    await msg.answer("Select your *gender*:", reply_markup=gender_kb(), parse_mode="Markdown")
    await state.set_state(Onboard.gender)

@router.callback_query(StateFilter(Onboard.gender), F.data.startswith("gender:"))
async def onboard_gender(cb: types.CallbackQuery, state: FSMContext):
    gender = cb.data.split(":")[1]
    await state.update_data(gender=gender)
    await cb.message.edit_text("Great! How *old* are you?")
    await state.set_state(Onboard.age)
    await cb.answer()

@router.message(Onboard.age)
async def onboard_age(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit() or not 13 <= int(msg.text) <= 99:
        return await msg.reply("Please send a valid age (13-99).")
    await state.update_data(age=int(msg.text))
    await msg.answer("Write a short *bio* (max 120 chars):", parse_mode="Markdown")
    await state.set_state(Onboard.bio)

@router.message(Onboard.bio)
async def onboard_bio(msg: types.Message, state: FSMContext):
    await state.update_data(bio=msg.text.strip()[:120])
    await msg.answer("Send me a *profile photo* (or /skip):", parse_mode="Markdown")
    await state.set_state(Onboard.photo)

@router.message(Onboard.photo, F.photo)
async def onboard_photo(msg: types.Message, state: FSMContext):
    await state.update_data(photo=msg.photo[-1].file_id)
    data = await state.get_data()
    await db.update_profile(msg.from_user.id, **data)
    await state.clear()
    await msg.answer("✅ Profile saved! Use /find to get matched.")

@router.message(Onboard.photo, Command("skip"))
async def onboard_skip_photo(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    await db.update_profile(msg.from_user.id, **data)
    await state.clear()
    await msg.answer("✅ Profile saved without photo! Use /find to get matched.")

# ----- /profile -----
@router.message(Command("profile"))
async def cmd_profile(msg: types.Message):
    profile = await db.get_profile(msg.from_user.id)
    if not profile["name"]:
        return await msg.reply("You don't have a profile yet. Use /start.")
    text = (
        f"*Name*: {profile['name']}\n"
        f"*Gender*: {profile['gender']}\n"
        f"*Age*: {profile['age']}\n"
        f"*Bio*: {profile['bio']}\n"
        f"*Prefers*: {profile['pref']}"
    )
    await msg.answer_photo(profile["photo"] or "", caption=text, parse_mode="Markdown", 
                           reply_markup=settings_kb())

# inline keyboard to change settings
def settings_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Gender Preference", callback_data="chg_pref")
    kb.adjust(1)
    return kb.as_markup()

@router.callback_query(F.data == "chg_pref")
async def cb_pref(cb: types.CallbackQuery):
    profile = await db.get_profile(cb.from_user.id)
    await cb.message.edit_reply_markup(reply_markup=gender_kb(profile["pref"]))
    await cb.answer("Pick your preferred partner gender!")

@router.callback_query(F.data.startswith("gender:"))
async def cb_set_pref(cb: types.CallbackQuery):
    pref = cb.data.split(":")[1]
    await db.update_profile(cb.from_user.id, pref=pref)
    await cb.answer("Preference updated!")
    await cb.message.edit_reply_markup(reply_markup=None)

# ----- /find and /next -----
@router.message(Command(commands=("find", "next")))
async def cmd_find(msg: types.Message, bot: Bot):
    if await is_in_chat(msg.from_user.id):
        # leave current chat first
        partner = await db.end_chat(msg.from_user.id)
        if partner:
            await send_system(partner, "Your partner left the chat.", bot)
    await db.enqueue(msg.from_user.id)
    partner = await db.pop_match(msg.from_user.id)
    if partner:
        await bot.send_message(partner, "🎉 You are now connected! Say hi!")
        await bot.send_message(msg.from_user.id, "🎉 You are now connected! Say hi!")
    else:
        await msg.answer("⌛ Waiting for a partner… Use /stop to cancel.")

# ----- /stop -----
@router.message(Command("stop"))
async def cmd_stop(msg: types.Message, bot: Bot):
    if await is_in_chat(msg.from_user.id):
        partner = await db.end_chat(msg.from_user.id)
        if partner:
            await send_system(partner, "Partner ended the chat.", bot)
        await msg.answer("🔕 Chat ended. Use /find to match again.")
    else:
        await db.dequeue(msg.from_user.id)
        await msg.answer("🚫 Queue cleared.")

# ----- Relay chat messages -----
@router.message()
async def relay(msg: types.Message, bot: Bot):
    partner = await db.get_partner(msg.from_user.id)
    if not partner:
        return  # ignore stray messages
    # copy almost any content
    if msg.content_type == "text":
        await bot.send_message(partner, msg.text)
    elif msg.content_type == "photo":
        await bot.send_photo(partner, msg.photo[-1].file_id, caption=msg.caption)
    elif msg.content_type == "sticker":
        await bot.send_sticker(partner, msg.sticker.file_id)
    else:
        await msg.reply("Unsupported message type.")

# ----- /report flow -----
@router.message(Command("report"))
async def cmd_report(msg: types.Message, state:FSMContext):
    if not await is_in_chat(msg.from_user.id):
        return await msg.reply("You can only report while in a chat.")
    await msg.reply("Please describe the issue briefly:")
    await state.set_state(ReportState.reason)

@router.message(ReportState.reason)
async def report_reason(msg: types.Message, state:FSMContext, bot: Bot):
    partner = await db.get_partner(msg.from_user.id)
    await db.add_report(msg.from_user.id, partner, msg.text[:250])
    await send_system(partner, "You were reported and disconnected.", bot)
    await db.end_chat(msg.from_user.id)
    await state.clear()
    await msg.answer("✅ Report submitted. You have left the chat.")

# ----- /help -----
HELP = """
*MateFinder Commands*
/start   – Create profile / restart bot
/find    – Enter matchmaking queue
/next    – Skip current partner & match new
/stop    – Leave chat or queue
/profile – View your profile
/settings– Set gender preference
/report  – Report current partner
/help    – Show this help
"""

@router.message(Command("help"))
async def cmd_help(msg: types.Message):
    await msg.answer(HELP, parse_mode="Markdown")

###############################################################################
#                            5.  RUN EVERYTHING                               #
###############################################################################
async def main():
    await db.init()
    bot = Bot(BOT_TOKEN, parse_mode="HTML")
    dp = Dispatcher()
    dp.include_router(router)

    log.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
