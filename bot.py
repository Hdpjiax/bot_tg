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
)

# --- 1. CONFIGURACIÓN WEB (KEEP-ALIVE PARA RENDER) ---
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot Online 🚀"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

# --- 2. CONFIGURACIÓN DE APIS Y VARIABLES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

# --- 3. FUNCIONES DE USUARIO E HISTORIAL ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mi Historial"), KeyboardButton("🏦 Datos de Pago")]
    ], resize_keyboard=True)
    await update.message.reply_text("✈️ **Gestor de Vuelos** activo.\nUsa el menú para navegar:", reply_markup=teclado, parse_mode="Markdown")

async def mostrar_historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    res = supabase.table("cotizaciones").select("*").eq("user_id", uid).execute()
    
    if not res.data:
        await update.message.reply_text("📭 No tienes registros registrados.")
        return

    for v in res.data:
        info = (f"🆔 **ID:** `{v['id']}`\n"
                f"✈️ **Pedido:** {v['pedido_completo']}\n"
                f"💰 **Monto:** {v['monto']}\n"
                f"📍 **Estado:** {v['estado']}")
        # Botón de borrado para el historial
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Borrar este vuelo", callback_data=f"del_{v['id']}")]])
        await update.message.reply_text(info, reply_markup=btn, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    uid = update.effective_user.id
    user_data = context.user_data

    if texto == "📝 Datos de vuelo":
        user_data["esperando"] = "texto_vuelo"
        await update.message.reply_text("Escribe los detalles de tu vuelo (Origen, Destino, Fecha):")
    elif texto == "📜 Mi Historial":
        await mostrar_historial(update, context)
    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")
    
    # --- Lógica de Admin (Cotizar y Confirmar) ---
    elif user_data.get("esperando") == "admin_id_cotizar":
        user_data["cotizar_id"] = texto
        user_data["esperando"] = "admin_monto"
        await update.message.reply_text(f"ID {texto} seleccionado. ¿Cuál es el monto de la cotización?")
    
    elif user_data.get("esperando") == "admin_monto":
        v_id = user_data["cotizar_id"]
        try:
            supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", v_id).execute()
            v_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            await context.bot.send_message(v_res.data["user_id"], f"💰 Tu vuelo ID {v_id} ha sido cotizado: **{texto}**", parse_mode="Markdown")
            await update.message.reply_text("✅ Cotización enviada con éxito.")
            user_data.clear()
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    elif user_data.get("esperando") == "admin_id_confirmar":
        try:
            supabase.table("cotizaciones").update({"estado": "✅ Pagado"}).eq("id", texto).execute()
            v_res = supabase.table("cotizaciones").select("user_id").eq("id", texto).single().execute()
            await context.bot.send_message(v_res.data["user_id"], f"✅ Tu pago para el ID {texto} ha sido confirmado.")
            await update.message.reply_text(f"✅ Vuelo {texto} marcado como pagado.")
            user_data.clear()
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    elif user_data.get("esperando") == "texto_vuelo":
        user_data["temp_text"] = texto
        await update.message.reply_text("✅ Detalles guardados. Ahora envía la **foto** para completar el registro.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    uid = update.effective_user.id
    user_name = f"@{update.effective_user.username}" or update.effective_user.first_name
    file_id = update.message.photo[-1].file_id

    detalles = context.user_data.get("temp_text", "comprobante")
    estado = "Pagado (Revisión)" if detalles == "comprobante" else "Esperando Pago"

    try:
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": user_name,
            "pedido_completo": detalles, "monto": "Pendiente", "estado": estado
        }).execute()
        
        v_id = res.data[0]['id']
        await context.bot.send_photo(ADMIN_CHAT_ID, file_id, 
                                   caption=f"🔔 **NUEVO REGISTRO**\nID: {v_id}\nUsuario: {user_name}\nDetalles: {detalles}")
        await update.message.reply_text(f"✅ Registrado con ID: {v_id}\nEstado: {estado}")
        context.user_data.clear()
    except Exception as e:
        logging.error(f"Error Supabase: {e}")

# --- 4. FUNCIONES DE ADMINISTRACIÓN ---

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
    
    if query.data.startswith("del_"):
        v_id = query.data.split("_")[1]
        try:
            supabase.table("cotizaciones").delete().eq("id", v_id).execute()
            await query.edit_message_text(f"🗑️ Registro ID {v_id} eliminado de la base de datos.")
        except Exception as e:
            await query.edit_message_text(f"❌ Error al eliminar: {e}")

    if update.effective_user.id == ADMIN_CHAT_ID:
        if query.data == "admin_cotizar":
            context.user_data["esperando"] = "admin_id_cotizar"
            await query.message.reply_text("Escribe el **ID** del vuelo que deseas cotizar:")
        elif query.data == "admin_confirmar":
            context.user_data["esperando"] = "admin_id_confirmar"
            await query.message.reply_text("Escribe el **ID** del vuelo para marcarlo como PAGADO:")

# --- 5. ARRANQUE DEL BOT ---

if __name__ == "__main__":
    # Iniciar servidor para Cron-job
    threading.Thread(target=run_server).start() 
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Registro de Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # drop_pending_updates evita errores de conflicto al reiniciar
    print("Bot activo...")
    app.run_polling(drop_pending_updates=True)
