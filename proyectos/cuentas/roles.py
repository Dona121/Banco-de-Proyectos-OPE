"""Roles del sistema (Django Groups) y utilidades para consultarlos.

Los grupos se crean en la migración `contenido.0002_roles_groups`.
"""

DIRECTOR = "Director"
COORDINADOR = "Coordinador"
FORMULADOR = "Formulador"

# Rol transversal de SOLO LECTURA: ve ambos dominios (Proyectos y Cuentas de
# cobro) pero no puede ejecutar ninguna acción. Es la única excepción a la
# regla de aislamiento entre dominios; se crea en `contenido.0003`.
CONSULTA = "Consulta"

ROLES = (DIRECTOR, COORDINADOR, FORMULADOR)

# Quién tiene acceso al dominio Proyectos: sus tres roles más el transversal de
# solo lectura. Lo usan el mixin y el decorador que protegen sus vistas.
ROLES_MODULO = ROLES + (CONSULTA,)


def roles_de(user):
    """Conjunto de nombres de grupo del usuario, cacheado en el propio objeto.

    Cada ``es_*()`` de este módulo y del de cuentas de cobro llama aquí, y los
    context processors los consultan varias veces por petición: sin caché, una
    página sin un solo dato hacía 23 consultas a la tabla de grupos. ``request.user``
    se resuelve una vez por petición, así que cachear en el objeto equivale a
    cachear por petición. La caché se invalida sola cuando cambian los grupos
    (ver ``cuentas.apps``), para que nadie lea un rol que ya no está.
    """
    if not user.is_authenticated:
        return set()
    cacheados = getattr(user, "_roles_cache", None)
    if cacheados is None:
        cacheados = set(user.groups.values_list("name", flat=True))
        user._roles_cache = cacheados
    return cacheados


def tiene_rol(user, *nombres):
    return bool(set(nombres) & roles_de(user))


def es_director(user):
    return user.is_authenticated and (user.is_superuser or DIRECTOR in roles_de(user))


def es_coordinador(user):
    return user.is_authenticated and (user.is_superuser or COORDINADOR in roles_de(user))


def es_formulador(user):
    return user.is_authenticated and (user.is_superuser or FORMULADOR in roles_de(user))


def es_consulta(user):
    """Rol transversal de solo lectura (sin bypass de superusuario: el admin ya
    ve todo por otras vías)."""
    return user.is_authenticated and CONSULTA in roles_de(user)


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
    if CONSULTA in grupos:
        return "Consulta"
    return "Sin rol"
