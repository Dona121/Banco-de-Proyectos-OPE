"""Selectores: querysets ya filtrados según el rol del usuario.

Centralizan la regla "un usuario nunca ve información que no le corresponda".
Las vistas SIEMPRE deben partir de estas funciones, nunca de ``Model.objects``.
"""
from django.db.models import Count, Q

from contenido.models import Actividades, ActividadEntrega, Proyectos, Revisiones
from cuentas.roles import roles_de, DIRECTOR, COORDINADOR, FORMULADOR


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
    if DIRECTOR in grupos:
        return qs.filter(proyecto__creador_por=user)
    if COORDINADOR in grupos:
        # Todas las actividades de los proyectos que coordina.
        return qs.filter(proyecto__asignado_a=user)
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
    if DIRECTOR in grupos:
        return qs.filter(actividad__proyecto__creador_por=user)
    if COORDINADOR in grupos:
        return qs.filter(actividad__proyecto__asignado_a=user)
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
    if DIRECTOR in grupos:
        return qs.filter(actividad_entrega__actividad__proyecto__creador_por=user)
    if COORDINADOR in grupos:
        return qs.filter(actividad_entrega__actividad__proyecto__asignado_a=user)
    if FORMULADOR in grupos:
        return qs.filter(actividad_entrega__actividad__asignado_a=user)
    return qs.none()


# --------------------------------------------------------------------------- #
# Permisos a nivel de objeto (defensa adicional en las vistas de acción)
# --------------------------------------------------------------------------- #
def puede_crear_entrega(user, actividad):
    """El formulador asignado puede entregar si la actividad no está aprobada."""
    if actividad.estado == Actividades.EstadoActividad.APROBADA:
        return False
    return user.is_superuser or actividad.asignado_a_id == user.id


def puede_revisar(user, entrega):
    """El coordinador del proyecto revisa, si la actividad no está aprobada."""
    if entrega.actividad.estado == Actividades.EstadoActividad.APROBADA:
        return False
    if hasattr(entrega, "revisiones"):
        return False  # ya tiene revisión (OneToOne)
    return user.is_superuser or entrega.actividad.proyecto.asignado_a_id == user.id
