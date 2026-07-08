from django.db import migrations

# Rol transversal de solo lectura: ve ambos dominios (Proyectos y Cuentas de
# cobro) pero no ejecuta ninguna acción. Es la única excepción a la regla de
# aislamiento entre dominios.
GRUPO = "Consulta"


def crear_grupo(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GRUPO)


def borrar_grupo(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GRUPO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("contenido", "0002_roles_groups"),
    ]

    operations = [
        migrations.RunPython(crear_grupo, borrar_grupo),
    ]
