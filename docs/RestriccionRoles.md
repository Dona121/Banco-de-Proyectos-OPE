# Tarea puntual: aislación de acceso por rol entre apps

El módulo de cuentas de cobro ya está implementado. Esta es la **única** tarea
pendiente y debe ser quirúrgica: **no rehagas ni refactorices nada más**, no toques
modelos, servicios ni el flujo. Solo control de acceso.

## Objetivo
Garantizar **aislación total entre las dos apps**: este módulo (cuentas de cobro) y
la otra app de gestión (`contenido`). Los roles NO se cruzan. Cada rol ve únicamente
las vistas de su propia app.

- Roles del módulo: `CONTRATISTA`, `SUPERVISOR`, `REVISOR`, `RADICACION`, `SECOP`.
- Roles de la otra app: `DIRECTOR`, `COORDINADOR`, `FORMULADOR`.
- Sin excepciones ni cruces: `SUPERVISOR` (módulo) y `DIRECTOR` (otra app) quedan
  separados. Ningún rol de una app accede a las vistas de la otra.

## Cómo (reutiliza lo existente — no inventes infraestructura)
Ya existe el sistema de permisos: `cuentas.mixins.RolRequeridoMixin` (login + bypass
superusuario + `tiene_rol`), los mixins por rol del módulo
(`ContratistaRequeridoMixin`, `SupervisorRequeridoMixin`, `RevisorRequeridoMixin`,
`RadicacionRequeridoMixin`, `SecopRequeridoMixin`) y `ModuloRequeridoMixin` para
vistas de consulta compartidas.

- NO construyas middleware ni una capa nueva de permisos.
- NO crees ni modifiques `roles.py`/`mixins.py` salvo que falte un mixin necesario.
- NO toques la otra app (`contenido`): su lógica y sus vistas se quedan como están.

La aislación se logra asegurando que **toda vista del módulo** lleve su mixin de rol:
- Vistas de **acción** (entregar, revisar, asignar/reasignar, decidir supervisor,
  cargar cierre, trámites finales `EC`/`SF`/`SC`) → el mixin del rol que actúa.
- Vistas de **consulta compartida** (listado de cuentas, detalle, línea de tiempo,
  "Información flujo de cuenta", panel de notificaciones) → `ModuloRequeridoMixin`.

Una vista del módulo sin mixin es el único hueco por el que se cruzaría un rol ajeno:
no debe quedar ninguna.

## Trabajo a realizar
1. **Auditoría:** recorre TODAS las vistas del módulo de cuentas de cobro (CBV y FBV;
   incluye vistas parciales/HTMX, endpoints de notificaciones y del botón de flujo) y
   reporta cuáles ya tienen mixin de rol y cuáles no. Para las FBV, verifica el
   decorador equivalente.
2. **Corrección:** aplica a cada vista sin proteger el mixin correcto según la
   clasificación acción/consulta de arriba. Si una vista de acción admite varios
   roles legítimos (p. ej. la aprobación de radicación la hacen `SUPERVISOR` o
   `RADICACION`), usa un mixin con esos roles del módulo —y solo del módulo—.
3. **Verificación:** confirma que las vistas de la otra app no sean accesibles por
   roles del módulo y viceversa (basta con que cada app gatee sus vistas con sus
   propios roles; no agregues roles cruzados a ningún mixin).

## Entregable
- Reporte de la auditoría (vista → estado previo → mixin aplicado).
- Los cambios mínimos en las vistas del módulo.
- Si hace falta un mixin que no exista (p. ej. radicación+supervisor para la
  aprobación de radicación), créalo en el `mixins.py` del módulo reutilizando
  `RolRequeridoMixin`; nada más.

## Antes de empezar
Devuélveme primero la **auditoría** (paso 1) y los mixins que piensas usar/crear. No
apliques cambios hasta que confirme.