Actúa como un Arquitecto de Software Senior, Desarrollador Django Senior y Diseñador Frontend/UI-UX Senior.

Tu objetivo es construir una aplicación web completa usando Django siguiendo el patrón MVT (Model-View-Template).

IMPORTANTE:

* NO modifiques los modelos existentes.
* Los modelos son la fuente de verdad del sistema.
* Debes construir la solución alrededor de los modelos existentes.
* Puedes crear vistas, formularios, templates, servicios, mixins, decoradores, utilidades, permisos, componentes frontend y estructura de navegación.
* Debes utilizar buenas prácticas de Django.
* Debes generar código mantenible y preparado para crecimiento futuro.

## Contexto del proyecto

Existe una carpeta llamada:

contexto/

Dentro de esta carpeta encontrarás:

* Manuales de usuario.
* Documentación funcional.

Debes analizar todo el contenido de la carpeta contexto antes de proponer cualquier solución.

---

## Flujo de negocio

Roles:

### Director

* Crea proyectos.
* Asigna proyectos a coordinadores.
* Supervisa avance general.

### Coordinador

* Gestiona actividades.
* Crea actividades.
* Crea subactividades.
* Asigna actividades a formuladores.
* Revisa entregas.
* Aprueba o solicita ajustes.

### Formulador

* Visualiza sus actividades.
* Registra entregas.
* Adjunta documentos.
* Atiende observaciones.
* Genera nuevas versiones cuando existan ajustes.

---

## Flujo funcional

Proyecto
→ Actividad
→ Entrega (Versión)
→ Documentos
→ Revisión

Posibles estados de actividad:

* Pendiente
* En revisión
* Requiere ajustes
* Aprobada

Flujo esperado:

1. Director crea proyecto.
2. Coordinador crea actividad.
3. Formulador realiza entrega.
4. Coordinador revisa.
5. Si requiere ajustes:

   * Se crea una nueva entrega.
6. Si se aprueba:

   * La actividad queda finalizada.

---

## Objetivo de la interfaz

NO quiero una interfaz tipo Django Admin.

Quiero una aplicación web moderna, profesional e intuitiva.

Debe parecer una aplicación empresarial desarrollada específicamente para la entidad.

Inspirarse en:

* Notion
* Jira
* Monday
* Asana
* ClickUp
* Linear

Sin copiar diseños.

---

## Requisitos de frontend

Utiliza tus habilidades de Frontend Design y UX/UI.

Diseña:

### Layout principal

* Sidebar colapsable.
* Topbar.
* Breadcrumbs.
* Menús por rol.
* Perfil de usuario.

### Dashboard

Director:

* Número de proyectos.
* Actividades por estado.
* Actividades vencidas.
* Indicadores generales.

Coordinador:

* Actividades asignadas.
* Entregas pendientes de revisión.
* Indicadores de cumplimiento.

Formulador:

* Mis actividades.
* Actividades próximas a vencer.
* Entregas recientes.

---

## Pantallas requeridas

### Proyectos

* Listado.
* Creación.
* Edición.
* Detalle.

### Actividades

* Listado.
* Detalle.
* Historial.
* Línea de tiempo.

### Entregas

* Crear entrega.
* Ver historial de versiones.
* Ver documentos asociados.

### Revisiones

* Aprobar.
* Solicitar ajustes.
* Consultar observaciones.

### Perfil

* Información básica.
* Actividad reciente.

---

## Experiencia de usuario

Implementar:

* Tarjetas.
* Tablas responsivas.
* Badges de estado.
* Alertas visuales.
* Timeline de versiones.
* Timeline de revisiones.
* Indicadores de progreso.
* Filtros avanzados.
* Buscador.

---

## Seguridad

Implementar:

* Login.
* Control de acceso por rol.
* Decoradores.
* Mixins.
* Restricciones de vistas.
* Restricciones de navegación.

Un usuario nunca debe ver información que no le corresponda.

---

## Arquitectura

Proponer:

* Estructura de carpetas.
* urls.py
* views.py
* forms.py
* services.py
* templates/
* static/
* componentes reutilizables.

---

## Resultado esperado

Antes de generar código:

1. Analiza la carpeta contexto.
2. Describe la arquitectura propuesta.
3. Diseña la navegación completa.
4. Diseña las pantallas.
5. Diseña la experiencia de usuario.
6. Luego genera el código necesario.

Prioriza una solución profesional, mantenible, escalable y con una excelente experiencia de usuario.


## Django Admin (obligatorio)

Aunque la aplicación principal debe construirse como una aplicación web completa usando vistas y templates personalizados, NO debes eliminar ni ignorar el Django Admin.

Debes construir y mantener un admin funcional utilizando Django Admin o Django Unfold.

Objetivos del admin:

* Parametrización del sistema.
* Soporte operativo.
* Auditoría.
* Corrección de datos.
* Gestión por superusuarios.
* Administración técnica.

El admin NO será la interfaz principal de negocio, pero debe quedar completamente operativo.

### Requisitos del admin

Implementar:

* ModelAdmin para todos los modelos.
* list_display optimizados.
* search_fields.
* list_filter.
* ordering.
* autocomplete_fields cuando aplique.
* fieldsets organizados.
* inlines donde tenga sentido.
* permisos por rol.
* acciones administrativas útiles.

### Gestión de parámetros

El admin debe permitir administrar fácilmente:

* Usuarios.
* Roles.
* Proyectos.
* Actividades.
* Subactividades.
* Entregas.
* Revisiones.
* Documentos.

### Filosofía

La aplicación web personalizada será utilizada por los usuarios finales.

El Django Admin será utilizado por:

* Administradores.
* Personal de soporte.
* Líderes funcionales.
* Desarrolladores.

Por lo tanto:

* La lógica de negocio principal debe estar en la aplicación web.
* El admin debe permanecer disponible como herramienta de administración y parametrización.
* No reemplazar la aplicación web con el admin.
* No diseñar la solución pensando únicamente en el admin.

La solución final debe incluir tanto:

1. Aplicación web completa para usuarios finales.
2. Admin robusto para administración y parametrización.


Me interesa que el sistema incluya dashboards específicos por rol, construidos a partir de la información disponible en los modelos existentes.

### Dashboard Director

El Director debe contar con una vista ejecutiva que le permita monitorear la gestión de los coordinadores y el estado general de los proyectos.

La información debe incluir:

* Número total de proyectos asignados a cada coordinador.
* Número total de actividades asociadas a cada proyecto.
* Número de actividades por estado:

  * Pendiente.
  * En revisión.
  * Requiere ajustes.
  * Aprobada.
* Número de entregas pendientes de revisión por coordinador.

  * Una entrega se considera pendiente de revisión cuando existe un registro en ActividadEntrega y aún no existe un registro asociado en Revisiones.
* Antigüedad de las entregas pendientes de revisión.

  * Mostrar cuántos días han transcurrido desde la fecha de creación de la entrega.
  * Identificar entregas con más de X días sin revisión.
* Ranking o listado de coordinadores con mayor cantidad de entregas pendientes.
* Indicadores de cumplimiento por proyecto.
* Actividades próximas a vencer.
* Actividades vencidas.

La vista debe permitir navegar desde los indicadores hasta el detalle de proyectos, actividades y entregas.

---

### Dashboard Coordinador

El Coordinador debe disponer de una vista operativa para gestionar el trabajo de los formuladores.

La información debe incluir:

* Proyectos bajo su responsabilidad.
* Actividades asignadas a formuladores.
* Actividades agrupadas por estado.
* Entregas pendientes de revisión.
* Entregas revisadas recientemente.
* Actividades próximas a vencer.
* Actividades vencidas.
* Tiempo promedio de revisión de entregas.
* Formuladores con mayor cantidad de actividades pendientes.

Debe existir acceso rápido para:

* Revisar entregas.
* Aprobar entregas.
* Solicitar ajustes.
* Consultar historial de versiones.

---

### Dashboard Formulador

El Formulador debe visualizar únicamente la información correspondiente a sus actividades asignadas.

La información debe incluir:

* Número total de actividades asignadas.
* Actividades agrupadas por proyecto.
* Actividades agrupadas por estado.
* Actividades pendientes de entrega.
* Actividades en revisión.
* Actividades devueltas para ajustes.
* Actividades aprobadas.
* Fecha programada y fecha de vencimiento de cada actividad.
* Días restantes para el vencimiento.
* Actividades vencidas.

Adicionalmente, mostrar indicadores de gestión personal:

* Tiempo transcurrido desde la última entrega realizada.
* Actividades sin ninguna entrega registrada.
* Actividades que requieren una nueva entrega debido a observaciones o ajustes.
* Historial de entregas por actividad.
* Historial de revisiones por actividad.

La vista debe permitir acceder rápidamente a:

* Crear una nueva entrega.
* Adjuntar documentos.
* Consultar observaciones realizadas por el coordinador.
* Revisar el historial completo de versiones y revisiones.

---

### Consideraciones generales

Los dashboards deben ser visuales y orientados a gestión.

Utilizar:

* Tarjetas de indicadores (KPI Cards).
* Gráficos de barras.
* Gráficos de estados.
* Tablas resumidas.
* Badges de estado.
* Alertas visuales para actividades vencidas o entregas atrasadas.
* Filtros por proyecto, usuario, estado y rango de fechas.

Todas las métricas deben calcularse utilizando exclusivamente la información disponible en los modelos existentes, sin modificar su estructura.

# Módulo de Reportes

Se requiere implementar una nueva sección denominada **Reportes**, accesible desde el menú principal de la aplicación.

Inicialmente se deben desarrollar los siguientes reportes.

---

# 1. Reporte de Proyectos Formulados

## Objetivo

Identificar los proyectos que han culminado su proceso de formulación.

## Regla de negocio

Un proyecto se considera **formulado** cuando **todas sus actividades se encuentran en estado "Aprobada"**.

## Información a mostrar

* Nombre del proyecto.
* Responsable del proyecto.
* Número total de actividades.
* Número de actividades aprobadas.
* Fecha de creación del proyecto.
* Fecha de última actualización.
* Estado del proyecto (Formulado).

## Filtros

* Rango de fechas.
* Responsable (Director o Coordinador).
* Proyecto.

## Salida

* Exportación a Excel (`.xlsx`).

## Consideraciones

La consulta debe excluir proyectos que tengan al menos una actividad en alguno de los siguientes estados:

* Pendiente.
* En revisión.
* Requiere ajustes.

---

# 2. Reporte de Avance por Proyecto

## Objetivo

Permitir conocer el avance de formulación de cada proyecto con base en el estado de sus actividades.

## Regla de negocio

El porcentaje de avance se calcula mediante la siguiente fórmula:

```text
Avance = (Actividades aprobadas / Total de actividades) * 100
```

### Ejemplo

* Proyecto con 10 actividades.
* 5 actividades aprobadas.

Resultado:

```text
Avance = 50%
```

## Información a mostrar

### Resumen por proyecto

* Nombre del proyecto.
* Total de actividades.
* Actividades aprobadas.
* Actividades en revisión.
* Actividades con ajustes.
* Actividades pendientes.
* Porcentaje de avance.

### Detalle de actividades

* Nombre de la actividad.
* Responsable.
* Estado actual.
* Fecha programada.
* Fecha de vencimiento.
* Fecha de creación.
* Fecha de última actualización.

## Filtros

* Fecha inicial.
* Fecha final.
* Proyecto.
* Responsable.
* Estado de actividad.

Debe ser posible generar el reporte considerando únicamente actividades que durante el período seleccionado se encuentren en alguno de los siguientes estados:

* Aprobada.
* En revisión.
* Requiere ajustes.
* Pendiente.

## Resumen ejecutivo

Al inicio del reporte incluir:

* Total de proyectos analizados.
* Total de actividades.
* Actividades aprobadas.
* Actividades en revisión.
* Actividades con ajustes.
* Actividades pendientes.
* Porcentaje promedio de avance.

## Salida

* Generación en PDF.

## Consideraciones

El PDF debe incluir:

* Encabezado institucional.
* Numeración de páginas.
* Fecha de generación.
* Usuario que generó el reporte.
* Tabla resumen por proyecto.
* Tabla detallada de actividades.

---

# Consideraciones Técnicas

* Implementar una vista independiente para reportes.
* Los filtros deben mostrarse mediante formularios.
* La generación de archivos debe realizarse bajo demanda.
* Utilizar consultas optimizadas (`select_related` y `prefetch_related`) para evitar problemas de rendimiento.
* Mantener separación entre lógica de negocio, consultas y generación de archivos.
* La solución debe ser escalable para la incorporación de futuros reportes.

---

# Criterios de Aceptación

## Reporte de Proyectos Formulados

* Solo deben aparecer proyectos cuyas actividades se encuentren 100% aprobadas.
* El archivo debe generarse correctamente en formato Excel.
* Los filtros deben aplicarse antes de la generación del archivo.

## Reporte de Avance por Proyecto

* El porcentaje de avance debe calcularse correctamente.
* Debe mostrarse tanto el resumen por proyecto como el detalle de actividades.
* El PDF debe generarse correctamente respetando los filtros seleccionados.
* El resumen ejecutivo debe reflejar únicamente la información filtrada.
