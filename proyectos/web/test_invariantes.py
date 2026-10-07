"""Invariantes del dominio Proyectos, atacados por la vía de servicio.

No duplican lo de `tests.py`, que recorre el camino bueno: aquí se SALTA la
vista y se llama al servicio o al modelo directamente, que es lo que haría el
admin, un comando de gestión, una tarea futura o dos peticiones simultáneas. Lo
que aguante aquí es lo que el sistema garantiza de verdad; lo que no, era una
regla que vivía solo en la plantilla.

Cinco de estas pruebas nacieron fallando: las reglas de "quién puede revisar
qué" estaban en la vista y el servicio las aceptaba todas.
"""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from contenido.models import (
    Actividades,
    ActividadEntrega,
    Documentos,
    Proyectos,
    Revisiones,
)
from cuentas import validadores
from cuentas.models import ExtensionArchivo
from web import metrics, selectors, services

Estado = Actividades.EstadoActividad
Resultado = Revisiones.ResultadoRevision


class Base(TestCase):
    def setUp(self):
        self.director = self._user("dir", "Director")
        self.coord = self._user("coord", "Coordinador")
        self.form = self._user("form", "Formulador")
        self.otro = self._user("otro", "Formulador")
        self.proyecto = Proyectos.objects.create(
            nombre="P", creador_por=self.director, asignado_a=self.coord
        )

    def _user(self, u, grupo):
        user = User.objects.create_user(u, password="x")
        user.groups.add(Group.objects.get(name=grupo))
        return user

    def _act(self, estado=Estado.PENDIENTE, asignado_a=None):
        ahora = timezone.now()
        return Actividades.objects.create(
            proyecto=self.proyecto, nombre="act", fecha_programada=ahora,
            fecha_vencimiento=ahora + timedelta(days=3), estado=estado,
            asignado_por=self.director, asignado_a=asignado_a or self.form,
        )

    def _doc(self, entrega):
        return Documentos.objects.create(
            actividad_entrega=entrega, nombre="d",
            archivo=SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        )

    def _entregada(self):
        act = self._act()
        e = services.crear_entrega(act, self.form, "v1")
        self._doc(e)
        services.realizar_entrega(e, self.form)
        act.refresh_from_db()
        return act, e


class RevisionSondeo(Base):
    def test_no_se_revisa_un_borrador_por_servicio(self):
        """La vista lo impide; ¿y el servicio?"""
        act = self._act()
        e = services.crear_entrega(act, self.form, "v1")
        with self.assertRaises(ValidationError):
            services.registrar_revision(e, self.coord, Resultado.APROBADA, "ok")
        act.refresh_from_db()
        self.assertEqual(act.estado, Estado.PENDIENTE)

    def test_no_se_revisa_dos_veces_la_misma_entrega(self):
        """Doble envío del formulario / dos revisores a la vez."""
        act, e = self._entregada()
        services.registrar_revision(e, self.coord, Resultado.AJUSTES, "1")
        with self.assertRaises(ValidationError):
            services.registrar_revision(e, self.coord, Resultado.APROBADA, "2")
        self.assertEqual(Revisiones.objects.filter(actividad_entrega=e).count(), 1)

    def test_el_ejecutor_no_revisa_su_propia_entrega(self):
        """Caso límite: quien ejecuta es también quien revisaría."""
        act = self._act(asignado_a=self.coord)   # el coordinador del proyecto ejecuta
        # y además es el director del proyecto: se revisaría a sí mismo.
        self.proyecto.creador_por = self.coord
        self.proyecto.save(update_fields=["creador_por"])
        act.refresh_from_db()
        e = services.crear_entrega(act, self.coord, "v1")
        self._doc(e)
        services.realizar_entrega(e, self.coord)
        self.assertFalse(selectors.puede_revisar(self.coord, e))
        with self.assertRaises(ValidationError):
            services.registrar_revision(e, self.coord, Resultado.APROBADA, "me apruebo")

    def test_no_se_revisa_una_version_reemplazada(self):
        act = self._act()
        vieja = services.crear_entrega(act, self.form, "v1")
        self._doc(vieja)
        nueva = ActividadEntrega(actividad=act, usuario=self.form, comentario="v2")
        nueva.save()
        act.estado = Estado.EN_REVISION
        act.save(update_fields=["estado"])
        with self.assertRaises(ValidationError):
            services.registrar_revision(vieja, self.coord, Resultado.APROBADA, "ok")


class EntregaSondeo(Base):
    def test_no_se_entrega_dos_veces(self):
        act, e = self._entregada()
        with self.assertRaises(ValidationError):
            services.realizar_entrega(e, self.form)

    def test_no_se_abre_una_entrega_con_la_actividad_en_revision(self):
        act, _ = self._entregada()
        with self.assertRaises(ValidationError):
            services.crear_entrega(act, self.form, "v2")

    def test_no_se_abre_una_entrega_sobre_una_actividad_aprobada(self):
        act = self._act(estado=Estado.APROBADA)
        with self.assertRaises(ValidationError):
            services.crear_entrega(act, self.form, "v1")

    def test_un_ajeno_no_entrega_lo_de_otro(self):
        act = self._act()
        e = services.crear_entrega(act, self.form, "v1")
        self._doc(e)
        with self.assertRaises(ValidationError):
            services.realizar_entrega(e, self.otro)

    def test_quitar_el_ultimo_documento_impide_entregar(self):
        act = self._act()
        e = services.crear_entrega(act, self.form, "v1")
        d = self._doc(e)
        d.delete()
        with self.assertRaises(ValidationError):
            services.realizar_entrega(e, self.form)

    def test_el_servicio_no_abre_un_segundo_borrador(self):
        act = self._act()
        services.crear_entrega(act, self.form, "v1")
        with self.assertRaises(ValidationError):
            services.crear_entrega(act, self.form, "v2")
        self.assertEqual(act.actividadentrega_set.count(), 1)

    def test_un_segundo_borrador_creado_a_mano_queda_contenido(self):
        """Hasta dónde llega la garantía, dicho sin adornos.

        Guardar el modelo directamente SÍ crea una segunda versión: la guarda
        está en el servicio, y `contenido/models.py` es definitivo, así que no
        hay restricción de base que lo impida. Lo que sí se garantiza es que el
        desorden no se propaga: solo la versión vigente cuenta, y la anterior
        queda inalcanzable en vez de editable.

        Es la situación que dejó el flujo anterior en los datos reales (una
        actividad con tres entregas sin revisar a la vez).
        """
        act = self._act()
        v1 = services.crear_entrega(act, self.form, "v1")
        v2 = ActividadEntrega(actividad=act, usuario=self.form, comentario="v2")
        v2.save()

        self.assertEqual(selectors.borrador_de(act), v2)        # solo la vigente
        self.assertFalse(selectors.puede_documentar(self.form, v1))
        self.assertFalse(selectors.puede_revisar(self.coord, v1))
        self.assertNotIn(v1, selectors.entregas_por_revisar(self.coord))
        with self.assertRaises(ValidationError):
            services.realizar_entrega(v1, self.form)


class ExtensionSondeo(TestCase):
    def test_no_admite_doble_extension(self):
        with self.assertRaises(ValidationError):
            ExtensionArchivo(extension="tar.gz").full_clean()

    def test_no_admite_separadores_de_ruta(self):
        for valor in ("../pdf", "a/b", "a\\b", ".", "..", ""):
            with self.subTest(valor=valor):
                with self.assertRaises(ValidationError):
                    ExtensionArchivo(extension=valor).full_clean()

    def test_no_se_duplica_con_otra_caja(self):
        ExtensionArchivo.objects.create(extension="tiff")
        with self.assertRaises(ValidationError):
            ExtensionArchivo.objects.create(extension="TIFF")

    def test_el_nombre_no_escapa_del_prefijo(self):
        for malo in ("../../etc/passwd.pdf", "..\\..\\x.pdf", "/abs/ruta.pdf"):
            with self.subTest(malo=malo):
                seguro = validadores.nombre_seguro(malo)
                self.assertNotIn("/", seguro)
                self.assertNotIn("\\", seguro)
                self.assertFalse(seguro.startswith("."))

    def test_una_extension_doble_no_cuela_un_ejecutable(self):
        with self.assertRaises(ValidationError):
            validadores.validar_extension("factura.pdf.exe")

    def test_sin_extension_se_rechaza(self):
        for nombre in ("archivo", "", ".", ".pdf."):
            with self.subTest(nombre=nombre):
                with self.assertRaises(ValidationError):
                    validadores.validar_extension(nombre)


class AccesoPorIdAjenoSondeo(Base):
    """Referencia directa a objetos: adivinar el id de otro y usarlo.

    Es el ataque más barato que existe contra un CRUD: cambiar el número de la
    URL. Las vistas parten de los selectores, que ya filtran por rol, pero eso
    hay que comprobarlo, no suponerlo.
    """

    def setUp(self):
        super().setUp()
        # Proyecto ajeno, con su propio equipo: nada en común con self.form.
        self.dir2 = self._user("dir2", "Director")
        self.coord2 = self._user("coord2", "Coordinador")
        self.ajeno = self._user("ajeno", "Formulador")
        self.proyecto2 = Proyectos.objects.create(
            nombre="P2", creador_por=self.dir2, asignado_a=self.coord2
        )
        ahora = timezone.now()
        self.act_ajena = Actividades.objects.create(
            proyecto=self.proyecto2, nombre="ajena", fecha_programada=ahora,
            fecha_vencimiento=ahora + timedelta(days=3), estado=Estado.PENDIENTE,
            asignado_por=self.dir2, asignado_a=self.ajeno,
        )
        self.entrega_ajena = services.crear_entrega(
            self.act_ajena, self.ajeno, "suya"
        )
        self.doc_ajeno = self._doc(self.entrega_ajena)

    def test_no_se_ve_la_entrega_de_otro(self):
        self.client.force_login(self.form)
        self.assertEqual(
            self.client.get(f"/entregas/{self.entrega_ajena.pk}/").status_code, 404
        )

    def test_no_se_descarga_el_documento_de_otro(self):
        self.client.force_login(self.form)
        self.assertEqual(
            self.client.get(f"/documentos/{self.doc_ajeno.pk}/descargar/").status_code,
            404,
        )

    def test_no_se_borra_el_documento_de_otro(self):
        self.client.force_login(self.form)
        resp = self.client.post(f"/documentos/{self.doc_ajeno.pk}/eliminar/")
        self.assertIn(resp.status_code, (403, 404))
        self.assertTrue(Documentos.objects.filter(pk=self.doc_ajeno.pk).exists())

    def test_no_se_sube_a_la_entrega_de_otro(self):
        self.client.force_login(self.form)
        for ruta, datos in (
            (f"/entregas/{self.entrega_ajena.pk}/subir/firmar/",
             {"nombre_documento": "X", "nombre": "a.pdf"}),
            (f"/entregas/{self.entrega_ajena.pk}/subir/confirmar/", {"token": "x"}),
            (f"/entregas/{self.entrega_ajena.pk}/documentos/nuevo/", {}),
        ):
            with self.subTest(ruta=ruta):
                self.assertIn(self.client.post(ruta, datos).status_code, (403, 404))

    def test_no_se_realiza_la_entrega_de_otro(self):
        self.client.force_login(self.form)
        resp = self.client.post(f"/entregas/{self.entrega_ajena.pk}/realizar/")
        self.assertIn(resp.status_code, (403, 404))
        self.act_ajena.refresh_from_db()
        self.assertEqual(self.act_ajena.estado, Estado.PENDIENTE)

    def test_no_se_revisa_la_entrega_de_otro_proyecto(self):
        self._doc(self.entrega_ajena)
        services.realizar_entrega(self.entrega_ajena, self.ajeno)
        self.client.force_login(self.coord)       # coordinador del OTRO proyecto
        resp = self.client.post(
            f"/entregas/{self.entrega_ajena.pk}/revisar/",
            {"resultado": Resultado.APROBADA, "comentario": "me cuelo"},
        )
        self.assertIn(resp.status_code, (403, 404))
        self.assertFalse(Revisiones.objects.exists())

    def test_no_se_entrega_en_la_actividad_de_otro(self):
        self.client.force_login(self.form)
        resp = self.client.post(
            f"/actividades/{self.act_ajena.pk}/entregas/nueva/", {"comentario": "x"}
        )
        self.assertIn(resp.status_code, (403, 404))


class CapasDeValidacionTest(Base):
    """Las dos redes que hay debajo de los servicios.

    1. La señal `pre_save` de `apps.py` enciende los `clean()` que los modelos
       definitivos declaran pero no ejecutan (no sobrescriben `save()`).
    2. Las restricciones `CHECK` de la base, para lo incondicional: lo único que
       tampoco salta el SQL crudo.
    """

    def test_la_senal_aplica_el_clean_dormido_de_actividades(self):
        """`Actividades.clean()` existía y solo corría vía formulario."""
        ahora = timezone.now()
        with self.assertRaises(ValidationError):
            Actividades.objects.create(
                proyecto=self.proyecto, nombre="imposible",
                fecha_programada=ahora + timedelta(days=5),   # después del vencimiento
                fecha_vencimiento=ahora,
                estado=Estado.PENDIENTE,
                asignado_por=self.director, asignado_a=self.form,
            )

    def test_la_senal_tambien_cubre_una_actualizacion(self):
        act = self._act()
        act.fecha_vencimiento = act.fecha_programada - timedelta(days=1)
        with self.assertRaises(ValidationError):
            act.save(update_fields=["fecha_vencimiento"])

    def test_el_estado_de_la_actividad_no_admite_un_valor_inventado(self):
        act = self._act()
        act.estado = "ZZ"
        with self.assertRaises(ValidationError):
            act.save(update_fields=["estado"])

    def test_la_restriccion_de_base_resiste_al_sql_crudo(self):
        """La única capa que no se puede esquivar saltándose el ORM."""
        from django.db import IntegrityError, connection

        act = self._act()
        with self.assertRaises(IntegrityError):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    "UPDATE contenido_actividades SET fecha_vencimiento = "
                    "fecha_programada - 1 WHERE id = %s",
                    [act.pk],
                )

    def test_la_version_de_una_entrega_no_puede_ser_cero(self):
        from django.db import IntegrityError, connection

        act = self._act()
        e = services.crear_entrega(act, self.form, "v1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(
                    "UPDATE contenido_actividadentrega SET numero_version = 0 "
                    "WHERE id = %s",
                    [e.pk],
                )


class ConteoDeActividadesTest(Base):
    """El número de la tarjeta de proyecto cuenta TODAS sus actividades.

    Reportado en producción: un proyecto con 4 actividades mostraba 1.
    """

    def setUp(self):
        super().setUp()
        ahora = timezone.now()
        for n in range(4):
            Actividades.objects.create(
                proyecto=self.proyecto, nombre=f"a{n}", fecha_programada=ahora,
                fecha_vencimiento=ahora + timedelta(days=3),
                estado=Estado.PENDIENTE, asignado_por=self.director,
                # Solo una es del formulador; las otras tres, de otra persona.
                asignado_a=self.form if n == 0 else self.otro,
            )

    def _n(self, user):
        return selectors.proyectos_visibles(user).get(pk=self.proyecto.pk).n_actividades

    def test_el_formulador_ve_el_total_no_solo_las_suyas(self):
        self.assertEqual(self._n(self.form), 4)

    def test_el_director_y_el_coordinador_ven_el_total(self):
        self.assertEqual(self._n(self.director), 4)
        self.assertEqual(self._n(self.coord), 4)

    def test_filtrar_por_formulador_no_altera_el_conteo(self):
        self.client.force_login(self.director)
        resp = self.client.get(f"/proyectos/?formulador={self.form.pk}")
        proyecto = resp.context["proyectos"][0]
        self.assertEqual(proyecto.n_actividades, 4)


class ExtensionesEnTodoPuntoDeCargaTest(TestCase):
    """La lista de extensiones rige en TODOS los campos de archivo, admin incluido.

    El admin genera sus formularios solos, así que no heredaba la validación de
    `ArchivoValidadoMixin`: quien tuviera acceso podía subir un ejecutable por
    ahí. Es una herramienta de soporte, no una puerta trasera.
    """

    def test_ningun_punto_de_carga_del_admin_queda_sin_validar(self):
        from django import forms as djforms
        from django.contrib import admin

        sin_validar = []
        for modelo, opciones in admin.site._registry.items():
            objetivos = []
            if [f for f in modelo._meta.fields if f.get_internal_type() == "FileField"]:
                objetivos.append((modelo.__name__, opciones))
            for inline in getattr(opciones, "inlines", None) or []:
                if [f for f in inline.model._meta.fields
                        if f.get_internal_type() == "FileField"]:
                    objetivos.append((inline.model.__name__, inline))
            for nombre, obj in objetivos:
                mro = getattr(obj, "__mro__", None) or type(obj).__mro__
                if not any("ArchivoValidado" in c.__name__ for c in mro):
                    sin_validar.append(nombre)
        self.assertEqual(sin_validar, [], f"sin validación: {sin_validar}")

    def test_el_formulario_del_admin_rechaza_un_ejecutable(self):
        from django.contrib import admin
        from django.contrib.auth.models import User
        from contenido.models import Documentos

        opciones = admin.site._registry[Documentos]
        peticion = type("P", (), {"user": User(is_superuser=True), "method": "GET"})()
        Form = opciones.get_form(peticion)
        form = Form(
            data={"nombre": "x"},
            files={"archivo": SimpleUploadedFile(
                "virus.exe", b"MZ", content_type="application/octet-stream")},
        )
        form.is_valid()
        self.assertIn("archivo", form.errors)
        self.assertIn("no es un tipo de archivo admitido", str(form.errors["archivo"]))

    def test_una_extension_activada_en_el_panel_vale_tambien_en_el_admin(self):
        from django.contrib import admin
        from django.contrib.auth.models import User
        from cuentas.models import ExtensionArchivo
        from contenido.models import Documentos

        ExtensionArchivo.objects.create(extension="dwg", descripcion="Plano CAD")
        opciones = admin.site._registry[Documentos]
        peticion = type("P", (), {"user": User(is_superuser=True), "method": "GET"})()
        form = opciones.get_form(peticion)(
            data={"nombre": "x"},
            files={"archivo": SimpleUploadedFile("plano.dwg", b"x")},
        )
        form.is_valid()
        self.assertNotIn("archivo", form.errors)


class SinCacheDeNavegadorTest(Base):
    """Las páginas de la aplicación no se quedan guardadas en el navegador.

    Nació de un caso real: una tarjeta mostraba "1 actividad" cuando el proyecto
    ya tenía cuatro. El dato y el código eran correctos; lo que se veía era una
    página de media hora antes. Sin `Cache-Control`, el navegador decide por su
    cuenta, y con el botón "atrás" ni siquiera pregunta.
    """

    def test_una_pagina_autenticada_no_se_guarda(self):
        self.client.force_login(self.form)
        cabecera = self.client.get("/proyectos/").headers.get("Cache-Control", "")
        self.assertIn("no-store", cabecera)

    def test_tampoco_el_login(self):
        """Una página de formulario cacheada trae un token CSRF viejo y el
        envío responde 403 sin explicar por qué."""
        cabecera = self.client.get("/cuentas/login/").headers.get("Cache-Control", "")
        self.assertIn("no-store", cabecera)

    def test_no_pisa_una_cabecera_puesta_a_proposito(self):
        from django.http import HttpResponse

        from cuentas.middleware import SinCacheDeNavegador

        propia = HttpResponse()
        propia.headers["Cache-Control"] = "max-age=3600, public"
        medio = SinCacheDeNavegador(lambda peticion: propia)
        self.assertEqual(
            medio(None).headers["Cache-Control"], "max-age=3600, public"
        )

    def test_va_despues_de_whitenoise(self):
        """Si se colara antes, los estáticos perderían su caché larga y cada
        carga de página volvería a pedir el CSS, las fuentes y los dos JS."""
        from django.conf import settings

        orden = settings.MIDDLEWARE
        self.assertLess(
            orden.index("whitenoise.middleware.WhiteNoiseMiddleware"),
            orden.index("cuentas.middleware.SinCacheDeNavegador"),
        )


class AccionesDeBorradoEnElAdminTest(Base):
    """Desactivar es lo normal; borrar de verdad es la excepción y está cerrada."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("root", password="x")
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)

    def _opciones(self, modelo):
        from django.contrib import admin
        return admin.site._registry[modelo]

    def _peticion(self, usuario):
        """Una petición con almacenamiento de mensajes: las acciones lo usan."""
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        peticion = RequestFactory().post("/")
        peticion.user = usuario
        peticion.session = {}
        peticion._messages = FallbackStorage(peticion)
        return peticion

    def test_desactivar_no_borra_nada(self):
        from contenido.admin import desactivar_usuarios

        desactivar_usuarios(
            None, self._peticion(self.admin), User.objects.filter(pk=self.form.pk)
        )
        self.form.refresh_from_db()
        self.assertFalse(self.form.is_active)
        self.assertTrue(User.objects.filter(pk=self.form.pk).exists())

    def test_el_borrado_total_es_solo_para_superusuario(self):
        opciones = self._opciones(User)
        peticion = type("P", (), {"user": self.staff})()
        self.assertFalse(opciones.has_borrado_total_permission(peticion))
        peticion.user = self.admin
        self.assertTrue(opciones.has_borrado_total_permission(peticion))

    def test_un_staff_no_ve_la_accion_destructiva(self):
        from django.test import RequestFactory

        opciones = self._opciones(User)
        peticion = RequestFactory().get("/")
        peticion.user = self.staff
        self.assertNotIn("eliminar_usuarios_con_rastro", opciones.get_actions(peticion))
        peticion.user = self.admin
        self.assertIn("eliminar_usuarios_con_rastro", opciones.get_actions(peticion))

    def test_borrar_un_usuario_se_lleva_sus_proyectos(self):
        from cuentas.borrado import eliminar_usuario_con_rastro
        from contenido.models import Proyectos

        act = self._act()
        with self.captureOnCommitCallbacks(execute=True):
            eliminar_usuario_con_rastro(self.director)
        self.assertFalse(Proyectos.objects.filter(pk=self.proyecto.pk).exists())
        self.assertFalse(Actividades.objects.filter(pk=act.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.director.pk).exists())

    def test_al_borrar_un_proyecto_sus_archivos_salen_del_bucket(self):
        """El defecto que había: la fila se iba y el archivo se quedaba."""
        from django.core.files.storage import default_storage

        act = self._act()
        entrega = services.crear_entrega(act, self.form, "v1")
        doc = self._doc(entrega)
        clave = doc.archivo.name
        self.assertTrue(default_storage.exists(clave))

        with self.captureOnCommitCallbacks(execute=True):
            self.proyecto.delete()
        self.assertFalse(default_storage.exists(clave))


class PanelesCoherentesConElFlujoTest(Base):
    """Los paneles agregan datos, y es donde se quedan las reglas viejas.

    Todo esto pasaba inadvertido: los números salían, solo que contaban otra
    cosa desde que entregar es un acto aparte de abrir la versión.
    """

    def setUp(self):
        super().setUp()
        from datetime import timedelta
        self.hace_una_semana = timezone.now() - timedelta(days=7)

    def _entrega_vieja_sin_enviar(self):
        """Un borrador abierto hace una semana y todavía sin entregar."""
        act = self._act()
        e = services.crear_entrega(act, self.form, "v1")
        ActividadEntrega.objects.filter(pk=e.pk).update(
            fecha_creacion=self.hace_una_semana,
            fecha_actualizacion=self.hace_una_semana,
        )
        return act, ActividadEntrega.objects.get(pk=e.pk)

    def test_un_borrador_no_es_trabajo_pendiente_del_revisor(self):
        self._entrega_vieja_sin_enviar()
        datos = metrics.coordinador(self.coord)
        self.assertEqual(datos["pendientes_count"], 0)
        self.assertEqual(datos["pendientes_atrasadas"], 0)

    def test_un_borrador_no_entra_en_el_backlog_del_director(self):
        self._entrega_vieja_sin_enviar()
        datos = metrics.director(self.director)
        self.assertEqual(
            sum(c["pendientes"] for c in datos["coordinadores"]), 0
        )

    def test_la_antiguedad_se_mide_desde_que_se_entrego(self):
        """El revisor no carga con lo que tardó el ejecutor en preparar."""
        act, e = self._entrega_vieja_sin_enviar()
        self._doc(e)
        services.realizar_entrega(e, self.form)   # entregada HOY

        datos = metrics.coordinador(self.coord)
        self.assertEqual(datos["pendientes_count"], 1)
        fila = datos["pendientes"][0]
        self.assertEqual(fila["dias"], 0, "cuenta desde la entrega, no desde el borrador")
        self.assertFalse(fila["atrasada"])

    def test_una_actividad_con_borrador_sigue_pendiente_de_entregar(self):
        """Antes desaparecía de su lista de tareas al abrir la versión."""
        act, _ = self._entrega_vieja_sin_enviar()
        datos = metrics.formulador(self.form)
        self.assertEqual(datos["sin_entrega_count"], 1)
        self.assertIn(act, datos["sin_entrega"])

    def test_abrir_un_borrador_no_cuenta_como_ultima_entrega(self):
        self._entrega_vieja_sin_enviar()
        self.assertIsNone(metrics.formulador(self.form)["dias_ultima_entrega"])

    def test_tras_entregar_si_cuenta(self):
        act, e = self._entrega_vieja_sin_enviar()
        self._doc(e)
        services.realizar_entrega(e, self.form)
        self.assertEqual(metrics.formulador(self.form)["dias_ultima_entrega"], 0)

    def test_el_tiempo_promedio_de_revision_no_incluye_el_borrador(self):
        act, e = self._entrega_vieja_sin_enviar()
        self._doc(e)
        services.realizar_entrega(e, self.form)
        services.registrar_revision(
            e, self.coord, Revisiones.ResultadoRevision.APROBADA, "ok"
        )
        prom = metrics.coordinador(self.coord)["tiempo_prom"]
        self.assertIsNotNone(prom)
        self.assertLess(prom, 1, "la semana del borrador no es tiempo de revisión")

    def test_los_paneles_responden_con_el_dominio_vacio(self):
        """Tras borrar los datos de prueba, ningún panel debe romperse."""
        for rol, user in (("director", self.director), ("coordinador", self.coord),
                          ("formulador", self.form)):
            with self.subTest(rol=rol):
                datos = getattr(metrics, rol)(user)
                self.assertIn("total_actividades", datos)
