# Pendientes y estado del proyecto

Última actualización: 2026-08-26. Documento de traspaso: resume qué se hizo, qué
quedó pendiente y con qué evidencia, para poder retomar en frío.

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

- **PR #1** — Tailwind compilado en lugar del CDN, y el endpoint S3 movido a
  variable de entorno.
- **PR #2** — endurecimiento HTTPS y cuatro guardas de negocio en cuentas de cobro.

Los detalles de ambos están en los mensajes de esos commits, que son extensos a
propósito. `CLAUDE.md` recoge lo que quedó siendo permanentemente cierto
(variables de entorno, flujo de compilación de Tailwind, avisos sobre las pruebas).

---

## Pendiente 1 — N+1 de consultas (prioridad alta)

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

La causa 3 es la de mejor relación esfuerzo/beneficio: cachear los grupos en el
objeto `request.user` durante la petición elimina esas 10 de una. Para 1 y 2 hay
que precalcular por lote (una consulta por tabla para toda la página) en vez de
por fila.

Ojo al tocarlo: `paso_actual` y `flujo_de_cuenta` alimentan el stepper y la
columna "responsable" de la bandeja. Hay que conservar el comportamiento exacto,
incluidos los casos de cuenta rechazada en radicación y cuenta cerrada.

---

## Pendiente 2 — Las pruebas escriben en el bucket real (prioridad alta)

El storage por defecto es S3, así que **cualquier prueba que guarde un
`FileField` sube el archivo a la Supabase de producción** cuando hay credenciales
en el entorno (y falla con `NoCredentialsError` cuando no las hay: así se
descubrió, 51 de 87 pruebas en error).

Ya protegidos, vía `@override_settings(STORAGES=...)` con `InMemoryStorage`:

- `web/tests.py`
- `cuentas_de_cobro/tests/test_guardas.py`

**Sin proteger**: `cuentas_de_cobro/tests/test_services.py` y
`cuentas_de_cobro/tests/test_views.py`.

El arreglo correcto no es repetir el decorador en cada clase, sino un módulo de
settings de prueba (`proyectos/test_settings.py`) que herede de `proyectos.settings`
y fije SQLite + `InMemoryStorage`, de modo que ninguna ruta pueda tocar producción
por accidente. Comando a documentar en `CLAUDE.md` una vez exista.

Mientras tanto, para correr el suite sin riesgo hace falta un módulo externo con
ese override; con SQLite y storage en memoria las **96 pruebas pasan** (~110 s,
casi todo hashing de contraseñas: un hasher rápido en pruebas lo bajaría a segundos).

---

## Pendiente 3 — Aislamiento de dominios asimétrico (prioridad media)

Las vistas de `web` (dominio Proyectos) solo llevan `LoginRequiredMixin`, sin
mixin de rol. Comprobado: un usuario **Contratista** (que pertenece solo al
dominio de cuentas de cobro) recibe **200** en `/proyectos/`, `/actividades/`,
`/reportes/` y `/`, y puede descargar el Excel de reportes.

**No hay fuga de datos**: los selectores devuelven `.none()` para ese rol y el
Excel sale vacío. Pero el módulo de cuentas de cobro sí responde 403 en el caso
simétrico, y la regla escrita en `CLAUDE.md` dice que un rol de un dominio nunca
accede a las vistas del otro. Es una inconsistencia entre lo documentado y lo
implementado: hay que decidir cuál de las dos se ajusta.

---

## Pendiente 4 — Detalles menores

- **Validación de archivos subidos**: ningún formulario con `FileField` valida
  extensión ni tamaño (`DocumentoForm`, `DocumentoCuentaForm`,
  `DocumentoCierreForm`, `TramiteFinalForm`).
- **Reportes que ignoran filtros inválidos en silencio**: `web/views.py`, en
  `reporte_formulados_excel` y `reporte_avance_pdf`, hace
  `form.cleaned_data if form.is_valid() else {}`. Un filtro mal formado genera el
  reporte completo en vez de avisar del error.
- **`CuentaEntrega.__str__` muestra el mes como número** ("2026: 6"), y eso
  aparece en migas de pan y títulos. Debería usar `get_mes_display()`, pero
  `models.py` es intocable: hay que resolverlo en plantilla o con un filtro.
- **`django-import-export`** está declarado como dependencia y no se usa en
  ninguna parte.
- **`psycopg2`** (compilación desde fuente) en vez de `psycopg2-binary`: el
  entorno no se puede instalar sin `libpq-dev`.
- **`django-unfold` sin tope de versión** (`>=0.97.0`). Si un build resuelve una
  versión más nueva y no se corre `collectstatic`, el `/admin/` completo devuelve
  500 por `Missing staticfiles manifest entry`. Con `collectstatic` en el build no
  pasa, pero fijar la versión lo elimina como riesgo.
- **HSTS en 1 hora**: subir `SECURE_HSTS_SECONDS` a `31536000` cuando se haya
  verificado que el sitio va estable por HTTPS.
- `check --deploy` reporta `W005` y `W021` a propósito (ver `CLAUDE.md`).

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
