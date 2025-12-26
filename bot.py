import logging
import os
import threading
import asyncio
from datetime import datetime, timedelta
from flask import Flask
from supabase import create_client, Client
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)

# --- 1. SERVIDOR ---
app_web = Flask('')
@app_web.route('/')
def home(): return "Sistema Vuelos Pro - Online 🚀"

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

# --- 3. FUNCIONES DE TECLADO ---

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mis Pedidos"), KeyboardButton("🏦 Datos de Pago")],
        [KeyboardButton("🆘 Soporte")]
    ], resize_keyboard=True)

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Cotizar Vuelo", callback_data="adm_cot"),
         InlineKeyboardButton("✅ Confirmar Pago", callback_data="adm_conf")],
        [InlineKeyboardButton("🖼️ Enviar QRs", callback_data="adm_qr"),
         InlineKeyboardButton("📊 Ver Pendientes", callback_data="adm_pend")],
        [InlineKeyboardButton("📜 Historial Total", callback_data="adm_his")]
    ])

# --- 4. LÓGICA DE USUARIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Bienvenido al Sistema de Vuelos**\nUsa el menú inferior para gestionar tus trámites.",
        reply_markup=get_user_keyboard()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    # --- BOTÓN MIS PEDIDOS ---
    if texto == "📜 Mis Pedidos":
        res = supabase.table("cotizaciones").select("*").eq("user_id", str(uid)).order("created_at", desc=True).execute()
        
        if not res.data:
            await update.message.reply_text("No tienes vuelos registrados actualmente.")
            return
        
        msj = "📜 **TUS VUELOS Y COTIZACIONES**\n\n"
        for v in res.data:
            msj += (f"🆔 ID: {v['id']}\n"
                    f"📍 Estatus: {v['estado']}\n"
                    f"📝 Datos: {v['pedido_completo']}\n"
                    f"💰 Monto: {v.get('monto', 'Pendiente')}\n"
                    f"--------------------------\n")
        await update.message.reply_text(msj)

    elif texto == "📝 Datos de vuelo":
        udata["estado"] = "usr_esperando_datos"
        await update.message.reply_text("Escribe el Origen, Destino y Fecha de tu vuelo:")

    elif texto == "📸 Enviar Pago":
        udata["estado"] = "usr_esperando_id_pago"
        await update.message.reply_text("Escribe el ID del vuelo que vas a pagar:")

    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **Datos de Pago**\n\nBBVA\nCLABE: 012180015886058959\nTitular: Antonio Garcia")

    elif texto == "🆘 Soporte":
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Contactar Soporte 💬", url=f"https://t.me/{SOPORTE_USER.replace('@','')}")]])
        await update.message.reply_text("Haz clic abajo para hablar con un agente:", reply_markup=btn)

    # Lógica de estados y Admin (Texto)
    elif uid == ADMIN_CHAT_ID:
        if udata.get("adm_estado") == "adm_esp_id_cot":
            udata["target_id"] = texto
            udata["adm_estado"] = "adm_esp_monto"
            await update.message.reply_text(f"ID {texto} seleccionado. Escribe el Monto total:")
        
        elif udata.get("adm_estado") == "adm_esp_monto":
            v_id = udata["target_id"]
            supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", v_id).execute()
            user_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            await context.bot.send_message(user_res.data["user_id"], f"💰 Tu vuelo ID {v_id} ha sido cotizado.\nMonto: {texto}\n\nUsa el botón 'Enviar Pago' para finalizar.")
            await update.message.reply_text(f"✅ Cotización enviada al usuario para el ID {v_id}.")
            udata.clear()

        elif udata.get("adm_estado") == "adm_esp_id_qr":
            udata["target_id_qr"] = texto
            udata["adm_estado"] = "adm_enviando_qrs"
            udata["coleccion_fotos"] = [] 
            await update.message.reply_text(f"✅ ID {texto} seleccionado. Envía el álbum de QRs ahora.")

    elif udata.get("estado") == "usr_esperando_datos":
        udata["tmp_datos"] = texto
        udata["estado"] = "usr_esperando_foto_vuelo"
        await update.message.reply_text("✅ Datos recibidos. Ahora envía una imagen de referencia del vuelo.")

    elif udata.get("estado") == "usr_esperando_id_pago":
        res = supabase.table("cotizaciones").select("monto").eq("id", texto).execute()
        if res.data:
            udata["pago_vuelo_id"] = texto
            udata["estado"] = "usr_esperando_comprobante"
            await update.message.reply_text(f"💳 ID: {texto}\n💰 Monto: {res.data[0]['monto']}\n\nEnvía la captura del pago ahora.")
        else:
            await update.message.reply_text("❌ ID de vuelo no encontrado.")

# --- 5. LÓGICA DE MEDIA (QRs Y SOLICITUDES) ---

async def enviar_paquete_qr(context: ContextTypes.DEFAULT_TYPE, target_uid, v_id, fotos):
    instrucciones = (f"🎫 INSTRUCCIONES DE VUELO ID: {v_id}\n\n"
                     "⚠️ Instrucciones para evitar caídas:\n"
                     "- No agregar a la app.\n"
                     "- No revisar el vuelo antes de 2 horas de abordar.\n"
                     "- En caso de caída se reubica en el siguiente horario.\n"
                     "- Guarda la foto en tu galería solo para escanear.")
    await context.bot.send_message(target_uid, instrucciones)
    media_group = [InputMediaPhoto(f) for f in fotos]
    await context.bot.send_media_group(target_uid, media_group)
    await context.bot.send_message(target_uid, "🎫 ¡Disfruta tu vuelo!")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    udata = context.user_data
    if not update.message.photo: return
    fid = update.message.photo[-1].file_id

    if udata.get("estado") == "usr_esperando_foto_vuelo":
        res = supabase.table("cotizaciones").insert({
            "user_id": str(uid), "username": update.effective_user.username or "SinUser",
            "pedido_completo": udata.get("tmp_datos"), "estado": "Esperando atención"
        }).execute()
        v_id = res.data[0]['id']
        await update.message.reply_text(f"✅ Solicitud recibida. ID: {v_id}")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"🔔 NUEVA SOLICITUD\nID: {v_id}\nUser: @{update.effective_user.username}\nInfo: {udata.get('tmp_datos')}")
        udata.clear()

    elif udata.get("estado") == "usr_esperando_comprobante":
        v_id = udata.get("pago_vuelo_id")
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación de pago"}).eq("id", v_id).execute()
        await update.message.reply_text("✅ Comprobante enviado. Revisaremos en breve.")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"💰 PAGO RECIBIDO\nID: {v_id}\nUser: @{update.effective_user.username}")
        udata.clear()

    elif uid == ADMIN_CHAT_ID and udata.get("adm_estado") == "adm_enviando_qrs":
        v_id = udata.get("target_id_qr")
        udata["coleccion_fotos"].append(fid)
        if "job_envio" in udata: udata["job_envio"].cancel()

        async def task():
            await asyncio.sleep(1) 
            user_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            await enviar_paquete_qr(context, user_res.data["user_id"], v_id, udata["coleccion_fotos"])
            supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
            await context.bot.send_message(ADMIN_CHAT_ID, f"✅ QRs del ID {v_id} enviados.")
            udata.clear()
        udata["job_envio"] = asyncio.create_task(task())

# --- 6. CALLBACKS ADMIN (PENDIENTES E HISTORIAL) ---

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_CHAT_ID: return

    if query.data == "adm_cot":
        context.user_data["adm_estado"] = "adm_esp_id_cot"
        await query.message.reply_text("Introduce el ID del vuelo a cotizar:")
    
    elif query.data == "adm_qr":
        context.user_data["adm_estado"] = "adm_esp_id_qr"
        await query.message.reply_text("Introduce el ID para enviar QRs:")

    elif query.data == "adm_pend":
        res = supabase.table("cotizaciones").select("*").neq("estado", "QR Enviados").order("username", desc=False).execute()
        if not res.data:
            await query.message.reply_text("No hay vuelos pendientes.")
            return
        
        msj = "📊 **VUELOS PENDIENTES**\n\n"
        for v in res.data:
            msj += f"👤 @{v['username']}\n🆔 ID: {v['id']} - {v['estado']}\n📝 {v['pedido_completo']}\n\n"
        await query.message.reply_text(msj)

    elif query.data == "adm_his":
        # Corregido: asc=True por desc=False
        res = supabase.table("cotizaciones").select("*").order("username", desc=False).execute()
        if not res.data:
            await query.message.reply_text("Historial vacío.")
            return

        msj = "📜 **HISTORIAL COMPLETO**\n\n"
        curr_user = ""
        for v in res.data:
            if curr_user != v['username']:
                curr_user = v['username']
                msj += f"\n👤 **@{curr_user}**\n"
            msj += f"- ID {v['id']}: {v['estado']} ({v.get('monto', '-')})\n"
        
        if len(msj) > 4000:
            for i in range(0, len(msj), 4000):
                await query.message.reply_text(msj[i:i+4000])
        else:
            await query.message.reply_text(msj)

# --- 7. ARRANQUE ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Registro de Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", lambda u, c: c.bot.send_message(ADMIN_CHAT_ID, "🛠 Panel Admin", reply_markup=get_admin_keyboard()) if u.effective_user.id == ADMIN_CHAT_ID else None))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Bot activo...")
    app.run_polling()
