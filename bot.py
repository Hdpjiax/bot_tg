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

# --- 1. SERVIDOR KEEP-ALIVE ---
app_web = Flask('')
@app_web.route('/')
def home(): return "Bot Online 🚀"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

# --- 2. CONFIGURACIÓN ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

# --- 3. TECLADOS ---

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("🏦 Datos de Pago")]
    ], resize_keyboard=True)

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Cotizar", callback_data="adm_cot"),
         InlineKeyboardButton("✅ Confirmar Pago", callback_data="adm_conf")],
        [InlineKeyboardButton("📜 Historial", callback_data="adm_his")]
    ])

# --- 4. LÓGICA DE MENSAJES ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Bienvenido**\nSelecciona una opción del menú:",
        reply_markup=get_user_keyboard(),
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    # --- FLUJO USUARIO ---
    if texto == "📝 Datos de vuelo":
        udata["modo"] = "vuelo"
        await update.message.reply_text("Escribe Origen, Destino y Fecha:")
    elif texto == "📸 Enviar Pago":
        udata["modo"] = "pago"
        await update.message.reply_text("Adjunta la foto de tu comprobante:")
    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")

    # --- FLUJO ADMIN (LOGICA COTIZAR/CONFIRMAR) ---
    elif uid == ADMIN_CHAT_ID:
        if udata.get("adm_state") == "wait_id_cot":
            udata["target_id"] = texto
            udata["adm_state"] = "wait_monto"
            await update.message.reply_text(f"ID `{texto}` seleccionado. Ahora escribe el **monto**:")
        
        elif udata.get("adm_state") == "wait_monto":
            v_id = udata["target_id"]
            monto = texto
            try:
                # Actualizar DB
                res = supabase.table("cotizaciones").update({"monto": monto, "estado": "Cotizado"}).eq("id", v_id).execute()
                u_id = res.data[0]["user_id"]
                # Notificar al usuario
                await context.bot.send_message(u_id, f"💰 Tu vuelo ID `{v_id}` ha sido cotizado: **{monto}**", parse_mode="Markdown")
                await update.message.reply_text(f"✅ Cotización enviada al usuario `{u_id}`.")
                udata.clear()
            except: await update.message.reply_text("❌ ID no encontrado.")

        elif udata.get("adm_state") == "wait_id_conf":
            try:
                res = supabase.table("cotizaciones").update({"estado": "✅ Pagado"}).eq("id", texto).execute()
                u_id = res.data[0]["user_id"]
                await context.bot.send_message(u_id, f"✅ El pago de tu vuelo ID `{texto}` ha sido confirmado.")
                await update.message.reply_text(f"✅ Pago confirmado para el ID `{texto}`.")
                udata.clear()
            except: await update.message.reply_text("❌ ID no encontrado.")

    # Guardar texto de vuelo antes de la foto
    elif udata.get("modo") == "vuelo":
        udata["vuelo_info"] = texto
        await update.message.reply_text("✅ Datos recibidos. Ahora envía la foto para finalizar.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    uid = update.effective_user.id
    uname = f"@{update.effective_user.username}" or "Usuario"
    fid = update.message.photo[-1].file_id
    udata = context.user_data

    # Etiquetas solicitadas
    if udata.get("modo") == "vuelo":
        titulo = "Cotización de vuelo"
        descripcion = f"{titulo}: {udata.get('vuelo_info', 'Ver foto')}"
    elif udata.get("modo") == "pago":
        titulo = "Comprobante de pago"
        descripcion = titulo
    else:
        return # No procesar fotos fuera de flujo

    try:
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": uname,
            "pedido_completo": descripcion, "monto": "Pendiente", "estado": "Pendiente"
        }).execute()
        
        v_id = res.data[0]['id']
        
        # Notificar Admin (IDs copiables)
        msg_admin = (f"🔔 **{titulo.upper()}**\n"
                    f"🆔 Vuelo: `{v_id}`\n"
                    f"👤 Usuario: `{uid}`\n"
                    f"📝 Detalle: {descripcion}")
        
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=msg_admin, parse_mode="Markdown")
        await update.message.reply_text(f"✅ Registrado como: {titulo}\n🆔 ID de seguimiento: `{v_id}`", parse_mode="Markdown")
        udata.clear()
    except Exception as e:
        logging.error(f"Error: {e}")

# --- 5. COMANDOS Y CALLBACKS ADMIN ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    await update.message.reply_text("🛠 **Panel Admin**", reply_markup=get_admin_keyboard())

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_CHAT_ID: return

    if query.data == "adm_cot":
        context.user_data["adm_state"] = "wait_id_cot"
        await query.message.reply_text("Escribe el **ID del vuelo** a cotizar:")
    
    elif query.data == "adm_conf":
        context.user_data["adm_state"] = "wait_id_conf"
        await query.message.reply_text("Escribe el **ID del vuelo** para confirmar pago:")

    elif query.data == "adm_his":
        res = supabase.table("cotizaciones").select("*").order("id", desc=True).limit(5).execute()
        for v in res.data:
            info = f"🆔 `{v['id']}` | 👤 `{v['user_id']}`\n📍 {v['estado']} | 💰 {v['monto']}\n📝 {v['pedido_completo']}"
            await context.bot.send_message(ADMIN_CHAT_ID, info, parse_mode="Markdown")

# --- 6. EJECUCIÓN ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # drop_pending_updates=True es vital para que Render no choque con instancias viejas
    app.run_polling(drop_pending_updates=True)
