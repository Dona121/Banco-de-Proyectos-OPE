"""Restricciones CHECK para lo que tiene que ser cierto SIEMPRE.

Las reglas de flujo (una sola entrega abierta, nadie revisa lo suyo, el orden de
los revisores) NO caben aquí: dependen de otras tablas y de quién actúa, y un
`CHECK` solo puede mirar su propia fila. Esas viven en los servicios, que es su
sitio. Lo que baja a la base es únicamente lo incondicional.

Se usa `SeparateDatabaseAndState` porque `contenido/models.py` es definitivo y no
se toca: la restricción se crea en la base, pero el estado de migraciones de
Django no la registra, así que `makemigrations` no detecta divergencia ni
intentará retirarla en la siguiente migración.

Comprobado en SQLite: la reconstrucción de tabla incluye el `CHECK` pese a no
estar en el estado del modelo, y **muerde contra un INSERT de SQL crudo**, que es
justo lo que ni los servicios ni la señal `pre_save` de `apps.py` pueden cubrir.
En PostgreSQL (producción) Django lo emite como `ALTER TABLE ... ADD CONSTRAINT`;
eso no se ha medido contra la base real, solo es cómo actúa el backend.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("contenido", "0003_rol_consulta")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.AddConstraint(
                    model_name="actividades",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            fecha_programada__lte=models.F("fecha_vencimiento")
                        ),
                        name="actividad_fechas_coherentes",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="actividadentrega",
                    constraint=models.CheckConstraint(
                        condition=models.Q(numero_version__gte=1),
                        name="entrega_version_positiva",
                    ),
                ),
            ],
            state_operations=[],
        ),
    ]
