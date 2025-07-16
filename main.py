"""
MateFinder Telegram Dating Bot
Author : 𝐒𝐮𝐝𝐢𝐩 𝐌𝐨𝐧𝐝𝐚𝐥 (July 2025)
Python : 3.11+
Libs   : python-telegram-bot v21 (async)
Run    : python main.py
"""

import asyncio
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    constants,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ---------- Config & Globals ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("7620053279:AAFfGVyoXsL5nOL0U5DcmDG4QDsW3XYb6t4")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("MateFinderBot")

# Admins list (provide your Telegram user ids here)
ADMINS = {7620053279}  # Replace with actual admin user IDs

# Conversation states
class RegState(Enum):
    GENDER = auto()
    PREF = auto()
    DONE = auto()
    EDIT_GENDER = auto()
    EDIT_PREF = auto()

# In-memory data (replace with DB for production)
waiting_queue: set[int] = set()
active_chats: dict[int, int] = {}       # user_id -> partner_id
reports: defaultdict[int, int] = defaultdict(int)  # partner_id -> count
USER_DATA_KEY = "profile"

# ---------- Data Classes ----------
@dataclass
class Profile:
    gender: str
    preference: str   # "male" | "female" | "any"

# ---------- Helper Functions ----------
def get_partner(user_id: int) -> int | None:
    return active_chats.get(user_id)

def get_user_profile(app, user_id: int) -> Profile | None:
    user_data_dict = app.user_data.get(user_id, {})
    return user_data_dict.get(USER_DATA_KEY)

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

async def end_chat(user_id: int, app) -> None:
    """Disconnect user (and partner if exists)."""
    partner_id = active_chats.pop(user_id, None)
    if partner_id:
        active_chats.pop(partner_id, None)
        await app.bot.send_message(
            partner_id,
            "🔇 The chat has ended. Type /find to meet someone new.",
        )

async def match_users(app):
    """Attempt to match users in queue based on preferences. Debug version."""
    log.info(f"[MATCH] Waiting queue: {waiting_queue}")
    if len(waiting_queue) < 2:
        log.info("[MATCH] Not enough users in queue.")
        return

    queue_list = list(waiting_queue)
    for i, uid1 in enumerate(queue_list):
        user_data_dict = app.user_data.get(uid1, {})
        prof1: Profile = user_data_dict.get(USER_DATA_KEY)
        log.info(f"[MATCH] UID1: {uid1} Profile: {prof1}")
        if not prof1:
            continue
        for uid2 in queue_list[i + 1:]:
            if uid2 not in waiting_queue:
                continue
            user_data_dict2 = app.user_data.get(uid2, {})
            prof2: Profile = user_data_dict2.get(USER_DATA_KEY)
            log.info(f"[MATCH]   UID2: {uid2} Profile: {prof2}")
            if not prof2:
                continue
            ok1 = (prof1.preference == "any") or (prof2 and prof2.gender == prof1.preference)
            ok2 = (prof2.preference == "any") or (prof1 and prof1.gender == prof2.preference)
            log.info(f"[MATCH]   ok1={ok1}, ok2={ok2}")
            if ok1 and ok2:
                # Match!
                waiting_queue.discard(uid1)
                waiting_queue.discard(uid2)
                active_chats[uid1] = uid2
                active_chats[uid2] = uid1
                log.info(f"[MATCH] Matched {uid1} <-> {uid2}")
                await app.bot.send_message(
                    uid1,
                    "💖 You're now connected! Say hi.\n"
                    "Commands: /skip  /stop  /report  /cancel",
                )
                await app.bot.send_message(
                    uid2,
                    "💖 You're now connected! Say hi.\n"
                    "Commands: /skip  /stop  /report  /cancel",
                )
                return  # Only match one pair at a time
    log.info("[MATCH] No match found in this round.")

# ---------- Command Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only allow profile creation once per user
    if context.user_data.get(USER_DATA_KEY):
        await update.message.reply_text(
            "✅ You already have a profile.\nUse /profile to view it or /find to chat."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 Welcome to MateFinder!\n"
        "Let's set up your profile (only once).\n"
        "Select your gender:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("♂️ Male", callback_data="g_male"),
                    InlineKeyboardButton("♀️ Female", callback_data="g_female"),
                ],
                [InlineKeyboardButton("⚧️ Other", callback_data="g_other")],
            ]
        ),
    )
    return RegState.GENDER

async def gender_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gender = query.data.split("_")[1]
    context.user_data[USER_DATA_KEY] = Profile(gender=gender, preference="")
    await query.edit_message_text(
        "Who would you like to meet?",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("♂️ Male", callback_data="p_male"),
                    InlineKeyboardButton("♀️ Female", callback_data="p_female"),
                ],
                [InlineKeyboardButton("🤝 Anyone", callback_data="p_any")],
            ]
        ),
    )
    return RegState.PREF

async def pref_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pref = query.data.split("_")[1]
    profile: Profile = context.user_data[USER_DATA_KEY]
    profile.preference = "any" if pref == "any" else pref
    await query.edit_message_text(
        "✅ Profile saved!\n\nCommands:\n"
        "• /find – browse potential matches\n"
        "• /edit – edit your profile\n"
        "• /profile – view your profile\n"
        "• /stop – end current chat\n"
        "• /skip – next partner\n"
        "• /report – report current partner\n"
        "• /help – show all commands\n"
        "• /cancel – cancel current operation"
    )
    return ConversationHandler.END

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prof = context.user_data.get(USER_DATA_KEY)
    if not prof:
        await update.message.reply_text("❌ You don't have a profile yet. Use /start to create one.")
        return
    await update.message.reply_text(
        f"📝 Your Profile:\n"
        f"• Gender: {prof.gender.capitalize()}\n"
        f"• Preference: {prof.preference.capitalize() if prof.preference != 'any' else 'Anyone'}\n\n"
        "Use /edit to update your profile."
    )

async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    profile = context.user_data.get(USER_DATA_KEY)
    if not profile:
        await update.message.reply_text("❌ You must create a profile first. Use /start.")
        return
    if user_id in active_chats:
        await update.message.reply_text("💬 You're already in a chat. Use /skip or /stop.")
        return
    if user_id in waiting_queue:
        await update.message.reply_text("⏳ Still searching… please wait.")
        return

    waiting_queue.add(user_id)
    await update.message.reply_text("🔍 Searching for a match…")
    await match_users(context.application)

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in active_chats:
        partner_id = active_chats[user_id]
        await update.message.reply_text("💬 You're already in a chat! Say hi to your match.")
        return
    elif user_id in waiting_queue:
        await update.message.reply_text("⏳ Still searching… please wait.")
        return
    else:
        await update.message.reply_text("❌ You are not matched yet. Use /find to start searching.")

async def stop_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in active_chats:
        await end_chat(user_id, context.application)
        await update.message.reply_text("👋 You left the chat. Use /find to match again.")
    elif user_id in waiting_queue:
        waiting_queue.discard(user_id)
        await update.message.reply_text("🛑 Search stopped.")
    else:
        await update.message.reply_text("❌ You're not in a chat or queue.")

async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in active_chats:
        await update.message.reply_text("❌ You're not in a chat to skip.")
        return
    await end_chat(user_id, context.application)
    waiting_queue.add(user_id)
    await update.message.reply_text("⏭️ Looking for someone new…")
    await match_users(context.application)

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    partner_id = get_partner(user_id)
    if not partner_id:
        await update.message.reply_text("⚠️ You aren't chatting with anyone.")
        return
    reports[partner_id] += 1
    await update.message.reply_text("🚩 Partner reported. Thank you.")
    await end_chat(user_id, context.application)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>MateFinder Bot Commands:</b>\n\n"
        "<b>/start</b> - Start the bot and create your profile\n"
        "<b>/profile</b> - View your current profile\n"
        "<b>/edit</b> - Edit your existing profile\n"
        "<b>/find</b> - Browse potential matches\n"
        "<b>/chat</b> - Start chatting with a matched user\n"
        "<b>/cancel</b> - Cancel any current operation (profile/chat)\n"
        "<b>/stop</b> - End the current chat\n"
        "<b>/skip</b> - Next partner\n"
        "<b>/report</b> - Report current partner\n"
        "<b>/help</b> - Show help and commands\n"
        "<b>/admin</b> - Admin panel (admins only)\n"
        "<b>/broadcast</b> - Send message to all users (admins only)\n",
        parse_mode=constants.ParseMode.HTML,
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ This command is for admins only.")
        return
    await update.message.reply_text(
        "🛡️ <b>Admin Panel</b>\n"
        f"Total users: {len(context.application.user_data)}\n"
        f"Active chats: {len(active_chats) // 2}\n"
        f"Waiting queue: {len(waiting_queue)}\n"
        f"Reports: {sum(reports.values())}\n"
        "Use /broadcast <msg> to send a message to all users.",
        parse_mode=constants.ParseMode.HTML,
    )

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ This command is for admins only.")
        return
    try:
        msg = " ".join(context.args)
        if not msg:
            await update.message.reply_text("Usage: /broadcast <message>")
            return
        count = 0
        for uid in context.application.user_data:
            try:
                await context.bot.send_message(uid, f"📢 <b>Admin Broadcast:</b>\n{msg}", parse_mode=constants.ParseMode.HTML)
                count += 1
            except Exception as e:
                log.warning(f"Failed to send to {uid}: {e}")
        await update.message.reply_text(f"Broadcast sent to {count} users.")
    except Exception as e:
        await update.message.reply_text(f"Broadcast failed: {e}")

async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prof = context.user_data.get(USER_DATA_KEY)
    if not prof:
        await update.message.reply_text("❌ You don't have a profile yet. Use /start to create one.")
        return ConversationHandler.END
    await update.message.reply_text(
        "📝 What do you want to edit?\n"
        "Choose gender to edit your gender, or preference to edit who you'd like to meet.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Gender", callback_data="edit_gender"),
                    InlineKeyboardButton("Preference", callback_data="edit_pref"),
                ]
            ]
        ),
    )
    return RegState.EDIT_GENDER

async def edit_gender_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Select your new gender:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("♂️ Male", callback_data="g_male"),
                    InlineKeyboardButton("♀️ Female", callback_data="g_female"),
                ],
                [InlineKeyboardButton("⚧️ Other", callback_data="g_other")],
            ]
        ),
    )
    return RegState.GENDER

async def edit_pref_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Who would you like to meet?",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("♂️ Male", callback_data="p_male"),
                    InlineKeyboardButton("♀️ Female", callback_data="p_female"),
                ],
                [InlineKeyboardButton("🤝 Anyone", callback_data="p_any")],
            ]
        ),
    )
    return RegState.PREF

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Remove from waiting queue or end chat if active
    if user_id in waiting_queue:
        waiting_queue.discard(user_id)
        await update.message.reply_text("❌ Operation cancelled and removed from queue.")
    elif user_id in active_chats:
        await end_chat(user_id, context.application)
        await update.message.reply_text("❌ Operation cancelled and chat ended.")
    else:
        await update.message.reply_text("❌ No operation to cancel.")
    return ConversationHandler.END

async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    partner_id = get_partner(user_id)
    if not partner_id:
        await update.message.reply_text(
            "ℹ️ Not in a chat.\nUse /find to meet someone."
        )
        return
    # Relay text, stickers, or photo
    if update.message.sticker:
        await context.bot.send_sticker(partner_id, update.message.sticker.file_id)
    elif update.message.photo:
        photo = update.message.photo[-1]  # highest resolution
        caption = update.message.caption_html or ""
        await context.bot.send_photo(partner_id, photo.file_id, caption=caption, parse_mode=constants.ParseMode.HTML)
    elif update.message.text:
        await context.bot.send_message(partner_id, update.message.text_html, parse_mode=constants.ParseMode.HTML)
    else:
        await update.message.reply_text("⚠️ Unsupported message type.")

# ---------- Main ----------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Registration and editing conversation
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("edit", edit),
        ],
        states={
            RegState.GENDER: [CallbackQueryHandler(gender_choice, pattern="^g_")],
            RegState.PREF: [CallbackQueryHandler(pref_choice, pattern="^p_")],
            RegState.EDIT_GENDER: [
                CallbackQueryHandler(edit_gender_choice, pattern="^edit_gender$"),
                CallbackQueryHandler(edit_pref_choice, pattern="^edit_pref$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("help", help_cmd),
            CommandHandler("start", start),
        ],
    )
    app.add_handler(conv)

    # Chat commands
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("chat", chat))
    app.add_handler(CommandHandler("stop", stop_chat))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("edit", edit))

    # Message relay
    app.add_handler(
        MessageHandler(filters.TEXT | filters.Sticker.ALL | filters.PHOTO, relay)
    )

    log.info("Bot started…")
    app.run_polling()

if __name__ == "__main__":
    main()
