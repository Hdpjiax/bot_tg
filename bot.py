import logging
import os
import threading
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

# --- 1. SERVIDOR KEEP-ALIVE ---
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
SOPORTE_USER = "@TuUsuarioSoporte" # Cambia esto

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
         InlineKeyboardButton("📊 Ver Pendientes", callback_data="adm_his")]
    ])

# --- 4. LÓGICA DE USUARIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Sistema de Gestión de Vuelos**\nBienvenido. Usa el menú para comenzar:",
        reply_markup=get_user_keyboard(),
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    # --- BOTONES USUARIO ---
    if texto == "📝 Datos de vuelo":
        udata["estado"] = "usr_esperando_datos"
        await update.message.reply_text("Escribe los detalles de tu vuelo (Origen, Destino, Fecha):")

    elif texto == "📸 Enviar Pago":
        udata["estado"] = "usr_esperando_id_pago"
        await update.message.reply_text("Escribe el **ID del vuelo** que vas a pagar:")

    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")

    elif texto == "🆘 Soporte":
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Ir a Soporte 💬", url=f"https://t.me/{SOPORTE_USER.replace('@','')}")]])
        await update.message.reply_text("¿Necesitas ayuda?", reply_markup=btn)

    elif texto == "📜 Mis Pedidos":
        res = supabase.table("cotizaciones").select("*").eq("user_id", uid).execute()
        if not res.data:
            await update.message.reply_text("No tienes pedidos registrados.")
            return
        for v in res.data:
            await update.message.reply_text(f"🆔 ID: `{v['id']}`\n📍 Estado: {v['estado']}\n💰 Monto: {v['monto']}", parse_mode="Markdown")

    # --- RESPUESTAS DE ESTADO ---
    elif udata.get("estado") == "usr_esperando_datos":
        udata["tmp_datos"] = texto
        udata["estado"] = "usr_esperando_foto_vuelo"
        await update.message.reply_text("✅ Datos recibidos. Ahora envía una imagen de referencia.")

    elif udata.get("estado") == "usr_esperando_id_pago":
        res = supabase.table("cotizaciones").select("*").eq("id", texto).eq("user_id", uid).execute()
        if res.data:
            monto = res.data[0]['monto']
            udata["pago_id"] = texto
            udata["estado"] = "usr_esperando_comprobante"
            msg = (f"💳 **Pago para ID `{texto}`**\n"
                   f"Monto: **{monto}**\n\n"
                   f"🏦 BBVA - CLABE: `012180015886058959` - Antonio Garcia\n\n"
                   f"👉 Envía la captura del pago ahora.")
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ ID no válido.")

    # --- LÓGICA ADMIN (TEXTO) ---
    elif uid == ADMIN_CHAT_ID:
        if udata.get("adm_estado") == "adm_esp_id_cot":
            udata["target_id"] = texto
            udata["adm_estado"] = "adm_esp_monto"
            await update.message.reply_text(f"ID `{texto}` seleccionado. Escribe el monto:")
        
        elif udata.get("adm_estado") == "adm_esp_monto":
            v_id = udata["target_id"]
            supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", v_id).execute()
            v_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            await context.bot.send_message(v_res.data["user_id"], f"💰 Tu vuelo ID `{v_id}` ha sido cotizado: **{texto}**")
            await update.message.reply_text("✅ Cotización enviada.")
            udata.clear()

        elif udata.get("adm_estado") == "adm_esp_id_qr":
            udata["target_id_qr"] = texto
            udata["adm_estado"] = "adm_enviando_qrs"
            await update.message.reply_text(f"✅ ID `{texto}` listo. Cada foto que envíes ahora llegará al usuario.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    uid = update.effective_user.id
    fid = update.message.photo[-1].file_id
    udata = context.user_data

    # 1. Registro inicial (Vuelo)
    if udata.get("estado") == "usr_esperando_foto_vuelo":
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": update.effective_user.username,
            "pedido_completo": f"Cotización de vuelo: {udata.get('tmp_datos')}",
            "monto": "Pendiente", "estado": "Esperando atención"
        }).execute()
        v_id = res.data[0]['id']
        await update.message.reply_text(f"✅ Cotización enviada (ID: `{v_id}`). Espera a ser atendido.")
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"🔔 **NUEVA SOLICITUD**\nID: `{v_id}`\nUser: `{uid}`\nInfo: {udata.get('tmp_datos')}", parse_mode="Markdown")
        udata.clear()

    # 2. Comprobante de Pago (Actualiza registro)
    elif udata.get("estado") == "usr_esperando_comprobante":
        v_id = udata.get("pago_id")
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación de pago"}).eq("id", v_id).execute()
        await update.message.reply_text(f"✅ Comprobante enviado para el ID `{v_id}`. Validando...")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Confirmar Pago ✅", callback_data=f"conf_direct_{v_id}")]])
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=f"💰 **COMPROBANTE RECIBIDO**\nID Vuelo: `{v_id}`", reply_markup=btn, parse_mode="Markdown")
        udata.clear()

    # 3. Admin enviando QRs (Reenvío automático)
    elif uid == ADMIN_CHAT_ID and udata.get("adm_estado") == "adm_enviando_qrs":
        v_id = udata.get("target_id_qr")
        res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
        u_id = res.data["user_id"]
        
        # Enviar foto al usuario
        await context.bot.send_photo(u_id, fid, caption=f"🎫 Pase de abordar - Vuelo ID: `{v_id}`")
        
        # Si es la primera foto del lote, mandamos instrucciones y cambiamos estado
        if udata.get("qr_enviado") != v_id:
            instrucciones = (
                "✈️ **¡Disfruta tu vuelo!**\n\n"
                "**Instrucciones de seguridad:**\n"
                "- No agregar a la app.\n"
                "- No revisar el vuelo; validación 2h antes si es necesario.\n"
                "- En caso de caída, se reasigna al siguiente horario.\n"
                "- Guarda los pases en tu galería para escanear."
            )
            await context.bot.send_message(u_id, instrucciones, parse_mode="Markdown")
            supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
            udata["qr_enviado"] = v_id # Para no repetir instrucciones en el mismo lote

# --- 5. CALLBACKS ADMIN ---

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_CHAT_ID: return

    if query.data == "adm_cot":
        context.user_data["adm_estado"] = "adm_esp_id_cot"
        await query.message.reply_text("ID del vuelo para cotizar:")

    elif query.data == "adm_qr":
        context.user_data["adm_estado"] = "adm_esp_id_qr"
        await query.message.reply_text("ID del vuelo para enviar QRs:")

    elif query.data.startswith("conf_direct_"):
        v_id = query.data.split("_")[2]
        res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
        await context.bot.send_message(res.data[0]["user_id"], f"✅ Tu pago para el ID `{v_id}` ha sido confirmado. Espera tus QRs.")
        await query.edit_message_caption(caption=f"✅ Pago Confirmado ID `{v_id}`")

    elif query.data == "adm_his":
        res = supabase.table("cotizaciones").select("*").neq("estado", "QR Enviados").limit(10).execute()
        for v in res.data:
            await context.bot.send_message(ADMIN_CHAT_ID, f"🆔 `{v['id']}` | 👤 `{v['user_id']}`\n📍 {v['estado']}", parse_mode="Markdown")

# --- 6. ARRANQUE ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", lambda u, c: c.bot.send_message(ADMIN_CHAT_ID, "🛠 Panel Admin", reply_markup=get_admin_keyboard())))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(drop_pending_updates=True)
