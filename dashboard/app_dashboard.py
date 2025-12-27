import os
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
from supabase import create_client, Client
from telegram import Bot, InputMediaPhoto

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cambia_esto")

def rango_proximos():
    hoy = datetime.utcnow().date()
    hasta = hoy + timedelta(days=5)
    return hoy, hasta


# --- SECCIÓN GENERAL / RESUMEN ---

@app.route("/")
def general():
    hoy, hasta = rango_proximos()

    vuelos = (
        supabase.table("cotizaciones")
        .select("*")
        .order("created_at", desc=True)
        .limit(100)
        .execute()
        .data
    )

    return render_template("general.html", vuelos=vuelos)


# --- SECCIÓN POR COTIZAR ---

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

    user_id = res.data[0]["user_id"]

    texto = (
        f"💰 Tu vuelo ID {v_id} ha sido cotizado.\n"
        f"Monto: {monto}\n\n"
        "Usa el botón '📸 Enviar Pago' en el bot para subir tu comprobante."
    )

    try:
        bot.send_message(chat_id=user_id, text=texto)
        flash("Cotización enviada y usuario notificado.", "success")
    except Exception as e:
        flash(f"Cotización actualizada, pero error al notificar: {e}", "error")

    return redirect(url_for("por_cotizar"))


# --- SECCIÓN VALIDAR PAGOS (Esperando confirmación) ---

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

    user_id = res.data[0]["user_id"]

    texto = (
        f"✅ Tu pago para el vuelo ID {v_id} ha sido confirmado.\n"
        "En breve recibirás tus códigos QR."
    )

    try:
        bot.send_message(chat_id=user_id, text=texto)
        flash("Pago confirmado y usuario notificado.", "success")
    except Exception as e:
        flash(f"Pago confirmado, pero error al notificar: {e}", "error")

    return redirect(url_for("validar_pagos"))


# --- SECCIÓN POR ENVIAR QR (Pago Confirmado) ---

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

    user_id = res.data["user_id"]

    if not fotos or fotos[0].filename == "":
        flash("Adjunta al menos una imagen de QR.", "error")
        return redirect(url_for("por_enviar_qr"))

    media_group = []
    for idx, f in enumerate(fotos):
        media_group.append(
            InputMediaPhoto(
                f,
                caption=f"Códigos QR vuelo ID {v_id}" if idx == 0 else ""
            )
        )

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

    try:
        bot.send_message(chat_id=user_id, text=instrucciones)
        bot.send_media_group(chat_id=user_id, media=media_group)
        bot.send_message(chat_id=user_id, text="🎉 Disfruta tu vuelo.")

        supabase.table("cotizaciones").update(
            {"estado": "QR Enviados"}
        ).eq("id", v_id).execute()

        flash("QRs enviados y estado actualizado a 'QR Enviados'.", "success")
    except Exception as e:
        flash(f"Error al enviar QRs o actualizar estado: {e}", "error")

    return redirect(url_for("por_enviar_qr"))


# --- SECCIÓN PRÓXIMOS VUELOS ---

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


# --- SECCIÓN HISTORIAL ---

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
