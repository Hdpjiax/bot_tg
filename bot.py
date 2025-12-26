import logging
import os
import threading
from flask import Flask
from supabase import create_client, Client
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters

# --- CONFIGURACIÓN WEB PARA RENDER ---
app_web = Flask('')
@app_web.route('/')
def home(): return "Bot Online 🚀"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

# --- CONFIGURACIÓN DE APIS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO)

# --- FUNCIONES DE USUARIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mi Historial"), KeyboardButton("🏦 Datos de Pago")],
        [KeyboardButton("🖼 Enviar QR")]
    ], resize_keyboard=True)
    await update.message.reply_text("✈️ **Gestor de Vuelos** activo.\nUsa el menú para navegar:", reply_markup=teclado, parse_mode="Markdown")

async def mostrar_historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    res = supabase.table("cotizaciones").select("*").eq("user_id", uid).execute()
    
    if not res.data:
        await update.message.reply_text("📭 No tienes registros.")
        return

    for v in res.data:
        info = (f"🆔 **ID:** `{v['id']}`\n"
                f"✈️ **Pedido:** {v['pedido_completo']}\n"
                f"💰 **Monto:** {v['monto']}\n"
                f"📍 **Estado:** {v['estado']}")
        # Botón para que el usuario pueda borrar su propio registro
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Borrar este vuelo", callback_data=f"del_{v['id']}")]])
        await update.message.reply_text(info, reply_markup=btn, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    uid = update.effective_user.id
    user_data = context.user_data

    # Menú de Usuario
    if texto == "📝 Datos de vuelo":
        user_data["esperando"] = "texto_vuelo"
        await update.message.reply_text("Escribe los detalles de tu vuelo (Origen, Destino, Fecha):")
    elif texto == "📜 Mi Historial":
        await mostrar_historial(update, context)
    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")
    elif texto == "🖼 Enviar QR":
        await update.message.reply_text("Envía la imagen de tu código QR:")
    
    # Lógica de estados (Usuario y Admin)
    elif user_data.get("esperando") == "texto_vuelo":
        user_data["temp_text"] = texto
        await update.message.reply_text("✅ Datos guardados. Ahora envía la **foto** o comprobante para finalizar.")
    
    elif user_data.get("esperando") == "admin_id_cotizar":
        user_data["cotizar_id"] = texto
        user_data["esperando"] = "admin_monto"
        await update.message.reply_text(f"ID {texto} seleccionado. ¿Cuál es el monto?")
    
    elif user_data.get("esperando") == "admin_monto":
        v_id = user_data["cotizar_id"]
        supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", v_id).execute()
        # Notificar al usuario
        v = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
        await context.bot.send_message(v.data["user_id"], f"💰 Tu vuelo ID {v_id} ha sido cotizado: **{texto}**", parse_mode="Markdown")
        await update.message.reply_text(f"✅ Cotización enviada al usuario.")
        user_data.clear()

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    uid = update.effective_user.id
    user_name = f"@{update.effective_user.username}" or update.effective_user.first_name
    file_id = update.message.photo[-1].file_id

    # Si el usuario mandó texto antes, es un vuelo nuevo. Si no, es solo comprobante.
    detalles = context.user_data.get("temp_text", "comprobante")
    estado = "Pagado (Revisión)" if detalles == "comprobante" else "Esperando Pago"

    res = supabase.table("cotizaciones").insert({
        "user_id": uid, "username": user_name,
        "pedido_completo": detalles, "monto": "Pendiente", "estado": estado
    }).execute()
    
    v_id = res.data[0]['id']
    await context.bot.send_photo(ADMIN_CHAT_ID, file_id, 
                               caption=f"🔔 **NUEVA ACCIÓN**\nID: {v_id}\nUser: {user_name}\nAcción: {detalles}")
    await update.message.reply_text(f"✅ Registrado con ID: {v_id}\nEstado: {estado}")
    context.user_data.clear()

# --- FUNCIONES DE ADMINISTRACIÓN ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID: return
    botones = [
        [InlineKeyboardButton("💰 Cotizar", callback_data="admin_cotizar"),
         InlineKeyboardButton("✅ Confirmar Pago", callback_data="admin_confirmar")]
    ]
    await update.message.reply_text("🛠 **Panel de Administrador**", reply_markup=InlineKeyboardMarkup(botones))

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id

    # Borrado desde el historial
    if query.data.startswith("del_"):
        v_id = query.data.split("_")[1]
        supabase.table("cotizaciones").delete().eq("id", v_id).execute()
        await query.edit_message_text(f"🗑️ Registro ID {v_id} eliminado con éxito.")

    # Acciones de Admin
    if uid == ADMIN_CHAT_ID:
        if query.data == "admin_cotizar":
            context.user_data["esperando"] = "admin_id_cotizar"
            await query.message.reply_text("Escribe el **ID** del vuelo a cotizar:")
        
        elif query.data == "admin_confirmar":
            context.user_data["esperando"] = "admin_id_confirmar"
            await query.message.reply_text("Escribe el **ID** para marcar como PAGADO:")

    # Lógica de confirmación rápida (si el admin escribe el ID después de dar clic)
    if context.user_data.get("esperando") == "admin_id_confirmar":
        # Esta parte se maneja mejor capturando el siguiente mensaje de texto en handle_text
        pass

# Modificación en handle_text para completar Confirmar Pago
# (Añadir esto dentro de handle_text arriba)
# elif user_data.get("esperando") == "admin_id_confirmar":
#    v_id = texto
#    supabase.table("cotizaciones").update({"estado": "✅ Pagado / Ticket en Proceso"}).eq("id", v_id).execute()
#    ... (notificar usuario)

if __name__ == "__main__":
    threading.Thread(target=run_server).start() # Keep-alive
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Bot en marcha...")
    app.run_polling(drop_pending_updates=True)
