import logging
import os
import threading
from flask import Flask
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

# --- CONFIGURACIÓN DE FLASK PARA RENDER ---
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is alive and healthy!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

def keep_alive():
    threading.Thread(target=run).start()

# =========================
# 🔧 CONFIGURACIÓN
# =========================
# Se obtienen de las variables de entorno de Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

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
    "• **No revisar en lo absoluto el vuelo.**\n"
    "• **Solo dejar guardada la foto** en tu galería."
)

logging.basicConfig(level=logging.INFO)

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
        "1️⃣ Toca 'Enviar datos de vuelo' y escribe los detalles.\n"
        "2️⃣ Luego envía la foto del vuelo o comprobante.\n"
        "3️⃣ El sistema guardará todo automáticamente."
    )
    await update.message.reply_text(mensaje_flujo, reply_markup=ReplyKeyboardMarkup(teclado, resize_keyboard=True), parse_mode="Markdown")

# =========================
# ✍️ MANEJO DE TEXTO
# =========================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    texto = update.message.text.strip()

    # Filtros de botones del menú
    if texto == "📝 Enviar datos de vuelo":
        context.user_data["esperando"] = "datos_vuelo"
        await update.message.reply_text("Escribe los detalles de tu vuelo (Origen, Destino, Fecha):")
        return
    if texto == "📸 Enviar Imagen/Pago":
        context.user_data["esperando"] = "pago"
        await update.message.reply_text("Adjunta la imagen de tu comprobante:")
        return
    if texto == "🏦 Ver Datos de Pago":
        await update.message.reply_text(CUENTA_BANCARIA, parse_mode="Markdown")
        return
    if texto == "📞 Soporte":
        await update.message.reply_text("Contacto: @Soporte_Vuelos")
        return

    # Si es un usuario enviando información de vuelo
    if update.effective_chat.id != ADMIN_CHAT_ID:
        # Guardamos el texto temporalmente en memoria del bot
        context.user_data["temp_text"] = texto
        await update.message.reply_text("✅ Texto recibido. Ahora **envía la imagen** para completar el envío al admin.")

# =========================
# 📸 MANEJO DE IMÁGENES (FLUJO AUTOMÁTICO)
# =========================
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = update.message
    uid = msg.from_user.id
    user_name = f"@{msg.from_user.username}" if msg.from_user.username else msg.from_user.first_name

    if msg.photo: file_id = msg.photo[-1].file_id
    elif msg.document and msg.document.mime_type.startswith("image/"): file_id = msg.document.file_id
    else: return

    # --- FLUJO USUARIO ---
    if chat_id != ADMIN_CHAT_ID:
        # Recuperamos el texto guardado anteriormente
        texto_vuelo = context.user_data.get("temp_text", "comprobante" if not msg.caption else msg.caption)
        
        # 1. GUARDAR EN SUPABASE AUTOMÁTICAMENTE
        try:
            res = supabase.table("cotizaciones").insert({
                "user_id": uid,
                "username": user_name,
                "pedido_completo": texto_vuelo,
                "monto": "Pendiente",
                "estado": "Pendiente"
            }).execute()
            
            vuelo_id = res.data[0]['id']
            
            # 2. ENVIAR AL ADMIN (TEXTO + IMAGEN JUNTO)
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Eliminar Vuelo", callback_data=f"del_{vuelo_id}")]])
            
            await context.bot.send_photo(
                ADMIN_CHAT_ID, 
                file_id, 
                caption=f"🚀 **NUEVA SOLICITUD (ID: {vuelo_id})**\n👤 {user_name}\n🆔 `{uid}`\n📝 {texto_vuelo}",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            
            await msg.reply_text(f"✅ ¡Todo enviado! Tu solicitud ha sido registrada con el ID: {vuelo_id}")
            context.user_data.clear() # Limpiar memoria temporal
            
        except Exception as e:
            logging.error(f"Error: {e}")
            await msg.reply_text("Hubo un error al guardar los datos.")

# =========================
# 🔘 CALLBACKS (ELIMINAR)
# =========================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("del_"):
        vuelo_id = query.data.split("_")[1]
        supabase.table("cotizaciones").delete().eq("id", vuelo_id).execute()
        await query.edit_message_caption(caption=f"🗑️ Vuelo ID {vuelo_id} eliminado de la base de datos.")

def main():
    # Iniciar mantenimiento de vida para Render
    keep_alive()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Bot activo...")
    app.run_polling()

if __name__ == "__main__":
    main()
