"""Selectores: querysets ya filtrados según el rol del usuario.

Centralizan la regla "un usuario nunca ve información que no le corresponda".
Las vistas SIEMPRE deben partir de estas funciones, nunca de ``Model.objects``.
"""
from django.contrib.auth import get_user_model
from django.db.models import Count, F, OuterRef, Q, Subquery

from contenido.models import Actividades, ActividadEntrega, Proyectos, Revisiones
from cuentas.roles import roles_de, CONSULTA, DIRECTOR, COORDINADOR, FORMULADOR

User = get_user_model()


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


def opciones_de_filtro(user):
    """Personas que de verdad aparecen en los proyectos visibles de ``user``.

    Una sola regla para los tres filtros en vez de una rama por rol: el alcance
    ya lo aplica ``proyectos_visibles``, así que ninguna opción puede acabar
    devolviendo una lista vacía. De ahí sale el comportamiento que se pedía (que
    el filtro dependa del rol y de las asignaciones) sin escribirlo a mano:

    * a un coordinador, "director" le ofrece solo los directores de los proyectos
      que coordina;
    * a un formulador, los directores y coordinadores de los proyectos donde
      tiene actividades, y los demás formuladores que trabajan en ellos;
    * a Consulta y al administrador, todos, porque ven todos los proyectos.

    Un proyecto tiene un único coordinador (``asignado_a``), así que el filtro de
    coordinador puede quedarse con una sola opción; cuando eso pasa no aporta
    nada y la plantilla lo esconde.
    """
    ids = list(proyectos_visibles(user).values_list("pk", flat=True))

    def personas(**filtro):
        if not ids:
            return User.objects.none()
        return (
            User.objects.filter(**filtro).distinct().order_by("first_name", "username")
        )

    return {
        "directores": personas(proyectos_creados__in=ids),
        "coordinadores": personas(proyectos_asignados__in=ids),
        "formuladores": personas(
            actividades_asignadas_a__proyecto__in=ids, groups__name=FORMULADOR
        ),
    }


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
# --------------------------------------------------------------------------- #
# Estado de una entrega: borrador o enviada
# --------------------------------------------------------------------------- #
# Una entrega no tenía momento de cierre: crearla mandaba la actividad a revisión
# en el acto, así que se podían abrir varias versiones a la vez y seguir
# adjuntando documentos a una que el revisor ya estaba mirando. Ahora crear la
# versión y entregarla son dos actos distintos, y esa diferencia se DERIVA de lo
# que ya existe (`contenido/models.py` es definitivo y no se toca):
#
#   | Estado actividad | Versión vigente   | Resultado            |
#   |------------------|-------------------|----------------------|
#   | Pendiente        | sin revisión      | borrador (editable)  |
#   | En revisión      | sin revisión      | enviada (congelada)  |
#   | Requiere ajustes | con revisión      | enviada (la devuelta)|
#   | Requiere ajustes | sin revisión      | borrador (la nueva)  |
#   | Aprobada         | con revisión      | enviada              |
#
# La regla cierra sola, sin casos especiales, porque una versión que no es la
# vigente SIEMPRE tiene revisión: es la única forma de que naciera la siguiente.
# De paso deja bien las entregas simultáneas que creó el flujo anterior: las que
# no son la vigente quedan como reemplazadas, ni editables ni revisables.
def entrega_vigente(actividad):
    """La última versión de la actividad (o None si no hay ninguna)."""
    return (
        actividad.actividadentrega_set.order_by("-numero_version").first()
    )


def entrega_enviada(entrega):
    """True si la entrega ya salió de las manos de quien la hizo."""
    if hasattr(entrega, "revisiones"):
        return True  # revisada: enviada con seguridad (OneToOne)
    if entrega.actividad.estado != Actividades.EstadoActividad.EN_REVISION:
        return False
    vigente = entrega_vigente(entrega.actividad)
    return vigente is not None and vigente.pk == entrega.pk


def borrador_de(actividad):
    """La versión abierta de la actividad, si la hay: la vigente sin enviar."""
    vigente = entrega_vigente(actividad)
    if vigente is None or entrega_enviada(vigente):
        return None
    return vigente


def puede_documentar(user, entrega):
    """Adjuntar, quitar o subir documentos: el dueño, mientras sea borrador.

    Pulsar "Realizar entrega" congela el paquete. Mientras esto no se comprobaba,
    el ejecutor podía seguir cambiando los documentos de una entrega que el
    revisor ya tenía delante.
    """
    # Una actividad aprobada cierra el expediente, incluso si esa versión no
    # llegó a tener revisión propia: los documentos que respaldan la aprobación
    # no se tocan. Sin esta guarda, una versión suelta de una actividad ya
    # aprobada se comportaría como borrador.
    if entrega.actividad.estado == Actividades.EstadoActividad.APROBADA:
        return False
    if not (user.is_superuser or entrega.usuario_id == user.id):
        return False
    # Tiene que ser EL borrador abierto, no solo "no enviada": una versión
    # reemplazada por otra posterior tampoco está enviada, y dejarla editable
    # sería seguir tocando una versión que ya nadie va a mirar.
    borrador = borrador_de(entrega.actividad)
    return borrador is not None and borrador.pk == entrega.pk


def puede_realizar_entrega(user, entrega):
    """Enviar la entrega a revisión: el dueño, con al menos un documento.

    Una entrega vacía es trabajo perdido para los dos: el revisor solo puede
    devolverla y el ciclo reinicia desde cero.
    """
    if not puede_documentar(user, entrega):
        return False
    return entrega.documentos_set.exists()


def puede_crear_entrega(user, actividad):
    """Abrir una versión nueva: el ejecutor asignado, si no hay otra abierta.

    Solo cuando la actividad está Pendiente (aún no ha entregado nada) o en
    Requiere ajustes (se la devolvieron). Con una entrega en revisión no se abre
    otra, que es lo que permitía las entregas simultáneas.
    """
    Estado = Actividades.EstadoActividad
    if actividad.estado not in (Estado.PENDIENTE, Estado.AJUSTES):
        return False
    if not (user.is_superuser or actividad.asignado_a_id == user.id):
        return False
    return borrador_de(actividad) is None


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
    """Revisa quien determine ``responsable_revision``, sobre una entrega que de
    verdad se entregó y todavía no tiene revisión."""
    if entrega.actividad.estado == Actividades.EstadoActividad.APROBADA:
        return False
    if hasattr(entrega, "revisiones"):
        return False  # ya tiene revisión (OneToOne)
    if not entrega_enviada(entrega):
        return False  # borrador: el ejecutor aún no ha pulsado "Realizar entrega"
    # Nadie revisa su propia entrega, ni siquiera el administrador. Es alcanzable
    # sin hacer nada raro: basta con que quien ejecuta la actividad sea también
    # el director del proyecto, y entonces `responsable_revision` lo devuelve a
    # él mismo. Separar quien hace de quien aprueba es la razón de ser de este
    # paso, así que esta guarda va por encima del atajo de superusuario.
    if entrega.usuario_id == user.id:
        return False
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
        .filter(
            revisiones__isnull=True,
            # Solo lo entregado: una actividad Pendiente o en Requiere ajustes
            # tiene un borrador abierto, que no es asunto del revisor.
            actividad__estado=Actividades.EstadoActividad.EN_REVISION,
        )
        # Y solo la versión vigente. El flujo anterior dejó actividades con
        # varias entregas sin revisar a la vez; las que no son la última están
        # reemplazadas y `puede_revisar` ya las rechaza, así que listarlas sería
        # ofrecer un trabajo que al pulsarlo da 403.
        .annotate(
            _ultima_version=Subquery(
                ActividadEntrega.objects.filter(actividad=OuterRef("actividad"))
                .order_by("-numero_version")
                .values("numero_version")[:1]
            )
        )
        .filter(numero_version=F("_ultima_version"))
    )
    if user.is_superuser:
        return qs.distinct()
    return qs.filter(
        (ejecutor_coord & Q(actividad__proyecto__creador_por=user))
        | (~ejecutor_coord & Q(actividad__proyecto__asignado_a=user))
    ).distinct()
