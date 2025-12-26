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
def home(): return "Servidor Multi-Transacción Activo 🚀"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

# --- 2. CONFIGURACIÓN ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

# --- 3. TECLADOS ---

def get_user_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
        [KeyboardButton("📜 Mis Pedidos"), KeyboardButton("🏦 Datos de Pago")]
    ], resize_keyboard=True)

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Cotizar ID", callback_data="adm_cot"),
         InlineKeyboardButton("✅ Confirmar Pago ID", callback_data="adm_conf")],
        [InlineKeyboardButton("📊 Ver Todos los Pendientes", callback_data="adm_his")]
    ])

# --- 4. FUNCIONES DE USUARIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ **Sistema de Gestión de Vuelos**\nBienvenido. Selecciona una opción:",
        reply_markup=get_user_keyboard(),
        parse_mode="Markdown"
    )

async def mostrar_pedidos_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Traemos todos los registros de este usuario específico
    res = supabase.table("cotizaciones").select("*").eq("user_id", uid).order("id", desc=True).execute()
    
    if not res.data:
        await update.message.reply_text("📭 No tienes pedidos registrados.")
        return

    await update.message.reply_text("📋 **Tus pedidos actuales:**")
    for v in res.data:
        info = (f"🆔 ID Pedido: `{v['id']}`\n"
                f"📝 Detalle: {v['pedido_completo']}\n"
                f"💰 Monto: {v['monto']}\n"
                f"📍 Estado: {v['estado']}")
        await update.message.reply_text(info, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    # --- BOTONES USUARIO ---
    if texto == "📝 Datos de vuelo":
        udata["modo"] = "vuelo"
        await update.message.reply_text("Escribe: Origen, Destino y Fecha:")
    elif texto == "📸 Enviar Pago":
        udata["modo"] = "pago"
        await update.message.reply_text("Envía la foto de tu comprobante:")
    elif texto == "📜 Mis Pedidos":
        await mostrar_pedidos_usuario(update, context)
    elif texto == "🏦 Datos de Pago":
        await update.message.reply_text("🏦 **BBVA**\nCLABE: `012180015886058959`\nTitular: Antonio Garcia", parse_mode="Markdown")

    # --- LÓGICA DE ADMIN (USANDO ID DE REGISTRO) ---
    elif uid == ADMIN_CHAT_ID:
        if udata.get("adm_state") == "wait_id_cot":
            udata["target_row_id"] = texto
            udata["adm_state"] = "wait_monto"
            await update.message.reply_text(f"Pedido `{texto}` seleccionado. Escribe el **monto**:")
        
        elif udata.get("adm_state") == "wait_monto":
            row_id = udata["target_row_id"]
            try:
                # Buscamos al usuario dueño de ese pedido ID
                res = supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", row_id).execute()
                user_to_notify = res.data[0]["user_id"]
                await context.bot.send_message(user_to_notify, f"💰 Tu pedido ID `{row_id}` ha sido cotizado: **{texto}**", parse_mode="Markdown")
                await update.message.reply_text(f"✅ Enviado al usuario `{user_to_notify}`.")
                udata.clear()
            except: await update.message.reply_text("❌ Error: ID de pedido no válido.")

        elif udata.get("adm_state") == "wait_id_conf":
            try:
                res = supabase.table("cotizaciones").update({"estado": "✅ Pagado"}).eq("id", texto).execute()
                user_to_notify = res.data[0]["user_id"]
                await context.bot.send_message(user_to_notify, f"✅ Pago confirmado para el pedido ID `{texto}`.")
                await update.message.reply_text(f"✅ Pedido `{texto}` marcado como PAGADO.")
                udata.clear()
            except: await update.message.reply_text("❌ Error: ID de pedido no válido.")

    # Guardar info de vuelo antes de la foto
    elif udata.get("modo") == "vuelo":
        udata["temp_info"] = texto
        await update.message.reply_text("✅ Datos anotados. Envía la foto para completar la **Cotización de vuelo**.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return
    uid = update.effective_user.id
    uname = f"@{update.effective_user.username}" or "Usuario"
    fid = update.message.photo[-1].file_id
    udata = context.user_data

    # Diferenciación automática de etiquetas
    if udata.get("modo") == "vuelo":
        etiqueta = "Cotización de vuelo"
        descripcion = f"{etiqueta}: {udata.get('temp_info', 'Sin detalles')}"
    else:
        # Por defecto o si eligió "Enviar Pago", es comprobante [Instrucción 2025-12-24]
        etiqueta = "Comprobante de pago"
        descripcion = etiqueta

    try:
        # Insertar nuevo registro (permite múltiples filas por usuario)
        res = supabase.table("cotizaciones").insert({
            "user_id": uid, "username": uname,
            "pedido_completo": descripcion, "monto": "Pendiente", "estado": "Pendiente"
        }).execute()
        
        row_id = res.data[0]['id']
        
        # Notificación al ADMIN (Todo copiable)
        admin_msg = (f"🔔 **{etiqueta.upper()}**\n"
                    f"🆔 Pedido: `{row_id}`\n"
                    f"👤 Usuario: `{uid}`\n"
                    f"📝 Info: {descripcion}")
        
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=admin_msg, parse_mode="Markdown")
        await update.message.reply_text(f"✅ Recibido como: {etiqueta}\n🆔 Tu ID de pedido es: `{row_id}`", parse_mode="Markdown")
        udata.clear()
    except Exception as e:
        logging.error(f"Error: {e}")

# --- 5. COMANDOS ADMIN ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    await update.message.reply_text("🛠 **Panel Admin**", reply_markup=get_admin_keyboard())

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_CHAT_ID: return

    if query.data == "adm_cot":
        context.user_data["adm_state"] = "wait_id_cot"
        await query.message.reply_text("Escribe el **ID de Pedido**:")
    
    elif query.data == "adm_conf":
        context.user_data["adm_state"] = "wait_id_conf"
        await query.message.reply_text("Escribe el **ID de Pedido** a confirmar:")

    elif query.data == "adm_his":
        # Ver últimos 10 pendientes de cualquier usuario
        res = supabase.table("cotizaciones").select("*").neq("estado", "✅ Pagado").limit(10).execute()
        if not res.data:
            await query.message.reply_text("No hay pendientes.")
            return
        for v in res.data:
            info = f"🆔 `{v['id']}` | 👤 `{v['user_id']}`\n📍 {v['estado']}\n📝 {v['pedido_completo']}"
            await context.bot.send_message(ADMIN_CHAT_ID, info, parse_mode="Markdown")

# --- 6. EJECUCIÓN ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    app.run_polling(drop_pending_updates=True)
