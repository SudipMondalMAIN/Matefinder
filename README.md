# MateFinder Telegram Dating Bot 💘

An anonymous, gender-based dating bot for Telegram.  this bot matches **two real users** for private chat based on their profile preferences. No usernames, no identity — just anonymous conversation.

---

## ✅ Features

| Command     | Description |
|-------------|-------------|
| `/start`    | Set up your profile (gender & preference) |
| `/profile`  | View your current profile |
| `/edit`     | Edit your gender and preference |
| `/find`     | Search for a match (waits until someone is found) |
| `/skip`     | Leave current chat and find someone new |
| `/stop`     | End the current chat |
| `/report`   | Report the user you’re chatting with |
| `/cancel`   | Cancel any ongoing action |
| `/help`     | Show available commands |

---

## 🛠 Tech Stack

- **Python 3.11+**
- `python-telegram-bot==21.1`
- `dotenv` (optional for token storage)
- Fully asynchronous (`asyncio`)
- Simple in-memory queue (can be upgraded to database)

---

## 🧩 How It Works

1. User starts with `/start` and sets gender & who they want to talk to (preference).
2. On `/find`, user enters the matchmaking queue.
3. When a match is found based on mutual preferences, both are connected anonymously.
4. Messages/stickers are relayed through the bot.
5. Use `/skip`, `/stop`, or `/report` anytime to exit.

---

## 🐍 Installation

### 1. Clone the project

```bash
git clone https://github.com/your-username/matefinder-telegram.git
cd matefinder-telegram
