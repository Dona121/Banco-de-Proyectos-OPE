# Comando de arranque en Railway. Mantenerlo igual al configurado en el panel.
#
# `--workers 3`: con el único worker de serie, cualquier operación lenta (generar
# el PDF de un reporte, por ejemplo) deja la plataforma sin responder a todos los
# demás mientras dura.
# `--timeout 120`: margen para esas mismas operaciones. Las subidas de archivos ya
# no lo necesitan (van directas al bucket, sin pasar por aquí), pero con 30
# segundos (el valor de serie) cualquier reporte grande moría a medias.
#
# `migrate --noinput`: se añadió al traer este despliegue cuatro migraciones
# (las extensiones de archivo administrables y las restricciones de integridad).
# Antes se aplicaban a mano, y eso significa que un despliegue con migración
# arranca bien y falla después, al tocar una tabla o una columna que no existe.
#
# ⚠️ Vale mientras Railway corra UNA sola réplica. Con dos o más, los
# contenedores arrancan a la vez y migrarían en paralelo; en ese caso hay que
# sacar `migrate` de aquí y aplicarlo en un paso previo al despliegue.
web: cd proyectos && uv run manage.py migrate --noinput && uv run manage.py collectstatic --noinput && gunicorn proyectos.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120
