import os
import threading
from datetime import datetime, timedelta
import re
import logging

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
from supabase import create_client, Client
from telegram import (
    Bot, InputMediaPhoto, Update,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# ----------------- CONFIG GENERAL -----------------

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "7721918273"))
SOPORTE_USER = os.getenv("SOPORTE_USER", "@TuUsuarioSoporte")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

# Bot síncrono para usar desde Flask (dashboard)
bot_sync = Bot(token=BOT_TOKEN)

# ----------------- FLASK APP (DASHBOARD) -----------------

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cambia_esto")


def rango_proximos():
    hoy = datetime.utcnow().date()
    hasta = hoy + timedelta(days=5)
    return hoy, hasta


# ---------- GENERAL / ESTADÍSTICAS ----------

@app.route("/")
def general():
    hoy = datetime.utcnow().date()

    res_usuarios = (
        supabase.table("cotizaciones")
        .select("user_id", count="exact")
        .execute()
    )
    usuarios_unicos = res_usuarios.count or 0

    res_total = (
        supabase.table("cotizaciones")
        .select("monto")
        .in_("estado", ["Pago Confirmado", "QR Enviados"])
        .execute()
        .data
    )
    total_recaudado = sum(float(r["monto"]) for r in res_total if r["monto"])

    urgentes_hoy = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("fecha", str(hoy))
        .in_("estado", ["Esperando confirmación de pago", "Pago Confirmado"])
        .order("created_at", desc=True)
        .execute()
        .data
    )

    return render_template(
        "general.html",
        usuarios_unicos=usuarios_unicos,
        total_recaudado=total_recaudado,
        urgentes_hoy=urgentes_hoy,
        hoy=hoy,
    )


# ---------- POR COTIZAR ----------

@app.route("/por-cotizar")
def por_cotizar():
    pendientes = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("estado", "Esperando atención")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("por_cotizar.html", vuelos=pendientes)


@app.route("/accion/cotizar", methods=["POST"])
def accion_cotizar():
    v_id = request.form.get("id")
    monto = request.form.get("monto")

    if not v_id or not monto:
        flash("Falta ID o monto.", "error")
        return redirect(url_for("por_cotizar"))

    res = (
        supabase.table("cotizaciones")
        .update({"monto": monto, "estado": "Cotizado"})
        .eq("id", v_id)
        .execute()
    )

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("por_cotizar"))

    user_id_raw = res.data[0]["user_id"]
    try:
        user_id = int(user_id_raw)
    except Exception:
        app.logger.error(f"user_id no es entero: {user_id_raw}")
        flash("Cotización guardada, pero user_id inválido en la base.", "error")
        return redirect(url_for("por_cotizar"))

    texto = (
        f"💰 Tu vuelo ID {v_id} ha sido cotizado.\n"
        f"Monto a pagar: {monto}\n\n"
        "Cuando tengas tu comprobante usa el botón \"📸 Enviar Pago\" en el bot."
    )

    try:
        bot_sync.send_message(chat_id=user_id, text=texto)
        flash("Cotización enviada y usuario notificado.", "success")
    except Exception as e:
        app.logger.error(f"Error al enviar cotización a Telegram: {e}")
        flash("Cotización guardada pero no se pudo notificar al usuario.", "error")

    return redirect(url_for("por_cotizar"))


# ---------- VALIDAR PAGOS ----------

@app.route("/validar-pagos")
def validar_pagos():
    pendientes = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("estado", "Esperando confirmación de pago")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("validar_pagos.html", vuelos=pendientes)


@app.route("/accion/confirmar_pago", methods=["POST"])
def accion_confirmar_pago():
    v_id = request.form.get("id")

    if not v_id:
        flash("Falta ID.", "error")
        return redirect(url_for("validar_pagos"))

    res = (
        supabase.table("cotizaciones")
        .update({"estado": "Pago Confirmado"})
        .eq("id", v_id)
        .execute()
    )

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("validar_pagos"))

    user_id_raw = res.data[0]["user_id"]
    try:
        user_id = int(user_id_raw)
    except Exception:
        app.logger.error(f"user_id no es entero: {user_id_raw}")
        flash("Pago confirmado pero user_id inválido en la base.", "error")
        return redirect(url_for("validar_pagos"))

    texto = (
        f"✅ Tu pago para el vuelo ID {v_id} ha sido confirmado.\n"
        "En breve recibirás tus códigos QR."
    )

    try:
        bot_sync.send_message(chat_id=user_id, text=texto)
        flash("Pago confirmado y usuario notificado.", "success")
    except Exception as e:
        app.logger.error(f"Error al enviar notificación de pago: {e}")
        flash("Pago confirmado pero no se pudo notificar al usuario.", "error")

    return redirect(url_for("validar_pagos"))


# ---------- POR ENVIAR QR ----------

@app.route("/por-enviar-qr")
def por_enviar_qr():
    pendientes = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("estado", "Pago Confirmado")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return render_template("por_enviar_qr.html", vuelos=pendientes)


@app.route("/accion/enviar_qr", methods=["POST"])
def accion_enviar_qr():
    v_id = request.form.get("id")
    fotos = request.files.getlist("fotos")

    if not v_id:
        flash("Falta ID de vuelo.", "error")
        return redirect(url_for("por_enviar_qr"))

    res = (
        supabase.table("cotizaciones")
        .select("user_id")
        .eq("id", v_id)
        .single()
        .execute()
    )

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("por_enviar_qr"))

    user_id_raw = res.data["user_id"]
    try:
        user_id = int(user_id_raw)
    except Exception:
        app.logger.error(f"user_id no es entero: {user_id_raw}")
        flash("No se pudieron enviar QRs: user_id inválido.", "error")
        return redirect(url_for("por_enviar_qr"))

    if not fotos or fotos[0].filename == "":
        flash("Adjunta al menos una imagen de QR.", "error")
        return redirect(url_for("por_enviar_qr"))

    instrucciones = (
        f"🎫 INSTRUCCIONES ID: {v_id}\n\n"
        "Instrucciones para evitar caídas:\n"
        "- No agregar el pase a la app de la aerolínea.\n"
        "- No revisar el vuelo, solo si se requiere se confirma "
        "2 horas antes del abordaje.\n"
        "- En caso de caída se sacaría un vuelo en el horario siguiente "
        "(ejemplo: salida 3pm, se reacomoda 5–6pm).\n"
        "- Solo deja guardada la foto de tu pase en tu galería para "
        "llegar al aeropuerto y escanear directamente."
    )

    media_group = []
    for idx, f in enumerate(fotos):
        media_group.append(
            InputMediaPhoto(
                f,
                caption=f"Códigos QR vuelo ID {v_id}" if idx == 0 else ""
            )
        )

    try:
        bot_sync.send_message(chat_id=user_id, text=instrucciones)
        bot_sync.send_media_group(chat_id=user_id, media=media_group)
        bot_sync.send_message(chat_id=user_id, text="🎉 Disfruta tu vuelo.")

        supabase.table("cotizaciones").update(
            {"estado": "QR Enviados"}
        ).eq("id", v_id).execute()

        flash("QRs enviados y estado actualizado a 'QR Enviados'.", "success")
    except Exception as e:
        app.logger.error(f"Error al enviar QRs a Telegram: {e}")
        flash("No se pudieron enviar los QRs al usuario.", "error")

    return redirect(url_for("por_enviar_qr"))


# ---------- PRÓXIMOS VUELOS & HISTORIAL ----------

@app.route("/proximos-vuelos")
def proximos_vuelos():
    hoy, hasta = rango_proximos()
    proximos = (
        supabase.table("cotizaciones")
        .select("*")
        .gte("fecha", str(hoy))
        .lte("fecha", str(hasta))
        .order("fecha", desc=False)
        .execute()
        .data
    )
    return render_template("proximos_vuelos.html", vuelos=proximos)


@app.route("/historial")
def historial():
    vuelos = (
        supabase.table("cotizaciones")
        .select("*")
        .order("created_at", desc=True)
        .limit(300)
        .execute()
        .data
    )
    return render_template("historial.html", vuelos=vuelos)


# ----------------- LÓGICA DEL BOT (USUARIO) -----------------
# (Recortado para mantenerlo simple, pero sigue tu flujo actual)

DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")

def extraer_fecha(texto: str):
    m = DATE_PATTERN.search(texto)
    if not m:
        return None
    d, mth, y = m.groups()
    try:
        dt = datetime(int(y), int(mth), int(d))
        return dt.date().isoformat()
    except ValueError:
        return None


def user_keyboard():
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📝 Datos de vuelo"), KeyboardButton("📸 Enviar Pago")],
            [KeyboardButton("🆘 Soporte")],
        ],
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ Bienvenido al Sistema de Vuelos.\nUsa el menú para iniciar.",
        reply_markup=user_keyboard(),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = update.message.text
    udata = context.user_data

    if uid == ADMIN_CHAT_ID:
        await update.message.reply_text("El panel de administración está en la web.")
        return

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    if texto == "📝 Datos de vuelo":
        udata.clear()
        udata["estado"] = "usr_esperando_datos"
        await update.message.reply_text(
            "Escribe Origen, Destino y Fecha.\n"
            "Ejemplo: CDMX a Cancún el 25-12-2025."
        )
        return

    if texto == "📸 Enviar Pago":
        udata.clear()
        udata["estado"] = "usr_esperando_id_pago"
        await update.message.reply_text("Escribe el ID del vuelo que vas a pagar.")
        return

    if texto == "🆘 Soporte":
        btn = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "Contactar Soporte 💬",
                url=f"https://t.me/{SOPORTE_USER.replace('@','')}"
            )]]
        )
        await update.message.reply_text("Pulsa para hablar con soporte:", reply_markup=btn)
        return

    if udata.get("estado") == "usr_esperando_datos":
        udata["tmp_datos"] = texto
        udata["tmp_fecha"] = extraer_fecha(texto)
        udata["estado"] = "usr_esperando_foto_vuelo"
        await update.message.reply_text(
            "Ahora envía una imagen del vuelo (captura o referencia)."
        )
        return

    if udata.get("estado") == "usr_esperando_id_pago":
        v_id = texto.strip()
        res = (
            supabase.table("cotizaciones")
            .select("monto, estado")
            .eq("id", v_id)
            .single()
            .execute()
        )
        if not res.data:
            await update.message.reply_text("❌ ID no encontrado.")
            return
        monto = res.data.get("monto")
        if not monto:
            await update.message.reply_text(
                "⚠️ Ese vuelo aún no tiene monto. Espera a que sea cotizado."
            )
            return

        udata["pago_vuelo_id"] = v_id
        udata["estado"] = "usr_esperando_comprobante"

        texto_msj = (
            f"💳 ID de vuelo: {v_id}\n"
            f"💰 Monto a pagar: {monto}\n\n"
            "🏦 Datos de Pago\n"
            "Banco: BBVA\n"
            "CLABE: 012180015886058959\n"
            "Titular: Antonio Garcia\n\n"
            "Ahora envía la captura del pago como foto."
        )
        await update.message.reply_text(texto_msj)
        return

    await update.message.reply_text(
        "Usa el menú para continuar.", reply_markup=user_keyboard()
    )


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    udata = context.user_data

    if uid == ADMIN_CHAT_ID:
        return
    if not update.message.photo:
        return

    fid = update.message.photo[-1].file_id

    # Nueva cotización
    if udata.get("estado") == "usr_esperando_foto_vuelo":
        fecha = udata.get("tmp_fecha")
        res = (
            supabase.table("cotizaciones")
            .insert(
                {
                    "user_id": str(uid),
                    "username": update.effective_user.username or "SinUser",
                    "pedido_completo": udata.get("tmp_datos"),
                    "estado": "Esperando atención",
                    "monto": None,
                    "fecha": fecha,
                }
            )
            .execute()
        )
        v_id = res.data[0]["id"]
        await update.message.reply_text(
            f"✅ Cotización recibida.\n"
            f"ID de vuelo: {v_id}\n"
            "Un agente revisará tu solicitud y te enviará el monto."
        )

        await context.bot.send_photo(
            ADMIN_CHAT_ID,
            fid,
            caption=(
                "🔔 NUEVA SOLICITUD DE COTIZACIÓN\n"
                f"ID: {v_id}\n"
                f"User: @{update.effective_user.username}\n"
                f"Info: {udata.get('tmp_datos')}"
            ),
        )
        udata.clear()
        return

    # Comprobante de pago
    if udata.get("estado") == "usr_esperando_comprobante":
        v_id = udata.get("pago_vuelo_id")

        supabase.table("cotizaciones").update(
            {"estado": "Esperando confirmación de pago"}
        ).eq("id", v_id).execute()

        await update.message.reply_text(
            "✅ Comprobante enviado. Tu pago está en revisión."
        )

        btn = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                f"Confirmar Pago ID {v_id} ✅",
                callback_data=f"conf_pago_{v_id}",
            )]]
        )

        await context.bot.send_photo(
            ADMIN_CHAT_ID,
            fid,
            caption=(
                "💰 COMPROBANTE DE PAGO RECIBIDO\n"
                f"ID Vuelo: `{v_id}`\n"
                f"User: @{update.effective_user.username}"
            ),
            reply_markup=btn,
            parse_mode="Markdown",
        )
        udata.clear()


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    if query.data.startswith("conf_pago_"):
        v_id = query.data.split("_")[2]

        res = (
            supabase.table("cotizaciones")
            .update({"estado": "Pago Confirmado"})
            .eq("id", v_id)
            .execute()
        )

        if not res.data:
            await query.message.reply_text("No se encontró el vuelo.")
            return

        user_id = int(res.data[0]["user_id"])
        await context.bot.send_message(
            user_id,
            f"✅ Tu pago para el vuelo ID {v_id} ha sido confirmado.\n"
            "En breve recibirás tus códigos QR."
        )
        await query.edit_message_caption(
            caption=f"✅ PAGO CONFIRMADO\nID Vuelo: {v_id}"
        )


# ----------------- ARRANQUE DEL BOT EN UN HILO -----------------

def run_bot():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callbacks))
    application.add_handler(MessageHandler(filters.PHOTO, handle_media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.run_polling()


bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()


# ----------------- MAIN LOCAL (para tests) -----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=True)
