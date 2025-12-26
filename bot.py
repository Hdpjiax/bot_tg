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
SOPORTE_USER = "@TuUsuarioSoporte" # Cambia esto por tu user real

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
        [InlineKeyboardButton("💰 Cotizar Vuelo", callback_data="adm_cot"),
         InlineKeyboardButton("✅ Confirmar Pago", callback_data="adm_conf")],
        [InlineKeyboardButton("🖼️ Enviar QRs", callback_data="adm_qr"),
         InlineKeyboardButton("📊 Ver Pendientes", callback_data="adm_pend")]
    ])

# --- 4. LÓGICA DE USUARIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Bienvenido al Sistema de Vuelos**\nUsa el menú inferior para gestionar tus trámites.",
        reply_markup=get_user_keyboard(),
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    # BOTONES PRINCIPALES
    if texto == "📝 Datos de vuelo":
        udata["estado"] = "usr_esperando_datos"
        await update.message.reply_text("Escribe el Origen, Destino y Fecha de tu vuelo:")

    elif texto == "📸 Enviar Pago":
        udata["estado"] = "usr_esperando_id_pago"
        await update.message.reply_text("Escribe el **ID del vuelo** que vas a pagar:")

    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **Datos de Transferencia**\n\nBBVA\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")

    elif texto == "🆘 Soporte":
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Contactar Soporte 💬", url=f"https://t.me/{SOPORTE_USER.replace('@','')}")]])
        await update.message.reply_text("Haz clic abajo para hablar con un agente:", reply_markup=btn)

    # MANEJO DE ESTADOS (FLUJO TEXTO)
    elif udata.get("estado") == "usr_esperando_datos":
        udata["tmp_datos"] = texto
        udata["estado"] = "usr_esperando_foto_vuelo"
        await update.message.reply_text("✅ Datos recibidos. Ahora envía una **imagen de referencia** del vuelo.")

    elif udata.get("estado") == "usr_esperando_id_pago":
        # Buscamos el monto en la DB
        res = supabase.table("cotizaciones").select("monto").eq("id", texto).execute()
        if res.data:
            udata["pago_vuelo_id"] = texto
            udata["estado"] = "usr_esperando_comprobante"
            monto = res.data[0]['monto']
            msg = (f"💳 **Pago para Vuelo ID:** `{texto}`\n"
                   f"💰 **Monto a pagar:** {monto}\n\n"
                   f"🏦 BBVA - CLABE: `012180015886058959`\n\n"
                   f"👉 Envía la **captura del pago** ahora.")
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ ID de vuelo no encontrado. Revisa el número en 'Mis Pedidos'.")

    # LÓGICA ADMIN (TEXTO)
    elif uid == ADMIN_CHAT_ID:
        if udata.get("adm_estado") == "adm_esp_id_cot":
            udata["target_id"] = texto
            udata["adm_estado"] = "adm_esp_monto"
            await update.message.reply_text(f"ID `{texto}` seleccionado. Escribe el **Monto total**: ")
        
        elif udata.get("adm_estado") == "adm_esp_monto":
            v_id = udata["target_id"]
            supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", v_id).execute()
            # Notificar al usuario
            user_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            await context.bot.send_message(user_res.data["user_id"], f"💰 Tu vuelo ID `{v_id}` ha sido cotizado.\n**Monto:** {texto}\n\nYa puedes proceder al pago en el botón 'Enviar Pago'.")
            await update.message.reply_text(f"✅ Cotización enviada al usuario para el ID `{v_id}`.")
            udata.clear()

        elif udata.get("adm_estado") == "adm_esp_id_qr":
            udata["target_id_qr"] = texto
            udata["adm_estado"] = "adm_enviando_qrs"
            await update.message.reply_text(f"✅ Listo para enviar QRs al ID `{texto}`. Envía las fotos o álbum ahora.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = f"@{update.effective_user.username}" if update.effective_user.username else "Sin User"
    udata = context.user_data
    if not update.message.photo: return
    fid = update.message.photo[-1].file_id

    # 1. USUARIO ENVÍA FOTO DE REFERENCIA (REGISTRO INICIAL)
    if udata.get("estado") == "usr_esperando_foto_vuelo":
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": update.effective_user.username,
            "pedido_completo": udata.get("tmp_datos"), "estado": "Esperando atención"
        }).execute()
        v_id = res.data[0]['id']
        await update.message.reply_text("✅ Se recibió su cotización. Por favor espere a que sea atendido.")
        
        # Notificar Admin
        cap = f"🔔 **NUEVA SOLICITUD**\nID: `{v_id}`\nUser: `{username}`\nID Telegram: `{uid}`\nInfo: {udata.get('tmp_datos')}"
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=cap, parse_mode="Markdown")
        udata.clear()

    # 2. USUARIO ENVÍA COMPROBANTE DE PAGO (ACTUALIZACIÓN)
    elif udata.get("estado") == "usr_esperando_comprobante":
        v_id = udata.get("pago_vuelo_id")
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación de pago"}).eq("id", v_id).execute()
        await update.message.reply_text(f"✅ Comprobante enviado para el ID `{v_id}`. En breve confirmaremos su pago.")
        
        # Notificar Admin con botón de confirmación automática
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Confirmar Pago ✅", callback_data=f"conf_direct_{v_id}")]])
        cap = f"💰 **COMPROBANTE RECIBIDO**\nID Vuelo: `{v_id}`\nUser: `{username}`\nID Telegram: `{uid}`"
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=cap, reply_markup=btn, parse_mode="Markdown")
        udata.clear()

    # 3. ADMIN ENVÍA QRS (ENVÍO AL USUARIO + CAMBIO ESTADO)
    elif uid == ADMIN_CHAT_ID and udata.get("adm_estado") == "adm_enviando_qrs":
        v_id = udata.get("target_id_qr")
        user_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
        target_uid = user_res.data["user_id"]

        # Enviar Instrucciones primero
        instrucciones = (
            "⚠️ **Instrucciones para evitar caídas:**\n\n"
            "- No agregar a la app.\n"
            "- No revisar en lo absoluto el vuelo. Validación 2h antes si se requiere.\n"
            "- En caso de caída, se saca vuelo en horario siguiente.\n"
            "- Solo deja la foto en tu galería para escanear."
        )
        await context.bot.send_message(target_uid, instrucciones)
        
        # Enviar el QR/Foto
        await context.bot.send_photo(target_uid, fid, caption=f"🎫 Pase de abordar - Vuelo ID: `{v_id}`")
        
        # Enviar mensaje final
        await context.bot.send_message(target_uid, "🎫 **¡Disfruta tu vuelo!**", parse_mode="Markdown")
        
        # Actualizar DB
        supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
        await update.message.reply_text(f"✅ QRs enviados con éxito al usuario del ID `{v_id}`.")

# --- 5. CALLBACKS ADMIN ---

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Confirmación de pago directa desde el botón de la foto
    if query.data.startswith("conf_direct_"):
        v_id = query.data.split("_")[2]
        res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
        u_id = res.data[0]['user_id']
        await context.bot.send_message(u_id, f"✅ Tu pago para el ID `{v_id}` ha sido confirmado. Por favor espera tus QRs.")
        await query.edit_message_caption(caption=f"✅ Pago Confirmado para ID `{v_id}`")

    elif query.data == "adm_cot":
        context.user_data["adm_estado"] = "adm_esp_id_cot"
        await query.message.reply_text("Introduce el **ID del vuelo** a cotizar:")
    
    elif query.data == "adm_qr":
        context.user_data["adm_estado"] = "adm_esp_id_qr"
        await query.message.reply_text("Introduce el **ID del vuelo** para enviar los QRs:")

# --- 6. ARRANQUE ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", lambda u, c: c.bot.send_message(ADMIN_CHAT_ID, "🛠 Panel Admin", reply_markup=get_admin_keyboard())))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
