from django.apps import AppConfig


def _validar_antes_de_guardar(sender, instance, **kwargs):
    """Ejecuta ``full_clean()`` antes de cada guardado del ORM.

    Por qué hace falta: `CuentaEntrega` define un `clean()` con un invariante
    serio (el supervisor no puede emitir decisión hasta que los revisores hayan
    aprobado) y **no sobrescribe `save()`**, así que solo se comprobaba dentro de
    `revisar_supervisor()` y en los formularios. Tampoco se aplicaban las
    `choices` del campo `mes`, que en Django son validación de formulario y no
    restricción de base: una cuenta con mes 13 se guardaba sin protestar.

    Por qué va aquí y no en el modelo: `cuentas_de_cobro/models.py` es definitivo
    y lo edita el usuario. `apps.py` no lo es, y una señal consigue lo mismo.

    Hasta dónde llega: cubre todo lo que pase por el ORM (servicios, admin,
    shell, comandos). **No** cubre `bulk_create`, `queryset.update()` ni el SQL
    crudo. Para lo que tiene que ser cierto SIEMPRE está además la restricción
    `CHECK` de `cuentas_de_cobro/migrations/0007_restricciones_de_integridad.py`.
    """
    instance.full_clean()


class CuentasDeCobroConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cuentas_de_cobro'

    def ready(self):
        from django.db.models.signals import pre_save

        from .models import CuentaEntrega

        # Al nivel del módulo: Django conecta los receptores con referencia
        # débil y una función local de `ready()` se recolecta al salir.
        pre_save.connect(
            _validar_antes_de_guardar,
            sender=CuentaEntrega,
            dispatch_uid="cuentas_de_cobro.validar_cuentaentrega",
        )

        # Igual que en `contenido`: el archivo se va con la fila, también cuando
        # la fila cae en cascada al borrar la cuenta entera.
        from cuentas.borrado import conectar_limpieza_de_archivos

        from .models import DocumentoCierre, DocumentosCuentaCobro, TramiteFinal

        conectar_limpieza_de_archivos(
            DocumentosCuentaCobro, DocumentoCierre, TramiteFinal
        )
