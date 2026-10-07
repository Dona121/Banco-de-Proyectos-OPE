# Comando de arranque en Railway. Mantenerlo igual al configurado en el panel.
#
# `--workers 3`: con el único worker de serie, cualquier operación lenta (generar
# el PDF de un reporte, por ejemplo) deja la plataforma sin responder a todos los
# demás mientras dura.
# `--timeout 120`: margen para esas mismas operaciones. Las subidas de archivos ya
# no lo necesitan —van directas al bucket, sin pasar por aquí—, pero con 30
# segundos (el valor de serie) cualquier reporte grande moría a medias.
#
# NO incluye `migrate`: hoy las migraciones se aplican a mano. Ver docs/Pendientes.md.
web: cd proyectos && uv run manage.py collectstatic --noinput && gunicorn proyectos.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120
