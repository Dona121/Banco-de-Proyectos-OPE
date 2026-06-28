from django.db import migrations

GRUPOS = ["Radicacion", "Secop"]


def crear_grupos(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nombre in GRUPOS:
        Group.objects.get_or_create(name=nombre)


def borrar_grupos(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=GRUPOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cuentas_de_cobro", "0004_alter_documentocierre_usuario_tramitefinal"),
    ]

    operations = [
        migrations.RunPython(crear_grupos, borrar_grupos),
    ]
