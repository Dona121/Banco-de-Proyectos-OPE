"""Roles del sistema (Django Groups) y utilidades para consultarlos.

Los grupos se crean en la migración `contenido.0002_roles_groups`.
"""

DIRECTOR = "Director"
COORDINADOR = "Coordinador"
FORMULADOR = "Formulador"

ROLES = (DIRECTOR, COORDINADOR, FORMULADOR)


def roles_de(user):
    """Conjunto de nombres de grupo del usuario."""
    if not user.is_authenticated:
        return set()
    return set(user.groups.values_list("name", flat=True))


def tiene_rol(user, *nombres):
    return bool(set(nombres) & roles_de(user))


def es_director(user):
    return user.is_authenticated and (user.is_superuser or DIRECTOR in roles_de(user))


def es_coordinador(user):
    return user.is_authenticated and (user.is_superuser or COORDINADOR in roles_de(user))


def es_formulador(user):
    return user.is_authenticated and (user.is_superuser or FORMULADOR in roles_de(user))


def rol_principal(user):
    """Rol "principal" para mostrar en la interfaz (jerarquía Director > Coord > Form)."""
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return "Administrador"
    grupos = roles_de(user)
    for rol in ROLES:
        if rol in grupos:
            return rol
    return "Sin rol"
