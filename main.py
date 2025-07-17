import asyncio
import logging
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Bot configuration
BOT_TOKEN = "7620053279:AAGUu17xi-1ZXCTcuRQI5P9T-E7gS5U3B24"  # Replace with your actual bot token
ADMIN_USER_ID = 6535216093  # Replace with your Telegram user ID

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

# Database setup
DB_NAME = "matefinder.db"

class ProfileStates(StatesGroup):
    """FSM states for profile editing"""
    editing_name = State()
    editing_age = State()
    editing_gender = State()
    editing_interest = State()
    editing_bio = State()

@dataclass
class User:
    """User data structure"""
    user_id: int
    name: str
    age: int
    gender: str
    interest_in: str
    bio: str
    created_at: str
    is_admin: bool = False

class DatabaseManager:
    """Database operations manager"""
    
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.init_database()
    
    def init_database(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                age INTEGER NOT NULL,
                gender TEXT NOT NULL,
                interest_in TEXT NOT NULL,
                bio TEXT,
                created_at TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                is_searching BOOLEAN DEFAULT FALSE,
                current_partner_id INTEGER DEFAULT NULL
            )
        ''')
        
        # Active chats table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                FOREIGN KEY (user1_id) REFERENCES users (user_id),
                FOREIGN KEY (user2_id) REFERENCES users (user_id)
            )
        ''')
        
        # Reports table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                reported_id INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (reporter_id) REFERENCES users (user_id),
                FOREIGN KEY (reported_id) REFERENCES users (user_id)
            )
        ''')
        
        # Blocked users table (to prevent immediate re-matching)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocked_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                blocked_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (blocked_user_id) REFERENCES users (user_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return User(
                user_id=result[0],
                name=result[1],
                age=result[2],
                gender=result[3],
                interest_in=result[4],
                bio=result[5],
                created_at=result[6],
                is_admin=bool(result[7])
            )
        return None
    
    def create_user(self, user_id: int, name: str, age: int, gender: str, interest_in: str, bio: str) -> bool:
        """Create new user"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            is_admin = user_id == ADMIN_USER_ID
            cursor.execute('''
                INSERT INTO users (user_id, name, age, gender, interest_in, bio, created_at, is_admin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, name, age, gender, interest_in, bio, datetime.now().isoformat(), is_admin))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def update_user_field(self, user_id: int, field: str, value: Any) -> bool:
        """Update specific user field"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute(f'UPDATE users SET {field} = ? WHERE user_id = ?', (value, user_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating user field: {e}")
            return False
        finally:
            conn.close()
    
    def find_match(self, user_id: int) -> Optional[int]:
        """Find potential match based on preferences"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Get current user's preferences
        user = self.get_user(user_id)
        if not user:
            return None
        
        # Find users with compatible preferences who are searching
        cursor.execute('''
            SELECT user_id FROM users 
            WHERE user_id != ? 
            AND gender = ? 
            AND interest_in = ? 
            AND is_searching = TRUE
            AND user_id NOT IN (
                SELECT blocked_user_id FROM blocked_matches WHERE user_id = ?
            )
            AND user_id NOT IN (
                SELECT user_id FROM blocked_matches WHERE blocked_user_id = ?
            )
            ORDER BY RANDOM()
            LIMIT 1
        ''', (user_id, user.interest_in, user.gender, user_id, user_id))
        
        result = cursor.fetchone()
        conn.close()
        
        return result[0] if result else None
    
    def create_chat(self, user1_id: int, user2_id: int) -> bool:
        """Create new chat session"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO active_chats (user1_id, user2_id, created_at)
                VALUES (?, ?, ?)
            ''', (user1_id, user2_id, datetime.now().isoformat()))
            
            # Update user status
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
            logger.error(f"Error creating chat: {e}")
            return False
        finally:
            conn.close()
    
    def end_chat(self, user_id: int) -> bool:
        """End current chat session"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            # Get current partner
            cursor.execute('SELECT current_partner_id FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            
            if result and result[0]:
                partner_id = result[0]
                
                # Update chat status
                cursor.execute('''
                    UPDATE active_chats SET is_active = FALSE
                    WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)
                ''', (user_id, partner_id, partner_id, user_id))
                
                # Reset user status
                cursor.execute('''
                    UPDATE users SET current_partner_id = NULL, is_searching = FALSE
                    WHERE user_id IN (?, ?)
                ''', (user_id, partner_id))
                
                conn.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Error ending chat: {e}")
            return False
        finally:
            conn.close()
    
    def block_user(self, user_id: int, blocked_user_id: int):
        """Block user from future matches"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO blocked_matches (user_id, blocked_user_id, created_at)
                VALUES (?, ?, ?)
            ''', (user_id, blocked_user_id, datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logger.error(f"Error blocking user: {e}")
        finally:
            conn.close()
    
    def report_user(self, reporter_id: int, reported_id: int, reason: str):
        """Report user for inappropriate behavior"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO reports (reporter_id, reported_id, reason, created_at)
                VALUES (?, ?, ?, ?)
            ''', (reporter_id, reported_id, reason, datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logger.error(f"Error reporting user: {e}")
        finally:
            conn.close()
    
    def get_stats(self) -> Dict[str, int]:
        """Get bot statistics"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM active_chats WHERE is_active = TRUE')
        active_chats = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM reports')
        total_reports = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_users': total_users,
            'active_chats': active_chats,
            'total_reports': total_reports
        }
    
    def get_all_users(self) -> List[int]:
        """Get all user IDs for broadcasting"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id FROM users')
        results = cursor.fetchall()
        conn.close()
        
        return [row[0] for row in results]

# Initialize database
db = DatabaseManager(DB_NAME)

# Helper functions
def create_gender_keyboard():
    """Create gender selection keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Male", callback_data="gender_male")],
        [InlineKeyboardButton(text="👩 Female", callback_data="gender_female")],
        [InlineKeyboardButton(text="⚧️ Other", callback_data="gender_other")]
    ])
    return keyboard

def create_profile_keyboard():
    """Create profile management keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Edit Name", callback_data="edit_name")],
        [InlineKeyboardButton(text="🎂 Edit Age", callback_data="edit_age")],
        [InlineKeyboardButton(text="⚧️ Edit Gender", callback_data="edit_gender")],
        [InlineKeyboardButton(text="❤️ Edit Interest", callback_data="edit_interest")],
        [InlineKeyboardButton(text="📝 Edit Bio", callback_data="edit_bio")]
    ])
    return keyboard

def create_admin_keyboard():
    """Create admin panel keyboard"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")]
    ])
    return keyboard

# Command handlers
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Handle /start command"""
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
            "Let's create your profile. First, please tell me your name:",
            reply_markup=None
        )
        await state.set_state(ProfileStates.editing_name)

@router.message(Command("profile"))
async def cmd_profile(message: Message):
    """Handle /profile command"""
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
        f"❤️ Looking for: {user.interest_in}\n"
        f"📝 Bio: {user.bio}\n\n"
        f"📅 Joined: {user.created_at.split('T')[0]}"
    )
    
    await message.answer(profile_text, reply_markup=create_profile_keyboard(), parse_mode="Markdown")

@router.message(Command("edit"))
async def cmd_edit(message: Message):
    """Handle /edit command"""
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
    """Handle /find command"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await message.answer("❌ Please start the bot first with /start")
        return
    
    # Check if user is already in chat
    if user.name:  # Simple check, you might want to verify with current_partner_id
        current_partner = db.get_user(user_id)
        if current_partner and hasattr(current_partner, 'current_partner_id') and current_partner.current_partner_id:
            await message.answer("❌ You're already in a chat! Use /stop to end it first.")
            return
    
    # Set user as searching
    db.update_user_field(user_id, 'is_searching', True)
    
    await message.answer("🔍 Searching for a match... Please wait!")
    
    # Find match
    match_id = db.find_match(user_id)
    
    if match_id:
        # Create chat
        if db.create_chat(user_id, match_id):
            await message.answer(
                "🎉 Match found! You can now start chatting.\n\n"
                "💬 Send any message to chat with your partner\n"
                "🚫 Use /stop to end the chat\n"
                "⏭️ Use /skip to find a new partner\n"
                "🚨 Use /report to report inappropriate behavior"
            )
            
            # Notify the matched user
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
                logger.error(f"Error notifying matched user: {e}")
        else:
            await message.answer("❌ Failed to create chat. Please try again.")
    else:
        await message.answer(
            "😔 No matches found at the moment.\n\n"
            "You'll remain in the queue and be notified when someone matches your preferences!"
        )

@router.message(Command("stop"))
async def cmd_stop(message: Message):
    """Handle /stop command"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await message.answer("❌ Please start the bot first with /start")
        return
    
    # Get current partner
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT current_partner_id FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        partner_id = result[0]
        
        # End chat
        if db.end_chat(user_id):
            await message.answer("✅ Chat ended successfully!")
            
            # Notify partner
            try:
                await bot.send_message(
                    partner_id,
                    "💔 Your chat partner has ended the conversation.\n\n"
                    "Use /find to search for a new match!"
                )
            except Exception as e:
                logger.error(f"Error notifying partner: {e}")
        else:
            await message.answer("❌ Failed to end chat.")
    else:
        await message.answer("❌ You're not currently in a chat.")

@router.message(Command("skip"))
async def cmd_skip(message: Message):
    """Handle /skip command"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await message.answer("❌ Please start the bot first with /start")
        return
    
    # Get current partner
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT current_partner_id FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        partner_id = result[0]
        
        # Block current partner temporarily
        db.block_user(user_id, partner_id)
        
        # End current chat
        db.end_chat(user_id)
        
        await message.answer("⏭️ Skipped current partner. Use /find to search for a new match!")
        
        # Notify partner
        try:
            await bot.send_message(
                partner_id,
                "⏭️ Your chat partner has skipped to find someone else.\n\n"
                "Use /find to search for a new match!"
            )
        except Exception as e:
            logger.error(f"Error notifying partner: {e}")
    else:
        await message.answer("❌ You're not currently in a chat.")

@router.message(Command("report"))
async def cmd_report(message: Message):
    """Handle /report command"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await message.answer("❌ Please start the bot first with /start")
        return
    
    # Get current partner
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT current_partner_id FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        partner_id = result[0]
        
        # Report user
        db.report_user(user_id, partner_id, "Inappropriate behavior")
        
        # Block user
        db.block_user(user_id, partner_id)
        
        # End chat
        db.end_chat(user_id)
        
        await message.answer(
            "🚨 User reported successfully!\n\n"
            "The chat has been ended and you won't be matched with this user again.\n"
            "Use /find to search for a new match."
        )
    else:
        await message.answer("❌ You're not currently in a chat.")

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command"""
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
        "3. Chat with your match\n"
        "4. Use /stop or /skip when done\n\n"
        "🛡️ **Safety**: Always report inappropriate behavior!"
    )
    
    await message.answer(help_text, parse_mode="Markdown")

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Handle /cancel command"""
    await state.clear()
    await message.answer("❌ Action cancelled.")

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Handle /admin command"""
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
    """Handle /broadcast command"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_USER_ID:
        await message.answer("❌ You don't have permission to use this command.")
        return
    
    await message.answer("📢 Please send the message you want to broadcast to all users:")
    # You would need to implement state for this

# Callback query handlers
@router.callback_query(F.data.startswith("gender_"))
async def handle_gender_selection(callback: CallbackQuery, state: FSMContext):
    """Handle gender selection"""
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
            "Now, what gender are you interested in?",
            reply_markup=create_gender_keyboard()
        )
        await state.set_state(ProfileStates.editing_interest)

@router.callback_query(F.data.startswith("edit_"))
async def handle_edit_selection(callback: CallbackQuery, state: FSMContext):
    """Handle profile edit selection"""
    edit_map = {
        "edit_name": ("editing_name", "Please enter your new name:"),
        "edit_age": ("editing_age", "Please enter your new age:"),
        "edit_gender": ("editing_gender", "Please select your gender:"),
        "edit_interest": ("editing_interest", "Please select your interest:"),
        "edit_bio": ("editing_bio", "Please enter your new bio:")
    }
    
    if callback.data in edit_map:
        state_name, message_text = edit_map[callback.data]
        
        if callback.data in ["edit_gender", "edit_interest"]:
            await callback.message.edit_text(message_text, reply_markup=create_gender_keyboard())
        else:
            await callback.message.edit_text(message_text)
        
        await state.set_state(getattr(ProfileStates, state_name))

# FSM handlers for profile creation/editing
@router.message(ProfileStates.editing_name)
async def process_name(message: Message, state: FSMContext):
    """Process name input"""
    name = message.text.strip()
    
    if len(name) < 2 or len(name) > 50:
        await message.answer("❌ Name must be between 2 and 50 characters. Please try again:")
        return
    
    data = await state.get_data()
    user_id = message.from_user.id
    
    # Check if this is profile creation or editing
    existing_user = db.get_user(user_id)
    
    if existing_user:
        # Edit existing profile
        db.update_user_field(user_id, 'name', name)
        await message.answer(f"✅ Name updated to: {name}")
        await state.clear()
    else:
        # Continue profile creation
        await state.update_data(name=name)
        await message.answer(f"✅ Name set to: {name}\n\nNow, please enter your age:")
        await state.set_state(ProfileStates.editing_age)

@router.message(ProfileStates.editing_age)
async def process_age(message: Message, state: FSMContext):
    """Process age input"""
    try:
        age = int(message.text.strip())
        if age < 18 or age > 100:
            await message.answer("❌ Age must be between 18 and 100. Please try again:")
            return
    except ValueError:
        await message.answer("❌ Please enter a valid number for age:")
        return
    
    data = await state.get_data()
    user_id = message.from_user.id
    
    # Check if this is profile creation or editing
    existing_user = db.get_user(user_id)
    
    if existing_user:
        # Edit existing profile
        db.update_user_field(user_id, 'age', age)
        await message.answer(f"✅ Age updated to: {age}")
        await state.clear()
    else:
        # Continue profile creation
        await state.update_data(age=age)
        await message.answer(
            f"✅ Age set to: {age}\n\nNow, please select your gender:",
            reply_markup=create_gender_keyboard()
        )
        await state.set_state(ProfileStates.editing_gender)

@router.message(ProfileStates.editing_bio)
async def process_bio(message: Message, state: FSMContext):
    """Process bio input"""
    bio = message.text.strip()
    
    if len(bio) > 500:
        await message.answer("❌ Bio must be less than 500 characters. Please try again:")
        return
    
    data = await state.get_data()
    user_id = message.from_user.id
    
    # Check if this is profile creation or editing
    existing_user = db.get_user(user_id)
    
    if existing_user:
        # Edit existing profile
        db.update_user_field(user_id, 'bio', bio)
        await message.answer(f"✅ Bio updated!")
        await state.clear()
    else:
        # Complete profile creation
        await state.update_data(bio=bio)
        data = await state.get_data()
        
        # Create user profile
        if db.create_user(
            user_id=user_id,
            name=data['name'],
            age=data['age'],
            gender=data['gender'],
            interest_in=data['interest_in'],
            bio=bio
        ):
            await message.answer(
                f"🎉 Profile created successfully!\n\n"
                f"📛 Name: {data['name']}\n"
                f"🎂 Age: {data['age']}\n"
                f"⚧️ Gender: {data['gender']}\n"
                f"❤️ Looking for: {data['interest_in']}\n"
                f"📝 Bio: {bio}\n\n"
                f"🔍 Use /find to start looking for matches!\n"
                f"✏️ Use /edit to modify your profile anytime."
            )
        else:
            await message.answer("❌ Failed to create profile. Please try again with /start")
        
        await state.clear()

# Message handler for chat forwarding
@router.message(F.text & ~F.text.startswith('/'))
async def handle_chat_message(message: Message):
    """Handle regular chat messages"""
    user_id = message.from_user.id
    
    # Get current partner
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT current_partner_id FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        partner_id = result[0]
        
        # Forward message to partner
        try:
            await bot.send_message(
                partner_id,
                f"💬 {message.text}"
            )
        except Exception as e:
            logger.error(f"Error forwarding message: {e}")
            await message.answer("❌ Failed to send message. Your partner might have left the chat.")
    else:
        await message.answer(
            "❌ You're not currently in a chat.\n\n"
            "🔍 Use /find to search for a match!"
        )

# Add router to dispatcher
dp.include_router(router)

async def main():
    """Main function to start the bot"""
    logger.info("Starting MateFinder bot...")
    
    # Start polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
