"""Expone las notificaciones derivadas de proyectos/actividades a las plantillas."""
from cuentas.roles import es_consulta, es_coordinador, es_director, es_formulador

from . import services


def notificaciones_web(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    # `web_ver_modulo`: quién ve la navegación del dominio Proyectos (incluye el
    # rol transversal de solo lectura Consulta).
    # `web_usa_modulo`: quién participa de verdad (tiene notificaciones y campana).
    usa = es_director(user) or es_coordinador(user) or es_formulador(user)
    if not usa:
        return {"web_usa_modulo": False, "web_ver_modulo": es_consulta(user)}
    notificaciones = services.notificaciones_para(user)
    return {
        "web_usa_modulo": True,
        "web_ver_modulo": True,
        "web_notificaciones": notificaciones,
        "web_notificaciones_total": len(notificaciones),
    }
