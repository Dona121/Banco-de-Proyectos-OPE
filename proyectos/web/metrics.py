"""Métricas para los dashboards por rol.

Todo se calcula **exclusivamente** desde los modelos existentes, reutilizando
los selectores (que ya filtran por rol). Sin tocar la estructura de datos.
"""
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.utils import timezone

from contenido.models import Actividades, ActividadEntrega
from cuentas.roles import COORDINADOR

from . import selectors

Estado = Actividades.EstadoActividad

# Umbral (días) a partir del cual una entrega pendiente se considera atrasada.
UMBRAL_ATRASO = 3

# Colores de marca por estado (para gráficos).
ESTADO_COLOR = {
    Estado.PENDIENTE: "#94a3b8",
    Estado.EN_REVISION: "#0b72ab",
    Estado.AJUSTES: "#ffa700",
    Estado.APROBADA: "#109d39",
}


def _distribucion_estado(actividades):
    """Lista de segmentos {code,label,count,pct,color} + total, para gráficos."""
    total = actividades.count()
    counts = {r["estado"]: r["c"] for r in actividades.values("estado").annotate(c=Count("id"))}
    segmentos = []
    for code, label in Estado.choices:
        c = counts.get(code, 0)
        segmentos.append({
            "code": code, "label": label, "count": c,
            "pct": round(c / total * 100) if total else 0,
            "color": ESTADO_COLOR[code],
        })
    return segmentos, total


def _entregada_el(entrega):
    """Cuándo se realizó la entrega, no cuándo se abrió el borrador.

    Desde que entregar es un acto aparte, el ejecutor puede tener una versión
    abierta días antes de enviarla. Medir desde `fecha_creacion` le cargaría al
    revisor un retraso que no es suyo: la entrega le llegó hoy.

    `realizar_entrega` guarda la entrega, así que `fecha_actualizacion` (de
    `Fechas`, con `auto_now`) marca ese momento. Es lo más cercano a una fecha
    de envío sin tocar el modelo, que es definitivo.
    """
    return entrega.fecha_actualizacion or entrega.fecha_creacion


def _pendientes_revision(entregas, ahora):
    """Entregas ya realizadas y sin revisar, con los días que llevan esperando.

    El queryset llega de `selectors.entregas_por_revisar`, que ya excluye los
    borradores y las versiones reemplazadas.
    """
    pend = (
        entregas.select_related("actividad", "actividad__proyecto",
                                "actividad__proyecto__asignado_a", "usuario")
        .order_by("fecha_actualizacion")
    )
    filas = []
    for e in pend:
        dias = (ahora - _entregada_el(e)).days
        filas.append({"entrega": e, "dias": dias, "atrasada": dias >= UMBRAL_ATRASO})
    return filas


# Entregar detiene el reloj: una actividad En revisión no está vencida ni por
# vencer, aunque su fecha haya pasado. Misma regla que el filtro `vencida` de
# `web_extras`; si cambia una, cambia la otra.
ESTADOS_SIN_PLAZO = (Estado.EN_REVISION, Estado.APROBADA)


def _proximas_y_vencidas(actividades, ahora):
    pendientes = actividades.exclude(estado__in=ESTADOS_SIN_PLAZO)
    vencidas = pendientes.filter(fecha_vencimiento__lt=ahora)
    proximas = (
        pendientes.filter(fecha_vencimiento__gte=ahora,
                          fecha_vencimiento__lte=ahora + timezone.timedelta(days=7))
        .order_by("fecha_vencimiento")
    )
    return proximas, vencidas


# --------------------------------------------------------------------------- #
# Director: vista ejecutiva
# --------------------------------------------------------------------------- #
def director(user):
    ahora = timezone.now()
    proyectos = selectors.proyectos_visibles(user)
    actividades = selectors.actividades_visibles(user)

    # Pendientes de MI revisión (actividades ejecutadas por un coordinador).
    pendientes = _pendientes_revision(selectors.entregas_por_revisar(user), ahora)
    proximas, vencidas = _proximas_y_vencidas(actividades, ahora)
    segmentos, total_act = _distribucion_estado(actividades)

    # Resumen por coordinador (proyectos asignados, actividades, backlog propio de
    # revisión: entregas de formuladores sin revisar en sus proyectos).
    coord = {}
    for p in proyectos.select_related("asignado_a"):
        c = p.asignado_a
        d = coord.setdefault(c.id, {"coordinador": c, "proyectos": 0,
                                    "actividades": 0, "pendientes": 0})
        d["proyectos"] += 1
    for row in actividades.values("proyecto__asignado_a").annotate(c=Count("id")):
        cid = row["proyecto__asignado_a"]
        if cid in coord:
            coord[cid]["actividades"] = row["c"]
    backlog = (
        ActividadEntrega.objects.filter(
            revisiones__isnull=True, actividad__proyecto__in=proyectos,
            # Solo lo entregado: un borrador abierto es trabajo del ejecutor,
            # no carga pendiente del coordinador.
            actividad__estado=Estado.EN_REVISION,
        )
        .exclude(actividad__asignado_a__groups__name=COORDINADOR)
        .values("actividad__proyecto__asignado_a")
        .annotate(c=Count("id"))
    )
    for row in backlog:
        cid = row["actividad__proyecto__asignado_a"]
        if cid in coord:
            coord[cid]["pendientes"] = row["c"]
    coordinadores = sorted(coord.values(), key=lambda x: -x["pendientes"])

    # Cumplimiento por proyecto (% aprobadas / total).
    cumplimiento = []
    proyectos_cumpl = proyectos.annotate(
        aprobadas=Count("actividades", filter=Q(actividades__estado=Estado.APROBADA))
    )
    for p in proyectos_cumpl:
        tot = p.n_actividades
        cumplimiento.append({
            "proyecto": p, "total": tot, "aprobadas": p.aprobadas,
            "pct": round(p.aprobadas / tot * 100) if tot else 0,
        })
    cumplimiento.sort(key=lambda x: x["pct"], reverse=True)

    return {
        "rol_dashboard": "Director",
        "total_proyectos": proyectos.count(),
        "total_actividades": total_act,
        "total_coordinadores": len(coordinadores),
        "vencidas_count": vencidas.count(),
        "pendientes_count": len(pendientes),
        "segmentos": segmentos,
        "coordinadores": coordinadores,
        "pendientes": pendientes[:8],
        "pendientes_atrasadas": sum(1 for f in pendientes if f["atrasada"]),
        "cumplimiento": cumplimiento,
        "proximas": proximas[:6],
        "vencidas": vencidas.order_by("fecha_vencimiento")[:6],
        "umbral_atraso": UMBRAL_ATRASO,
    }


# --------------------------------------------------------------------------- #
# Coordinador: vista operativa
# --------------------------------------------------------------------------- #
def coordinador(user):
    ahora = timezone.now()
    proyectos = selectors.proyectos_visibles(user)
    actividades = selectors.actividades_visibles(user)
    revisiones = selectors.revisiones_visibles(user)

    # Pendientes de MI revisión (entregas de formuladores en mis proyectos); no
    # incluye lo que yo mismo entregue como ejecutor (eso lo revisa el director).
    pendientes = _pendientes_revision(selectors.entregas_por_revisar(user), ahora)
    proximas, vencidas = _proximas_y_vencidas(actividades, ahora)
    segmentos, total_act = _distribucion_estado(actividades)

    # Tiempo promedio de revisión (revisión.fecha_creacion - entrega.fecha_creacion).
    dur = revisiones.annotate(
        delta=ExpressionWrapper(
            # Desde que se entregó (ver `_entregada_el`), no desde que se abrió
            # el borrador: si no, el promedio incluye lo que tardó el ejecutor.
            F("fecha_creacion") - F("actividad_entrega__fecha_actualizacion"),
            output_field=DurationField(),
        )
    ).aggregate(prom=Avg("delta"))["prom"]
    tiempo_prom = round(dur.total_seconds() / 86400, 1) if dur else None

    # Formuladores con más actividades pendientes (no aprobadas).
    formuladores = [
        {"nombre": (r["asignado_a__first_name"] or r["asignado_a__username"]),
         "count": r["c"]}
        for r in actividades.exclude(estado=Estado.APROBADA)
        .values("asignado_a__first_name", "asignado_a__username")
        .annotate(c=Count("id")).order_by("-c")[:6]
    ]

    return {
        "rol_dashboard": "Coordinador",
        "total_proyectos": proyectos.count(),
        "total_actividades": total_act,
        "pendientes_count": len(pendientes),
        "vencidas_count": vencidas.count(),
        "tiempo_prom": tiempo_prom,
        "segmentos": segmentos,
        "proyectos": proyectos.order_by("-fecha_creacion")[:6],
        "pendientes": pendientes[:8],
        "pendientes_atrasadas": sum(1 for f in pendientes if f["atrasada"]),
        "revisadas": revisiones.select_related(
            "actividad_entrega__actividad").order_by("-fecha_creacion")[:6],
        "proximas": proximas[:6],
        "vencidas": vencidas.order_by("fecha_vencimiento")[:6],
        "formuladores": formuladores,
        "umbral_atraso": UMBRAL_ATRASO,
    }


# --------------------------------------------------------------------------- #
# Consulta: panel transversal de solo lectura (ambos dominios)
# --------------------------------------------------------------------------- #
def consulta(user):
    """Totales globales de solo lectura. El rol Consulta ve todo pero no actúa;
    los selectores ya le devuelven el universo completo."""
    ahora = timezone.now()
    proyectos = selectors.proyectos_visibles(user)
    actividades = selectors.actividades_visibles(user)
    segmentos, total_act = _distribucion_estado(actividades)
    proximas, vencidas = _proximas_y_vencidas(actividades, ahora)

    # Cuentas de cobro: import diferido para no acoplar los dominios al importar
    # (Consulta es la única vista que cruza ambos).
    from cuentas_de_cobro import selectors as cc_selectors

    cuentas = cc_selectors.cuentas_visibles(user)
    total_cuentas = cuentas.count()
    cuentas_abiertas = cuentas.filter(fecha_cierre__isnull=True).count()

    return {
        "rol_dashboard": "Consulta",
        "total_proyectos": proyectos.count(),
        "total_actividades": total_act,
        "vencidas_count": vencidas.count(),
        "total_cuentas": total_cuentas,
        "cuentas_abiertas": cuentas_abiertas,
        "cuentas_cerradas": total_cuentas - cuentas_abiertas,
        "segmentos": segmentos,
        "proyectos": proyectos.order_by("-fecha_creacion")[:6],
        "proximas": proximas[:6],
        "vencidas": vencidas.order_by("fecha_vencimiento")[:6],
    }


# --------------------------------------------------------------------------- #
# Formulador: vista personal
# --------------------------------------------------------------------------- #
def formulador(user):
    ahora = timezone.now()
    actividades = selectors.actividades_visibles(user)  # asignadas a él
    entregas = selectors.entregas_visibles(user).filter(usuario=user)

    proximas, vencidas = _proximas_y_vencidas(actividades, ahora)
    segmentos, total_act = _distribucion_estado(actividades)

    counts = {r["estado"]: r["c"] for r in actividades.values("estado").annotate(c=Count("id"))}
    # Pendientes de entregar. Antes era "sin ninguna fila de entrega", pero
    # desde que abrir la versión y entregarla son dos actos, una actividad con
    # un borrador abierto tiene fila y seguía sin estar entregada: desaparecía
    # de la lista de tareas de quien la tenía a medias. El estado PENDIENTE
    # significa exactamente eso: nada enviado todavía.
    sin_entrega = actividades.filter(estado=Estado.PENDIENTE)

    # Agrupación por proyecto.
    por_proyecto = list(
        actividades.values("proyecto__nombre")
        .annotate(c=Count("id")).order_by("-c")
    )
    max_proyecto = max((x["c"] for x in por_proyecto), default=0)

    # La última entrega REALIZADA: un borrador abierto no es una entrega hecha.
    ultima = (
        entregas.filter(
            Q(revisiones__isnull=False)
            | Q(actividad__estado__in=(Estado.EN_REVISION, Estado.APROBADA))
        )
        .order_by("-fecha_actualizacion")
        .first()
    )
    dias_ultima = (ahora - _entregada_el(ultima)).days if ultima else None

    return {
        "rol_dashboard": "Formulador",
        "total_actividades": total_act,
        "en_revision": counts.get(Estado.EN_REVISION, 0),
        "aprobadas": counts.get(Estado.APROBADA, 0),
        "ajustes": counts.get(Estado.AJUSTES, 0),
        "pendientes": counts.get(Estado.PENDIENTE, 0),
        "segmentos": segmentos,
        "por_proyecto": por_proyecto,
        "max_proyecto": max_proyecto,
        "sin_entrega": sin_entrega.order_by("fecha_vencimiento"),
        "sin_entrega_count": sin_entrega.count(),
        "requieren_nueva": actividades.filter(estado=Estado.AJUSTES).order_by("fecha_vencimiento"),
        "proximas": proximas[:6],
        "vencidas": vencidas.order_by("fecha_vencimiento")[:6],
        "vencidas_count": vencidas.count(),
        "mis_actividades": actividades.order_by("fecha_vencimiento")[:10],
        "entregas_recientes": entregas.select_related(
            "actividad").order_by("-fecha_creacion")[:6],
        "dias_ultima_entrega": dias_ultima,
    }
