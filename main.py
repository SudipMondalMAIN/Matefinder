import asyncio
import logging
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ------- CONFIGURATION --------
BOT_TOKEN = "7620053279:AAGUu17xi-1ZXCTcuRQI5P9T-E7gS5U3B24"        # <-- PUT BOT TOKEN HERE
ADMIN_USER_ID = 6535216093                # <-- PUT YOUR TELEGRAM USER ID HERE (for /admin)
DB_NAME = "matefinder.db"

# ------- LOGGING -------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------- AIOGRAM INIT -------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

# ------- USER MODEL ---------
@dataclass
class User:
    user_id: int
    name: str
    age: int
    gender: str
    bio: str
    created_at: str
    is_admin: bool = False
    photo_id: str = None

# ------- DB MANAGER ---------
class DatabaseManager:
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.init_database()
    def init_database(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        # User table (with profile picture)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                age INTEGER NOT NULL,
                gender TEXT NOT NULL,
                bio TEXT,
                created_at TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                is_searching BOOLEAN DEFAULT FALSE,
                current_partner_id INTEGER DEFAULT NULL,
                photo_id TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                reported_id INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocked_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                blocked_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()
    # --- User handling ---
    def get_user(self, user_id: int) -> Optional[User]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        if result:
            return User(
                result[0], result[1], result[2], result[3], result[4],
                result[5], bool(result[6]), result[10] if len(result) > 10 else None
            )
        return None
    def create_user(self, user_id: int, name: str, age: int, gender: str, bio: str, photo_id: str) -> bool:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            is_admin = user_id == ADMIN_USER_ID
            cursor.execute('''
                INSERT INTO users (user_id, name, age, gender, bio, created_at, is_admin, photo_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, name, age, gender, bio, datetime.now().isoformat(), is_admin, photo_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    def update_user_field(self, user_id: int, field: str, value: Any) -> bool:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute(f'UPDATE users SET {field} = ? WHERE user_id = ?', (value, user_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Update user err: {e}")
            return False
        finally:
            conn.close()
    def set_user_searching(self, user_id: int, searching: bool):
        self.update_user_field(user_id, 'is_searching', int(searching))
    def clear_partner(self, user_id: int):
        self.update_user_field(user_id, 'current_partner_id', None)
    # --- Matchmaking (anyone except blocked/skipped) ---
    def find_match(self, user_id: int) -> Optional[int]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id FROM users 
            WHERE user_id != ?
            AND is_searching = TRUE
            AND user_id NOT IN (
                SELECT blocked_user_id FROM blocked_matches WHERE user_id = ?
            )
            AND user_id NOT IN (
                SELECT user_id FROM blocked_matches WHERE blocked_user_id = ?
            )
            AND current_partner_id IS NULL
            ORDER BY RANDOM()
            LIMIT 1
        ''', (user_id, user_id, user_id))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    # --- Chat management ---
    def create_chat(self, user1_id: int, user2_id: int) -> bool:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO active_chats (user1_id, user2_id, created_at)
                VALUES (?, ?, ?)
            ''', (user1_id, user2_id, datetime.now().isoformat()))
            cursor.execute('''
                UPDATE users SET is_searching = FALSE, current_partner_id = ?
                WHERE user_id = ?
            ''', (user2_id, user1_id))
            cursor.execute('''
                UPDATE users SET is_searching = FALSE, current_partner_id = ?
                WHERE user_id = ?
            ''', (user1_id, user2_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Create chat error: {e}")
            return False
        finally:
            conn.close()
    def end_chat(self, user_id: int) -> Optional[int]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT current_partner_id FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            if result and result[0]:
                partner_id = result[0]
                cursor.execute('''
                    UPDATE active_chats SET is_active = FALSE
                    WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)
                ''', (user_id, partner_id, partner_id, user_id))
                cursor.execute('''
                    UPDATE users SET current_partner_id = NULL, is_searching = FALSE
                    WHERE user_id IN (?, ?)
                ''', (user_id, partner_id))
                conn.commit()
                return partner_id
            return None
        except Exception as e:
            logger.error(f"End chat error: {e}")
            return None
        finally:
            conn.close()
    # --- Block ---
    def block_user(self, user_id: int, blocked_user_id: int):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO blocked_matches (user_id, blocked_user_id, created_at)
                VALUES (?, ?, ?)
            ''', (user_id, blocked_user_id, datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logger.error(f"Block err: {e}")
        finally:
            conn.close()
    # --- Report ---
    def report_user(self, reporter_id: int, reported_id: int, reason: str):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO reports (reporter_id, reported_id, reason, created_at)
                VALUES (?, ?, ?, ?)
            ''', (reporter_id, reported_id, reason, datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logger.error(f"Report err: {e}")
        finally:
            conn.close()
    # --- Stats/Admin ---
    def get_stats(self) -> Dict[str, int]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM active_chats WHERE is_active = TRUE')
        active_chats = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM reports')
        total_reports = cursor.fetchone()[0]
        conn.close()
        return {'total_users': total_users, 'active_chats': active_chats, 'total_reports': total_reports}
    def get_all_users(self) -> List[int]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        results = cursor.fetchall()
        conn.close()
        return [row[0] for row in results]
    def get_current_partner(self, user_id: int) -> Optional[int]:
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT current_partner_id, photo_id FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result and result[0] else None

# ------- DB INIT -------------
db = DatabaseManager(DB_NAME)

# ------- FSM STATES ----------
class ProfileStates(StatesGroup):
    editing_name = State()
    editing_age = State()
    editing_gender = State()
    editing_bio = State()
    editing_photo = State()

# ------- Inline Keyboards ----
def create_gender_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Male", callback_data="gender_male")],
        [InlineKeyboardButton(text="👩 Female", callback_data="gender_female")],
        [InlineKeyboardButton(text="⚧️ Other", callback_data="gender_other")]
    ])
def create_profile_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Edit Name", callback_data="edit_name")],
        [InlineKeyboardButton(text="🎂 Edit Age", callback_data="edit_age")],
        [InlineKeyboardButton(text="⚧️ Edit Gender", callback_data="edit_gender")],
        [InlineKeyboardButton(text="📝 Edit Bio", callback_data="edit_bio")],
        [InlineKeyboardButton(text="🖼️ Edit Photo", callback_data="edit_photo")]
    ])
def create_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")]
    ])

# ------- COMMAND HANDLERS ----
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    existing_user = db.get_user(user_id)
    if existing_user:
        await message.answer(
            f"👋 Welcome back, {existing_user.name}!\n\n"
            "🔸 Use /find to start searching for a match\n"
            "🔸 Use /profile to view your profile\n"
            "🔸 Use /help to see all commands"
        )
    else:
        await message.answer(
            "🎉 Welcome to MateFinder!\n\n"
            "Let's create your profile. First, please tell me your name:"
        )
        await state.set_state(ProfileStates.editing_name)

@router.message(Command("profile"))
async def cmd_profile(message: Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    if not user:
        await message.answer("❌ Please start the bot first with /start")
        return
    profile_text = (
        f"👤 **Your Profile**\n\n"
        f"📛 Name: {user.name}\n"
        f"🎂 Age: {user.age}\n"
        f"⚧️ Gender: {user.gender}\n"
        f"📝 Bio: {user.bio}\n\n"
        f"📅 Joined: {user.created_at.split('T')[0]}"
    )
    if user.photo_id:
        await message.answer_photo(user.photo_id, caption=profile_text, parse_mode="Markdown", reply_markup=create_profile_keyboard())
    else:
        await message.answer(profile_text, parse_mode="Markdown", reply_markup=create_profile_keyboard())

@router.message(Command("edit"))
async def cmd_edit(message: Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    if not user:
        await message.answer("❌ Please start the bot first with /start")
        return
    await message.answer(
        "✏️ **Edit Profile**\n\nSelect what you'd like to edit:",
        reply_markup=create_profile_keyboard(),
        parse_mode="Markdown"
    )

@router.message(Command("find"))
async def cmd_find(message: Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    if not user:
        await message.answer("❌ Please start the bot first with /start")
        return
    if db.get_current_partner(user_id):
        await message.answer("❌ You're already in a chat! Use /stop to end it first.")
        return
    db.set_user_searching(user_id, True)
    await message.answer("🔍 Searching for a match... Please wait.")
    match_id = db.find_match(user_id)
    if match_id:
        if db.create_chat(user_id, match_id):
            await message.answer(
                "🎉 Match found! You can now start chatting.\n\n"
                "💬 Send any message to chat with your partner\n"
                "🚫 Use /stop to end the chat\n"
                "⏭️ Use /skip to find a new partner\n"
                "🚨 Use /report to report inappropriate behavior"
            )
            try:
                await bot.send_message(
                    match_id,
                    "🎉 Match found! You can now start chatting.\n\n"
                    "💬 Send any message to chat with your partner\n"
                    "🚫 Use /stop to end the chat\n"
                    "⏭️ Use /skip to find a new partner\n"
                    "🚨 Use /report to report inappropriate behavior"
                )
            except Exception as e:
                logger.error(f"Notify matched user error: {e}")
        else:
            await message.answer("❌ Failed to create chat. Please try again.")
    else:
        await message.answer(
            "😔 No matches found right now.\n"
            "You'll remain in queue and be notified when someone else is available!"
        )

@router.message(Command("stop"))
async def cmd_stop(message: Message):
    user_id = message.from_user.id
    partner_id = db.get_current_partner(user_id)
    if partner_id:
        db.end_chat(user_id)
        await message.answer("✅ Chat ended successfully!")
        try:
            await bot.send_message(
                partner_id,
                "💔 Your chat partner has ended the conversation.\n\n"
                "Use /find to search for a new match!"
            )
        except Exception as e:
            logger.error(f"Notify partner (stop) failed: {e}")
    else:
        await message.answer("❌ You're not currently in a chat.")

@router.message(Command("skip"))
async def cmd_skip(message: Message):
    user_id = message.from_user.id
    partner_id = db.get_current_partner(user_id)
    if partner_id:
        db.block_user(user_id, partner_id)
        db.end_chat(user_id)
        await message.answer("⏭️ Skipped current partner. Use /find to search for a new match!")
        try:
            await bot.send_message(
                partner_id,
                "⏭️ Your chat partner has skipped to find someone else.\n\n"
                "Use /find to search for a new match!"
            )
        except Exception as e:
            logger.error(f"Notify partner (skip) failed: {e}")
    else:
        await message.answer("❌ You're not currently in a chat.")

@router.message(Command("report"))
async def cmd_report(message: Message):
    user_id = message.from_user.id
    partner_id = db.get_current_partner(user_id)
    if partner_id:
        db.report_user(user_id, partner_id, "Inappropriate behavior")
        db.block_user(user_id, partner_id)
        db.end_chat(user_id)
        await message.answer(
            "🚨 User reported and blocked!\n"
            "The chat has ended and you won't match with this user again.\n"
            "Use /find to search for a new match."
        )
    else:
        await message.answer("❌ You're not currently in a chat.")

@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🤖 **MateFinder Bot Commands**\n\n"
        "🔸 /start - Start the bot and create profile\n"
        "🔸 /profile - View your current profile\n"
        "🔸 /edit - Edit your profile\n"
        "🔸 /find - Find a match and start chatting\n"
        "🔸 /stop - End current chat\n"
        "🔸 /skip - Skip current partner\n"
        "🔸 /report - Report inappropriate behavior\n"
        "🔸 /cancel - Cancel any ongoing action\n"
        "🔸 /help - Show this help message\n\n"
        "💡 **How to use:**\n"
        "1. Create your profile with /start\n"
        "2. Use /find to search for matches\n"
        "3. Chat with your match anonymously\n"
        "4. Use /stop or /skip when done\n"
        "🛡️ Always report inappropriate behavior!"
    )
    await message.answer(help_text, parse_mode="Markdown")

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Action cancelled.")

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_USER_ID:
        await message.answer("❌ You don't have permission to use this command.")
        return
    stats = db.get_stats()
    admin_text = (
        f"🔧 **Admin Panel**\n\n"
        f"👥 Total Users: {stats['total_users']}\n"
        f"💬 Active Chats: {stats['active_chats']}\n"
        f"🚨 Total Reports: {stats['total_reports']}"
    )
    await message.answer(admin_text, reply_markup=create_admin_keyboard(), parse_mode="Markdown")

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_USER_ID:
        await message.answer("❌ You don't have permission to use this command.")
        return
    await message.answer("📢 Please send the message you want to broadcast to all users. (Not implemented)")

# ------- CALLBACK HANDLERS ----
@router.callback_query(F.data.startswith("gender_"))
async def handle_gender_selection(callback: CallbackQuery, state: FSMContext):
    gender_map = {
        "gender_male": "Male",
        "gender_female": "Female",
        "gender_other": "Other"
    }
    gender = gender_map.get(callback.data)
    if gender:
        await state.update_data(gender=gender)
        await callback.message.edit_text(
            f"✅ Gender set to: {gender}\n\n"
            "Now, please enter a short bio about yourself:"
        )
        await state.set_state(ProfileStates.editing_bio)

@router.callback_query(F.data.startswith("edit_"))
async def handle_edit_selection(callback: CallbackQuery, state: FSMContext):
    edit_map = {
        "edit_name": ("editing_name", "Please enter your new name:"),
        "edit_age": ("editing_age", "Please enter your new age:"),
        "edit_gender": ("editing_gender", "Please select your gender:"),
        "edit_bio": ("editing_bio", "Please enter your new bio:"),
        "edit_photo": ("editing_photo", "Send your new profile picture or /skip to remove.")
    }
    if callback.data in edit_map:
        state_name, message_text = edit_map[callback.data]
        if callback.data == "edit_gender":
            await callback.message.edit_text(message_text, reply_markup=create_gender_keyboard())
        else:
            await callback.message.edit_text(message_text)
        await state.set_state(getattr(ProfileStates, state_name))

# ------- FSM PROFILE CREATION & EDIT --------
@router.message(ProfileStates.editing_name)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await message.answer("❌ Name must be between 2 and 50 characters. Please try again:")
        return
    user_id = message.from_user.id
    existing_user = db.get_user(user_id)
    if existing_user:
        db.update_user_field(user_id, 'name', name)
        await message.answer(f"✅ Name updated to: {name}")
        await state.clear()
    else:
        await state.update_data(name=name)
        await message.answer("✅ Name set. Please enter your age (18-100):")
        await state.set_state(ProfileStates.editing_age)

@router.message(ProfileStates.editing_age)
async def process_age(message: Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        if age < 18 or age > 100:
            await message.answer("❌ Age must be between 18 and 100. Please try again:")
            return
    except ValueError:
        await message.answer("❌ Please enter a valid number for age:")
        return
    user_id = message.from_user.id
    existing_user = db.get_user(user_id)
    if existing_user:
        db.update_user_field(user_id, 'age', age)
        await message.answer(f"✅ Age updated to: {age}")
        await state.clear()
    else:
        await state.update_data(age=age)
        await message.answer("✅ Age set. Please select your gender:", reply_markup=create_gender_keyboard())
        await state.set_state(ProfileStates.editing_gender)

@router.message(ProfileStates.editing_bio)
async def process_bio(message: Message, state: FSMContext):
    bio = message.text.strip()
    if len(bio) > 500:
        await message.answer("❌ Bio must be less than 500 characters. Please try again:")
        return
    user_id = message.from_user.id
    existing_user = db.get_user(user_id)
    if existing_user:
        db.update_user_field(user_id, 'bio', bio)
        await message.answer(f"✅ Bio updated!")
        await state.clear()
    else:
        await state.update_data(bio=bio)
        await message.answer(
            "Would you like to add a profile picture?\nSend me a photo, or type /skip."
        )
        await state.set_state(ProfileStates.editing_photo)

@router.message(ProfileStates.editing_photo, F.photo)
async def process_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    data = await state.get_data()
    user_id = message.from_user.id
    created = db.create_user(
        user_id=user_id,
        name=data['name'],
        age=data['age'],
        gender=data['gender'],
        bio=data['bio'],
        photo_id=photo_id
    )
    if created:
        profile_text = (
            f"🎉 Profile created successfully!\n\n"
            f"📛 Name: {data['name']}\n"
            f"🎂 Age: {data['age']}\n"
            f"⚧️ Gender: {data['gender']}\n"
            f"📝 Bio: {data['bio']}\n"
            f"🖼️ Photo: [see above]\n\n"
            f"🔍 Use /find to start looking for matches!\n"
            f"✏️ Use /edit to modify your profile anytime."
        )
        await message.answer_photo(photo_id, caption=profile_text)
    else:
        await message.answer("❌ Failed to create profile. Please try again with /start")
    await state.clear()

@router.message(ProfileStates.editing_photo, F.text & F.text.lower() == "/skip")
async def process_skip_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    created = db.create_user(
        user_id=user_id,
        name=data['name'],
        age=data['age'],
        gender=data['gender'],
        bio=data['bio'],
        photo_id=None
    )
    if created:
        await message.answer(
            f"🎉 Profile created successfully!\n\n"
            f"📛 Name: {data['name']}\n"
            f"🎂 Age: {data['age']}\n"
            f"⚧️ Gender: {data['gender']}\n"
            f"📝 Bio: {data['bio']}\n\n"
            f"🔍 Use /find to start looking for matches!\n"
            f"✏️ Use /edit to modify your profile anytime."
        )
    else:
        await message.answer("❌ Failed to create profile. Please try again with /start")
    await state.clear()

@router.message(ProfileStates.editing_photo, F.text)
async def process_photo_text_invalid(message: Message, state: FSMContext):
    await message.answer("❌ Please send a photo or type /skip.")

# Add for photo edit as well in profile editing
@router.message(ProfileStates.editing_photo, F.photo)
async def update_profile_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    user_id = message.from_user.id
    db.update_user_field(user_id, 'photo_id', photo_id)
    await message.answer("✅ Photo updated!")
    await state.clear()

@router.message(ProfileStates.editing_photo, F.text & F.text.lower() == "/skip")
async def clear_profile_photo(message: Message, state: FSMContext):
    user_id = message.from_user.id
    db.update_user_field(user_id, 'photo_id', None)
    await message.answer("✅ Profile photo removed!")
    await state.clear()

@router.message(ProfileStates.editing_photo, F.text)
async def invalid_edit_photo_text(message: Message, state: FSMContext):
    await message.answer("❌ Please send a photo or type /skip.")

# ------- CHAT RELAY HANDLER ---------
@router.message(F.text & ~F.text.startswith('/'))
async def handle_chat_message(message: Message):
    user_id = message.from_user.id
    partner_id = db.get_current_partner(user_id)
    if partner_id:
        try:
            await bot.send_message(
                partner_id,
                f"💬 {message.text}"
            )
        except Exception as e:
            logger.error(f"Relay msg fail: {e}")
            await message.answer("❌ Failed to send message. Your partner may have left the chat.")
    else:
        await message.answer(
            "❌ You're not currently in a chat.\n"
            "🔍 Use /find to search for a match!"
        )

@router.message(F.photo)
async def handle_photo_chat(message: Message):
    user_id = message.from_user.id
    partner_id = db.get_current_partner(user_id)
    if partner_id:
        try:
            await bot.send_photo(
                partner_id,
                photo=message.photo[-1].file_id,
                caption="(Photo from your chat partner)"
            )
        except Exception as e:
            logger.error(f"Photo relay fail: {e}")
    else:
        await message.answer(
            "❌ You're not currently in a chat.\n"
            "🔍 Use /find to search for a match!"
        )

# ------- DISPATCHER ----------
dp.include_router(router)

# ---------- MAIN -------------
async def main():
    logger.info("Starting MateFinder bot...")
    await dp.start_polling(bot)
if __name__ == "__main__":
    asyncio.run(main())
        
