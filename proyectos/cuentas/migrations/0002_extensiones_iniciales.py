"""Siembra las extensiones que hasta ahora estaban escritas en el código.

Sin esto, al desplegar la tabla quedaría vacía. El validador cae entonces al
conjunto de respaldo, así que no se rompería nada, pero el administrador abriría
el panel y no vería ninguna extensión que gestionar: el punto del cambio es
justamente que las vea y pueda tocarlas.
"""
from django.db import migrations

# Las mismas 12 de `cuentas.validadores.EXTENSIONES_POR_OMISION`, con una
# descripción para quien administre.
EXTENSIONES = [
    (".pdf", "Documento PDF"),
    (".doc", "Documento de Word (formato antiguo)"),
    (".docx", "Documento de Word"),
    (".xls", "Hoja de cálculo de Excel (formato antiguo)"),
    (".xlsx", "Hoja de cálculo de Excel"),
    (".odt", "Documento de LibreOffice"),
    (".ods", "Hoja de cálculo de LibreOffice"),
    (".jpg", "Imagen JPG"),
    (".jpeg", "Imagen JPG"),
    (".png", "Imagen PNG"),
    (".webp", "Imagen WebP"),
    (".zip", "Carpeta comprimida"),
]


def sembrar(apps, schema_editor):
    ExtensionArchivo = apps.get_model("cuentas", "ExtensionArchivo")
    for extension, descripcion in EXTENSIONES:
        # `get_or_create` y no `bulk_create`: si la migración se reaplica sobre
        # una base donde ya se administraron extensiones, no debe pisarlas ni
        # fallar por la restricción de unicidad.
        ExtensionArchivo.objects.get_or_create(
            extension=extension,
            defaults={"descripcion": descripcion, "activa": True},
        )


def borrar(apps, schema_editor):
    ExtensionArchivo = apps.get_model("cuentas", "ExtensionArchivo")
    ExtensionArchivo.objects.filter(
        extension__in=[e for e, _ in EXTENSIONES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("cuentas", "0001_initial")]

    operations = [migrations.RunPython(sembrar, borrar)]
