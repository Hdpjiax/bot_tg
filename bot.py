import logging
import os
import threading
import asyncio
import re
from datetime import datetime
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

def extraer_fecha(texto):
    """Ordena por fecha de salida escrita en el mensaje para evitar error de columna created_at."""
    meses = {"ene":1, "feb":2, "mar":3, "abr":4, "may":5, "jun":6, "jul":7, "ago":8, "sep":9, "oct":10, "nov":11, "dic":12}
    try:
        match = re.search(r'(\d{1,2})\s*([a-z]{3})', str(texto).lower())
        if match:
            dia, mes_txt = match.groups()
            return datetime(2025, meses.get(mes_txt, 1), int(dia))
    except: pass
    return datetime(2099, 12, 31)

# --- SERVIDOR WEB (FLASK) ---
app = Flask(__name__)

def obtener_metricas(vuelos):
    """Calcula las métricas para que siempre estén disponibles en todas las secciones"""
    total = sum(float(v.get('monto', 0) or 0) for v in vuelos if v.get('estado') in ['Pago Confirmado', 'QR Enviados'])
    validar = len([v for v in vuelos if 'confirmación' in str(v.get('estado')).lower()])
    pendientes_qr = len([v for v in vuelos if v.get('estado') == 'Pago Confirmado'])
    return total, validar, pendientes_qr

@app.route('/')
def home(): 
    return redirect(url_for('dashboard_stats'))

@app.route('/dashboard')
def dashboard_stats():
    try:
        # Se eliminó .order("created_at") para solucionar el error APIError 42703
        res = supabase.table("cotizaciones").select("*").execute()
        vuelos = res.data or []
        vuelos.sort(key=lambda x: extraer_fecha(x.get('pedido_completo', '')))
        total, validar, pendientes_qr = obtener_metricas(vuelos)
        
        return render_template('dashboard.html', 
                               seccion="stats", 
                               total=total, 
                               vuelos=vuelos, 
                               validar_pago=validar, 
                               por_enviar_qr=pendientes_qr, 
                               titulo="RESUMEN GENERAL")
    except Exception as e:
        logging.error(f"Error Dashboard: {e}")
        return f"Error en Dashboard: {str(e)}", 500

@app.route('/dashboard/seccion/<tipo>')
def dashboard_seccion(tipo):
    try:
        res = supabase.table("cotizaciones").select("*").execute()
        todos_los_vuelos = res.data or []
        total, validar, pendientes_qr = obtener_metricas(todos_los_vuelos)
        
        # Filtrado lógico por sección
        if tipo == "por_cotizar": 
            vuelos_filtrados = [v for v in todos_los_vuelos if v.get('estado') == 'Esperando atención']
        elif tipo == "confirmar_pago": 
            vuelos_filtrados = [v for v in todos_los_vuelos if 'confirmación' in str(v.get('estado')).lower()]
        elif tipo == "por_enviar_qr": 
            vuelos_filtrados = [v for v in todos_los_vuelos if v.get('estado') == 'Pago Confirmado']
        else:
            vuelos_filtrados = todos_los_vuelos

        vuelos_filtrados.sort(key=lambda x: extraer_fecha(x.get('pedido_completo', '')))
        
        # Enviamos todas las métricas para evitar el error de variable indefinida
        return render_template('dashboard.html', 
                               seccion="tabla", 
                               vuelos=vuelos_filtrados, 
                               total=total, 
                               validar_pago=validar, 
                               por_enviar_qr=pendientes_qr, 
                               titulo=tipo.replace("_", " ").upper())
    except Exception as e:
        logging.error(f"Error Sección {tipo}: {e}")
        return f"Error en sección: {e}", 500

# --- ACCIONES ---
@app.route('/accion/web_cotizar', methods=['POST'])
def web_cotizar():
    v_id, monto = request.form.get('v_id'), request.form.get('monto')
    res = supabase.table("cotizaciones").update({"monto": monto, "estado": "Cotizado"}).eq("id", v_id).execute()
    if res.data:
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(res.data[0]['user_id'], f"💰 Cotización Lista\nID: {v_id}\nMonto: ${monto}\nYa puede enviar su pago."),
            bot_app.loop
        )
    return redirect(request.referrer or url_for('dashboard_stats'))

@app.route('/accion/enviar_qr_masivo', methods=['POST'])
def enviar_qr_masivo():
    v_id = request.form.get('v_id')
    files = request.files.getlist('fotos')
    res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
    if res.data:
        uid = res.data['user_id']
        for f in files:
            img_bytes = f.read()
            asyncio.run_coroutine_threadsafe(
                bot_app.bot.send_photo(chat_id=uid, photo=img_bytes, caption=f"🎫 Tu boleto para el Vuelo #{v_id}"),
                bot_app.loop
            )
        supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
    return jsonify({"status": "success"})

@app.route('/accion/web_confirmar/<v_id>')
def web_confirmar(v_id):
    res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
    if res.data:
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(res.data[0]['user_id'], "✅ Su pago ha sido confirmado. En breve enviaremos sus boletos."),
            bot_app.loop
        )
    return redirect(request.referrer)

@app.route('/accion/eliminar/<v_id>')
def eliminar_vuelo(v_id):
    supabase.table("cotizaciones").delete().eq("id", v_id).execute()
    return redirect(request.referrer)

# --- LÓGICA DEL BOT ---
async def start(u, c):
    k = ReplyKeyboardMarkup([[KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")]], resize_keyboard=True)
    await u.message.reply_text("✈️ Sistema Vuelos Pro Activo", reply_markup=k)

async def handle_msg(u, c):
    uid, txt, ud = u.effective_user.id, u.message.text, c.user_data
    if txt == "📝 Datos de vuelo":
        ud["st"] = "datos"
        await u.message.reply_text("Escriba Origen, Destino y Fecha:")
    elif ud.get("st") == "datos":
        res = supabase.table("cotizaciones").insert({
            "user_id": str(uid), "username": u.effective_user.username, 
            "pedido_completo": txt, "estado": "Esperando atención"
        }).execute()
        await u.message.reply_text(f"✅ Registrado. Su ID es: {res.data[0]['id']}")
        ud.clear()

# --- EJECUCIÓN ---
def run_flask(): 
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Limpieza de webhook para solucionar el error de "Conflict"
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot_app.bot.delete_webhook(drop_pending_updates=True))
    
    bot_app.run_polling()
