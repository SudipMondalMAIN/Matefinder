"""
MateFinder AI Dating Bot with GPT and Language Support
"""

import logging
import asyncio
import openai
import aiosqlite

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ======== CONFIG ========
BOT_TOKEN = "7620053279:AAFfGVyoXsL5nOL0U5DcmDG4QDsW3XYb6t4"
OPENAI_API_KEY = "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"  # Replace with your real OpenAI key
openai.api_key = OPENAI_API_KEY

SUPPORTED_LANGUAGES = [
    ("en", "English"),
    ("hi", "Hindi"),
    ("bn", "Bengali"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    # Add more as needed
]

# ======== Logging ========
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("matefinder")

# ======== DB Setup ========
DB_NAME = "matefinder_ai.db"
CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        age INTEGER,
        gender TEXT,
        language TEXT DEFAULT 'en'
    )
    """
]

# ======== States ========
class Profile(StatesGroup):
    waiting_name = State()
    waiting_age = State()
    waiting_gender = State()
    chatting = State()
    ai_personality = State()  # Used for /skip to refresh personality

# ======== Keyboards ========
def language_kb():
    kb = InlineKeyboardBuilder()
    for code, lang in SUPPORTED_LANGUAGES:
        kb.button(text=lang, callback_data=f"lang:{code}")
    kb.adjust(3)
    return kb.as_markup()

def gender_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Male", callback_data="gender:male")],
            [InlineKeyboardButton(text="Female", callback_data="gender:female")],
            [InlineKeyboardButton(text="Other", callback_data="gender:other")],
        ]
    )

# ======== AI Reply ========
import random

default_personalities = [
    "flirty and playful",
    "gentle and caring",
    "witty and humorous",
    "mysterious and poetic",
    "bold and adventurous",
    "charming and elegant",
    "thoughtful and intellectual",
]

async def get_ai_personality(state: FSMContext):
    data = await state.get_data()
    personality = data.get("ai_personality")
    if not personality:
        personality = random.choice(default_personalities)
        await state.update_data(ai_personality=personality)
    return personality

async def refresh_ai_personality(state: FSMContext):
    personality = random.choice(default_personalities)
    await state.update_data(ai_personality=personality)
    return personality

async def ai_reply(user_input: str, lang_code: str, user_profile: dict, state: FSMContext):
    personality = await get_ai_personality(state)
    prompt = (
        f"You are a dating partner chatting in {lang_code.upper()}."
        f" Respond {personality}, romantically and naturally."
    )
    context = f"Name: {user_profile.get('name')}, Age: {user_profile.get('age')}, Gender: {user_profile.get('gender')}"
    try:
        resp = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt + "\n" + context},
                {"role": "user", "content": user_input}
            ],
            max_tokens=150,
            temperature=0.9,
            n=1,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return "Sorry, AI is not available at the moment."

# ======== Bot Setup ========
bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

@dp.startup()
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        for stmt in CREATE_TABLES:
            await db.execute(stmt)
        await db.commit()

# ======== Handlers ========

# --- /start ---
@dp.message(CommandStart())
async def start_cmd(m: types.Message, state: FSMContext):
    await m.answer("🌐 Choose your language:", reply_markup=language_kb())

@dp.callback_query(F.data.startswith("lang:"))
async def set_language(cb: types.CallbackQuery, state: FSMContext):
    lang = cb.data.split(":")[1]
    await state.update_data(language=lang)
    await cb.message.answer("💁‍♂️ What's your name?")
    await state.set_state(Profile.waiting_name)
    await cb.answer()

@dp.message(Profile.waiting_name)
async def get_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await m.answer("🎂 Your age?")
    await state.set_state(Profile.waiting_age)

@dp.message(Profile.waiting_age)
async def get_age(m: types.Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.reply("Please enter a valid number.")
    await state.update_data(age=int(m.text.strip()))
    await m.answer("🚻 Your gender?", reply_markup=gender_kb())
    await state.set_state(Profile.waiting_gender)

@dp.callback_query(F.data.startswith("gender:"))
async def set_gender(cb: types.CallbackQuery, state: FSMContext):
    gender = cb.data.split(":")[1]
    data = await state.get_data()
    user_id = cb.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, name, age, gender, language) VALUES (?, ?, ?, ?, ?)",
            (user_id, data.get("name"), data.get("age"), gender, data.get("language", "en")),
        )
        await db.commit()
    await cb.message.answer("✅ Profile saved!\nUse /chat to start chatting with your AI mate.")
    await state.clear()
    await cb.answer()

# --- /profile ---
@dp.message(Command("profile"))
async def show_profile(m: types.Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT name, age, gender, language FROM users WHERE user_id=?", (m.from_user.id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                name, age, gender, language = row
                await m.answer(
                    f"<b>Your Profile</b>\nName: {name}\nAge: {age}\nGender: {gender}\nLanguage: {language.upper()}"
                )
            else:
                await m.answer("No profile found. Use /start to create one.")

# --- /chat ---
@dp.message(Command("chat"))
async def chat_cmd(m: types.Message, state: FSMContext):
    await state.set_state(Profile.chatting)
    await get_ai_personality(state)  # Ensure personality is set for this chat session
    await m.answer("💬 You're now chatting with your AI partner. Say something romantic!\nUse /skip to meet a new personality.")

@dp.message(Profile.chatting)
async def ai_convo(m: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT name, age, gender, language FROM users WHERE user_id=?", (m.from_user.id,)
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        await m.answer("Profile not found. Use /start to create your profile.")
        return
    name, age, gender, language = row
    profile = {"name": name, "age": age, "gender": gender}
    reply = await ai_reply(m.text, language, profile, state)
    await m.answer(reply)

# --- /skip ---
@dp.message(Command("skip"))
async def skip_cmd(m: types.Message, state: FSMContext):
    personality = await refresh_ai_personality(state)
    await m.answer(
        f"🔄 You've skipped! Your AI partner now has a new personality: <i>{personality}</i>.\nKeep chatting!"
    )

# --- /stop ---
@dp.message(Command("stop"))
async def stop_chat(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("🛑 Chat stopped. Use /chat to talk again.")

# --- /help ---
@dp.message(Command("help"))
async def help_cmd(m: types.Message):
    await m.answer(
        """🤖 <b>MateFinder Bot Commands</b>
        /start - Start the bot and create your profile
        /profile - View your current profile
        /chat - Start chatting with your AI partner
        /skip - Refresh your AI partner's personality
        /stop - Stop chatting
        /help - Show this help message
        """
    )

# ======== Run App ========
if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
