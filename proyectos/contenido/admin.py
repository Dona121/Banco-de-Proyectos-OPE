"""Django Admin — herramienta técnica de administración y parametrización.

NO es la interfaz de negocio (esa es la app web). Aquí los superusuarios y el
personal de soporte parametrizan, auditan y corrigen datos sobre TODOS los
modelos, con el control de permisos estándar de Django (no por rol de negocio).
"""
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RelatedDropdownFilter
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import (
    Actividades,
    ActividadEntrega,
    Documentos,
    Proyectos,
    Revisiones,
    Subactividades,
)

ESTADO_LABELS = {
    "Pendiente": "warning",
    "En revisión": "info",
    "Requiere ajustes": "danger",
    "Aprobada": "success",
}
RESULTADO_LABELS = {
    "Aprobada": "success",
    "Requiere ajustes": "warning",
    "Rechazada": "danger",
}


# --------------------------------------------------------------------------- #
# Inlines
# --------------------------------------------------------------------------- #
class SubactividadesInline(TabularInline):
    model = Subactividades
    extra = 0
    fields = ("nombre",)


class ActividadesInline(TabularInline):
    model = Actividades
    extra = 0
    fields = ("nombre", "estado", "asignado_a", "fecha_vencimiento")
    autocomplete_fields = ("asignado_a",)
    show_change_link = True


class ActividadEntregaInline(TabularInline):
    model = ActividadEntrega
    extra = 0
    fields = ("numero_version", "usuario", "comentario", "fecha_creacion")
    readonly_fields = ("numero_version", "fecha_creacion")
    autocomplete_fields = ("usuario",)
    show_change_link = True


class DocumentosInline(TabularInline):
    model = Documentos
    extra = 0
    fields = ("nombre", "archivo")


class RevisionInline(StackedInline):
    model = Revisiones
    extra = 0
    max_num = 1
    fields = ("revisor", "resultado", "comentario")
    autocomplete_fields = ("revisor",)


# --------------------------------------------------------------------------- #
# Proyectos
# --------------------------------------------------------------------------- #
@admin.register(Proyectos)
class ProyectosAdmin(ModelAdmin):
    list_display = ("nombre", "creador_por", "asignado_a", "fecha_creacion")
    search_fields = ("nombre", "creador_por__username", "asignado_a__username")
    list_filter = ("fecha_creacion",)
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("creador_por", "asignado_a")
    readonly_fields = ("fecha_creacion", "fecha_actualizacion")
    inlines = (ActividadesInline,)
    fieldsets = (
        (_("Proyecto"), {"fields": ("nombre", "creador_por", "asignado_a")}),
        (_("Auditoría"), {"fields": ("fecha_creacion", "fecha_actualizacion")}),
    )


# --------------------------------------------------------------------------- #
# Actividades
# --------------------------------------------------------------------------- #
@admin.register(Actividades)
class ActividadesAdmin(ModelAdmin):
    list_display = ("nombre", "proyecto", "asignado_a", "estado_badge", "fecha_vencimiento")
    list_filter = (("estado", ChoicesDropdownFilter), ("proyecto", RelatedDropdownFilter),
                   "fecha_vencimiento")
    search_fields = ("nombre", "proyecto__nombre")
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("proyecto", "asignado_por", "asignado_a")
    readonly_fields = ("fecha_creacion", "fecha_actualizacion")
    inlines = (SubactividadesInline, ActividadEntregaInline)
    actions = ("marcar_en_revision", "marcar_aprobada", "marcar_ajustes")
    fieldsets = (
        (_("Actividad"), {"fields": ("proyecto", "nombre", "estado")}),
        (_("Fechas"), {"fields": ("fecha_programada", "fecha_vencimiento")}),
        (_("Asignación"), {"fields": ("asignado_por", "asignado_a")}),
        (_("Auditoría"), {"fields": ("fecha_creacion", "fecha_actualizacion")}),
    )

    @display(description=_("Estado"), label=ESTADO_LABELS)
    def estado_badge(self, obj):
        return obj.get_estado_display()

    def _set_estado(self, request, queryset, nuevo, etiqueta):
        n = queryset.update(estado=nuevo)
        self.message_user(
            request, _("%(n)d actividad(es) → «%(e)s».") % {"n": n, "e": etiqueta},
            messages.SUCCESS,
        )

    @action(description=_("Marcar como En revisión"))
    def marcar_en_revision(self, request, queryset):
        self._set_estado(request, queryset, Actividades.EstadoActividad.EN_REVISION, _("En revisión"))

    @action(description=_("Marcar como Aprobada"))
    def marcar_aprobada(self, request, queryset):
        self._set_estado(request, queryset, Actividades.EstadoActividad.APROBADA, _("Aprobada"))

    @action(description=_("Marcar como Requiere ajustes"))
    def marcar_ajustes(self, request, queryset):
        self._set_estado(request, queryset, Actividades.EstadoActividad.AJUSTES, _("Requiere ajustes"))


# --------------------------------------------------------------------------- #
# Subactividades
# --------------------------------------------------------------------------- #
@admin.register(Subactividades)
class SubactividadesAdmin(ModelAdmin):
    list_display = ("nombre", "actividad", "fecha_creacion")
    search_fields = ("nombre", "actividad__nombre")
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("actividad",)
    readonly_fields = ("fecha_creacion", "fecha_actualizacion")


# --------------------------------------------------------------------------- #
# Entregas
# --------------------------------------------------------------------------- #
@admin.register(ActividadEntrega)
class ActividadEntregaAdmin(ModelAdmin):
    list_display = ("actividad", "numero_version", "usuario", "estado_actividad", "fecha_creacion")
    list_filter = (("actividad__proyecto", RelatedDropdownFilter),
                   ("actividad__estado", ChoicesDropdownFilter))
    search_fields = ("actividad__nombre", "actividad__proyecto__nombre")
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("actividad", "usuario")
    readonly_fields = ("numero_version", "fecha_creacion", "fecha_actualizacion")
    inlines = (DocumentosInline, RevisionInline)
    fieldsets = (
        (_("Entrega"), {"fields": ("actividad", "numero_version", "usuario", "comentario")}),
        (_("Auditoría"), {"fields": ("fecha_creacion", "fecha_actualizacion")}),
    )

    @display(description=_("Estado actividad"), label=ESTADO_LABELS)
    def estado_actividad(self, obj):
        return obj.actividad.get_estado_display()


# --------------------------------------------------------------------------- #
# Documentos
# --------------------------------------------------------------------------- #
@admin.register(Documentos)
class DocumentosAdmin(ModelAdmin):
    list_display = ("nombre", "actividad_entrega", "fecha_creacion")
    search_fields = ("nombre", "actividad_entrega__actividad__nombre")
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("actividad_entrega",)
    readonly_fields = ("fecha_creacion", "fecha_actualizacion")


# --------------------------------------------------------------------------- #
# Revisiones
# --------------------------------------------------------------------------- #
@admin.register(Revisiones)
class RevisionesAdmin(ModelAdmin):
    list_display = ("actividad_entrega", "revisor", "resultado_badge", "fecha_creacion")
    list_filter = (("resultado", ChoicesDropdownFilter),)
    search_fields = ("actividad_entrega__actividad__nombre", "revisor__username")
    ordering = ("-fecha_creacion",)
    autocomplete_fields = ("actividad_entrega", "revisor")
    readonly_fields = ("fecha_creacion", "fecha_actualizacion")

    @display(description=_("Resultado"), label=RESULTADO_LABELS)
    def resultado_badge(self, obj):
        return obj.get_resultado_display()


# --------------------------------------------------------------------------- #
# Usuarios y Roles (Grupos) con estilos de Unfold
# --------------------------------------------------------------------------- #
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "get_full_name", "email", "mostrar_roles", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")

    @display(description=_("Roles"))
    def mostrar_roles(self, obj):
        return ", ".join(obj.groups.values_list("name", flat=True)) or "—"


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    search_fields = ("name",)


admin.site.site_header = "Administración · Gobernación de Sucre"
admin.site.site_title = "Administración"
admin.site.index_title = "Parametrización y soporte"
