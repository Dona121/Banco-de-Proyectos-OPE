"""Servicios de negocio del módulo de cuentas de cobro.

Aquí vive TODA la lógica de transición de estado y el gating secuencial; las
vistas son delgadas y solo invocan estas funciones. Decisiones de diseño:

* **Disparo explícito.** Los métodos del modelo (`actualizar_fecha_radicacion`,
  `actualizar_estado`, `revisar_supervisor`, `cerrar`) se llaman aquí tras cada
  acción, no por señales, para controlar el orden del gating.
* **Versionado.** Siempre se trabaja sobre la última `DocumentoEntrega`. El
  contratista NO crea versiones a mano: la versión 1 nace al crear la cuenta y,
  ante cualquier devolución, el sistema genera automáticamente una versión nueva
  (vacía). El contratista vuelve a cargar el paquete completo y pulsa "Entregar".
* **Concurrencia.** Las transiciones se envuelven en ``transaction.atomic`` y se
  bloquea la `CuentaEntrega` con ``select_for_update`` antes de evaluar/cambiar
  estado.
* **Reinicio TOTAL del ciclo (decisión definitiva).** Ante cualquier devolución
  (`AJ`/`RE`) de cualquier rol, el flujo reinicia completo desde el revisor
  jurídico: la nueva versión nace vacía y los tres roles re-revisan desde cero.
  No se arrastran revisiones ni documentos de versiones anteriores.
"""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from .models import (
    AsignacionRevisor,
    CuentaEntrega,
    DocumentoCierre,
    DocumentoEntrega,
    DocumentosCuentaCobro,
    EventoTrazabilidad,
    RequisitoDocumental,
    RevisionCuentaCobro,
    RevisionParaRadicacion,
    TramiteFinal,
)
from .roles import (
    es_contratista,
    es_radicacion,
    es_secop,
    es_supervisor,
)

# Orden estricto del gating de revisión.
SECUENCIA_ROLES = [
    RevisionCuentaCobro.Rol.JURIDICO,
    RevisionCuentaCobro.Rol.ADMINISTRATIVO,
    RevisionCuentaCobro.Rol.TECNICO,
]

# Orden estricto de los trámites finales.
SECUENCIA_TRAMITES = [
    TramiteFinal.Tipo.CARGUE_SIIFWEB,
    TramiteFinal.Tipo.CARGUE_SECOP,
]

# Nombre legible del rol de revisión para construir textos de evento.
_ROL_NOMBRE = {
    RevisionCuentaCobro.Rol.JURIDICO: "jurídico",
    RevisionCuentaCobro.Rol.ADMINISTRATIVO: "administrativo",
    RevisionCuentaCobro.Rol.TECNICO: "técnico",
}


# --------------------------------------------------------------------------- #
# Catálogo de etiquetas de evento (§8 del doc). Sin strings mágicos dispersos.
# --------------------------------------------------------------------------- #
class Eventos:
    CUENTA_CREADA = "Cuenta creada"
    ENVIADO = "Documentos enviados a revisión"
    RAD_APROBADA = "Radicación aprobada"
    RAD_DEVOLUCION = "Devolución en radicación (requiere ajustes)"
    RAD_RECHAZADA = "Radicación rechazada"
    DOC_NO_APLICA = "Documento marcado no aplica"
    NUEVA_VERSION = "Nueva versión generada"

    REVISORES_ASIGNADOS = "Revisores asignados"
    REVISOR_DECLINO = "Revisor declinó"
    REVISOR_REASIGNADO = "Revisor reasignado"

    REVISORES_APROBARON = "Revisores aprobaron la cuenta"

    SUP_APROBADO = "Aprobado por supervisor (para firma de documentos de cierre)"
    SUP_RECHAZADO = "Rechazado por supervisor"

    CIERRE_CARGADOS = "Documentos de cierre firmados cargados"
    TF_SIIFWEB = "Cargue en SIIFWEB registrado"
    TF_SECOP = "Cargue en SECOP II registrado"
    CERRADO = "Trámite cerrado"

    @staticmethod
    def aprobado_por_revisor(rol):
        return f"Aprobado por revisor {_ROL_NOMBRE[rol]}"

    @staticmethod
    def devolucion_de_revisor(rol):
        return f"Devolución de revisor {_ROL_NOMBRE[rol]}"


# Etiquetas (o prefijos) que cuentan como devolución para resaltado/filtro.
ETIQUETAS_DEVOLUCION = frozenset({
    Eventos.RAD_DEVOLUCION,
    Eventos.RAD_RECHAZADA,
    Eventos.REVISOR_DECLINO,
    Eventos.SUP_RECHAZADO,
})

_EVENTO_TRAMITE = {
    TramiteFinal.Tipo.CARGUE_SIIFWEB: Eventos.TF_SIIFWEB,
    TramiteFinal.Tipo.CARGUE_SECOP: Eventos.TF_SECOP,
}


def es_devolucion(evento):
    """True si la etiqueta de evento representa una devolución."""
    return evento in ETIQUETAS_DEVOLUCION or evento.startswith("Devolución")


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def ultima_entrega(cuenta):
    """Última versión de `DocumentoEntrega` de la cuenta (o None)."""
    return cuenta.documentoentrega_set.order_by("-numero_version").first()


def _lock(cuenta):
    """Re-lee la cuenta con bloqueo para transiciones de estado."""
    return CuentaEntrega.objects.select_for_update().get(pk=cuenta.pk)


def registrar_evento(cuenta, actor, etapa, evento, detalle=""):
    """Escribe una entrada en la bitácora de trazabilidad."""
    return EventoTrazabilidad.objects.create(
        cuenta_entrega=cuenta, actor=actor, etapa=etapa, evento=evento, detalle=detalle
    )


# --------------------------------------------------------------------------- #
# 1. Cargue, entrega y radicación
# --------------------------------------------------------------------------- #
@transaction.atomic
def crear_cuenta(usuario, vigencia, mes, comentario):
    """Crea la cuenta del contratista junto con su primera entrega (versión 1)."""
    cuenta = CuentaEntrega.objects.create(
        usuario=usuario, vigencia=vigencia, mes=mes, comentario=comentario or ""
    )
    DocumentoEntrega.objects.create(
        cuenta_entrega=cuenta, usuario=usuario, comentario=comentario or ""
    )
    registrar_evento(
        cuenta, usuario, EventoTrazabilidad.Etapa.RADICACION,
        Eventos.CUENTA_CREADA, f"Vigencia {vigencia.vigencia}, mes {mes}.",
    )
    return cuenta


def adjuntar_documento(entrega, tipo_documento, archivo):
    """Adjunta un documento de un tipo a la entrega (estado inicial: Pendiente)."""
    try:
        return DocumentosCuentaCobro.objects.create(
            documento_entrega=entrega, tipo_documento=tipo_documento, documento=archivo
        )
    except IntegrityError:
        raise ValidationError(
            "Ya cargaste un documento de ese tipo en esta versión."
        )


def tipos_obligatorios(cuenta):
    """Tipos de documento obligatorios para la vigencia de la cuenta."""
    return [
        r.tipo_documento
        for r in RequisitoDocumental.objects.filter(
            vigencia=cuenta.vigencia, obligatorio=True
        ).select_related("tipo_documento")
    ]


def documentos_faltantes(cuenta):
    """Tipos obligatorios sin cargar en la última entrega."""
    entrega = ultima_entrega(cuenta)
    if entrega is None:
        return tipos_obligatorios(cuenta)
    cargados = set(
        entrega.documentoscuentacobro_set.values_list("tipo_documento_id", flat=True)
    )
    return [t for t in tipos_obligatorios(cuenta) if t.id not in cargados]


def documentos_sin_resolver(cuenta):
    """Documentos obligatorios cargados que aún no fueron aprobados/marcados NA.

    Bloquean la aprobación de la radicación.
    """
    entrega = ultima_entrega(cuenta)
    if entrega is None:
        return []
    obligatorios = {t.id for t in tipos_obligatorios(cuenta)}
    resueltos = {
        DocumentosCuentaCobro.EstadoDocumento.APROBADO,
        DocumentosCuentaCobro.EstadoDocumento.NO_APLICA,
    }
    return [
        d
        for d in entrega.documentoscuentacobro_set.select_related("tipo_documento")
        if d.tipo_documento_id in obligatorios and d.estado not in resueltos
    ]


def revisar_documento(documento, estado, comentario="", actor=None):
    """Marca el estado/comentario de un documento.

    Lo usa el supervisor/radicación en la radicación y el revisor de turno al
    observar documentos en la revisión secuencial (estado PE/AP/RE/NA + causal).
    """
    if estado not in DocumentosCuentaCobro.EstadoDocumento.values:
        raise ValidationError("Estado de documento inválido.")
    documento.estado = estado
    documento.comentario = comentario or ""
    documento.save(update_fields=["estado", "comentario"])
    if actor is not None and estado == DocumentosCuentaCobro.EstadoDocumento.NO_APLICA:
        cuenta = documento.documento_entrega.cuenta_entrega
        registrar_evento(
            cuenta, actor, EventoTrazabilidad.Etapa.RADICACION,
            Eventos.DOC_NO_APLICA, documento.tipo_documento.nombre,
        )
    return documento


@transaction.atomic
def nueva_version(cuenta, usuario, comentario="", detalle=""):
    """Genera automáticamente una nueva versión (vacía) de la entrega.

    El ``save()`` del modelo autoincrementa ``numero_version`` y valida que la
    cuenta no esté aprobada. La versión nueva nace SIN revisiones: reinicia el
    ciclo y los tres roles vuelven a revisar desde cero. No se arrastran
    revisiones ni documentos de versiones anteriores. La crea el sistema tras una
    devolución; el contratista nunca la crea a mano.
    """
    entrega = DocumentoEntrega(
        cuenta_entrega=cuenta, usuario=cuenta.usuario,
        comentario=comentario or detalle or "Versión generada tras devolución",
    )
    entrega.save()
    registrar_evento(
        cuenta, usuario, EventoTrazabilidad.Etapa.RADICACION,
        Eventos.NUEVA_VERSION, detalle,
    )
    return entrega


def entrega_enviada(cuenta):
    """True si la última versión ya fue enviada a revisión ("Entregar")."""
    entrega = ultima_entrega(cuenta)
    if entrega is None:
        return False
    return cuenta.eventos.filter(
        evento=Eventos.ENVIADO, fecha_creacion__gte=entrega.fecha_creacion
    ).exists()


@transaction.atomic
def entregar(cuenta, usuario):
    """Acción "Entregar" del contratista: valida completitud y envía a revisión.

    No crea versiones (ya existen): solo valida el paquete obligatorio de la
    última versión y registra el evento que lo pone a disposición de radicación
    (o de la re-revisión, si ya estaba radicada).
    """
    cuenta = _lock(cuenta)
    entrega = ultima_entrega(cuenta)
    if entrega is None:
        raise ValidationError("La cuenta no tiene una versión activa.")
    faltantes = documentos_faltantes(cuenta)
    if faltantes:
        nombres = ", ".join(t.nombre for t in faltantes)
        raise ValidationError(f"Faltan documentos obligatorios: {nombres}.")
    registrar_evento(
        cuenta, usuario, EventoTrazabilidad.Etapa.RADICACION,
        Eventos.ENVIADO, f"Versión {entrega.numero_version}",
    )
    return entrega


@transaction.atomic
def registrar_revision_radicacion(cuenta, usuario, resultado, comentario):
    """Decisión de radicación (la emite el supervisor o el rol de radicación).

    - Aprobado → valida completitud y dispara ``actualizar_fecha_radicacion``.
    - Requiere ajustes → el sistema genera una nueva versión; el contratista
      vuelve a entregar el paquete completo.
    - Rechazado → queda registrado sin radicar.
    """
    cuenta = _lock(cuenta)
    Resultado = RevisionParaRadicacion.ResultadoRevision

    if resultado not in Resultado.values:
        raise ValidationError("Resultado de radicación inválido.")
    if cuenta.fecha_radicacion is not None:
        raise ValidationError("Esta cuenta ya fue radicada.")
    if not entrega_enviada(cuenta):
        raise ValidationError("El contratista aún no ha entregado los documentos.")

    if resultado == Resultado.APROBADA:
        faltantes = documentos_faltantes(cuenta)
        if faltantes:
            nombres = ", ".join(t.nombre for t in faltantes)
            raise ValidationError(f"Faltan documentos obligatorios: {nombres}.")
        pendientes = documentos_sin_resolver(cuenta)
        if pendientes:
            nombres = ", ".join(d.tipo_documento.nombre for d in pendientes)
            raise ValidationError(
                f"Hay documentos obligatorios sin aprobar: {nombres}."
            )

    revision = RevisionParaRadicacion.objects.create(
        cuenta_entrega=cuenta, supervisor=usuario,
        resultado=resultado, comentario=comentario or "",
    )

    if resultado == Resultado.APROBADA:
        cuenta.actualizar_fecha_radicacion()
        registrar_evento(
            cuenta, usuario, EventoTrazabilidad.Etapa.RADICACION,
            Eventos.RAD_APROBADA, comentario or "",
        )
    elif resultado == Resultado.AJUSTES:
        registrar_evento(
            cuenta, usuario, EventoTrazabilidad.Etapa.RADICACION,
            Eventos.RAD_DEVOLUCION, comentario or "",
        )
        nueva_version(cuenta, usuario, detalle="Devolución en radicación")
    else:
        registrar_evento(
            cuenta, usuario, EventoTrazabilidad.Etapa.RADICACION,
            Eventos.RAD_RECHAZADA, comentario or "",
        )
    return revision


# --------------------------------------------------------------------------- #
# 2. Asignación de revisores
# --------------------------------------------------------------------------- #
@transaction.atomic
def asignar_revisor(cuenta, rol, revisor, supervisor):
    """Crea la asignación activa de un rol (jurídico/administrativo/técnico)."""
    cuenta = _lock(cuenta)
    if cuenta.fecha_radicacion is None:
        raise ValidationError("No se pueden asignar revisores antes de radicar.")
    asignacion = AsignacionRevisor.reasignar_revisor(
        cuenta_entrega=cuenta, rol=rol, nuevo_revisor=revisor, supervisor=supervisor
    )
    registrar_evento(
        cuenta, supervisor, EventoTrazabilidad.Etapa.ASIGNACION,
        Eventos.REVISORES_ASIGNADOS,
        f"{asignacion.get_rol_display()}: {revisor.get_full_name() or revisor.username}",
    )
    return asignacion


@transaction.atomic
def declinar_asignacion(asignacion, motivo):
    """El revisor declina; libera el slot para reasignación."""
    asignacion.declinar(motivo)
    registrar_evento(
        asignacion.cuenta_entrega, asignacion.revisor,
        EventoTrazabilidad.Etapa.ASIGNACION,
        Eventos.REVISOR_DECLINO, f"{asignacion.get_rol_display()}: {motivo}",
    )
    return asignacion


@transaction.atomic
def reasignar(cuenta, rol, nuevo_revisor, supervisor):
    """Reasigna un rol cuyo revisor declinó."""
    cuenta = _lock(cuenta)
    asignacion = AsignacionRevisor.reasignar_revisor(
        cuenta_entrega=cuenta, rol=rol, nuevo_revisor=nuevo_revisor, supervisor=supervisor
    )
    registrar_evento(
        cuenta, supervisor, EventoTrazabilidad.Etapa.ASIGNACION,
        Eventos.REVISOR_REASIGNADO,
        f"{asignacion.get_rol_display()}: "
        f"{nuevo_revisor.get_full_name() or nuevo_revisor.username}",
    )
    return asignacion


# --------------------------------------------------------------------------- #
# 3. Revisión secuencial (gating jurídico → administrativo → técnico)
# --------------------------------------------------------------------------- #
def _roles_aprobados(entrega):
    return {
        r.rol
        for r in entrega.revisioncuentacobro_set.all()
        if r.resultado == RevisionCuentaCobro.ResultadoRevision.APROBADA
    }


def rol_habilitado(entrega, rol):
    """True si ``rol`` puede revisar ahora sobre ``entrega`` (gating secuencial)."""
    if entrega is None:
        return False
    aprobados = _roles_aprobados(entrega)
    if rol in aprobados:
        return False  # ya aprobado en esta versión
    idx = SECUENCIA_ROLES.index(rol)
    if idx == 0:
        return True
    return SECUENCIA_ROLES[idx - 1] in aprobados


@transaction.atomic
def registrar_revision(asignacion, resultado, comentario):
    """Registra la revisión de un rol sobre la última entrega, con gating.

    - Aprobado → habilita el siguiente rol; si los tres aprobaron, dispara
      ``actualizar_estado`` (estado_revisores = Aprobada).
    - Requiere ajustes / Rechazado → devuelve al contratista: el sistema genera
      una nueva versión vacía (reinicio total desde el jurídico).
    """
    cuenta = _lock(asignacion.cuenta_entrega)
    Resultado = RevisionCuentaCobro.ResultadoRevision

    if resultado not in Resultado.values:
        raise ValidationError("Resultado de revisión inválido.")
    if cuenta.fecha_radicacion is None:
        raise ValidationError("La cuenta no está radicada.")
    if asignacion.estado != AsignacionRevisor.Estado.ACTIVA:
        raise ValidationError("La asignación no está activa.")

    entrega = ultima_entrega(cuenta)
    if entrega is None:
        raise ValidationError("No hay una entrega para revisar.")
    if not rol_habilitado(entrega, asignacion.rol):
        raise ValidationError(
            "Aún no es el turno de este rol o ya fue revisado en esta versión."
        )

    revision = RevisionCuentaCobro(
        documento_entrega=entrega, asignacion=asignacion,
        rol=asignacion.rol, resultado=resultado, comentario=comentario or "",
    )
    revision.save()  # full_clean valida rol/asignación activa/cuenta no aprobada

    if resultado == Resultado.APROBADA:
        cuenta.actualizar_estado()
        registrar_evento(
            cuenta, asignacion.revisor, EventoTrazabilidad.Etapa.REVISION,
            Eventos.aprobado_por_revisor(asignacion.rol), comentario or "",
        )
        cuenta.refresh_from_db()
        if cuenta.estado_revisores == CuentaEntrega.ResultadoRevision.APROBADA:
            registrar_evento(
                cuenta, asignacion.revisor, EventoTrazabilidad.Etapa.REVISION,
                Eventos.REVISORES_APROBARON, "",
            )
    else:
        registrar_evento(
            cuenta, asignacion.revisor, EventoTrazabilidad.Etapa.REVISION,
            Eventos.devolucion_de_revisor(asignacion.rol), comentario or "",
        )
        nueva_version(
            cuenta, asignacion.revisor,
            detalle=f"Devolución de revisor {_ROL_NOMBRE[asignacion.rol]}",
        )
    return revision


# --------------------------------------------------------------------------- #
# 4. Decisión final del supervisor (aprobación para firma)
# --------------------------------------------------------------------------- #
@transaction.atomic
def decidir_supervisor(cuenta, supervisor, resultado, comentario):
    """Cierre manual del supervisor (solo si los revisores ya aprobaron).

    Aprobar habilita el cargue de los documentos de cierre firmados por el rol de
    radicación. El supervisor NO carga documentos.
    """
    cuenta = _lock(cuenta)
    cuenta.revisar_supervisor(resultado, comentario)
    aprobado = resultado == CuentaEntrega.ResultadoRevision.APROBADA
    registrar_evento(
        cuenta, supervisor, EventoTrazabilidad.Etapa.DECISION_SUPERVISOR,
        Eventos.SUP_APROBADO if aprobado else Eventos.SUP_RECHAZADO, comentario or "",
    )
    return cuenta


# --------------------------------------------------------------------------- #
# 5. Cargue de documentos de cierre firmados (por el rol de radicación)
# --------------------------------------------------------------------------- #
def documentos_cierre_faltantes(cuenta):
    """Tipos obligatorios de la vigencia que aún no se cargaron como documento de
    cierre firmado. Mismos tipos obligatorios que validan la entrega inicial."""
    cargados = set(cuenta.documentocierre_set.values_list("tipo_documento_id", flat=True))
    return [t for t in tipos_obligatorios(cuenta) if t.id not in cargados]


@transaction.atomic
def cargar_documento_cierre(cuenta, tipo_documento, archivo, usuario):
    """Carga un documento de cierre FIRMADO (mismo tipo del catálogo que la entrega
    inicial). Lo hace el rol de radicación tras la aprobación del supervisor.

    El ``clean()`` del modelo exige que el supervisor haya aprobado.
    """
    if not es_radicacion(usuario):
        raise ValidationError("Solo el rol de radicación puede cargar el cierre.")
    try:
        doc = DocumentoCierre(
            cuenta_entrega=cuenta, tipo_documento=tipo_documento,
            documento=archivo, usuario=usuario,
        )
        doc.save()  # full_clean valida cuenta aprobada
    except IntegrityError:
        raise ValidationError("Ya se cargó ese documento de cierre.")
    if not documentos_cierre_faltantes(cuenta):
        registrar_evento(
            cuenta, usuario, EventoTrazabilidad.Etapa.CIERRE,
            Eventos.CIERRE_CARGADOS, "",
        )
    return doc


# --------------------------------------------------------------------------- #
# 6. Trámites finales (SF → SC, secuenciales por rol)
# --------------------------------------------------------------------------- #
def tramite_de(cuenta, tipo):
    return cuenta.tramites_finales.filter(tipo=tipo).first()


def tramite_realizado(cuenta, tipo):
    t = tramite_de(cuenta, tipo)
    return bool(t and t.realizado)


def tramite_habilitado(cuenta, tipo):
    """True si el trámite ``tipo`` puede responderse ahora (secuencia SF→SC).

    ``SF`` se habilita cuando los documentos de cierre firmados están completos.
    """
    if cuenta.estado_supervisor != CuentaEntrega.ResultadoRevision.APROBADA:
        return False
    if documentos_cierre_faltantes(cuenta):
        return False
    if tramite_realizado(cuenta, tipo):
        return False
    idx = SECUENCIA_TRAMITES.index(tipo)
    if idx == 0:
        return True
    return tramite_realizado(cuenta, SECUENCIA_TRAMITES[idx - 1])


@transaction.atomic
def responder_tramite(cuenta, tipo, usuario, realizado, evidencia, comentario):
    """Registra la respuesta a un trámite final. Al marcar realizado exige
    evidencia (lo impone el modelo). Cierra el trámite si los dos están listos."""
    cuenta = _lock(cuenta)
    if tipo not in TramiteFinal.Tipo.values:
        raise ValidationError("Tipo de trámite inválido.")
    if not tramite_habilitado(cuenta, tipo):
        raise ValidationError("Este trámite aún no está habilitado.")

    tramite = tramite_de(cuenta, tipo) or TramiteFinal(
        cuenta_entrega=cuenta, tipo=tipo
    )
    tramite.usuario = usuario
    tramite.realizado = realizado
    tramite.comentario = comentario or ""
    if evidencia is not None:
        tramite.evidencia = evidencia
    tramite.save()  # full_clean: evidencia exige realizado y viceversa

    if realizado:
        registrar_evento(
            cuenta, usuario, EventoTrazabilidad.Etapa.CIERRE,
            _EVENTO_TRAMITE[tipo], comentario or "",
        )
        if all(tramite_realizado(cuenta, t) for t in SECUENCIA_TRAMITES):
            _cerrar(cuenta, usuario)
    return tramite


# --------------------------------------------------------------------------- #
# 7. Cierre del trámite
# --------------------------------------------------------------------------- #
def _cerrar(cuenta, usuario):
    """Cierra la cuenta (ya bloqueada y validada). Idempotente."""
    cuenta.cerrar()
    registrar_evento(
        cuenta, usuario, EventoTrazabilidad.Etapa.CIERRE, Eventos.CERRADO, ""
    )
    return cuenta


@transaction.atomic
def cerrar_tramite(cuenta, usuario):
    """Cierra el trámite tras verificar cierre + los tres trámites finales."""
    cuenta = _lock(cuenta)
    faltan_cierre = documentos_cierre_faltantes(cuenta)
    if faltan_cierre:
        nombres = ", ".join(t.nombre for t in faltan_cierre)
        raise ValidationError(f"Faltan documentos de cierre: {nombres}.")
    faltan_tramites = [t for t in SECUENCIA_TRAMITES if not tramite_realizado(cuenta, t)]
    if faltan_tramites:
        nombres = ", ".join(TramiteFinal.Tipo(t).label for t in faltan_tramites)
        raise ValidationError(f"Faltan trámites finales: {nombres}.")
    return _cerrar(cuenta, usuario)


# --------------------------------------------------------------------------- #
# 8. Notificaciones derivadas (§9) — sin modelo, calculadas en vivo
# --------------------------------------------------------------------------- #
# Tipos de notificación (alimentan el color en la plantilla).
NOTIF_ASIGNACION = "asignacion"
NOTIF_REVISION = "revision"
NOTIF_DEVOLUCION = "devolucion"
NOTIF_APROBACION = "aprobacion"


def _notif(tipo, texto, cuenta):
    return {
        "tipo": tipo,
        "texto": texto,
        "cuenta_id": cuenta.pk,
        "url": reverse("cuentas_cobro:cuenta_detalle", args=[cuenta.pk]),
        "cuenta": f"{cuenta.vigencia.vigencia} · {cuenta.get_mes_display()}",
    }


def _revisor_administrativo_activo(cuenta, user):
    return cuenta.asignacionrevisor_set.filter(
        rol=AsignacionRevisor.Rol.ADMINISTRATIVO,
        estado=AsignacionRevisor.Estado.ACTIVA,
        revisor=user,
    ).exists()


def notificaciones_para(user):
    """Lista de pendientes accionables del usuario, derivada del estado del flujo."""
    if not user.is_authenticated:
        return []
    items = []
    AP = CuentaEntrega.ResultadoRevision.APROBADA
    base = CuentaEntrega.objects.select_related("vigencia").filter(fecha_cierre__isnull=True)

    # Contratista (sobre sus propias cuentas)
    if es_contratista(user):
        for c in base.filter(usuario=user):
            if c.estado_supervisor == CuentaEntrega.ResultadoRevision.RECHAZADA:
                continue
            if c.estado_supervisor == AP:
                continue  # tras la aprobación, el cierre lo carga radicación
            if not entrega_enviada(c):
                entrega = ultima_entrega(c)
                if entrega is not None and entrega.numero_version > 1:
                    items.append(_notif(
                        NOTIF_DEVOLUCION, "Corrige y vuelve a entregar", c))
                else:
                    items.append(_notif(
                        NOTIF_REVISION, "Completa y entrega tus documentos", c))

    # Supervisor
    if es_supervisor(user):
        for c in base.filter(estado_supervisor__isnull=True):
            if c.fecha_radicacion is None:
                if entrega_enviada(c):
                    items.append(_notif(
                        NOTIF_APROBACION, "Esperando aprobación de radicación", c))
            else:
                tiene_activas = c.asignacionrevisor_set.filter(
                    estado=AsignacionRevisor.Estado.ACTIVA).exists()
                if not tiene_activas:
                    items.append(_notif(
                        NOTIF_ASIGNACION, "Radicada sin revisores asignados", c))
                elif c.estado_revisores == AP:
                    items.append(_notif(
                        NOTIF_APROBACION, "Esperando tu decisión final", c))

    # Rol de radicación
    if es_radicacion(user):
        for c in base.filter(fecha_radicacion__isnull=True, estado_supervisor__isnull=True):
            if entrega_enviada(c):
                items.append(_notif(
                    NOTIF_APROBACION, "Esperando aprobación de radicación", c))
        for c in base.filter(estado_supervisor=AP):
            if documentos_cierre_faltantes(c):
                items.append(_notif(
                    NOTIF_REVISION, "Carga los documentos de cierre firmados", c))

    # Revisores (incluye al administrativo para el trámite SIIFWEB)
    asignaciones = AsignacionRevisor.objects.filter(
        revisor=user, estado=AsignacionRevisor.Estado.ACTIVA,
        cuenta_entrega__fecha_cierre__isnull=True,
    ).select_related("cuenta_entrega__vigencia")
    for a in asignaciones:
        c = a.cuenta_entrega
        if c.estado_supervisor is None and c.fecha_radicacion is not None:
            entrega = ultima_entrega(c)
            if rol_habilitado(entrega, a.rol) and not \
                    entrega.revisioncuentacobro_set.filter(rol=a.rol).exists():
                items.append(_notif(
                    NOTIF_REVISION, f"Tienes una revisión pendiente ({a.get_rol_display()})", c))
        if c.estado_supervisor == AP and a.rol == AsignacionRevisor.Rol.ADMINISTRATIVO:
            if tramite_habilitado(c, TramiteFinal.Tipo.CARGUE_SIIFWEB):
                items.append(_notif(
                    NOTIF_REVISION, "Registra el cargue en SIIFWEB", c))

    # Rol de secop
    if es_secop(user):
        for c in base.filter(estado_supervisor=AP):
            if tramite_habilitado(c, TramiteFinal.Tipo.CARGUE_SECOP):
                items.append(_notif(
                    NOTIF_REVISION, "Registra el cargue en SECOP II", c))

    return items


# --------------------------------------------------------------------------- #
# 9. Etapa actual / flujo de la cuenta (§10) — derivado del estado
# --------------------------------------------------------------------------- #
HECHA, ACTUAL, FUTURA, RECHAZADA = "hecha", "actual", "futura", "rechazada"


def _etapa(clave, titulo, estado, detalle=""):
    return {"clave": clave, "titulo": titulo, "estado": estado, "detalle": detalle}


def flujo_de_cuenta(cuenta):
    """Lista de etapas con su estado (hecha/actual/futura/rechazada) para el stepper."""
    AP = CuentaEntrega.ResultadoRevision.APROBADA
    RE = CuentaEntrega.ResultadoRevision.RECHAZADA
    entrega = ultima_entrega(cuenta)
    version = entrega.numero_version if entrega else 1
    enviada = entrega_enviada(cuenta)
    radicada = cuenta.fecha_radicacion is not None
    activas = {
        a.rol: a
        for a in cuenta.asignacionrevisor_set.filter(
            estado=AsignacionRevisor.Estado.ACTIVA)
    }
    aprobados = _roles_aprobados(entrega) if entrega else set()

    etapas = []

    # 1. Cargue y entrega
    if enviada or radicada:
        e1 = HECHA
        det1 = f"Versión {version} entregada"
    elif version > 1:
        e1 = ACTUAL
        det1 = f"En corrección por devolución (versión {version})"
    else:
        e1 = ACTUAL
        det1 = f"Versión {version} en preparación"
    etapas.append(_etapa("entrega", "Cargue y entrega", e1, det1))

    # 2. Radicación
    if radicada:
        etapas.append(_etapa("radicacion", "Radicación", HECHA, "Aprobada"))
    elif enviada:
        etapas.append(_etapa("radicacion", "Radicación", ACTUAL, "Esperando aprobación"))
    else:
        etapas.append(_etapa("radicacion", "Radicación", FUTURA))

    # 3. Asignación de revisores
    todos_asignados = all(r in activas for r in SECUENCIA_ROLES)
    if todos_asignados:
        etapas.append(_etapa("asignacion", "Asignación de revisores", HECHA, "Revisores asignados"))
    elif radicada:
        etapas.append(_etapa("asignacion", "Asignación de revisores", ACTUAL, "Pendiente de asignar"))
    else:
        etapas.append(_etapa("asignacion", "Asignación de revisores", FUTURA))

    # 4. Revisión secuencial
    if cuenta.estado_revisores == AP:
        det4, e4 = "Los tres roles aprobaron", HECHA
    elif radicada and todos_asignados:
        en_turno = next((r for r in SECUENCIA_ROLES if rol_habilitado(entrega, r)), None)
        if en_turno is not None:
            det4 = f"En revisión {_ROL_NOMBRE[en_turno]}"
        else:
            det4 = "En revisión"
        e4 = ACTUAL
    else:
        det4, e4 = "", FUTURA
    etapas.append(_etapa("revision", "Revisión (jurídico → administrativo → técnico)", e4, det4))

    # 5. Decisión del supervisor
    if cuenta.estado_supervisor == AP:
        etapas.append(_etapa("supervisor", "Decisión del supervisor (para firma)", HECHA, "Aprobada para firma"))
    elif cuenta.estado_supervisor == RE:
        etapas.append(_etapa("supervisor", "Decisión del supervisor (para firma)", RECHAZADA, "Rechazada"))
    elif cuenta.estado_revisores == AP:
        etapas.append(_etapa("supervisor", "Decisión del supervisor (para firma)", ACTUAL, "Esperando decisión"))
    else:
        etapas.append(_etapa("supervisor", "Decisión del supervisor (para firma)", FUTURA))

    # 6. Cargue de documentos de cierre firmados (radicación)
    if cuenta.estado_supervisor == AP:
        if documentos_cierre_faltantes(cuenta):
            etapas.append(_etapa("cierre_docs", "Documentos de cierre firmados", ACTUAL, "Cargue incompleto"))
        else:
            etapas.append(_etapa("cierre_docs", "Documentos de cierre firmados", HECHA, "Cargados"))
    else:
        etapas.append(_etapa("cierre_docs", "Documentos de cierre firmados", FUTURA))

    # 7. Trámites finales
    hechos = [t for t in SECUENCIA_TRAMITES if tramite_realizado(cuenta, t)]
    if len(hechos) == len(SECUENCIA_TRAMITES):
        etapas.append(_etapa("tramites", "Trámites finales (SIIFWEB → SECOP II)", HECHA, "Completados"))
    elif cuenta.estado_supervisor == AP and not documentos_cierre_faltantes(cuenta):
        nombres = ", ".join(TramiteFinal.Tipo(t).label for t in hechos) or "ninguno"
        etapas.append(_etapa("tramites", "Trámites finales (SIIFWEB → SECOP II)", ACTUAL, f"Realizados: {nombres}"))
    else:
        etapas.append(_etapa("tramites", "Trámites finales (SIIFWEB → SECOP II)", FUTURA))

    # 8. Cierre
    if cuenta.fecha_cierre is not None:
        etapas.append(_etapa("cierre", "Cierre", HECHA, "Trámite cerrado"))
    elif len(hechos) == len(SECUENCIA_TRAMITES):
        etapas.append(_etapa("cierre", "Cierre", ACTUAL, ""))
    else:
        etapas.append(_etapa("cierre", "Cierre", FUTURA))

    return etapas


def _responsable_paso(cuenta, clave):
    """Quién debe ejecutar el paso actual."""
    if clave == "entrega":
        return "Contratista"
    if clave == "radicacion":
        return "Supervisor o rol de radicación"
    if clave == "asignacion":
        return "Supervisor"
    if clave == "revision":
        entrega = ultima_entrega(cuenta)
        en_turno = next((r for r in SECUENCIA_ROLES if rol_habilitado(entrega, r)), None)
        return f"Revisor {_ROL_NOMBRE[en_turno]}" if en_turno else "Revisores"
    if clave == "supervisor":
        return "Supervisor"
    if clave == "cierre_docs":
        return "Rol de radicación"
    if clave == "tramites":
        nxt = next((t for t in SECUENCIA_TRAMITES if not tramite_realizado(cuenta, t)), None)
        return {
            TramiteFinal.Tipo.CARGUE_SIIFWEB: "Revisor administrativo",
            TramiteFinal.Tipo.CARGUE_SECOP: "Rol de secop",
        }.get(nxt, "—")
    if clave == "cierre":
        return "Sistema (automático)"
    return "—"


def _inicio_paso(cuenta):
    """Fecha en que la cuenta entró al paso actual (último evento registrado)."""
    ultimo = cuenta.eventos.order_by("fecha_creacion").last()
    return ultimo.fecha_creacion if ultimo else cuenta.fecha_creacion


def paso_actual(cuenta):
    """Paso actual de la cuenta, responsable, desde cuándo está en él y el siguiente.

    Devuelve dict: cerrada, rechazada, titulo, detalle, responsable, desde, siguiente.
    """
    RE = CuentaEntrega.ResultadoRevision.RECHAZADA
    if cuenta.fecha_cierre is not None:
        return {
            "cerrada": True, "rechazada": False, "titulo": "Cerrada",
            "detalle": "", "responsable": "—", "desde": cuenta.fecha_cierre,
            "siguiente": "—", "siguiente_responsable": "—",
        }
    if cuenta.estado_supervisor == RE:
        return {
            "cerrada": False, "rechazada": True, "titulo": "Rechazada por el supervisor",
            "detalle": "", "responsable": "—", "desde": _inicio_paso(cuenta),
            "siguiente": "—", "siguiente_responsable": "—",
        }
    flujo = flujo_de_cuenta(cuenta)
    idx = next((i for i, e in enumerate(flujo) if e["estado"] == ACTUAL), None)
    if idx is None:
        return {
            "cerrada": False, "rechazada": False, "titulo": "En proceso",
            "detalle": "", "responsable": "—", "desde": _inicio_paso(cuenta),
            "siguiente": "—", "siguiente_responsable": "—",
        }
    actual = flujo[idx]
    if idx + 1 < len(flujo):
        sig = flujo[idx + 1]
        siguiente = sig["titulo"]
        siguiente_responsable = _responsable_paso(cuenta, sig["clave"])
    else:
        siguiente = "Finaliza el trámite"
        siguiente_responsable = "—"
    return {
        "cerrada": False, "rechazada": False,
        "titulo": actual["titulo"], "detalle": actual["detalle"],
        "responsable": _responsable_paso(cuenta, actual["clave"]),
        "desde": _inicio_paso(cuenta),
        "siguiente": siguiente,
        "siguiente_responsable": siguiente_responsable,
    }
