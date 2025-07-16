#!/usr/bin/env python3
"""
MateFinder Telegram Bot – Flirtu-style anonymous dating

Features
--------
/start     – profile onboarding
/find      – join matchmaking queue
/next      – skip current partner & rematch
/stop      – leave current chat or queue
/profile   – view (and tweak) your profile
/settings  – set gender preference
/report    – report current partner
/help      – list commands

Tech stack
----------
• Python 3.10+  (works on 3.10 thanks to StrEnum fallback)
• aiogram 3.5.0 (async Telegram framework)
• SQLite via aiosqlite (no external DB)
"""

###############################################################################
# 0. BASIC CONFIG (👉 replace BOT_TOKEN)                                       #
###############################################################################
BOT_TOKEN = "7620053279:AAFfGVyoXsL5nOL0U5DcmDG4QDsW3XYb6t4"          # ← put your @BotFather token here
ADMIN_IDS = set()                          # optional ints like {123, 456}

###############################################################################
# 1. IMPORTS & STRENUM FALLBACK                                               #
###############################################################################
import asyncio
import logging
from datetime import datetime
from enum import Enum, auto          # auto used for States, Enum for fallback
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ---- Python < 3.11 compatibility: provide StrEnum if missing ---------------
try:
    from enum import StrEnum                           # Python 3.11+
except ImportError:
    class StrEnum(str, Enum):                          # fallback
        pass
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("MateFinder")

###############################################################################
# 2. DATABASE LAYER                                                           #
###############################################################################
DB_FILE = "matefinder.db"

CREATE_SQL = [
    """CREATE TABLE IF NOT EXISTS users(
        user_id   INTEGER PRIMARY KEY,
        name      TEXT,
        gender    TEXT,
        age       INTEGER,
        bio       TEXT,
        photo_id  TEXT,
        pref      TEXT DEFAULT 'any'
    )""",
    """CREATE TABLE IF NOT EXISTS queue(
        user_id   INTEGER UNIQUE,
        queued_at TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS chats(
        user_id    INTEGER UNIQUE,
        partner_id INTEGER,
        since      TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS reports(
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter  INTEGER,
        reported  INTEGER,
        reason    TEXT,
        ts        TIMESTAMP
    )""",
]

class Gender(StrEnum):
    male   = "male"
    female = "female"
    other  = "other"
    any    = "any"

class DB:
    """Tiny async-SQLite helper"""
    def __init__(self, path: str):
        self.path = path
        self.pool: Optional[aiosqlite.Connection] = None

    # ---------- bootstrap ----------
    async def init(self):
        self.pool = await aiosqlite.connect(self.path)
        await self.pool.execute("PRAGMA foreign_keys = ON")
        for stmt in CREATE_SQL:
            await self.pool.execute(stmt)
        await self.pool.commit()
        log.info("SQLite ready → %s", self.path)

    # ---------- users ----------
    async def add_user_if_absent(self, uid: int):
        async with self.pool.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)) as c:
            if await c.fetchone() is None:
                await self.pool.execute("INSERT INTO users(user_id) VALUES(?)", (uid,))
                await self.pool.commit()

    async def set_profile(self, uid: int, **fields):
        if not fields: return
        cols, vals = zip(*fields.items())
        sets = ",".join(f"{k}=?" for k in cols)
        await self.pool.execute(f"UPDATE users SET {sets} WHERE user_id=?", (*vals, uid))
        await self.pool.commit()

    async def get_profile(self, uid: int) -> Optional[dict]:
        async with self.pool.execute(
            "SELECT name,gender,age,bio,photo_id,pref FROM users WHERE user_id=?", (uid,)
        ) as c:
            row = await c.fetchone()
            if not row:
                return None
            return dict(zip(("name","gender","age","bio","photo","pref"), row))

    # ---------- queue ----------
    async def enqueue(self, uid: int):
        await self.pool.execute(
            "INSERT OR IGNORE INTO queue(user_id,queued_at) VALUES(?,?)",
            (uid, datetime.utcnow())
        )
        await self.pool.commit()

    async def dequeue(self, uid: int):
        await self.pool.execute("DELETE FROM queue WHERE user_id=?", (uid,))
        await self.pool.commit()

    # ---------- matchmaking ----------
    async def pop_match(self, uid: int) -> Optional[int]:
        """Try to find compatible partner, else None"""
        async with self.pool.execute("SELECT gender,pref FROM users WHERE user_id=?", (uid,)) as c:
            me_gender, me_pref = await c.fetchone()
        me_pref = me_pref or Gender.any

        async with self.pool.execute("SELECT user_id FROM queue WHERE user_id!=?", (uid,)) as c:
            async for (cand,) in c:
                # get candidate data
                async with self.pool.execute(
                    "SELECT gender,pref FROM users WHERE user_id=?", (cand,)
                ) as c2:
                    cand_gender, cand_pref = await c2.fetchone()
                cand_pref = cand_pref or Gender.any
                # compatibility check
                if (me_pref in (Gender.any, cand_gender)) and (cand_pref in (Gender.any, me_gender)):
                    # bingo
                    await self.pool.execute(
                        "DELETE FROM queue WHERE user_id IN (?,?)", (uid, cand)
                    )
                    t = datetime.utcnow()
                    await self.pool.executemany(
                        "INSERT OR REPLACE INTO chats(user_id,partner_id,since) VALUES(?,?,?)",
                        ((uid,  cand, t), (cand, uid, t))
                    )
                    await self.pool.commit()
                    return cand
        return None

    # ---------- chat ----------
    async def partner_of(self, uid: int) -> Optional[int]:
        async with self.pool.execute(
            "SELECT partner_id FROM chats WHERE user_id=?", (uid,)
        ) as c:
            row = await c.fetchone()
            return row[0] if row else None

    async def end_chat(self, uid: int):
        partner = await self.partner_of(uid)
        await self.pool.execute("DELETE FROM chats WHERE user_id IN (?,?)", (uid, partner or -1))
        await self.pool.commit()
        return partner

    # ---------- reports ----------
    async def add_report(self, reporter: int, reported: int, reason: str):
        await self.pool.execute(
            "INSERT INTO reports(reporter,reported,reason,ts) VALUES(?,?,?,?)",
            (reporter, reported, reason[:250], datetime.utcnow())
        )
        await self.pool.commit()

db = DB(DB_FILE)

###############################################################################
# 3. FSM STATES                                                               #
###############################################################################
class Onboard(StatesGroup):
    name   = State()
    gender = State()
    age    = State()
    bio    = State()
    photo  = State()

class ReportState(StatesGroup):
    reason = State()

###############################################################################
# 4. UTILITY FUNCTIONS & KEYBOARDS                                            #
###############################################################################
def gender_kb(selected: Optional[str] = None):
    kb = InlineKeyboardBuilder()
    for g in (Gender.male, Gender.female, Gender.other):
        prefix = "✅ " if selected == g else ""
        kb.button(text=prefix + g.capitalize(), callback_data=f"gsel:{g}")
    kb.adjust(3)
    return kb.as_markup()

def settings_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Gender Preference", callback_data="pref")
    kb.adjust(1)
    return kb.as_markup()

async def send_sys(bot: Bot, chat_id: int, text: str):
    await bot.send_message(chat_id, f"⚠️ <b>System</b>: {text}", parse_mode="HTML")

async def in_chat(uid: int) -> bool:
    return await db.partner_of(uid) is not None

###############################################################################
# 5. ROUTER & HANDLERS                                                        #
###############################################################################
router = Router(name="matefinder")

# ---------- /start ----------
@router.message(CommandStart())
async def start(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    await db.add_user_if_absent(uid)
    prof = await db.get_profile(uid)
    if prof["name"]:
        await m.answer("👋 Welcome back to <b>MateFinder</b>!\nUse /find to meet someone.",
                       parse_mode="HTML")
    else:
        await m.answer("👋 Hi! Let's set up your profile.\n\nWhat's your <b>name</b>?",
                       parse_mode="HTML")
        await state.set_state(Onboard.name)

# ---------- Onboarding flow ----------
@router.message(Onboard.name)
async def ob_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text.strip()[:32])
    await m.answer("Select your <b>gender</b>:", reply_markup=gender_kb(), parse_mode="HTML")
    await state.set_state(Onboard.gender)

@router.callback_query(StateFilter(Onboard.gender), F.data.startswith("gsel:"))
async def ob_gender(cb: types.CallbackQuery, state: FSMContext):
    gender = cb.data.split(":")[1]
    await state.update_data(gender=gender)
    await cb.message.edit_text("Great! How <b>old</b> are you?", parse_mode="HTML")
    await state.set_state(Onboard.age)
    await cb.answer()

@router.message(Onboard.age)
async def ob_age(m: types.Message, state: FSMContext):
    if not (m.text.isdigit() and 13 <= int(m.text) <= 99):
        return await m.reply("Please enter a valid age (13-99).")
    await state.update_data(age=int(m.text))
    await m.answer("Write a short <b>bio</b> (max 120 chars):", parse_mode="HTML")
    await state.set_state(Onboard.bio)

@router.message(Onboard.bio)
async def ob_bio(m: types.Message, state: FSMContext):
    await state.update_data(bio=m.text.strip()[:120])
    await m.answer("Finally, send a profile <b>photo</b> (or /skip):", parse_mode="HTML")
    await state.set_state(Onboard.photo)

@router.message(Onboard.photo, F.photo)
async def ob_photo(m: types.Message, state: FSMContext):
    await state.update_data(photo_id=m.photo[-1].file_id)
    data = await state.get_data()
    await db.set_profile(m.from_user.id, **data)
    await state.clear()
    await m.answer("✅ Profile saved! Use /find to get matched.")

@router.message(Onboard.photo, Command("skip"))
async def ob_skip(m: types.Message, state: FSMContext):
    data = await state.get_data()
    data.pop("photo_id", None)
    await db.set_profile(m.from_user.id, **data)
    await state.clear()
    await m.answer("✅ Profile saved (without photo). Use /find to get matched.")

# ---------- /profile ----------
@router.message(Command("profile"))
async def profile(m: types.Message):
    prof = await db.get_profile(m.from_user.id)
    if not prof["name"]:
        return await m.reply("You don't have a profile yet. Send /start.")
    txt = (
        f"<b>Name</b>: {prof['name']}\n"
        f"<b>Gender</b>: {prof['gender']}\n"
        f"<b>Age</b>: {prof['age']}\n"
        f"<b>Bio</b>: {prof['bio'] or '—'}\n"
        f"<b>Prefers</b>: {prof['pref']}"
    )
    if prof["photo"]:
        await m.answer_photo(prof["photo"], caption=txt, parse_mode="HTML",
                             reply_markup=settings_kb())
    else:
        await m.answer(txt, parse_mode="HTML", reply_markup=settings_kb())

# ---------- Settings: change preference ----------
@router.callback_query(F.data == "pref")
async def pref_menu(cb: types.CallbackQuery):
    prof = await db.get_profile(cb.from_user.id)
    await cb.message.edit_reply_markup(gender_kb(prof["pref"]))
    await cb.answer("Choose preferred partner gender")

@router.callback_query(F.data.startswith("gsel:"))
async def pref_set(cb: types.CallbackQuery):
    pref = cb.data.split(":")[1]
    await db.set_profile(cb.from_user.id, pref=pref)
    await cb.answer("Preference updated!")
    await cb.message.edit_reply_markup(reply_markup=None)

# ---------- /find & /next ----------
@router.message(Command(("find", "next")))
async def find(m: types.Message, bot: Bot):
    uid = m.from_user.id
    # if already chatting → leave first
    if await in_chat(uid):
        partner = await db.end_chat(uid)
        if partner:
            await send_sys(bot, partner, "Your partner left the chat.")
    # join queue
    await db.enqueue(uid)
    partner = await db.pop_match(uid)
    if partner:
        await bot.send_message(partner, "🎉 You are now connected! Say hi!")
        await bot.send_message(uid,      "🎉 You are now connected! Say hi!")
    else:
        await m.answer("⌛ Waiting for a partner... Use /stop to cancel.")

# ---------- /stop ----------
@router.message(Command("stop"))
async def stop(m: types.Message, bot: Bot):
    uid = m.from_user.id
    if await in_chat(uid):
        partner = await db.end_chat(uid)
        if partner:
            await send_sys(bot, partner, "Partner ended the chat.")
        await m.answer("🔕 Chat ended. Use /find to match again.")
    else:
        await db.dequeue(uid)
        await m.answer("🚫 You left the queue.")

# ---------- Relay messages ----------
@router.message()
async def relay(m: types.Message, bot: Bot):
    partner = await db.partner_of(m.from_user.id)
    if not partner:
        return  # ignore if not in chat
    t = m.content_type
    if t == "text":
        await bot.send_message(partner, m.text)
    elif t == "photo":
        await bot.send_photo(partner, m.photo[-1].file_id, caption=m.caption or "")
    elif t == "sticker":
        await bot.send_sticker(partner, m.sticker.file_id)
    else:
        await m.reply("Unsupported message type.")

# ---------- /report ----------
@router.message(Command("report"))
async def report(m: types.Message, state: FSMContext):
    if not await in_chat(m.from_user.id):
        return await m.reply("You can only report while chatting.")
    await m.reply("Please briefly describe what happened:")
    await state.set_state(ReportState.reason)

@router.message(ReportState.reason)
async def report_reason(m: types.Message, state: FSMContext, bot: Bot):
    partner = await db.partner_of(m.from_user.id)
    await db.add_report(m.from_user.id, partner, m.text)
    await send_sys(bot, partner, "You were reported and disconnected.")
    await db.end_chat(m.from_user.id)
    await state.clear()
    await m.answer("✅ Report submitted. You have left the chat.")

# ---------- /help ----------
HELP_TEXT = """
<b>MateFinder Commands</b>
/start  – create / reset profile
/find   – join matchmaking queue
/next   – skip current partner
/stop   – leave chat / queue
/profile – view your profile
/settings – set gender preference
/report – report current partner
/help   – show this help
"""
@router.message(Command("help"))
async def help_cmd(m: types.Message):
    await m.answer(HELP_TEXT, parse_mode="HTML")

###############################################################################
# 6. MAIN ENTRYPOINT                                                          #
###############################################################################
async def main():
    await db.init()
    bot = Bot(BOT_TOKEN, parse_mode="HTML")
    dp  = Dispatcher()
    dp.include_router(router)
    log.info("MateFinder bot ready 🎉")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
