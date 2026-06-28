"""Consultas y reglas de negocio de los reportes.

Separadas de la generación de archivos (excel.py / pdf.py) y de las vistas.
Todo parte de los selectores (scoping por rol): un usuario solo reporta sobre
lo que le corresponde. Sin modificar los modelos.
"""
from django.db.models import Count, Q

from contenido.models import Actividades, Proyectos

from .. import selectors

Estado = Actividades.EstadoActividad


def _filtro_alcance(qs, proyecto=None, responsable=None):
    """Filtra actividades por proyecto y/o responsable (director o coordinador)."""
    if proyecto:
        qs = qs.filter(proyecto=proyecto)
    if responsable:
        qs = qs.filter(
            Q(proyecto__creador_por=responsable) | Q(proyecto__asignado_a=responsable)
        )
    return qs


# --------------------------------------------------------------------------- #
# 1. Proyectos formulados (todas sus actividades aprobadas)
# --------------------------------------------------------------------------- #
def proyectos_formulados(user, *, proyecto=None, responsable=None, desde=None, hasta=None):
    """Proyectos cuyas actividades (visibles) están 100% aprobadas.

    Excluye proyectos con alguna actividad Pendiente / En revisión / Requiere
    ajustes, y proyectos sin actividades.
    """
    base = _filtro_alcance(selectors.actividades_visibles(user), proyecto, responsable)
    if desde:
        base = base.filter(proyecto__fecha_creacion__date__gte=desde)
    if hasta:
        base = base.filter(proyecto__fecha_creacion__date__lte=hasta)

    agg = base.values("proyecto").annotate(
        total=Count("id"),
        aprobadas=Count("id", filter=Q(estado=Estado.APROBADA)),
    )
    mapa = {r["proyecto"]: r for r in agg}
    ids = [pid for pid, r in mapa.items() if r["total"] > 0 and r["total"] == r["aprobadas"]]

    proyectos = (
        Proyectos.objects.filter(id__in=ids)
        .select_related("creador_por", "asignado_a")
        .order_by("nombre")
    )
    filas = []
    for p in proyectos:
        r = mapa[p.id]
        filas.append({
            "proyecto": p,
            "responsable": p.asignado_a,
            "creador": p.creador_por,
            "total": r["total"],
            "aprobadas": r["aprobadas"],
            "fecha_creacion": p.fecha_creacion,
            "fecha_actualizacion": p.fecha_actualizacion,
            "estado": "Formulado",
        })
    return filas


# --------------------------------------------------------------------------- #
# 2. Avance por proyecto
# --------------------------------------------------------------------------- #
def avance_por_proyecto(user, *, proyecto=None, responsable=None, desde=None,
                        hasta=None, estados=None):
    """Resumen por proyecto + detalle de actividades + resumen ejecutivo.

    ``estados`` (lista de códigos) y el período (sobre la fecha de creación de
    la actividad) acotan las actividades consideradas.
    """
    base = _filtro_alcance(selectors.actividades_visibles(user), proyecto, responsable)
    if desde:
        base = base.filter(fecha_creacion__date__gte=desde)
    if hasta:
        base = base.filter(fecha_creacion__date__lte=hasta)
    if estados:
        base = base.filter(estado__in=estados)

    # Resumen por proyecto.
    agg = (
        base.values("proyecto", "proyecto__nombre")
        .annotate(
            total=Count("id"),
            aprobadas=Count("id", filter=Q(estado=Estado.APROBADA)),
            revision=Count("id", filter=Q(estado=Estado.EN_REVISION)),
            ajustes=Count("id", filter=Q(estado=Estado.AJUSTES)),
            pendientes=Count("id", filter=Q(estado=Estado.PENDIENTE)),
        )
        .order_by("proyecto__nombre")
    )
    resumen = []
    for r in agg:
        total = r["total"]
        resumen.append({
            "nombre": r["proyecto__nombre"],
            "total": total,
            "aprobadas": r["aprobadas"],
            "revision": r["revision"],
            "ajustes": r["ajustes"],
            "pendientes": r["pendientes"],
            "avance": round(r["aprobadas"] / total * 100) if total else 0,
        })

    # Detalle de actividades.
    detalle = list(
        base.select_related("proyecto", "asignado_a").order_by(
            "proyecto__nombre", "fecha_vencimiento"
        )
    )

    # Resumen ejecutivo.
    tot_act = sum(r["total"] for r in resumen)
    ejecutivo = {
        "proyectos": len(resumen),
        "actividades": tot_act,
        "aprobadas": sum(r["aprobadas"] for r in resumen),
        "revision": sum(r["revision"] for r in resumen),
        "ajustes": sum(r["ajustes"] for r in resumen),
        "pendientes": sum(r["pendientes"] for r in resumen),
        "avance_promedio": round(sum(r["avance"] for r in resumen) / len(resumen))
        if resumen else 0,
    }
    return {"ejecutivo": ejecutivo, "resumen": resumen, "detalle": detalle}
