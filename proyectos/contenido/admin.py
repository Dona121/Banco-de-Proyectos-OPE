"""Django Admin: herramienta técnica de administración y parametrización.

NO es la interfaz de negocio (esa es la app web). Aquí los superusuarios y el
personal de soporte parametrizan, auditan y corrigen datos sobre TODOS los
modelos, con el control de permisos estándar de Django (no por rol de negocio).
"""
from django.contrib import admin, messages
from django.shortcuts import render
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, StackedInline, TabularInline

from cuentas.validadores import ArchivoValidadoAdminMixin, ArchivoValidadoInlineMixin
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


class DocumentosInline(ArchivoValidadoInlineMixin, TabularInline):
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
class DocumentosAdmin(ArchivoValidadoAdminMixin, ModelAdmin):
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

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """"Rechazada" tampoco se ofrece aquí.

        Dejó de usarse en la app (hacía lo mismo que "Requiere ajustes"); si
        siguiera disponible en el admin volvería a aparecer en los datos. Las
        revisiones históricas que ya la tengan se siguen viendo: solo se quita
        de las opciones al editar.
        """
        if db_field.name == "resultado":
            kwargs["choices"] = [
                (c.value, c.label)
                for c in Revisiones.ResultadoRevision
                if c != Revisiones.ResultadoRevision.RECHAZADA
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)


# --------------------------------------------------------------------------- #
# Usuarios y Roles (Grupos) con estilos de Unfold
# --------------------------------------------------------------------------- #
admin.site.unregister(User)
admin.site.unregister(Group)


# --------------------------------------------------------------------------- #
# Acciones sobre usuarios: desactivar es lo normal; borrar es la excepción
# --------------------------------------------------------------------------- #
@admin.action(description="Desactivar (no podrán entrar, su rastro se conserva)")
def desactivar_usuarios(modeladmin, request, queryset):
    n = queryset.update(is_active=False)
    messages.success(request, f"{n} usuario(s) desactivado(s).")


@admin.action(description="Reactivar")
def activar_usuarios(modeladmin, request, queryset):
    n = queryset.update(is_active=True)
    messages.success(request, f"{n} usuario(s) reactivado(s).")


@admin.action(
    description="ELIMINAR definitivamente, con todo lo que tocaron",
    permissions=["borrado_total"],
)
def eliminar_usuarios_con_rastro(modeladmin, request, queryset):
    """Borrado real de un usuario y de todo lo que lo referencia.

    Nueve claves ``PROTECT`` apuntan a ``User``, así que el borrado normal
    falla: no es un descuido, es que una cuenta de cobro dice quién la presentó
    y quién la aprobó. Esta acción retira primero esas cuentas **enteras**, y
    por eso enseña antes qué se va a llevar por delante.

    Para un usuario que ya trabajó, lo sano es **desactivarlo**.
    """
    from cuentas.borrado import eliminar_usuario_con_rastro, resumen_de_borrado

    if request.POST.get("confirmado"):
        for usuario in list(queryset):
            eliminar_usuario_con_rastro(usuario)
        messages.success(request, "Usuario(s) eliminado(s) con todo su rastro.")
        return None

    detalle = []
    for usuario in queryset:
        r = resumen_de_borrado(usuario)
        detalle.append(
            f"{usuario.username}: {len(r['cuentas'])} cuenta(s) de cobro, "
            f"{len(r['proyectos'])} proyecto(s) y {r['n_actividades']} actividad(es)."
        )
    return render(request, "admin/confirmar_borrado.html", {
        "titulo": "Eliminar usuarios definitivamente",
        "advertencia": (
            "Se eliminarán los usuarios y TODO lo que los referencia. No se "
            "borra solo su parte: una cuenta de cobro en la que intervinieron "
            "desaparece completa, con su trazabilidad. Si lo que quieres es que "
            "dejen de entrar, usa «Desactivar»."
        ),
        "detalle": detalle,
        "objetos": queryset,
        "accion": "eliminar_usuarios_con_rastro",
    })


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "get_full_name", "email", "mostrar_roles", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    actions = (desactivar_usuarios, activar_usuarios, eliminar_usuarios_con_rastro)

    @display(description=_("Roles"))
    def mostrar_roles(self, obj):
        return ", ".join(obj.groups.values_list("name", flat=True)) or "-"

    def has_borrado_total_permission(self, request):
        """Permiso de la acción destructiva: solo superusuario.

        Un `staff` con permiso de cambio sobre usuarios podría desactivarlos,
        que es reversible; llevarse por delante cuentas de cobro enteras no.
        """
        return request.user.is_superuser


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    search_fields = ("name",)


admin.site.site_header = "Administración · Gobernación de Sucre"
admin.site.site_title = "Administración"
admin.site.index_title = "Parametrización y soporte"
