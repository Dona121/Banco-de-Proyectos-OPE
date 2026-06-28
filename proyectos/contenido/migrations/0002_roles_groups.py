from django.db import migrations

GRUPOS = ["Director", "Coordinador", "Formulador"]


def crear_grupos(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nombre in GRUPOS:
        Group.objects.get_or_create(name=nombre)


def borrar_grupos(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=GRUPOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("contenido", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(crear_grupos, borrar_grupos),
    ]
