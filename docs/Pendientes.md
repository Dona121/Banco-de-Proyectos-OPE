# Pendientes y estado del proyecto

Última actualización: 2026-10-06. Documento de traspaso: resume qué se hizo, qué
quedó pendiente y con qué evidencia, para poder retomar en frío.

**Suite de pruebas: 204 en verde** (`cd proyectos && uv run python manage.py test
--settings=proyectos.test_settings`, ~10 s).

---

## Estado actual

La plataforma está desplegada en Railway contra un **proyecto de Supabase nuevo**
(ref `gqsvgpchdojaufsaygdf`; el anterior, `jzuhwyxzxnqosgfmpxhy`, está muerto).
Verificado en producción: la interfaz se ve bien en móvil y los usuarios entran
con sus contraseñas anteriores.

Migrado a la cuenta nueva: 31 usuarios (con nombre, correo, banderas, fecha de
alta y hash de contraseña intactos), los 9 grupos de rol y los catálogos de
cuentas de cobro (vigencia 2026, 7 tipos de documento, 7 requisitos obligatorios).
No había filas transaccionales ni archivos subidos, así que no hubo nada más que
mover. La comparación origen–destino dio coincidencia exacta campo por campo.

`main` incluye dos PR ya mergeados:

- **PR #1**: Tailwind compilado en lugar del CDN, y el endpoint S3 movido a
  variable de entorno.
- **PR #2**: endurecimiento HTTPS y cuatro guardas de negocio en cuentas de cobro.

Los detalles de ambos están en los mensajes de esos commits, que son extensos a
propósito. `CLAUDE.md` recoge lo que quedó siendo permanentemente cierto
(variables de entorno, flujo de compilación de Tailwind, avisos sobre las pruebas).

---

## ~~Pendiente 1~~: RESUELTO (2026-10-06): N+1 de consultas

Medido con `CaptureQueriesContext` sobre SQLite; contra Supabase pesa mucho más,
porque cada consulta paga latencia de red.

| Página | Consultas SQL |
|---|---|
| Bandeja de cuentas de cobro, 10 filas | **161** |
| Detalle de una cuenta | **109** |

Tres causas que se acumulan:

1. `cuentas_de_cobro/views.py`, `BandejaView.get_context_data` llama
   `services.paso_actual(c)` **por fila**. Esa función entra a
   `flujo_de_cuenta`, que a su vez llama `entrega_enviada`, `ultima_entrega`,
   `documentos_cierre_faltantes`, `tipos_obligatorios`, `radicacion_rechazada` y
   `tramite_realizado`: unas 15 consultas por fila.
2. `services.notificaciones_para(user)` corre en **cada petición** desde los
   context processors, e itera en Python sobre todas las cuentas abiertas.
3. `roles.roles_de(user)` vuelve a consultar `user.groups` en cada llamada a
   `es_*()`. Solo los dos context processors disparan ~10 consultas de grupos por
   petición.

**Causa 3: RESUELTA (2026-10-06).** `cuentas.roles.roles_de` cachea los grupos
en el objeto `user` (equivale a cachear por petición, porque `request.user` se
resuelve una vez), y `cuentas_de_cobro.roles` reutiliza esa misma función en vez
de duplicar la consulta. La caché se invalida por `m2m_changed` sobre
`User.groups` (ver `cuentas/apps.py`), así que cambiar los grupos de un usuario
ya consultado no deja roles obsoletos. Medido después:

| Página | Antes | Después |
|---|---|---|
| Dashboard de formulador **sin datos** | 39 (23 a `auth_group`) | **17 (1)** |
| Bandeja, 10 filas | 161 | **138** |
| Detalle de una cuenta | 111 | **89** |

(El suite completo también bajó de ~6,5 s a ~3 s.)

**Causas 1 y 2: RESUELTAS (2026-10-06).** Se tomó el camino que aquí se
recomendaba: **separar "obtener los datos" de "decidir el estado"**. Los lectores
del flujo pasaron de `.filter()/.exists()` (que siempre van a la base) a `.all()`
+ filtrado en Python, y las dos vistas que pagaban el coste por fila traen ahora
las relaciones por lote:

- `services.RELACIONES_DEL_FLUJO`: la tupla de `prefetch_related` que necesita
  `flujo_de_cuenta` para resolver las ocho etapas sin volver a la base. La usan
  `BandejaView`, `CuentaDetailView` y `notificaciones_para`.
- `services.precargar_tipos_obligatorios(cuentas)`: una sola consulta de
  requisitos para toda la página, en vez de una por cuenta.

Medido sobre la base local, con la caché de proceso ya caliente:

| Página | Antes | Tras la causa 3 | Hoy |
|---|---|---|---|
| Bandeja | 161 (10 filas) | 138 | **23** (9 filas) |
| Detalle de una cuenta | 111 | 89 | **35** |
| Dashboard de formulador sin datos | 39 | 17 | **17** |

Las dos trampas que se habían anotado al estudiarlo se respetaron:

- **No se memoiza por instancia a ciegas.** `responder_tramite` consulta
  `tramite_realizado`, guarda el trámite y **vuelve a consultarlo** para decidir
  si cierra la cuenta; con un valor memoizado, la cuenta no se cerraría nunca.
  Lo mismo con `documentos_cierre_faltantes` dentro de `cargar_documento_cierre`.
  Por eso lo que se precarga es la relación (que Django invalida al guardar), no
  el resultado de la decisión.
- **`.all()` + filtrado en Python** solo rinde si quien llama trae el
  `prefetch_related`; por eso `RELACIONES_DEL_FLUJO` es pública y está citada en
  el docstring de los lectores, para que una vista nueva no abra un N+1 sin
  enterarse.

La red que permitió hacerlo sin romper nada es
`cuentas_de_cobro/tests/test_pasos.py`, que fija el resultado exacto de las ocho
etapas y del paso actual en cada punto del flujo (incluidos devolución, rechazo en
radicación, rechazo del supervisor y cuenta cerrada).

---

## ~~Pendiente 5~~: RESUELTO (2026-10-06): el flujograma tras una devolución

Detectado al escribir las pruebas de caracterización, no leyendo el código.

Cuando un revisor devuelve la cuenta, nace la versión siguiente **vacía y sin
entregar**, así que la pelota vuelve al contratista. Los permisos y las
notificaciones lo entienden bien: el revisor no puede revisar y el contratista
recibe "Corrige y vuelve a entregar". Pero la presentación no:

- La etapa de cargue aparece **hecha**, con el detalle "Versión 2 entregada",
  cuando esa versión está vacía y nadie la ha entregado.
- La etapa de revisión aparece **en curso**, y por eso la bandeja muestra
  `paso actual = "Revisión"` con `responsable = "Revisores"`.

Es decir, **la bandeja señala al revisor cuando el trabajo lo tiene el
contratista**. En una pantalla cuya razón de ser es saber de quién depende cada
cuenta, lleva a perseguir a la persona equivocada.

Causa: `flujo_de_cuenta` leía el **historial** de la cuenta ("ya pasó por
radicación") en lugar del estado de la **versión vigente** ("la versión 2 no ha
salido del escritorio del contratista"). La primera etapa se evaluaba con
`enviada or radicada` (y `radicada` sigue siendo cierto tras la devolución), y la
etapa de revisión se ponía en curso sin comprobar que hubiera algo entregado.
Ese `or radicada` no protegía ningún caso legítimo: para radicar, el sistema
exige la entrega, y `fecha_radicacion` es de solo lectura en el admin.

Arreglado con tres reglas:

1. El cargue está hecho solo si **la versión vigente** fue entregada.
2. La revisión está en curso solo si hay **una versión entregada** que revisar.
3. El paso siguiente es **la próxima etapa que falte**, no la siguiente de la
   lista; y el responsable de la etapa de revisión se deduce de quién no ha
   aprobado la versión vigente, sin exigir que esté entregada, para poder
   nombrar al revisor que la recibirá.

Las reglas 1 y 2 corrigen el responsable actual; la 3 evita el efecto colateral
de anunciar "Radicación" como paso siguiente en una cuenta que ya está radicada.
Resultado verificado en los tres escenarios:

| Situación | Paso actual | Paso siguiente |
|---|---|---|
| Devolución de **revisor** (ya radicada) | Cargue y entrega · Contratista | Revisión · Revisor técnico |
| La misma, tras reentregar | Revisión · Revisor técnico | Decisión del supervisor · Supervisor |
| Devolución en **radicación** (nunca radicada) | Cargue y entrega · Contratista | Radicación · Supervisor o rol de radicación |

La regla 3 distingue los dos tipos de devolución sin casos especiales, porque se
apoya en qué etapas están hechas. Fijado en `test_pasos.py`
(`test_devolucion_de_revisor`, `test_tras_reentregar_vuelve_a_mandar_el_revisor_tecnico`,
`test_devolucion_en_radicacion_si_vuelve_a_radicacion`).

---

## ~~Pendiente 2~~: RESUELTO (2026-10-06): `proyectos/test_settings.py`

El storage por defecto era S3, así que **cualquier prueba que guardara un
`FileField` subía el archivo a la Supabase de producción** cuando hay
credenciales en el entorno (y fallaba con `NoCredentialsError` cuando no las hay:
así se descubrió, 51 de 87 pruebas en error). `test_services.py` y
`test_views.py` no tenían ninguna protección; se verificó que el storage por
defecto resolvía al bucket `documentos` del proyecto **vivo**.

Resuelto con el módulo que pedía esta nota: `proyectos/test_settings.py` hereda
de `proyectos.settings` y fija SQLite en memoria + `InMemoryStorage` + storage de
estáticos plano + hasher MD5, y define `SECRET_KEY`/`HTTPS_ESTRICTO` antes de
importar los settings reales (así el suite corre sin `.env`). Se eliminaron los
`@override_settings(STORAGES=...)` de `web/tests.py` y `test_guardas.py`, que ya
sobran. Comando documentado en `CLAUDE.md` y en el `README.md`:

```bash
cd proyectos && uv run python manage.py test --settings=proyectos.test_settings
```

Efecto colateral: el override de `web/tests.py` fijaba
`CompressedManifestStaticFilesStorage` y hacía que 2 pruebas dependieran de haber
corrido `collectstatic`; con el storage plano del módulo, el suite pasa completo
(**107 pruebas, ~7 s**, frente a 96 con 2 errores y ~110 s).

---

## ~~Pendiente 3~~: RESUELTO (2026-10-06): aislamiento de dominios

Las vistas de `web` solo llevaban `LoginRequiredMixin`: un **Contratista**
recibía 200 en `/proyectos/`, `/actividades/`, `/reportes/` y los dos reportes
descargables. No había fuga de datos (los selectores le devolvían vacío), pero
contradecía la regla de aislamiento, que es la que se ajustó.

- `cuentas.mixins.ModuloProyectosRequeridoMixin` (Director/Coordinador/Formulador
  + `Consulta`) en las vistas de lectura del dominio; las de acción conservan
  además su mixin específico.
- `cuentas.decorators.rol_requerido` para las dos vistas función de reportes.
- **El panel de inicio es la excepción deliberada.** Es el destino del login de
  todos los roles (`LOGIN_REDIRECT_URL = web:dashboard`), así que ponerle el
  mixin habría dejado a todo el módulo de cuentas de cobro con un 403 al entrar.
  En su lugar, a quien solo pertenece a ese módulo se le redirige a su bandeja;
  antes aterrizaba en un "tu cuenta no tiene un rol asignado" que era falso.

Verificado: el contratista recibe 403 en las cuatro rutas y 302 a su bandeja en
la raíz; `Consulta` sigue entrando a ambos dominios. Pruebas en
`web.tests.AislamientoDeDominiosTest`.

---

## Pendiente 4: Detalles menores

Resueltos el 2026-10-06:

- ~~**Validación de archivos subidos**~~ → `cuentas/validadores.py`
  (`ArchivoValidadoMixin`) en los cuatro formularios con `FileField`: extensión de
  una lista blanca y un tope de tamaño. Antes se podía subir un ejecutable. (El
  tope arrancó en 20 MB y hoy es **1 GB**, el del bucket, desde que la subida va
  directa y no pasa por Django.)
- ~~**Reportes que ignoran filtros inválidos en silencio**~~ → ahora vuelven a
  `/reportes/` con un mensaje que nombra el campo y el motivo, en vez de generar
  el reporte completo (que es justo lo que no se pidió, y encima pasaba
  desapercibido). Sin filtros sigue saliendo el reporte completo, que es lo
  esperado.
- ~~**`CuentaEntrega.__str__` muestra el mes como número**~~ →
  `services.nombre_de_cuenta` da "2026 · Junio" y lo usan las migas, las
  notificaciones y el filtro de plantilla `cc_nombre`. El modelo no se tocó.
- ~~**`django-import-export` sin usar**~~ → fuera (cero referencias en el código
  y no estaba ni en `INSTALLED_APPS`). Se fueron con él `tablib` y
  `diff-match-patch`.
- ~~**`psycopg2` compilado desde fuente**~~ → `psycopg2-binary`.
- ~~**`django-unfold` sin tope**~~ → `>=0.97.0,<0.98`.
- ~~**`DEBUG` fijo en `False`**~~ → variable `DEBUG` (por omisión `0`). Con
  `DEBUG=1` los estáticos pasan al storage plano, así que en local ya no hace
  falta `collectstatic`.
- ~~**`ALLOWED_HOSTS=["*"]` fijo**~~ → variable `ALLOWED_HOSTS`, con `*` por
  omisión (lo que necesita Railway).
- ~~**Alpine y HTMX desde CDN sin `integrity`**~~ → servidos desde
  `static/vendor/` con la versión en el nombre. En vez de añadir SRI se quitó la
  dependencia de terceros, que es la decisión que ya se había tomado con
  Tailwind. Google Fonts es el único recurso externo que queda.
- ~~Imports sin usar~~, ~~carpeta vacía `%22contexto/`~~ (borrada).

Siguen abiertos:

- **HSTS en 1 hora**: subir `SECURE_HSTS_SECONDS` a `31536000` cuando se haya
  verificado que el sitio va estable por HTTPS. **Decisión pendiente**: el
  navegador recuerda la cabecera durante todo ese tiempo y no hay forma de
  retirársela a quien ya la recibió.
- ~~**El despliegue no es reproducible desde el repo**~~: resuelto el 2026-10-06.
  El comando de arranque estaba solo en el panel de Railway; ahora está también
  en el `Procfile` de la raíz, idéntico, y explicado en `CLAUDE.md`:

  ```
  cd proyectos && uv run manage.py collectstatic --noinput && gunicorn proyectos.wsgi:application --bind 0.0.0.0:$PORT
  ```

  Consecuencias que aclara: `collectstatic` corre **en cada arranque** (no en el
  build), y por eso `proyectos/staticfiles/` pasó a estar ignorado; y el riesgo
  del manifiesto en producción queda descartado mientras ese comando no cambie.

- ⚠️ **`migrate` no está en el arranque** (detectado al documentar el comando).
  Las migraciones se aplican a mano, así que un despliegue que traiga una
  migración nueva **arranca bien y falla después**, al tocar la columna que no
  existe. Propuesta: añadir `uv run manage.py migrate --noinput &&` antes de
  gunicorn, tras confirmar que hay una sola réplica (si no, dos contenedores
  migrarían a la vez). **Decisión pendiente.**

- **Gunicorn con un solo worker sync** (el de serie). Aguanta la carga actual,
  pero una exportación larga de PDF o Excel bloquea el resto de peticiones
  mientras corre. Subir workers es un cambio de una línea en el comando.
- **Restos de pruebas en `proyectos/media/`** y **13 logos publicados de los que
  se usan 2** (`escudo-blanco.png`, `escudo-color.png`): son archivos del
  usuario, no se borran por iniciativa propia.
- `check --deploy` reporta `W005` y `W021` a propósito (ver `CLAUDE.md`).

---

## Revisión del 2026-10-06

### Arreglado

- **Filtros numéricos de la querystring → 500.** `?contratista=abc` en la bandeja
  y `?proyecto=abc` en `/actividades/` llegaban al ORM como id y lanzaban
  `ValueError: Field 'id' expected a number`. Ahora el valor se descarta si no es
  numérico. Con pruebas de regresión en `test_views.py` y `web/tests.py`.
- **Los errores 500 no dejaban rastro.** El handler de consola que trae Django
  está filtrado por `require_debug_true` y el de correo exige `ADMINS`: con
  `DEBUG=False` y sin `ADMINS`, ningún traceback llegaba a los logs de Railway.
  `settings.py` define ahora un `LOGGING` que manda `django` (y con él
  `django.request`, que es quien emite el traceback de cada 500) a la salida
  estándar. Nivel por `LOG_LEVEL`, `INFO` por defecto.
- **El paquete entregado se podía modificar.** `puede_cargar_documentos` no
  comprobaba `entrega_enviada` ni `radicacion_rechazada`, así que un POST directo
  colaba un documento en una versión ya en revisión; entraba en `PENDIENTE` y
  dejaba al revisor de turno **sin ninguna acción válida** (no puede aprobar
  porque hay un pendiente, ni devolver porque no hay ninguno rechazado). Guarda
  en el selector y en `services.adjuntar_documento` (que además exige que sea la
  última versión). Hacía falta un `RequisitoDocumental` **opcional** para
  explotarlo; con el catálogo actual (todos obligatorios) no era alcanzable.
- **Rechazar un documento sin causal.** `services.revisar_documento` exige ahora
  comentario cuando el estado es `RE` (es lo que el contratista necesita para
  corregir, y la devolución de la cuenta se apoya en esos rechazos), y el campo
  se marca `required` en la plantilla cuando se elige "Rechazado".

### Cuatro ajustes del 2026-10-06 (plan aprobado)

1. **Filtros de director, coordinador y formulador en `/proyectos/`.** Las opciones
   salen de `selectors.opciones_de_filtro`, derivadas de `proyectos_visibles`: una
   sola regla, sin ramas por rol, y ninguna opción puede devolver lista vacía. De
   paso se arregló `components/paginacion.html`, que concatenaba a mano unos pocos
   parámetros y **perdía los filtros al cambiar de página**: ahora usa el tag
   `{% querystring %}` que Django 5.2 ya trae.

   ⚠️ **Corregido después, al probarlo el usuario.** La primera versión ocultaba el
   desplegable que tuviera menos de dos opciones, con el argumento de que a un
   coordinador el filtro de coordinador solo le ofrecería su propio nombre. En la
   base real del usuario eso escondió **los tres**, y el reporte fue literalmente
   "aún no me aparecen los filtros". Ahora se pinta cualquier desplegable con al
   menos una opción. La lección: una heurística de "esto no le sirve al usuario"
   que depende del tamaño de los datos se comporta distinto en una base pequeña,
   que es justo donde se prueba.


2. **Subida de archivos.** Ver el bloque en `CLAUDE.md`. Tres partes:
   - *El 500 de las tildes*: `cuentas.validadores.nombre_seguro` translitera el
     nombre (NFKD → ASCII) y el mixin lo aplica a los cuatro formularios con
     archivo. No era Django: `validate_file_name` acepta tildes, y el nombre
     llegaba sin tocar a boto3 y la clave no-ASCII rompía contra el gateway S3 de
     Supabase.
   - *Subida directa al bucket* con porcentaje de avance, para entrega y cierre.
     No hace falta configurar CORS (ni se puede: Supabase no implementa
     `PutBucketCors`). Comprobado contra el proyecto vivo: el gateway responde al
     preflight con `Access-Control-Allow-Origin: *` y admite `PUT` con la cabecera
     `content-type`, que es lo único que manda este flujo. El comando para
     volver a comprobarlo está en `CLAUDE.md`.
   - *Límite* a 1 GB y `Procfile` con `--workers 3 --timeout 120`.

2b. **Cancelar la subida y descargar los adjuntos** (en los cuatro espacios donde
   se cargan archivos).

   - *Cancelar*: el componente guarda la petición en curso y la aborta con
     `XMLHttpRequest.abort()`. El botón solo aparece mientras el archivo viaja:
     una vez el servidor lo está registrando ya no hay nada que detener. Abortar
     un `PUT` deja el objeto sin crear, así que no hay basura que limpiar.
   - *Descargar*: enlace nuevo junto a "Abrir", que pasa por el servidor
     (`cuentas_cobro:descargar` y `web:documento_descargar`) en vez de poner el
     enlace del bucket en la plantilla. Dos ventajas: se comprueba que quien
     descarga puede ver esa cuenta o esa entrega, y el archivo baja con un nombre
     que dice qué es ("Cuenta de cobro 2026 Octubre.pdf") en lugar de la clave
     con el fragmento aleatorio. Lo hace `cuentas.subidas.url_de_descarga`,
     firmando `ResponseContentDisposition`; el atributo `download` de un enlace
     no serviría, porque el archivo está en otro dominio y los navegadores lo
     ignoran para enlaces de otro origen.

   Verificado contra el bucket real: la descarga responde
   `Content-Disposition: attachment; filename="Cuenta_de_cobro_2026_Octubre.pdf"`,
   y un nombre con tildes viaja normalizado (la cabecera tiene que ser ASCII).

3. **Fuera el hover que levanta la tarjeta y los difuminados.** Se quitó la regla
   `.card-hover` de `app.css` (su único consumidor era la lista de proyectos), el
   `hover:shadow-md` de la línea de tiempo de actividades y los seis
   `backdrop-blur` (overlay del menú móvil, barra superior y las cuatro pastillas
   del login). La barra superior pasó a fondo opaco: translúcida sin difuminado
   dejaba ver el contenido por debajo. **No se encontró ningún difuminado sobre
   iconos**; si el efecto que molestaba era otro, hay que señalar dónde se ve.

4. **Aviso al contratista cuando el cierre está cargado.** Antes se quedaba sin
   noticias en cuanto el supervisor aprobaba.

5. **Flujograma en `/proyectos/`.** El mismo modal que ya existía en actividades,
   ahora también desde el listado de proyectos, que es donde la gente entra primero.
   Reutiliza `web/actividades/_flujograma.html`, sin duplicar el diagrama.

6. **Quitar documentos ya cargados**, en los tres espacios donde tiene sentido:
   documentos de la entrega (contratista, antes de entregar), documentos de cierre
   (radicación, antes del primer trámite final) y documentos de una entrega de
   Proyectos (quien la hizo, mientras la actividad no esté aprobada). Componente
   `components/eliminar_archivo.html`, con confirmación en dos pasos. El archivo se
   borra del bucket en `transaction.on_commit`: dentro de la transacción, un
   rollback dejaría la fila apuntando a un archivo que ya no existe. La bitácora
   conserva el hecho aunque el archivo desaparezca.

7. **Reemplazar la evidencia de un trámite final, mientras ese paso siga siendo el
   actual.** Nació de una pregunta del usuario sobre qué eran "el trámite y la
   evidencia": el soporte de SIIFWEB o de SECOP II se adjunta una sola vez y, si
   sale mal (una captura ilegible, el pantallazo equivocado), no había forma de
   cambiarlo. Ahora lo hace **el mismo rol que respondió** y **no reabre nada**: el
   trámite sigue realizado y la cuenta se queda donde está. Queda en la trazabilidad
   (`Eventos.EVIDENCIA_REEMPLAZADA`) y el archivo anterior se borra del bucket tras
   confirmar la transacción. `selectors.puede_reemplazar_evidencia` +
   `services.reemplazar_evidencia`; pruebas en `ReemplazarEvidenciaTest`.

   ⚠️ **Corregido el mismo día, por indicación del usuario.** La primera versión
   permitía reemplazar **también con la cuenta cerrada**, con el argumento de que el
   cierre es automático y si no el error se detectaría siempre demasiado tarde. El
   usuario lo rechazó: *"el reemplazo solo debe servir cuando el usuario encargado
   está cargando el archivo; una vez terminada la gestión no debería ser capaz de
   modificarla"*. Tenía razón: esa ventana no se cerraba nunca, así que un soporte
   podía cambiar años después de cerrado el expediente, sin que nadie lo estuviera
   esperando. Ahora la ventana se cierra con cualquiera de las dos condiciones: que
   se haya respondido el **trámite siguiente** o que la **cuenta esté cerrada**.
   Consecuencia asumida y documentada: **el cargue en SECOP II no tiene ventana**,
   porque responderlo cierra la cuenta en la misma transacción.

   Lección de método: cuando un argumento de diseño es "si no, el error se
   detectaría demasiado tarde", conviene mirar si lo que se está construyendo es una
   ventana con cierre o una puerta abierta de forma permanente. Era lo segundo.

10. **Confirmación antes de enviar, en los cuatro cargues.** Salió de una pregunta
    del usuario: *"¿el usuario cuando carga un documento errado puede volver a
    cargar y se quita el anterior, antes de darle aprobar al envío?"*. Al revisarlo,
    la respuesta era que **ese "aprobar el envío" no existía**: el botón subía el
    archivo y, en los trámites finales, respondía el trámite y cerraba la cuenta, de
    un solo clic. Y el estado intermedio que haría falta ("archivo puesto pero sin
    enviar") está prohibido por el modelo: `TramiteFinal` tiene un
    `CheckConstraint` que no admite evidencia sin `realizado=True`, y `models.py` no
    se toca.

    Ahora el botón abre un resumen de lo que se va a enviar (archivo con su tamaño y
    cada campo visible; un `select` muestra el texto de la opción, no el id) con
    **Confirmar envío** y **Volver a elegir**. En ese punto el archivo no ha salido
    del equipo, así que volver atrás no deja nada a medias ni basura en el bucket.
    Va en los cuatro cargues por consistencia, a petición del usuario, y los
    irreversibles añaden un aviso de lo que implica confirmar (el del cargue en
    SECOP II dice que la cuenta se cierra y el soporte ya no se podrá cambiar).

11. ⚠️ **Fallo de reactividad encontrado al probar lo anterior en el navegador, y
    anterior a este cambio.** `puedeSubir` lee los campos con `querySelectorAll`,
    es decir del DOM, que **no es reactivo**: Alpine solo reevalúa un getter cuando
    cambia una propiedad reactiva que ese getter tocó. Escribir en un `<input>` no
    cambia nada que Alpine vigile, así que **elegir primero el archivo y después el
    tipo dejaba el botón deshabilitado sin forma de rehabilitarlo**. Al revés
    funcionaba (elegir el archivo dispara `elegir()`, que sí toca estado reactivo),
    y ese es el orden natural, por eso llevaba semanas sin notarse.

    Arreglado con un contador `tocado` que suben `@input`/`@change` en la raíz del
    componente (los eventos burbujean, así que un solo oyente cubre todos los
    campos) y que los getters que leen el DOM declaran al principio. Es exactamente
    el tipo de defecto que no aparece en una prueba de plantilla: el HTML era
    correcto en los dos casos.

12. **Se retiró el borrado de documentos de cierre** (reportado por el usuario:
    *"después de subir todos y eliminarlo posteriormente me trabó el flujo"*).
    Completar el cierre es lo que habilita el cargue en SIIFWEB, así que retirar un
    documento después lo deshabilitaba otra vez: la cuenta quedaba oscilando entre
    "lista para SIIFWEB" y "faltan documentos" sin explicación visible. Y
    `DocumentoCierre` es **único por (cuenta, tipo)**, de modo que tras retirarlo
    tampoco se podía recargar ese tipo: la "corrección" dejaba el expediente peor
    que antes.

    Retirados la opción en la plantilla, la clave de contexto, la rama de la vista,
    `selectors.puede_eliminar_cierre` y `services.eliminar_documento_cierre`. El
    evento `Eventos.CIERRE_ELIMINADO` **se conserva**: hay cuentas con ese registro
    en la bitácora y la bitácora no se reescribe. Regresión en `CierreNoSeQuitaTest`
    (la interfaz no lo ofrece, un POST directo responde 403, y el cierre completo
    deja SIIFWEB habilitado), que es la prueba que faltaba: el borrado de cierre no
    tenía ninguna, y por eso se pudo introducir sin que nada avisara.

    ⚠️ **Queda abierto:** sin borrado, un cierre mal cargado **no se corrige desde
    la aplicación**, solo desde el admin. La salida limpia es **reemplazar el
    archivo sin borrar la fila**, como ya se hace con la evidencia de un trámite:
    no descompleta el cierre (así que no reproduce el problema) y esquiva la
    unicidad. Pendiente de decidir si se implementa.

13. **Se retiró el reemplazo de evidencia de los trámites finales**, a petición del
    usuario (*"quita la opción de reemplazar en siifweb"* / *"con la confirmación
    por ahora está bien"*). Como el cargue en SECOP II ya no tenía ventana
    (responderlo cierra la cuenta), quitar la de SIIFWEB **elimina la función
    entera**, así que se retiró el código completo en vez de dejarlo muerto:
    `services.reemplazar_evidencia`, `selectors.puede_reemplazar_evidencia`, el
    destino de subida `"evidencia"` con sus dos ramas, la clave de contexto
    `puede_reemplazar` y el bloque de la plantilla. `Eventos.EVIDENCIA_REEMPLAZADA`
    **se conserva**, por la misma razón que `CIERRE_ELIMINADO`: puede haber cuentas
    con ese registro y la bitácora no se reescribe.

    El criterio que queda, y que conviene no volver a discutir desde cero: **la
    protección va antes de registrar, no después**. La pantalla de confirmación
    muestra el archivo y el comentario y permite cambiarlos sin coste, porque hasta
    ahí nada salió del equipo del usuario. Un soporte que se puede cambiar después
    de registrado debilita el valor probatorio del expediente, que es justo lo que
    este módulo existe para sostener.

    Regresión en `EvidenciaNoSeReemplazaTest`: el detalle no lo ofrece, los dos
    endpoints responden 403 con destino `evidencia`, y las funciones ya no existen.

    Balance de las tres decisiones seguidas (reemplazo con ventana → sin ventana →
    retirado) y del borrado de cierre: el sistema ya no deja **deshacer** nada en la
    etapa de cierre, y a cambio **avisa antes** en todos los cargues. Si aparece un
    caso real de archivo equivocado ya registrado, la vía es el admin.

8. **Sin sombras en los botones** (`.btn-primary`, `.btn-outline`). Se conservó el
   anillo de `:focus-visible`: esa no es decoración, es lo que permite usar la
   aplicación con el teclado.

9. **Fuera el guion largo de todo el sistema** (código, plantillas y documentación).
   ⚠️ **El reemplazo automático causó daño colateral y hay que saberlo**: una
   expresión regular que emparejaba guiones como si fueran pares de paréntesis
   corrompió 27 frases, cruzando paréntesis entre bloques distintos (p. ej. el
   cierre de una viñeta terminaba dentro de la siguiente). Se detectaron buscando
   paréntesis descompensados y se repararon una por una, descartando 22 falsos
   positivos que eran paréntesis legítimos repartidos en varias líneas. Hoy se
   repitió el barrido sobre los 123 archivos de texto del proyecto: quedaba **una**
   frase dañada en `CLAUDE.md`, ya reparada, y los dos únicos avisos restantes son
   falsos positivos dentro del bundle minificado de Alpine.
### Cuatro ajustes del 2026-10-07 (plan aprobado)

14. **La entrega tiene un momento de cierre: botón "Realizar entrega".** Reportado
    por el usuario: *"hoy el usuario puede hacer entregas simultáneas e incluso
    cargar documentos a entregas pasadas"*. La causa era que `crear_entrega` ponía
    la actividad En revisión en el mismo acto de crear la versión, así que abrir y
    entregar eran lo mismo. Comprobado en la base local antes de tocar nada: la
    actividad 4 tenía **tres entregas sin revisar a la vez**.

    Ahora son dos tiempos: abrir la versión (borrador, solo la ve su autor, la
    actividad sigue Pendiente) y **realizarla** (exige al menos un documento, pasa
    a En revisión y congela el paquete). Solo hay un borrador abierto por
    actividad, y se reabre únicamente con "Requiere ajustes", como una versión
    nueva.

    **Sin tocar el modelo**, que es definitivo: el estado se deriva en
    `web/selectors.py` (`entrega_enviada`, `borrador_de`, `puede_documentar`,
    `puede_realizar_entrega`). Una entrega está enviada si tiene revisión o si es
    la vigente de una actividad En revisión; una versión que no es la vigente
    siempre tiene revisión, así que la regla cierra sola. Eso ordena de paso los
    datos heredados: las entregas simultáneas que no son la última quedan
    **reemplazadas**, ni editables ni revisables, sin migración de datos.

    La guarda que importa está en el **servidor**, no en la plantilla: los cuatro
    puntos de `views.py` que repetían el criterio a mano pasan a
    `selectors.puede_documentar`, incluidos los dos endpoints de subida directa.
    Esconder el botón no habría impedido un POST.

15. **La revisión pasa a tener dos salidas.** "Rechazada" hacía exactamente lo
    mismo que "Requiere ajustes" (dejar la actividad para corregir) y confundía.
    Retirada del formulario, del servicio y del admin. **Sigue en el modelo**
    (`contenido/models.py` es definitivo) y se conserva su estilo en
    `RESULTADO_CLASE`, para que las revisiones históricas que la tengan se sigan
    pintando.

16. **Entregar detiene el reloj del vencimiento.** Una actividad En revisión o
    Aprobada ya no se marca vencida aunque su fecha haya pasado, se haya entregado
    a tiempo o tarde. Si la devuelven, vuelve a contar. Eran cuatro sitios con la
    misma condición incompleta (excluir solo Aprobada): el filtro `vencida`, las
    métricas de los paneles (`ESTADOS_SIN_PLAZO`), los avisos de plazo de la
    campana y una condición suelta (`dr < 0`) en el panel de formulador, que se
    había escapado del filtro.

    De paso, los dos accesos directos a "nueva entrega" del panel de formulador
    apuntaban a `entrega_nueva` sin comprobar permiso: con un borrador abierto
    habrían dado 403. Ahora llevan al detalle de la actividad, que decide qué
    ofrecer.

17. **Extensiones de archivo administrables.** Eran un `frozenset` en el código.
    Ahora `cuentas.models.ExtensionArchivo`, editable desde el panel, sembrado con
    las 12 de siempre en una migración de datos. El validador lee las activas con
    caché de proceso; **si la tabla queda vacía cae al conjunto del código**, para
    que un borrado accidental no deje la plataforma sin poder subir nada.

    ⚠️ **Primera migración desde que se documentó que `migrate` no está en el
    arranque de Railway.** Hay que aplicarla a mano, o añadirla al comando.

    Dos defectos que solo aparecieron al escribir las pruebas, los dos reales:

    - **El receptor de señal se recolectaba.** Estaba definido dentro de
      `ready()`, y Django conecta con referencia débil: al terminar `ready()` la
      función se recolecta y la señal deja de llegar **sin ningún aviso**. La
      caché nunca se invalidaba, así que activar o desactivar una extensión desde
      el panel no surtía efecto. Movido al nivel del módulo, como
      `_limpiar_cache_de_roles`.
    - **"No enviada" no es lo mismo que "borrador".** Una versión reemplazada por
      otra posterior tampoco está enviada, y `puede_documentar` la dejaba
      editable. Ahora exige ser **el** borrador abierto, no solo no estar enviada.

18. **Dos notificaciones rotas** por la limpieza del guion largo
    (`web/services.py`): *"Actividad asignada: realízala y entrégala ({nombre}"* y
    *"...vuelve a entregar) {nombre}"*. Eran los dos únicos casos que quedaban;
    el barrido anterior no los vio porque contaba paréntesis **por bloque**, y un
    `(` en una cadena y un `)` en la siguiente se compensaban. El barrido nuevo
    recorre los literales de cadena con AST, uno a uno.

    Método para repetirlo: `ast.walk` sobre cada archivo, mirando `ast.Constant`
    y la parte literal de cada `ast.JoinedStr` (f-string), y comparando `(` con
    `)` dentro de cada cadena de una sola línea.

**Suite: 229 pruebas** (eran 207). Clases nuevas en `web/tests.py`:
`RealizarEntregaTest`, `RevisionSinRechazoTest`, `VencimientoTest` y
`ExtensionesParametrizablesTest`. El andamiaje `ReglaBBaseTest._entrega` ahora
hace los dos pasos (abre y realiza) y se añadió `_borrador` para las pruebas que
necesitan una entrega a medias.

### Sondeo adversarial del 2026-10-07: dónde cedía la lógica

El usuario preguntó si la suite cubría todos los casos. No los cubría: probaba
los caminos buenos. Se escribieron dos módulos que **atacan**, saltándose la
vista y llamando al servicio o al modelo directamente, que es lo que haría el
admin, un comando, una tarea futura o dos peticiones simultáneas:
`web/test_invariantes.py` y `cuentas_de_cobro/tests/test_invariantes.py`.

**Seis pruebas nacieron fallando, y las seis eran defectos reales.** Todas del
mismo patrón: *la regla vivía en la vista y el servicio aceptaba cualquier cosa*,
justo lo contrario de lo que dice la arquitectura.

| Lo que se pudo hacer | Dónde faltaba la guarda |
|---|---|
| Revisar un borrador que su autor no había entregado | `web.services.registrar_revision` |
| Revisar dos veces la misma entrega (doble envío) | ídem, y `IntegrityError` salía como 500 |
| Revisar una versión ya reemplazada | ídem |
| **Revisarse a uno mismo** su propia entrega | `selectors.puede_revisar` |
| Abrir una segunda entrega con una ya en revisión | `web.services.crear_entrega` |
| Crear una cuenta de cobro con **mes 0, 13 o -1** | `cuentas_de_cobro.services.crear_cuenta` |

El del mes merece una nota: `CuentaEntrega.mes` **sí** declara `choices`, pero el
modelo no llama a `full_clean()` en su `save()`, y las `choices` de Django son
validación de formulario, no restricción de base. El formulario lo impedía; el
servicio no.

El de la autorrevisión es alcanzable sin hacer nada raro: basta con que quien
ejecuta la actividad sea también el director del proyecto, y entonces
`responsable_revision` lo devuelve a él mismo. La guarda se puso **por encima del
atajo de superusuario**: separar quien hace de quien aprueba es la razón de ser
de ese paso.

**Lo que aguantó sin un rasguño:**

- **Referencia directa a objetos** (cambiar el id en la URL): siete intentos de
  ver, descargar, borrar, subir, entregar y revisar cosas de otro proyecto, todos
  rechazados. Los selectores hacen su trabajo.
- El gating secuencial de cuentas de cobro (nadie se adelanta al técnico), el
  rechazo terminal en radicación, la coherencia entre documentos y decisión, el
  tope de una cuenta por periodo, y no entregar dos veces.
- Saneado de nombres de archivo: ni `../../etc/passwd.pdf` ni `/abs/ruta.pdf` ni
  rutas con contrabarra escapan del prefijo; `factura.pdf.exe` se rechaza.
- `ExtensionArchivo` no admite `tar.gz`, separadores de ruta, vacíos, ni
  duplicados por diferencia de mayúsculas.

**Suite: 274 pruebas** (eran 229).

#### Lo que sigue SIN cubrir, dicho sin adornos

- ⚠️ **La concurrencia no está probada.** Se añadió `select_for_update` en
  `crear_entrega`, `realizar_entrega` y ya estaba en cuentas de cobro, pero
  **SQLite no implementa el bloqueo de filas** (`has_select_for_update = False`),
  así que en las pruebas la cláusula se omite en silencio. Los bloqueos solo
  actúan en Postgres, es decir en producción, y ahí **nadie los ha verificado**.
  Probarlo de verdad exige dos conexiones reales contra Postgres.
- ~~**Escribir el modelo directamente salta todas las guardas**~~: **resuelto en
  parte el 2026-10-07**, a petición del usuario, y conviene entender hasta dónde.

  Se añadieron **dos capas por debajo de los servicios**, sin tocar los
  `models.py` definitivos:

  1. **Señal `pre_save` → `full_clean()`** en `contenido/apps.py` y
     `cuentas_de_cobro/apps.py`, para `Actividades` y `CuentaEntrega`. Los dos
     declaraban un `clean()` con un invariante real y **ninguno sobrescribe
     `save()`**, así que esas reglas solo corrían vía formulario: estaban
     escritas y apagadas. Cubre todo lo que pase por el ORM; no cubre
     `bulk_create`, `queryset.update()` ni el SQL crudo.
  2. **Restricciones `CHECK` en la base** por migración, solo para lo
     incondicional: `actividad_fechas_coherentes`, `entrega_version_positiva` y
     `cuenta_mes_valido`. Con `SeparateDatabaseAndState`, de modo que el estado
     de migraciones no las registra y `makemigrations --check` no detecta
     divergencia (comprobado). **Comprobado también que rechazan un UPDATE de
     SQL crudo**, que es justo lo que las otras dos capas no pueden parar.

  **Lo que NO se bajó, y es deliberado:** las reglas de flujo (una sola entrega
  abierta, nadie revisa lo suyo, el orden de los revisores, la coherencia entre
  documentos y decisión) dependen de otras tablas y de quién actúa. Un `CHECK`
  solo ve su propia fila y un modelo no sabe quién está guardando: no caben ahí,
  y forzarlas sería empeorarlas. Se quedan en los servicios, que es su sitio.

  Medición previa al cambio: encender `full_clean()` en cada guardado **no rompió
  nada del producto**; los únicos 6 fallos fueron de un andamiaje de prueba que
  creaba actividades con el vencimiento anterior a la fecha programada, es decir
  la validación atrapando datos imposibles que la propia suite generaba.

  Sigue en pie que una operación en lote o el SQL crudo esquivan las capas 1 y 2;
  para eso está la 3, limitada a lo que se puede expresar en una sola fila.
- **Reenvío del mismo token de subida** (`confirmar_subida`): el token va firmado
  pero no se marca como usado, así que reenviarlo crearía una segunda fila
  apuntando al mismo archivo del bucket. No se pudo probar porque el paso exige
  `head_object` contra el bucket real y las pruebas usan almacenamiento en
  memoria. Riesgo bajo (duplica una fila, no filtra nada), pero está abierto.
- No hay pruebas de propiedades ni *fuzzing*; las entradas probadas son las que
  se eligieron a mano.

### Dos huecos cerrados el 2026-10-07 (reportados por el usuario)

19. **El admin subía archivos sin validar.** Preguntando si el modelo de
    extensiones regía en todos los campos de carga, resultó que no: los cuatro
    puntos de la aplicación sí (formulario con `ArchivoValidadoMixin` y los dos
    endpoints de firma con `validar_extension`), pero el **Django Admin no**.
    Sus formularios se generan solos y no heredaban nada, así que desde ahí se
    podía subir un ejecutable o un archivo de 5 GB, saltándose la lista de
    extensiones y el tope de tamaño. Eran seis puntos: `DocumentosAdmin`,
    `TramiteFinalAdmin` y cuatro inlines.

    Resuelto con `ArchivoValidadoAdminMixin` y `ArchivoValidadoInlineMixin`
    (`cuentas/validadores.py`), que envuelven el formulario que el admin
    construye. La prueba `ExtensionesEnTodoPuntoDeCargaTest` **recorre
    `admin.site._registry`** y falla si aparece cualquier modelo o inline con
    `FileField` sin la validación: registrar algo nuevo no puede reabrir el
    hueco en silencio.

20. **Las páginas se quedaban guardadas en el navegador.** Reportado como "el
    proyecto 14 tiene 4 actividades pero la tarjeta dice 1". Se comprobó en
    producción, en solo lectura, que **el dato y el código eran correctos**: las
    4 actividades existían, el conteo daba 4 para los cinco usuarios implicados
    (con filtro y sin él), y la página renderizada daba 4. El dato que lo cerró:
    **ningún proyecto de la base tenía 1 actividad** en ese momento, así que
    ninguna tarjeta podía mostrar ese número. Era una página de media hora
    antes, de cuando solo existía la primera de las cuatro.

    La causa real: las respuestas salían **solo con `Vary: Cookie`, sin
    `Cache-Control`**. Sin instrucción explícita el navegador decide por su
    cuenta cuánto considerarlas frescas, y con el botón "atrás" usa su copia sin
    preguntar. Resuelto con `cuentas.middleware.SinCacheDeNavegador`, **después
    de WhiteNoise** para que los estáticos conserven su caché larga, y
    respetando la cabecera que una vista haya puesto a propósito.

    Cierra además dos problemas peores que no se habían notado: tras cerrar
    sesión, el botón "atrás" podía devolver pantallas con datos del usuario
    anterior en un equipo compartido; y una página de formulario servida desde
    la caché enviaba un token CSRF viejo, que responde 403 sin explicar nada.

    Lección de método: el usuario reportó un número mal y la hipótesis natural
    (el conteo está mal calculado) era falsa. Escribí la prueba que debía
    demostrarla y **pasó**, lo cual evitó "arreglar" código que funcionaba. El
    fallo estaba una capa más arriba, en cómo se entrega la página.

**Suite: 284 pruebas.**

### Borrado de datos de prueba (2026-10-07)

21. **No se podían retirar las pruebas hechas en producción.** Dos causas
    distintas que conviene no confundir:

    - **El modelo protege a propósito**: nueve claves `PROTECT` hacia `User` y
      `RevisionCuentaCobro.asignacion` hacia la asignación. Una cuenta de cobro
      dice quién la presentó y quién la aprobó; ese nombre no debería poder
      evaporarse. Resuelto con `cuentas/borrado.py`, explícito y acotado:
      `eliminar_cuenta_con_rastro` y `eliminar_usuario_con_rastro`, expuestos
      como acciones del admin **solo para superusuario** y con una pantalla de
      confirmación que enumera lo que se va a llevar por delante. Para un
      usuario que ya trabajó, la acción sana es **desactivarlo**, que está al
      lado.
    - **Los archivos se quedaban en el bucket**: borrar un proyecto sí
      funcionaba, pero sus documentos seguían ahí, ocupando sitio y accesibles
      con una URL firmada. Eso no era protección, era una fuga. Resuelto con un
      receptor de `post_delete` que retira los `FileField` de la fila borrada en
      `transaction.on_commit`, cascadas incluidas.

    **Ejecutado en producción el 2026-10-07**, a petición del usuario: se
    borraron las **4 cuentas de cobro de prueba** (pks 9, 10, 11 y 12, de
    `ContratistaPrueba`, `MichelleguarinCT` y `Contratista`) con sus 66 archivos
    asociados. Antes se verificó el inventario y se probó el borrado en una base
    desechable. Los catálogos (1 vigencia, 7 tipos de documento, 6 requisitos)
    quedaron intactos, que es lo que no había que tocar.

22. **Limpieza de archivos huérfanos del bucket (2026-10-07).** Tras el borrado
    quedaban restos anteriores al arreglo: subidas abandonadas (se firma el
    permiso, el navegador sube y nadie confirma) y documentos borrados desde la
    interfaz cuando el archivo todavía no se iba con la fila.

    Se barrió el bucket entero desde la raíz y se contrastó contra **todos** los
    campos de archivo del proyecto, recorriendo `apps.get_models()` en vez de
    fiarse de los cuatro conocidos; se comprobó además que ningún nombre
    apareciera en código ni plantillas (todas las URLs de media salen de campos
    de la base). De 89 archivos, **10 referenciados y 79 huérfanos**, borrados.

    Verificación posterior: 10 archivos en el bucket, 10 referenciados,
    **0 referenciados faltantes**. La lista de lo borrado quedó registrada.

    Entre los restos había archivos en la **raíz** del bucket, sin prefijo de
    carpeta: vienen de cuando las pruebas escribían contra el almacenamiento de
    producción, el problema que resolvió `proyectos/test_settings.py`. Uno
    (`x.pdf`) lo generó esta misma sesión al probar el borrado en una base
    desechable: la base era aparte, pero el `.env` seguía apuntando al bucket
    real. **Aislar la base no aísla el almacenamiento**, y es un tropiezo fácil
    de repetir.

### Regla de negocio corregida (reportada por el usuario al probar la app)

La validación de "una cuenta por vigencia y mes" solo miraba si la cuenta
**existía**. Con una cuenta de septiembre rechazada, el contratista no podía
volver a presentarla. Ahora decide el **estado**: una rechazada no bloquea, una
aprobada sí, y una en trámite también (dos abiertas del mismo periodo dejarían el
flujo sin responsable claro). Ver la regla en `ModuloCuentasCobro.md` y las
pruebas en `test_guardas.py`.

### Abierto (detectado en la misma revisión)

- ~~**`staticfiles/` desincronizado en la copia local**~~: resuelto el
  2026-10-06 corriendo `collectstatic`. El manifiesto no tenía
  `web/tailwind.css` y, como `base.html` lo referencia, *toda* página reventaba
  con `DEBUG=False`, incluidas `403.html` y `500.html` (el error se encadenaba y
  no se podía ni renderizar la página de error). Verificado: ahora el login
  responde 200 con el storage de manifiesto.
- ~~**La bitácora de trazabilidad es editable en `/admin/`**~~, resuelto:
  `EventoTrazabilidadAdmin` es de solo lectura (sin añadir, cambiar ni borrar), al
  igual que su inline. Es el registro de auditoría del flujo.
- ~~**`CuentaEntregaAdmin.readonly_fields` omite `fecha_aprobacion_supervisor`**~~: resuelto, ya protege las cuatro fechas.
- ~~**`README.md` desactualizado**~~: resuelto: decía Tailwind "(CDN)" y daba el
  orden de revisión como *jurídico → administrativo → técnico* (el correcto es
  **técnico → jurídico → administrativo**).
- ~~**El orden de revisión invertido en tres documentos más**~~: resuelto el
  2026-10-06. El mismo error estaba en `ESTRUCTURA.md`, `ModuloCuentasCobro.md`
  (§10) y en **cuatro** sitios de `ManualRoles.md`, que es el que leen las personas
  de la entidad. Corregido en todos. Donde se enumeran los tres roles sin indicar
  secuencia se dejó como estaba, porque ahí el orden no afirma nada.
- **`main.py`** en la raíz: la plantilla de `uv init`, no la usa nadie. Añadido al
  `.gitignore`, pero **si ya está versionado hay que borrarlo**: ignorar un
  archivo rastreado no lo saca del repositorio.
- Conviven **dos manifiestos de dependencias** (`requirements.txt` pineado y
  `pyproject.toml` con rangos). Se regeneró el primero para que coincidan, pero
  sigue sin estar escrito cuál instala producción: ver el punto del despliegue en
  el Pendiente 4.

### `.gitignore` (2026-10-06) y el riesgo del `.env`

**No existía ningún `.gitignore` en el proyecto.** Se creó uno en la raíz, junto
con un `.env.example` que documenta las 13 variables sin valores reales.

⚠️ **Pendiente de verificar en el repositorio remoto: si `proyectos/.env` está
versionado, las credenciales están en el historial de git.** Contiene la cadena
de conexión de Supabase (con contraseña) y la `SECRET_KEY`. Añadirlo al
`.gitignore` **no** lo quita de la historia: habría que sacarlo del índice
(`git rm --cached`), **rotar la contraseña de la base y la `SECRET_KEY`**, y
considerar que el historial sigue conteniéndolas. Comprobarlo con
`git ls-files proyectos/.env` desde un clon del remoto (esta copia de trabajo no
tiene `.git`).

`proyectos/staticfiles/` se dejó **sin ignorar a propósito**: es salida de
`collectstatic`, pero si el build de Railway no lo corre, esa carpeta tiene que
viajar en el repo. Queda atado al punto del despliegue.

### Mediciones del Pendiente 1 (reproducidas y luego resueltas)

Punto de partida: bandeja con 10 filas, **161 consultas**; detalle de una cuenta,
**111**; y un dashboard de formulador **sin un solo dato**, 39 consultas, **23 de
ellas a `auth_group`**. Hoy quedan en **23 / 35 / 17**; ver el Pendiente 1, arriba,
para cómo se midieron y qué se cambió.

---

## Cosas que cuesta volver a descubrir

- El `DATABASE_URL` debe ser el **session pooler**
  (`aws-0-us-east-1.pooler.supabase.com:5432`, usuario `postgres.<ref>`). El host
  directo `db.<ref>.supabase.co` resuelve **solo a IPv6**; el transaction pooler
  (`:6543`) cuelga operaciones largas como `dumpdata`.
- Un proyecto de Supabase **pausado** se reconoce porque el endpoint de storage
  devuelve **HTTP 540** y el pooler responde `tenant/user not found`.
- `load_dotenv()` se llama sin ruta: buscar desde el directorio actual. Los
  comandos hay que correrlos desde `proyectos/`, no desde la raíz del repositorio.
- Borrar una `CuentaEntrega` por el ORM falla (`RevisionCuentaCobro.asignacion`
  es `PROTECT`); usar `TRUNCATE cuentas_de_cobro_cuentaentrega CASCADE`.

### Trampas de Alpine (costaron tres intentos fallidos el 2026-10-06)

El botón de "Realizar cargue" no aparecía, y las tres explicaciones obvias eran
falsas. Las tres causas reales:

- **Un script que registra un componente de Alpine tiene que cargar ANTES que
  Alpine.** El bundle termina en `queueMicrotask(() => Alpine.start())`, así que
  arranca y dispara `alpine:init` antes de que corra el siguiente script diferido.
  Cargado después, el componente no se registra nunca, `x-data` lanza un error y
  todo lo que esté tras un `x-show` en ese subárbol se oculta. Por eso el botón
  "se veía un instante y desaparecía". En `base.html` el orden es: HTMX →
  `subidas.js` → Alpine.
- **Corolario para cualquier `x-show`:** una expresión que falla evalúa a
  `undefined`, que Alpine trata como falso y **esconde** el elemento. Nunca poner
  un `x-show` sobre algo cuya desaparición deje al usuario sin salida (la etiqueta
  de un botón, por ejemplo): que falle visible.
- **Dentro de un `@click`, `$el` es el elemento que disparó el evento**, no la raíz
  del componente. Hay que usar **`$root`**. Con `$el`, el componente buscaba el
  token CSRF dentro del `<button>`, no lo encontraba, y toda subida respondía
  `403 Forbidden (CSRF token missing)`.

### Método: los cambios de interfaz se verifican en el navegador

Los tres intentos anteriores se reportaron como resueltos habiendo comprobado
**solo el HTML generado**. El HTML estaba bien las tres veces: el fallo estaba en
el momento en que Alpine arranca, que no se ve en el marcado. Un cambio de interfaz
no está verificado hasta haberlo mirado en Chrome.

Para una comprobación que necesite un estado difícil de alcanzar (una cuenta ya
cerrada, un trámite respondido), lo práctico es renderizar la página con el cliente
de pruebas de Django y `force_login`, que no pide contraseña:

```python
from django.test import Client
c = Client(); c.force_login(usuario)
html = c.get("/cuentas-cobro/9/").content.decode()
```

Eso confirma el marcado y los permisos; el comportamiento de Alpine, no. Para eso
hace falta el navegador.
