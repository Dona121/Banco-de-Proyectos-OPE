"""Borrado de datos de prueba, con su rastro y sus archivos.

En producción se han hecho pruebas (cuentas, usuarios, proyectos) y hay que
poder retirarlas. El sistema lo impedía por dos motivos distintos, y conviene
no confundirlos:

* **El modelo protege de verdad.** Nueve claves apuntan a ``User`` con
  ``PROTECT`` y ``RevisionCuentaCobro.asignacion`` protege a la asignación. No
  es un descuido: una cuenta de cobro dice quién la presentó y quién la aprobó,
  y ese nombre no debería poder evaporarse. Por eso el borrado real vive aquí,
  explícito y acotado, y no se gana por accidente.
* **Los archivos se quedaban en el bucket.** Borrar un proyecto sí funcionaba,
  pero sus documentos seguían ocupando sitio y accesibles con una URL firmada.
  Eso no era una protección, era una fuga.
"""
from django.db import transaction
from django.db.models import Q


# --------------------------------------------------------------------------- #
# Archivos: que no se queden huérfanos en el bucket
# --------------------------------------------------------------------------- #
def archivos_de(instancia):
    """Los ``FieldFile`` con contenido de una instancia."""
    from django.db.models import FileField

    campos = [
        f.name for f in instancia._meta.fields if isinstance(f, FileField)
    ]
    return [
        archivo
        for archivo in (getattr(instancia, nombre, None) for nombre in campos)
        if archivo and archivo.name
    ]


def borrar_archivos_al_confirmar(sender, instance, **kwargs):
    """Receptor de ``post_delete``: retira del bucket lo que colgaba de la fila.

    Va en ``on_commit`` y no en el acto: si la transacción se deshace, la fila
    sigue ahí y su archivo tiene que seguir también. Al revés (borrar el archivo
    y que la fila vuelva) deja un registro apuntando a la nada.

    Cubre también las cascadas, porque Django emite ``post_delete`` por cada
    objeto que arrastra: borrar un proyecto limpia los documentos de todas sus
    entregas sin que nadie lo recuerde.
    """
    for archivo in archivos_de(instance):
        nombre = archivo.name
        almacen = archivo.storage
        transaction.on_commit(lambda n=nombre, a=almacen: a.delete(n))


def conectar_limpieza_de_archivos(*modelos):
    """Conecta ``borrar_archivos_al_confirmar`` a los modelos indicados.

    La llama cada app desde su ``apps.py`` con sus propios modelos, para no
    cruzar importaciones entre dominios al arrancar.
    """
    from django.db.models.signals import post_delete

    for modelo in modelos:
        post_delete.connect(
            borrar_archivos_al_confirmar,
            sender=modelo,
            dispatch_uid=f"cuentas.limpiar_archivos.{modelo._meta.label}",
        )


# --------------------------------------------------------------------------- #
# Borrado de una cuenta de cobro completa
# --------------------------------------------------------------------------- #
@transaction.atomic
def eliminar_cuenta_con_rastro(cuenta):
    """Borra una cuenta de cobro con todo lo que cuelga de ella.

    El único obstáculo real es ``RevisionCuentaCobro.asignacion``, que es
    ``PROTECT``: al borrar la cuenta, Django quiere llevarse las asignaciones y
    las revisiones se lo impiden. Retirando antes las revisiones, el resto cae
    en cascada (entregas y sus documentos, radicaciones, asignaciones,
    documentos de cierre, trámites finales y la bitácora).
    """
    from cuentas_de_cobro.models import RevisionCuentaCobro

    RevisionCuentaCobro.objects.filter(
        documento_entrega__cuenta_entrega=cuenta
    ).delete()
    cuenta.delete()


def cuentas_en_las_que_participa(usuario):
    """Cuentas de cobro donde ``usuario`` aparece de cualquier forma.

    Son las que impiden borrarlo: cada una lo referencia con una clave
    ``PROTECT``. Sirve para enseñar, antes de confirmar, qué se va a llevar por
    delante el borrado.
    """
    from cuentas_de_cobro.models import CuentaEntrega

    return CuentaEntrega.objects.filter(
        Q(usuario=usuario)
        | Q(documentoentrega__usuario=usuario)
        | Q(asignacionrevisor__revisor=usuario)
        | Q(asignacionrevisor__supervisor=usuario)
        | Q(revisionpararadicacion__supervisor=usuario)
        | Q(documentocierre__usuario=usuario)
        | Q(tramites_finales__usuario=usuario)
        | Q(eventos__actor=usuario)
    ).distinct()


def proyectos_que_dirige_o_coordina(usuario):
    """Proyectos que se irían en cascada al borrar al usuario."""
    from contenido.models import Proyectos

    return Proyectos.objects.filter(
        Q(creador_por=usuario) | Q(asignado_a=usuario)
    ).distinct()


def resumen_de_borrado(usuario):
    """Qué se llevaría por delante borrar a ``usuario``. Para la confirmación."""
    from contenido.models import Actividades

    cuentas = list(cuentas_en_las_que_participa(usuario))
    proyectos = list(proyectos_que_dirige_o_coordina(usuario))
    actividades = Actividades.objects.filter(
        Q(proyecto__in=proyectos) | Q(asignado_a=usuario) | Q(asignado_por=usuario)
    ).distinct()
    return {
        "usuario": usuario,
        "cuentas": cuentas,
        "proyectos": proyectos,
        "n_actividades": actividades.count(),
    }


@transaction.atomic
def eliminar_usuario_con_rastro(usuario):
    """Borra un usuario y todo lo que lo referencia.

    Es destructivo a propósito y sin vuelta atrás: se lleva las cuentas de
    cobro en las que aparece (completas, no solo su parte) y los proyectos que
    creó o coordina, con sus actividades. Por eso el admin enseña antes un
    resumen y lo reserva al superusuario.

    La alternativa sana para un usuario que ya trabajó es **desactivarlo**: no
    vuelve a entrar, desaparece de los desplegables y el expediente conserva
    quién hizo qué.
    """
    for cuenta in cuentas_en_las_que_participa(usuario):
        eliminar_cuenta_con_rastro(cuenta)
    proyectos_que_dirige_o_coordina(usuario).delete()
    usuario.delete()
