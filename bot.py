import logging
import os
import threading
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
         InlineKeyboardButton("✅ Confirmar Pago", callback_data="adm_conf_list")],
        [InlineKeyboardButton("🖼️ Enviar QRs", callback_data="adm_qr"),
         InlineKeyboardButton("⏳ Pendientes (5d)", callback_data="adm_pend")],
        [InlineKeyboardButton("📜 Historial Total", callback_data="adm_his")]
    ])

# --- 4. FUNCIONES DE APOYO ---

def get_date_range():
    hoy = datetime.now().date()
    futuro = hoy + timedelta(days=5)
    return hoy.isoformat(), futuro.isoformat()

# --- 5. LÓGICA DE USUARIO ---

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    if texto == "📝 Datos de vuelo":
        udata["estado"] = "usr_esperando_datos"
        await update.message.reply_text("Escribe: Origen, Destino y Fecha (YYYY-MM-DD):")

    elif texto == "📸 Enviar Pago":
        udata["estado"] = "usr_esperando_id_pago"
        await update.message.reply_text("Escribe el **ID del vuelo**:")

    # ... (Resto de botones de usuario igual que antes)

    elif udata.get("estado") == "usr_esperando_datos":
        # Intentamos extraer la fecha si el usuario la pone
        udata["tmp_datos"] = texto
        udata["estado"] = "usr_esperando_foto_vuelo"
        await update.message.reply_text("✅ Recibido. Envía la imagen de referencia.")

    # LÓGICA ADMIN
    elif uid == ADMIN_CHAT_ID:
        if udata.get("adm_estado") == "adm_esp_id_qr":
            udata["target_id_qr"] = texto
            udata["adm_estado"] = "adm_enviando_qrs"
            await update.message.reply_text(f"✅ ID `{texto}` seleccionado.\nEnvía las fotos (individual o álbum). Las enviaré con el formato: Instrucciones -> QRs -> Disfruta.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    udata = context.user_data
    
    # Manejo de fotos (incluyendo álbumes/MediaGroups)
    if update.message.photo:
        fid = update.message.photo[-1].file_id

        # 1. Registro de Vuelo
        if udata.get("estado") == "usr_esperando_foto_vuelo":
            # Guardamos datos incluyendo la fecha si se detecta
            res = supabase.table("cotizaciones").insert({
                "user_id": uid, "username": update.effective_user.username,
                "pedido_completo": udata.get('tmp_datos'),
                "estado": "Esperando atención"
            }).execute()
            await update.message.reply_text(f"✅ Enviado (ID: `{res.data[0]['id']}`).")
            await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"🔔 **NUEVA SOLICITUD**\nID: `{res.data[0]['id']}`\nInfo: {udata.get('tmp_datos')}")
            udata.clear()

       # 2. Comprobante de Pago
        elif udata.get("estado") == "usr_esperando_comprobante":
            v_id = udata.get("pago_id")
            # Aplicando tu instrucción: si no hay texto, poner 'comprobante'
            caption_text = "comprobante" if not update.message.caption else update.message.caption
            
            supabase.table("cotizaciones").update({"estado": "Esperando confirmación"}).eq("id", v_id).execute()
            await update.message.reply_text(f"✅ Comprobante enviado para el ID `{v_id}`.")
            
            # Botón de confirmación rápida y ID copiable para el Admin
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("Confirmar Pago ✅", callback_data=f"conf_direct_{v_id}")]])
            await context.bot.send_photo(
                ADMIN_CHAT_ID, 
                fid, 
                caption=f"💰 **PAGO RECIBIDO**\n\n"
                        f"🆔 ID Vuelo: `{v_id}` (Copiable)\n"
                        f"👤 Usuario: `{uid}`\n"
                        f"💬 Nota: {caption_text}", 
                reply_markup=btn,
                parse_mode="Markdown"
            )
            udata.clear()

        # 3. Admin enviando QRs (FLUJO: Instrucciones -> Foto -> Disfruta)
        elif uid == ADMIN_CHAT_ID and udata.get("adm_estado") == "adm_enviando_qrs":
            v_id = udata.get("target_id_qr")
            res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            u_id = res.data["user_id"]

            # 1. Enviar Instrucciones
            instrucciones = "✈️ **INSTRUCCIONES DE SEGURIDAD**\n- No agregar a la app.\n- No revisar el vuelo antes de tiempo.\n- Solo validación 2h antes."
            await context.bot.send_message(u_id, instrucciones, parse_mode="Markdown")
            
            # 2. Enviar el QR (Foto)
            await context.bot.send_photo(u_id, fid)
            
            # 3. Enviar mensaje final
            await context.bot.send_message(u_id, "🎫 **¡Disfruta tu vuelo!**")
            
            supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
            await update.message.reply_text("✅ QR enviado con éxito.")
# --- 5. LÓGICA DE USUARIO ---

# ESTA FUNCIÓN ES LA QUE FALTABA DEFINIR:
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Sistema de Gestión de Vuelos**\nBienvenido. Selecciona una opción del menú:",
        reply_markup=get_user_keyboard(),
        parse_mode="Markdown"
    )
# --- 6. CALLBACKS ADMIN ---

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_CHAT_ID: return

    # --- BOTÓN PENDIENTES ---
    if query.data == "adm_pend":
        res = supabase.table("cotizaciones").select("*")\
            .filter("estado", "in", '("Cotizado", "Pago Confirmado", "Esperando confirmación")')\
            .execute()
        
        if not res.data:
            await query.message.reply_text("No hay pendientes urgentes.")
            return

        msj = "⏳ **PRÓXIMOS PENDIENTES**\n\n"
        for v in res.data:
            # Datos formateados para ser copiables
            msj += (f"🆔 ID Vuelo: `{v['id']}`\n"
                    f"👤 User ID: `{v['user_id']}`\n"
                    f"👤 @{v['username']}\n"
                    f"📍 Estado: {v['estado']}\n"
                    f"📝 Info: {v['pedido_completo']}\n\n"
                    f"-------------------\n")
        await query.message.reply_text(msj, parse_mode="Markdown")

    # --- BOTÓN HISTORIAL TOTAL ---
    elif query.data == "adm_his":
        res = supabase.table("cotizaciones").select("*").order("id", desc=True).limit(20).execute()
        msj = "📜 **HISTORIAL RECIENTE**\n\n"
        for v in res.data:
            msj += (f"🆔 ID Vuelo: `{v['id']}`\n"
                    f"👤 User ID: `{v['user_id']}`\n"
                    f"👤 Username: `@{v['username']}`\n"
                    f"📝 Info: {v['pedido_completo']}\n"
                    f"✅ Estado: {v['estado']}\n"
                    f"-------------------\n")
        
        if len(msj) > 4000:
            await query.message.reply_text(msj[:4000], parse_mode="Markdown")
        else:
            await query.message.reply_text(msj, parse_mode="Markdown")

    # ... (Resto de callbacks: adm_cot, adm_qr, etc.)

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

