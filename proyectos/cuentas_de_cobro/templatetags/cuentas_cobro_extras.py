"""Filtros/tags de presentación para el módulo de cuentas de cobro."""
from django import template
from django.utils.html import format_html

register = template.Library()

# Mapa código → (etiqueta, clase de badge de app.css). Los códigos los comparten
# las distintas enumeraciones del módulo (AP/AJ/RE para resultados; PE/NA para
# estado de documento).
_MAPA = {
    "AP": ("Aprobado", "badge-aprobada"),
    "AJ": ("Requiere ajustes", "badge-ajustes"),
    "RE": ("Rechazado", "badge-rechazada"),
    "PE": ("Pendiente", "badge-pendiente"),
    "NA": ("No aplica", "badge-revision"),
    "AC": ("Activa", "badge-aprobada"),
    "DE": ("Declinada", "badge-rechazada"),
}


@register.simple_tag
def cc_badge(codigo, vacio="Pendiente"):
    """Badge para un código de estado/resultado del módulo."""
    if not codigo:
        return format_html('<span class="badge badge-pendiente">{}</span>', vacio)
    etiqueta, clase = _MAPA.get(codigo, (codigo, "badge-pendiente"))
    return format_html('<span class="badge {}">{}</span>', clase, etiqueta)
