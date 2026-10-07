# Banproe: Gestión de Proyectos (Gobernación de Sucre)

Plataforma web en Django para la gestión de proyectos de la Gobernación de Sucre.
Incluye el módulo **Gestión de cuentas de cobro**, que cubre el flujo completo de
una cuenta de cobro de contratistas: cargue de documentos, radicación, revisión
secuencial por tres roles, aprobación del supervisor, cargue de documentos de
cierre y trámites finales.

## Stack

- **Python** ≥ 3.13 · **Django** 5.2
- **Base de datos:** PostgreSQL en Supabase (`DATABASE_URL`); SQLite si no está definida
- **Frontend:** Django Templates + Tailwind **compilado** + Alpine.js + HTMX (sin Node)
- **Admin:** Django Unfold
- **Dependencias clave:** `django-unfold`, `django-storages`, `openpyxl`, `reportlab`
- **Gestor de entorno/paquetes:** [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`)

> El CSS de Tailwind está **compilado y versionado** en
> `proyectos/static/web/tailwind.css`; no se carga del CDN. Si cambias clases en
> una plantilla hay que recompilarlo (ver `CLAUDE.md`). Alpine y HTMX también se
> sirven desde `static/vendor/`, con versión fija.

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

# 2. Configurar el entorno: copiar la plantilla y rellenarla
cp .env.example proyectos/.env    # SECRET_KEY es obligatoria

# 3. Entrar a proyectos/: `load_dotenv()` busca el .env desde el directorio
#    actual, así que desde la raíz NO lo encuentra
cd proyectos

# 4. Aplicar migraciones y crear un superusuario (opcional, para el admin)
uv run python manage.py migrate
uv run python manage.py createsuperuser

# 5. Levantar el servidor de desarrollo
uv run python manage.py runserver
```

La aplicación queda en `http://127.0.0.1:8000/` (por **http**, no https) y el
admin en `/admin/`.

> Para trabajar en local conviene `HTTPS_ESTRICTO=0` (si no, las cookies `Secure`
> impiden iniciar sesión) y `DEBUG=1` (así los estáticos no necesitan
> `collectstatic`). Con `DATABASE_URL` vacía se usa la SQLite local.

> Alternativa sin `uv run`: activa el entorno (`source .venv/Scripts/activate` en
> Git Bash / `.venv\Scripts\Activate.ps1` en PowerShell) y usa
> `python manage.py …`.

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
   Hay una sola cuenta vigente por vigencia y mes: si la anterior quedó
   **rechazada** puede volver a presentarla; si fue **aprobada**, no.
2. El supervisor o el rol de radicación aprueban la **radicación**.
3. El supervisor asigna tres revisores y se revisa en orden estricto
   **técnico → jurídico → administrativo**.
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

Córrelos siempre con el módulo de settings de prueba, desde `proyectos/`:

```bash
cd proyectos
uv run python manage.py test --settings=proyectos.test_settings
```

> `proyectos/test_settings.py` fija SQLite en memoria y almacenamiento de
> archivos en memoria. Sin él, el suite crea la base de pruebas **en el servidor
> de Supabase** y cualquier test con `FileField` **sube el archivo al bucket de
> producción**.
