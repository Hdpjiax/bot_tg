             import logging
import os
import threading
from flask import Flask
from supabase import create_client, Client
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)

# --- SERVIDOR WEB (PARA RENDER) ---
app_web = Flask('')
@app_web.route('/')
def home(): return "Bot Live!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

def keep_alive():
    threading.Thread(target=run).start()

# --- CONFIGURACIÓN ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- BOTONES Y TEXTOS ---
MENU_TECLADO = ReplyKeyboardMarkup([
    [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
    [KeyboardButton("📜 Mi Historial"), KeyboardButton("🏦 Datos de Pago")]
], resize_keyboard=True)

# --- LÓGICA DEL BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✈️ **Panel de Vuelos**\nUsa los botones para gestionar tus solicitudes.", 
                                  reply_markup=MENU_TECLADO, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    texto = update.message.text

    if texto == "📝 Datos de vuelo":
        await update.message.reply_text("Escribe los detalles (Origen, Destino, Fecha):")
        context.user_data["esperando"] = "texto_vuelo"
    elif texto == "📸 Enviar Pago":
        await update.message.reply_text("Adjunta la imagen de tu comprobante:")
    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("BBVA\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")
    elif texto == "📜 Mi Historial":
        await mostrar_historial(update, context)
    elif context.user_data.get("esperando") == "texto_vuelo":
        context.user_data["temp_text"] = texto
        await update.message.reply_text("✅ Texto recibido. Ahora envía la **foto de referencia** para registrarlo.")

async def mostrar_historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    res = supabase.table("cotizaciones").select("*").eq("user_id", uid).execute()
    
    if not res.data:
        await update.message.reply_text("No tienes vuelos registrados.")
        return

    for v in res.data:
        # Definir emoji por estado
        emoji = "⏳" if "Esperando" in v['estado'] else "✅" if "Pagado" in v['estado'] else "🎫"
        msg = (f"🆔 **ID:** {v['id']}\n"
               f"✈️ **Vuelo:** {v['pedido_completo']}\n"
               f"💰 **Monto:** {v['monto']}\n"
               f"{emoji} **Estado:** {v['estado']}")
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Eliminar Vuelo", callback_data=f"del_{v['id']}")]])
        await update.message.reply_text(msg, reply_markup=btn, parse_mode="Markdown")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_name = f"@{update.effective_user.username}" or update.effective_user.first_name
    
    if update.message.photo: file_id = update.message.photo[-1].file_id
    else: return

    # Regla: Si no hay texto previo, es comprobante [cite: 2025-12-24]
    texto_vuelo = context.user_data.get("temp_text", "comprobante")
    estado_ini = "Pagado (Revisión)" if texto_vuelo == "comprobante" else "Esperando Pago"

    try:
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": user_name,
            "pedido_completo": texto_vuelo, "monto": "Pendiente", "estado": estado_ini
        }).execute()
        
        v_id = res.data[0]['id']
        await context.bot.send_photo(ADMIN_CHAT_ID, file_id, 
                                   caption=f"🔔 **NUEVO REGISTRO ID: {v_id}**\n👤 {user_name}\n📝 {texto_vuelo}")
        await update.message.reply_text(f"✅ Registrado con ID: {v_id}. Estado: {estado_ini}")
        context.user_data.clear()
    except Exception as e:
        logging.error(e)

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("del_"):
        v_id = query.data.split("_")[1]
        supabase.table("cotizaciones").delete().eq("id", v_id).execute()
        await query.edit_message_text(f"🗑️ El registro ID {v_id} ha sido eliminado.")

if __name__ == "__main__":
    keep_alive() # Para Cron-job.org
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.run_polling()
