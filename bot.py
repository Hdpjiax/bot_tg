import logging
import os
import threading
import asyncio
from flask import Flask, render_template, request, redirect, url_for
from supabase import create_client, Client
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)

# --- CONFIGURACIÓN ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "7721918273"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SOPORTE_USER = "@TuUsuarioSoporte"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

# --- SERVIDOR WEB (FLASK) ---
app_web = Flask(__name__)

# Ruta Principal del Dashboard
@app_web.route('/dashboard')
def dashboard():
    res = supabase.table("cotizaciones").select("*").order("created_at", desc=True).execute()
    return render_template('dashboard.html', vuelos=res.data)

# Acción: Cotizar desde la Web
@app_web.route('/accion/web_cotizar', methods=['POST'])
def web_cotizar():
    v_id = request.form.get('v_id')
    monto = request.form.get('monto')
    res = supabase.table("cotizaciones").update({"monto": monto, "estado": "Cotizado"}).eq("id", v_id).execute()
    
    if res.data:
        uid = res.data[0]['user_id']
        # Enviamos notificación al usuario vía Bot
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(uid, f"💰 **Tu vuelo ID {v_id} ha sido cotizado.**\n\n**Monto:** {monto}\n\nYa puedes proceder con el pago usando el botón '📸 Enviar Pago'."),
            bot_app.loop
        )
    return redirect(url_for('dashboard'))

# Acción: Confirmar Pago desde la Web
@app_web.route('/accion/web_confirmar/<v_id>')
def web_confirmar(v_id):
    res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
    if res.data:
        uid = res.data[0]['user_id']
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(uid, f"✅ **Pago Confirmado**\nTu pago para el vuelo ID {v_id} ha sido validado. En breve recibirás tus pases."),
            bot_app.loop
        )
    return redirect(url_for('dashboard'))

# --- LÓGICA DEL BOT (TU CÓDIGO ACTUAL) ---
def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mis Pedidos"), KeyboardButton("🏦 Datos de Pago")],
        [KeyboardButton("🆘 Soporte")]
    ], resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✈️ **Bienvenido al Sistema de Vuelos**", reply_markup=get_user_keyboard())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, texto, udata = update.effective_user.id, update.message.text, context.user_data
    
    if texto == "📜 Mis Pedidos":
        res = supabase.table("cotizaciones").select("*").eq("user_id", str(uid)).order("created_at", desc=True).execute()
        msj = "📜 **TUS PEDIDOS**\n\n"
        for v in res.data: msj += f"🆔 {v['id']} | {v['estado']} | {v.get('monto','-')}\n"
        await update.message.reply_text(msj)
    
    elif texto == "📝 Datos de vuelo":
        udata["estado"] = "usr_esp_datos"
        await update.message.reply_text("Escribe Origen, Destino y Fecha:")
    
    elif texto == "📸 Enviar Pago":
        udata["estado"] = "usr_esp_id_pago"
        await update.message.reply_text("Escribe el ID del vuelo:")

    elif udata.get("estado") == "usr_esp_datos":
        udata["tmp_datos"], udata["estado"] = texto, "usr_esp_foto"
        await update.message.reply_text("Envía imagen de referencia:")

    elif udata.get("estado") == "usr_esp_id_pago":
        udata["pago_vuelo_id"], udata["estado"] = texto, "usr_esp_comprobante"
        await update.message.reply_text("Envía captura del pago:")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    udata, fid, uid = context.user_data, update.message.photo[-1].file_id, update.effective_user.id
    
    if udata.get("estado") == "usr_esp_foto":
        res = supabase.table("cotizaciones").insert({"user_id": str(uid), "username": update.effective_user.username, "pedido_completo": udata["tmp_datos"], "estado": "Esperando atención"}).execute()
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"🔔 NUEVA SOLICITUD ID: {res.data[0]['id']}\nInfo: {udata['tmp_datos']}")
        udata.clear()
    
    elif udata.get("estado") == "usr_esp_comprobante":
        v_id = udata["pago_vuelo_id"]
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación de pago"}).eq("id", v_id).execute()
        btn = InlineKeyboardMarkup([[InlineKeyboardButton(f"Confirmar Pago {v_id} ✅", callback_data=f"conf_pago_{v_id}")]])
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"💰 PAGO RECIBIDO ID: {v_id}", reply_markup=btn)
        udata.clear()

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.startswith("conf_pago_"):
        v_id = query.data.split("_")[2]
        res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
        await context.bot.send_message(res.data[0]['user_id'], f"✅ Pago ID {v_id} confirmado.")
        await query.edit_message_caption("✅ Confirmado")

def run_flask():
    app_web.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(callbacks))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    threading.Thread(target=run_flask).start()
    bot_app.run_polling()
