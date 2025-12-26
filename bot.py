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
         InlineKeyboardButton("✅ Confirmar Pago Man.", callback_data="adm_conf")],
        [InlineKeyboardButton("🖼️ Enviar QRs", callback_data="adm_qr"),
         InlineKeyboardButton("📊 Ver Pendientes", callback_data="adm_pend")],
        [InlineKeyboardButton("📜 Historial Total", callback_data="adm_his")]
    ])

# --- 4. LÓGICA DE USUARIO Y TEXTO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Bienvenido al Sistema de Vuelos**\nUsa el menú inferior para gestionar tus trámites.",
        reply_markup=get_user_keyboard()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    if texto == "📜 Mis Pedidos":
        res = supabase.table("cotizaciones").select("*").eq("user_id", str(uid)).order("created_at", desc=True).execute()
        if not res.data:
            await update.message.reply_text("No tienes vuelos registrados actualmente.")
            return
        msj = "📜 **TUS VUELOS Y COTIZACIONES**\n\n"
        for v in res.data:
            msj += f"🆔 ID: {v['id']}\n📍 Estatus: {v['estado']}\n📝 Datos: {v['pedido_completo']}\n💰 Monto: {v.get('monto', 'Pendiente')}\n--------------------------\n"
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
            await update.message.reply_text(f"✅ Cotización enviada.")
            udata.clear()
        elif udata.get("adm_estado") == "adm_esp_id_qr":
            udata["target_id_qr"] = texto
            udata["adm_estado"] = "adm_enviando_qrs"
            udata["coleccion_fotos"] = [] 
            await update.message.reply_text(f"✅ ID {texto} seleccionado. Envía el álbum de QRs.")

    elif udata.get("estado") == "usr_esperando_datos":
        udata["tmp_datos"] = texto
        udata["estado"] = "usr_esperando_foto_vuelo"
        await update.message.reply_text("✅ Datos recibidos. Ahora envía una imagen de referencia.")

    elif udata.get("estado") == "usr_esperando_id_pago":
        res = supabase.table("cotizaciones").select("monto").eq("id", texto).execute()
        if res.data:
            udata["pago_vuelo_id"] = texto # Guardamos el ID para la foto que sigue
            udata["estado"] = "usr_esperando_comprobante"
            await update.message.reply_text(f"💳 ID: {texto}\n💰 Monto: {res.data[0]['monto']}\n\nEnvía la captura del pago.")
        else:
            await update.message.reply_text("❌ ID no encontrado.")

# --- 5. MANEJO DE MEDIA (BOTÓN AUTOMÁTICO DE PAGO) ---

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    udata = context.user_data
    if not update.message.photo: return
    fid = update.message.photo[-1].file_id

    # Caso: Usuario envía foto de referencia inicial
    if udata.get("estado") == "usr_esperando_foto_vuelo":
        res = supabase.table("cotizaciones").insert({
            "user_id": str(uid), "username": update.effective_user.username or "SinUser",
            "pedido_completo": udata.get("tmp_datos"), "estado": "Esperando atención"
        }).execute()
        v_id = res.data[0]['id']
        await update.message.reply_text(f"✅ Recibido. ID: {v_id}")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"🔔 NUEVA SOLICITUD\nID: {v_id}\nUser: @{update.effective_user.username}\nInfo: {udata.get('tmp_datos')}")
        udata.clear()

    # Caso: Usuario envía COMPROBANTE DE PAGO (Aquí se genera el botón automático)
    elif udata.get("estado") == "usr_esperando_comprobante":
        v_id = udata.get("pago_vuelo_id")
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación"}).eq("id", v_id).execute()
        
        # Crear botón automático para el Admin
        btn_confirmar = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"Confirmar Pago ID {v_id} ✅", callback_data=f"conf_pago_{v_id}")
        ]])
        
        await update.message.reply_text("✅ Comprobante enviado. Revisaremos en breve.")
        await context.bot.send_photo(
            ADMIN_CHAT_ID, 
            fid, 
            caption=f"💰 **PAGO RECIBIDO**\nID Vuelo: `{v_id}`\nUser: @{update.effective_user.username}",
            reply_markup=btn_confirmar,
            parse_mode="Markdown"
        )
        udata.clear()

    # Caso: Admin envía QRs
    elif uid == ADMIN_CHAT_ID and udata.get("adm_estado") == "adm_enviando_qrs":
        v_id = udata.get("target_id_qr")
        udata["coleccion_fotos"].append(fid)
        if "job_envio" in udata: udata["job_envio"].cancel()
        async def task():
            await asyncio.sleep(1.5)
            user_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            # (Lógica de envío de QRs se mantiene igual...)
            await context.bot.send_message(user_res.data["user_id"], f"🎫 INSTRUCCIONES ID: {v_id}\n...")
            await context.bot.send_media_group(user_res.data["user_id"], [InputMediaPhoto(f) for f in udata["coleccion_fotos"]])
            supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
            await context.bot.send_message(ADMIN_CHAT_ID, f"✅ QRs ID {v_id} enviados.")
            udata.clear()
        udata["job_envio"] = asyncio.create_task(task())

# --- 6. CALLBACKS (CONFIRMACIÓN AUTOMÁTICA) ---

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_CHAT_ID: return

    # Lógica del botón automático enviado con la foto
    if query.data.startswith("conf_pago_"):
        v_id = query.data.split("_")[2]
        res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
        
        # Notificar al usuario
        target_user = res.data[0]['user_id']
        await context.bot.send_message(target_user, f"✅ Tu pago para el vuelo ID {v_id} ha sido confirmado. En breve recibirás tus pases.")
        
        # Actualizar mensaje del Admin
        await query.edit_message_caption(caption=f"✅ PAGO CONFIRMADO\nID Vuelo: {v_id}")

    elif query.data == "adm_pend":
        res = supabase.table("cotizaciones").select("*").neq("estado", "QR Enviados").order("username", desc=False).execute()
        msj = "📊 **PENDIENTES**\n\n"
        for v in res.data: msj += f"👤 @{v['username']}\n🆔 {v['id']} - {v['estado']}\n\n"
        await query.message.reply_text(msj)

    elif query.data == "adm_his":
        res = supabase.table("cotizaciones").select("*").order("username", desc=False).execute()
        msj = "📜 **HISTORIAL**\n"
        for v in res.data: msj += f"👤 @{v['username']} | ID {v['id']}: {v['estado']}\n"
        await query.message.reply_text(msj[:4000])

# --- 7. ARRANQUE ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", lambda u, c: c.bot.send_message(ADMIN_CHAT_ID, "🛠 Panel Admin", reply_markup=get_admin_keyboard())))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
