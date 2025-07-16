#!/usr/bin/env python3
import asyncio, logging
from datetime import datetime
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

# === CONFIG ===
BOT_TOKEN = "7620053279:AAFfGVyoXsL5nOL0U5DcmDG4QDsW3XYb6t4"  # ← replace with your BotFather token

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("FlirtuClone")

DB_FILE = "flirtu_clone.db"
CREATE_SQL = [
    """CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        age INTEGER,
        gender TEXT,
        pref TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS queue (
        user_id INTEGER UNIQUE,
        queued_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS chats (
        user_id INTEGER UNIQUE,
        partner_id INTEGER,
        since TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter INTEGER,
        reported INTEGER,
        reason TEXT,
        ts TIMESTAMP
    )"""
]

# === FSM States ===
class Onboard(StatesGroup):
    name = State()
    age = State()
    gender = State()
    pref = State()

class Chatting(StatesGroup):
    in_chat = State()

# === DB helper ===
class DB:
    def __init__(self, file): self.f=file; self.db=None
    async def init(self):
        self.db = await aiosqlite.connect(self.f)
        await self.db.execute("PRAGMA foreign_keys=ON")
        for s in CREATE_SQL: await self.db.execute(s)
        await self.db.commit()
    async def add_user(self, uid):
        await self.db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)",(uid,))
        await self.db.commit()
    async def update(self, uid, **f):
        cols = ",".join(f.keys())
        vals = list(f.values()) + [uid]
        await self.db.execute(f"UPDATE users SET {','.join([k+'=?' for k in f])} WHERE user_id=?", vals)
        await self.db.commit()
    async def get_profile(self, uid):
        cur= await self.db.execute("SELECT name,age,gender,pref FROM users WHERE user_id=?", (uid,))
        r = await cur.fetchone()
        return dict(zip(["name","age","gender","pref"], r)) if r else None
    async def enqueue(self, uid):
        await self.db.execute("INSERT OR IGNORE INTO queue(user_id,queued_at) VALUES(?,?)",(uid,datetime.utcnow()))
        await self.db.commit()
    async def dequeue(self, uid):
        await self.db.execute("DELETE FROM queue WHERE user_id=?", (uid,))
        await self.db.commit()
    async def pop_match(self, uid):
        prof = await self.get_profile(uid)
        async for (cand,) in (await self.db.execute("SELECT user_id FROM queue WHERE user_id!=?",(uid,))):
            cprof = await self.get_profile(cand)
            if prof["pref"] in ("any", cprof["gender"]) and cprof["pref"] in ("any", prof["gender"]):
                await self.db.execute("DELETE FROM queue WHERE user_id IN (?,?)", (uid,cand))
                t=datetime.utcnow()
                await self.db.execute("INSERT OR REPLACE INTO chats(user_id,partner_id,since) VALUES(?,?,?)",(uid,cand,t))
                await self.db.execute("INSERT OR REPLACE INTO chats(user_id,partner_id,since) VALUES(?,?,?)",(cand,uid,t))
                await self.db.commit()
                return cand
        return None
    async def get_partner(self, uid):
        cur=await self.db.execute("SELECT partner_id FROM chats WHERE user_id=?", (uid,))
        r=await cur.fetchone()
        return r[0] if r else None
    async def end_chat(self, uid):
        p=await self.get_partner(uid)
        await self.db.execute("DELETE FROM chats WHERE user_id IN (?,?)",(uid, p or -1))
        await self.db.commit()
        return p
    async def add_report(self, rep, rpt, reason):
        await self.db.execute("INSERT INTO reports(reporter,reported,reason,ts) VALUES(?,?,?,?)",(rep,rpt,reason,datetime.utcnow()))
        await self.db.commit()

db=DB(DB_FILE)

# === Keyboards ===
def gender_kb():
    kb=InlineKeyboardBuilder()
    for g in ("male","female","other"):
        kb.button(text=g.capitalize(), callback_data=f"g:{g}")
    kb.adjust(3)
    return kb.as_markup()

# === Bot logic ===
bot=Bot(BOT_TOKEN, parse_mode="HTML")
dp=Dispatcher()

@dp.startup()
async def start_db(): await db.init()

@dp.message(CommandStart())
async def start(m: types.Message, state: FSMContext):
    await db.add_user(m.from_user.id)
    prof = await db.get_profile(m.from_user.id)
    if prof and prof["name"]:
        await m.answer("Welcome back! Use /find to meet someone.")
    else:
        await m.answer("👋 Hi! Let's create your profile.\nYour name?")
        await state.set_state(Onboard.name)

@dp.message(Onboard.name)
async def name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text[:32])
    await m.answer("Your age?")
    await state.set_state(Onboard.age)

@dp.message(Onboard.age)
async def age(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.reply("Send number")
    await state.update_data(age=int(m.text))
    await m.answer("Choose gender:", reply_markup=gender_kb())
    await state.set_state(Onboard.gender)

@dp.callback_query(F.data.startswith("g:"))
async def got_gender(cb: types.CallbackQuery, state: FSMContext):
    g = cb.data.split(":")[1]
    await state.update_data(gender=g)
    # Removed: Preferred partner gender (or other/any)?
    await state.set_state(Onboard.pref)
    await cb.answer()

@dp.callback_query(F.data.startswith("g:"))
async def got_pref(cb: types.CallbackQuery, state: FSMContext):
    p=cb.data.split(":")[1]
    data=await state.get_data()
    await db.update(cb.from_user.id, name=data["name"], age=data["age"], gender=data["gender"], pref=p)
    await state.clear()
    await cb.message.edit_text("✅ Profile saved! Use /find to meet someone.")
    await cb.answer()

@dp.message(Command("profile"))
async def profile(m: types.Message):
    p=await db.get_profile(m.from_user.id)
    if not p or not p["name"]: return await m.reply("No profile. Send /start")
    await m.answer(f"Name: {p['name']}\nAge: {p['age']}\nGender: {p['gender']}\nPrefers: {p['pref']}")

@dp.message(Command("find"))
async def find(m: types.Message):
    await db.enqueue(m.from_user.id)
    partner=await db.pop_match(m.from_user.id)
    if partner:
        await bot.send_message(partner, "🎉 You are now connected! Say hi.")
        await bot.send_message(m.from_user.id, "🎉 You are now connected! Say hi.")
        await dp.current_state(m.from_user.id).set_state(Chatting.in_chat)
        await dp.current_state(partner).set_state(Chatting.in_chat)
    else:
        await m.reply("Waiting for a partner... /stop to cancel.")

@dp.message(Chatting.in_chat)
async def echo(m: types.Message):
    p=await db.get_partner(m.from_user.id)
    if not p: return
    await bot.send_message(p, m.text)

@dp.message(Command("stop"))
async def stop(m: types.Message):
    p=await db.end_chat(m.from_user.id)
    if p:
        await bot.send_message(p, "⚠️ Partner ended chat.")
    await m.reply("Chat stopped. /find to start again.")

@dp.message(Command("skip"))
async def skip(m: types.Message):
    await dp.process_update(types.Update(**{"message":{"chat":{"id":m.chat.id},"text":"/stop|/find"}}))
    await m.delete()  # simulate skip

@dp.message(Command("report"))
async def report(m: types.Message):
    p=await db.get_partner(m.from_user.id)
    if not p: return await m.reply("Not chatting.")
    await db.add_report(m.from_user.id, p, "no reason")
    await m.reply("Reported and chat ended.")
    await stop(m)

@dp.message(Command("help"))
async def help(m: types.Message):
    await m.reply("/start /profile /find /stop /skip /report /help")

# === Run Bot ===
if __name__=="__main__":
    asyncio.run(dp.start_polling(bot))
