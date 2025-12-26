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
         InlineKeyboardButton("📊 Ver Pendientes", callback_data="adm_pend")],
        [InlineKeyboardButton("📜 Historial Total", callback_data="adm_his")]
    ])

# --- 4. FUNCIONES DE APOYO ---

def get_date_range_5d():
    hoy = datetime.now()
    futuro = hoy + timedelta(days=5)
    return hoy.strftime('%Y-%m-%d'), futuro.strftime('%Y-%m-%d')

# --- 5. LÓGICA DE USUARIO ---

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

    # --- BOTÓN MIS PEDIDOS (CORREGIDO) ---
    if texto == "📜 Mis Pedidos":
        # Filtramos estrictamente por el ID del usuario que presiona el botón
        res = supabase.table("cotizaciones").select("*").eq("user_id", str(uid)).order("created_at", desc=True).execute()
        
        if not res.data:
            await update.message.reply_text("❌ No tienes vuelos registrados con este ID de Telegram.")
            return
        
        msj = "📜 **TUS VUELOS Y COTIZACIONES**\n\n"
        for v in res.data:
            msj += (f"🆔 **ID de Vuelo:** `{v['id']}`\n"
                    f"📍 **Estatus:** {v['estado']}\n"
                    f"📝 **Datos:** {v['pedido_completo']}\n"
                    f"💰 **Monto:** {v.get('monto', 'Pendiente de cotizar')}\n"
                    f"📅 **Fecha Registro:** {v['created_at'][:10]}\n"
                    f"--------------------------\n")
        
        # Dividir mensaje si es muy largo
        if len(msj) > 4000:
            for i in range(0, len(msj), 4000):
                await update.message.reply_text(msj[i:i+4000], parse_mode="Markdown")
        else:
            await update.message.reply_text(msj, parse_mode="Markdown")

    elif texto == "📝 Datos de vuelo":
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

    # --- MANEJO DE ESTADOS ---
    elif udata.get("estado") == "usr_esperando_datos":
        udata["tmp_datos"] = texto
        udata["estado"] = "usr_esperando_foto_vuelo"
        await update.message.reply_text("✅ Datos recibidos. Ahora envía una **imagen de referencia** del vuelo.")

    elif udata.get("estado") == "usr_esperando_id_pago":
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
            await update.message.reply_text("❌ ID de vuelo no encontrado.")

    # --- LÓGICA ADMIN TEXTO ---
    elif uid == ADMIN_CHAT_ID:
        if udata.get("adm_estado") == "adm_esp_id_cot":
            udata["target_id"] = texto
            udata["adm_estado"] = "adm_esp_monto"
            await update.message.reply_text(f"ID `{texto}` seleccionado. Escribe el **Monto total**: ")
        
        elif udata.get("adm_estado") == "adm_esp_monto":
            v_id = udata["target_id"]
            supabase.table("cotizaciones").update({"monto": texto, "estado": "Cotizado"}).eq("id", v_id).execute()
            user_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            await context.bot.send_message(user_res.data["user_id"], f"💰 Tu vuelo ID `{v_id}` ha sido cotizado.\n**Monto:** {texto}\n\nUsa el botón 'Enviar Pago' para finalizar.")
            await update.message.reply_text(f"✅ Cotización enviada al usuario para el ID `{v_id}`.")
            udata.clear()

        elif udata.get("adm_estado") == "adm_esp_id_qr":
            udata["target_id_qr"] = texto
            udata["adm_estado"] = "adm_enviando_qrs"
            udata["coleccion_fotos"] = [] 
            await update.message.reply_text(f"✅ ID `{texto}` seleccionado. Envía el álbum de QRs ahora.")

# --- 6. ENVÍO DE QRs ---

async def enviar_paquete_qr(context: ContextTypes.DEFAULT_TYPE, target_uid, v_id, fotos):
    instrucciones = (f"🎫 **INSTRUCCIONES DE VUELO ID: {v_id}**\n\n"
                     "⚠️ **Instrucciones para evitar caídas:**\n"
                     "- No agregar a la app.\n"
                     "- No revisar en lo absoluto el vuelo, solo si se requiere se manda 2 horas antes del abordaje de que sigue en pie\n"
                     "- En caso de caida se sacaria un vuelo en el horario siguiente ejemplo: salida 3pm se sacaria salida 5 o 6pm\n"
                     "- Solo dejar guardada la foto de tu pase en tu galeria para llegar al aeropuerto solo a escanear")
    await context.bot.send_message(target_uid, instrucciones)
    media_group = [InputMediaPhoto(f) for f in fotos]
    await context.bot.send_media_group(target_uid, media_group)
    await context.bot.send_message(target_uid, "🎫 **¡Disfruta tu vuelo!**", parse_mode="Markdown")

# --- 7. MANEJO DE MEDIA ---

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = f"@{update.effective_user.username}" if update.effective_user.username else "SinUser"
    udata = context.user_data
    if not update.message.photo: return
    fid = update.message.photo[-1].file_id

    if udata.get("estado") == "usr_esperando_foto_vuelo":
        res = supabase.table("cotizaciones").insert({
            "user_id": str(uid), "username": username.replace("@",""),
            "pedido_completo": udata.get("tmp_datos"), "estado": "Esperando atención"
        }).execute()
        v_id = res.data[0]['id']
        await update.message.reply_text("✅ Se recibió su cotización. Por favor espere a que sea atendido.")
        cap = f"🔔 **NUEVA SOLICITUD**\nID: `{v_id}`\nUser: @{username.replace('@','')}\nID Telegram: `{uid}`\nInfo: {udata.get('tmp_datos')}"
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=cap, parse_mode="Markdown")
        udata.clear()

    elif udata.get("estado") == "usr_esperando_comprobante":
        v_id = udata.get("pago_vuelo_id")
        supabase.table("cotizaciones").update({"estado": "Esperando confirmación de pago"}).eq("id", v_id).execute()
        await update.message.reply_text(f"✅ Comprobante enviado para el ID `{v_id}`. En breve confirmaremos su pago.")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Confirmar Pago ✅", callback_data=f"conf_direct_{v_id}")]])
        cap = f"💰 **PAGO RECIBIDO**\nID Vuelo: `{v_id}`\nUser: @{username.replace('@','')}\nID Telegram: `{uid}`"
        await context.bot.send_photo(ADMIN_CHAT_ID, fid, caption=cap, reply_markup=btn, parse_mode="Markdown")
        udata.clear()

    elif uid == ADMIN_CHAT_ID and udata.get("adm_estado") == "adm_enviando_qrs":
        v_id = udata.get("target_id_qr")
        udata["coleccion_fotos"].append(fid)
        if "job_envio" in udata: udata["job_envio"].cancel()

        async def programar_envio():
            await asyncio.sleep(0.8) 
            user_res = supabase.table("cotizaciones").select("user_id").eq("id", v_id).single().execute()
            await enviar_paquete_qr(context, user_res.data["user_id"], v_id, udata["coleccion_fotos"])
            supabase.table("cotizaciones").update({"estado": "QR Enviados"}).eq("id", v_id).execute()
            await context.bot.send_message(ADMIN_CHAT_ID, f"✅ QRs del ID `{v_id}` enviados.")
            udata.clear()
        udata["job_envio"] = asyncio.create_task(programar_envio())

# --- 8. CALLBACKS ADMIN (HISTORIAL Y PENDIENTES CORREGIDOS) ---

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_CHAT_ID: return

    if query.data.startswith("conf_direct_"):
        v_id = query.data.split("_")[2]
        res = supabase.table("cotizaciones").update({"estado": "Pago Confirmado"}).eq("id", v_id).execute()
        await context.bot.send_message(res.data[0]['user_id'], f"✅ Tu pago para el ID `{v_id}` ha sido confirmado. En breve recibirás tus QRs.")
        await query.edit_message_caption(caption=f"✅ Pago Confirmado para ID `{v_id}`")

    elif query.data == "adm_cot":
        context.user_data["adm_estado"] = "adm_esp_id_cot"
        await query.message.reply_text("Introduce el **ID del vuelo** a cotizar:")
    
    elif query.data == "adm_qr":
        context.user_data["adm_estado"] = "adm_esp_id_qr"
        await query.message.reply_text("Introduce el **ID del vuelo** para enviar los QRs:")

    elif query.data == "adm_pend":
        # Pendientes: Filtrado por estatus que requieren atención inmediata
        res = supabase.table("cotizaciones").select("*")\
            .filter("estado", "in", '("Esperando atención", "Cotizado", "Esperando confirmación de pago", "Pago Confirmado")')\
            .order("username", asc=True).execute()
        
        if not res.data:
            await query.message.reply_text("✅ No hay vuelos pendientes actualmente.")
            return

        msj = "📊 **VUELOS PENDIENTES DE GESTIÓN**\n\n"
        agrupados = {}
        for v in res.data:
            uname = v['username'] or "SinUser"
            if uname not in agrupados: agrupados[uname] = []
            agrupados[uname].append(v)
        
        for user, vuelos in agrupados.items():
            msj += f"👤 **Usuario: @{user}**\n"
            for v in vuelos:
                msj += (f"  🆔 ID: `{v['id']}`\n"
                        f"  📍 Estatus: {v['estado']}\n"
                        f"  📝 Info: {v['pedido_completo']}\n"
                        f"  💰 Monto: {v.get('monto', 'Pte')}\n\n")
            msj += "----------\n"
        await query.message.reply_text(msj, parse_mode="Markdown")

    elif query.data == "adm_his":
        # Historial: Trae absolutamente todos los vuelos registrados
        res = supabase.table("cotizaciones").select("*").order("username", asc=True).execute()
        
        if not res.data:
            await query.message.reply_text("Historial vacío.")
            return

        agrupados = {}
        for v in res.data:
            uname = v['username'] or "SinUser"
            if uname not in agrupados: agrupados[uname] = []
            agrupados[uname].append(v)

        msj_total = "📜 **HISTORIAL COMPLETO AGRUPADO POR USER**\n\n"
        for user, vuelos in agrupados.items():
            msj_total += f"👤 **Usuario: @{user}**\n"
            for v in vuelos:
                msj_total += (f"  🆔 ID: `{v['id']}`\n"
                        f"  📍 Estatus: {v['estado']}\n"
                        f"  📝 Info: {v['pedido_completo']}\n"
                        f"  💰 Monto: {v.get('monto', '-')}\n"
                        f"  📅 Creado: {v['created_at'][:10]}\n\n")
            msj_total += "━━━━━━━━━━━━━━\n"

        # Dividir mensajes por el límite de caracteres de Telegram
        if len(msj_total) > 4000:
            for i in range(0, len(msj_total), 4000):
                await query.message.reply_text(msj_total[i:i+4000], parse_mode="Markdown")
        else:
            await query.message.reply_text(msj_total, parse_mode="Markdown")

# --- 9. ARRANQUE ---

if __name__ == "__main__":
    threading.Thread(target=run_server).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", lambda u, c: c.bot.send_message(ADMIN_CHAT_ID, "🛠 Panel Admin", reply_markup=get_admin_keyboard())))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()
