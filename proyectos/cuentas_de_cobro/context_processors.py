"""Expone roles y notificaciones del módulo de cuentas de cobro a las plantillas."""
from . import services
from .roles import (
    es_contratista,
    es_radicacion,
    es_revisor,
    es_secop,
    es_supervisor,
    usa_modulo,
)


def roles_cuentas_cobro(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    if not usa_modulo(user):
        return {"cc_usa_modulo": False}
    notificaciones = services.notificaciones_para(user)
    return {
        "cc_es_contratista": es_contratista(user),
        "cc_es_supervisor": es_supervisor(user),
        "cc_es_revisor": es_revisor(user),
        "cc_es_radicacion": es_radicacion(user),
        "cc_es_secop": es_secop(user),
        "cc_usa_modulo": True,
        "cc_notificaciones": notificaciones,
        "cc_notificaciones_total": len(notificaciones),
    }
