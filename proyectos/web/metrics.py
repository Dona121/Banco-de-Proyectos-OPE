"""Métricas para los dashboards por rol.

Todo se calcula **exclusivamente** desde los modelos existentes, reutilizando
los selectores (que ya filtran por rol). Sin tocar la estructura de datos.
"""
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.utils import timezone

from contenido.models import Actividades

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


def _pendientes_revision(entregas, ahora):
    """Entregas sin Revisión asociada, con su antigüedad en días."""
    pend = (
        entregas.filter(revisiones__isnull=True)
        .select_related("actividad", "actividad__proyecto",
                        "actividad__proyecto__asignado_a", "usuario")
        .order_by("fecha_creacion")
    )
    filas = []
    for e in pend:
        dias = (ahora - e.fecha_creacion).days
        filas.append({"entrega": e, "dias": dias, "atrasada": dias >= UMBRAL_ATRASO})
    return filas


def _proximas_y_vencidas(actividades, ahora):
    vencidas = actividades.filter(fecha_vencimiento__lt=ahora).exclude(estado=Estado.APROBADA)
    proximas = (
        actividades.filter(fecha_vencimiento__gte=ahora,
                           fecha_vencimiento__lte=ahora + timezone.timedelta(days=7))
        .exclude(estado=Estado.APROBADA)
        .order_by("fecha_vencimiento")
    )
    return proximas, vencidas


# --------------------------------------------------------------------------- #
# Director — vista ejecutiva
# --------------------------------------------------------------------------- #
def director(user):
    ahora = timezone.now()
    proyectos = selectors.proyectos_visibles(user)
    actividades = selectors.actividades_visibles(user)
    entregas = selectors.entregas_visibles(user)

    pendientes = _pendientes_revision(entregas, ahora)
    proximas, vencidas = _proximas_y_vencidas(actividades, ahora)
    segmentos, total_act = _distribucion_estado(actividades)

    # Resumen por coordinador (proyectos asignados, actividades, entregas pendientes).
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
    for fila in pendientes:
        cid = fila["entrega"].actividad.proyecto.asignado_a_id
        if cid in coord:
            coord[cid]["pendientes"] += 1
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
# Coordinador — vista operativa
# --------------------------------------------------------------------------- #
def coordinador(user):
    ahora = timezone.now()
    proyectos = selectors.proyectos_visibles(user)
    actividades = selectors.actividades_visibles(user)
    entregas = selectors.entregas_visibles(user)
    revisiones = selectors.revisiones_visibles(user)

    pendientes = _pendientes_revision(entregas, ahora)
    proximas, vencidas = _proximas_y_vencidas(actividades, ahora)
    segmentos, total_act = _distribucion_estado(actividades)

    # Tiempo promedio de revisión (revisión.fecha_creacion - entrega.fecha_creacion).
    dur = revisiones.annotate(
        delta=ExpressionWrapper(
            F("fecha_creacion") - F("actividad_entrega__fecha_creacion"),
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
# Formulador — vista personal
# --------------------------------------------------------------------------- #
def formulador(user):
    ahora = timezone.now()
    actividades = selectors.actividades_visibles(user)  # asignadas a él
    entregas = selectors.entregas_visibles(user).filter(usuario=user)

    proximas, vencidas = _proximas_y_vencidas(actividades, ahora)
    segmentos, total_act = _distribucion_estado(actividades)

    counts = {r["estado"]: r["c"] for r in actividades.values("estado").annotate(c=Count("id"))}
    sin_entrega = actividades.filter(actividadentrega__isnull=True)

    # Agrupación por proyecto.
    por_proyecto = list(
        actividades.values("proyecto__nombre")
        .annotate(c=Count("id")).order_by("-c")
    )
    max_proyecto = max((x["c"] for x in por_proyecto), default=0)

    ultima = entregas.order_by("-fecha_creacion").first()
    dias_ultima = (ahora - ultima.fecha_creacion).days if ultima else None

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
