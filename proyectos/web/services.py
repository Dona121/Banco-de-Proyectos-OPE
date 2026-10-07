"""Servicios de negocio: encapsulan las reglas y transiciones de estado.

Reutilizados por la app web (y disponibles para el admin). Toda la lógica de
"qué pasa con el estado de la actividad" vive aquí, no en las vistas.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from contenido.models import Actividades, ActividadEntrega, Proyectos, Revisiones
from cuentas.roles import COORDINADOR, es_coordinador, es_director

from . import selectors

Estado = Actividades.EstadoActividad
Resultado = Revisiones.ResultadoRevision


@transaction.atomic
def crear_entrega(actividad, usuario, comentario):
    """Abre una nueva entrega (versión) en BORRADOR.

    Crear la versión ya NO manda la actividad a revisión: eso lo hace
    ``realizar_entrega``. Mientras tanto el ejecutor adjunta sus documentos con
    el paquete abierto, y nadie más lo ve como trabajo por revisar.

    El número de versión lo calcula el propio modelo. Lanza ValidationError
    (vía el ``clean()`` del modelo) si la actividad está aprobada.
    """
    # Se bloquea la actividad para decidir: sin esto, dos peticiones a la vez
    # pasan las dos comprobaciones antes de que ninguna guarde y acaban abriendo
    # dos versiones. Es exactamente lo que dejó el flujo anterior en los datos.
    actividad = Actividades.objects.select_for_update().get(pk=actividad.pk)
    if actividad.estado not in (Estado.PENDIENTE, Estado.AJUSTES):
        raise ValidationError(
            "No se puede abrir una entrega nueva: la actividad está "
            f"«{actividad.get_estado_display()}»."
        )
    if selectors.borrador_de(actividad) is not None:
        raise ValidationError(
            "Ya tienes una entrega abierta para esta actividad. Complétala y "
            "pulsa «Realizar entrega» antes de empezar otra."
        )
    entrega = ActividadEntrega(
        actividad=actividad, usuario=usuario, comentario=comentario
    )
    entrega.save()  # save() del modelo calcula numero_version y valida
    return entrega


@transaction.atomic
def realizar_entrega(entrega, usuario):
    """Envía la entrega a revisión: la congela y pone la actividad En revisión.

    Es el momento de cierre que faltaba. A partir de aquí el ejecutor no puede
    adjuntar, quitar ni sustituir documentos de esta versión, y tampoco abrir
    otra: el paquete que ve el revisor es el que se entregó. Se vuelve a abrir
    solo si el revisor pide ajustes, y entonces como una versión nueva.

    Guardar la entrega actualiza su ``fecha_actualizacion`` (de ``Fechas``, con
    ``auto_now``), que queda como la fecha de envío: es el único registro de ese
    momento posible sin tocar el modelo, que es definitivo.
    """
    # Mismo bloqueo que al abrirla: un doble envío del formulario no debe
    # entregar dos veces ni dejar la actividad en un estado a medias.
    Actividades.objects.select_for_update().get(pk=entrega.actividad_id)
    entrega.refresh_from_db()
    if not selectors.puede_documentar(usuario, entrega):
        raise ValidationError("Esta entrega ya no se puede modificar.")
    if not entrega.documentos_set.exists():
        raise ValidationError(
            "Adjunta al menos un documento antes de realizar la entrega."
        )
    entrega.save()  # marca la fecha de envío en fecha_actualizacion
    actividad = entrega.actividad
    actividad.estado = Estado.EN_REVISION
    actividad.save(update_fields=["estado", "fecha_actualizacion"])
    return entrega


@transaction.atomic
def registrar_revision(entrega, revisor, resultado, comentario):
    """Crea la revisión de una entrega y actualiza el estado de la actividad.

    Dos salidas, no tres:

    - Aprobada          → actividad APROBADA (finalizada)
    - Requiere ajustes  → actividad REQUIERE AJUSTES, y el ejecutor puede abrir
                          una versión nueva

    "Rechazada" (``RE``) existe en el modelo, que es definitivo, pero no se
    ofrece ni se acepta: hacía exactamente lo mismo que "Requiere ajustes" y
    tener dos nombres para una sola cosa solo confundía a quien revisa.
    """
    if resultado == Resultado.RECHAZADA:
        raise ValidationError(
            "«Rechazada» ya no se usa. Si hay que corregir algo, elige "
            "«Requiere ajustes»."
        )
    # Las mismas guardas que la vista, pero aquí, que es donde mandan. Sin
    # ellas, cualquier otra vía (el admin, un comando, una tarea futura) podía
    # revisar un borrador que su autor aún no había entregado, revisar dos veces
    # la misma entrega, o revisar una versión ya reemplazada.
    if hasattr(entrega, "revisiones"):
        raise ValidationError("Esta entrega ya tiene una revisión registrada.")
    if not selectors.entrega_enviada(entrega):
        raise ValidationError(
            "Esta entrega todavía no se ha realizado: no hay nada que revisar."
        )
    if entrega.usuario_id == revisor.id:
        raise ValidationError("No puedes revisar tu propia entrega.")
    if not selectors.puede_revisar(revisor, entrega):
        raise ValidationError("No te corresponde revisar esta entrega.")
    revision = Revisiones(
        actividad_entrega=entrega,
        revisor=revisor,
        resultado=resultado,
        comentario=comentario,
    )
    try:
        revision.save()  # save() del modelo valida (no revisar si está aprobada)
    except IntegrityError:
        # `Revisiones.actividad_entrega` es OneToOne: dos revisores pulsando a la
        # vez pasan la comprobación de arriba y uno choca aquí. Sin esto sería un
        # 500; así es el mismo mensaje que si hubiera llegado un segundo después.
        raise ValidationError("Esta entrega ya tiene una revisión registrada.")

    actividad = entrega.actividad
    if resultado == Resultado.APROBADA:
        actividad.estado = Estado.APROBADA
    else:
        actividad.estado = Estado.AJUSTES
    actividad.save(update_fields=["estado", "fecha_actualizacion"])
    return revision


# --------------------------------------------------------------------------- #
# Notificaciones derivadas (sin modelo): pendientes accionables por usuario.
# Color por tipo: asignación=azul, revisión=verde, devolución=rojo, plazo=ámbar.
# --------------------------------------------------------------------------- #
NOTIF_ASIGNACION = "asignacion"
NOTIF_REVISION = "revision"
NOTIF_DEVOLUCION = "devolucion"
NOTIF_PLAZO = "plazo"

# Días de anticipación con que se avisa que un plazo está por cumplirse.
DIAS_AVISO_PLAZO = 3


def _notif(tipo, texto, url, contexto):
    return {"tipo": tipo, "texto": texto, "url": url, "contexto": contexto}


def notificaciones_para(user):
    """Pendientes accionables del usuario en proyectos/actividades, derivados del
    estado actual (no se persiste ningún modelo de notificaciones).

    Cubre: proyecto asignado, actividad asignada, actividad enviada (por revisar),
    actividad revisada con ajustes, y plazos por cumplirse o vencidos.
    """
    if not user.is_authenticated:
        return []
    items = []
    ahora = timezone.now()
    limite = ahora + timedelta(days=DIAS_AVISO_PLAZO)

    # Coordinador: proyectos por poblar y actividades por revisar cuyo ejecutor es
    # un formulador (las ejecutadas por un coordinador las revisa el director).
    if es_coordinador(user):
        for p in Proyectos.objects.filter(asignado_a=user):
            if not p.actividades_set.exists():
                items.append(_notif(
                    NOTIF_ASIGNACION,
                    f"Proyecto asignado: agrega sus actividades, {p.nombre}",
                    reverse("web:proyecto_detalle", args=[p.pk]),
                    p.nombre,
                ))
        for a in Actividades.objects.filter(
            proyecto__asignado_a=user, estado=Estado.EN_REVISION
        ).exclude(asignado_a__groups__name=COORDINADOR).select_related("proyecto"):
            items.append(_notif(
                NOTIF_REVISION,
                f"Actividad por revisar: {a.nombre}",
                reverse("web:actividad_detalle", args=[a.pk]),
                a.proyecto.nombre,
            ))

    # Director: actividades por revisar cuyo ejecutor es un coordinador (delegadas
    # por él) en los proyectos que creó.
    if es_director(user):
        for a in Actividades.objects.filter(
            proyecto__creador_por=user, estado=Estado.EN_REVISION,
            asignado_a__groups__name=COORDINADOR,
        ).select_related("proyecto").distinct():
            items.append(_notif(
                NOTIF_REVISION,
                f"Actividad por revisar: {a.nombre}",
                reverse("web:actividad_detalle", args=[a.pk]),
                a.proyecto.nombre,
            ))

    # Ejecutor (formulador, o coordinador con actividad asignada): lo asignado y lo
    # devuelto para corrección.
    for a in Actividades.objects.filter(
        asignado_a=user, estado=Estado.PENDIENTE
    ).select_related("proyecto"):
        items.append(_notif(
            NOTIF_ASIGNACION,
            f"Actividad asignada, realízala y entrégala: {a.nombre}",
            reverse("web:actividad_detalle", args=[a.pk]),
            a.proyecto.nombre,
        ))
    for a in Actividades.objects.filter(
        asignado_a=user, estado=Estado.AJUSTES
    ).select_related("proyecto"):
        items.append(_notif(
            NOTIF_DEVOLUCION,
            f"Te pidieron ajustes, corrige y vuelve a entregar: {a.nombre}",
            reverse("web:actividad_detalle", args=[a.pk]),
            a.proyecto.nombre,
        ))

    # Plazos por cumplirse o vencidos (para el responsable de la actividad).
    # En revisión no cuenta: el trabajo ya se entregó y el plazo dejó de
    # correr. Si se lo devuelven (Requiere ajustes) vuelve a contar, porque
    # vuelve a estar pendiente.
    for a in Actividades.objects.filter(
        asignado_a=user, fecha_vencimiento__lte=limite
    ).exclude(
        estado__in=(Estado.EN_REVISION, Estado.APROBADA)
    ).select_related("proyecto"):
        vencida = a.fecha_vencimiento < ahora
        texto = (
            f"Plazo vencido: {a.nombre}" if vencida
            else f"El plazo está por cumplirse: {a.nombre}"
        )
        items.append(_notif(
            NOTIF_PLAZO, texto,
            reverse("web:actividad_detalle", args=[a.pk]), a.proyecto.nombre,
        ))

    return items
