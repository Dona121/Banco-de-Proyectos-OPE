"""Expone el rol del usuario a todas las plantillas."""
from .roles import es_coordinador, es_director, es_formulador, rol_principal


def roles(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {
        "es_director": es_director(user),
        "es_coordinador": es_coordinador(user),
        "es_formulador": es_formulador(user),
        "rol_principal": rol_principal(user),
    }
