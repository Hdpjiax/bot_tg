import os
from datetime import datetime, timedelta
from flask import Flask, request, render_template, redirect, url_for, flash
from supabase import create_client, Client
from telegram import Bot, Update, InputMediaPhoto
from telegram.ext import Dispatcher, CallbackContext, MessageHandler, CommandHandler, CallbackQueryHandler, filters

# ============ CONFIG ============

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secreto-x")  # algo difícil de adivinar
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "7721918273"))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "bf3145e6595577f099e00638d96e4405b24bb0cd17f6908d34b065943b97dd27")

# Dispatcher síncrono para manejar updates dentro de Flask
dispatcher = Dispatcher(bot, update_queue=None, workers=0, use_context=True)

# ============ AQUÍ PEGAS TODA TU LÓGICA DEL DASHBOARD ============
# (las rutas /, /por-cotizar, /accion/cotizar, etc. usando bot.send_message / send_media_group)
# -----------------------------------------------------------------


# ============ HANDLERS DEL BOT (recortar tu bot.py) ============

def start(update: Update, context: CallbackContext):
    # versión síncrona de tu /start
    ...

def handle_text(update: Update, context: CallbackContext):
    ...

def handle_media(update: Update, context: CallbackContext):
    ...

def callbacks(update: Update, context: CallbackContext):
    ...

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CallbackQueryHandler(callbacks))
dispatcher.add_handler(MessageHandler(filters.PHOTO, handle_media))
dispatcher.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# ============ ENDPOINT WEBHOOK ============

@app.route(f"/telegram/{WEBHOOK_SECRET}", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

# ============ MAIN LOCAL ============

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=True)
