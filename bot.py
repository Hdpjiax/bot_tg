import logging
import os
from flask import Flask
import threading
app = Flask('')
@app.route('/')
def home():
    return "Bot is alive!"

def run():
    # Render usa el puerto 10000 por defecto
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()
from supabase import create_client, Client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# =========================
# 🔧 CONFIGURACIÓN
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_ID", 7721918273))

# Conexión oficial
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

CUENTA_BANCARIA = (
    "🏦 *DATOS DE PAGO (Toca para copiar)*\n\n"
    "Banco: `BBVA`\n"
    "CLABE: `012180015886058959`\n"
    "Titular: `Antonio Garcia`\n"
    "Concepto: `Ropa`"
)

TEXTO_INSTRUCCIONES_QR = (
    "⚠️ **Instrucciones para evitar caídas:**\n\n"
    "Luego de tener tu código QR con tu pase:\n"
    "• **No agregar a la app.**\n"
    "• **No revisar en lo absoluto el vuelo**; solo si se requiere, se manda confirmación 2 horas antes del abordaje de que sigue en pie.\n"
    "• **En caso de caída:** Se sacaría un vuelo en el horario siguiente. Ejemplo: salida 3pm -> se sacaría salida 5 o 6pm.\n"
    "• **Solo dejar guardada la foto** de tu pase en tu galería para llegar al aeropuerto solo a escanear."
)

logging.basicConfig(level=logging.INFO)

# =========================
# 🧠 MEMORIA VOLÁTIL
# =========================
usuarios = {}     
last_text = {}    
albums = {}       

# =========================
# 🧠 AUXILIARES
# =========================
def get_user(uid):
    if uid not in usuarios:
        usuarios[uid] = {"historial": [], "estado": "inicio", "intent": None}
    return usuarios[uid]

def log(uid, texto):
    user_data = get_user(uid)
    user_data["historial"].append(texto)

# =========================
# 🚀 COMANDO START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        [KeyboardButton("📝 Enviar datos de vuelo"), KeyboardButton("📸 Enviar Imagen/Pago")],
        [KeyboardButton("🏦 Ver Datos de Pago"), KeyboardButton("📞 Soporte")]
    ]
    mensaje_flujo = (
        "✈️ **¡Bienvenido!**\n\n"
        "1️⃣ Enviar Texto 📝\n2️⃣ Enviar Imagen 📸\n3️⃣ Esperar Cotización ⏳\n"
        "4️⃣ Recibir Cotización 💰\n5️⃣ Mandar Comprobante 💳\n6️⃣ Esperar Confirmación ✅\n"
        "7️⃣ Esperar QR 🎫\n8️⃣ Entrega de QR ✈️"
    )
    await update.message.reply_text(mensaje_flujo, reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True), parse_mode="Markdown")

# =========================
# 👨‍💼 PANEL ADMIN
# =========================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID: return
    botones = [
        [InlineKeyboardButton("💰 Cotizar", callback_data="cotizar")],
        [InlineKeyboardButton("✅ Confirmar Pago", callback_data="confirmar_pago")],
        [InlineKeyboardButton("📤 Enviar QR", callback_data="reenviar_qr")],
        [InlineKeyboardButton("📜 Historial", callback_data="historial")]
    ]
    await update.message.reply_text("🛠 **Panel Admin**", reply_markup=InlineKeyboardMarkup(botones))

# =========================
# 🔘 CALLBACKS
# =========================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("conf_"):
        target_uid = int(data.split("_")[1])
        
        # ACTUALIZAR EN SUPABASE
        supabase.table("cotizaciones").update({"estado": "Pagado"}).eq("user_id", target_uid).eq("estado", "Pendiente").execute()

        await context.bot.send_message(target_uid, "✅ **¡Pago recibido con éxito!**\nEstamos procesando tus QR.", parse_mode="Markdown")
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n🟢 **PAGO CONFIRMADO**", reply_markup=None)
        return

    context.user_data["accion"] = data
    await query.message.reply_text(f"✏️ Acción: {data.upper()}\nEnvía ID del usuario.")

# =========================
# ✍️ MANEJO DE TEXTO
# =========================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.message.from_user.id
    texto = update.message.text.strip()
    session = get_user(uid)

    if texto == "📝 Enviar datos de vuelo":
        session["intent"] = "cotizacion"
        await update.message.reply_text("Escribe los detalles de tu vuelo (Origen, Destino, Fecha):")
        return
    if texto == "📸 Enviar Imagen/Pago":
        session["intent"] = "pago"
        await update.message.reply_text("Adjunta la imagen de tu comprobante:")
        return
    if texto == "🏦 Ver Datos de Pago":
        await update.message.reply_text(CUENTA_BANCARIA, parse_mode="Markdown")
        return
    if texto == "📞 Soporte":
        await update.message.reply_text("Contacto: @Soporte_Vuelos")
        return

    if chat_id != ADMIN_CHAT_ID:
        last_text[uid] = texto
        tipo = "✈️ SOLICITUD DE COTIZACIÓN" if session["intent"] == "cotizacion" else "📎 MENSAJE"
        await context.bot.send_message(ADMIN_CHAT_ID, f"{tipo}\n👤 @{update.message.from_user.username}\n🆔 `{uid}`\n📝 {texto}", parse_mode="Markdown")
        log(uid, f"Texto: {texto}")
        return

    if "accion" not in context.user_data: return
    accion = context.user_data.pop("accion")
    try:
        partes = texto.split()
        target_uid = int(partes[0])
        if accion == "cotizar":
            monto = partes[1]
            get_user(target_uid)["estado"] = "esperando_pago"
            
            # GUARDAR EN SUPABASE
            supabase.table("cotizaciones").insert({
                "user_id": target_uid, 
                "username": "User", 
                "monto": monto, 
                "estado": "Pendiente"
            }).execute()

            await context.bot.send_message(target_uid, f"✈️ **Cotización Lista**\nTotal: **${monto} MXN**\n\n{CUENTA_BANCARIA}", parse_mode="Markdown")
            await update.message.reply_text(f"✅ Cotización enviada a {target_uid}")
            
        elif accion == "reenviar_qr":
            if not albums: return
            mid = list(albums.keys())[-1]
            fotos = albums.pop(mid)
            media = [InputMediaPhoto(f, caption="🎫 **Tus pases de abordar**" if i == 0 else "") for i, f in enumerate(fotos)]
            await context.bot.send_media_group(chat_id=target_uid, media=media)
            await context.bot.send_message(chat_id=target_uid, text=TEXTO_INSTRUCCIONES_QR, parse_mode="Markdown")
            await update.message.reply_text(f"✅ QR enviado a {target_uid}")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

# =========================
# 📸 MANEJO DE IMÁGENES
# =========================
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = update.message
    uid = msg.from_user.id
    session = get_user(uid)

    if msg.photo: file_id = msg.photo[-1].file_id
    elif msg.document and msg.document.mime_type.startswith("image/"): file_id = msg.document.file_id
    else: return

    if chat_id != ADMIN_CHAT_ID:
        if session["intent"] == "pago" or (session.get("estado") == "esperando_pago" and uid not in last_text):
            texto_f, tipo = "comprobante", "💰 COMPROBANTE DE PAGO"
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Confirmar Pago", callback_data=f"conf_{uid}")]])
        else:
            texto_f = last_text.pop(uid) if uid in last_text else "Sin descripción"
            tipo = "✈️ IMAGEN DE REFERENCIA" if session["intent"] == "cotizacion" else "📸 IMAGEN"
            markup = None
        
        await context.bot.send_photo(ADMIN_CHAT_ID, file_id, caption=f"{tipo}\n👤 @{msg.from_user.username}\n🆔 `{uid}`\n📝 {texto_f}", reply_markup=markup, parse_mode="Markdown")
        await msg.reply_text("✅ Recibido. Procesando...")
    else:
        if msg.media_group_id: albums.setdefault(msg.media_group_id, []).append(file_id)
        else: albums[f"s_{msg.message_id}"] = [file_id]

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    if __name__ == '__main__':
        try:
            keep_alive()  # Esto ya funciona y mantiene vivo el bot
             print("Servidor web iniciado...")
            print("Servidor web iniciado...")
            application.run_polling()
        except Exception as e:
            print(f"Error al arrancar: {e}")
if __name__ == "__main__":
    main()







