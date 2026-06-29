"""Django Admin (Unfold) del módulo de cuentas de cobro.

Herramienta técnica de parametrización/soporte. La interfaz de negocio es la
app web del módulo; aquí los superusuarios parametrizan y auditan.
"""
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RelatedDropdownFilter
from unfold.decorators import display

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
    TipoDocumentoCargue,
    TramiteFinal,
    Vigencia,
)

ESTADO_DOC_LABELS = {
    "Pendiente": "warning",
    "Aprobado": "success",
    "Rechazado": "danger",
    "No aplica": "info",
}
RESULTADO_LABELS = {
    "Aprobado": "success",
    "Requiere ajustes": "warning",
    "Rechazado": "danger",
}


# --------------------------------------------------------------------------- #
# Inlines
# --------------------------------------------------------------------------- #
class RequisitoDocumentalInline(TabularInline):
    model = RequisitoDocumental
    extra = 0
    fields = ("tipo_documento", "obligatorio")
    autocomplete_fields = ("tipo_documento",)


class DocumentoEntregaInline(TabularInline):
    model = DocumentoEntrega
    extra = 0
    fields = ("numero_version", "usuario", "comentario", "fecha_creacion")
    readonly_fields = ("numero_version", "fecha_creacion")
    autocomplete_fields = ("usuario",)
    show_change_link = True


class DocumentosCuentaCobroInline(TabularInline):
    model = DocumentosCuentaCobro
    extra = 0
    fields = ("tipo_documento", "documento", "estado", "comentario")
    autocomplete_fields = ("tipo_documento",)


class RevisionCuentaCobroInline(TabularInline):
    model = RevisionCuentaCobro
    extra = 0
    fields = ("rol", "asignacion", "resultado", "comentario")
    autocomplete_fields = ("asignacion",)


class AsignacionRevisorInline(TabularInline):
    model = AsignacionRevisor
    extra = 0
    fields = ("rol", "revisor", "estado", "motivo_declinacion")
    autocomplete_fields = ("revisor", "supervisor")
    show_change_link = True


class RevisionParaRadicacionInline(TabularInline):
    model = RevisionParaRadicacion
    extra = 0
    fields = ("supervisor", "resultado", "comentario", "fecha_creacion")
    readonly_fields = ("fecha_creacion",)
    autocomplete_fields = ("supervisor",)


class DocumentoCierreInline(TabularInline):
    model = DocumentoCierre
    extra = 0
    fields = ("tipo_documento", "documento", "usuario")
    autocomplete_fields = ("tipo_documento", "usuario")


class TramiteFinalInline(TabularInline):
    model = TramiteFinal
    extra = 0
    fields = ("tipo", "realizado", "evidencia", "usuario", "comentario")
    autocomplete_fields = ("usuario",)


class EventoTrazabilidadInline(TabularInline):
    model = EventoTrazabilidad
    extra = 0
    fields = ("etapa", "evento", "actor", "detalle", "fecha_creacion")
    readonly_fields = ("etapa", "evento", "actor", "detalle", "fecha_creacion")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# --------------------------------------------------------------------------- #
# Parametrización
# --------------------------------------------------------------------------- #
@admin.register(Vigencia)
class VigenciaAdmin(ModelAdmin):
    list_display = ("vigencia", "fecha_creacion")
    search_fields = ("vigencia",)
    ordering = ("-vigencia",)
    inlines = (RequisitoDocumentalInline,)


@admin.register(TipoDocumentoCargue)
class TipoDocumentoCargueAdmin(ModelAdmin):
    list_display = ("nombre", "fecha_creacion")
    search_fields = ("nombre",)
    ordering = ("nombre",)


@admin.register(RequisitoDocumental)
class RequisitoDocumentalAdmin(ModelAdmin):
    list_display = ("vigencia", "tipo_documento", "obligatorio")
    list_filter = ("obligatorio", ("vigencia", RelatedDropdownFilter))
    search_fields = ("tipo_documento__nombre",)
    autocomplete_fields = ("vigencia", "tipo_documento")


# --------------------------------------------------------------------------- #
# Cuentas de cobro
# --------------------------------------------------------------------------- #
@admin.register(CuentaEntrega)
class CuentaEntregaAdmin(ModelAdmin):
    list_display = (
        "__str__", "usuario", "vigencia", "mes",
        "estado_revisores_badge", "estado_supervisor_badge", "fecha_radicacion",
    )
    list_filter = (
        ("vigencia", RelatedDropdownFilter),
        ("estado_revisores", ChoicesDropdownFilter),
        ("estado_supervisor", ChoicesDropdownFilter),
    )
    search_fields = ("usuario__username", "usuario__first_name", "vigencia__vigencia")
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("usuario", "vigencia")
    readonly_fields = (
        "fecha_radicacion", "fecha_aprobacion_revisores", "fecha_cierre",
        "fecha_creacion", "fecha_actualizacion",
    )
    inlines = (
        DocumentoEntregaInline, AsignacionRevisorInline,
        RevisionParaRadicacionInline, DocumentoCierreInline, TramiteFinalInline,
        EventoTrazabilidadInline,
    )

    @display(description=_("Revisores"), label=RESULTADO_LABELS)
    def estado_revisores_badge(self, obj):
        return obj.get_estado_revisores_display() or "—"

    @display(description=_("Supervisor"), label=RESULTADO_LABELS)
    def estado_supervisor_badge(self, obj):
        return obj.get_estado_supervisor_display() or "—"


@admin.register(DocumentoEntrega)
class DocumentoEntregaAdmin(ModelAdmin):
    list_display = ("cuenta_entrega", "numero_version", "usuario", "fecha_creacion")
    list_filter = (("cuenta_entrega__vigencia", RelatedDropdownFilter),)
    search_fields = ("cuenta_entrega__usuario__username",)
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("cuenta_entrega", "usuario")
    readonly_fields = ("numero_version", "fecha_creacion", "fecha_actualizacion")
    inlines = (DocumentosCuentaCobroInline, RevisionCuentaCobroInline)


@admin.register(AsignacionRevisor)
class AsignacionRevisorAdmin(ModelAdmin):
    list_display = ("cuenta_entrega", "rol", "revisor", "estado_badge")
    list_filter = (("rol", ChoicesDropdownFilter), ("estado", ChoicesDropdownFilter))
    search_fields = ("revisor__username", "cuenta_entrega__usuario__username")
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("cuenta_entrega", "revisor", "supervisor")

    @display(description=_("Estado"), label={"Activa": "success", "Declinada": "danger"})
    def estado_badge(self, obj):
        return obj.get_estado_display()


@admin.register(RevisionCuentaCobro)
class RevisionCuentaCobroAdmin(ModelAdmin):
    list_display = ("documento_entrega", "rol", "resultado_badge", "fecha_creacion")
    list_filter = (("rol", ChoicesDropdownFilter), ("resultado", ChoicesDropdownFilter))
    search_fields = ("documento_entrega__cuenta_entrega__usuario__username",)
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("documento_entrega", "asignacion")

    @display(description=_("Resultado"), label=RESULTADO_LABELS)
    def resultado_badge(self, obj):
        return obj.get_resultado_display()


@admin.register(TramiteFinal)
class TramiteFinalAdmin(ModelAdmin):
    list_display = ("cuenta_entrega", "tipo", "realizado", "usuario", "fecha_creacion")
    list_filter = (("tipo", ChoicesDropdownFilter), "realizado")
    search_fields = ("cuenta_entrega__usuario__username",)
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("cuenta_entrega", "usuario")


@admin.register(EventoTrazabilidad)
class EventoTrazabilidadAdmin(ModelAdmin):
    list_display = ("cuenta_entrega", "etapa", "evento", "actor", "fecha_creacion")
    list_filter = (("etapa", ChoicesDropdownFilter),)
    search_fields = ("evento", "cuenta_entrega__usuario__username")
    ordering = ("fecha_creacion",)
    autocomplete_fields = ("cuenta_entrega", "actor")
    readonly_fields = ("fecha_creacion", "fecha_actualizacion")
