"""Filtros/tags de presentación para el módulo de cuentas de cobro."""
from django import template
from django.utils.html import format_html, format_html_join

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

# Significado de cada estado SEGÚN EL PROCESO del flujo. El mismo código puede
# significar cosas distintas (p. ej. "RE" es un documento que no cumple, pero
# como decisión de radicación es un rechazo definitivo). Alimenta los tooltips
# (title) de los badges y la leyenda que se muestra bajo cada formulario.
_SIGNIFICADOS = {
    # Estado de cada documento cargado (lo marca radicación o el revisor de turno).
    "documento": {
        "PE": "Aún no revisado.",
        "AP": "El documento cumple.",
        "RE": "No cumple (archivo equivocado, ilegible, incompleto o mal diligenciado): debe corregirse.",
        "NA": "No corresponde a esta cuenta; no bloquea la completitud.",
    },
    # Decisión de radicación (supervisor o rol de radicación).
    "radicacion": {
        "AP": "Documentos correctos: la cuenta queda radicada y pasa a los revisores.",
        "AJ": "Requiere ajustes: la cuenta vuelve al contratista para corregir y entregar de nuevo.",
        "RE": "Rechazo definitivo: la cuenta queda rechazada y no continúa (estado terminal).",
    },
    # Resultado de un revisor (técnico / jurídico / administrativo). El texto
    # dice QUÉ LE PASA A LA CUENTA al elegir cada opción.
    "revision": {
        "AP": "Aprobado: la cuenta avanza al siguiente revisor; si es el último, pasa a la decisión del supervisor.",
        "AJ": "Requiere ajustes: la cuenta vuelve al contratista, se genera una versión nueva y la revisión reinicia desde el técnico.",
    },
    # Estado global de la etapa de revisión (cuenta.estado_revisores).
    "revisores": {
        "AP": "Los tres revisores aprobaron: pasa a la decisión del supervisor.",
    },
    # Decisión final del supervisor (para firma).
    "supervisor": {
        "AP": "Aprobada para la firma de los documentos de cierre.",
        "RE": "Rechazada: la cuenta no continúa.",
    },
    # Estado de una asignación de revisor.
    "asignacion": {
        "AC": "Asignación activa: el revisor está a cargo.",
        "DE": "El revisor declinó; debe reasignarse.",
    },
}


@register.simple_tag
def cc_badge(codigo, vacio="Pendiente", proceso=""):
    """Badge para un código de estado/resultado del módulo.

    Si se indica ``proceso`` y hay un significado definido para ese código, se
    agrega como tooltip (atributo ``title``) para explicar qué significa el estado
    en ese punto del flujo.
    """
    if not codigo:
        return format_html('<span class="badge badge-pendiente">{}</span>', vacio)
    etiqueta, clase = _MAPA.get(codigo, (codigo, "badge-pendiente"))
    titulo = _SIGNIFICADOS.get(proceso, {}).get(codigo, "")
    if titulo:
        return format_html(
            '<span class="badge {} cursor-help" title="{}">{}</span>',
            clase, titulo, etiqueta,
        )
    return format_html('<span class="badge {}">{}</span>', clase, etiqueta)


@register.simple_tag
def cc_leyenda(proceso):
    """Leyenda con el significado de cada estado posible de un ``proceso``.

    Pensada para ir junto al formulario donde se elige el estado/resultado, para
    que quien decide sepa qué implica cada opción.
    """
    items = _SIGNIFICADOS.get(proceso, {})
    if not items:
        return ""
    filas = format_html_join(
        "",
        '<li class="flex items-start gap-2">'
        '<span class="badge {} shrink-0">{}</span>'
        '<span class="text-slate-500">{}</span></li>',
        (
            (_MAPA.get(code, (code, "badge-pendiente"))[1],
             _MAPA.get(code, (code, "badge-pendiente"))[0],
             significado)
            for code, significado in items.items()
        ),
    )
    return format_html(
        '<ul class="mt-3 space-y-1.5 rounded-lg border border-slate-100 '
        'bg-slate-50 p-3 text-xs">{}</ul>',
        filas,
    )
