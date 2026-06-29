# Construcción del módulo "Gestión de cuentas de cobro"

## Contexto

Módulo nuevo dentro de un proyecto Django existente (una página ya en
funcionamiento). Gestiona el flujo de revisión y aprobación de cuentas de cobro
de contratistas: desde el cargue de documentos hasta el cierre final del
supervisor, pasando por radicación, asignación de revisores y revisión secuencial
por tres roles.

Los **modelos ya están definidos, revisados y son DEFINITIVOS** (archivo
`models.py` de esta app). Tu trabajo es construir la **lógica de flujo** sobre
ellos: servicios, vistas, formularios, URLs, plantillas, permisos, admin y tests.

## Restricciones duras (leer antes que nada)

1. **NO toques los modelos.** No agregues, renombres, elimines ni cambies campos,
   métodos, choices, constraints, `related_name` ni `on_delete`. No generes
   migraciones que alteren su estructura. Si durante la implementación crees que
   falta algo en el modelo, **detente y pregúntame**; no lo agregues por tu
   cuenta. Toda la lógica que no esté en el modelo va en servicios/vistas.
2. **Es un módulo aparte.** Se integra como una app más. No toques otras apps ni
   su lógica.
3. **Preserva el diseño actual.** Antes de escribir plantillas, inspecciona
   `base.html`, los estáticos, el sistema de estilos (Tailwind / CSS /
   componentes) y las convenciones de UI del proyecto. Las vistas heredan del
   layout existente y deben verse consistentes con el resto del sitio. NO
   introduzcas un framework de CSS nuevo ni rediseñes lo que ya existe.

## Inventario de modelos (úsalo tal cual, no lo re-deduzcas)

Estos son los modelos disponibles. Respeta nombres de campos, métodos y choices
exactamente.

- **`Vigencia`** — `vigencia` (int).
- **`TipoDocumentoCargue`** — `nombre`. Catálogo de tipos de documento.
- **`RequisitoDocumental`** — `vigencia`, `tipo_documento`, `obligatorio` (bool).
  Único por `(vigencia, tipo_documento)`. **Define qué tipos se exigen por
  vigencia.** Es la fuente de verdad para la validación de completitud.
- **`CuentaEntrega`** — `usuario`, `vigencia`, `mes`, `estado_revisores`,
  `estado_supervisor` (ambos choices `ResultadoRevision`: `AP`/`RE`, nullable),
  `fecha_radicacion`, `fecha_aprobacion_revisores`, `fecha_cierre`, `comentario`.
  Métodos:
  - `actualizar_fecha_radicacion()` → setea `fecha_radicacion` si la última
    `RevisionParaRadicacion` es `APROBADA`.
  - `actualizar_estado()` → si los tres roles aprobaron en la **última**
    `DocumentoEntrega`, setea `estado_revisores=AP` y `fecha_aprobacion_revisores`.
  - `revisar_supervisor(resultado, comentario="")` → exige `estado_revisores=AP`;
    setea `estado_supervisor` (AP o RE). NO exige comentario.
  - `cerrar()` → exige `estado_supervisor=AP`; setea `fecha_cierre`. NO valida
    presencia de documentos de cierre (eso es responsabilidad del servicio).
  - `clean()` → impide `estado_supervisor` no nulo si `estado_revisores != AP`.
- **`DocumentoEntrega`** — `cuenta_entrega`, `numero_version`, `usuario`,
  `comentario`. Único por `(cuenta_entrega, numero_version)`. `save()`
  autoincrementa `numero_version`. `clean()` **bloquea** crear entregas si
  `estado_supervisor == AP`. Es el **paquete versionado** de la entrega.
- **`DocumentosCuentaCobro`** — `documento_entrega`, `tipo_documento`,
  `documento` (FileField), `estado` (choices `EstadoDocumento`:
  `PE`/`AP`/`RE`/`NA`, default `PE`), `comentario`. Único por
  `(documento_entrega, tipo_documento)`. Es **cada archivo cargado por tipo**, con
  su propio estado de revisión.
- **`RevisionParaRadicacion`** — `cuenta_entrega`, `supervisor`, `comentario`,
  `resultado` (choices `AP`/`AJ`/`RE`). Veredicto del supervisor sobre la entrega
  para radicarla.
- **`AsignacionRevisor`** — `cuenta_entrega`, `supervisor`, `revisor`, `rol`
  (`JU`/`AD`/`TE`), `estado` (`AC`/`DE`), `motivo_declinacion`. Índice único
  parcial: **una sola asignación `AC` por `(cuenta_entrega, rol)`**. Métodos:
  - `declinar(motivo)` → pasa la asignación a `DE` (libera el slot).
  - `reasignar_revisor(cuenta_entrega, rol, nuevo_revisor, supervisor)`
    (`@staticmethod`) → crea una nueva `AC` para ese rol si no hay otra activa.
- **`RevisionCuentaCobro`** — `documento_entrega`, `asignacion` (FK a
  `AsignacionRevisor`), `rol`, `comentario`, `resultado` (`AP`/`AJ`/`RE`). Único
  por `(documento_entrega, rol)`. `clean()` valida que `rol` coincida con
  `asignacion.rol`, que la asignación esté `AC`, y que la cuenta no esté aprobada.
  Es **el veredicto de un rol sobre una entrega**.
- **`DocumentoCierre`** — `cuenta_entrega`, `tipo_documento` (**FK a
  `TipoDocumentoCargue`**: los mismos tipos del catálogo de la entrega, ahora
  **firmados**), `documento` (FileField), `usuario`. Único por
  `(cuenta_entrega, tipo_documento)`. `clean()` **exige** `estado_supervisor == AP`
  (guarda inversa a `DocumentoEntrega`). Lo carga el **rol de radicación**, no se
  versiona.
- **`TramiteFinal`** — `cuenta_entrega` (related_name `tramites_finales`), `tipo`
  (`SF` Cargue en SIIFWEB / `SC` Cargue en SECOP II), `usuario`, `realizado` (bool),
  `evidencia` (FileField, opcional), `comentario`. Único por `(cuenta_entrega, tipo)`.
  `clean()` + `CheckConstraint` **impiden evidencia sin `realizado=True`**, y
  `clean()` exige evidencia al marcar realizado. Los dos pasos finales del flujo.
- **`EventoTrazabilidad`** — `cuenta_entrega` (related_name `eventos`), `actor`,
  `etapa` (`RAD`/`ASI`/`REV`/`SUP`/`CIE`), `evento` (str corto), `detalle`.
  Bitácora. **Lo escribes desde servicios en cada transición.**

## Antes de empezar (obligatorio)

Explora el proyecto local y reporta lo que encuentres ANTES de generar código:
- Estructura de apps y dónde encaja este módulo.
- `settings.py`: apps instaladas, `AUTH_USER_MODEL`, `MEDIA_ROOT`/`MEDIA_URL`,
  storage de archivos, paquetes relevantes (HTMX, Alpine, etc.).
- Convenciones de plantillas y estáticos (layout base, bloques, estilos).
- Cómo se manejan permisos/roles (grupos de Django, decoradores, mixins propios).
- El `models.py` de este módulo, para confirmar el inventario de arriba.

Con eso, **propón un plan de archivos y espera mi confirmación antes de
implementar.**

## Actores y roles

- **Contratista** (`CuentaEntrega.usuario`): crea la cuenta, carga documentos,
  pulsa "Entregar" y recarga el paquete completo tras devoluciones. **No** carga los
  documentos de cierre.
- **Rol de radicación:** aprueba la radicación (igual que el supervisor) y, tras la
  aprobación del supervisor, **carga los documentos de cierre firmados** (los mismos
  tipos del catálogo que la entrega, ahora firmados).
- **Supervisor:** aprueba la radicación, asigna y reasigna revisores, y emite la
  decisión final (aprobación **para firma de los documentos de cierre**, o rechazo;
  al aprobar setea `fecha_aprobacion_supervisor`). **NO carga documentos**.
- **Revisores** (jurídico, administrativo, técnico): cada uno revisa en su rol y
  marca documentos; puede declinar. El **revisor administrativo** además responde el
  trámite final 8.1 ("¿Se cargó a SIIFWEB?").
- **Rol de secop:** responde el trámite final 8.2 ("¿Se cargó a SECOP II?").
- **Sistema**: valida completitud, registra radicación, crea versiones
  automáticamente, escribe trazabilidad y cierra el trámite.

Implementa el control de acceso con el mecanismo de roles que ya use el proyecto
(grupos de Django si aplica). Roles nuevos a contemplar: **radicación** y **secop**.
Cada acción restringida a su actor.

## Flujo completo a implementar (paso a paso, con métodos exactos)

### 1. Cargue y radicación
1. El contratista crea una `CuentaEntrega` (vigencia + mes) y un `DocumentoEntrega`
   (versión 1, autoincrementada por el `save()` del modelo — **no la calcules
   tú**).
2. Carga sus archivos como `DocumentosCuentaCobro` (uno por `tipo_documento`,
   estado inicial `PE`).
3. El contratista pulsa **"Entregar"** para enviar a revisión. Esta acción:
   - Valida completitud: toma los `RequisitoDocumental` con `obligatorio=True` de la
     vigencia de la cuenta (conjunto de `tipo_documento` exigidos) y los compara con
     los presentes en la **última** `DocumentoEntrega`. Si falta alguno, muestra
     alerta de documentos faltantes y NO avanza.
   - Si está completa, registra `EventoTrazabilidad(etapa=RAD)` "Documentos enviados
     a revisión" y pone la cuenta a disposición de la radicación.
   - Es también la acción que materializa una nueva versión tras una devolución (ver
     §3): el contratista nunca crea versiones a mano.
4. Aprobación para radicación (ver "Rol de radicación" en Actores): un usuario con
   rol de radicación **o** el supervisor emite `RevisionParaRadicacion` con
   `resultado`:
   - **`AP` (Aprobado)** → llama a `cuenta.actualizar_fecha_radicacion()`. Avanza
     a asignación de revisores. Registra `EventoTrazabilidad(etapa=RAD)`.
   - **`AJ` (Requiere ajustes)** → devuelve al contratista. El sistema habilita
     automáticamente una nueva versión de `DocumentoEntrega` (vacía); el contratista
     vuelve a cargar el **paquete completo** y pulsa **"Entregar"**. No hay creación
     manual de versiones.
   - **`RE` (Rechazado)** → para el caso "documento que no aplica", marca el
     `DocumentosCuentaCobro` correspondiente con `estado=NA` o `RE` y su
     `comentario` (causal). Si los demás documentos obligatorios sí cumplen, la
     entrega puede darse por cumplida; un documento `NA` no bloquea la completitud
     (un `NA` no cuenta como faltante de un obligatorio que sí está).

### 2. Asignación de revisores
1. Solo habilitada si `cuenta.fecha_radicacion` no es nulo.
2. El supervisor crea **una `AsignacionRevisor` activa por cada rol** (`JU`, `AD`,
   `TE`). El índice parcial garantiza una sola activa por rol; respétalo (no crees
   dos activas del mismo rol).
3. Registra `EventoTrazabilidad(etapa=ASI)`.

**Declinación / reasignación:**
- Un revisor que no puede revisar llama a `asignacion.declinar(motivo)` → pasa a
  `DE` y libera el slot.
- El supervisor llama a `AsignacionRevisor.reasignar_revisor(cuenta, rol,
  nuevo_revisor, supervisor)` para crear otra activa del mismo rol. Si ya hay una
  activa, el método lanza error (es lo esperado: primero se declina).

### 3. Revisión secuencial (GATING — lo central del módulo)
- Orden estricto: **jurídico → administrativo → técnico**. Este orden **NO está en
  los modelos; lo implementas en servicios.**
- Reglas de habilitación:
  - `AD` no se habilita hasta que exista una `RevisionCuentaCobro` con `rol=JU` y
    `resultado=AP` sobre la última `DocumentoEntrega`.
  - `TE` no se habilita hasta que exista la de `rol=AD` con `resultado=AP` sobre la
    misma entrega.
- Cada revisor emite `RevisionCuentaCobro` (apuntando a su `AsignacionRevisor`
  activa, sobre la **última** `DocumentoEntrega`):
  - **`AP`** → habilita el siguiente rol. Registra `EventoTrazabilidad(etapa=REV)`.
    **Para aprobar, ningún documento de la entrega puede estar `PENDIENTE` ni
    `RECHAZADO`** (todos en `AP`/`NA`): lo impone `RevisionCuentaCobro.clean()`, así
    que la UI debe guiar al revisor a resolver el estado de cada documento (AP/NA/RE)
    antes de habilitar "Aprobar".
  - **`AJ` / `RE`** → devuelve al contratista. El revisor marca **por documento**
    cuáles se observan: pone `DocumentosCuentaCobro.estado = RE` (o `NA`) con su
    `comentario`/causal en los que no cumplen, y deja en `AP` los que sí.
- Tras cada `AP`, llama a `cuenta.actualizar_estado()`: cuando los **tres roles**
  estén `AP` en la última entrega, marcará `estado_revisores=AP` y
  `fecha_aprobacion_revisores`.

**Reinicio TOTAL del ciclo (decisión definitiva del equipo):** ante cualquier
devolución (`AJ`/`RE`) de cualquier rol, el flujo **reinicia completo desde el
revisor jurídico**. NO hay carry-forward: la nueva versión de `DocumentoEntrega`
nace **vacía** y el contratista vuelve a entregar el **paquete completo** de
documentos; los tres roles re-revisan desde cero. Esto cae naturalmente del modelo
(las `RevisionCuentaCobro` cuelgan de `documento_entrega` y `actualizar_estado`
solo mira la última versión), así que no copies ni arrastres revisiones ni
documentos de versiones anteriores.

**Versionamiento del paquete:** se versiona el **paquete de entrega completo**
(`DocumentoEntrega`), no documentos individuales. El archivo de las versiones
anteriores se preserva para el historial (nunca se reemplaza en sitio); cada
versión conserva sus propios documentos y revisiones.

**Creación automática de versiones + botón "Entregar":** el contratista NO crea
versiones manualmente (evita versiones innecesarias). La nueva versión se crea de
forma **automática** cuando corresponde:
- Primera entrega: el contratista carga sus documentos en la versión 1 (creada al
  crear la cuenta) y pulsa **"Entregar"** para enviarla a revisión.
- Tras una devolución: el sistema habilita una nueva versión automáticamente; el
  contratista carga de nuevo el paquete completo y pulsa **"Entregar"**.
- El botón **"Entregar"** es la acción que (a) valida completitud contra
  `RequisitoDocumental`, (b) registra `EventoTrazabilidad` "Documentos enviados a
  revisión", y (c) envía el paquete al flujo de radicación/revisión. No debe
  existir un botón de "crear versión" expuesto al contratista.

### 4. Decisión final del supervisor (aprobación para firma)
1. Solo habilitada cuando `cuenta.estado_revisores == AP`.
2. El supervisor llama a `cuenta.revisar_supervisor(resultado, comentario)`. En la
   UI debe quedar explícito que esta aprobación es **para la firma de los documentos
   de cierre** (no es un cargue de documentos: el supervisor NO carga nada).
   - **`AP`** → `estado_supervisor=AP` y se setea **`fecha_aprobacion_supervisor`**.
     Habilita el cargue de los documentos de cierre firmados por **radicación** (§5).
   - **`RE`** → `estado_supervisor=RE` (y `fecha_aprobacion_supervisor` queda en
     `None`). La cuenta queda rechazada; NO reabre el flujo automáticamente.
3. **El comentario es obligatorio en el rechazo**: el modelo NO lo exige, así que
   **imponlo en el formulario y/o servicio**. Registra `EventoTrazabilidad(etapa=SUP)`.

### 5. Cargue de documentos de cierre firmados (por el rol de radicación)
1. Con `estado_supervisor == AP`, **el rol de radicación** carga los
   `DocumentoCierre` — el `tipo_documento` es FK a `TipoDocumentoCargue` (los mismos
   tipos del catálogo que la entrega, ahora **firmados**); el `usuario` del
   `DocumentoCierre` es el usuario de radicación. El `clean()` del modelo exige que
   la cuenta esté aprobada por el supervisor; respétalo. El servicio valida que el
   usuario tenga rol de radicación.
2. El **servicio** valida la completitud contra **los tipos obligatorios de la
   vigencia** (`RequisitoDocumental` con `obligatorio=True`): cada tipo obligatorio
   debe existir como `DocumentoCierre` de la cuenta (reutiliza el mismo helper de
   completitud que la entrega inicial, comparando contra `DocumentoCierre`). Registra
   `EventoTrazabilidad(etapa=CIE)` "Documentos de cierre firmados cargados".
3. Nota: con `estado_supervisor=AP`, los `clean()` de `DocumentoEntrega` y
   `RevisionCuentaCobro` bloquean nuevas entregas/revisiones del flujo de revisión.
   Es intencional. No lo evadas.

### 6. Trámites finales (secuenciales, modelo `TramiteFinal`)
Tras cargar los documentos de cierre firmados, siguen **dos pasos secuenciales**, cada
uno una instancia de `TramiteFinal` con su `tipo`. Cada paso plantea una pregunta sí/no;
al marcar "sí" (`realizado=True`) se **exige adjuntar evidencia + comentario**. El
modelo ya impone que NO haya evidencia sin `realizado=True` (clean + CheckConstraint);
NO evadas esa guarda. La autorización por rol se valida en servicios.

1. **`SF` — Cargue en SIIFWEB.** Se habilita cuando los documentos de cierre firmados
   están **completos** (§5). Pregunta: "¿Se cargó a SIIFWEB?". La responde **solo el
   revisor administrativo**. Al marcar sí → evidencia + comentario.
2. **`SC` — Cargue en SECOP II.** Solo se habilita tras `SF` realizado. Pregunta:
   "¿Se cargó a SECOP II?". La responde **solo el rol de secop**. Al marcar sí →
   evidencia + comentario.

La secuencialidad (`SC` tras `SF`) es **lógica de servicio** (el modelo solo
garantiza unicidad por `(cuenta_entrega, tipo)`). Registra un
`EventoTrazabilidad(etapa=CIE)` por cada trámite marcado realizado.

### 7. Cierre del trámite
1. Cuando los dos `TramiteFinal` (`SF`, `SC`) están `realizado=True`, el
   **servicio** valida esa completitud y llama a `cuenta.cerrar()` → setea
   `fecha_cierre`. Registra `EventoTrazabilidad(etapa=CIE)` "Trámite cerrado".

### 8. Línea de tiempo / trazabilidad histórica por cuenta (funcionalidad clave)

Cada `CuentaEntrega` debe exponer una **línea de tiempo cronológica** que cualquier
actor con acceso a esa cuenta (contratista, revisor o supervisor) pueda consultar.
Muestra el historial completo del trámite: **fechas, devoluciones realizadas y los
usuarios que intervinieron en cada etapa.**

**Fuente de datos:** se construye SOBRE `EventoTrazabilidad` (related_name
`eventos`), que ya está ordenado por `fecha_creacion`. NO se crean modelos nuevos.
La condición para que la línea de tiempo sea completa es que los servicios
**escriban un evento en cada transición** (ver catálogo abajo). Cada evento aporta:
`fecha_creacion` (la fecha), `actor` (el usuario que intervino), `etapa`, `evento`
(qué pasó) y `detalle` (p. ej. el comentario/causal de una devolución).

**Catálogo de eventos a registrar** (usa textos consistentes en `evento` para poder
filtrar; pon el comentario/motivo en `detalle`):
- Etapa `RAD`: "Cuenta creada", "Documentos enviados a revisión", "Radicación
  aprobada" (por supervisor o rol de radicación), **"Devolución en radicación
  (requiere ajustes)"**, "Documento marcado no aplica", "Nueva versión generada".
- Etapa `ASI`: "Revisores asignados", **"Revisor declinó"** (detalle = motivo),
  "Revisor reasignado".
- Etapa `REV`: "Aprobado por revisor jurídico/administrativo/técnico",
  **"Devolución de revisor jurídico/administrativo/técnico"** (detalle =
  comentario), "Revisores aprobaron la cuenta".
- Etapa `SUP`: "Aprobado por supervisor (para firma de documentos de cierre)",
  **"Rechazado por supervisor"** (detalle = razón de devolución).
- Etapa `CIE`: "Documentos de cierre firmados cargados", "Cargue en SIIFWEB
  registrado", "Cargue en SECOP II registrado", "Trámite cerrado".

**Devoluciones:** son el subconjunto de eventos marcados en negrita arriba (las
`RevisionParaRadicacion`/`RevisionCuentaCobro` con `resultado` en `AJ`/`RE`, las
declinaciones, y el rechazo del supervisor). La vista debe poder **destacarlas
visualmente y ofrecer un filtro "solo devoluciones"**. Como `EventoTrazabilidad`
no tiene un flag de devolución, identifícalas por su `evento` del catálogo (define
la lista de etiquetas de devolución como constante en servicios para no usar
strings mágicos dispersos).

**Presentación:** línea de tiempo vertical, orden cronológico ascendente, cada
entrada con fecha/hora, usuario (nombre completo o username), etiqueta de etapa, la
descripción del evento y el detalle cuando exista. Las devoluciones resaltadas
(color/ícono distinto). Reutiliza componentes de UI del proyecto; no inventes un
estilo nuevo.

**Alcance por rol (no es un filtro de contenido, es de acceso):** la línea de
tiempo de una cuenta muestra el historial **completo** a todo el que tenga acceso a
esa cuenta. La cuenta **pertenece al contratista**; los demás acceden por haber
intervenido. El acceso se restringe así:
- **Contratista:** las cuentas donde él es `CuentaEntrega.usuario` (es el dueño).
- **Revisor:** las cuentas donde tiene (o tuvo) una `AsignacionRevisor` —incluidas
  las declinadas, para que conserve el historial de lo que alcanzó a tocar—.
- **Supervisor:** las cuentas donde aparece como `supervisor` en una
  `RevisionParaRadicacion` (validó para radicación) o en una `AsignacionRevisor`
  (asignó revisores). Es decir, su pertenencia se deriva de su intervención real,
  no de un campo de supervisión en `CuentaEntrega` (que no existe).
- **Rol de radicación:** las cuentas donde aprobó radicación (`RevisionParaRadicacion`
  con su usuario), donde cargó algún `DocumentoCierre`, o (con `estado_supervisor=AP`)
  las que tiene pendientes de cargar el cierre firmado.
- **Rol de secop:** las cuentas donde registró el `TramiteFinal` `SC`.
Quien no participa en la cuenta no ve su línea de tiempo.

### 9. Panel de notificaciones (derivado, sin tocar modelos)

En la barra superior, **a la izquierda del ícono de cuenta de usuario**, un panel de
notificaciones (campana con contador) que muestra los **pendientes del usuario
actual según su rol**. Es **derivado**: se calcula en vivo consultando el estado del
flujo; NO se persiste ningún modelo `Notificacion` ni se toca el modelo. No hay
"leído/no leído": es un contador de pendientes accionables.

**Qué mostrar por rol** (cada ítem enlaza a la cuenta/acción correspondiente):
- **Contratista:** cuentas con devolución pendiente de corregir (documentos en `RE`
  / `RevisionParaRadicacion` o `RevisionCuentaCobro` en `AJ`/`RE` sobre su última
  entrega). Tras la aprobación del supervisor ya no tiene pendientes: el cierre lo
  carga radicación.
- **Supervisor / rol de radicación:** cuentas entregadas esperando aprobación de
  radicación; (supervisor) cuentas con `estado_revisores=AP` esperando su decisión
  final; cuentas radicadas sin revisores asignados; (radicación) cuentas con
  `estado_supervisor=AP` pendientes de cargar el cierre firmado.
- **Revisor (JU/AD/TE):** cuentas donde tiene una `AsignacionRevisor` activa **y su
  turno está habilitado** por el gating (su rol es el siguiente en
  jurídico→administrativo→técnico) y aún no ha emitido revisión sobre la última
  entrega.
- **Rol administrativo / secop (trámites finales):** cuentas donde su
  `TramiteFinal` (`SF`/`SC`) está habilitado por la secuencia y aún no
  realizado.

**Tipos de notificación con color característico** (usa la paleta del proyecto;
estos son los significados, no códigos hex):
- **Asignación pendiente** (te asignaron y es tu turno) — un color.
- **Revisión pendiente** (debes revisar) — otro.
- **Devolución** (algo te fue devuelto para corregir) — color de alerta/devolución,
  el mismo que resalta devoluciones en la línea de tiempo (§8), para coherencia.
- **Aprobación pendiente** (debes aprobar/decidir) — otro.

**Implementación:** un servicio `notificaciones_para(usuario)` que arma la lista de
pendientes consultando estado + asignaciones + gating (reutiliza la lógica de
`rol_habilitado` y la de `actualizar_estado`, no la dupliques). Exponlo en el
contexto del layout (context processor o vista parcial). Si el proyecto usa HTMX,
puede refrescarse con un poll ligero; si no, se calcula por request. El contador es
el número de pendientes accionables del usuario. **Este panel pertenece al módulo de
cuentas de cobro: solo muestra notificaciones de este módulo, y solo a usuarios con
un rol del módulo** (`tiene_rol(user, CONTRATISTA, SUPERVISOR, REVISOR, RADICACION,
SECOP)`). La otra app maneja sus propias notificaciones por separado; no las mezcles.

### 10. Botón "Información flujo de cuenta" (flujo + posición actual)

Junto a "Crear nueva cuenta", un botón **"Información flujo de cuenta"** que abre una
vista/modal con el **flujo completo de la cuenta de cobro representado de forma
dinámica, resaltando en qué etapa está ESTA cuenta**. No es un diagrama estático
genérico: refleja el avance real de la cuenta seleccionada.

**Etapas a representar** (en orden, derivadas del estado de la cuenta):
1. Cargue y entrega (versión actual, si hubo devoluciones mostrar el número de
   versión).
2. Radicación (pendiente / aprobada — `fecha_radicacion`).
3. Asignación de revisores (pendiente / hecha).
4. Revisión jurídica → administrativa → técnica (cuál está en curso, cuáles
   aprobadas, según las `RevisionCuentaCobro` de la última entrega).
5. Decisión del supervisor para firma (`estado_supervisor`, `fecha_aprobacion_supervisor`).
6. Cargue de documentos de cierre firmados por radicación (completo/incompleto).
7. Trámites finales `SF` → `SC` (cuáles realizados).
8. Cierre (`fecha_cierre`).

**Comportamiento:**
- La etapa actual se resalta; las completadas se marcan como hechas; las futuras se
  muestran atenuadas. Si la cuenta está en una **devolución**, indícalo en la etapa
  correspondiente (p. ej. "En corrección por devolución de revisor administrativo").
- El estado se **deriva del modelo** (mismos campos que usa el flujo); no introduce
  estructura nueva. Reutiliza el cálculo de etapa actual que también alimenta el
  panel de notificaciones y la línea de tiempo, para que las tres vistas sean
  coherentes.
- Visualmente, un stepper/timeline horizontal o vertical con la estética del
  proyecto; no inventes un estilo nuevo.

## Reglas de negocio críticas (no omitir)

- **Quién dispara los métodos del modelo:** `actualizar_fecha_radicacion`,
  `actualizar_estado` y `cerrar` NO se ejecutan solos. Llámalos **explícitamente
  desde los servicios** tras la acción correspondiente (no por señales), para
  controlar el orden del gating.
- **Siempre la última versión:** toda lógica de revisión opera sobre
  `cuenta.documentoentrega_set.order_by("-numero_version").first()`. No mezcles
  versiones.
- **Integridad bajo concurrencia:** envuelve cada transición de estado en
  `transaction.atomic()` y usa `select_for_update()` sobre la `CuentaEntrega` al
  evaluar/cambiar estado, para que dos revisores en paralelo no dejen el estado
  inconsistente.
- **Respeta los `clean()`:** no persistas con `save()` crudos que salten la
  validación cuando el modelo define `full_clean()` en su `save()`. Si necesitas
  saltarte una guarda, es señal de que el flujo está mal y debes consultarme.
- **Trazabilidad en cada transición:** escribe un `EventoTrazabilidad` en cada
  paso relevante (radicación, asignación, cada revisión, decisión del supervisor,
  cierre), con el actor real.

## Control de acceso entre apps (aislación total, usando los mixins existentes)

El proyecto aloja **dos apps** que comparten `User`/auth: este módulo (cuentas de
cobro) y otra app de gestión. **Ya existe un sistema de permisos por rol** que DEBES
reutilizar — no construyas middleware ni infraestructura nueva:
- Base: `cuentas.mixins.RolRequeridoMixin` (login + bypass de superusuario + chequeo
  vía `tiene_rol(user, *roles)`), parametrizable con `roles_permitidos`.
- Otra app (`contenido`): roles `DIRECTOR`, `COORDINADOR`, `FORMULADOR`; sus mixins
  propios.
- Este módulo: roles `CONTRATISTA`, `RADICACION`, `REVISOR`, `SECOP`, `SUPERVISOR`;
  mixins `ContratistaRequeridoMixin`, `SupervisorRequeridoMixin`,
  `RevisorRequeridoMixin`, `RadicacionRequeridoMixin`, `SecopRequeridoMixin`, y
  `ModuloRequeridoMixin` (cualquier actor del módulo, para vistas de consulta).
- **Estos archivos (`roles.py`, `mixins.py`) ya existen. Úsalos; no los recrees.**

### Aislación TOTAL — sin cruces
**Los roles NO se cruzan entre apps.** Cada rol ve únicamente lo que le corresponde
dentro de su propia app. En particular:
- Ningún rol de la otra app (`DIRECTOR`, `COORDINADOR`, `FORMULADOR`) accede a las
  vistas de cuentas de cobro.
- Ningún rol del módulo (`CONTRATISTA`, `SUPERVISOR`, `REVISOR`, `RADICACION`,
  `SECOP`) accede a las vistas de la otra app.
- **`SUPERVISOR` (módulo) y `DIRECTOR` (otra app) quedan separados**, sin solape, para
  evitar interferencias entre ambos.

Esto **ya emerge de los mixins**: como cada mixin del módulo lista solo roles del
módulo, un rol ajeno recibe 403 automáticamente. La única condición es que **TODA
vista del módulo lleve su mixin de rol** (acción → mixin del rol que actúa; consulta
compartida → `ModuloRequeridoMixin`). No dejes vistas sin proteger: una vista sin
mixin es el único hueco posible. **NO agregues roles de otra app a los mixins del
módulo, ni toques la otra app.**

### Nota sobre el rol REVISOR
`REVISOR` es **un solo grupo**; el sub-rol jurídico/administrativo/técnico NO es un
grupo de Django, se resuelve por `AsignacionRevisor.rol` por cuenta. Por tanto:
- El acceso al módulo y a las vistas de revisor se controla con el grupo `REVISOR`.
- Quién es el jurídico/administrativo/técnico de UNA cuenta (para el gating y para el
  trámite final `SF` que responde "el administrativo") se determina con la
  `AsignacionRevisor` activa de esa cuenta, NO con grupos. No crees tres grupos de
  revisor.

## Entregables

1. `services.py` (o paquete `services/`): TODA la lógica de transición y gating,
   validación de completitud (vía `RequisitoDocumental`), creación automática de
   versiones (acción "Entregar"), validación de presencia de documentos de cierre,
   secuencialidad y autorización por rol de los `TramiteFinal`, reasignación,
   escritura de `EventoTrazabilidad`, el cálculo de **notificaciones derivadas**
   `notificaciones_para(usuario)` (§9) y el cálculo de **etapa actual de la cuenta**
   que alimenta el botón de flujo (§10) y el panel. Define un **catálogo de
   etiquetas de evento** (constantes) según la sección 8, incluida la lista de
   etiquetas que cuentan como devolución. Las vistas llaman a servicios; no metas
   lógica de negocio en las vistas.
2. `views.py`: vistas por actor/acción, siguiendo el patrón del proyecto
   (CBV/FBV). Si el proyecto usa HTMX, úsalo para las acciones parciales.
3. `urls.py` del módulo, incluido en el `urls.py` raíz.
4. `forms.py`: cargue de documentos, acción "Entregar", revisión para radicación,
   revisión por rol, asignación, decisión del supervisor (con comentario obligatorio
   en rechazo), cargue de cierre firmado (radicación; selección de `tipo_documento`
   sobre los obligatorios de la vigencia), y los dos `TramiteFinal` (sí/no +
   evidencia + comentario, respetando que evidencia exige `realizado=True`).
5. Permisos: **reutiliza** los mixins existentes (`cuentas.mixins.RolRequeridoMixin`
   y los mixins por rol del módulo en su `mixins.py`). Aplica el mixin apropiado a
   CADA vista (acción → mixin del rol que actúa; consulta compartida →
   `ModuloRequeridoMixin`), sin dejar vistas sin proteger. NO recrees
   `roles.py`/`mixins.py`, NO construyas middleware, NO cruces roles entre apps y NO
   toques la otra app. Ver "Control de acceso entre apps".
6. Plantillas que **heredan del layout existente**:
   - **Vista/listado de cuentas de cobro con filtros por contratista y por estado
     de revisión** (además de los filtros que ya use el proyecto).
   - Bandeja por rol (contratista, supervisor, revisores, radicación, secop) con el
     estado de cada cuenta.
   - Detalle de cuenta: estado actual, versiones de entrega, documentos y sus
     estados, historial de revisiones, documentos de cierre y trámites finales.
   - **Línea de tiempo / trazabilidad** de la cuenta (sección 8): vista o
     componente que renderiza `cuenta.eventos` en orden cronológico, con fecha,
     usuario, etapa, evento y detalle; devoluciones resaltadas y filtro "solo
     devoluciones". Accesible según el alcance por rol.
   - **Panel de notificaciones** (§9) en la barra superior, a la izquierda del ícono
     de cuenta: campana con contador y lista de pendientes del usuario, con el color
     por tipo (asignación/revisión/devolución/aprobación). Derivado, sin modelo.
   - **Botón "Información flujo de cuenta"** (§10) junto a "Crear nueva cuenta":
     vista/modal con el flujo de la cuenta y su etapa actual resaltada.
   - Formularios de cada acción.
7. `admin.py`: registra los modelos con inlines útiles (`DocumentosCuentaCobro` por
   `DocumentoEntrega`; `RevisionCuentaCobro` por entrega) y filtros por estado,
   vigencia y rol. Registra también `RequisitoDocumental`, `DocumentoCierre`,
   `TramiteFinal` y
   `EventoTrazabilidad`.
8. Tests (`tests/`): camino feliz completo (cargue → "Entregar" → radicación →
   asignación → revisión jurídica→admin→técnica → decisión supervisor → cargue de
   cierre firmado por radicación → trámites finales `SF`→`SC` → cierre); gating
   secuencial (que `TE` no arranque sin `AD`); completitud (falta un obligatorio →
   no avanza); declinación + reasignación; **reinicio TOTAL desde jurídico** (ante
   cualquier devolución, la nueva versión nace vacía, el contratista reentrega el
   paquete completo y los tres roles re-revisan desde cero; el archivo de la versión
   previa se preserva); creación automática de versión vía "Entregar" (no manual);
   `TramiteFinal` (evidencia exige `realizado=True`; secuencialidad `SF`→`SC`;
   cada paso solo por su rol); cargue de cierre por radicación (no contratista);
   completitud de cierre contra los obligatorios de la vigencia; que
   `revisar_supervisor` con `AP` setea `fecha_aprobacion_supervisor` (y con `RE` la
   deja en `None`); que una `RevisionCuentaCobro` con `AP` se rechaza si algún
   documento está `PENDIENTE`/`RECHAZADO` y pasa con todos en `AP`/`NA`; rechazo del
   supervisor; bloqueo de nuevas entregas tras aprobación; y **la línea de tiempo**:
   que cada transición deja su evento, que las devoluciones se marcan, y que el
   alcance por rol restringe el acceso correctamente.

## Decisiones ya resueltas

- **Storage de archivos:** por ahora se usa **almacenamiento local** (`FileField`
  con `MEDIA_ROOT`). NO configures object storage todavía. Más adelante se migrará
  a **Supabase Storage** (compatible con S3 vía `django-storages`, sin cambiar los
  modelos: solo configuración en `settings`). Deja el código preparado para ese
  cambio (no hardcodees rutas absolutas; usa `MEDIA_URL`/`FileField.url`), pero no
  implementes el backend remoto ahora.

## Criterios de aceptación

- `python manage.py makemigrations` NO genera cambios estructurales en los modelos
  (solo las migraciones iniciales del módulo). Si propusiera alterar un modelo,
  detente: algo se tocó indebidamente.
- `migrate` corre sin errores.
- El flujo es recorrible **end-to-end** por los distintos roles: cargue →
  "Entregar" → radicación → asignación → revisión secuencial (3 roles) → decisión
  del supervisor (para firma) → cargue de cierre firmado por radicación → trámites
  finales (`SF` administrativo, `SC` secop) → cierre.
- La completitud documental se valida contra `RequisitoDocumental`.
- El gating secuencial (jurídico→administrativo→técnico) es inviolable desde la UI
  y desde servicios.
- Ante cualquier devolución, el flujo reinicia **totalmente desde jurídico**: nueva
  versión vacía, reentrega del paquete completo, re-revisión de los tres roles; el
  archivo de la versión anterior se preserva. La versión se crea automáticamente con
  "Entregar", nunca de forma manual.
- Un revisor que declinó no puede registrar revisiones.
- El supervisor aprueba para firma (no carga documentos); **radicación** carga los
  documentos de cierre firmados; no se cierra sin todos los tipos **obligatorios de
  la vigencia** presentes como `DocumentoCierre` ni sin los dos `TramiteFinal`
  realizados.
- En un `TramiteFinal` no se puede adjuntar evidencia sin `realizado=True`, y cada
  trámite solo lo responde su rol (administrativo/secop), en orden.
- Tras aprobación del supervisor, no se admiten nuevas entregas ni revisiones del
  flujo de revisión.
- Cada transición deja un `EventoTrazabilidad`.
- Cada cuenta expone una línea de tiempo cronológica con fechas, usuarios que
  intervinieron y devoluciones realizadas, visible según el alcance de acceso por
  rol, y con las devoluciones identificables.
- La vista de cuentas de cobro ofrece filtros por contratista y por estado de
  revisión.
- El panel de notificaciones muestra, por usuario y según su rol, los pendientes
  accionables (asignación/revisión/devolución/aprobación) con su color, calculados
  en vivo sin un modelo de notificaciones.
- El botón "Información flujo de cuenta" muestra el flujo completo con la etapa
  actual de esa cuenta resaltada, derivada del estado del modelo.
- Toda vista del módulo está protegida por un mixin de rol (ninguna vista sin
  mixin). Un rol de la otra app (`DIRECTOR`/`COORDINADOR`/`FORMULADOR`) recibe 403 en
  las vistas del módulo, y un rol del módulo recibe 403 en las de la otra app.
- **No hay cruce de roles entre apps**: `SUPERVISOR` y `DIRECTOR` quedan separados,
  cada rol ve solo lo de su app. No se modifica la otra app.
- El control de acceso reutiliza el sistema de mixins existente; no se añade
  middleware ni infraestructura de permisos nueva.
- La UI es visualmente consistente con el resto del sitio.
- Los tests pasan.

Empieza explorando el proyecto y devolviéndome el plan de archivos. **No generes
código hasta confirmar el plan, y no toques los modelos en ningún momento.**