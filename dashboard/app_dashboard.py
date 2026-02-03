import os
from datetime import datetime, timedelta
import requests
import json
import re
from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
from supabase import create_client, Client
from telegram import Bot, InputMediaPhoto

# ----------------- CONFIG -----------------

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


def enviar_mensaje(chat_id: int, texto: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": texto}
    r = requests.post(url, data=data, timeout=10)
    r.raise_for_status()
    
def enviar_foto(chat_id: int, fileobj, caption: str = ""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": (fileobj.filename, fileobj.stream, fileobj.mimetype)}
    data = {"chat_id": chat_id, "caption": caption}
    r = requests.post(url, data=data, files=files, timeout=20)
    r.raise_for_status()




# ----------------- GENERAL / ESTADÍSTICAS -----------------
@app.route("/")
def general():
    hoy = datetime.utcnow().date()
    manana = hoy + timedelta(days=1)
    pasado_manana = hoy + timedelta(days=2)

    res_usuarios = (
        supabase.table("cotizaciones")
        .select("username", count="exact", head=True)
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

    # URGENTES: vuelos entre hoy y mañana (incluye TODO mañana)
    urgentes = (
        supabase.table("cotizaciones")
        .select("*")
        .gte("fecha", str(hoy))
        .lt("fecha", str(pasado_manana))   # <-- clave
        .order("fecha", desc=False)
        .order("created_at", desc=True)
        .execute()
        .data
    )

    return render_template(
        "general.html",
        usuarios_unicos=usuarios_unicos,
        total_recaudado=total_recaudado,
        urgentes=urgentes,
        hoy=hoy,
        manana=manana,
    )

# ----------------- POR COTIZAR -----------------

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

    for v in pendientes:
        v["total_vuelo"] = extraer_total_vuelo(v.get("pedido_completo", ""))

    return render_template("por_cotizar.html", vuelos=pendientes)


@app.route("/accion/cotizar", methods=["POST"])
def accion_cotizar():
    v_id = (request.form.get("id") or "").strip()
    porcentaje_raw = (request.form.get("porcentaje") or "").strip()
    monto_raw = (request.form.get("monto") or "").strip()  # fallback

    if not v_id:
        flash("Falta ID.", "error")
        return redirect(url_for("por_cotizar"))

    sel = (
        supabase.table("cotizaciones")
        .select("user_id, pedido_completo")
        .eq("id", v_id)
        .single()
        .execute()
    )

    if not sel.data:
        flash("No se encontró el vuelo.", "error")
        return redirect(url_for("por_cotizar"))

    pedido = sel.data.get("pedido_completo") or ""
    total = extraer_total_vuelo(pedido)

    pct = None
    if porcentaje_raw:
        try:
            pct = float(porcentaje_raw)
        except ValueError:
            flash("Porcentaje inválido.", "error")
            return redirect(url_for("por_cotizar"))

        if pct <= 0 or pct > 100:
            flash("El porcentaje debe estar entre 0 y 100.", "error")
            return redirect(url_for("por_cotizar"))

        if total is None:
            flash("No se detectó el total del vuelo. Agrega el total como $1234 o captura el monto manualmente.", "error")
            return redirect(url_for("por_cotizar"))

        monto_calc = round(total * (pct / 100.0), 2)
        monto_str = f"{monto_calc:.2f}"
    else:
        if not monto_raw:
            flash("Falta porcentaje o monto.", "error")
            return redirect(url_for("por_cotizar"))
        monto_str = monto_raw

    res = (
        supabase.table("cotizaciones")
        .update({"monto": monto_str, "estado": "Cotizado"})
        .eq("id", v_id)
        .execute()
    )

    if not res.data:
        flash("No se pudo actualizar el vuelo.", "error")
        return redirect(url_for("por_cotizar"))

    user_id_raw = sel.data.get("user_id")
    try:
        user_id = int(user_id_raw)
    except Exception:
        flash("Cotización guardada, pero user_id inválido en la base.", "error")
        return redirect(url_for("por_cotizar"))

    if pct is not None and total is not None:
        texto = (
            f"💰 Tu vuelo ID {v_id} ha sido cotizado.\n"
            f"Total del vuelo: ${total:.2f}\n"
            f"Porcentaje a pagar: {pct:.2f}%\n"
            f"Monto a pagar: ${monto_str}\n\n"
            "Cuando tengas tu comprobante usa el botón \"📸 Enviar Pago\" en el bot."
        )
    else:
        texto = (
            f"💰 Tu vuelo ID {v_id} ha sido cotizado.\n"
            f"Monto a pagar: {monto_str}\n\n"
            "Cuando tengas tu comprobante usa el botón \"📸 Enviar Pago\" en el bot."
        )

    try:
        enviar_mensaje(user_id, texto)
        flash("Cotización enviada y usuario notificado.", "success")
    except Exception:
        flash("Cotización guardada pero no se pudo notificar al usuario.", "error")

    return redirect(url_for("por_cotizar"))



# ----------------- VALIDAR PAGOS -----------------

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
        enviar_mensaje(user_id, texto)
        flash("Pago confirmado y usuario notificado.", "success")
    except Exception as e:
        app.logger.error(f"Error al enviar notificación de pago: {e}")
        flash("Pago confirmado pero no se pudo notificar al usuario.", "error")

    return redirect(url_for("validar_pagos"))

# ----------------- POR ENVIAR QR -----------------

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

# ----------------- POR ENVIAR QR -----------------

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

    try:
        enviar_mensaje(user_id, instrucciones)

        # mandar cada foto una por una
        for idx, f in enumerate(fotos):
            caption = f"Códigos QR vuelo ID {v_id}" if idx == 0 else ""
            enviar_foto(user_id, f, caption=caption)

        enviar_mensaje(user_id, "🎉 Disfruta tu vuelo.")

        supabase.table("cotizaciones").update(
            {"estado": "QR Enviados"}
        ).eq("id", v_id).execute()

        flash("QRs enviados y estado actualizado a 'QR Enviados'.", "success")
    except Exception as e:
        app.logger.error(f"Error al enviar QRs a Telegram: {e}")
        flash("No se pudieron enviar los QRs al usuario.", "error")

    return redirect(url_for("por_enviar_qr"))

# ----------------- PRÓXIMOS VUELOS -----------------

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

_MONEY_RE = re.compile(r"(?:\$|MXN\s*)\s*([0-9][0-9.,]*)", re.IGNORECASE)

def extraer_total_vuelo(texto: str):
    """Extrae el total desde pedido_completo. Busca el último monto tipo $5633 o MXN 5,633.50."""
    if not texto:
        return None
    matches = _MONEY_RE.findall(texto)
    if not matches:
        return None
    raw = matches[-1].replace(",", "").strip()
    try:
        return float(raw)
    except ValueError:
        return None
    
# ----------------- HISTORIAL -----------------

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

#historial por usuario
@app.route("/historial/usuario/<user_id>")
def historial_usuario(user_id):
    vuelos = (
        supabase.table("cotizaciones")
        .select("*")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(500)
        .execute()
        .data
    )

    username = vuelos[0].get("username", "SinUser") if vuelos else "SinUser"
    return render_template(
        "historial_usuario.html",
        vuelos=vuelos,
        user_id=user_id,
        username=username
    )
@app.route("/vuelo/<vuelo_id>")
def vuelo_detalle(vuelo_id):
    """Detalle completo de un vuelo por ID."""
    try:
        res = (
            supabase.table("cotizaciones")
            .select("*")
            .eq("id", vuelo_id)
            .single()
            .execute()
        )
    except Exception as e:
        app.logger.error(f"Error al cargar vuelo {vuelo_id}: {e}")
        flash("No se pudo cargar el detalle del vuelo.", "error")
        return redirect(url_for("historial"))

    v = res.data
    if not v:
        flash("Vuelo no encontrado.", "error")
        return redirect(url_for("historial"))

    total_vuelo = extraer_total_vuelo(v.get("pedido_completo", "") or "")

    monto_val = None
    try:
        if v.get("monto") not in (None, ""):
            monto_val = float(v.get("monto"))
    except Exception:
        monto_val = None

    porcentaje = None
    if total_vuelo and monto_val is not None and total_vuelo > 0:
        porcentaje = round((monto_val / total_vuelo) * 100.0, 2)

    return render_template(
        "vuelo_detalle.html",
        v=v,
        total_vuelo=total_vuelo,
        porcentaje=porcentaje,
    )
# ----------------- MAIN -----------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
