# Manual de roles

Este documento explica, en lenguaje sencillo, **qué hace cada rol** en la
plataforma: qué puede ver y hacer (permisos), en qué procesos participa, qué
acciones realiza en cada paso y **qué ocurre después** de cada acción (qué se
pone en marcha automáticamente).

La plataforma tiene **dos aplicaciones independientes**:

1. **Proyectos** — gestión de proyectos, actividades y subactividades.
2. **Cuentas de cobro** — el trámite mensual de la cuenta de cobro de los contratistas.

> **Importante: las dos aplicaciones están separadas.** Los roles **no se cruzan**.
> Quien tiene un rol de Proyectos solo ve y usa la aplicación de Proyectos; quien
> tiene un rol de Cuentas de cobro solo ve y usa la de Cuentas de cobro. En el
> menú de la izquierda cada persona ve **únicamente** los módulos de su
> aplicación, y si intenta entrar a una sección de la otra, el sistema se lo
> impide. (El administrador del sistema es la única excepción: puede ver todo.)

---

# Aplicación 1 · Proyectos

El flujo va de arriba hacia abajo: el **Director** crea el proyecto y se lo
entrega a un **Coordinador**; el Coordinador lo organiza en actividades y se las
reparte a los **Formuladores**; el Formulador hace el trabajo y lo entrega; el
Coordinador lo revisa y lo aprueba.

```
Director  →  Coordinador  →  Formulador  →  (revisa) Coordinador  →  Fin
```

## Director

**Quién es:** la persona que abre los proyectos y decide quién los gestiona.

**Qué puede hacer (permisos):**
- Crear y editar proyectos.
- Asignar cada proyecto a un coordinador.
- Ver los proyectos, sus actividades y el avance.
- Consultar los reportes.

**En qué procesos participa y qué hace:**
1. **Crear el proyecto.** Registra el proyecto con sus datos.
2. **Asignar un coordinador.** Elige al coordinador responsable de gestionarlo.

**Qué se activa cuando actúa:**
- Al **asignar el proyecto a un coordinador**, a ese coordinador le aparece un
  aviso de “Proyecto asignado: agrega sus actividades”. El proyecto queda a la
  espera de que el coordinador lo organice.

## Coordinador

**Quién es:** la persona que recibe un proyecto, lo divide en actividades y
revisa el trabajo de los formuladores.

**Qué puede hacer (permisos):**
- Ver los proyectos que le asignaron.
- Crear actividades dentro de esos proyectos, ponerles fecha de entrega y
  asignárselas a un formulador.
- Dividir una actividad grande en subactividades (sus pasos más pequeños).
- Revisar las entregas de los formuladores y aprobarlas o pedir correcciones.

**En qué procesos participa y qué hace:**
1. **Organizar el proyecto.** Crea las actividades, les pone plazo y se las
   asigna a un formulador. Si una actividad es grande, la divide en
   subactividades.
2. **Revisar la entrega.** Cuando un formulador entrega, el coordinador revisa y
   decide:
   - **Aprobada** → la actividad queda terminada.
   - **Requiere ajustes** o **Rechazada** → vuelve al formulador para que corrija.

**Qué se activa cuando actúa:**
- Al **asignar una actividad** a un formulador, a ese formulador le llega el aviso
  “Actividad asignada: realízala y entrégala”.
- Al **aprobar** una entrega, la actividad pasa a **terminada** y el proyecto
  avanza.
- Al **pedir ajustes o rechazar**, la actividad vuelve al formulador, a quien le
  llega el aviso “Te pidieron ajustes: corrige y vuelve a entregar”.

## Formulador

**Quién es:** la persona que ejecuta las actividades y las entrega.

**Qué puede hacer (permisos):**
- Ver las actividades que le asignaron.
- Realizarlas y entregarlas con sus documentos de soporte.
- Corregir y volver a entregar cuando le piden ajustes.

**En qué procesos participa y qué hace:**
1. **Realizar la actividad.** Hace el trabajo de la actividad (y de sus
   subactividades, si las tiene).
2. **Entregar.** Sube la entrega con los documentos de soporte.
3. **Corregir.** Si le piden ajustes, corrige y vuelve a entregar.

**Qué se activa cuando actúa:**
- Al **entregar**, la actividad pasa a **“En revisión”** y al coordinador le
  llega el aviso “Actividad por revisar”.
- Cada vez que vuelve a entregar después de una corrección, se repite la revisión
  del coordinador. Esto se repite hasta que la actividad quede aprobada.

## Avisos automáticos en Proyectos

Además de lo anterior, el sistema avisa de los **plazos**: cuando la fecha de
entrega de una actividad está por cumplirse (o ya se venció), al responsable de
esa actividad le aparece un aviso de plazo.

---

# Aplicación 2 · Cuentas de cobro

El flujo va así: el **Contratista** arma su cuenta del mes y la entrega; alguien
de **Radicación o el Supervisor** hace la primera revisión; el **Supervisor**
nombra a tres **Revisores** (jurídico, administrativo y técnico) que revisan en
orden; el **Supervisor** da la decisión final; el **Contratista** sube los
documentos de cierre; y por último se hacen tres **pasos finales** (Radicación,
Revisor administrativo y Secop) que cierran la cuenta.

```
Contratista → (1ª revisión) Radicación/Supervisor → Supervisor nombra revisores
→ Revisores: jurídico → administrativo → técnico → Supervisor (decisión final)
→ Contratista (documentos de cierre) → Radicación → Rev. administrativo → Secop → Cierre
```

> **Regla clave:** si en cualquier revisión piden correcciones o no aprueban, la
> cuenta **vuelve al contratista** y, cuando él corrige, **todo empieza de nuevo
> desde la primera revisión**. No se guarda nada de la vuelta anterior: el
> contratista sube otra vez todos los documentos.

## Contratista

**Quién es:** la persona que cobra y arma su cuenta de cobro cada mes.

**Qué puede hacer (permisos):**
- Crear su cuenta de cobro del mes.
- Subir los documentos que se le piden.
- Entregar la cuenta para que la revisen.
- Más adelante, subir los documentos de cierre.
- Ver únicamente **sus propias** cuentas.

**En qué procesos participa y qué hace:**
1. **Crear la cuenta** del mes (vigencia y mes).
2. **Subir los documentos** obligatorios.
3. **Entregar.** Cuando están todos, presiona **Entregar** para mandarlos a revisión.
4. **Corregir** (si le devuelven la cuenta): sube de nuevo todos los documentos y
   vuelve a entregar.
5. **Subir los documentos de cierre** (informe de supervisión y certificado de
   cumplimiento) cuando el supervisor aprueba.

**Qué se activa cuando actúa:**
- Al **entregar**, la cuenta queda lista para la primera revisión y la pueden ver
  Radicación y el Supervisor.
- Al **subir los documentos de cierre completos**, se habilitan los pasos finales.

## Radicación

**Quién es:** el área que hace la primera revisión de los documentos y, al final,
confirma la entrega de los documentos firmados.

**Qué puede hacer (permisos):**
- Hacer la **primera revisión** de la cuenta (igual que el supervisor).
- Marcar el estado de los documentos durante esa primera revisión.
- Responder el **primer paso final** (entrega de documentos al contratista).

**En qué procesos participa y qué hace:**
1. **Primera revisión.** Revisa los documentos que entregó el contratista y decide:
   - **Aprobado** → la cuenta sigue adelante.
   - **Requiere ajustes** → vuelve al contratista para corregir.
   - **Rechazado** → deja anotado que algún documento no corresponde.
2. **Primer paso final.** Al terminar todo, confirma que los documentos firmados
   se le entregaron al contratista.

**Qué se activa cuando actúa:**
- Al **aprobar** la primera revisión, la cuenta queda lista para que el supervisor
  nombre a los revisores.
- Al **pedir ajustes**, la cuenta vuelve al contratista y el trámite reinicia.
- Al **confirmar el primer paso final**, se habilita el siguiente paso final.

## Supervisor

**Quién es:** quien coordina la revisión, nombra a los revisores y da la decisión
final.

**Qué puede hacer (permisos):**
- Hacer la **primera revisión** (igual que Radicación).
- **Nombrar a los tres revisores** (jurídico, administrativo y técnico).
- **Reasignar** un rol a otra persona si hace falta.
- Dar la **decisión final** de la cuenta.

**En qué procesos participa y qué hace:**
1. **Primera revisión** (opcional, puede hacerla también Radicación).
2. **Nombrar revisores.** Elige a las tres personas que van a revisar.
3. **Reasignar.** Si una persona no puede revisar, pone a otra en ese rol.
4. **Decisión final**, cuando los tres revisores ya aprobaron:
   - **Aprobado** → da el visto bueno para firmar los documentos de cierre.
   - **Rechazado** → la cuenta no continúa (fin).

**Qué se activa cuando actúa:**
- Al **nombrar a los revisores**, empieza la revisión en orden (primero el jurídico).
- Al **aprobar** la decisión final, le toca al contratista subir los documentos
  de cierre.
- Al **rechazar**, la cuenta se cierra sin continuar.

## Revisores (jurídico, administrativo y técnico)

**Quiénes son:** tres personas que revisan la cuenta **una después de otra**. El
rol de cada una (jurídico, administrativo o técnico) lo define el supervisor al
nombrarlas.

**Qué pueden hacer (permisos):**
- Revisar la cuenta **cuando es su turno**.
- Marcar el estado de los documentos durante su revisión.
- **Declinar** su asignación (avisar que no pueden revisarla) indicando el motivo.

**En qué procesos participan y qué hacen:**
1. **Revisión en orden.** Cada persona revisa solo cuando la anterior ya aprobó:
   - **1.º** la revisión **jurídica**,
   - **2.º** la **administrativa**,
   - **3.º** la **técnica**.
   En su turno deciden: **Aprobado** (pasa al siguiente) o **piden ajustes / no
   aprueban** (la cuenta se devuelve).
2. **Declinar** (si no pueden): el supervisor pondrá a otra persona en ese rol.

**Qué se activa cuando actúan:**
- Al **aprobar** el jurídico, se habilita el administrativo; al aprobar el
  administrativo, se habilita el técnico.
- Cuando **los tres aprueban**, la cuenta pasa sola a la decisión del supervisor.
- Si **cualquiera pide ajustes o no aprueba**, la cuenta vuelve al contratista y,
  al corregir, la revisión **empieza de nuevo desde el jurídico**.
- Además, el **revisor administrativo** tiene a su cargo uno de los pasos finales
  (ver abajo): confirmar que la cuenta se subió al sistema SIIFWEB.

## Secop

**Quién es:** el área que publica la cuenta en el portal SECOP II al final del
proceso.

**Qué puede hacer (permisos):**
- Responder el **último paso final** (publicación en SECOP II).

**En qué procesos participa y qué hace:**
1. **Último paso final.** Confirma que la cuenta se publicó en el portal SECOP II.

**Qué se activa cuando actúa:**
- Al confirmar este paso, se completan los tres pasos finales y **la cuenta se
  cierra sola**.

## Los tres pasos finales (en orden)

Después de que el contratista sube los documentos de cierre, se hacen tres pasos,
**uno después de otro**, y cada uno con su soporte:

1. **Radicación** — confirma que los documentos firmados se entregaron al contratista.
2. **Revisor administrativo** — confirma que la cuenta se subió al sistema SIIFWEB.
3. **Secop** — confirma que la cuenta se publicó en el portal SECOP II.

Cuando se cumplen los tres, **el sistema cierra la cuenta automáticamente**. Fin
del proceso.

## Avisos automáticos en Cuentas de cobro

A cada persona le aparecen avisos según lo que le toca: cuando le asignan una
cuenta para revisar, cuando hay algo pendiente de revisar, cuando una cuenta fue
devuelta y cuando algo queda aprobado. Así cada quien sabe qué le toca hacer sin
tener que estar buscando.

---

## Resumen rápido

| Aplicación | Rol | En una frase |
|---|---|---|
| Proyectos | **Director** | Crea proyectos y los asigna a un coordinador. |
| Proyectos | **Coordinador** | Organiza el proyecto en actividades y revisa las entregas. |
| Proyectos | **Formulador** | Realiza las actividades y las entrega. |
| Cuentas de cobro | **Contratista** | Arma su cuenta del mes, la entrega y sube el cierre. |
| Cuentas de cobro | **Radicación** | Hace la primera revisión y confirma la entrega final de documentos. |
| Cuentas de cobro | **Supervisor** | Nombra revisores y da la decisión final. |
| Cuentas de cobro | **Revisores** | Revisan en orden: jurídico, administrativo y técnico. |
| Cuentas de cobro | **Secop** | Publica la cuenta en SECOP II y se cierra el proceso. |
