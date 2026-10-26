# Registro de cambios

Qué cambió, **respecto a qué** y por qué. Cada entrada contrasta el
comportamiento anterior con el nuevo, porque lo que cuesta recordar meses
después no es qué se añadió sino qué hacía antes y por qué dejó de valer.

Las decisiones permanentes viven en `CLAUDE.md` y en `docs/`; aquí queda la
historia. La bitácora de trabajo con el detalle técnico está en
`docs/Pendientes.md`.

---

## Sin publicar

Lo que hay en la copia de trabajo y todavía no está en `main`. El último commit
subido es `4fdbc8a` (2026-10-07).

---

### 2026-10-26

Revisión de seguridad de los cambios de ayer frente a los datos de producción, y
barrido adversarial del flujo completo. Suite: de 354 a 403 pruebas.

#### Producción no se ve afectada

| Qué se comprobó | Resultado |
|---|---|
| Las restricciones nuevas contra las filas que ya existen | Las 79 actividades y las 13 cuentas pasan `full_clean()`; ninguna viola un `CHECK` |
| La copia completa (1012 filas) cargada en una base con **todas** las migraciones | Entra limpia |
| Las 171 páginas de la aplicación, para los 70 usuarios activos con rol, sobre esa copia | **11.970 peticiones, ningún 5xx** |
| Lo que el despliegue aplica de verdad | Solo `cuentas/0003_adjuntoderevision`: una tabla nueva. Nada existente se toca, y `makemigrations --check` no detecta deriva |

#### Tres fallos reales encontrados, todos con 500 a la vista

| Síntoma | Causa | Arreglo |
|---|---|---|
| Pulsar **Guardar** en el formulario de actividad con las fechas en blanco daba un **500** | `Actividades.clean()` compara las dos fechas sin comprobar que existan, y Django llama al `clean()` del modelo **aunque el formulario ya tenga errores de campo** | `cuentas/formularios.py`: la validación del modelo se salta mientras el formulario tenga errores propios. `models.py` es definitivo, así que la guarda vive en el formulario |
| Lo mismo en el admin de **entregas** y de **revisiones**, por otra puerta | Sus `clean()` leen una clave ajena (`self.actividad`, `self.actividad_entrega`): en blanco no es un campo vacío, es `RelatedObjectDoesNotExist` | La misma guarda, llevada a los formularios que el admin genera solo (`ModeloValidadoAdminMixin` / `...InlineMixin`) |
| El supervisor podía **decidir dos veces** | `revisar_supervisor` (modelo, definitivo) sobreescribe estado y fecha sin mirar si ya había decisión | Guarda en `decidir_supervisor`. Lo grave no era el doble clic: era poder **rechazar** más tarde una cuenta con el cierre ya cargado y un trámite respondido |

Una prueba recorre `admin.site._registry` y falla si un admin sobre un modelo con
`clean()` propio se queda sin la guarda, igual que la de extensiones hace con los
archivos: el hueco no se reabre en silencio.

#### Un cuarto, menor: 404 donde tocaba el login

Abrir sin sesión "Nueva actividad" o "Nueva entrega" devolvía **404** en vez de
llevar al login: las dos vistas resolvían el objeto en `dispatch` **antes** de
que corriera la comprobación de sesión, y para un anónimo los selectores no
devuelven nada. No se ejecutaba ni se filtraba nada; el mensaje era el
equivocado.

#### Lo que el barrido sí aguantó

`web/test_acceso.py` ataca las **44 rutas de acción** de los dos dominios, en
50 entradas (los cuatro destinos de subida comparten vista, pero cada uno tiene
su propio permiso, así que los cuatro entran):

- **Once roles contra todas las rutas**, ejecutando de verdad, de modo que unas
  van cambiando el estado bajo las siguientes.
- **Sin sesión**: todo redirige al login, ni un 200.
- **Aislamiento entre dominios**: un contratista no entra en nada de Proyectos y
  un formulador en nada de cuentas de cobro.
- **`Consulta` no ejecuta ninguna acción**, y tampoco realiza la entrega de otro
  (ahí no hay mixin de rol: quien lo impide es el servicio).
- **Dobles clics** en entregar, radicar, asignar, revisar, decidir, los dos
  trámites finales, realizar entrega, abrir versión, revisar y eliminar: ninguno
  duplica el hecho, y la cuenta cerrada no admite nada más.
- **Ids basura**: inexistentes, 0, 2^31, texto, y el de un documento **de otra
  cuenta** o un soporte de otra entrega.
- **Sesiones mal cerradas**: POST tras cerrar sesión, cambio de usuario en el
  mismo navegador, usuario desactivado, rol retirado a mitad de sesión.
- **Sin token CSRF** no se ejecuta ningún POST.

Y una prueba de cobertura compara la matriz con las `urlpatterns` de los dos
dominios: una ruta nueva que no se añada al barrido hace fallar la suite.

---

### 2026-10-25

Cuatro ajustes pedidos sobre el módulo de cuentas de cobro y el de proyectos.
Los tres primeros apuntaban al mismo hueco: **una devolución no dejaba ver por
qué**. Suite: de 304 a 347 pruebas.

#### 1. El contratista ya puede ver las observaciones de cada versión

| Antes | Ahora |
|---|---|
| El detalle solo mostraba la versión vigente. Al devolver una cuenta, la causal documento a documento quedaba escrita en la versión anterior y **dejaba de verse**. El contratista leía el comentario general en la línea de tiempo, pero no sabía **cuál** documento estaba mal ni por qué. | Tarjeta **Versiones anteriores**, plegada por omisión, con cada versión, sus documentos (estado y causal), sus revisiones (rol, resultado, comentario, autor) y los soportes que se adjuntaron. Solo consulta: abrir y descargar, ninguna acción. |

Llega plegada a propósito: una cuenta con varias devoluciones acumula versiones
y desplegadas empujan el resto de la pantalla fuera de la vista. El chevron a la
izquierda gira al abrir.

#### 2. La devolución ya no obliga a recargarlo todo

| Antes | Ahora |
|---|---|
| **Reinicio total.** La versión nueva nacía **vacía** y el contratista volvía a subir el paquete completo, incluso los documentos que nadie había objetado. | `nueva_version` **arrastra** los documentos en `AP`/`NA` con su estado. Solo quedan por recargar los rechazados. |

⚠️ **Esto invierte una regla que estaba documentada como definitiva.** Lo que no
se arrastra son las **revisiones**: los tres roles vuelven a tener turno desde el
técnico, decisión tomada entre tres opciones. Como lo arrastrado ya viene
aprobado, cada revisor solo mira lo recargado, y nadie firma una versión que no
vio entera. Corregido en `CLAUDE.md`, `ModuloCuentasCobro.md` y `ESTRUCTURA.md`.

Tres cosas cayeron solas (`documentos_faltantes`, el desplegable de carga y
`puede_entregar`, todas derivadas de "catálogo menos lo cargado") y dos no:

- **Un documento aprobado no se quita**, ni uno de una versión anterior. Lo
  segundo lo destapó una prueba: el contratista **podía borrar documentos de
  versiones pasadas**, es decir la prueba de lo que le objetaron.
- **La copia reutiliza la clave del bucket**, así que dos filas comparten
  archivo. `cuentas/borrado.py` cuenta referencias antes de retirarlo. Sin eso,
  borrar una fila dejaba a la otra apuntando a la nada, que es peor que un
  huérfano: el huérfano solo ocupa sitio, la fila rota rompe la descarga y nadie
  se entera hasta que alguien la pulsa.

#### 3 y 4. Quien revisa puede adjuntar documentos

| Antes | Ahora |
|---|---|
| El revisor solo podía escribir un comentario. No tenía dónde dejar el documento que lo sustenta. | Puede adjuntar soportes, **opcionalmente**, apruebe o devuelva, en los **dos dominios**. Los ve y descarga todo el que ve la cuenta o la entrega, **incluido quien entregó**: son observaciones dirigidas a él. |

Modelo nuevo `cuentas.models.AdjuntoDeRevision` (relación genérica; vive en
`cuentas` porque los dos `models.py` son definitivos). Migración
`cuentas/0003_adjuntoderevision.py`.

La decisión que lo hace funcionar: **cuelga del objeto revisado, no de la
revisión**. Cuando el revisor está escribiendo el formulario la revisión aún no
existe, así que no habría a qué atar el archivo.

#### Correcciones del mismo día, reportadas al probarlo

| Síntoma | Causa | Arreglo |
|---|---|---|
| "Después que revisé me siguió apareciendo el formulario de cargue" | Lo permití a propósito ("por si quiere dejar constancia"), y era **incoherente**: ya no se podía *quitar* un soporte tras revisar, pero sí seguir añadiendo | El cargue se cierra al registrar la revisión, **por rol**: otro revisor que aún no decidió sigue pudiendo |
| El cargue se reabría al devolver, y el soporte adjuntado desaparecía | Al devolver nace la versión siguiente **en el acto**; la pantalla mira la vigente, que ya es otra | El cargue exige además que la versión esté **entregada**; el historial muestra los soportes **de cada versión** |
| Al contratista le salía el icono de eliminar sobre un documento aprobado, y un **403** sin explicación | El icono se decidía con una sola bandera para toda la tabla | Se decide **por documento**, con el mismo selector que manda. Una prueba verifica que lo ofrecido es exactamente lo permitido |

---

### 2026-10-08

Revisión de los paneles tras el cambio a la entrega en dos tiempos. Ninguno de
estos fallos daba error: los números salían, solo que contaban otra cosa.

| Panel | Antes | Ahora |
|---|---|---|
| Pendientes de mi revisión (coordinador) | La antigüedad corría desde que se **abría el borrador**, así que una entrega recién recibida podía aparecer "atrasada 7 días" | Cuenta desde que se **entregó** |
| Backlog por coordinador (director) | Contaba borradores como carga pendiente del coordinador | Solo lo entregado |
| "Sin ninguna entrega" (formulador) | Abrir un borrador sacaba la actividad de su lista de tareas, sin haber entregado nada | Pasa a **"Pendientes de entregar"**: las que están en Pendiente |
| "Tu última entrega fue hace N días" | Contaba el último borrador abierto | Cuenta la última entrega **realizada** |
| Tiempo prom. de revisión | Incluía revisiones hechas **por otros** sobre el trabajo del propio usuario, y medía desde la apertura del borrador | Solo **sus** revisiones, desde el envío |

---

## Publicado

### 2026-10-07 · `4fdbc8a`

Entrega en dos tiempos, extensiones administrables y reglas en el servicio.

- **La entrega tiene un momento de cierre.** Abrir la versión y pulsar "Realizar
  entrega" pasan a ser dos actos. Antes crear la versión mandaba la actividad a
  revisión en el acto, así que se podían abrir varias a la vez y seguir cambiando
  documentos de una que el revisor ya tenía delante: la base de producción tenía
  una actividad con **tres entregas sin revisar simultáneas**.
- **La revisión tiene dos salidas.** "Rechazada" hacía lo mismo que "Requiere
  ajustes" y confundía.
- **Entregar detiene el reloj del vencimiento.**
- **Las reglas viven en el servicio, no en la vista.** Un sondeo que llama a los
  servicios saltándose las vistas destapó seis agujeros, entre ellos revisarse a
  uno mismo y crear una cuenta con mes 0, 13 o -1.
- **Extensiones de archivo administrables** desde el panel, y la validación llega
  también al admin, que dejaba subir un ejecutable.
- **Borrado de datos de prueba** con acciones de superusuario, y los archivos se
  van con la fila.
- **Las páginas ya no se quedan guardadas en el navegador.**
- **`migrate --noinput` entra en el arranque** de Railway.

### 2026-10-06 · `ae7b3f8`

Subida directa al bucket, confirmación antes de enviar y N+1 resuelto. Ver el
mensaje del commit, que es extenso a propósito, y `docs/Pendientes.md`.
