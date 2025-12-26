import logging
import os
import threading
import asyncio
from datetime import datetime, timedelta
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

# --- SERVIDOR WEB (FLASK) ---
app_web = Flask(__name__)

# 1. Página Principal: Estadísticas
@app_web.route('/dashboard')
def dashboard_stats():
    res = supabase.table("cotizaciones").select("*").execute()
    vuelos = res.data
    # Cálculo de métricas
    total_recaudado = sum(float(v.get('monto', 0) or 0) for v in vuelos if v['estado'] == 'Pago Confirmado' or v['estado'] == 'QR Enviados')
    pendientes_atencion = len([v for v in vuelos if v['estado'] == 'Esperando atención'])
    return render_template('dashboard.html', seccion="stats", total=total_recaudado, pendientes=pendientes_atencion, vuelos=vuelos)

# 2. Secciones Filtradas
@app_web.route('/dashboard/seccion/<tipo>')
def dashboard_seccion(tipo):
    res = supabase.table("cotizaciones").select("*").execute()
    vuelos = res.data
    
    if tipo == "por_cotizar":
        vuelos = [v for v in vuelos if v['estado'] == 'Esperando atención']
    elif tipo == "esperando_pago":
        vuelos = [v for v in vuelos if v['estado'] == 'Cotizado']
    elif tipo == "confirmar_pago":
        vuelos = [v for v in vuelos if 'confirmación' in v['estado'].lower()]
    elif tipo == "proximos":
        # Filtro de seguridad para vuelos recientes o no finalizados
        vuelos = [v for v in vuelos if v['estado'] != 'QR Enviados']
        
    return render_template('dashboard.html', seccion="tabla", vuelos=vuelos, titulo=tipo.replace("_", " ").upper())

# 3. Historial por Usuario
@app_web.route('/dashboard/usuario/<username>')
def historial_usuario(username):
    res = supabase.table("cotizaciones").select("*").eq("username", username).execute()
    return render_template('dashboard.html', seccion="usuario", vuelos=res.data, user=username)

# 4. Acciones CRUD (Borrar/Cotizar/Confirmar)
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
        asyncio.run_coroutine_threadsafe(bot_app.bot.send_message(res.data[0]['user_id'], f"💰 ID {v_id} COTIZADO: ${monto}. Ya puede enviar su pago."), bot_app.loop)
    return redirect(request.referrer)

@app_web.route('/accion/web_confirmar/<v_id>')
def web_confirmar(v_id):
    res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
    if res.data:
        asyncio.run_coroutine_threadsafe(bot_app.bot.send_message(res.data[0]['user_id'], f"✅ Pago ID {v_id} validado correctamente."), bot_app.loop)
    return redirect(request.referrer)

# --- LÓGICA DEL BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = ReplyKeyboardMarkup([[KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")], [KeyboardButton("📜 Mis Pedidos"), KeyboardButton("🆘 Soporte")]], resize_keyboard=True)
    await update.message.reply_text("✈️ Sistema Vuelos Pro Activo", reply_markup=kbd)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, texto, udata = update.effective_user.id, update.message.text, context.user_data
    if texto == "📝 Datos de vuelo":
        udata["estado"] = "esp_datos"
        await update.message.reply_text("Escriba Origen, Destino y Fecha:")
    elif udata.get("estado") == "esp_datos":
        udata["tmp_datos"], udata["estado"] = texto, "esp_foto"
        await update.message.reply_text("Envíe foto de referencia:")
    elif texto == "📸 Enviar Pago":
        udata["estado"] = "esp_pago_id"
        await update.message.reply_text("Escriba el ID del vuelo:")
    elif udata.get("estado") == "esp_pago_id":
        udata["p_id"], udata["estado"] = texto, "esp_comprobante"
        await update.message.reply_text("Envíe captura del pago:")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    udata, fid, uid = context.user_data, update.message.photo[-1].file_id, update.effective_user.id
    if udata.get("estado") == "esp_foto":
        res = supabase.table("cotizaciones").insert({"user_id": str(uid), "username": update.effective_user.username, "pedido_completo": udata["tmp_datos"], "estado": "Esperando atención"}).execute()
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"🔔 NUEVA SOLICITUD ID: {res.data[0]['id']}")
        udata.clear()
    elif udata.get("estado") == "esp_comprobante":
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación"}).eq("id", udata["p_id"]).execute()
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"💰 PAGO RECIBIDO ID: {udata['p_id']}")
        udata.clear()

# --- EJECUCIÓN ---

def run_flask():
    app_web.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    threading.Thread(target=run_flask, daemon=True).start()
    bot_app.run_polling()
