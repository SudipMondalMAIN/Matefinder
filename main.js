/**
 * Converted MateFinder Telegram bot (Python aiogram -> JavaScript telegraf)
 * - All secrets/values are hardcoded as requested.
 * - Dependencies: telegraf, better-sqlite3
 *
 * Install:
 *   npm init -y
 *   npm install telegraf better-sqlite3
 *
 * Run:
 *   node main.js
 *
 * Notes:
 * - This is a direct feature-port; uses an in-memory Map for FSM state (like MemoryStorage).
 * - Uses better-sqlite3 (synchronous) for simplicity and parity with sqlite3 usage in Python.
 */

const { Telegraf, Markup } = require('telegraf');
const Database = require('better-sqlite3');
const fs = require('fs');

// Hardcoded config (as requested)
const BOT_TOKEN = "7620053279:AAGUu17xi-1ZXCTcuRQI5P9T-E7gS5U3B24";
const ADMIN_USER_ID = 6535216093;
const DB_NAME = "matefinder.db";

if (!BOT_TOKEN) {
  console.error('BOT_TOKEN is missing!');
  process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);

// Simple memory-based FSM (userId -> { state: string|null, data: {} })
const fsm = new Map();

function setState(userId, state) {
  let entry = fsm.get(userId) || { state: null, data: {} };
  entry.state = state;
  fsm.set(userId, entry);
}
function getState(userId) {
  return (fsm.get(userId) || { state: null, data: {} }).state;
}
function updateData(userId, obj) {
  let entry = fsm.get(userId) || { state: null, data: {} };
  entry.data = Object.assign(entry.data || {}, obj);
  fsm.set(userId, entry);
}
function getData(userId) {
  return (fsm.get(userId) || { state: null, data: {} }).data || {};
}
function clearState(userId) {
  fsm.delete(userId);
}

// Database manager using better-sqlite3
class DatabaseManager {
  constructor(dbName) {
    this.db = new Database(dbName);
    this.initDatabase();
  }

  initDatabase() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        age INTEGER NOT NULL,
        gender TEXT NOT NULL,
        bio TEXT,
        created_at TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        current_partner_id INTEGER DEFAULT NULL,
        photo_id TEXT
      );

      CREATE TABLE IF NOT EXISTS active_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user1_id INTEGER NOT NULL,
        user2_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        is_active INTEGER DEFAULT 1
      );

      CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        reported_id INTEGER NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS pending_likes (
        liker_id INTEGER NOT NULL,
        liked_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (liker_id, liked_id)
      );

      CREATE TABLE IF NOT EXISTS skips (
        user_id INTEGER NOT NULL,
        skipped_user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, skipped_user_id)
      );

      CREATE TABLE IF NOT EXISTS blocked_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        blocked_user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
      );
    `);
  }

  get_user(user_id) {
    const row = this.db.prepare('SELECT * FROM users WHERE user_id = ?').get(user_id);
    if (!row) return null;
    return {
      user_id: row.user_id,
      name: row.name,
      age: row.age,
      gender: row.gender,
      bio: row.bio,
      created_at: row.created_at,
      is_admin: !!row.is_admin,
      photo_id: row.photo_id || null,
      current_partner_id: row.current_partner_id || null
    };
  }

  create_user(user_id, name, age, gender, bio, photo_id) {
    try {
      const is_admin = user_id === ADMIN_USER_ID ? 1 : 0;
      const stmt = this.db.prepare(`
        INSERT INTO users (user_id, name, age, gender, bio, created_at, is_admin, photo_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `);
      stmt.run(user_id, name, age, gender, bio || '', new Date().toISOString(), is_admin, photo_id || null);
      return true;
    } catch (err) {
      // likely integrity error (already exists)
      return false;
    }
  }

  update_user_field(user_id, field, value) {
    try {
      // basic sanitization: allow only certain fields
      const allowed = ['name', 'age', 'gender', 'bio', 'photo_id', 'current_partner_id'];
      if (!allowed.includes(field)) return false;
      const stmt = this.db.prepare(`UPDATE users SET ${field} = ? WHERE user_id = ?`);
      const info = stmt.run(value, user_id);
      return info.changes > 0;
    } catch (e) {
      console.error('Update user err:', e);
      return false;
    }
  }

  set_in_chat(user1_id, user2_id) {
    const insertChat = this.db.prepare(`
      INSERT INTO active_chats (user1_id, user2_id, created_at) VALUES (?, ?, ?)
    `);
    const updatePartner = this.db.prepare(`UPDATE users SET current_partner_id = ? WHERE user_id = ?`);
    const tx = this.db.transaction(() => {
      insertChat.run(user1_id, user2_id, new Date().toISOString());
      updatePartner.run(user2_id, user1_id);
      updatePartner.run(user1_id, user2_id);
    });
    try {
      tx();
      return true;
    } catch (e) {
      console.error('Set in chat error:', e);
      return false;
    }
  }

  end_chat(user_id) {
    try {
      const partnerRow = this.db.prepare('SELECT current_partner_id FROM users WHERE user_id = ?').get(user_id);
      if (!partnerRow || !partnerRow.current_partner_id) return null;
      const partner_id = partnerRow.current_partner_id;
      const deactivate = this.db.prepare(`
        UPDATE active_chats SET is_active = 0
        WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)
      `);
      const clearUsers = this.db.prepare(`
        UPDATE users SET current_partner_id = NULL WHERE user_id IN (?, ?)
      `);
      const tx = this.db.transaction(() => {
        deactivate.run(user_id, partner_id, partner_id, user_id);
        clearUsers.run(user_id, partner_id);
      });
      tx();
      return partner_id;
    } catch (e) {
      console.error('End chat error:', e);
      return null;
    }
  }

  skip_user(user_id, target_id) {
    try {
      const stmt = this.db.prepare(`
        INSERT OR IGNORE INTO skips (user_id, skipped_user_id, created_at) VALUES (?, ?, ?)
      `);
      stmt.run(user_id, target_id, new Date().toISOString());
    } catch (e) {
      console.error('Skip user error:', e);
    }
  }

  block_user(user_id, blocked_user_id) {
    try {
      const stmt = this.db.prepare(`
        INSERT OR IGNORE INTO blocked_matches (user_id, blocked_user_id, created_at) VALUES (?, ?, ?)
      `);
      stmt.run(user_id, blocked_user_id, new Date().toISOString());
    } catch (e) {
      console.error('Block user error:', e);
    }
  }

  report_user(reporter_id, reported_id, reason) {
    try {
      const stmt = this.db.prepare(`
        INSERT INTO reports (reporter_id, reported_id, reason, created_at) VALUES (?, ?, ?, ?)
      `);
      stmt.run(reporter_id, reported_id, reason || '', new Date().toISOString());
    } catch (e) {
      console.error('Report user error:', e);
    }
  }

  add_pending_like(liker_id, liked_id) {
    try {
      const stmt = this.db.prepare(`
        INSERT OR IGNORE INTO pending_likes (liker_id, liked_id, created_at) VALUES (?, ?, ?)
      `);
      stmt.run(liker_id, liked_id, new Date().toISOString());
    } catch (e) {
      console.error('Pending like error:', e);
    }
  }

  pending_like_exists(liker_id, liked_id) {
    const row = this.db.prepare('SELECT 1 FROM pending_likes WHERE liker_id = ? AND liked_id = ?').get(liker_id, liked_id);
    return !!row;
  }

  remove_pending_like(liker_id, liked_id) {
    try {
      const stmt = this.db.prepare(`
        DELETE FROM pending_likes WHERE (liker_id = ? AND liked_id = ?) OR (liker_id = ? AND liked_id = ?)
      `);
      stmt.run(liker_id, liked_id, liked_id, liker_id);
    } catch (e) {
      console.error('Remove pending like error:', e);
    }
  }

  get_next_profile(user_id) {
    const row = this.db.prepare(`
      SELECT * FROM users
      WHERE user_id != ?
        AND (current_partner_id IS NULL OR current_partner_id = 0)
        AND user_id NOT IN (SELECT skipped_user_id FROM skips WHERE user_id = ?)
        AND user_id NOT IN (SELECT reported_id FROM reports WHERE reporter_id = ?)
        AND user_id NOT IN (SELECT blocked_user_id FROM blocked_matches WHERE user_id = ?)
        AND user_id NOT IN (SELECT blocked_user_id FROM blocked_matches WHERE blocked_user_id = ?)
      ORDER BY RANDOM()
      LIMIT 1
    `).get(user_id, user_id, user_id, user_id, user_id);

    if (!row) return null;
    return {
      user_id: row.user_id,
      name: row.name,
      age: row.age,
      gender: row.gender,
      bio: row.bio,
      created_at: row.created_at,
      is_admin: !!row.is_admin,
      photo_id: row.photo_id || null
    };
  }

  get_current_partner(user_id) {
    const row = this.db.prepare('SELECT current_partner_id FROM users WHERE user_id = ?').get(user_id);
    return row ? row.current_partner_id : null;
  }

  get_stats() {
    const total_users = this.db.prepare('SELECT COUNT(*) as c FROM users').get().c;
    const active_chats = this.db.prepare('SELECT COUNT(*) as c FROM active_chats WHERE is_active = 1').get().c;
    const total_reports = this.db.prepare('SELECT COUNT(*) as c FROM reports').get().c;
    return { total_users, active_chats, total_reports };
  }
}

const db = new DatabaseManager(DB_NAME);

// ---- Keyboards / Markup helpers ----
function createGenderKeyboard() {
  return Markup.inlineKeyboard([
    [Markup.button.callback('👨 Male', 'gender_male')],
    [Markup.button.callback('👩 Female', 'gender_female')],
    [Markup.button.callback('⚧️ Other', 'gender_other')]
  ]);
}
function createProfileKeyboard() {
  return Markup.inlineKeyboard([
    [Markup.button.callback('✏️ Edit Name', 'edit_name')],
    [Markup.button.callback('🎂 Edit Age', 'edit_age')],
    [Markup.button.callback('⚧️ Edit Gender', 'edit_gender')],
    [Markup.button.callback('📝 Edit Bio', 'edit_bio')],
    [Markup.button.callback('🖼️ Edit Photo', 'edit_photo')]
  ]);
}
function createAdminKeyboard() {
  return Markup.inlineKeyboard([
    [Markup.button.callback('📊 Statistics', 'admin_stats')],
    [Markup.button.callback('📢 Broadcast', 'admin_broadcast')]
  ]);
}
function likeSkipKeyboard(target_user_id) {
  return Markup.inlineKeyboard([
    [Markup.button.callback('👍 Like', `like_user_${target_user_id}`), Markup.button.callback('⏭️ Skip', `skip_user_${target_user_id}`)]
  ]);
}

// ---- Bot command handlers ----

bot.start(async (ctx) => {
  const userId = ctx.from.id;
  const existing = db.get_user(userId);
  if (existing) {
    await ctx.reply(
      `👋 Welcome back, ${existing.name}!\n\n🔸 Use /find to start searching for a match\n🔸 Use /profile to view your profile\n🔸 Use /help to see all commands`
    );
  } else {
    await ctx.reply("🎉 Welcome to MateFinder!\n\nLet's create your profile. First, please tell me your name:");
    setState(userId, 'editing_name');
  }
});

bot.command('profile', async (ctx) => {
  const userId = ctx.from.id;
  const user = db.get_user(userId);
  if (!user) {
    await ctx.reply('❌ Please start the bot first with /start');
    return;
  }
  const profileText =
    `👤 *Your Profile*\n\n` +
    `📛 Name: ${user.name}\n` +
    `🎂 Age: ${user.age}\n` +
    `⚧️ Gender: ${user.gender}\n` +
    `📝 Bio: ${user.bio}\n\n` +
    `📅 Joined: ${user.created_at.split('T')[0]}`;
  if (user.photo_id) {
    try {
      await ctx.replyWithPhoto(user.photo_id, { caption: profileText, parse_mode: 'Markdown', ...createProfileKeyboard() });
    } catch (e) {
      // If file_id invalid, fallback to text
      await ctx.reply(profileText, { parse_mode: 'Markdown', ...createProfileKeyboard() });
    }
  } else {
    await ctx.reply(profileText, { parse_mode: 'Markdown', ...createProfileKeyboard() });
  }
});

bot.command('edit', async (ctx) => {
  const userId = ctx.from.id;
  const user = db.get_user(userId);
  if (!user) {
    await ctx.reply('❌ Please start the bot first with /start');
    return;
  }
  await ctx.reply('✏️ *Edit Profile*\n\nSelect what you\'d like to edit:', { parse_mode: 'Markdown', ...createProfileKeyboard() });
});

bot.command('find', async (ctx) => {
  const userId = ctx.from.id;
  const user = db.get_user(userId);
  if (!user) {
    await ctx.reply('❌ Please start the bot first with /start');
    return;
  }
  if (db.get_current_partner(userId)) {
    await ctx.reply("❌ You're already in a chat! Use /stop to end it first.");
    return;
  }
  const candidate = db.get_next_profile(userId);
  if (!candidate) {
    await ctx.reply('😔 No profiles to show right now. Please try again later.');
    return;
  }
  updateData(userId, { last_candidate_id: candidate.user_id });
  const cap =
    `📛 Name: ${candidate.name}\n` +
    `🎂 Age: ${candidate.age}\n` +
    `⚧️ Gender: ${candidate.gender}\n` +
    `📝 Bio: ${candidate.bio}\n\n` +
    `Like or skip:`;
  if (candidate.photo_id) {
    try {
      await ctx.replyWithPhoto(candidate.photo_id, { caption: cap, ...likeSkipKeyboard(candidate.user_id) });
    } catch (e) {
      await ctx.reply(cap, likeSkipKeyboard(candidate.user_id));
    }
  } else {
    await ctx.reply(cap, likeSkipKeyboard(candidate.user_id));
  }
});

bot.command('stop', async (ctx) => {
  const userId = ctx.from.id;
  const partnerId = db.get_current_partner(userId);
  if (partnerId) {
    db.end_chat(userId);
    await ctx.reply('✅ Chat ended successfully!');
    try {
      await ctx.telegram.sendMessage(partnerId, '💔 Your chat partner has ended the conversation.\n\nUse /find to search for a new match!');
    } catch (e) {
      console.error('Notify partner (stop) failed:', e);
    }
  } else {
    await ctx.reply("❌ You're not currently in a chat.");
  }
});

bot.command('skip', async (ctx) => {
  const userId = ctx.from.id;
  const partnerId = db.get_current_partner(userId);
  if (partnerId) {
    db.skip_user(userId, partnerId);
    db.end_chat(userId);
    await ctx.reply('⏭️ Skipped current partner. Use /find to search for a new match!');
    try {
      await ctx.telegram.sendMessage(partnerId, '⏭️ Your chat partner has skipped to find someone else.\n\nUse /find to search for a new match!');
    } catch (e) {
      console.error('Notify partner (skip) failed:', e);
    }
  } else {
    await ctx.reply("❌ You're not currently in a chat.");
  }
});

bot.command('report', async (ctx) => {
  const userId = ctx.from.id;
  const partnerId = db.get_current_partner(userId);
  if (partnerId) {
    db.report_user(userId, partnerId, 'Inappropriate behavior');
    db.block_user(userId, partnerId);
    db.end_chat(userId);
    await ctx.reply('🚨 User reported and blocked!\nThe chat has ended and you won\'t match with this user again.\nUse /find to search for a new match.');
  } else {
    await ctx.reply("❌ You're not currently in a chat.");
  }
});

bot.command('help', async (ctx) => {
  const helpText =
    "🤖 *MateFinder Bot Commands*\n\n" +
    "🔸 /start - Start the bot and create profile\n" +
    "🔸 /profile - View your current profile\n" +
    "🔸 /edit - Edit your profile\n" +
    "🔸 /find - Browse profiles and match\n" +
    "🔸 /stop - End current chat\n" +
    "🔸 /skip - Skip current partner in chat or profile\n" +
    "🔸 /report - Report inappropriate behavior\n" +
    "🔸 /cancel - Cancel any ongoing action\n" +
    "🔸 /help - Show this help message";
  await ctx.reply(helpText, { parse_mode: 'Markdown' });
});

bot.command('cancel', async (ctx) => {
  clearState(ctx.from.id);
  await ctx.reply('❌ Action cancelled.');
});

bot.command('admin', async (ctx) => {
  const userId = ctx.from.id;
  if (userId !== ADMIN_USER_ID) {
    await ctx.reply("❌ You don't have permission to use this command.");
    return;
  }
  const stats = db.get_stats();
  const adminText =
    `🔧 *Admin Panel*\n\n` +
    `👥 Total Users: ${stats.total_users}\n` +
    `💬 Active Chats: ${stats.active_chats}\n` +
    `🚨 Total Reports: ${stats.total_reports}`;
  await ctx.reply(adminText, { parse_mode: 'Markdown', ...createAdminKeyboard() });
});

bot.command('broadcast', async (ctx) => {
  const userId = ctx.from.id;
  if (userId !== ADMIN_USER_ID) {
    await ctx.reply("❌ You don't have permission to use this command.");
    return;
  }
  await ctx.reply('📢 Please send the message you want to broadcast to all users. (Not implemented)');
});

// ---- Callback query handlers ----

bot.on('callback_query', async (ctx) => {
  const data = ctx.callbackQuery.data;
  const fromId = ctx.from.id;

  // Gender selection
  if (data && data.startsWith('gender_')) {
    const genderMap = {
      gender_male: 'Male',
      gender_female: 'Female',
      gender_other: 'Other'
    };
    const gender = genderMap[data];
    if (gender) {
      updateData(fromId, { gender });
      try {
        await ctx.editMessageText(`✅ Gender set to: ${gender}\n\nNow, please enter a short bio about yourself:`);
      } catch (e) {
        // ignore
      }
      setState(fromId, 'editing_bio');
      await ctx.answerCbQuery();
      return;
    }
  }

  // Edit selection
  if (data && data.startsWith('edit_')) {
    const editMap = {
      edit_name: ['editing_name', 'Please enter your new name:'],
      edit_age: ['editing_age', 'Please enter your new age:'],
      edit_gender: ['editing_gender', 'Please select your gender:'],
      edit_bio: ['editing_bio', 'Please enter your new bio:'],
      edit_photo: ['editing_photo', 'Send your new profile picture or /skip to remove.']
    };
    if (editMap[data]) {
      const [stateName, messageText] = editMap[data];
      if (data === 'edit_gender') {
        try {
          await ctx.editMessageText(messageText, createGenderKeyboard());
        } catch (e) {
          await ctx.reply(messageText, createGenderKeyboard());
        }
      } else {
        try {
          await ctx.editMessageText(messageText);
        } catch (e) {
          await ctx.reply(messageText);
        }
      }
      setState(fromId, stateName);
      await ctx.answerCbQuery();
      return;
    }
  }

  // Admin keyboard (not fully implemented but placeholder)
  if (data === 'admin_stats') {
    if (fromId !== ADMIN_USER_ID) {
      await ctx.answerCbQuery("No permission", { show_alert: true });
      return;
    }
    const stats = db.get_stats();
    await ctx.reply(`👥 ${stats.total_users} users, ${stats.active_chats} active chats, ${stats.total_reports} reports`);
    await ctx.answerCbQuery();
    return;
  }

  if (data === 'admin_broadcast') {
    if (fromId !== ADMIN_USER_ID) {
      await ctx.answerCbQuery("No permission", { show_alert: true });
      return;
    }
    await ctx.reply('📢 Broadcast: (not implemented)');
    await ctx.answerCbQuery();
    return;
  }

  // Like / Skip callbacks: pattern like "like_user_123" or "skip_user_123"
  const m = data && data.match(/^(like|skip)_user_(\d+)$/);
  if (m) {
    const action = m[1];
    const targetId = parseInt(m[2], 10);
    const myId = fromId;

    const candidate = db.get_user(targetId);
    const me = db.get_user(myId);
    if (!candidate || !me) {
      try { await ctx.answerCbQuery('Invalid candidate.'); } catch(e){}
      return;
    }

    if (action === 'skip') {
      db.skip_user(myId, targetId);
      try { await ctx.answerCbQuery('Skipped.'); } catch(e){}
      // try to remove buttons on the original message
      try {
        await ctx.editMessageReplyMarkup();
      } catch (e) { /* ignore */ }

      // show next
      const next = db.get_next_profile(myId);
      if (next) {
        updateData(myId, { last_candidate_id: next.user_id });
        const cap =
          `📛 Name: ${next.name}\n` +
          `🎂 Age: ${next.age}\n` +
          `⚧️ Gender: ${next.gender}\n` +
          `📝 Bio: ${next.bio}\n\nLike or skip:`;
        if (next.photo_id) {
          try {
            await ctx.replyWithPhoto(next.photo_id, { caption: cap, ...likeSkipKeyboard(next.user_id) });
          } catch (e) {
            await ctx.reply(cap, likeSkipKeyboard(next.user_id));
          }
        } else {
          await ctx.reply(cap, likeSkipKeyboard(next.user_id));
        }
      } else {
        await ctx.reply('No more profiles available now.');
        clearState(myId);
      }
      return;
    }

    if (action === 'like') {
      if (db.pending_like_exists(targetId, myId)) {
        // mutual like -> set in chat
        db.remove_pending_like(myId, targetId);
        db.set_in_chat(myId, targetId);
        try { await ctx.answerCbQuery("🎉 It's a Match! Say hello!"); } catch(e){}
        try {
          await bot.telegram.sendMessage(targetId,
            "🎉 It's a Match! You both liked each other. Say hello!\n" +
            "💬 Send any message to chat.\n" +
            "🚫 Use /stop to end the chat\n" +
            "⏭️ Use /skip to find a new partner\n" +
            "🚨 Use /report to report inappropriate behavior"
          );
        } catch (e) {
          console.error('Notify match failed:', e);
        }
        try {
          await ctx.editMessageReplyMarkup();
        } catch (e) { /* ignore */ }
      } else {
        db.add_pending_like(myId, targetId);
        try { await ctx.answerCbQuery("Liked! We'll notify you if it's a match."); } catch(e){}
        try { await ctx.editMessageReplyMarkup(); } catch(e){}
        // show next
        const next = db.get_next_profile(myId);
        if (next) {
          updateData(myId, { last_candidate_id: next.user_id });
          const cap =
            `📛 Name: ${next.name}\n` +
            `🎂 Age: ${next.age}\n` +
            `⚧️ Gender: ${next.gender}\n` +
            `📝 Bio: ${next.bio}\n\nLike or skip:`;
          if (next.photo_id) {
            try {
              await ctx.replyWithPhoto(next.photo_id, { caption: cap, ...likeSkipKeyboard(next.user_id) });
            } catch (e) {
              await ctx.reply(cap, likeSkipKeyboard(next.user_id));
            }
          } else {
            await ctx.reply(cap, likeSkipKeyboard(next.user_id));
          }
        } else {
          await ctx.reply('No more profiles available now.');
          clearState(myId);
        }
      }
      return;
    }
  }

  // fallback: answer callback to avoid "loading"
  try { await ctx.answerCbQuery(); } catch(e){}
});

// ---- Message handlers / FSM handlers ----

bot.on('message', async (ctx, next) => {
  // photo messages handled separately below, but we still allow FSM to catch them
  const userId = ctx.from.id;
  const state = getState(userId);

  // If message is a command, pass through to other handlers
  if (ctx.message.text && ctx.message.text.startsWith('/')) {
    return next();
  }

  // FSM-based routing
  if (state === 'editing_name') {
    const name = (ctx.message.text || '').trim();
    if (!name || name.length < 2 || name.length > 50) {
      await ctx.reply('❌ Name must be between 2 and 50 characters. Please try again:');
      return;
    }
    const existing = db.get_user(userId);
    if (existing) {
      db.update_user_field(userId, 'name', name);
      await ctx.reply(`✅ Name updated to: ${name}`);
      clearState(userId);
    } else {
      updateData(userId, { name });
      await ctx.reply('✅ Name set. Please enter your age (18-100):');
      setState(userId, 'editing_age');
    }
    return;
  }

  if (state === 'editing_age') {
    const text = (ctx.message.text || '').trim();
    const age = parseInt(text, 10);
    if (isNaN(age) || age < 18 || age > 100) {
      await ctx.reply('❌ Please enter a valid number for age (18-100):');
      return;
    }
    const existing = db.get_user(userId);
    if (existing) {
      db.update_user_field(userId, 'age', age);
      await ctx.reply(`✅ Age updated to: ${age}`);
      clearState(userId);
    } else {
      updateData(userId, { age });
      await ctx.reply('✅ Age set. Please select your gender:', createGenderKeyboard());
      setState(userId, 'editing_gender');
    }
    return;
  }

  if (state === 'editing_bio') {
    const bio = (ctx.message.text || '').trim();
    if (bio.length > 500) {
      await ctx.reply('❌ Bio must be less than 500 characters. Please try again:');
      return;
    }
    const existing = db.get_user(userId);
    if (existing) {
      db.update_user_field(userId, 'bio', bio);
      await ctx.reply('✅ Bio updated!');
      clearState(userId);
    } else {
      updateData(userId, { bio });
      await ctx.reply('Would you like to add a profile picture?\nSend me a photo.');
      setState(userId, 'editing_photo');
    }
    return;
  }

  // For editing_photo: user may send '/skip' text to skip photo (handled here)
  if (state === 'editing_photo') {
    const text = (ctx.message.text || '').trim().toLowerCase();
    if (text === '/skip') {
      const data = getData(userId);
      const created = db.create_user(userId, data.name, data.age, data.gender, data.bio, null);
      if (created) {
        await ctx.reply(
          `🎉 Profile created successfully!\n\n📛 Name: ${data.name}\n🎂 Age: ${data.age}\n⚧️ Gender: ${data.gender}\n📝 Bio: ${data.bio}\n\n🔍 Use /find to start looking for matches!\n✏️ Use /edit to modify your profile anytime.`
        );
      } else {
        await ctx.reply('❌ Failed to create profile. Please try again with /start');
      }
      clearState(userId);
      return;
    }
    // else it's not a skip - fallthrough to next handlers (maybe invalid text)
    await ctx.reply('❌ Please send a photo or type /skip.');
    return;
  }

  // If no FSM state, treat normal chat message: relay to partner if in chat
  if (!state) {
    const partnerId = db.get_current_partner(userId);
    if (partnerId) {
      try {
        await bot.telegram.sendMessage(partnerId, `💬 ${ctx.message.text || ''}`);
      } catch (e) {
        console.error('Relay msg fail:', e);
        await ctx.reply("❌ Failed to send message. Your partner may have left the chat.");
      }
    } else {
      await ctx.reply("❌ You're not currently in a chat.\n🔍 Use /find to search for a match!");
    }
    return;
  }

  // fallback: pass to next middleware
  return next();
});

// Photo handlers
bot.on('photo', async (ctx) => {
  const userId = ctx.from.id;
  const state = getState(userId);
  const photos = ctx.message.photo || [];
  const fileId = photos.length ? photos[photos.length - 1].file_id : null;

  if (state === 'editing_photo') {
    // If user is creating profile
    updateData(userId, { photo_id: fileId });
    const data = getData(userId);
    const created = db.create_user(userId, data.name, data.age, data.gender, data.bio, fileId);
    if (created) {
      const profileText =
        `🎉 Profile created successfully!\n\n` +
        `📛 Name: ${data.name}\n` +
        `🎂 Age: ${data.age}\n` +
        `⚧️ Gender: ${data.gender}\n` +
        `📝 Bio: ${data.bio}\n` +
        `🖼️ Photo: [see above]\n\n` +
        `🔍 Use /find to start looking for matches!\n✏️ Use /edit to modify your profile anytime.`;
      await ctx.replyWithPhoto(fileId, { caption: profileText });
    } else {
      await ctx.reply('❌ Failed to create profile. Please try again with /start');
    }
    clearState(userId);
    return;
  }

  // If editing existing profile's photo
  if (state === 'editing_photo' && db.get_user(userId)) {
    db.update_user_field(userId, 'photo_id', fileId);
    await ctx.reply('✅ Photo updated!');
    clearState(userId);
    return;
  }

  // If user is in chat, forward photo to partner
  const partnerId = db.get_current_partner(userId);
  if (partnerId) {
    try {
      await bot.telegram.sendPhoto(partnerId, fileId, { caption: '(Photo from your chat partner)' });
    } catch (e) {
      console.error('Photo relay fail:', e);
      await ctx.reply('❌ Failed to forward photo.');
    }
  } else {
    await ctx.reply("❌ You're not currently in a chat.\n🔍 Use /find to search for a match!");
  }
});

// Additional handlers to support editing photo removal (text '/skip' when in editing_photo)
// We already handle '/skip' in message FSM above.

// Start polling
(async () => {
  try {
    console.log('Starting MateFinder bot (Telegraf)...');
    await bot.launch();
    console.log('Bot started (polling).');
  } catch (e) {
    console.error('Failed to launch bot', e);
    process.exit(1);
  }
})();

// Graceful shutdown
process.once('SIGINT', () => {
  console.log('SIGINT received: stopping bot');
  bot.stop('SIGINT');
});
process.once('SIGTERM', () => {
  console.log('SIGTERM received: stopping bot');
  bot.stop('SIGTERM');
});
