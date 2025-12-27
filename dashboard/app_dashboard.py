import os
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash
from supabase import create_client, Client
from telegram import Bot, InputMediaPhoto

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = 7721918273

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")


def get_rango_fechas():
    hoy = datetime.utcnow().date()
    hasta = hoy + timedelta(days=5)
    return hoy, hasta


@app.route("/")
def dashboard():
    pendientes_cot = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("estado", "Esperando atención")
        .order("created_at", desc=True)
        .execute()
        .data
    )

    pendientes_pago = (
        supabase.table("cotizaciones")
        .select("*")
        .in_("estado", ["Cotizado", "Esperando confirmación de pago"])
        .order("created_at", desc=True)
        .execute()
        .data
    )

    hoy, hasta = get_rango_fechas()
    proximos = (
        supabase.table("cotizaciones")
        .select("*")
        .gte("fecha", str(hoy))
        .lte("fecha", str(hasta))
        .order("fecha", desc=False)
        .execute()
        .data
    )

    historial = (
        supabase.table("cotizaciones")
        .select("*")
        .order("created_at", desc=True)
        .limit(200)
        .execute()
        .data
    )

    return render_template(
        "dashboard.html",
        pendientes_cot=pendientes_cot,
        pendientes_pago=pendientes_pago,
        proximos=proximos,
        historial=historial,
    )


# --- COTIZAR DESDE EL DASHBOARD ---

@app.route("/cotizar", methods=["POST"])
def cotizar():
    v_id = request.form.get("id")
    monto = request.form.get("monto")

    if not v_id or not monto:
        flash("Falta ID o monto.", "error")
        return redirect(url_for("dashboard"))

    res = (
        supabase.table("cotizaciones")
        .update({"monto": monto, "estado": "Cotizado"})
        .eq("id", v_id)
        .execute()
    )

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("dashboard"))

    user_id = res.data[0]["user_id"]

    texto = (
        f"💰 Tu vuelo ID {v_id} ha sido cotizado.\n"
        f"Monto: {monto}\n\nUsa el botón '📸 Enviar Pago' para subir tu comprobante."
    )

    try:
        bot.send_message(chat_id=user_id, text=texto)
        flash("Cotización enviada y usuario notificado.", "success")
    except Exception as e:
        flash(f"Cotización actualizada, pero error al notificar: {e}", "error")

    return redirect(url_for("dashboard"))


# --- CONFIRMAR PAGO DESDE DASHBOARD (SIN USAR BOTÓN TELEGRAM) ---

@app.route("/confirmar_pago", methods=["POST"])
def confirmar_pago():
    v_id = request.form.get("id")
    if not v_id:
        flash("Falta ID.", "error")
        return redirect(url_for("dashboard"))

    res = (
        supabase.table("cotizaciones")
        .update({"estado": "Pago Confirmado"})
        .eq("id", v_id)
        .execute()
    )

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("dashboard"))

    user_id = res.data[0]["user_id"]

    texto = (
        f"✅ Tu pago para el vuelo ID {v_id} ha sido confirmado.\n"
        f"En breve recibirás tus códigos QR."
    )

    try:
        bot.send_message(chat_id=user_id, text=texto)
        flash("Pago confirmado y usuario notificado.", "success")
    except Exception as e:
        flash(f"Pago confirmado, pero error al notificar: {e}", "error")

    return redirect(url_for("dashboard"))


# --- ENVIAR QRs DESDE DASHBOARD ---

@app.route("/enviar_qr", methods=["POST"])
def enviar_qr():
    """
    Este endpoint asume que ya tienes guardados en algún sitio los file_id de los QRs
    o que usas otro flujo para subirlos. Si los subes desde otro panel,
    aquí solo se marca el estado y se envía el mensaje de instrucciones.
    """
    v_id = request.form.get("id")
    if not v_id:
        flash("Falta ID.", "error")
        return redirect(url_for("dashboard"))

    # Obtener usuario
    res = (
        supabase.table("cotizaciones")
        .select("user_id")
        .eq("id", v_id)
        .single()
        .execute()
    )

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("dashboard"))

    user_id = res.data["user_id"]

    # Mensaje de instrucciones
    instrucciones = (
        f"🎫 INSTRUCCIONES ID: {v_id}\n\n"
        "Instrucciones para evitar caídas:\n"
        "- No agregar el pase a la app de la aerolínea.\n"
        "- No revisar el vuelo en la app; solo, si se requiere, "
        "se confirma 2 horas antes del abordaje.\n"
        "- En caso de caída, se saca un vuelo en el horario siguiente "
        "(ejemplo: salida 3pm, se reacomoda 5–6pm).\n"
        "- Solo deja guardada la foto de tu pase en tu galería para "
        "llegar al aeropuerto y escanear directamente."
    )

    try:
        bot.send_message(chat_id=user_id, text=instrucciones)

        # Aquí podrías mandar un media_group con los QRs si tienes sus file_id
        # bot.send_media_group(chat_id=user_id, media=[InputMediaPhoto(file_id1), ...])

        supabase.table("cotizaciones").update(
            {"estado": "QR Enviados"}
        ).eq("id", v_id).execute()

        bot.send_message(chat_id=user_id, text="🎉 Disfruta tu vuelo.")
        flash("QRs enviados y estado actualizado a 'QR Enviados'.", "success")
    except Exception as e:
        flash(f"Error al enviar QRs o actualizar estado: {e}", "error")

    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
