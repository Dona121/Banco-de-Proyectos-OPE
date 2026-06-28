"""Filtros y tags de presentación para la app web."""
from django import template
from django.utils import timezone
from django.utils.html import format_html

from contenido.models import Actividades, Revisiones

register = template.Library()

Estado = Actividades.EstadoActividad
Resultado = Revisiones.ResultadoRevision

ESTADO_CLASE = {
    Estado.PENDIENTE: "badge-pendiente",
    Estado.EN_REVISION: "badge-revision",
    Estado.AJUSTES: "badge-ajustes",
    Estado.APROBADA: "badge-aprobada",
}
RESULTADO_CLASE = {
    Resultado.APROBADA: "badge-aprobada",
    Resultado.AJUSTES: "badge-ajustes",
    Resultado.RECHAZADA: "badge-rechazada",
}


@register.simple_tag
def estado_badge(estado):
    clase = ESTADO_CLASE.get(estado, "badge-pendiente")
    label = dict(Estado.choices).get(estado, estado)
    return format_html('<span class="badge {}">{}</span>', clase, label)


@register.simple_tag
def resultado_badge(resultado):
    clase = RESULTADO_CLASE.get(resultado, "badge-pendiente")
    label = dict(Resultado.choices).get(resultado, resultado)
    return format_html('<span class="badge {}">{}</span>', clase, label)


@register.filter
def vencida(actividad):
    """True si la actividad está vencida y no aprobada."""
    if actividad.estado == Estado.APROBADA:
        return False
    return actividad.fecha_vencimiento < timezone.now()


@register.filter
def startswith(value, prefix):
    return bool(value) and str(value).startswith(prefix)


@register.filter
def dias_restantes(fecha):
    """Días que faltan para ``fecha`` (negativo si ya pasó)."""
    if not fecha:
        return None
    return (fecha - timezone.now()).days


@register.filter
def dias_transcurridos(fecha):
    """Días transcurridos desde ``fecha``."""
    if not fecha:
        return None
    return (timezone.now() - fecha).days


@register.filter
def porcentaje(parcial, total):
    try:
        total = float(total)
        if total <= 0:
            return 0
        return round(float(parcial) / total * 100)
    except (TypeError, ValueError):
        return 0
