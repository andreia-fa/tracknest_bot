# 🏡 TrackNest Bot

**TrackNest** is a **Telegram-based home inventory assistant** built in Python.

It helps you manage and track household items through a conversational chat interface — ideal for small households, roommates, or solo users who want a lightweight, on-demand inventory tool.

---

## 🚀 Features (Planned & In Progress)

- ✅ `/start` – Welcome and setup help
- ✅ `/add_item <name> <qty>` – Add or update an inventory item
- ✅ `/list_items` – Show current inventory list
- ⏳ `/remove_item <name>` – Remove an item from inventory
- ⏳ `/update_item <name> <qty>` – Set a new quantity
- 🧠 Future: Persistent storage with SQLite
- 🧠 Future: Low-stock alerts, category tagging, expiration tracking

---

## 💡 Project Motivation

This project was born from a desire to **learn by building**.

TrackNest combines:
- Real-world automation
- Clean coding principles
- Telegram’s simplicity

...to create something genuinely useful and extendable.

Although it's currently a **proof of concept**, it’s designed with a **scalable architecture** from the start — making it easy to maintain, expand, and learn from as it grows.

---

## 🧰 Built With

- 🐍 [Python 3.10+](https://www.python.org/)
- 🤖 [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- 🔐 `python-dotenv` for secret management
- 📁 Clean architecture (handlers, services, utils)
- ⚙️ `config.properties` for project settings
- 📄 MIT License

---

## ⚙️ Setup Instructions

### 1. Clone the repo
```bash
git clone https://github.com/andreia-fa/tracknest_bot.git
cd tracknest_bot

# Create a virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the bot
python bot/main.py

