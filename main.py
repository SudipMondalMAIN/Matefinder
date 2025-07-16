MateFinder AI Dating Bot with GPT and Language Support

import logging import asyncio from aiogram import Bot, Dispatcher, F, types from aiogram.filters import CommandStart, Command from aiogram.fsm.state import State, StatesGroup from aiogram.fsm.context import FSMContext from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup from aiogram.utils.keyboard import InlineKeyboardBuilder import aiosqlite import openai

======== CONFIG ========

BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN" OPENAI_API_KEY = "sk-XXXX..."  # Replace with your real OpenAI key openai.api_key = OPENAI_API_KEY SUPPORTED_LANGUAGES = [ ("en", "English"), ("hi", "Hindi"), ("bn", "Bengali"), ("ta", "Tamil"), ("te", "Telugu"), ("zh", "Chinese"), ("es", "Spanish"), ("ar", "Arabic"), ("fr", "French") ]

======== Logging ========

logging.basicConfig(level=logging.INFO) log = logging.getLogger("matefinder")

======== DB Setup ========

DB_NAME = "matefinder_ai.db"

CREATE_TABLES = [ """ CREATE TABLE IF NOT EXISTS users ( user_id INTEGER PRIMARY KEY, name TEXT, age INTEGER, gender TEXT, language TEXT DEFAULT 'en' ) """ ]

======== States ========

class Profile(StatesGroup): waiting_name = State() waiting_age = State() waiting_gender = State() chatting = State()

======== Keyboards ========

def language_kb(): kb = InlineKeyboardBuilder() for code, lang in SUPPORTED_LANGUAGES: kb.button(text=lang, callback_data=f"lang:{code}") kb.adjust(3) return kb.as_markup()

def gender_kb(): return InlineKeyboardMarkup(inline_keyboard=[ [InlineKeyboardButton(text="Male", callback_data="gender:male")], [InlineKeyboardButton(text="Female", callback_data="gender:female")], [InlineKeyboardButton(text="Other", callback_data="gender:other")] ])

======== AI Reply ========

async def ai_reply(user_input: str, lang_code: str, user_profile: dict): prompt = f"You are a dating partner chatting in {lang_code.upper()}. Respond romantically and naturally." context = f"Name: {user_profile.get('name')}, Age: {user_profile.get('age')}, Gender: {user_profile.get('gender')}" full_prompt = f"{prompt}\n{context}\nPartner: {user_input}\nYou:" try: response = await openai.ChatCompletion.acreate( model="gpt-3.5-turbo", messages=[{"role": "user", "content": full_prompt}], temperature=0.8 ) return response.choices[0].message.content except Exception as e: return "[AI ERROR: Could not respond right now.]"

======== Bot Setup ========

bot = Bot(BOT_TOKEN, parse_mode="HTML") dp = Dispatcher()

@dp.startup() async def init_db(): async with aiosqlite.connect(DB_NAME) as db: for stmt in CREATE_TABLES: await db.execute(stmt) await db.commit()

======== /start =========

@dp.message(CommandStart()) async def start_cmd(m: types.Message, state: FSMContext): await m.answer("🌐 Choose your language:", reply_markup=language_kb())

@dp.callback_query(F.data.startswith("lang:")) async def set_language(cb: types.CallbackQuery, state: FSMContext): lang = cb.data.split(":")[1] await state.update_data(language=lang) await cb.message.answer("👤 What's your name?") await state.set_state(Profile.waiting_name) await cb.answer()

@dp.message(Profile.waiting_name) async def get_name(m: types.Message, state: FSMContext): await state.update_data(name=m.text.strip()) await m.answer("🎂 Your age?") await state.set_state(Profile.waiting_age)

@dp.message(Profile.waiting_age) async def get_age(m: types.Message, state: FSMContext): if not m.text.isdigit(): return await m.reply("Please enter a valid number.") await state.update_data(age=int(m.text.strip())) await m.answer("🚻 Select gender:", reply_markup=gender_kb()) await state.set_state(Profile.waiting_gender)

@dp.callback_query(F.data.startswith("gender:")) async def set_gender(cb: types.CallbackQuery, state: FSMContext): gender = cb.data.split(":")[1] data = await state.get_data() async with aiosqlite.connect(DB_NAME) as db: await db.execute("REPLACE INTO users(user_id, name, age, gender, language) VALUES (?, ?, ?, ?, ?)", (cb.from_user.id, data['name'], data['age'], gender, data['language'])) await db.commit() await state.clear() await cb.message.answer("✅ Profile saved! Use /chat to start chatting with your AI match.") await cb.answer()

======== /profile =========

@dp.message(Command("profile")) async def show_profile(m: types.Message): async with aiosqlite.connect(DB_NAME) as db: async with db.execute("SELECT name, age, gender, language FROM users WHERE user_id=?", (m.from_user.id,)) as c: row = await c.fetchone() if row: name, age, gender, lang = row await m.answer(f"👤 <b>Profile</b>\nName: {name}\nAge: {age}\nGender: {gender}\nLanguage: {lang}", parse_mode="HTML") else: await m.answer("No profile found. Use /start to set up.")

======== /chat =========

@dp.message(Command("chat")) async def chat_cmd(m: types.Message, state: FSMContext): await state.set_state(Profile.chatting) await m.answer("💬 You're now chatting with your AI partner. Say something!")

@dp.message(Profile.chatting) async def ai_convo(m: types.Message, state: FSMContext): async with aiosqlite.connect(DB_NAME) as db: async with db.execute("SELECT name, age, gender, language FROM users WHERE user_id=?", (m.from_user.id,)) as c: row = await c.fetchone() if not row: return await m.reply("Profile missing. Use /start.") name, age, gender, lang = row user_profile = {"name": name, "age": age, "gender": gender} reply = await ai_reply(m.text, lang, user_profile) await m.answer(reply)

======== /stop =========

@dp.message(Command("stop")) async def stop_chat(m: types.Message, state: FSMContext): await state.clear() await m.answer("🛑 Chat stopped. Use /chat to talk again.")

======== /help =========

@dp.message(Command("help")) async def help_cmd(m: types.Message): await m.answer(""" 🤖 <b>MateFinder Bot Commands</b> /start - Start the bot and create your profile /profile - View your current profile /chat - Start chatting with a matched user (AI) /stop - End the current chat /help - Show this help """, parse_mode="HTML")

======== Run App ========

if name == 'main': asyncio.run(dp.start_polling(bot))

