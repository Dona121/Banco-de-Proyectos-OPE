"""Restricción CHECK del mes de la cuenta.

`CuentaEntrega.mes` declara `choices`, pero las `choices` de Django son
validación de formulario, no restricción de base, y el modelo no llama a
`full_clean()` en su `save()`: una cuenta con mes 0, 13 o -1 se guardaba sin
protestar. Lo detectó el sondeo adversarial (`tests/test_invariantes.py`).

El servicio ya lo valida y la señal de `apps.py` enciende el `full_clean()`.
Esto es la última red: lo único que tampoco salta el SQL crudo ni una operación
en lote.

Se usa `SeparateDatabaseAndState` porque `cuentas_de_cobro/models.py` es
definitivo y no se toca: la restricción se crea en la base pero el estado de
migraciones no la registra, así que `makemigrations` no detecta divergencia.
Comprobado en SQLite que queda aplicada y que rechaza un INSERT de SQL crudo
con mes 13; en PostgreSQL es el `ALTER TABLE` habitual de Django, no medido
contra la base real.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cuentas_de_cobro", "0006_remove_documentocierre_documento_cierre_unico_por_tipo_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.AddConstraint(
                    model_name="cuentaentrega",
                    constraint=models.CheckConstraint(
                        condition=models.Q(mes__gte=1) & models.Q(mes__lte=12),
                        name="cuenta_mes_valido",
                    ),
                ),
            ],
            state_operations=[],
        ),
    ]
