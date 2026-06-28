# Gestión de Proyectos — Gobernación de Sucre

Sistema de seguimiento de la formulación de proyectos: **Director → Coordinador → Formulador**, con flujo Proyecto → Actividad → Entrega (versión) → Documentos → Revisión.

Incluye además un módulo aparte, **Gestión de cuentas de cobro** (app `cuentas_de_cobro`), con su propio flujo y roles (Contratista, Supervisor, Revisor, Radicación, Secop). Ver la sección dedicada más abajo y la especificación autoritativa en `ModuloCuentasCobro.md`.

La solución tiene **dos interfaces**:

1. **Aplicación web de negocio** (MVT, estilo Notion/Jira) para los usuarios finales.
2. **Django Admin (Unfold)** como herramienta técnica de parametrización/soporte para staff y superusuarios.

> **Regla dura del proyecto:** los modelos de `contenido/models.py` son la fuente de verdad y **NO se modifican**. Toda la solución se construye alrededor de ellos.

---

## Stack

- **Backend:** Django 5.2, SQLite (dev), patrón MVT.
- **Frontend:** Django Templates + **Tailwind CSS (CDN)** + **HTMX** + **Alpine.js** (sin build/Node).
- **Admin:** Django Unfold (`unfold`, `unfold.contrib.filters`).
- **Reportes:** `openpyxl` (Excel) y `reportlab` (PDF).
- **Identidad:** marca Gobernación de Sucre — verde `#109d39`, azul `#0b72ab`, rojo `#d92a34`, ámbar `#ffa700`; tipografías Montserrat (titulares) y Open Sans (texto).

---

## Estructura de carpetas

```
banproe/
├─ README.md                   # Puesta en marcha
├─ docs/                       # Documentación del proyecto
│  ├─ ESTRUCTURA.md            # Este documento
│  ├─ ModuloProyectos.md       # Requerimientos del módulo de proyectos (fuente del alcance)
│  ├─ ModuloCuentasCobro.md    # Especificación del módulo de cuentas de cobro
│  ├─ ManualRoles.md           # Manual de roles de cada app (lenguaje sencillo)
│  └─ RestriccionRoles.md      # Aislación de acceso por rol entre apps
├─ pyproject.toml / uv.lock    # Dependencias (gestor: uv)
├─ contexto/                   # Manual de identidad (PDF, solo imágenes)
└─ proyectos/                  # Proyecto Django (manage.py aquí; BASE_DIR)
   ├─ manage.py
   ├─ db.sqlite3
   ├─ media/                   # Archivos subidos (Documentos)
   ├─ static/
   │  ├─ web/app.css           # Design system de marca
   │  └─ logos/                # Logos oficiales + escudo-blanco.png / escudo-color.png (recortes)
   ├─ templates/               # Plantillas globales (DIRS)
   ├─ proyectos/               # Configuración del proyecto (settings, urls raíz, wsgi/asgi)
   ├─ contenido/               # App de modelos + Django Admin técnico
   ├─ cuentas/                 # Autenticación, roles, control de acceso, perfil
   ├─ web/                     # Aplicación web de negocio (vistas, servicios, reportes)
   └─ cuentas_de_cobro/        # Módulo de cuentas de cobro (app aparte; ver sección dedicada)
```

> El repositorio incluye también `README.md` y `.gitignore` en la raíz. La documentación (este archivo, `ModuloProyectos.md`, `ModuloCuentasCobro.md`, `ManualRoles.md`, `RestriccionRoles.md`) vive en `docs/`.

---

## App `contenido` — Modelos y Admin

Modelos (en `contenido/models.py`, **no modificar**):

| Modelo | Campos clave | Notas |
|---|---|---|
| `Proyectos` | nombre, creador_por (Director), asignado_a (Coordinador) | hereda `Fechas` (creación/actualización) |
| `Actividades` | proyecto, nombre, fecha_programada, fecha_vencimiento, **estado**, asignado_por (Coord), asignado_a (Formulador) | `estado ∈ {PE Pendiente, ER En revisión, AJ Requiere ajustes, AP Aprobada}`; `clean()` valida fechas |
| `Subactividades` | nombre, actividad | |
| `ActividadEntrega` | actividad, numero_version, usuario, comentario | `numero_version` autoincremental por actividad; `clean()` bloquea entregas si la actividad está Aprobada |
| `Documentos` | actividad_entrega, nombre, archivo (FileField) | |
| `Revisiones` | actividad_entrega (OneToOne), revisor, comentario, **resultado** | `resultado ∈ {AP, AJ, RE}`; bloquea revisión si la actividad está Aprobada |

- **`contenido/admin.py`** — Admin **técnico** (Unfold) para staff: `ModelAdmin` de todos los modelos + `User`/`Group` re-registrados con estilos Unfold. `list_display`, `search_fields`, `list_filter` (dropdowns Unfold), `ordering`, `autocomplete_fields`, `fieldsets`, inlines y acciones de estado. **Sin** filtrado por rol de negocio (eso vive en la app web).
- **`migrations/0002_roles_groups.py`** — crea los grupos **Director, Coordinador, Formulador**.

---

## App `cuentas` — Accesos y roles

| Archivo | Responsabilidad |
|---|---|
| `roles.py` | Constantes `DIRECTOR/COORDINADOR/FORMULADOR` y helpers (`es_director`, `es_coordinador`, `es_formulador`, `rol_principal`). Superusuario = Director. |
| `mixins.py` | `RolRequeridoMixin` y derivados (`DirectorRequeridoMixin`, `GestionRequeridoMixin`, …) para CBVs. |
| `decorators.py` | `@rol_requerido(...)` para FBVs. |
| `context_processors.py` | Expone `es_director/es_coordinador/es_formulador/rol_principal` a todas las plantillas. |
| `forms.py` | `LoginForm` estilizado. |
| `views.py` | `AppLoginView`, `AppLogoutView`, `PerfilView`. |
| `urls.py` | `cuentas:login`, `cuentas:logout`, `cuentas:perfil`. |

> Los roles de **proyectos** (Director/Coordinador/Formulador) viven aquí. El módulo de **cuentas de cobro** define sus propios roles y mixins en `cuentas_de_cobro/roles.py` y `cuentas_de_cobro/mixins.py` (reutilizando `RolRequeridoMixin`).

---

## App `web` — Aplicación de negocio

| Archivo | Responsabilidad |
|---|---|
| `selectors.py` | **Querysets filtrados por rol** (Director: sus proyectos; Coordinador: asignados; Formulador: sus actividades). Punto único de scoping ("un usuario nunca ve lo que no le corresponde"). |
| `services.py` | **Transiciones de estado**: `crear_entrega()` (→ actividad En revisión), `registrar_revision()` (→ Aprobada / Requiere ajustes). También `notificaciones_para(user)`: pendientes derivados (proyecto/actividad asignada, actividad por revisar, ajustes pedidos, plazos por cumplirse/vencidos). |
| `context_processors.py` | `notificaciones_web`: expone `web_notificaciones` / `web_notificaciones_total` al topbar (campana de proyectos). |
| `metrics.py` | Métricas de dashboards por rol (`director()`, `coordinador()`, `formulador()`). |
| `forms.py` | `ProyectoForm`, `ActividadForm`, `SubactividadForm`, `EntregaForm`, `DocumentoForm`, `RevisionForm` (estilizados). |
| `views.py` | Vistas de dashboard, proyectos, actividades, entregas, revisiones y reportes. |
| `urls.py` | Rutas con namespace `web:`. |
| `templatetags/web_extras.py` | Filtros/tags: `estado_badge`, `resultado_badge`, `vencida`, `dias_restantes`, `dias_transcurridos`, `porcentaje`, `startswith`. |
| `reports/` | Módulo de reportes (ver abajo). |

### Vistas y rutas (`web:`)

| Ruta | Nombre | Vista | Acceso |
|---|---|---|---|
| `/` | `dashboard` | `DashboardView` (template por rol) | autenticado |
| `/proyectos/` | `proyectos` | `ProyectoListView` | autenticado (scoping) |
| `/proyectos/nuevo/` | `proyecto_nuevo` | `ProyectoCreateView` | Director |
| `/proyectos/<pk>/` | `proyecto_detalle` | `ProyectoDetailView` | scoping |
| `/proyectos/<pk>/editar/` | `proyecto_editar` | `ProyectoUpdateView` | Director |
| `/proyectos/<pk>/actividades/nueva/` | `actividad_nueva` | `ActividadCreateView` | Coordinador del proyecto |
| `/actividades/` | `actividades` | `ActividadListView` (filtros estado/proyecto/buscador) | scoping |
| `/actividades/<pk>/` | `actividad_detalle` | `ActividadDetailView` (timeline) | scoping |
| `/actividades/<pk>/subactividades/nueva/` | `subactividad_nueva` | `SubactividadCreateView` | Coordinador |
| `/actividades/<pk>/entregas/nueva/` | `entrega_nueva` | `EntregaCreateView` | Formulador asignado |
| `/entregas/<pk>/` | `entrega_detalle` | `EntregaDetailView` | scoping |
| `/entregas/<pk>/documentos/nuevo/` | `documento_nuevo` | `DocumentoCreateView` | Formulador dueño |
| `/entregas/<pk>/revisar/` | `revision_nueva` | `RevisionCreateView` | Coordinador del proyecto |
| `/reportes/` | `reportes` | `ReportesIndexView` | autenticado |
| `/reportes/proyectos-formulados.xlsx` | `reporte_formulados_excel` | FBV | autenticado |
| `/reportes/avance-por-proyecto.pdf` | `reporte_avance_pdf` | FBV | autenticado |

---

## Flujo de negocio

1. **Director** crea proyecto y lo asigna a un **Coordinador**.
2. **Coordinador** crea actividades (y subactividades), las asigna a un **Formulador** (estado inicial: Pendiente).
3. **Formulador** registra una **entrega** (versión) → la actividad pasa a **En revisión**; adjunta documentos.
4. **Coordinador** revisa la entrega:
   - **Aprobada** → actividad **Aprobada** (finalizada).
   - **Requiere ajustes / Rechazada** → actividad **Requiere ajustes**; el formulador crea una nueva entrega.

Colores de estado: Pendiente (gris), En revisión (azul), Requiere ajustes (ámbar), Aprobada (verde), Vencida/Rechazada (rojo).

---

## Dashboards por rol

Plantillas en `templates/web/dashboard/{director,coordinador,formulador,generico}.html`; datos en `web/metrics.py`. KPI cards, gráfico de estados (barra apilada), barras de avance, tablas resumidas, badges y alertas.

- **Director (ejecutivo):** proyectos, coordinadores, pendientes de revisión (con antigüedad/atrasadas), vencidas; resumen/ranking por coordinador; cumplimiento por proyecto; próximas/vencidas.
- **Coordinador (operativo):** actividades, pendientes de revisión (acceso rápido a *Revisar*), tiempo promedio de revisión, formuladores con más pendientes, revisadas recientes, proyectos a cargo.
- **Formulador (personal):** mis actividades, en revisión, requieren ajustes, vencidas; por proyecto/estado; "requieren nueva entrega" y "sin ninguna entrega" con acción directa; días restantes; historial.

---

## Módulo de Reportes (`web/reports/`)

Arquitectura con separación negocio / consultas / generación, escalable a nuevos reportes.

| Archivo | Responsabilidad |
|---|---|
| `queries.py` | `proyectos_formulados()` y `avance_por_proyecto()` — reglas + consultas optimizadas, scoping por rol. |
| `excel.py` | Generación `.xlsx` (openpyxl), encabezado institucional, cabeceras de marca, autofiltro. |
| `pdf.py` | Generación PDF (reportlab): encabezado con escudo, numeración de páginas, fecha y usuario, tablas con cabecera repetida. |
| `forms.py` | `ReporteFormuladosForm`, `ReporteAvanceForm` (filtros). |

- **Proyectos Formulados → Excel:** proyectos con **todas** sus actividades Aprobadas. Filtros: rango de fechas, responsable, proyecto.
- **Avance por Proyecto → PDF:** avance = aprobadas/total. Resumen ejecutivo + resumen por proyecto + detalle de actividades. Filtros: fechas, proyecto, responsable, estados.

---

## App `cuentas_de_cobro` — Módulo de cuentas de cobro

Módulo **aparte** (no toca las otras apps). Especificación autoritativa: `ModuloCuentasCobro.md`. Los modelos de `cuentas_de_cobro/models.py` son **definitivos**; toda la lógica que no esté en el modelo vive en servicios/selectores.

| Archivo | Responsabilidad |
|---|---|
| `models.py` | `Vigencia`, `TipoDocumentoCargue`, `RequisitoDocumental`, `CuentaEntrega`, `DocumentoEntrega` (paquete versionado), `DocumentosCuentaCobro`, `RevisionParaRadicacion`, `AsignacionRevisor`, `RevisionCuentaCobro`, `DocumentoCierre`, `TramiteFinal` (EC/SF/SC), `EventoTrazabilidad`. **No modificar.** |
| `services.py` | Transiciones y **gating secuencial** (`transaction.atomic` + `select_for_update`); catálogo de eventos `Eventos`; `entregar`, `registrar_revision_radicacion`, `asignar_revisor`, `registrar_revision`, `decidir_supervisor`, `cargar_documento_cierre`, `responder_tramite`; `notificaciones_para(user)`; `flujo_de_cuenta(cuenta)` y `paso_actual(cuenta)` (etapa actual, responsable, paso siguiente). |
| `selectors.py` | Querysets por rol/**intervención** y permisos a nivel de objeto (`puede_entregar`, `puede_radicar`, `puede_marcar_documentos`, `puede_cargar_cierre`, `puede_responder_tramite`, …). |
| `roles.py` / `mixins.py` | Roles propios por Django Groups: **Contratista, Supervisor, Revisor, Radicacion, Secop**. El rol jurídico/administrativo/técnico lo lleva la `AsignacionRevisor`, no un grupo. |
| `context_processors.py` | `roles_cuentas_cobro`: roles + `cc_notificaciones` para el topbar. |
| `admin.py` · `views.py` · `urls.py` · `templatetags/cuentas_cobro_extras.py` | Admin Unfold; CBVs delgadas; namespace `cuentas_cobro:`; tag `cc_badge`. |

**Flujo:** contratista crea la cuenta y carga documentos → **Entregar** (valida completitud) → radicación (Supervisor o Radicación) → asignación de 3 revisores → revisión secuencial **jurídico → administrativo → técnico** (gating) → decisión final del supervisor (para firma) → el contratista carga los documentos de cierre → trámites finales **EC → SF → SC** (Radicación / Revisor administrativo / Secop) → cierre automático. **Reinicio TOTAL** ante cualquier devolución: nueva versión vacía y re-revisión desde el jurídico.

**Migraciones de grupos:** `0003_roles_cuentas_cobro` (Contratista/Supervisor/Revisor) y `0005_roles_radicacion_secop` (Radicacion/Secop).

**UI:** bandeja con filtros (contratista/estado, incluye **Cerrada**) y columnas **Paso actual** / **Paso siguiente** con su responsable y **Tiempo en paso actual**; detalle con stepper "Información flujo de cuenta", línea de tiempo con filtro "solo devoluciones" + resaltado, y **flujograma del proceso** (modal).

---

## Notificaciones (campana en el topbar)

Dos paneles **derivados** (sin modelo; se calculan en vivo por request) en la barra superior, a la izquierda del menú de usuario:

- **Proyectos** (app `web`, ícono de carpeta): proyecto/actividad asignada, actividad por revisar, ajustes pedidos, plazos por cumplirse o vencidos.
- **Cuentas de cobro** (ícono de cobro): radicación pendiente, revisión en turno, devoluciones, decisión del supervisor y trámites finales habilitados.

Cada campana se muestra solo si el usuario pertenece a ese dominio. Color por tipo: asignación = azul, revisión = verde, devolución = rojo, aprobación/plazo = ámbar.

---

## Plantillas y componentes

- `base.html` — esqueleto (Tailwind CDN config de marca, fuentes, HTMX/Alpine, `app.css`, favicon escudo, toasts).
- `base_app.html` — shell de la app: **sidebar** (verde oscuro sólido, menú por rol, escudo blanco), **topbar** (breadcrumbs, **campanas de notificaciones**, menú de usuario), contenido.
- `components/` — `nav_item.html`, `form_field.html`, `paginacion.html`, `estado_bar.html` (gráfico de estados), `lista_actividades.html`.
- `cuentas/` — `login.html` (panel de marca + formulario), `perfil.html`.
- `web/` — `proyectos/`, `actividades/`, `entregas/`, `dashboard/`, `reportes/`; `web/_notificaciones.html` (campana de proyectos); `web/actividades/_flujograma.html` + `_flecha.html` (flujograma del proceso completo: proyecto → actividad → subactividad).
- `cuentas_cobro/` — `bandeja.html`, `cuenta_form.html`, `cuenta_detalle.html`; `_notificaciones.html` (campana), `_flujograma.html` + `_flecha.html` (flujograma del proceso). Los modales usan `x-teleport="body"` para no quedar atrapados en el stacking context de `<main>`.

---

## Identidad visual (`contexto/`)

El PDF en `contexto/` es el **Manual de Identidad** (solo imágenes, texto no extraíble). Paleta y tipografía implementadas como tokens CSS en `static/web/app.css` y en la config inline de Tailwind en `base.html`. Logos oficiales en `static/logos/`; `escudo-blanco.png` (fondos verdes) y `escudo-color.png` (fondos claros / favicon) son recortes del emblema.

---

## Cómo ejecutar

```bash
cd proyectos
python manage.py migrate
python manage.py createsuperuser          # acceso al admin (/admin/)
python manage.py runserver
```

> Con `uv`: `uv sync` y luego `uv run python proyectos/manage.py <comando>` (ver `README.md`).

- App web: `/`  ·  Cuentas de cobro: `/cuentas-cobro/`  ·  Admin técnico: `/admin/`
- Asignar a cada usuario su grupo y marcar `is_staff` para acceso al admin:
  - Proyectos: **Director / Coordinador / Formulador**.
  - Cuentas de cobro: **Contratista / Supervisor / Revisor / Radicacion / Secop**.

---

## Dependencias

- **Runtime (pyproject):** Django 5.2, django-unfold, django-import-export, openpyxl, reportlab.
- **Solo en el venv** (preparación de assets, no son del producto): `pypdf`, `pymupdf`, `pillow`.

---

## Decisiones de diseño relevantes

- El **admin no es la interfaz de negocio**: la lógica de rol vive en `web/selectors.py` (consultas) y `web/services.py` (transiciones).
- Visibilidad por rol centralizada en selectores; defensa adicional a nivel de objeto en las vistas de acción.
- UI con colores **sólidos** de marca (sin degradados decorativos), sin emojis; verde oscuro en sidebar y panel de login.
