"""Selectores: querysets filtrados por rol y permisos a nivel de objeto.

Las vistas SIEMPRE parten de estas funciones, nunca de ``Model.objects``.

Alcance por rol (§8 del doc): el acceso a una cuenta se deriva de la intervención
real del usuario. Para que los roles que aprueban/registran puedan actuar antes de
intervenir, el alcance incluye además las cuentas accionables de su etapa.
"""
from django.db.models import Q

from cuentas.roles import CONSULTA

from . import services
from .models import AsignacionRevisor, CuentaEntrega, RevisionCuentaCobro, TramiteFinal
from .roles import (
    CONTRATISTA,
    RADICACION,
    REVISOR,
    SECOP,
    SUPERVISOR,
    es_contratista,
    es_radicacion,
    es_secop,
    puede_aprobar_radicacion as rol_aprueba_radicacion,
    roles_de,
)

_AP = CuentaEntrega.ResultadoRevision.APROBADA


# --------------------------------------------------------------------------- #
# Cuentas visibles por rol
# --------------------------------------------------------------------------- #
def cuentas_visibles(user):
    qs = CuentaEntrega.objects.select_related("usuario", "vigencia")
    if user.is_superuser:
        return qs
    grupos = roles_de(user)
    if CONSULTA in grupos:
        return qs  # rol transversal de solo lectura: ve todas las cuentas
    condiciones = Q(pk__in=[])  # vacío por defecto

    if CONTRATISTA in grupos:
        condiciones |= Q(usuario=user)
    if REVISOR in grupos:
        # Cuentas donde tiene (o tuvo) alguna asignación (incluidas declinadas).
        condiciones |= Q(asignacionrevisor__revisor=user)
    if SUPERVISOR in grupos:
        condiciones |= (
            Q(revisionpararadicacion__supervisor=user)
            | Q(asignacionrevisor__supervisor=user)
            | Q(estado_supervisor__isnull=True, eventos__evento=services.Eventos.ENVIADO)
        )
    if RADICACION in grupos:
        condiciones |= (
            Q(revisionpararadicacion__supervisor=user)
            | Q(tramites_finales__usuario=user)
            | Q(fecha_radicacion__isnull=True, eventos__evento=services.Eventos.ENVIADO)
            | Q(estado_supervisor=_AP)
        )
    if SECOP in grupos:
        condiciones |= Q(tramites_finales__usuario=user) | Q(estado_supervisor=_AP)

    return qs.filter(condiciones).distinct()


# --------------------------------------------------------------------------- #
# Permisos a nivel de objeto (defensa adicional en vistas de acción)
# --------------------------------------------------------------------------- #
def es_dueno(user, cuenta):
    return cuenta.usuario_id == user.id


def puede_cargar_documentos(user, cuenta):
    """Solo el contratista dueño carga documentos, y solo mientras la versión
    actual siga abierta.

    Pulsar "Entregar" congela el paquete. Mientras esto no se comprobaba, un POST
    directo podía colar un documento en una versión ya entregada: entraba en
    PENDIENTE y dejaba al revisor de turno sin ninguna acción válida: no podía
    aprobar (hay un pendiente) ni devolver (no hay ninguno rechazado). Tras una
    devolución nace una versión nueva, sin enviar, y el cargue se reabre solo.
    """
    if cuenta.estado_supervisor == _AP:
        return False
    if services.radicacion_rechazada(cuenta):
        return False
    if services.entrega_enviada(cuenta):
        return False
    return es_contratista(user) and es_dueno(user, cuenta)


def puede_entregar(user, cuenta):
    """El contratista puede pulsar "Entregar" si el paquete está completo y no
    ha sido enviado todavía en esta versión."""
    if not puede_cargar_documentos(user, cuenta):
        return False
    if services.documentos_faltantes(cuenta):
        return False
    return not services.entrega_enviada(cuenta)


def puede_eliminar_documento(user, documento):
    """Quitar un documento de la entrega: lo mismo que hace falta para cargarlo.

    Si el paquete sigue abierto para añadir, también lo está para corregir un
    cargue equivocado; en cuanto se entrega, queda congelado para las dos cosas.
    """
    return puede_cargar_documentos(user, documento.documento_entrega.cuenta_entrega)


# Un documento de cierre NO se quita. Se permitió un tiempo, para corregir un
# cargue equivocado, y resultó contraproducente: completar el cierre habilita el
# primer trámite final, y retirar un documento después lo deshabilitaba, de modo
# que la cuenta parecía trabada. Además `DocumentoCierre` es único por (cuenta,
# tipo), así que tras borrarlo tampoco se podía recargar el mismo tipo.


def puede_radicar(user, cuenta):
    """Aprueban la radicación el supervisor o el rol de radicación, tras la
    entrega del contratista y antes de radicar. Un rechazo definitivo en
    radicación es terminal: ya no se puede volver a decidir."""
    if cuenta.fecha_radicacion is not None:
        return False
    if services.radicacion_rechazada(cuenta):
        return False
    if not rol_aprueba_radicacion(user):
        return False
    return services.entrega_enviada(cuenta)


def es_supervisor_de(user):
    return user.is_superuser or SUPERVISOR in roles_de(user)


def asignaciones_activas_de(user, cuenta):
    """Asignaciones activas de ``user`` como revisor en ``cuenta``."""
    return cuenta.asignacionrevisor_set.filter(
        revisor=user, estado=AsignacionRevisor.Estado.ACTIVA
    )


def puede_revisar(user, asignacion):
    """El revisor asignado puede revisar si su rol está habilitado por el gating."""
    if asignacion.estado != AsignacionRevisor.Estado.ACTIVA:
        return False
    if not (user.is_superuser or asignacion.revisor_id == user.id):
        return False
    entrega = services.ultima_entrega(asignacion.cuenta_entrega)
    return services.rol_habilitado(entrega, asignacion.rol)


def puede_marcar_documentos(user, cuenta):
    """Quién puede marcar el estado (AP/RE/NA) de los documentos de la entrega.

    - El supervisor o el rol de radicación, durante la revisión de radicación.
    - El revisor cuyo rol está en turno, durante la revisión secuencial.
    Bloqueado una vez el supervisor aprobó la cuenta.
    """
    if cuenta.estado_supervisor == _AP:
        return False
    if cuenta.fecha_radicacion is None:
        # Un rechazo definitivo en radicación cierra la cuenta: no tiene sentido
        # seguir cambiando el estado de sus documentos. Y antes de que el
        # contratista entregue tampoco hay nada que revisar.
        if services.radicacion_rechazada(cuenta) or not services.entrega_enviada(cuenta):
            return False
        return rol_aprueba_radicacion(user)
    return any(puede_revisar(user, a) for a in asignaciones_activas_de(user, cuenta))


def puede_cargar_cierre(user, cuenta):
    """El rol de radicación carga los documentos de cierre firmados tras la
    aprobación del supervisor, mientras falte alguno."""
    if cuenta.estado_supervisor != _AP:
        return False
    if not es_radicacion(user):
        return False
    return bool(services.documentos_cierre_faltantes(cuenta))


def _es_el_rol_del_tramite(user, cuenta, tipo):
    """A quién le toca ese trámite: secop el de SECOP II, el revisor
    administrativo de la cuenta el de SIIFWEB."""
    Tipo = TramiteFinal.Tipo
    if tipo == Tipo.CARGUE_SECOP:
        return es_secop(user)
    if tipo == Tipo.CARGUE_SIIFWEB:
        return user.is_superuser or cuenta.asignacionrevisor_set.filter(
            rol=AsignacionRevisor.Rol.ADMINISTRATIVO,
            estado=AsignacionRevisor.Estado.ACTIVA,
            revisor=user,
        ).exists()
    return False


def puede_responder_tramite(user, cuenta, tipo):
    """Cada trámite final solo lo responde su rol, y solo si está habilitado."""
    if tipo not in TramiteFinal.Tipo.values:
        return False
    if not services.tramite_habilitado(cuenta, tipo):
        return False
    return _es_el_rol_del_tramite(user, cuenta, tipo)


# La evidencia de un trámite respondido NO se reemplaza. Existió un tiempo, para
# corregir una captura equivocada, con una ventana que se cerraba al responder el
# trámite siguiente. Se retiró porque el cargue pide confirmación antes de
# registrar nada: ahí es donde se evita el archivo equivocado, y un soporte que se
# puede cambiar después debilita el valor probatorio del expediente. Un error que
# se detecte más tarde se corrige por el admin, no por la aplicación.


# --------------------------------------------------------------------------- #
# Tablero de revisión (para el detalle de la cuenta)
# --------------------------------------------------------------------------- #
def tablero_revisiones(cuenta):
    """Estado de cada rol sobre la última entrega: asignación, revisión y gating."""
    entrega = services.ultima_entrega(cuenta)
    asignaciones = {
        a.rol: a
        for a in cuenta.asignacionrevisor_set.filter(
            estado=AsignacionRevisor.Estado.ACTIVA
        ).select_related("revisor")
    }
    revisiones = {}
    if entrega is not None:
        revisiones = {
            r.rol: r
            for r in entrega.revisioncuentacobro_set.select_related(
                "asignacion__revisor"
            )
        }
    filas = []
    Rol = RevisionCuentaCobro.Rol
    for rol in services.SECUENCIA_ROLES:
        filas.append({
            "rol": rol,
            "rol_label": Rol(rol).label,
            "asignacion": asignaciones.get(rol),
            "revision": revisiones.get(rol),
            "habilitado": services.rol_habilitado(entrega, rol),
        })
    return filas


def tramites_finales(cuenta):
    """Estado de cada trámite final para el detalle (habilitado/realizado)."""
    filas = []
    preguntas = {
        TramiteFinal.Tipo.CARGUE_SIIFWEB: "¿Se cargó a SIIFWEB?",
        TramiteFinal.Tipo.CARGUE_SECOP: "¿Se cargó a SECOP II?",
    }
    # Lo que el usuario lee justo antes de confirmar. Responder un trámite no se
    # deshace, y el último además cierra la cuenta: conviene decirlo ahí, no en
    # un manual que nadie tiene abierto en ese momento.
    avisos = {
        TramiteFinal.Tipo.CARGUE_SIIFWEB: (
            "Al confirmar, el paso queda registrado como realizado. Después solo "
            "podrás cambiar el soporte hasta que se responda el cargue en SECOP II."
        ),
        TramiteFinal.Tipo.CARGUE_SECOP: (
            "Al confirmar, el paso queda registrado y la cuenta se cierra. El "
            "soporte ya no se podrá cambiar."
        ),
    }
    for tipo in services.SECUENCIA_TRAMITES:
        filas.append({
            "tipo": tipo,
            "tipo_label": TramiteFinal.Tipo(tipo).label,
            "pregunta": preguntas[tipo],
            "aviso": avisos[tipo],
            "tramite": services.tramite_de(cuenta, tipo),
            "habilitado": services.tramite_habilitado(cuenta, tipo),
            "realizado": services.tramite_realizado(cuenta, tipo),
        })
    return filas
