"""Selectores: querysets ya filtrados según el rol del usuario.

Centralizan la regla "un usuario nunca ve información que no le corresponda".
Las vistas SIEMPRE deben partir de estas funciones, nunca de ``Model.objects``.
"""
from django.db.models import Count, Q

from contenido.models import Actividades, ActividadEntrega, Proyectos, Revisiones
from cuentas.roles import roles_de, CONSULTA, DIRECTOR, COORDINADOR, FORMULADOR


# --------------------------------------------------------------------------- #
# Proyectos
# --------------------------------------------------------------------------- #
def proyectos_visibles(user):
    qs = Proyectos.objects.select_related("creador_por", "asignado_a").annotate(
        n_actividades=Count("actividades", distinct=True)
    )
    if user.is_superuser:
        return qs
    grupos = roles_de(user)
    if CONSULTA in grupos:
        return qs  # solo lectura: ve todo
    if DIRECTOR in grupos:
        return qs.filter(creador_por=user)
    if COORDINADOR in grupos:
        return qs.filter(asignado_a=user)
    if FORMULADOR in grupos:
        return qs.filter(actividades__asignado_a=user).distinct()
    return qs.none()


# --------------------------------------------------------------------------- #
# Actividades
# --------------------------------------------------------------------------- #
def actividades_visibles(user):
    qs = Actividades.objects.select_related(
        "proyecto", "asignado_a", "asignado_por"
    )
    if user.is_superuser:
        return qs
    grupos = roles_de(user)
    if CONSULTA in grupos:
        return qs  # solo lectura: ve todo
    if DIRECTOR in grupos:
        return qs.filter(proyecto__creador_por=user)
    if COORDINADOR in grupos:
        # Las actividades de los proyectos que coordina y, además, las que le
        # asignaron a él como ejecutor (el director puede asignarle actividades).
        return qs.filter(
            Q(proyecto__asignado_a=user) | Q(asignado_a=user)
        ).distinct()
    if FORMULADOR in grupos:
        return qs.filter(asignado_a=user)
    return qs.none()


# --------------------------------------------------------------------------- #
# Entregas
# --------------------------------------------------------------------------- #
def entregas_visibles(user):
    qs = ActividadEntrega.objects.select_related(
        "actividad", "actividad__proyecto", "usuario"
    )
    if user.is_superuser:
        return qs
    grupos = roles_de(user)
    if CONSULTA in grupos:
        return qs  # solo lectura: ve todo
    if DIRECTOR in grupos:
        return qs.filter(actividad__proyecto__creador_por=user)
    if COORDINADOR in grupos:
        return qs.filter(
            Q(actividad__proyecto__asignado_a=user) | Q(actividad__asignado_a=user)
        ).distinct()
    if FORMULADOR in grupos:
        return qs.filter(actividad__asignado_a=user)
    return qs.none()


# --------------------------------------------------------------------------- #
# Revisiones
# --------------------------------------------------------------------------- #
def revisiones_visibles(user):
    qs = Revisiones.objects.select_related(
        "actividad_entrega__actividad__proyecto", "revisor"
    )
    if user.is_superuser:
        return qs
    grupos = roles_de(user)
    if CONSULTA in grupos:
        return qs  # solo lectura: ve todo
    if DIRECTOR in grupos:
        return qs.filter(actividad_entrega__actividad__proyecto__creador_por=user)
    if COORDINADOR in grupos:
        return qs.filter(
            Q(actividad_entrega__actividad__proyecto__asignado_a=user)
            | Q(actividad_entrega__actividad__asignado_a=user)
        ).distinct()
    if FORMULADOR in grupos:
        return qs.filter(actividad_entrega__actividad__asignado_a=user)
    return qs.none()


# --------------------------------------------------------------------------- #
# Permisos a nivel de objeto (defensa adicional en las vistas de acción)
# --------------------------------------------------------------------------- #
def puede_crear_entrega(user, actividad):
    """El ejecutor asignado puede entregar si la actividad no está aprobada."""
    if actividad.estado == Actividades.EstadoActividad.APROBADA:
        return False
    return user.is_superuser or actividad.asignado_a_id == user.id


def puede_editar_actividad(user, actividad):
    """Solo el director o el coordinador que **creó** la actividad puede editar su
    nombre y fechas. El creador queda registrado en ``asignado_por``.

    Un formulador nunca crea actividades, así que la comprobación por
    ``asignado_por`` ya lo excluye; el chequeo de rol es defensa explícita.

    Una actividad **aprobada** ya no se puede editar (ni por su creador).
    """
    if actividad.estado == Actividades.EstadoActividad.APROBADA:
        return False
    if user.is_superuser:
        return True
    grupos = roles_de(user)
    if DIRECTOR not in grupos and COORDINADOR not in grupos:
        return False
    return actividad.asignado_por_id == user.id


def _ejecutor_es_coordinador(actividad):
    """El ejecutor de la actividad (asignado_a) pertenece al grupo Coordinador."""
    return actividad.asignado_a.groups.filter(name=COORDINADOR).exists()


def responsable_revision(actividad):
    """Fuente ÚNICA de verdad de quién revisa las entregas de una actividad.

    - Si el ejecutor es un **coordinador** → revisa el **director** del proyecto
      (``proyecto.creador_por``).
    - Si el ejecutor es un **formulador** → revisa el **coordinador** del proyecto
      (``proyecto.asignado_a``), como en el flujo original.
    """
    proyecto = actividad.proyecto
    if _ejecutor_es_coordinador(actividad):
        return proyecto.creador_por
    return proyecto.asignado_a


def puede_revisar(user, entrega):
    """Revisa quien determine ``responsable_revision``, si la actividad no está
    aprobada y la entrega aún no tiene revisión."""
    if entrega.actividad.estado == Actividades.EstadoActividad.APROBADA:
        return False
    if hasattr(entrega, "revisiones"):
        return False  # ya tiene revisión (OneToOne)
    if user.is_superuser:
        return True
    return responsable_revision(entrega.actividad).id == user.id


def entregas_por_revisar(user):
    """Entregas sin revisar cuya revisión le corresponde a ``user`` (regla B).

    Une los dos caminos en una sola consulta: ejecutor coordinador → la revisa el
    director del proyecto; ejecutor formulador → la revisa el coordinador.
    """
    ejecutor_coord = Q(actividad__asignado_a__groups__name=COORDINADOR)
    qs = (
        ActividadEntrega.objects.select_related(
            "actividad", "actividad__proyecto", "usuario"
        )
        .filter(revisiones__isnull=True)
        .exclude(actividad__estado=Actividades.EstadoActividad.APROBADA)
    )
    if user.is_superuser:
        return qs.distinct()
    return qs.filter(
        (ejecutor_coord & Q(actividad__proyecto__creador_por=user))
        | (~ejecutor_coord & Q(actividad__proyecto__asignado_a=user))
    ).distinct()
