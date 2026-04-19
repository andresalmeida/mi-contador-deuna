"""Alertas proactivas async para Chainlit."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

from agent.prompts import DEUNA_PRODUCTOS


THRESHOLD_CAIDA = 0.20
DEFAULT_INTERVALO_SEGUNDOS = 300
MESES_MINIMOS_CREDITO = 12  # Banco Pichincha exige 12 meses para créditos hasta $25k

# Uplift estimado por tipo de comercio al aceptar tarjetas
# Basado en que wallets representan ~6% del mercado digital Ecuador vs 74% tarjetas
# Usamos estimados conservadores por perfil de negocio
UPLIFT_TARJETA = {
    "COM-001": 0.15,   # tienda: tickets pequeños, ganancia moderada con tarjeta
    "COM-002": 0.15,   # fonda: similar, aunque almuerzo cada vez más se paga con tarjeta
    "COM-003": 0.22,   # salón: servicios de mayor valor → clientes prefieren tarjeta
}
DEUNA_NEGOCIOS_URL = "https://www.deuna.ec/negocios/"

DIAS_SEMANA = {
    0: "domingo",
    1: "lunes",
    2: "martes",
    3: "miércoles",
    4: "jueves",
    5: "viernes",
    6: "sábado",
}


@dataclass(frozen=True)
class AlertaVentas:
    ventas_hoy: float
    mediana_historica: float
    variacion: float
    dia_semana: int
    fecha_referencia: date

    @property
    def variacion_pct_abs(self) -> float:
        return abs(self.variacion) * 100


def obtener_dia_espanol(dia_semana: int) -> str:
    """Convierte DuckDB DAYOFWEEK (0=domingo) a nombre en español."""
    return DIAS_SEMANA.get(dia_semana, "día")


def calcular_alerta_ventas(
    con: Any,
    comercio_id: str | None = None,
    fecha_referencia: str | date | None = None,
    threshold: float = THRESHOLD_CAIDA,
) -> AlertaVentas | None:
    """Compara ventas de la fecha de referencia vs mediana histórica del mismo día."""
    resultado = con.execute(
        """
        WITH daily AS (
            SELECT
                dia,
                DAYOFWEEK(dia) AS dia_semana,
                ROUND(SUM(total), 2) AS total
            FROM ventas_diarias
            WHERE (? IS NULL OR comercio_id = ?)
            GROUP BY dia, dia_semana
        ),
        referencia AS (
            SELECT COALESCE(CAST(? AS DATE), MAX(dia)) AS dia
            FROM daily
        ),
        hoy AS (
            SELECT d.dia, d.dia_semana, d.total
            FROM daily d
            JOIN referencia r ON d.dia = r.dia
        ),
        historico AS (
            SELECT MEDIAN(d.total) AS mediana
            FROM daily d
            JOIN hoy h ON d.dia_semana = h.dia_semana
            WHERE d.dia < h.dia
        )
        SELECT
            hoy.total AS ventas_hoy,
            historico.mediana AS mediana_historica,
            CASE
                WHEN historico.mediana IS NULL OR historico.mediana = 0 THEN NULL
                ELSE (hoy.total - historico.mediana) / historico.mediana
            END AS variacion,
            hoy.dia_semana,
            hoy.dia AS fecha_referencia
        FROM hoy, historico;
        """,
        [comercio_id, comercio_id, fecha_referencia],
    ).fetchone()

    if not resultado or resultado[2] is None:
        return None

    ventas_hoy, mediana_historica, variacion, dia_semana, fecha = resultado
    if variacion >= -threshold:
        return None

    return AlertaVentas(
        ventas_hoy=float(ventas_hoy),
        mediana_historica=float(mediana_historica),
        variacion=float(variacion),
        dia_semana=int(dia_semana),
        fecha_referencia=fecha,
    )


def construir_mensaje_alerta(alerta: AlertaVentas) -> str:
    """Mensaje corto y accionable para el comerciante."""
    dia = obtener_dia_espanol(alerta.dia_semana)
    return (
        f"Este {dia} llevas **${alerta.ventas_hoy:.2f}** — "
        f"**{alerta.variacion_pct_abs:.0f}% menos** que un {dia} normal "
        f"(**${alerta.mediana_historica:.2f}**). ¿Revisamos qué está más quieto?"
    )


def calcular_calificacion_credito(
    con: Any,
    comercio_id: str | None = None,
    fecha_referencia: str | date | None = None,
    meses_minimos: int = MESES_MINIMOS_CREDITO,
) -> dict | None:
    """Verifica si el comercio tiene suficientes meses de historial para proponer un crédito."""
    try:
        resultado = con.execute(
            """
            SELECT
                COUNT(DISTINCT DATE_TRUNC('month', dia)) AS meses_con_ventas,
                ROUND(AVG(total), 2) AS promedio_mensual,
                ROUND(SUM(total), 2) AS total_historico
            FROM ventas_diarias
            WHERE (? IS NULL OR comercio_id = ?)
              AND (? IS NULL OR dia <= CAST(? AS DATE))
            """,
            [comercio_id, comercio_id, fecha_referencia, fecha_referencia],
        ).fetchone()

        if not resultado or not resultado[0]:
            return None

        meses, promedio, total = resultado
        if meses < meses_minimos:
            return None

        return {
            "meses": int(meses),
            "promedio_mensual": float(promedio or 0),
            "total_historico": float(total or 0),
        }
    except Exception:
        return None


def construir_mensaje_credito(calificacion: dict, comercio_id: str | None) -> tuple[str, str]:
    """Mensaje proactivo cuando el comercio ya califica. Devuelve (mensaje, url_producto)."""
    meses = calificacion["meses"]
    promedio = calificacion["promedio_mensual"]

    # Alerta genérica: detectamos elegibilidad pero no referimos un banco específico.
    # Si el comerciante pregunta explícitamente en el chat, financial_advisor_node
    # responde con el producto y banco concreto.
    mensaje = (
        f"Llevas **{meses} meses** en DeUna con **${promedio:.0f}/mes** en promedio. "
        f"Ya calificas para **acceder a crédito** desde el celular — sin carpetas ni agencia. "
        f"¿Quieres saber cuánto podrías gestionar?"
    )
    return mensaje, DEUNA_NEGOCIOS_URL


def calcular_potencial_tarjeta(
    con: Any,
    comercio_id: str | None = None,
) -> dict | None:
    """Calcula el promedio mensual real del comercio (excluyendo mes parcial)."""
    try:
        resultado = con.execute(
            """
            SELECT ROUND(AVG(mes_total), 2) AS promedio_mensual
            FROM (
                SELECT DATE_TRUNC('month', dia) AS mes,
                       SUM(total) AS mes_total
                FROM ventas_diarias
                WHERE (? IS NULL OR comercio_id = ?)
                  AND dia < DATE '2026-04-01'
                GROUP BY mes
            ) sub
            """,
            [comercio_id, comercio_id],
        ).fetchone()

        if not resultado or not resultado[0]:
            return None

        promedio = float(resultado[0])
        uplift_pct = UPLIFT_TARJETA.get(comercio_id or "", 0.15)
        ganancia_estimada = round(promedio * uplift_pct, 0)

        return {
            "promedio_mensual": promedio,
            "uplift_pct": uplift_pct,
            "ganancia_estimada": ganancia_estimada,
        }
    except Exception:
        return None


def construir_mensaje_tarjeta(potencial: dict, comercio_id: str | None) -> tuple[str, str]:
    """Mensaje proactivo sobre DeUna Negocios (cobro con tarjeta desde el celular)."""
    promedio = potencial["promedio_mensual"]
    ganancia = potencial["ganancia_estimada"]
    uplift_pct = int(potencial["uplift_pct"] * 100)

    if comercio_id == "COM-003":
        contexto = "En el salón, los clientes prefieren pagar tintes y tratamientos con tarjeta."
    elif comercio_id == "COM-002":
        contexto = "Cada vez más gente paga el almuerzo con tarjeta — si no puede, busca otro lado."
    else:
        contexto = "Mucha gente sale sin efectivo; si no aceptas tarjeta, no compra."

    mensaje = (
        f"DeUna Negocios te permite **cobrar con tarjeta desde el celular** — sin datáfono, "
        f"sin cuota mensual. {contexto} "
        f"Con tu promedio de **${promedio:.0f}/mes**, aceptar tarjeta podría sumarte "
        f"**~${ganancia:.0f} extra al mes**."
    )
    return mensaje, DEUNA_NEGOCIOS_URL


async def _send_chainlit_message(content: str) -> None:
    import chainlit as cl

    await cl.Message(content=content).send()


async def _send_credito_message(content: str, url: str, link_label: str = "Ver más →") -> None:
    import chainlit as cl

    texto = f"{content}\n\n[📋 {link_label}]({url})"
    await cl.Message(content=texto).send()


async def monitor_ventas(
    session_id: str,
    con: Any,
    intervalo_segundos: int = DEFAULT_INTERVALO_SEGUNDOS,
    comercio_id: str | None = None,
    fecha_referencia: str | date | None = None,
) -> None:
    """Monitorea caídas de venta y calificación crediticia; empuja alertas al hilo de Chainlit."""
    alerta_caida_enviada = False
    alerta_tarjeta_enviada = False

    while True:
        await asyncio.sleep(intervalo_segundos)

        # Tick 1: alerta de caída de ventas
        if not alerta_caida_enviada:
            alerta = calcular_alerta_ventas(
                con=con,
                comercio_id=comercio_id,
                fecha_referencia=fecha_referencia,
            )
            if alerta is not None:
                await _send_chainlit_message(content=f"💡 {construir_mensaje_alerta(alerta)}")
                alerta_caida_enviada = True
            continue

        # Tick 2: DeUna Negocios — cobro con tarjeta desde el celular
        if not alerta_tarjeta_enviada:
            potencial = calcular_potencial_tarjeta(con=con, comercio_id=comercio_id)
            if potencial is not None:
                mensaje, url = construir_mensaje_tarjeta(potencial, comercio_id)
                await _send_credito_message(
                    content=f"💳 {mensaje}", url=url, link_label="Ver DeUna Negocios →"
                )
            alerta_tarjeta_enviada = True


def iniciar_monitor_ventas(
    session_id: str,
    con: Any,
    intervalo_segundos: int = DEFAULT_INTERVALO_SEGUNDOS,
    comercio_id: str | None = None,
    fecha_referencia: str | date | None = None,
) -> asyncio.Task[None]:
    """Crea la tarea background; app.py debe guardarla en cl.user_session."""
    return asyncio.create_task(
        monitor_ventas(
            session_id=session_id,
            con=con,
            intervalo_segundos=intervalo_segundos,
            comercio_id=comercio_id,
            fecha_referencia=fecha_referencia,
        )
    )
