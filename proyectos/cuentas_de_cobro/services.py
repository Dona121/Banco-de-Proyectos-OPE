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
  técnico: la nueva versión nace vacía, el contratista recarga y vuelve a entregar,
  y los tres roles re-revisan desde cero. No se arrastran revisiones ni documentos
  de versiones anteriores.
"""
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
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

# Orden estricto del gating de revisión: técnico → jurídico → administrativo.
# (El técnico va primero porque es quien suele pedir más ajustes; así una
# devolución reinicia el ciclo desde él y ahorra revisiones repetidas.)
SECUENCIA_ROLES = [
    RevisionCuentaCobro.Rol.TECNICO,
    RevisionCuentaCobro.Rol.JURIDICO,
    RevisionCuentaCobro.Rol.ADMINISTRATIVO,
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
    DOC_ELIMINADO = "Documento retirado por el contratista"
    CIERRE_ELIMINADO = "Documento de cierre retirado"
    NUEVA_VERSION = "Nueva versión generada"

    REVISORES_ASIGNADOS = "Revisores asignados"
    REVISOR_DECLINO = "Revisor declinó"
    REVISOR_REASIGNADO = "Revisor reasignado"

    REVISORES_APROBARON = "Revisores aprobaron la cuenta"

    SUP_APROBADO = "Aprobado por supervisor (para firma de documentos de cierre)"
    SUP_RECHAZADO = "Rechazado por supervisor"

    CIERRE_CARGADOS = "Documentos de cierre firmados cargados"
    EVIDENCIA_REEMPLAZADA = "Evidencia de trámite reemplazada"
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
# --------------------------------------------------------------------------- #
# Lectura del estado del flujo
# --------------------------------------------------------------------------- #
# Las funciones de esta sección resuelven cada dato con ``.all()`` y filtran en
# Python, en vez de usar ``.filter()/.exists()``, que siempre van a la base. Así
# una vista que precargue las relaciones (ver RELACIONES_DEL_FLUJO) las sirve sin
# una sola consulta extra, mientras quien no precargue nada se comporta igual que
# antes. Los volúmenes por cuenta son de decenas de filas (es el expediente de un
# mes de un contratista), así que filtrar en memoria no cuesta nada.
#
# OJO: no memorizar estos resultados en la instancia. Hay servicios que consultan,
# mutan y vuelven a consultar (``responder_tramite`` relee ``tramite_realizado``
# para decidir si cierra la cuenta), y un valor memorizado dejaría la cuenta sin
# cerrar. Con ``.all()`` eso no pasa: los objetos que llegan a los servicios no
# traen precarga, así que cada llamada va a la base.
RELACIONES_DEL_FLUJO = (
    "documentoentrega_set",
    "documentoentrega_set__revisioncuentacobro_set",
    "eventos",
    "revisionpararadicacion_set",
    "asignacionrevisor_set",
    "documentocierre_set",
    "tramites_finales",
)


def ultima_entrega(cuenta):
    """Última versión de `DocumentoEntrega` de la cuenta (o None)."""
    entregas = cuenta.documentoentrega_set.all()
    return max(entregas, key=lambda e: e.numero_version, default=None)


def _lock(cuenta):
    """Re-lee la cuenta con bloqueo para transiciones de estado."""
    return CuentaEntrega.objects.select_for_update().get(pk=cuenta.pk)


def _asignar_archivo(campo, archivo, clave):
    """Pone en un ``FileField`` un archivo recién subido o uno que ya está arriba.

    Con ``clave`` solo se guarda la ruta dentro del bucket: el navegador ya subió
    el archivo directamente y volver a transferirlo no tendría sentido. Asignar
    ``.name`` es la forma de hacerlo sin tocar los modelos, que son definitivos.
    """
    if clave:
        campo.name = clave
    elif archivo is not None:
        campo.save(archivo.name, archivo, save=False)
    else:
        raise ValidationError("No se recibió ningún archivo.")


def registrar_evento(cuenta, actor, etapa, evento, detalle=""):
    """Escribe una entrada en la bitácora de trazabilidad."""
    return EventoTrazabilidad.objects.create(
        cuenta_entrega=cuenta, actor=actor, etapa=etapa, evento=evento, detalle=detalle
    )


# --------------------------------------------------------------------------- #
# 1. Cargue, entrega y radicación
# --------------------------------------------------------------------------- #
def cuenta_rechazada(cuenta):
    """True si la cuenta terminó rechazada y no continúa.

    Son los dos rechazos terminales del flujo: el definitivo en radicación y el
    del supervisor en su decisión final.
    """
    return (
        cuenta.estado_supervisor == CuentaEntrega.ResultadoRevision.RECHAZADA
        or radicacion_rechazada(cuenta)
    )


def cuenta_aprobada(cuenta):
    """True si el supervisor aprobó la cuenta (siga en cierre/trámites o ya cerrada)."""
    return (
        cuenta.estado_supervisor == CuentaEntrega.ResultadoRevision.APROBADA
        or cuenta.fecha_cierre is not None
    )


@transaction.atomic
def crear_cuenta(usuario, vigencia, mes, comentario):
    """Crea la cuenta del contratista junto con su primera entrega (versión 1).

    Un contratista tiene una sola cuenta **vigente** por vigencia y mes, y lo que
    cuenta es el estado, no la simple existencia:

    * una cuenta **rechazada** (en radicación o por el supervisor) no bloquea: el
      contratista puede volver a presentar la cuenta de ese periodo;
    * una cuenta **aprobada** sí bloquea: ese periodo ya se tramitó y no se
      presenta dos veces;
    * una cuenta **en trámite** también bloquea, porque tener dos abiertas del
      mismo periodo dejaría el flujo sin un responsable claro.

    La regla no se puede imponer con una restricción de base de datos porque
    ``models.py`` es definitivo, así que se valida aquí, dentro de la transacción
    y tras bloquear las filas del periodo, para que dos peticiones simultáneas no
    creen dos.
    """
    del_periodo = list(
        CuentaEntrega.objects.select_for_update().filter(
            usuario=usuario, vigencia=vigencia, mes=mes
        )
    )
    bloqueantes = [c for c in del_periodo if not cuenta_rechazada(c)]
    if bloqueantes:
        periodo = (
            f"{vigencia.vigencia} - {dict(CuentaEntrega.Meses.choices).get(mes, mes)}"
        )
        if any(cuenta_aprobada(c) for c in bloqueantes):
            raise ValidationError(
                f"Ya tienes una cuenta de cobro aprobada para {periodo}: ese "
                f"periodo ya se tramitó y no admite otra cuenta."
            )
        raise ValidationError(
            f"Ya tienes una cuenta de cobro en trámite para {periodo}. Espera a "
            f"que termine su revisión antes de presentar otra."
        )
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


def adjuntar_documento(entrega, tipo_documento, archivo=None, clave=None):
    """Adjunta un documento de un tipo a la entrega (estado inicial: Pendiente).

    Solo sobre la última versión y mientras no se haya entregado: el paquete
    enviado a revisión no se toca. `puede_cargar_documentos` ya lo esconde en la
    interfaz, pero la guarda tiene que estar aquí, que es donde se modifica la
    entrega.

    Se recibe ``archivo`` (subida clásica, que viaja por el servidor) o ``clave``
    (el archivo ya está en el bucket porque el navegador lo subió directo). En el
    segundo caso solo se guarda la ruta: no se vuelve a transferir nada.
    """
    cuenta = entrega.cuenta_entrega
    ultima = ultima_entrega(cuenta)
    if ultima is not None and entrega.pk != ultima.pk:
        raise ValidationError(
            "Solo se pueden cargar documentos en la última versión de la entrega."
        )
    if entrega_enviada(cuenta):
        raise ValidationError(
            "Ya entregaste esta versión: el paquete no se puede modificar. Si te "
            "devuelven la cuenta, podrás cargar los documentos de nuevo."
        )
    documento = DocumentosCuentaCobro(
        documento_entrega=entrega, tipo_documento=tipo_documento
    )
    _asignar_archivo(documento.documento, archivo, clave)
    try:
        documento.save()
    except IntegrityError:
        raise ValidationError(
            "Ya cargaste un documento de ese tipo en esta versión."
        )
    return documento


@transaction.atomic
def eliminar_documento(documento, usuario):
    """Quita un documento de la entrega, y su archivo del bucket.

    Solo mientras el paquete siga abierto: una vez entregado está en manos de
    quien revisa, y quitarle un documento por debajo cambiaría lo que esa persona
    ya vio. Queda registrado en la bitácora, porque en un expediente oficial
    importa saber que algo se retiró.
    """
    cuenta = documento.documento_entrega.cuenta_entrega
    if entrega_enviada(cuenta):
        raise ValidationError(
            "Ya entregaste esta versión: no se pueden quitar documentos. Si te "
            "devuelven la cuenta, podrás rehacer el paquete."
        )
    if cuenta.estado_supervisor is not None:
        raise ValidationError("Esta cuenta ya tiene decisión del supervisor.")
    nombre = documento.tipo_documento.nombre
    archivo = documento.documento
    documento.delete()
    registrar_evento(
        cuenta, usuario, EventoTrazabilidad.Etapa.RADICACION,
        Eventos.DOC_ELIMINADO, nombre,
    )
    # Tras confirmar: si el borrado del archivo falla, lo peor que queda es un
    # objeto huérfano en el bucket, no una fila apuntando a un archivo que ya no
    # está.
    transaction.on_commit(lambda: archivo.delete(save=False))
    return nombre


# `eliminar_documento_cierre` se retiró: ver la nota en selectors.py. Retirar
# un documento de cierre deshabilitaba el trámite final que su propia carga
# había habilitado, y la unicidad por (cuenta, tipo) impedía recargarlo.
# El evento Eventos.CIERRE_ELIMINADO se conserva: hay cuentas con ese registro
# en la bitácora y la bitácora no se reescribe.


def tipos_obligatorios(cuenta):
    """Tipos de documento obligatorios para la vigencia de la cuenta.

    El resultado se guarda en la instancia de la vigencia porque se pide varias
    veces por cuenta (completitud de la entrega y del cierre) y es
    parametrización: ningún servicio toca los requisitos, así que no puede
    quedar desactualizado dentro de una petición. Para compartirlo entre las
    filas de un listado, ver ``precargar_tipos_obligatorios``.
    """
    vigencia = cuenta.vigencia
    cacheados = getattr(vigencia, "_tipos_obligatorios", None)
    if cacheados is None:
        cacheados = [
            r.tipo_documento
            for r in RequisitoDocumental.objects.filter(
                vigencia=vigencia, obligatorio=True
            ).select_related("tipo_documento")
        ]
        vigencia._tipos_obligatorios = cacheados
    return cacheados


def precargar_tipos_obligatorios(cuentas):
    """Resuelve los tipos obligatorios de varias cuentas en una sola consulta.

    Cada fila de un listado trae su propia instancia de la vigencia, así que sin
    esto cada una pagaría su consulta. Agrupa por vigencia y siembra la caché que
    lee ``tipos_obligatorios``.
    """
    cuentas = list(cuentas)
    ids = {c.vigencia_id for c in cuentas}
    if not ids:
        return cuentas
    por_vigencia = {}
    for r in RequisitoDocumental.objects.filter(
        vigencia_id__in=ids, obligatorio=True
    ).select_related("tipo_documento"):
        por_vigencia.setdefault(r.vigencia_id, []).append(r.tipo_documento)
    for cuenta in cuentas:
        cuenta.vigencia._tipos_obligatorios = por_vigencia.get(cuenta.vigencia_id, [])
    return cuentas


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
    # Rechazar sin causal deja al contratista sin saber qué corregir, y la
    # devolución de la cuenta se apoya precisamente en estos rechazos.
    if (
        estado == DocumentosCuentaCobro.EstadoDocumento.RECHAZADO
        and not (comentario or "").strip()
    ):
        raise ValidationError(
            "Al rechazar un documento debes escribir la causal: es lo que el "
            "contratista necesita para corregirlo."
        )
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
    return any(
        e.evento == Eventos.ENVIADO and e.fecha_creacion >= entrega.fecha_creacion
        for e in cuenta.eventos.all()
    )


def radicacion_rechazada(cuenta):
    """True si la radicación se **rechazó de forma definitiva** (la última decisión
    de radicación fue `RE` y la cuenta no quedó radicada). Es un estado terminal:
    no genera nueva versión y no admite más acciones (a diferencia de `AJ`, que
    devuelve para corregir). Se deriva de los registros ``RevisionParaRadicacion``
    porque la radicación no tiene un campo de estado propio en la cuenta."""
    if cuenta.fecha_radicacion is not None:
        return False
    ultima = max(
        cuenta.revisionpararadicacion_set.all(),
        key=lambda r: (r.fecha_creacion, r.pk),
        default=None,
    )
    return (
        ultima is not None
        and ultima.resultado == RevisionParaRadicacion.ResultadoRevision.RECHAZADA
    )


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
    if cuenta.estado_supervisor is not None:
        raise ValidationError("Esta cuenta ya tiene decisión del supervisor.")
    if radicacion_rechazada(cuenta):
        raise ValidationError("Esta cuenta fue rechazada en radicación y no continúa.")
    # Pulsar "Entregar" de nuevo sobre una versión ya enviada no cambia nada del
    # flujo, pero duplica el evento ENVIADO: ensucia la trazabilidad y corre la
    # fecha desde la que se mide cuánto lleva la cuenta en su paso actual.
    if entrega_enviada(cuenta):
        raise ValidationError(
            "Ya entregaste esta versión; espera la revisión. Si te devolvieron "
            "la cuenta, carga de nuevo los documentos antes de entregar."
        )
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
    # Un `RE` en radicación es terminal: la cuenta no continúa y no admite otra
    # decisión. `puede_radicar` ya lo esconde en la interfaz, pero la guarda
    # tiene que estar aquí, que es donde se decide la transición de estado.
    if radicacion_rechazada(cuenta):
        raise ValidationError(
            "Esta cuenta fue rechazada definitivamente en radicación; no admite "
            "una nueva decisión."
        )
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
    else:
        # Devolver (requiere ajustes) o rechazar exige que al menos un documento
        # esté rechazado; si todos están correctos, la única opción es aprobar.
        entrega = ultima_entrega(cuenta)
        hay_rechazados = entrega is not None and entrega.documentoscuentacobro_set.filter(
            estado=DocumentosCuentaCobro.EstadoDocumento.RECHAZADO
        ).exists()
        if not hay_rechazados:
            raise ValidationError(
                "Para devolver o rechazar la cuenta debes marcar como rechazado al "
                "menos un documento. Si todos están correctos, aprueba la radicación."
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
# 3. Revisión secuencial (gating técnico → jurídico → administrativo)
# --------------------------------------------------------------------------- #
def _roles_aprobados(entrega):
    return {
        r.rol
        for r in entrega.revisioncuentacobro_set.all()
        if r.resultado == RevisionCuentaCobro.ResultadoRevision.APROBADA
    }


def rol_habilitado(entrega, rol):
    """True si ``rol`` puede revisar ahora sobre ``entrega`` (gating secuencial).

    Exige además que la versión haya sido **entregada** por el contratista: tras
    una devolución nace una versión nueva vacía, y nadie puede revisarla hasta que
    el contratista recargue el paquete y vuelva a pulsar "Entregar". Sin esto, el
    reinicio se saltaría y el revisor podría aprobar la versión vacía.
    """
    if entrega is None:
        return False
    if not entrega_enviada(entrega.cuenta_entrega):
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
    - Requiere ajustes → devuelve al contratista: el sistema genera una nueva
      versión vacía (reinicio total desde el primer rol: el técnico).

    El revisor solo puede **aprobar** o **devolver** (requiere ajustes). El
    "rechazo" (RE) no aplica en esta etapa: una devolución ya reinicia el ciclo; el
    rechazo definitivo solo existe en radicación y en la decisión del supervisor.
    """
    cuenta = _lock(asignacion.cuenta_entrega)
    Resultado = RevisionCuentaCobro.ResultadoRevision

    if resultado not in (Resultado.APROBADA, Resultado.AJUSTES):
        raise ValidationError(
            "La revisión solo puede aprobarse o devolverse (requiere ajustes)."
        )
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

    # Coherencia entre el resultado y el estado de los documentos:
    #  - Aprobar exige que ninguno esté pendiente/rechazado (lo valida el modelo).
    #  - Devolver (ajustes/rechazo) exige al menos un documento rechazado; si todos
    #    están correctos no hay nada que corregir y debe aprobarse.
    if resultado != Resultado.APROBADA:
        hay_rechazados = entrega.documentoscuentacobro_set.filter(
            estado=DocumentosCuentaCobro.EstadoDocumento.RECHAZADO
        ).exists()
        if not hay_rechazados:
            raise ValidationError(
                "Para devolver la cuenta debes rechazar al menos un documento. "
                "Si todos están correctos, aprueba la revisión."
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
    cargados = {d.tipo_documento_id for d in cuenta.documentocierre_set.all()}
    return [t for t in tipos_obligatorios(cuenta) if t.id not in cargados]


@transaction.atomic
def cargar_documento_cierre(cuenta, tipo_documento, archivo=None, usuario=None, clave=None):
    """Carga un documento de cierre FIRMADO (mismo tipo del catálogo que la entrega
    inicial). Lo hace el rol de radicación tras la aprobación del supervisor.

    El ``clean()`` del modelo exige que el supervisor haya aprobado.
    """
    if not es_radicacion(usuario):
        raise ValidationError("Solo el rol de radicación puede cargar el cierre.")
    doc = DocumentoCierre(
        cuenta_entrega=cuenta, tipo_documento=tipo_documento, usuario=usuario
    )
    _asignar_archivo(doc.documento, archivo, clave)
    try:
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
    return next((t for t in cuenta.tramites_finales.all() if t.tipo == tipo), None)


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
def responder_tramite(cuenta, tipo, usuario, realizado, evidencia, comentario, clave=None):
    """Registra la respuesta a un trámite final. Al marcar realizado exige
    evidencia (lo impone el modelo). Cierra el trámite si los dos están listos.

    La evidencia llega como ``evidencia`` (subida clásica) o como ``clave`` (ya
    está en el bucket porque el navegador la subió directo)."""
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
    if clave or evidencia is not None:
        _asignar_archivo(tramite.evidencia, evidencia, clave)
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
# `reemplazar_evidencia` se retiró: ver la nota en selectors.py. El cargue de un
# trámite pide confirmación antes de registrar nada, que es donde se evita el
# archivo equivocado; un soporte que se pueda cambiar después debilita el valor
# probatorio del expediente. El evento Eventos.EVIDENCIA_REEMPLAZADA se conserva:
# puede haber cuentas con ese registro en la bitácora, y la bitácora no se
# reescribe.


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
# 8. Notificaciones derivadas (§9): sin modelo, calculadas en vivo
# --------------------------------------------------------------------------- #
# Tipos de notificación (alimentan el color en la plantilla).
NOTIF_ASIGNACION = "asignacion"
NOTIF_REVISION = "revision"
NOTIF_DEVOLUCION = "devolucion"
NOTIF_APROBACION = "aprobacion"


def nombre_de_cuenta(cuenta):
    """Nombre legible de una cuenta: "2026 · Junio".

    ``CuentaEntrega.__str__`` devuelve el mes como número ("2026: 6") y así
    aparecía en las migas de pan. El modelo es definitivo y no se toca, de modo
    que el formato vive aquí, en un solo sitio, y lo usan las notificaciones, las
    migas y el filtro de plantilla ``cc_nombre``.
    """
    if cuenta is None:
        return ""
    return f"{cuenta.vigencia.vigencia} · {cuenta.get_mes_display()}"


def _notif(tipo, texto, cuenta):
    return {
        "tipo": tipo,
        "texto": texto,
        "cuenta_id": cuenta.pk,
        "url": reverse("cuentas_cobro:cuenta_detalle", args=[cuenta.pk]),
        "cuenta": nombre_de_cuenta(cuenta),
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
    # Esto corre en CADA petición desde los context processors (alimenta la
    # campana), así que la precarga importa más aquí que en cualquier otro sitio.
    base = (
        CuentaEntrega.objects.select_related("vigencia")
        .prefetch_related(*RELACIONES_DEL_FLUJO)
        .filter(fecha_cierre__isnull=True)
    )

    # Contratista (sobre sus propias cuentas)
    if es_contratista(user):
        for c in base.filter(usuario=user):
            if c.estado_supervisor == CuentaEntrega.ResultadoRevision.RECHAZADA:
                continue
            if c.estado_supervisor == AP:
                # El cierre lo carga radicación, no el contratista; pero cuando
                # termina de cargarlo hay que avisarle, porque hasta ahora se
                # quedaba sin noticias justo en la etapa final. Mientras falte
                # algún documento no se dice nada: ese trabajo no es suyo.
                if not documentos_cierre_faltantes(c):
                    items.append(_notif(
                        NOTIF_APROBACION,
                        "Ya se cargaron los documentos de cierre firmados", c))
                continue
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
                if entrega_enviada(c) and not radicacion_rechazada(c):
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
            if entrega_enviada(c) and not radicacion_rechazada(c):
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
# 9. Etapa actual / flujo de la cuenta (§10): derivado del estado
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
        for a in cuenta.asignacionrevisor_set.all()
        if a.estado == AsignacionRevisor.Estado.ACTIVA
    }
    aprobados = _roles_aprobados(entrega) if entrega else set()

    etapas = []

    # 1. Cargue y entrega
    # Manda el estado de la VERSIÓN VIGENTE, no el historial de la cuenta: tras
    # una devolución la cuenta sigue radicada pero la versión nueva está vacía y
    # sin entregar, así que el cargue vuelve a estar en curso.
    if enviada:
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
    elif radicacion_rechazada(cuenta):
        etapas.append(_etapa("radicacion", "Radicación", RECHAZADA, "Rechazada"))
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
    # Exige `enviada` por lo mismo que la etapa 1: tras una devolución no hay nada
    # entregado que revisar, y la etapa vuelve a esperar su turno.
    if cuenta.estado_revisores == AP:
        det4, e4 = "Los tres roles aprobaron", HECHA
    elif radicada and todos_asignados and enviada:
        en_turno = next((r for r in SECUENCIA_ROLES if rol_habilitado(entrega, r)), None)
        if en_turno is not None:
            det4 = f"En revisión {_ROL_NOMBRE[en_turno]}"
        else:
            det4 = "En revisión"
        e4 = ACTUAL
    else:
        det4, e4 = "", FUTURA
    etapas.append(_etapa("revision", "Revisión (técnico → jurídico → administrativo)", e4, det4))

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
        # El turno se deduce de quién no ha aprobado la versión vigente, sin exigir
        # que esté entregada: así, cuando la revisión es el paso SIGUIENTE (tras una
        # devolución, mientras el contratista corrige), se nombra al revisor que la
        # recibirá (el técnico, porque el ciclo reinicia desde él) en vez del
        # genérico "Revisores".
        entrega = ultima_entrega(cuenta)
        aprobados = _roles_aprobados(entrega) if entrega else set()
        en_turno = next((r for r in SECUENCIA_ROLES if r not in aprobados), None)
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
        }.get(nxt, "-")
    if clave == "cierre":
        return "Sistema (automático)"
    return "-"


def _inicio_paso(cuenta):
    """Fecha en que la cuenta entró al paso actual (último evento registrado)."""
    ultimo = max(
        cuenta.eventos.all(), key=lambda e: (e.fecha_creacion, e.pk), default=None
    )
    return ultimo.fecha_creacion if ultimo else cuenta.fecha_creacion


def paso_actual(cuenta):
    """Paso actual de la cuenta, responsable, desde cuándo está en él y el siguiente.

    Devuelve dict: cerrada, rechazada, titulo, detalle, responsable, desde, siguiente.
    """
    RE = CuentaEntrega.ResultadoRevision.RECHAZADA
    if cuenta.fecha_cierre is not None:
        return {
            "cerrada": True, "rechazada": False, "titulo": "Cerrada",
            "detalle": "", "responsable": "-", "desde": cuenta.fecha_cierre,
            "siguiente": "-", "siguiente_responsable": "-",
        }
    if cuenta.estado_supervisor == RE:
        return {
            "cerrada": False, "rechazada": True, "titulo": "Rechazada por el supervisor",
            "detalle": "", "responsable": "-", "desde": _inicio_paso(cuenta),
            "siguiente": "-", "siguiente_responsable": "-",
        }
    if radicacion_rechazada(cuenta):
        return {
            "cerrada": False, "rechazada": True, "titulo": "Rechazada en radicación",
            "detalle": "", "responsable": "-", "desde": _inicio_paso(cuenta),
            "siguiente": "-", "siguiente_responsable": "-",
        }
    flujo = flujo_de_cuenta(cuenta)
    idx = next((i for i, e in enumerate(flujo) if e["estado"] == ACTUAL), None)
    if idx is None:
        return {
            "cerrada": False, "rechazada": False, "titulo": "En proceso",
            "detalle": "", "responsable": "-", "desde": _inicio_paso(cuenta),
            "siguiente": "-", "siguiente_responsable": "-",
        }
    actual = flujo[idx]
    # El siguiente paso es la próxima etapa QUE FALTE, no la siguiente de la lista:
    # tras una devolución de revisor la cuenta ya está radicada y con revisores
    # asignados, así que al reentregar va directa a la revisión; anunciar
    # "Radicación" porque es la etapa de al lado sería mandar a la gente a un paso
    # que ya ocurrió.
    sig = next((e for e in flujo[idx + 1:] if e["estado"] != HECHA), None)
    if sig is not None:
        siguiente = sig["titulo"]
        siguiente_responsable = _responsable_paso(cuenta, sig["clave"])
    else:
        siguiente = "Finaliza el trámite"
        siguiente_responsable = "-"
    return {
        "cerrada": False, "rechazada": False,
        "titulo": actual["titulo"], "detalle": actual["detalle"],
        "responsable": _responsable_paso(cuenta, actual["clave"]),
        "desde": _inicio_paso(cuenta),
        "siguiente": siguiente,
        "siguiente_responsable": siguiente_responsable,
    }
