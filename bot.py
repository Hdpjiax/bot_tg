import logging
import os
import threading
from datetime import datetime, timedelta
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
def home(): return "Bot Multi-Función Activo 🚀"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

# --- 2. CONFIGURACIÓN ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SOPORTE_USER = "@TuUsuarioSoporte" 

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

# --- 3. TECLADOS ---

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mis Pedidos"), KeyboardButton("🏦 Datos de Pago")],
        [KeyboardButton("🆘 Soporte")]
    ], resize_keyboard=True)

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Cotizar", callback_data="adm_cot"),
         InlineKeyboardButton("✅ Confirmar Pago", callback_data="adm_conf")],
        [InlineKeyboardButton("🖼️ Enviar QRs", callback_data="adm_qr"),
         InlineKeyboardButton("⏳ Pendientes (5d)", callback_data="adm_pend")],
        [InlineKeyboardButton("📜 Historial Total", callback_data="adm_his")]
    ])

# --- 4. FUNCIONES DE USUARIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Sistema de Vuelos**\nBienvenido. Selecciona una opción del menú:",
        reply_markup=get_user_keyboard(),
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    if texto == "📝 Datos de vuelo":
        udata["estado"] = "usr_esp_datos"
        await update.message.reply_text("Escribe Origen, Destino y Fecha:")
    elif texto == "📸 Enviar Pago":
        udata["estado"] = "usr_esp_id_pago"
        await update.message.reply_text("Escribe el **ID del vuelo**:")
    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")
    elif texto == "🆘 Soporte":
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Ir a Soporte 💬", url=f"https://t.me/{SOPORTE_USER.replace('@','')}")]])
        await update.message.reply_text("Contacto de soporte:", reply_markup=btn)
    elif texto == "📜 Mis Pedidos":
        res = supabase.table("cotizaciones").select("*").eq("user_id", uid).execute()
        for v in res.data:
            await update.message.reply_text(f"🆔 ID: `{v['id']}`\n📍 Estado: {v['estado']}\n💰 Monto: {v['monto']}")

    # --- LÓGICA DE ESTADOS ---
    elif udata.get("estado") == "usr_esp_datos":
        udata["tmp_datos"] = texto
        udata["estado"] = "usr_esp_foto"
        await update.message.reply_text("✅ Datos recibidos. Envía la imagen de referencia.")
    
    elif udata.get("estado") == "usr_esp_id_pago":
        res = supabase.table("cotizaciones").select("*").eq("id", texto).eq("user_id", uid).execute()
        if res.data:
            udata["pago_id"] = texto
            udata["estado"] = "usr_esp_comprobante"
            await update.message.reply_text(f"Monto: {res.data[0]['monto']}\nEnvía la captura del pago ahora.")
        else: await update.message.reply_text("❌ ID no válido.")

    # --- LÓGICA ADMIN ---
    elif uid == ADMIN_CHAT_ID:
        if udata.get("adm_estado") == "adm_esp_id_cot":
            udata["target_id"] = texto
            udata["adm_estado"] = "adm_esp_monto"
            await update.message.reply_text(f"ID `{texto}` seleccionado. Escribe el monto:")
        elif udata.get("adm_estado") == "adm_esp_monto":
            v_id = udata["target_id"]
            supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", v_id).execute()
            await update.message.reply_text("✅ Cotización enviada.")
            udata.clear()
        elif udata.get("adm_estado") == "adm_esp_id_qr":
            udata["target_id_qr"] = texto
            udata["adm_estado"] = "adm_enviando_qrs"
            await update.message.reply_text(f"Listo para ID `{texto}`. Envía las fotos (Instrucciones se enviarán primero automáticamente).")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    fid = update.message.photo[-1].file_id
    udata = context.user_data

    if udata.get("estado") == "usr_esp_foto":
        res = supabase.table("cotizaciones").insert({"user_id": uid, "username": update.effective_user.username, "pedido_completo": udata.get("tmp_datos"), "estado": "Pendiente"}).execute()
        await update.message.reply_text(f"✅ Recibido. ID: `{res.data[0]['id']}`. Espera ser atendido.")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"🔔 NUEVA SOLICITUD\nID: `{res.data[0]['id']}`")
        udata.clear()

    elif udata.get("estado") == "usr_esp_comprobante":
        v_id = udata.get("pago_id")
        supabase.table("cotizaciones").update({"estado": "Confirmando Pago"}).eq("id", v_id).execute()
        await update.message.reply_text("✅ Comprobante enviado.")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"💰 PAGO RECIBIDO\nID: `{v_id}`")
        udata.clear()

    elif uid == ADMIN_CHAT_ID and udata.get("adm_estado") == "adm_enviando_qrs":
        v_id = udata.get("target_id_qr")
        u_id = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute().data["user_id"]
        # Secuencia: Instrucciones -> QR -> Disfruta
        await context.bot.send_message(u_id, "✈️ **INSTRUCCIONES DE SEGURIDAD:**\n- No agregar a app.\n- No revisar vuelo.\n- Validación 2h antes.")
        await context.bot.send_photo(u_id, fid)
        await context.bot.send_message(u_id, "🎫 **¡Disfruta tu vuelo!**")
        supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
        await update.message.reply_text("✅ QR enviado.")

# --- 5. CALLBACKS ADMIN ---

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_CHAT_ID: return

    if query.data == "adm_cot":
        context.user_data["adm_estado"] = "adm_esp_id_cot"
        await query.message.reply_text("Escribe el ID:")
    elif query.data == "adm_qr":
        context.user_data["adm_estado"] = "adm_esp_id_qr"
        await query.message.reply_text("Escribe el ID para QRs:")
    elif query.data == "adm_pend":
        res = supabase.table("cotizaciones").select("*").filter("estado", "in", '("Cotizado", "Confirmando Pago")').execute()
        msj = "⏳ **PENDIENTES:**\n"
        for v in res.data: msj += f"🆔 `{v['id']}` | 👤 @{v['username']}\n"
        await query.message.reply_text(msj, parse_mode="Markdown")
    elif query.data == "adm_his":
        res = supabase.table("cotizaciones").select("*").limit(10).execute()
        msj = "📜 **HISTORIAL:**\n"
        for v in res.data: msj += f"🆔 `{v['id']}` | 👤 `{v['user_id']}` | @{v['username']}\n"
        await query.message.reply_text(msj, parse_mode="Markdown")

# --- 6. ARRANQUE ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", lambda u, c: c.bot.send_message(ADMIN_CHAT_ID, "🛠 Panel", reply_markup=get_admin_keyboard())))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(drop_pending_updates=True)
