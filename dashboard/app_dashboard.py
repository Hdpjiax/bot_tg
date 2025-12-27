import os
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash
from supabase_py import create_client, Client
from telegram import Bot

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
    )  [web:58]

    pendientes_pago = (
        supabase.table("cotizaciones")
        .select("*")
        .in_("estado", ["Cotizado", "Esperando confirmación"])
        .order("created_at", desc=True)
        .execute()
        .data
    )  [web:58][web:63]

    hoy, hasta = get_rango_fechas()
    proximos = (
        supabase.table("cotizaciones")
        .select("*")
        .gte("fecha", str(hoy))
        .lte("fecha", str(hasta))
        .order("fecha", desc=False)
        .execute()
        .data
    )  [web:63][web:69]

    historial = (
        supabase.table("cotizaciones")
        .select("*")
        .order("created_at", desc=True)
        .limit(200)
        .execute()
        .data
    )  [web:58]

    return render_template(
        "dashboard.html",
        pendientes_cot=pendientes_cot,
        pendientes_pago=pendientes_pago,
        proximos=proximos,
        historial=historial,
    )


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
    )  [web:92]

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("dashboard"))

    user_id = res.data[0]["user_id"]
    texto = (
        f"💰 Tu vuelo ID {v_id} ha sido cotizado.\n"
        f"Monto: {monto}\n\nUsa el botón 'Enviar Pago' para finalizar."
    )

    try:
        bot.send_message(chat_id=user_id, text=texto)  [web:4]
        flash("Cotización enviada y usuario notificado.", "success")
    except Exception as e:
        flash(f"Cotización actualizada, pero error al notificar: {e}", "error")

    return redirect(url_for("dashboard"))


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
    )  [web:92]

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("dashboard"))

    user_id = res.data[0]["user_id"]
    texto = (
        f"✅ Tu pago para el vuelo ID {v_id} ha sido confirmado. "
        f"En breve recibirás tus pases."
    )

    try:
        bot.send_message(chat_id=user_id, text=texto)  [web:4]
        flash("Pago confirmado y usuario notificado.", "success")
    except Exception as e:
        flash(f"Pago confirmado, pero error al notificar: {e}", "error")

    return redirect(url_for("dashboard"))


@app.route("/marcar_qr_enviado", methods=["POST"])
def marcar_qr_enviado():
    v_id = request.form.get("id")
    if not v_id:
        flash("Falta ID.", "error")
        return redirect(url_for("dashboard"))

    res = (
        supabase.table("cotizaciones")
        .update({"estado": "QR Enviados"})
        .eq("id", v_id)
        .execute()
    )  [web:92]

    if not res.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("dashboard"))

    flash("Estado actualizado a 'QR Enviados'.", "success")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
