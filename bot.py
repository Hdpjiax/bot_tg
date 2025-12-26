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

# --- 1. CONFIGURACIÓN DEL SERVIDOR (KEEP-ALIVE) ---
app_web = Flask('')
@app_web.route('/')
def home(): return "Bot Activo y Protegido 🚀"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

# --- 2. CONFIGURACIÓN DE APIS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

# --- 3. TECLADOS DIFERENCIADOS ---

def get_user_keyboard():
    # El usuario común NO ve Historial, ni Cotizar, ni QR
    return ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("🏦 Datos de Pago")]
    ], resize_keyboard=True)

def get_admin_inline_keyboard():
    # Solo para el Admin
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Cotizar", callback_data="admin_cotizar"),
         InlineKeyboardButton("✅ Confirmar Pago", callback_data="admin_confirmar")],
        [InlineKeyboardButton("📜 Historial Global", callback_data="admin_historial"),
         InlineKeyboardButton("🖼 Enviar QR", callback_data="admin_qr")]
    ])

# --- 4. FUNCIONES DE LÓGICA ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Bienvenido al Gestor de Vuelos**\nSelecciona una opción para comenzar:",
        reply_markup=get_user_keyboard(),
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    uid = update.effective_user.id
    user_data = context.user_data

    # --- FLUJO USUARIO ---
    if texto == "📝 Datos de vuelo":
        user_data["flujo"] = "vuelo"
        await update.message.reply_text("Escribe los detalles (Origen, Destino, Fecha):")
    
    elif texto == "📸 Enviar Pago":
        user_data["flujo"] = "pago"
        await update.message.reply_text("Adjunta la foto de tu comprobante:")

    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")

    # --- LÓGICA DE ESPERA DE DATOS (ADMIN Y USUARIO) ---
    elif user_data.get("esperando_datos_vuelo"):
        user_data["detalles_vuelo"] = texto
        user_data["esperando_datos_vuelo"] = False
        await update.message.reply_text("✅ Datos guardados. Ahora envía la foto.")

    elif user_data.get("esperando") == "admin_id_cotizar":
        user_data["cotizar_id"] = texto
        user_data["esperando"] = "admin_monto"
        await update.message.reply_text(f"ID `{texto}` seleccionado. Escribe el monto:")

    elif user_data.get("esperando") == "admin_monto":
        v_id = user_data["cotizar_id"]
        supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", v_id).execute()
        v_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
        await context.bot.send_message(v_res.data["user_id"], f"💰 Vuelo ID `{v_id}` cotizado en: **{texto}**", parse_mode="Markdown")
        await update.message.reply_text("✅ Cotización enviada.")
        user_data.clear()

    elif user_data.get("esperando") == "admin_id_confirmar":
        supabase.table("cotizaciones").update({"estado": "✅ Pagado"}).eq("id", texto).execute()
        v_res = supabase.table("cotizaciones").select("user_id").eq("id", texto).single().execute()
        await context.bot.send_message(v_res.data["user_id"], f"✅ Pago del ID `{texto}` confirmado.")
        await update.message.reply_text(f"✅ ID `{texto}` confirmado como Pagado.")
        user_data.clear()

    elif user_data.get("flujo") == "vuelo":
        user_data["temp_info"] = texto
        user_data["esperando_foto"] = True
        await update.message.reply_text("✅ Detalles registrados. Envía la foto para completar tu 'Cotización de vuelo'.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    uid = update.effective_user.id
    user_name = f"@{update.effective_user.username}" or update.effective_user.first_name
    file_id = update.message.photo[-1].file_id
    user_data = context.user_data

    # Lógica de etiquetas solicitada
    flujo = user_data.get("flujo")
    if flujo == "vuelo":
        etiqueta = "Cotización de vuelo"
        detalles = f"{etiqueta}: {user_data.get('temp_info', 'Sin detalles')}"
    elif flujo == "pago":
        etiqueta = "Comprobante de pago"
        detalles = etiqueta
    else:
        etiqueta = "Archivo recibido"
        detalles = "comprobante"

    try:
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": user_name,
            "pedido_completo": detalles, "monto": "Pendiente", "estado": "En revisión"
        }).execute()
        
        v_id = res.data[0]['id']
        
        # Notificación Admin con IDs copiables
        admin_msg = (f"🔔 **{etiqueta.upper()}**\n"
                    f"🆔 Vuelo: `{v_id}`\n"
                    f"👤 Usuario: `{uid}` ({user_name})\n"
                    f"📝 Info: {detalles}")
        
        await context.bot.send_photo(ADMIN_CHAT_ID, file_id, caption=admin_msg, parse_mode="Markdown")
        await update.message.reply_text(f"✅ Recibido: {etiqueta}\n🆔 ID: `{v_id}`", parse_mode="Markdown")
        user_data.clear()
    except Exception as e:
        logging.error(f"Error: {e}")

# --- 5. PANEL Y FUNCIONES ADMIN ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ No tienes permisos de administrador.")
        return
    await update.message.reply_text("🛠 **Panel de Control Admin**", reply_markup=get_admin_inline_keyboard(), parse_mode="Markdown")

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id

    if uid != ADMIN_CHAT_ID: return

    if query.data == "admin_cotizar":
        context.user_data["esperando"] = "admin_id_cotizar"
        await query.message.reply_text("Ingresa el **ID del vuelo** para cotizar:")
    
    elif query.data == "admin_confirmar":
        context.user_data["esperando"] = "admin_id_confirmar"
        await query.message.reply_text("Ingresa el **ID del vuelo** para confirmar pago:")

    elif query.data == "admin_historial":
        res = supabase.table("cotizaciones").select("*").order("id", desc=True).limit(10).execute()
        if not res.data:
            await query.message.reply_text("Historial vacío.")
            return
        for v in res.data:
            txt = (f"🆔 `{v['id']}` | 👤 `{v['user_id']}`\n"
                   f"📝 {v['pedido_completo']}\n"
                   f"📍 {v['estado']} | 💰 {v['monto']}")
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Borrar", callback_data=f"del_{v['id']}")]])
            await context.bot.send_message(ADMIN_CHAT_ID, txt, reply_markup=btn, parse_mode="Markdown")

    elif query.data == "admin_qr":
        await query.message.reply_text("⚠️ Función Enviar QR: Envía el QR al usuario manualmente o usa el ID para notificarlo.")

    elif query.data.startswith("del_"):
        v_id = query.data.split("_")[1]
        supabase.table("cotizaciones").delete().eq("id", v_id).execute()
        await query.edit_message_text(f"🗑️ ID `{v_id}` eliminado.")

# --- 6. INICIO ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # drop_pending_updates=True evita conflictos si Render no responde al cancelar deploy
    app.run_polling(drop_pending_updates=True)
