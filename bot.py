import logging
import os
import threading
import asyncio
from flask import Flask, render_template, request, redirect, url_for, jsonify
from supabase import create_client, Client
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- CONFIGURACIÓN ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "7721918273"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# --- FUNCIONES DE APOYO ---

def obtener_metricas(vuelos):
    """Calcula totales para evitar el error 'Undefined' en el HTML"""
    total = sum(float(v.get('monto', 0) or 0) for v in vuelos if v.get('estado') in ['Pago Confirmado', 'QR Enviados'])
    validar = len([v for v in vuelos if 'confirmación' in str(v.get('estado')).lower()])
    pendientes_qr = len([v for v in vuelos if v.get('estado') == 'Pago Confirmado'])
    return total, validar, pendientes_qr

# --- RUTAS DEL DASHBOARD ---

@app.route('/')
def home(): 
    return redirect(url_for('dashboard_stats'))

@app.route('/dashboard')
def dashboard_stats():
    try:
        # Consulta limpia sin .order() para evitar error 42703
        res = supabase.table("cotizaciones").select("*").execute()
        vuelos = res.data or []
        t, v, p = obtener_metricas(vuelos)
        return render_template('dashboard.html', seccion="stats", total=t, vuelos=vuelos, validar_pago=v, por_enviar_qr=p, titulo="GENERAL")
    except Exception as e:
        return f"Error: {e}", 500

@app.route('/dashboard/seccion/<tipo>')
def dashboard_seccion(tipo):
    try:
        res = supabase.table("cotizaciones").select("*").execute()
        todos = res.data or []
        t, v, p = obtener_metricas(todos)
        
        if tipo == "por_cotizar": filtrados = [x for x in todos if x.get('estado') == 'Esperando atención']
        elif tipo == "confirmar_pago": filtrados = [x for x in todos if 'confirmación' in str(x.get('estado')).lower()]
        elif tipo == "por_enviar_qr": filtrados = [x for x in todos if x.get('estado') == 'Pago Confirmado']
        else: filtrados = todos

        return render_template('dashboard.html', seccion="tabla", vuelos=filtrados, total=t, validar_pago=v, por_enviar_qr=p, titulo=tipo.upper())
    except Exception as e:
        return f"Error: {e}", 500

# --- ACCIONES DE LOS BOTONES (CRUD) ---

@app.route('/accion/web_cotizar', methods=['POST'])
def web_cotizar():
    v_id = request.form.get('v_id')
    monto = request.form.get('monto')
    # Actualización inmediata en base de datos
    res = supabase.table("cotizaciones").update({"monto": monto, "estado": "Cotizado"}).eq("id", v_id).execute()
    if res.data:
        # Notificar al usuario por Telegram
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(res.data[0]['user_id'], f"💰 Cotización Lista\nID: {v_id}\nMonto: ${monto}\nYa puede enviar su pago."),
            bot_app.loop
        )
    return redirect(request.referrer or url_for('dashboard_stats'))

@app.route('/accion/web_confirmar/<v_id>')
def web_confirmar(v_id):
    res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
    if res.data:
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(res.data[0]['user_id'], f"✅ Pago Validado (ID {v_id}). En breve recibirá sus QRs."),
            bot_app.loop
        )
    return redirect(request.referrer or url_for('dashboard_stats'))

@app.route('/accion/eliminar/<v_id>')
def eliminar_vuelo(v_id):
    supabase.table("cotizaciones").delete().eq("id", v_id).execute()
    return redirect(request.referrer or url_for('dashboard_stats'))

@app.route('/accion/enviar_qr_masivo', methods=['POST'])
def enviar_qr_masivo():
    v_id = request.form.get('v_id')
    files = request.files.getlist('fotos')
    res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
    if res.data:
        for f in files:
            asyncio.run_coroutine_threadsafe(
                bot_app.bot.send_photo(chat_id=res.data['user_id'], photo=f.read(), caption=f"🎫 Boleto ID #{v_id}"),
                bot_app.loop
            )
        supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
    return jsonify({"status": "success"})

# --- BOT DE TELEGRAM ---

async def handle_msg(u, c):
    uid, txt, ud = u.effective_user.id, u.message.text, c.user_data
    if txt == "📝 Datos de vuelo":
        ud["st"] = "datos"
        await u.message.reply_text("Origen, Destino y Fecha:")
    elif ud.get("st") == "datos":
        res = supabase.table("cotizaciones").insert({"user_id": str(uid), "username": u.effective_user.username, "pedido_completo": txt, "estado": "Esperando atención"}).execute()
        await u.message.reply_text(f"✅ Registrado. ID: {res.data[0]['id']}")
        ud.clear()

if __name__ == "__main__":
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000))), daemon=True).start()
    
    # Limpieza de conflictos de sesión
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot_app.bot.delete_webhook(drop_pending_updates=True))
    bot_app.run_polling()
