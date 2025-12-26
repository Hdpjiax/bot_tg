import logging
import os
import threading
import time
from flask import Flask
from supabase import create_client, Client
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)

# --- 1. CONFIGURACIÓN DE SERVIDOR WEB (KEEP-ALIVE) ---
# Esto responde a Cron-job.org para que Render no apague el bot
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is Live and Healthy! 🚀"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_server)
    t.start()

# --- 2. VARIABLES DE ENTORNO Y CONEXIÓN ---
# Configura estas variables en el panel de Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273  # Tu ID de administrador
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO)

# --- 3. FUNCIONES DE USUARIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mi Historial"), KeyboardButton("🏦 Datos de Pago")]
    ], resize_keyboard=True)
    
    mensaje = (
        "✈️ **¡Bienvenido al Gestor de Vuelos!**\n\n"
        "1. Toca '📝 Datos de vuelo' y escribe los detalles.\n"
        "2. Luego envía la foto para confirmar.\n"
        "3. Consulta tus estados en '📜 Mi Historial'."
    )
    await update.message.reply_text(mensaje, reply_markup=teclado, parse_mode="Markdown")

async def mostrar_historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    res = supabase.table("cotizaciones").select("*").eq("user_id", uid).execute()
    
    if not res.data:
        await update.message.reply_text("📭 No tienes registros aún.")
        return

    await update.message.reply_text("📋 **Tus Vuelos Registrados:**", parse_mode="Markdown")
    for v in res.data:
        estado = v.get('estado', 'Esperando Pago')
        emoji = "⏳" if "Esperando" in estado else "✅" if "Pagado" in estado else "🎫"
        
        info = (f"🆔 **ID:** `{v['id']}`\n"
                f"✈️ **Detalles:** {v['pedido_completo']}\n"
                f"💰 **Monto:** {v['monto']}\n"
                f"{emoji} **Estado:** {estado}")
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Eliminar", callback_data=f"del_{v['id']}")]])
        await update.message.reply_text(info, reply_markup=btn, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    uid = update.effective_user.id

    if texto == "📝 Datos de vuelo":
        context.user_data["esperando"] = "texto_vuelo"
        await update.message.reply_text("Escribe Origen, Destino y Fecha:")
    elif texto == "📜 Mi Historial":
        await mostrar_historial(update, context)
    elif texto == "📸 Enviar Pago":
        await update.message.reply_text("Envía la imagen de tu comprobante:")
    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")
    elif context.user_data.get("esperando") == "texto_vuelo":
        context.user_data["temp_text"] = texto
        await update.message.reply_text("✅ Recibido. Ahora envía la **foto** para guardar todo junto.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_name = f"@{update.effective_user.username}" or update.effective_user.first_name
    
    if not update.message.photo: return
    file_id = update.message.photo[-1].file_id

    # Si no hay texto guardado, se asume que es comprobante de pago [cite: 2025-12-24]
    detalles = context.user_data.get("temp_text", "comprobante")
    estado_inicial = "Pagado (Revisión)" if detalles == "comprobante" else "Esperando Pago"

    try:
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": user_name,
            "pedido_completo": detalles, "monto": "Pendiente", "estado": estado_inicial
        }).execute()
        
        v_id = res.data[0]['id']
        # Avisar al Admin
        await context.bot.send_photo(ADMIN_CHAT_ID, file_id, 
                                   caption=f"🔔 **NUEVO ID: {v_id}**\n👤 {user_name}\n📝 {detalles}\n📍 {estado_inicial}")
        
        await update.message.reply_text(f"✅ Registrado con ID: {v_id}\n📍 Estado: {estado_inicial}")
        context.user_data.clear()
    except Exception as e:
        logging.error(f"Error Supabase: {e}")

# --- 4. FUNCIONES DE ADMIN ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID: return
    botones = [
        [InlineKeyboardButton("💰 Cotizar", callback_data="cotizar_mode")],
        [InlineKeyboardButton("✅ Confirmar Pago", callback_data="confirm_mode")]
    ]
    await update.message.reply_text("🛠 **Panel Admin**", reply_markup=InlineKeyboardMarkup(botones))

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("del_"):
        v_id = query.data.split("_")[1]
        supabase.table("cotizaciones").delete().eq("id", v_id).execute()
        await query.edit_message_text(f"🗑️ Registro ID {v_id} eliminado.")

# --- 5. BLOQUE PRINCIPAL ---

if __name__ == "__main__":
    # Iniciar servidor para que Cron-job mantenga vivo el bot
    keep_alive() 
    
    # Iniciar aplicación
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Registro de Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Arrancar limpiando conflictos anteriores
    print("Bot activo y listo...")
    app.run_polling(drop_pending_updates=True)
