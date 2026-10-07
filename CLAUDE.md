# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Django platform for the Gobernación de Sucre with **two independent business domains**:

- **Proyectos**: projects → activities → subactivities. Apps: `contenido` (core models + technical admin), `cuentas` (auth/roles/perfil), `web` (business app: dashboards, projects, activities, reports).
- **Cuentas de cobro**: monthly contractor billing workflow. App: `cuentas_de_cobro` (self-contained module with its own roles).

The two domains **do not cross**: a role in one domain never accesses the other's views, and each user sees only their domain's navigation. Superuser sees everything. **One deliberate exception:** the transversal read-only role `Consulta` sees both domains (all data, both nav menus) but can execute no action.

## Commands

Environment manager is **uv** (`pyproject.toml` + `uv.lock`). Python ≥ 3.13, Django 5.2. `manage.py` lives in `proyectos/`, not the repo root.

```bash
uv sync                                             # install deps into .venv
uv run python proyectos/manage.py migrate
uv run python proyectos/manage.py runserver
uv run python proyectos/manage.py createsuperuser
uv run python proyectos/manage.py collectstatic --noinput
```

### Tests: always run them with `proyectos.test_settings`

```bash
cd proyectos
uv run python manage.py test --settings=proyectos.test_settings
uv run python manage.py test cuentas_de_cobro --settings=proyectos.test_settings
# single test:
uv run python manage.py test web.tests.PuedeRevisarTest.test_trabajo_de_coordinador --settings=proyectos.test_settings
```

`proyectos/test_settings.py` inherits from `proyectos.settings` and pins the three things that otherwise reach production, so **no test can touch it by accident** and no test needs its own `override_settings`:

- **SQLite in memory.** `DATABASE_URL` points to Supabase Postgres; without this, `manage.py test` creates/drops a `test_postgres` database **on the server** (slow, and a failed teardown leaves it orphaned).
- **`InMemoryStorage`.** The default storage is S3, so any test saving a `FileField` **uploads to the production bucket** when credentials are present (and errors with `NoCredentialsError` when they are not).
- **Plain static storage**, so the suite does not depend on having run `collectstatic`, plus MD5 password hashing (the suite drops from ~110 s to ~7 s) and `HTTPS_ESTRICTO=0`/a throwaway `SECRET_KEY` set *before* importing the real settings, so the suite runs on a machine with no `.env`.

Test suites live in `cuentas_de_cobro/tests/` and `web/tests.py` (`contenido/tests.py` is empty). 139 tests, all green.

Static-file storage uses `ManifestStaticFilesStorage` **when `DEBUG=0`**, so **re-run `collectstatic` after changing any static asset** (CSS, logos, vendored JS) or WhiteNoise serves the stale version. A missing manifest entry raises `ValueError: Missing staticfiles manifest entry for '<path>'` and returns a **500 with `DEBUG=False`**, and since `base.html` loads the CSS, that 500 covers *every* page, including `403.html`/`500.html`, so the error page cannot render either. Two more things worth knowing:

- The manifest is read **once per process**: after `collectstatic` you must restart `runserver`, or it keeps serving the old manifest and 500s on the new files.
- With `DEBUG=1` the plain storage is used instead, precisely so local work does not need `collectstatic` at all.

Config/secrets are in `proyectos/.env`:

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Supabase **session pooler** (`aws-0-us-east-1.pooler.supabase.com:5432`, user `postgres.<ref>`). The direct host `db.<ref>.supabase.co` resolves to **IPv6 only** and fails without IPv6; the transaction pooler (`:6543`) hangs on long operations like `dumpdata`. |
| `SECRET_KEY` | **Required**: the app now refuses to start without it. |
| `SUPABASE_S3_ACCESS_KEY` / `SUPABASE_S3_SECRET_KEY` | |
| `SUPABASE_S3_ENDPOINT_URL` | **Required.** If unset, `AWS_S3_ENDPOINT_URL` is `None`, boto3 falls back to real AWS S3 and **all uploads/downloads break**. |
| `SUPABASE_BUCKET` | Optional, defaults to `documentos`. |
| `CSRF_TRUSTED_ORIGINS` | Optional, comma-separated `scheme://host` (no trailing slash). Only needed for *other* origins: Django already accepts the request's own origin. |
| `HTTPS_ESTRICTO` | Defaults to `1`. **Set to `0` for local development over HTTP**, or the `Secure` cookies stop you from logging in and `SECURE_SSL_REDIRECT` causes a redirect loop. |
| `SECURE_HSTS_SECONDS` | Optional, defaults to `3600`. Raise to `31536000` once HTTPS is verified stable. |
| `DEBUG` | Optional, defaults to `0` (off). `DEBUG=1` also switches static files to the plain storage, so no `collectstatic` is needed locally. Never set it in production. |
| `ALLOWED_HOSTS` | Optional, comma-separated, defaults to `*` (what the Railway deploy needs, since the assigned domain changes). Narrow it if the domain ever becomes fixed. |
| `LOG_LEVEL` | Optional, defaults to `INFO`. Controls the `django` logger, which now writes to stdout: that is how 500 tracebacks reach the Railway logs. |

`load_dotenv()` is called **without a path**, so it searches upward from the *current working directory*: running `uv run python proyectos/manage.py …` from the repo root does **not** find `proyectos/.env`. Run commands from inside `proyectos/`, or export the variables. `override=False` is the default: a shell-exported var wins over `.env` (that's why the SQLite test override works).

## Architecture pattern (follow it)

**Thin CBVs → `services.py` + `selectors.py`.** Views only call these; no business logic in views.

- `services.py`: state transitions. In `cuentas_de_cobro` these are wrapped in `transaction.atomic` + `select_for_update`, and drive the model transition methods explicitly (not via signals) to control gating order. Also holds derived **notifications** (`notificaciones_para(user)`) and flow/stage computation.
- `selectors.py`: role- and intervention-scoped querysets (the single source of "a user never sees what isn't theirs") plus object-level permission helpers (`puede_*`). Views start from these, never from `Model.objects`.

**Roles are Django Groups**, created in data migrations (not settings): Proyectos = `Director`, `Coordinador`, `Formulador` (`contenido.0002`); Cuentas de cobro = `Contratista`, `Supervisor`, `Revisor` (`0003`), `Radicacion`, `Secop` (`0005`); transversal read-only `Consulta` (`contenido.0003`). Access control uses mixins subclassing `cuentas.mixins.RolRequeridoMixin` (login + superuser bypass + `tiene_rol`); `cuentas_de_cobro/mixins.py` parametrizes it with the module's groups. **Every module view carries a role mixin**: action views get the acting role's mixin, shared read views get `ModuloRequeridoMixin`.

**`Consulta` (read-only, cross-domain)** is granted by adding a `CONSULTA` branch to every `selectors.*_visibles` queryset (returns the full set, like superuser) and by listing `CONSULTA` in `ModuloRequeridoMixin.roles_permitidos`. It gets no write access for free: the object-level `puede_*` helpers already gate on role/ownership/superuser, all of which are false for a Consulta user, and action views keep their specific role mixins. Nav visibility is driven by `web_ver_modulo`/`cc_ver_modulo` (context processors): separate from `web_usa_modulo`/`cc_usa_modulo`, which still gate the notification bells (Consulta has none). Its dashboard is `web/dashboard/consulta.html` (metrics via `metrics.consulta`, which does a **deferred** import of `cuentas_de_cobro.selectors` to avoid coupling the domains at import time).

**Derived notifications** have no model: computed live per request in `services.notificaciones_para(user)` and exposed via context processors. Two bells in the topbar (`web` = folder icon, `cuentas_de_cobro` = billing icon), each visible only to its domain.

**Frontend**: Django Templates + **compiled Tailwind** + Alpine.js + HTMX, no Node. Admin is Django Unfold. App pages extend `base_app.html` (sidebar + topbar); it extends `base.html` (which renders message toasts).

**Two Alpine traps, both paid for the hard way** (they cost a working feature twice, and neither shows up in a template render test):

- **Scripts that register an Alpine component must load BEFORE Alpine.** The vendored bundle ends in `queueMicrotask(() => Alpine.start())`, so it starts and fires `alpine:init` before the next deferred script runs. Load it after and the component is never registered, `x-data` throws, and anything behind an `x-show` in that subtree gets hidden: the upload button appeared for an instant and vanished.
- **Inside an `x-on`/`@click` handler, `$el` is the element that fired the event**, not the component root. Use **`$root`** to reach the component. With `$el`, `subidas.js` searched for the CSRF token inside the `<button>`, found nothing and every upload came back `403 Forbidden (CSRF token missing)`.
- Corollary for any `x-show`: a failing expression evaluates to undefined, which Alpine treats as false and **hides** the element. Never put an `x-show` on something whose disappearance would leave the user stuck (a button's label, for instance): fail visible. Prefer `:disabled` where you can: if it fails it evaluates falsy, so the control stays usable, which is the safe side of the error.
- **A getter that reads the DOM is not reactive.** `puedeSubir` and `resumen` read the `[data-subida]` fields with `querySelectorAll`, and Alpine only re-evaluates a getter when a *reactive* property it touched changes. Typing in a plain `<input>` changes nothing Alpine watches, so the getter kept its stale value: picking the file **before** filling the type/name left the button disabled with no way to re-enable it (picking the file after worked, which is why it went unnoticed). The fix is a `tocado` counter bumped by `@input`/`@change` on the component root (events bubble, so one listener covers every field) and read at the top of each such getter. Any new getter that reads the DOM must declare it too.

**File uploads go straight to the bucket, not through Django.** `cuentas/subidas.py` + `static/web/subidas.js` + `templates/components/subida_directa.html` implement a three-step flow: the browser asks for permission (the server checks the role, validates the extension and returns a presigned PUT plus a signed token), uploads with `XMLHttpRequest` showing a progress percentage, then confirms (the server verifies with `head_object` that the object is really there and how big it is, and only then creates the row by assigning `documento.name = <key>`, no second transfer). Two things to know:

- **No CORS setup is needed, and none is possible.** Supabase Storage does not implement `PutBucketCors`/`GetBucketCors`, so a bucket's CORS cannot be configured at all. It does not need to be: the gateway already answers the preflight with `Access-Control-Allow-Origin: *` and allows `PUT` plus the `content-type` header, which is all this flow sends. Verified against the live project with:

  ```bash
  curl -s -o /dev/null -D - -X OPTIONS \
    "https://<ref>.storage.supabase.co/storage/v1/s3/documentos/media/x.pdf" \
    -H "Origin: https://<app-domain>" -H "Access-Control-Request-Method: PUT" \
    -H "Access-Control-Request-Headers: content-type"
  ```

  Re-run that if a browser upload ever fails with a CORS error: the answer comes from Supabase, not from this code. Note the signature covers `Content-Type`, so the browser must send exactly the value the server signed (that is why `subidas.js` sets it from the response).
- The token is signed with `django.core.signing` and carries the account and document type. Without it, the confirm step would accept any key the browser made up, and someone could attach to one account a file living elsewhere in the bucket.
- Why not through Django: a single sync worker plus the default `--timeout` capped uploads at whatever fits in 30 s, and the transfer blocked the whole platform while it ran (1 GB at 5 Mbps is 27 minutes).
- **All four upload spots use the component**: entrega documents, closing documents, trámite-final evidence (`cuentas_cobro/cuenta_detalle.html`) and Proyectos entrega documents (`web/entregas/detalle.html`). The classic form-POST views (`DocumentoCargarView`, `DocumentoCierreView`, `TramiteFinalView`, `web.DocumentoCreateView`) are still wired and still enforce their own permissions, but nothing in the UI points at them any more; they are the fallback if the bucket flow ever has to be rolled back.
- **The button does not send: it asks to confirm first.** Pressing it opens a summary of what is about to go out (file name and size, plus every visible `[data-subida]` field, a `<select>` showing its option's text rather than the id), with "Confirmar envío" and "Volver a elegir". Nothing has left the browser at that point, so going back costs nothing and leaves no orphan in the bucket. It is on **all four** upload spots on purpose, but it is not cosmetic: `TramiteFinal` has a `CheckConstraint` forbidding evidence without `realizado=True`, so there is no "file attached but not submitted" state to fall back on, and answering the last trámite closes the account in the same transaction. This pause is the only "are you sure?" possible without touching `models.py`. The irreversible spots also pass an `advertencia` (the trámite's own, from `selectors.tramites_finales`) saying what confirming will do.
- **The same component also cancels, downloads and deletes.** Cancel aborts the `XMLHttpRequest` while the file is in flight (an aborted `PUT` never creates the object, so there is nothing to clean up) and disappears once the server is registering it, where there is nothing left to stop. Download goes through the server (`cuentas_cobro:descargar`, `web:documento_descargar`), which re-checks visibility and signs `ResponseContentDisposition` so the file lands with a readable name instead of the key's random prefix; a link's `download` attribute would not work, because the file is on another origin. Delete (`components/eliminar_archivo.html`, two-step confirm) removes the row and then the object, via `transaction.on_commit(lambda: default_storage.delete(...))`, never inside the transaction: a rollback would leave the row pointing at a file that no longer exists. **Delete is only offered for entrega documents** (cuentas de cobro and Proyectos), never for closing documents: see the rule below.

**Tailwind is compiled, not loaded from the CDN.** It used to be `cdn.tailwindcss.com`, which is the Tailwind *compiler*: ~400 KB of JS that scans the DOM and generates the CSS in the browser on every load. On phones that showed up as a completely unstyled page. Now:

- `proyectos/tailwind.config.js`: brand colors and fonts (was inline in `base.html`).
- `proyectos/tailwind.src.css`: the three `@tailwind` directives. Deliberately **outside** `static/`, so `collectstatic` does not publish the source.
- `proyectos/static/web/tailwind.css`: the built output (~27 KB), **committed to the repo** because there is no build step on Railway.
- `static/web/app.css` is plain CSS (no `@apply`) with the design-system classes only: `.badge`, `.btn`, `.card`, `.chip`, `.timeline`. It contains **no** layout utilities.

⚠️ **After changing Tailwind classes in any template you must rebuild and commit the CSS**, or the new class simply has no style. The standalone CLI is a single binary, no Node required:

```bash
cd proyectos
./tailwindcss -c tailwind.config.js -i tailwind.src.css -o static/web/tailwind.css --minify
uv run python manage.py collectstatic --noinput
```

**Alpine and HTMX are vendored too**, in `proyectos/static/vendor/`, with the version in the filename (`alpine-3.16.3.min.js`, `htmx-1.9.12.min.js`). They used to come from jsdelivr/unpkg on every page load, with no integrity check, on a platform that handles official documents. Serving them from the app's own domain removes that third-party dependency, and upgrading is now a deliberate, reviewable change: drop the new file in, update the `{% static %}` tags in `base.html`, re-run `collectstatic`. (They were already pinned to exact versions: Alpine was once on floating `3.x.x`, where any upstream release could break the app without a code change.)

Google Fonts is still loaded from its CDN: the only remaining external asset.

## Domain-specific rules that span multiple files

**Cuentas de cobro flow**: contratista creates `CuentaEntrega` + `DocumentoEntrega` v1 → uploads docs → **Entregar** → radicación approved by Supervisor OR Radicacion → Supervisor assigns 3 reviewers (JU/AD/TE, carried by `AsignacionRevisor`, not a group) → sequential gating técnico→jurídico→administrativo → Supervisor final decision (for signing; sets `fecha_aprobacion_supervisor`) → **Radicacion** uploads the signed closing docs (`DocumentoCierre`, `tipo_documento` FK to the catalog, same types as the entrega, now signed) → final steps `SF` (SIIFWEB, admin reviewer) → `SC` (SECOP II, Secop) → auto-close. **Any devolución = full restart** from the técnico reviewer (a new empty version, no carry-forward). **Review/radicación decisions must be coherent with the documents:** to approve, every document must be `AP`/`NA` (none `PENDIENTE`/`RECHAZADO`); to return/reject (`AJ`/`RE`), at least one document must be `RECHAZADO`; otherwise the only valid action is approve. Both directions are enforced in services (`registrar_revision`, `registrar_revision_radicacion`), not just the model. **A reviewer has only two outcomes: approve (`AP`) or return/requiere-ajustes (`AJ`);** the terminal reject (`RE`) does not apply in the reviewer stage (a return already restarts the cycle) and exists only in radicación and the supervisor's final decision, restricted in both the form and `registrar_revision`. **A radicación `RE` is terminal:** the account stays un-radicada and does not continue. Since radicación has no status field on the account, the rejection is derived from the latest `RevisionParaRadicacion` (`services.radicacion_rechazada`): `puede_radicar` then returns False (form disappears), the stepper marks the stage *Rechazada*, and radicación notifications stop (analogous to the supervisor's terminal `estado_supervisor=RE`). **A restarted (empty) version is not reviewable until the contractor re-uploads and re-delivers:** `rol_habilitado` requires `entrega_enviada` for the latest version, so after a devolución no reviewer can act (or even see the review form) until "Entregar" is pressed again.

**Nothing in the closing stage can be undone; the guard sits before the record is written.** A `TramiteFinal`'s evidence cannot be replaced and a `DocumentoCierre` cannot be deleted, so the confirmation step on every upload is what prevents the wrong file. That is deliberate: a supporting document that can be swapped after the fact weakens the evidentiary value of the file this module exists to sustain, and the confirmation costs nothing because the file has not left the user's machine yet. A mistake found after the fact is fixed through the admin. `Eventos.EVIDENCIA_REEMPLAZADA` and `Eventos.CIERRE_ELIMINADO` stay in the catalog: accounts may carry those entries, and the bitácora is never rewritten.

**A `DocumentoCierre` is never deleted.** It was allowed for a while, to fix a wrong upload, and it backfired: completing the closing set is exactly what enables the SIIFWEB trámite, so removing one disabled it again and the account looked stuck with no visible reason. `DocumentoCierre` is also **unique per (cuenta, tipo)**, so after deleting one you could not re-upload that type either. Consequence currently accepted: **a wrong closing upload cannot be fixed from the app**, only from the admin, which is why that upload carries a warning in its confirmation step. The clean way out, if it comes up again, is to replace the file without deleting the row (as `reemplazar_evidencia` does for a trámite): it never un-completes the set, so it cannot destabilize the flow, and it sidesteps the unique constraint.

- `cuentas_de_cobro/models.py` is **DEFINITIVE: do not modify.** The user edits the models directly when the spec changes; align services/selectors/views/forms/templates/admin/tests to them instead.

**Proyectos review rule** (single source: `web.selectors.responsable_revision(actividad)`): the reviewer of an activity's entrega is the **project coordinator** (`proyecto.asignado_a`) if the executor is a formulador, or the **project director** (`proyecto.creador_por`) if the executor is a coordinator. The director may assign activities to a coordinator or a formulador; the coordinator only to formuladores. This rule is applied consistently in `puede_revisar`, `entregas_por_revisar`, notifications, and dashboard metrics: keep them in sync.

## Infra / deployment

Postgres on **Supabase** via `DATABASE_URL` (`dj_database_url`, psycopg2-binary). Media in **Supabase Storage** (S3) via `django-storages`/boto3: all `FileField`s go there; bucket is private (`documentos`) with signed URLs. Static served by **WhiteNoise**. Deployed on **Railway**; `DEBUG` and `ALLOWED_HOSTS` default to off/`*`.

**Start command** (now mirrored in the repo's `Procfile`, so it stops living only in the Railway dashboard):

```
cd proyectos && uv run manage.py collectstatic --noinput && gunicorn proyectos.wsgi:application --bind 0.0.0.0:$PORT
```

Three things follow from it:

- **`collectstatic` runs on every boot**, not at build time. That is why `proyectos/staticfiles/` is gitignored: it is regenerated on each deploy. If this ever changes, that folder has to go back into the repo or every page 500s on the missing manifest.
- **`migrate` is NOT in there.** Migrations are applied by hand, so a deploy that ships a new migration boots fine and then fails at runtime when it touches the new column. Worth adding (`uv run manage.py migrate --noinput &&` before gunicorn) once you confirm there is a single replica, so two containers don't migrate at once.
- Gunicorn runs with the **default single sync worker**. Fine for the current load, but a long PDF/Excel export blocks every other request while it runs.

**The Supabase project moved accounts in Aug 2026.** The old ref `jzuhwyxzxnqosgfmpxhy` is dead; the live one is `gqsvgpchdojaufsaygdf`. Users, roles and the cuentas-de-cobro catalogs were migrated; there were no transactional rows and no stored files to move. The S3 endpoint is no longer hardcoded: it comes from `SUPABASE_S3_ENDPOINT_URL`, precisely so the next move does not require a code change.

**HTTPS hardening** lives at the end of `settings.py` behind `HTTPS_ESTRICTO` (see the env table above). `check --deploy` still reports `W005` (`includeSubDomains`) and `W021` (`preload`): those are **a deliberate decision, not an oversight**: both affect neighbouring domains and are even harder to reverse than plain HSTS.

Gotchas confirmed the hard way:
- `CSRF_TRUSTED_ORIGINS` entries must be `scheme://host` with **no trailing slash**; behind Railway's proxy you need `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`.
- Deleting a `CuentaEntrega` via the ORM fails (`RevisionCuentaCobro.asignacion` is `PROTECT`); use `TRUNCATE cuentas_de_cobro_cuentaentrega CASCADE` at the DB level to clear test data.

## UI conventions

- **Modals must use `<template x-teleport="body">`.** `<main class="page">` has a `fadeInUp` animation that creates a stacking context; a `fixed inset-0` modal inside it gets trapped under the sticky topbar (white bands). Teleporting to `<body>` fixes it.
- Django `{# … #}` comments only on **one line** (multi-line renders on screen); use `{% comment %}…{% endcomment %}` otherwise.
- **Flujogramas and step descriptions in plain Spanish, no technical terms** (any staff member should understand them); no internal field/state names in visible text.
- Brand colors (solid, no gradients, no emojis): green `#109d39`, blue `#0b72ab`, red `#d92a34`, amber `#ffa700`. Design system classes (`card`, `btn`/`btn-primary`/`btn-soft`/`btn-outline`, `badge-*`, `data` tables, `timeline`) in `static/web/app.css`.
- **State meanings are process-scoped.** In cuentas de cobro the same code (e.g. `RE`) means different things per step; `cuentas_cobro_extras.py` centralizes this in `_SIGNIFICADOS[proceso][codigo]`. Render state chips with `{% cc_badge codigo proceso="documento|radicacion|revision|revisores|supervisor|asignacion" %}` (adds a `title` tooltip) and put `{% cc_leyenda "<proceso>" %}` under the decision form (legend of every option's meaning). Keep both in sync when a state's meaning changes.

## Documentation

Living docs in `docs/`: `ModuloCuentasCobro.md` (authoritative spec of the billing module, re-read it, it wins over current code on behavior), `ModuloProyectos.md` (Proyectos requirements), `ManualRoles.md` (plain-language role manual), `ESTRUCTURA.md` (architecture), `RestriccionRoles.md` (role-isolation task). `README.md` (setup) stays at the repo root.
