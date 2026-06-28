# Banproe — Gestión de Proyectos (Gobernación de Sucre)

Plataforma web en Django para la gestión de proyectos de la Gobernación de Sucre.
Incluye el módulo **Gestión de cuentas de cobro**, que cubre el flujo completo de
una cuenta de cobro de contratistas: cargue de documentos, radicación, revisión
secuencial por tres roles, aprobación del supervisor, cargue de documentos de
cierre y trámites finales.

## Stack

- **Python** ≥ 3.13 · **Django** 5.2
- **Base de datos:** SQLite (desarrollo)
- **Frontend:** Django Templates + Tailwind (CDN) + Alpine.js + HTMX (sin build/Node)
- **Admin:** Django Unfold
- **Dependencias clave:** `django-unfold`, `django-import-export`, `openpyxl`, `reportlab`
- **Gestor de entorno/paquetes:** [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`)

## Estructura

```
banproe/
├─ proyectos/                 # Proyecto Django (manage.py vive aquí)
│  ├─ proyectos/              # settings, urls raíz, wsgi/asgi
│  ├─ contenido/              # modelos núcleo + admin técnico
│  ├─ cuentas/                # autenticación y roles
│  ├─ web/                    # app de negocio (dashboard, proyectos, etc.)
│  ├─ cuentas_de_cobro/       # módulo de cuentas de cobro
│  ├─ templates/              # plantillas (heredan de base.html / base_app.html)
│  ├─ static/                 # estáticos del proyecto (web/app.css, logos)
│  ├─ media/                  # archivos subidos (no versionar)
│  └─ db.sqlite3              # BD de desarrollo (no versionar)
├─ docs/                      # documentación del proyecto
│  ├─ ESTRUCTURA.md           # detalle de la arquitectura del proyecto
│  ├─ ModuloProyectos.md      # requerimientos del módulo de proyectos (contenido/web)
│  ├─ ModuloCuentasCobro.md   # especificación autoritativa del módulo de cuentas de cobro
│  ├─ ManualRoles.md          # manual de roles de cada app (lenguaje sencillo)
│  └─ RestriccionRoles.md     # tarea de aislación de acceso por rol entre apps
├─ pyproject.toml / uv.lock   # dependencias
└─ README.md
```

## Puesta en marcha

Requiere [uv](https://docs.astral.sh/uv/) instalado.

```bash
# 1. Instalar dependencias (crea .venv a partir de uv.lock)
uv sync

# 2. Aplicar migraciones
uv run python proyectos/manage.py migrate

# 3. Crear un superusuario (opcional, para el admin)
uv run python proyectos/manage.py createsuperuser

# 4. Levantar el servidor de desarrollo
uv run python proyectos/manage.py runserver
```

La aplicación queda en `http://127.0.0.1:8000/` y el admin en `/admin/`.

> Alternativa sin `uv run`: activa el entorno (`source .venv/Scripts/activate` en
> Git Bash / `.venv\Scripts\Activate.ps1` en PowerShell), ubícate en `proyectos/`
> y usa `python manage.py …`.

## Módulo de cuentas de cobro

Documentación autoritativa: **`docs/ModuloCuentasCobro.md`**.

Arquitectura (módulo aparte, no toca otras apps):
- Lógica de negocio en `services.py` (transiciones de estado, gating secuencial,
  catálogo de eventos, notificaciones y cálculo del flujo/etapa actual).
- Querysets por rol y permisos a nivel de objeto en `selectors.py`.
- Vistas CBV delgadas que solo invocan servicios.
- **Modelos definitivos** (no se modifican); toda la lógica que no esté en el
  modelo vive en servicios/selectores.

### Roles (Django Groups)

`Contratista`, `Supervisor`, `Revisor`, `Radicacion`, `Secop`. El rol específico
del revisor (jurídico / administrativo / técnico) lo determina la
`AsignacionRevisor` de cada cuenta, no un grupo. Los grupos se crean por
migraciones de datos.

### Flujo

1. El contratista crea la cuenta, carga sus documentos y pulsa **Entregar**.
2. El supervisor o el rol de radicación aprueban la **radicación**.
3. El supervisor asigna tres revisores y se revisa en orden estricto
   **jurídico → administrativo → técnico**.
4. Ante cualquier devolución, el flujo reinicia por completo: el sistema genera
   una nueva versión vacía y el contratista vuelve a entregar el paquete completo.
5. El supervisor aprueba **para firma** (no carga documentos).
6. El contratista carga los **documentos de cierre**.
7. Se ejecutan los **trámites finales** `EC → SF → SC` (rol de radicación,
   revisor administrativo y rol de secop). Al completarse los tres, la cuenta
   se cierra automáticamente.

La bandeja muestra, por cuenta, el paso actual y su responsable, el paso
siguiente y su responsable, y el tiempo en el paso actual; cada cuenta expone una
línea de tiempo de trazabilidad y un panel de notificaciones derivado del estado.

## Tests

```bash
uv run python proyectos/manage.py test cuentas_de_cobro
```

> Los tests con `FileField` escriben en `proyectos/media/` (subcarpetas
> `cuentas_cobro/`, `cierres/`, `tramites_finales/`); conviene limpiarlas tras
> correrlos.
