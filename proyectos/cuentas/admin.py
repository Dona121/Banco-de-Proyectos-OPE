"""Admin técnico de los parámetros de `cuentas` (Unfold, como el resto)."""
from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import ExtensionArchivo


@admin.register(ExtensionArchivo)
class ExtensionArchivoAdmin(ModelAdmin):
    list_display = ("extension", "descripcion", "activa", "fecha_actualizacion")
    list_filter = ("activa",)
    search_fields = ("extension", "descripcion")
    ordering = ("extension",)
    readonly_fields = ("fecha_creacion", "fecha_actualizacion")
    list_editable = ("activa",)
