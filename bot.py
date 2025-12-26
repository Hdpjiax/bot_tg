import logging
import os
import threading
from flask import Flask
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# --- CONFIGURACIÓN DE RED (KEEP-ALIVE) ---
app_web = Flask('')
@app_web.route('/')
def home(): return "Bot Live!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

def keep_alive():
    threading.Thread(target=run).start()

# --- CONFIGURACIÓN VARIABLES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- BOTONES ---
MENU_PRINCIPAL = ReplyKeyboardMarkup([
    [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
    [KeyboardButton("📜 Mi Historial"), KeyboardButton("🏦 Datos de Pago")]
], resize_keyboard=True)

# --- LÓGICA ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✈️ **Bienvenido al Gestor de Vuelos**", reply_markup=MENU_PRINCIPAL, parse_mode="Markdown")

async def mostrar_historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Consultamos todos los vuelos del usuario
    res = supabase.table("cotizaciones").select("*").eq("user_id", uid).execute()
    
    if not res.data:
        await update.message.reply_text("📭 No tienes vuelos registrados aún.")
        return

    await update.message.reply_text(f"📋 **Tus Vuelos Registrados:**", parse_mode="Markdown")
    
    for v in res.data:
        # Definir emojis según el estado
        estado = v.get('estado', 'Esperando Pago')
        emoji = "⏳" if "Esperando" in estado else "✅" if "Pagado" in estado else "🎫"
        
        info = (f"🆔 **ID:** `{v['id']}`\n"
                f"✈️ **Detalles:** {v['pedido_completo']}\n"
                f"💰 **Monto:** {v['monto']}\n"
                f"{emoji} **Estado:** {estado}")
        
        # Botón para borrar este registro específico
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Eliminar este vuelo", callback_data=f"del_{v['id']}")]])
        await update.message.reply_text(info, reply_markup=btn, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    uid = update.effective_user.id

    if texto == "📝 Datos de vuelo":
        context.user_data["esperando"] = "texto_vuelo"
        await update.message.reply_text("Por favor, escribe el Origen, Destino y Fecha:")
    elif texto == "📜 Mi Historial":
        await mostrar_historial(update, context)
    elif texto == "📸 Enviar Pago":
        await update.message.reply_text("Envía la captura de tu comprobante de pago:")
    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")
    elif context.user_data.get("esperando") == "texto_vuelo":
        context.user_data["temp_text"] = texto
        await update.message.reply_text("✅ Detalles guardados. Ahora **envía una imagen** (referencia o pago) para registrarlo.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_name = f"@{update.effective_user.username}" or update.effective_user.first_name
    
    if not update.message.photo: return
    file_id = update.message.photo[-1].file_id

    # Lógica de guardado: si hay texto previo se usa, si no, se marca como comprobante
    detalles = context.user_data.get("temp_text", "comprobante")
    estado_inicial = "Pagado (Revisión)" if detalles == "comprobante" else "Esperando Pago"

    try:
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": user_name,
            "pedido_completo": detalles, "monto": "Pendiente", "estado": estado_inicial
        }).execute()
        
        v_id = res.data[0]['id']
        await context.bot.send_photo(ADMIN_CHAT_ID, file_id, caption=f"🔔 **NUEVO REGISTRO ID: {v_id}**\n👤 {user_name}\n📝 {detalles}\n📍 Estado: {estado_inicial}")
        await update.message.reply_text(f"✅ Vuelo registrado con éxito.\n🆔 ID: {v_id}\n📍 Estado: {estado_inicial}")
        context.user_data.clear()
    except Exception as e:
        logging.error(f"Error Supabase: {e}")

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("del_"):
        v_id = query.data.split("_")[1]
        try:
            supabase.table("cotizaciones").delete().eq("id", v_id).execute()
            await query.edit_message_text(f"🗑️ El vuelo con ID `{v_id}` ha sido eliminado de la base de datos.", parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Error al borrar: {e}")

if __name__ == "__main__":
    keep_alive() # Inicia el servidor Flask para Cron-job
    
    # Se usa 'app' para evitar el error 'application not defined'
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(CallbackQueryHandler(callbacks))
    
    app.run_polling()
