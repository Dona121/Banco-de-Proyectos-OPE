"""Roles del módulo "Gestión de cuentas de cobro" (Django Groups).

Estos grupos son propios del módulo y se crean en migraciones de datos. NO se
reutilizan los roles de la app de proyectos (Director/Coordinador/Formulador):
este es un módulo aparte con su propio control de acceso.

El rol específico del revisor (jurídico / administrativo / técnico) NO se modela
como grupo: lo determina la `AsignacionRevisor` de cada cuenta. Basta con que el
usuario pertenezca al grupo ``Revisor`` y esté asignado al rol correspondiente.

Roles que SÍ son grupo:
- ``Contratista`` — crea la cuenta, carga documentos y los de cierre.
- ``Supervisor`` — radica, asigna revisores y decide para firma.
- ``Revisor`` — revisa en su rol (JU/AD/TE) según la asignación.
- ``Radicacion`` — aprueba radicación (como el supervisor) y responde el trámite
  final de entrega de documentos de cierre (`EC`).
- ``Secop`` — responde el trámite final de cargue en SECOP II (`SC`).
"""

CONTRATISTA = "Contratista"
SUPERVISOR = "Supervisor"
REVISOR = "Revisor"
RADICACION = "Radicacion"
SECOP = "Secop"

ROLES = (CONTRATISTA, SUPERVISOR, REVISOR, RADICACION, SECOP)


def roles_de(user):
    """Conjunto de nombres de grupo del usuario."""
    if not user.is_authenticated:
        return set()
    return set(user.groups.values_list("name", flat=True))


def tiene_rol(user, *nombres):
    return bool(set(nombres) & roles_de(user))


def es_contratista(user):
    return user.is_authenticated and (user.is_superuser or CONTRATISTA in roles_de(user))


def es_supervisor(user):
    return user.is_authenticated and (user.is_superuser or SUPERVISOR in roles_de(user))


def es_revisor(user):
    return user.is_authenticated and (user.is_superuser or REVISOR in roles_de(user))


def es_radicacion(user):
    return user.is_authenticated and (user.is_superuser or RADICACION in roles_de(user))


def es_secop(user):
    return user.is_authenticated and (user.is_superuser or SECOP in roles_de(user))


def puede_aprobar_radicacion(user):
    """Aprueban la radicación tanto el supervisor como el rol de radicación."""
    return es_supervisor(user) or es_radicacion(user)


def usa_modulo(user):
    """True si el usuario participa en el módulo de cuentas de cobro."""
    return (
        es_contratista(user)
        or es_supervisor(user)
        or es_revisor(user)
        or es_radicacion(user)
        or es_secop(user)
    )
