from django.contrib.auth.models import User
from contenido.models import Fechas
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Q

# Create your models here.


class Vigencia(Fechas):
    vigencia = models.IntegerField(verbose_name="Vigencia")

    class Meta:
        verbose_name = "Vigencia"
        verbose_name_plural = "Vigencias"

    def __str__(self):
        return f"{self.vigencia}"


class TipoDocumentoCargue(Fechas):
    """Tabla de apoyo para definir y estandarizar los tipos de documento de cargue."""
    nombre = models.CharField(max_length=255, verbose_name="Tipo de documento")

    class Meta:
        verbose_name = "Tipo de documento"
        verbose_name_plural = "Tipos de documento"

    def __str__(self):
        return f"{self.nombre}"


class RequisitoDocumental(Fechas):
    """Configura qué tipos de documento se requieren por vigencia y si son
    obligatorios. La validación de completitud compara los tipos obligatorios de
    la vigencia contra los cargados en la última entrega; y también contra los
    documentos de cierre firmados."""
    vigencia = models.ForeignKey(
        Vigencia, on_delete=models.CASCADE, verbose_name="Vigencia",
    )
    tipo_documento = models.ForeignKey(
        TipoDocumentoCargue, on_delete=models.CASCADE, verbose_name="Tipo de documento",
    )
    obligatorio = models.BooleanField(default=True, verbose_name="Obligatorio")

    class Meta:
        verbose_name = "Requisito documental"
        verbose_name_plural = "Requisitos documentales"
        constraints = [
            models.UniqueConstraint(
                fields=["vigencia", "tipo_documento"],
                name="requisito_unico_por_vigencia",
            )
        ]

    def __str__(self):
        marca = "obligatorio" if self.obligatorio else "opcional"
        return f"{self.vigencia.vigencia} - {self.tipo_documento.nombre} ({marca})"


class CuentaEntrega(Fechas):
    """Define la entrega realizada por el contratista.

    La fecha de radicación solo se define cuando el supervisor aprueba la
    radicación. El estado de los revisores se marca aprobado cuando los tres
    roles (jurídico, administrativo, técnico) aprueban. El estado del supervisor
    es el paso final: solo puede emitirse cuando los revisores ya aprobaron, y
    es una decisión manual (aprobar o rechazar).
    """

    class ResultadoRevision(models.TextChoices):
        APROBADA = "AP", "Aprobado"
        RECHAZADA = "RE", "Rechazado"

    class Meses(models.IntegerChoices):
        ENERO = 1, "Enero"
        FEBRERO = 2, "Febrero"
        MARZO = 3, "Marzo"
        ABRIL = 4, "Abril"
        MAYO = 5, "Mayo"
        JUNIO = 6, "Junio"
        JULIO = 7, "Julio"
        AGOSTO = 8, "Agosto"
        SEPTIEMBRE = 9, "Septiembre"
        OCTUBRE = 10, "Octubre"
        NOVIEMBRE = 11, "Noviembre"
        DICIEMBRE = 12, "Diciembre"

    usuario = models.ForeignKey(
        User, on_delete=models.PROTECT,
        verbose_name="Usuario que realizó la entrega de documentos",
    )
    vigencia = models.ForeignKey(
        Vigencia, verbose_name="Vigencia de entrega", on_delete=models.CASCADE,
    )
    mes = models.IntegerField(choices=Meses.choices, verbose_name="Mes de entrega")
    estado_revisores = models.CharField(
        max_length=2, choices=ResultadoRevision.choices, null=True, blank=True,
    )
    estado_supervisor = models.CharField(
        max_length=2, choices=ResultadoRevision.choices, null=True, blank=True,
    )
    fecha_radicacion = models.DateTimeField(
        verbose_name="Fecha de radicación de la entrega", null=True, blank=True,
    )
    fecha_aprobacion_revisores = models.DateTimeField(
        verbose_name="Fecha de aprobación revisores", null=True, blank=True,
    )
    fecha_aprobacion_supervisor = models.DateTimeField(
        verbose_name="Fecha de aprobación supervisor", null=True, blank=True,
    )
    fecha_cierre = models.DateTimeField(
        verbose_name="Fecha de cierre del trámite", null=True, blank=True,
    )
    comentario = models.TextField(verbose_name="Comentario")

    class Meta:
        verbose_name = "Cuenta entrega"
        verbose_name_plural = "Cuentas entrega"

    def __str__(self):
        return f"{self.vigencia.vigencia}: {self.mes}"

    def clean(self):
        super().clean()
        # Invariante: el supervisor no puede emitir decisión hasta que los
        # revisores hayan aprobado.
        if (
            self.estado_supervisor is not None
            and self.estado_revisores != self.ResultadoRevision.APROBADA
        ):
            raise ValidationError(
                "El supervisor no puede emitir decisión hasta que los "
                "revisores hayan aprobado."
            )

    def actualizar_fecha_radicacion(self):
        if self.fecha_radicacion is not None:
            return
        ultima = self.revisionpararadicacion_set.order_by("fecha_creacion").last()
        if ultima and ultima.resultado == RevisionParaRadicacion.ResultadoRevision.APROBADA:
            self.fecha_radicacion = timezone.now()
            self.save(update_fields=["fecha_radicacion"])

    def actualizar_estado(self):
        if self.fecha_aprobacion_revisores is not None:
            return
        ultima_entrega = self.documentoentrega_set.order_by("-numero_version").first()
        if ultima_entrega is None:
            return
        roles_requeridos = {r for r, _ in RevisionCuentaCobro.Rol.choices}
        aprobados = {
            rev.rol
            for rev in ultima_entrega.revisioncuentacobro_set.all()
            if rev.resultado == RevisionCuentaCobro.ResultadoRevision.APROBADA
        }
        if roles_requeridos.issubset(aprobados):
            self.estado_revisores = self.ResultadoRevision.APROBADA
            self.fecha_aprobacion_revisores = timezone.now()
            self.save(update_fields=["estado_revisores", "fecha_aprobacion_revisores"])

    def revisar_supervisor(self, resultado, comentario=""):
        if self.estado_revisores != self.ResultadoRevision.APROBADA:
            raise ValidationError(
                "No se puede habilitar la decisión del supervisor: los "
                "revisores aún no han aprobado."
            )
        if resultado not in self.ResultadoRevision.values:
            raise ValidationError("Resultado inválido para la decisión del supervisor.")
        self.estado_supervisor = resultado
        if resultado == self.ResultadoRevision.APROBADA:
            self.fecha_aprobacion_supervisor = timezone.now()
        if comentario:
            self.comentario = comentario
        self.full_clean()
        self.save(update_fields=[
            "estado_supervisor", "fecha_aprobacion_supervisor", "comentario",
        ])

    def cerrar(self):
        """Cierra el trámite. Solo procede si el supervisor aprobó. La presencia
        de los documentos de cierre se valida en la capa de servicios antes de
        llamar a este método."""
        if self.estado_supervisor != self.ResultadoRevision.APROBADA:
            raise ValidationError(
                "No se puede cerrar un trámite que el supervisor no ha aprobado."
            )
        if self.fecha_cierre is not None:
            return
        self.fecha_cierre = timezone.now()
        self.save(update_fields=["fecha_cierre"])


class DocumentoEntrega(Fechas):
    cuenta_entrega = models.ForeignKey(
        CuentaEntrega, on_delete=models.CASCADE,
        verbose_name="Cuenta de entrega",
    )
    numero_version = models.IntegerField(verbose_name="Número de la versión")
    usuario = models.ForeignKey(
        User, verbose_name="Usuario que entregó", on_delete=models.PROTECT,
    )
    comentario = models.TextField(verbose_name="Comentario")

    class Meta:
        verbose_name = "Documento entrega"
        verbose_name_plural = "Documentos entrega"
        constraints = [
            models.UniqueConstraint(
                fields=["cuenta_entrega", "numero_version"],
                name="cuenta_entrega_version_unique",
            )
        ]

    def __str__(self):
        return f"{self.cuenta_entrega.usuario.first_name}, versión: {self.numero_version}"

    def clean(self):
        super().clean()
        if self.cuenta_entrega.estado_supervisor == CuentaEntrega.ResultadoRevision.APROBADA:
            raise ValidationError("No se puede crear una entrega para una cuenta aprobada")

    def save(self, *args, **kwargs):
        if not self.pk:
            ultima = (
                DocumentoEntrega.objects
                .filter(cuenta_entrega=self.cuenta_entrega)
                .order_by("-numero_version")
                .first()
            )
            self.numero_version = ultima.numero_version + 1 if ultima else 1
        self.full_clean()
        super().save(*args, **kwargs)


class DocumentosCuentaCobro(Fechas):
    """Documentos cargados por tipo dentro de una entrega.

    Está versionada a nivel de DocumentoEntrega: si el supervisor determina que
    uno o más documentos no cumplen, el contratista vuelve a cargar y se genera
    una nueva versión de la entrega.
    """

    class EstadoDocumento(models.TextChoices):
        PENDIENTE = "PE", "Pendiente"
        APROBADO = "AP", "Aprobado"
        RECHAZADO = "RE", "Rechazado"
        NO_APLICA = "NA", "No aplica"

    documento_entrega = models.ForeignKey(
        DocumentoEntrega, verbose_name="Documento entrega", on_delete=models.CASCADE,
    )
    tipo_documento = models.ForeignKey(
        TipoDocumentoCargue, verbose_name="Tipo de documento", on_delete=models.CASCADE,
    )
    documento = models.FileField(
        verbose_name="Documento", upload_to="cuentas_cobro/%Y/%m/",
    )
    estado = models.CharField(
        max_length=2, choices=EstadoDocumento.choices,
        default=EstadoDocumento.PENDIENTE, verbose_name="Estado del documento",
    )
    comentario = models.TextField(
        blank=True, verbose_name="Comentario de revisión (causal de devolución)",
    )

    class Meta:
        verbose_name = "Documento cuenta de cobro entrega"
        verbose_name_plural = "Documentos cuenta de cobro entrega"
        constraints = [
            # No cargar dos veces el mismo tipo en una misma entrega.
            models.UniqueConstraint(
                fields=["documento_entrega", "tipo_documento"],
                name="documento_tipo_unico_por_entrega",
            )
        ]

    def __str__(self):
        ce = self.documento_entrega.cuenta_entrega
        return f"{ce.usuario}: {ce.vigencia.vigencia} - {ce.mes}"


class RevisionParaRadicacion(Fechas):
    """Revisión del supervisor previa a la radicación.

    1. Si todos los documentos requeridos en su última versión son aprobados, la
       cuenta queda radicada y se procede a la asignación de revisores.
    2. Si requiere ajustes, el contratista vuelve a cargar y la entrega se
       versiona.
    3. Un documento que no aplica se rechaza sin bloquear el cumplimiento de la
       entrega.
    """

    class ResultadoRevision(models.TextChoices):
        APROBADA = "AP", "Aprobado"
        AJUSTES = "AJ", "Requiere ajustes"
        RECHAZADA = "RE", "Rechazado"

    cuenta_entrega = models.ForeignKey(CuentaEntrega, on_delete=models.CASCADE)
    supervisor = models.ForeignKey(
        User, verbose_name="Supervisor", on_delete=models.PROTECT,
    )
    comentario = models.TextField(verbose_name="Comentario")
    resultado = models.CharField(max_length=2, choices=ResultadoRevision.choices)

    class Meta:
        verbose_name = "Revisión documento"
        verbose_name_plural = "Revisión documentos"

    def __str__(self):
        return f"{self.supervisor.first_name}: {self.resultado}"


class AsignacionRevisor(Fechas):
    """Asignación de un revisor a una cuenta para un rol específico.

    Una fila por rol activo. Si un revisor no puede revisar, marca la asignación
    como declinada (liberando el slot) y el supervisor crea una nueva asignación
    activa con otro usuario para el mismo rol.
    """

    class Rol(models.TextChoices):
        JURIDICO = "JU", "Jurídico"
        ADMINISTRATIVO = "AD", "Administrativo"
        TECNICO = "TE", "Técnico"

    class Estado(models.TextChoices):
        ACTIVA = "AC", "Activa"
        DECLINADA = "DE", "Declinada"  # el revisor marcó que no puede revisar

    cuenta_entrega = models.ForeignKey(CuentaEntrega, on_delete=models.CASCADE)
    supervisor = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name="asignaciones_hechas", verbose_name="Supervisor que asigna",
    )
    revisor = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name="asignaciones_recibidas", verbose_name="Revisor asignado",
    )
    rol = models.CharField(max_length=2, choices=Rol.choices)
    estado = models.CharField(
        max_length=2, choices=Estado.choices, default=Estado.ACTIVA,
    )
    motivo_declinacion = models.TextField(
        blank=True, verbose_name="Motivo por el que no puede revisar",
    )

    class Meta:
        verbose_name = "Asignación de revisor"
        verbose_name_plural = "Asignaciones de revisor"
        constraints = [
            # Solo una asignación activa por rol y cuenta. Las declinadas se
            # acumulan como bitácora (requiere PostgreSQL).
            models.UniqueConstraint(
                fields=["cuenta_entrega", "rol"],
                condition=models.Q(estado="AC"),
                name="una_asignacion_activa_por_rol",
            )
        ]

    def __str__(self):
        return f"{self.get_rol_display()}: {self.revisor.username} ({self.get_estado_display()})"

    def declinar(self, motivo):
        """El revisor marca que no puede realizar la revisión."""
        if self.estado != self.Estado.ACTIVA:
            raise ValidationError("Solo una asignación activa puede declinarse.")
        if not motivo:
            raise ValidationError("Debe registrar el motivo por el que no puede revisar.")
        self.estado = self.Estado.DECLINADA
        self.motivo_declinacion = motivo
        self.save(update_fields=["estado", "motivo_declinacion"])

    @staticmethod
    def reasignar_revisor(cuenta_entrega, rol, nuevo_revisor, supervisor):
        """Crea una nueva asignación activa para un rol. Requiere que no exista
        ya una activa (la anterior debe haber declinado)."""
        activa = AsignacionRevisor.objects.filter(
            cuenta_entrega=cuenta_entrega, rol=rol,
            estado=AsignacionRevisor.Estado.ACTIVA,
        ).first()
        if activa is not None:
            raise ValidationError(
                "Ya hay un revisor activo en este rol; debe declinar antes de reasignar."
            )
        return AsignacionRevisor.objects.create(
            cuenta_entrega=cuenta_entrega, rol=rol, revisor=nuevo_revisor,
            supervisor=supervisor, estado=AsignacionRevisor.Estado.ACTIVA,
        )


class RevisionCuentaCobro(Fechas):
    """Revisión de un rol sobre una entrega.

    Se crea una instancia por rol (jurídico, administrativo, técnico), enganchada
    a la asignación activa correspondiente. El orden de revisión jurídico →
    administrativo → técnico se gobierna en la capa de servicios.
    """

    class ResultadoRevision(models.TextChoices):
        APROBADA = "AP", "Aprobado"
        AJUSTES = "AJ", "Requiere ajustes"
        RECHAZADA = "RE", "Rechazado"

    class Rol(models.TextChoices):
        JURIDICO = "JU", "Jurídico"
        ADMINISTRATIVO = "AD", "Administrativo"
        TECNICO = "TE", "Técnico"

    documento_entrega = models.ForeignKey(
        DocumentoEntrega, on_delete=models.CASCADE, verbose_name="Documento entrega",
    )
    asignacion = models.ForeignKey(
        AsignacionRevisor, on_delete=models.PROTECT, verbose_name="Asignación",
    )
    rol = models.CharField(max_length=2, choices=Rol.choices, verbose_name="Rol de revisión")
    comentario = models.TextField(verbose_name="Comentario")
    resultado = models.CharField(max_length=2, choices=ResultadoRevision.choices)

    class Meta:
        verbose_name = "Revisión"
        verbose_name_plural = "Revisiones"
        constraints = [
            models.UniqueConstraint(
                fields=["documento_entrega", "rol"], name="revision_unica_por_rol",
            )
        ]

    def __str__(self):
        return f"Revisión {self.id} ({self.get_rol_display()}): {self.asignacion.revisor.username}"

    def clean(self):
        super().clean()
        # El rol de la revisión debe coincidir con el de la asignación.
        if self.asignacion.rol != self.rol:
            raise ValidationError("El rol de la revisión no coincide con el de la asignación.")
        # No se puede revisar con una asignación declinada.
        if self.asignacion.estado != AsignacionRevisor.Estado.ACTIVA:
            raise ValidationError("No se puede revisar con una asignación declinada.")
        # No se pueden crear revisiones sobre una cuenta ya aprobada.
        if self.documento_entrega.cuenta_entrega.estado_supervisor == CuentaEntrega.ResultadoRevision.APROBADA:
            raise ValidationError("No se pueden crear revisiones sobre una cuenta aprobada")
        # Para APROBAR, todos los documentos deben estar aprobados o no aplica
        # (ninguno rechazado ni pendiente).
        if self.resultado == self.ResultadoRevision.APROBADA:
            hay_pendientes_o_rechazados = self.documento_entrega.documentoscuentacobro_set.filter(
                Q(estado=DocumentosCuentaCobro.EstadoDocumento.RECHAZADO)
                | Q(estado=DocumentosCuentaCobro.EstadoDocumento.PENDIENTE)
            ).exists()
            if hay_pendientes_o_rechazados:
                raise ValidationError(
                    "No se puede aprobar la revisión: todos los documentos deben estar "
                    "aprobados o marcados como no aplica (hay documentos rechazados o pendientes)."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class DocumentoCierre(Fechas):
    """Documentos de cierre: los MISMOS tipos que el contratista cargó
    inicialmente para revisión, pero ahora FIRMADOS. Los carga el rol de
    RADICACIÓN tras la aprobación final del supervisor (la que autoriza la firma).

    Los documentos iniciales pueden no estar firmados; los de cierre sí. Se
    apoyan en el mismo catálogo (TipoDocumentoCargue) y los mismos requisitos
    obligatorios (RequisitoDocumental) que la entrega inicial. Desacoplado de
    DocumentoEntrega: no se versiona ni pasa por los tres revisores. Su guarda en
    clean() es la INVERSA de DocumentoEntrega: aquí se EXIGE que el supervisor
    haya aprobado, mientras que allá se PROHÍBE.
    """

    cuenta_entrega = models.ForeignKey(
        CuentaEntrega, on_delete=models.CASCADE, verbose_name="Cuenta de entrega",
    )
    tipo_documento = models.ForeignKey(
        TipoDocumentoCargue, on_delete=models.CASCADE, verbose_name="Tipo de documento",
    )
    documento = models.FileField(
        upload_to="cierres/%Y/%m/", verbose_name="Documento firmado",
    )
    usuario = models.ForeignKey(
        User, on_delete=models.PROTECT,
        verbose_name="Rol de radicación que carga el cierre firmado",
    )

    class Meta:
        verbose_name = "Documento de cierre"
        verbose_name_plural = "Documentos de cierre"
        constraints = [
            models.UniqueConstraint(
                fields=["cuenta_entrega", "tipo_documento"],
                name="documento_cierre_unico_por_tipo",
            )
        ]

    def __str__(self):
        return f"{self.tipo_documento.nombre} (firmado) - {self.cuenta_entrega}"

    def clean(self):
        super().clean()
        if self.cuenta_entrega.estado_supervisor != CuentaEntrega.ResultadoRevision.APROBADA:
            raise ValidationError(
                "Solo se pueden cargar documentos de cierre en una cuenta "
                "aprobada por el supervisor."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class EventoTrazabilidad(Fechas):
    """Bitácora de eventos del flujo. Se escribe desde la capa de servicios en
    cada transición, para reconstruir tiempos y responsables por etapa sin
    depender de inferir desde fecha_creacion de otros registros."""

    class Etapa(models.TextChoices):
        RADICACION = "RAD", "Radicación"
        ASIGNACION = "ASI", "Asignación de revisores"
        REVISION = "REV", "Revisión"
        DECISION_SUPERVISOR = "SUP", "Decisión del supervisor"
        CIERRE = "CIE", "Cierre"

    cuenta_entrega = models.ForeignKey(
        CuentaEntrega, on_delete=models.CASCADE,
        related_name="eventos", verbose_name="Cuenta de entrega",
    )
    actor = models.ForeignKey(
        User, on_delete=models.PROTECT, verbose_name="Actor",
    )
    etapa = models.CharField(max_length=3, choices=Etapa.choices, verbose_name="Etapa")
    evento = models.CharField(max_length=255, verbose_name="Evento")
    detalle = models.TextField(blank=True, verbose_name="Detalle")

    class Meta:
        verbose_name = "Evento de trazabilidad"
        verbose_name_plural = "Eventos de trazabilidad"
        ordering = ["fecha_creacion"]

    def __str__(self):
        return f"{self.cuenta_entrega} | {self.get_etapa_display()}: {self.evento}"


class TramiteFinal(Fechas):
    """Trámites posteriores al cargue de documentos de cierre firmados.

    Dos pasos secuenciales, cada uno respondido por un rol específico:
      - CARGUE_SIIFWEB: ¿Se cargó a SIIFWEB? → la responde el REVISOR ADMINISTRATIVO.
      - CARGUE_SECOP: ¿Se cargó a SECOP II? → la responde el rol de SECOP.

    (El antiguo paso de "entrega de documentos de cierre" se eliminó: ahora la
    carga de los documentos firmados por el rol de radicación cumple esa función.)

    Restricción de modelo: NO se puede adjuntar evidencia si el trámite no está
    marcado como realizado. Se refuerza en clean() y con un CheckConstraint a
    nivel de base de datos. La validación de qué rol puede responder cada tipo se
    hace en la capa de servicios/permisos.
    """

    class Tipo(models.TextChoices):
        CARGUE_SIIFWEB = "SF", "Cargue en SIIFWEB"
        CARGUE_SECOP = "SC", "Cargue en SECOP II"

    cuenta_entrega = models.ForeignKey(
        CuentaEntrega, on_delete=models.CASCADE,
        related_name="tramites_finales", verbose_name="Cuenta de entrega",
    )
    tipo = models.CharField(
        max_length=2, choices=Tipo.choices, verbose_name="Tipo de trámite",
    )
    usuario = models.ForeignKey(
        User, on_delete=models.PROTECT, verbose_name="Usuario que registra el trámite",
    )
    realizado = models.BooleanField(default=False, verbose_name="¿Realizado?")
    evidencia = models.FileField(
        upload_to="tramites_finales/%Y/%m/", null=True, blank=True,
        verbose_name="Evidencia",
    )
    comentario = models.TextField(blank=True, verbose_name="Comentario")

    class Meta:
        verbose_name = "Trámite final"
        verbose_name_plural = "Trámites finales"
        constraints = [
            models.UniqueConstraint(
                fields=["cuenta_entrega", "tipo"], name="tramite_final_unico_por_tipo",
            ),
            # No se puede adjuntar evidencia si no se marcó "realizado".
            models.CheckConstraint(
                condition=models.Q(realizado=True) | models.Q(evidencia=""),
                name="evidencia_requiere_realizado",
            ),
        ]

    def __str__(self):
        marca = "✓" if self.realizado else "✗"
        return f"{self.get_tipo_display()} [{marca}] - {self.cuenta_entrega}"

    def clean(self):
        super().clean()
        # La evidencia solo puede existir si el trámite está marcado como realizado.
        if self.evidencia and not self.realizado:
            raise ValidationError(
                "No se puede adjuntar evidencia si el trámite no está marcado "
                "como realizado."
            )
        # Al marcar como realizado, la evidencia es obligatoria.
        if self.realizado and not self.evidencia:
            raise ValidationError(
                "Debe adjuntar la evidencia al marcar el trámite como realizado."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)