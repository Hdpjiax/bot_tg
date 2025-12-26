import logging
import os
import threading
import asyncio
import re
from datetime import datetime
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

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

# --- UTILIDADES ---

def extraer_fecha(texto):
    """Extrae fecha para ordenar vuelos por proximidad."""
    meses = {
        "ene":1, "feb":2, "mar":3, "abr":4, "may":5, "jun":6, 
        "jul":7, "ago":8, "sep":9, "oct":10, "nov":11, "dic":12
    }
    try:
        # Busca patrones tipo '27 dic' o '27/12'
        match = re.search(r'(\d{1,2})\s*([a-z]{3})', texto.lower())
        if match:
            dia, mes_txt = match.groups()
            mes = meses.get(mes_txt, 1)
            return datetime(2025, mes, int(dia))
    except:
        pass
    return datetime(2099, 12, 31)

# --- SERVIDOR WEB (FLASK) ---
app_web = Flask(__name__)

@app_web.route('/dashboard')
def dashboard_stats():
    res = supabase.table("cotizaciones").select("*").execute()
    vuelos = res.data
    # Ordenar por proximidad de fecha
    vuelos.sort(key=lambda x: extraer_fecha(x['pedido_completo']))
    
    total_recaudado = sum(float(v.get('monto', 0) or 0) for v in vuelos if v['estado'] == 'Pago Confirmado')
    validar_pago = len([v for v in vuelos if 'confirmación' in v['estado'].lower()])
    por_enviar_qr = len([v for v in vuelos if v['estado'] == 'Pago Confirmado'])
    
    return render_template('dashboard.html', 
                           seccion="stats", 
                           total=total_recaudado, 
                           vuelos=vuelos,
                           validar_pago=validar_pago,
                           por_enviar_qr=por_enviar_qr)

@app_web.route('/dashboard/seccion/<tipo>')
def dashboard_seccion(tipo):
    res = supabase.table("cotizaciones").select("*").execute()
    vuelos = res.data
    
    if tipo == "por_cotizar":
        vuelos = [v for v in vuelos if v['estado'] == 'Esperando atención']
    elif tipo == "confirmar_pago":
        vuelos = [v for v in vuelos if 'confirmación' in v['estado'].lower()]
    elif tipo == "por_enviar_qr":
        vuelos = [v for v in vuelos if v['estado'] == 'Pago Confirmado']
        
    vuelos.sort(key=lambda x: extraer_fecha(x['pedido_completo']))
    return render_template('dashboard.html', seccion="tabla", vuelos=vuelos, titulo=tipo.replace("_", " ").upper())

# --- ACCIONES CRUD ---

@app_web.route('/accion/eliminar/<v_id>')
def eliminar_vuelo(v_id):
    supabase.table("cotizaciones").delete().eq("id", v_id).execute()
    return redirect(request.referrer or url_for('dashboard_stats'))

@app_web.route('/accion/web_cotizar', methods=['POST'])
def web_cotizar():
    v_id = request.form.get('v_id')
    monto = request.form.get('monto')
    res = supabase.table("cotizaciones").update({"monto": monto, "estado": "Cotizado"}).eq("id", v_id).execute()
    if res.data:
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(res.data[0]['user_id'], f"💰 Cotización Lista\nID: {v_id}\nMonto: ${monto}\nYa puede enviar su pago."),
            bot_app.loop
        )
    return redirect(request.referrer)

@app_web.route('/accion/web_confirmar/<v_id>')
def web_confirmar(v_id):
    res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
    if res.data:
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(res.data[0]['user_id'], f"✅ Pago Validado\nSu pago para el ID {v_id} ha sido confirmado. En breve enviaremos sus QRs."),
            bot_app.loop
        )
    return redirect(request.referrer)

# --- LÓGICA DEL BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mis Pedidos"), KeyboardButton("🆘 Soporte")]
    ], resize_keyboard=True)
    await update.message.reply_text("✈️ Sistema Vuelos Pro\nSeleccione una opción:", reply_markup=kbd)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, texto, udata = update.effective_user.id, update.message.text, context.user_data
    
    if texto == "📝 Datos de vuelo":
        udata["estado"] = "esp_datos"
        await update.message.reply_text("Escriba Origen, Destino y Fecha:")
    elif udata.get("estado") == "esp_datos":
        udata["tmp_datos"], udata["estado"] = texto, "esp_foto"
        await update.message.reply_text("Envíe foto de referencia del vuelo:")
    elif texto == "📸 Enviar Pago":
        udata["estado"] = "esp_pago_id"
        await update.message.reply_text("Escriba el ID de su vuelo:")
    elif udata.get("estado") == "esp_pago_id":
        udata["p_id"], udata["estado"] = texto, "esp_comprobante"
        await update.message.reply_text(f"ID {texto} seleccionado. Envíe la captura de su pago:")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    udata, fid = context.user_data, update.message.photo[-1].file_id
    uid, user = update.effective_user.id, update.effective_user.username

    if udata.get("estado") == "esp_foto":
        res = supabase.table("cotizaciones").insert({
            "user_id": str(uid), "username": user, 
            "pedido_completo": udata["tmp_datos"], "estado": "Esperando atención"
        }).execute()
        v_id = res.data[0]['id']
        await update.message.reply_text(f"✅ Recibido. Su ID es: {v_id}")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"🔔 NUEVA SOLICITUD ID: {v_id}\nInfo: {udata['tmp_datos']}")
        udata.clear()

    elif udata.get("estado") == "esp_comprobante":
        v_id = udata["p_id"]
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación"}).eq("id", v_id).execute()
        await update.message.reply_text("✅ Comprobante enviado. Validaremos su pago en breve.")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"💰 PAGO RECIBIDO ID: {v_id}")
        udata.clear()

# --- EJECUCIÓN ---

def run_flask():
    app_web.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Iniciar Flask y Bot simultáneamente
    threading.Thread(target=run_flask, daemon=True).start()
    bot_app.run_polling()
