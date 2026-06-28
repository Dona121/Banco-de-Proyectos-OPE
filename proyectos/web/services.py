"""Servicios de negocio: encapsulan las reglas y transiciones de estado.

Reutilizados por la app web (y disponibles para el admin). Toda la lógica de
"qué pasa con el estado de la actividad" vive aquí, no en las vistas.
"""
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from contenido.models import Actividades, ActividadEntrega, Proyectos, Revisiones
from cuentas.roles import es_coordinador, es_formulador

Estado = Actividades.EstadoActividad
Resultado = Revisiones.ResultadoRevision


@transaction.atomic
def crear_entrega(actividad, usuario, comentario):
    """Registra una nueva entrega (versión) y pone la actividad En revisión.

    El número de versión lo calcula el propio modelo. Lanza ValidationError
    (vía el ``clean()`` del modelo) si la actividad está aprobada.
    """
    entrega = ActividadEntrega(
        actividad=actividad, usuario=usuario, comentario=comentario
    )
    entrega.save()  # save() del modelo calcula numero_version y valida
    if actividad.estado != Estado.EN_REVISION:
        actividad.estado = Estado.EN_REVISION
        actividad.save(update_fields=["estado", "fecha_actualizacion"])
    return entrega


@transaction.atomic
def registrar_revision(entrega, revisor, resultado, comentario):
    """Crea la revisión de una entrega y actualiza el estado de la actividad.

    - Aprobada           → actividad APROBADA (finalizada)
    - Requiere ajustes /
      Rechazada          → actividad REQUIERE AJUSTES
    """
    revision = Revisiones(
        actividad_entrega=entrega,
        revisor=revisor,
        resultado=resultado,
        comentario=comentario,
    )
    revision.save()  # save() del modelo valida (no revisar si está aprobada)

    actividad = entrega.actividad
    if resultado == Resultado.APROBADA:
        actividad.estado = Estado.APROBADA
    else:
        actividad.estado = Estado.AJUSTES
    actividad.save(update_fields=["estado", "fecha_actualizacion"])
    return revision


# --------------------------------------------------------------------------- #
# Notificaciones derivadas (sin modelo) — pendientes accionables por usuario.
# Color por tipo: asignación=azul, revisión=verde, devolución=rojo, plazo=ámbar.
# --------------------------------------------------------------------------- #
NOTIF_ASIGNACION = "asignacion"
NOTIF_REVISION = "revision"
NOTIF_DEVOLUCION = "devolucion"
NOTIF_PLAZO = "plazo"

# Días de anticipación con que se avisa que un plazo está por cumplirse.
DIAS_AVISO_PLAZO = 3


def _notif(tipo, texto, url, contexto):
    return {"tipo": tipo, "texto": texto, "url": url, "contexto": contexto}


def notificaciones_para(user):
    """Pendientes accionables del usuario en proyectos/actividades, derivados del
    estado actual (no se persiste ningún modelo de notificaciones).

    Cubre: proyecto asignado, actividad asignada, actividad enviada (por revisar),
    actividad revisada con ajustes, y plazos por cumplirse o vencidos.
    """
    if not user.is_authenticated:
        return []
    items = []
    ahora = timezone.now()
    limite = ahora + timedelta(days=DIAS_AVISO_PLAZO)

    # Coordinador: proyectos que se le asignaron y actividades que le toca revisar.
    if es_coordinador(user):
        for p in Proyectos.objects.filter(asignado_a=user):
            if not p.actividades_set.exists():
                items.append(_notif(
                    NOTIF_ASIGNACION,
                    f"Proyecto asignado: agrega sus actividades — {p.nombre}",
                    reverse("web:proyecto_detalle", args=[p.pk]),
                    p.nombre,
                ))
        for a in Actividades.objects.filter(
            proyecto__asignado_a=user, estado=Estado.EN_REVISION
        ).select_related("proyecto"):
            items.append(_notif(
                NOTIF_REVISION,
                f"Actividad por revisar: {a.nombre}",
                reverse("web:actividad_detalle", args=[a.pk]),
                a.proyecto.nombre,
            ))

    # Formulador: actividades que le asignaron y las que le devolvieron.
    if es_formulador(user):
        for a in Actividades.objects.filter(
            asignado_a=user, estado=Estado.PENDIENTE
        ).select_related("proyecto"):
            items.append(_notif(
                NOTIF_ASIGNACION,
                f"Actividad asignada: realízala y entrégala — {a.nombre}",
                reverse("web:actividad_detalle", args=[a.pk]),
                a.proyecto.nombre,
            ))
        for a in Actividades.objects.filter(
            asignado_a=user, estado=Estado.AJUSTES
        ).select_related("proyecto"):
            items.append(_notif(
                NOTIF_DEVOLUCION,
                f"Te pidieron ajustes: corrige y vuelve a entregar — {a.nombre}",
                reverse("web:actividad_detalle", args=[a.pk]),
                a.proyecto.nombre,
            ))

    # Plazos por cumplirse o vencidos (para el responsable de la actividad).
    for a in Actividades.objects.filter(
        asignado_a=user, fecha_vencimiento__lte=limite
    ).exclude(estado=Estado.APROBADA).select_related("proyecto"):
        vencida = a.fecha_vencimiento < ahora
        texto = (
            f"Plazo vencido: {a.nombre}" if vencida
            else f"El plazo está por cumplirse: {a.nombre}"
        )
        items.append(_notif(
            NOTIF_PLAZO, texto,
            reverse("web:actividad_detalle", args=[a.pk]), a.proyecto.nombre,
        ))

    return items
