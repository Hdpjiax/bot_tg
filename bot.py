import logging
import os
import threading
import asyncio
from flask import Flask, render_template, request, redirect, url_for
from supabase import create_client, Client
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ReplyKeyboardMarkup, KeyboardButton
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

@app_web.route('/')
def home():
    return "Bot Operativo. Accede a /dashboard para administrar."

@app_web.route('/dashboard')
def dashboard():
    try:
        # Simplificamos la consulta para evitar errores de columnas inexistentes
        res = supabase.table("cotizaciones").select("*").execute()
        # Ordenamos manualmente en Python si la DB da problemas con created_at
        vuelos_sorted = sorted(res.data, key=lambda x: x.get('id', 0), reverse=True)
        return render_template('dashboard.html', vuelos=vuelos_sorted)
    except Exception as e:
        return f"Error en Dashboard: {str(e)}"

@app_web.route('/accion/web_cotizar', methods=['POST'])
def web_cotizar():
    v_id = request.form.get('v_id')
    monto = request.form.get('monto')
    res = supabase.table("cotizaciones").update({"monto": monto, "estado": "Cotizado"}).eq("id", v_id).execute()
    
    if res.data:
        uid = res.data[0]['user_id']
        # Usamos texto plano para evitar el error "Can't parse entities"
        msj = f"💰 Su vuelo ID {v_id} ha sido cotizado.\nMonto: {monto}\n\nYa puede enviar su pago."
        asyncio.run_coroutine_threadsafe(bot_app.bot.send_message(uid, msj), bot_app.loop)
    return redirect(url_for('dashboard'))

@app_web.route('/accion/web_confirmar/<v_id>')
def web_confirmar(v_id):
    res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
    if res.data:
        uid = res.data[0]['user_id']
        msj = f"✅ Pago Confirmado. Su pago para el ID {v_id} ha sido validado correctamente."
        asyncio.run_coroutine_threadsafe(bot_app.bot.send_message(uid, msj), bot_app.loop)
    return redirect(url_for('dashboard'))

# --- LÓGICA DEL BOT ---

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mis Pedidos"), KeyboardButton("🏦 Datos de Pago")],
        [KeyboardButton("🆘 Soporte")]
    ], resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✈️ Bienvenido al Sistema de Vuelos", reply_markup=get_user_keyboard())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, texto, udata = update.effective_user.id, update.message.text, context.user_data

    if texto == "📜 Mis Pedidos":
        res = supabase.table("cotizaciones").select("*").eq("user_id", str(uid)).execute()
        if not res.data:
            await update.message.reply_text("No tienes pedidos registrados.")
            return
        msj = "📜 TUS PEDIDOS:\n"
        for v in res.data:
            msj += f"- ID {v['id']}: {v['estado']} (${v.get('monto','--')})\n"
        await update.message.reply_text(msj)

    elif texto == "📝 Datos de vuelo":
        udata["estado"] = "usr_esp_datos"
        await update.message.reply_text("Escribe Origen, Destino y Fecha:")

    elif texto == "📸 Enviar Pago":
        udata["estado"] = "usr_esp_id_pago"
        await update.message.reply_text("Escribe el ID del vuelo a pagar:")

    elif udata.get("estado") == "usr_esp_datos":
        udata["tmp_datos"], udata["estado"] = texto, "usr_esp_foto"
        await update.message.reply_text("Envía una imagen de referencia de tu vuelo:")

    elif udata.get("estado") == "usr_esp_id_pago":
        udata["pago_vuelo_id"], udata["estado"] = texto, "usr_esp_comprobante"
        await update.message.reply_text(f"ID {texto} seleccionado. Envía la captura de tu pago:")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    udata, fid, uid = context.user_data, update.message.photo[-1].file_id, update.effective_user.id

    if udata.get("estado") == "usr_esp_foto":
        res = supabase.table("cotizaciones").insert({
            "user_id": str(uid), "username": update.effective_user.username,
            "pedido_completo": udata["tmp_datos"], "estado": "Esperando atención"
        }).execute()
        v_id = res.data[0]['id']
        await update.message.reply_text(f"✅ Registrado. ID: {v_id}")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"NUEVA SOLICITUD ID {v_id}\nInfo: {udata['tmp_datos']}")
        udata.clear()

    elif udata.get("estado") == "usr_esp_comprobante":
        v_id = udata["pago_vuelo_id"]
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación"}).eq("id", v_id).execute()
        await update.message.reply_text("✅ Comprobante enviado. Revisaremos en breve.")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"PAGO RECIBIDO ID {v_id}")
        udata.clear()

# --- ARRANQUE ---

def run_flask():
    app_web.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    # Creamos la aplicación del bot
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Registramos los comandos y mensajes
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Hilo para Flask (Dashboard)
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Iniciamos el Bot
    bot_app.run_polling()
