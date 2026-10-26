"""Smoke tests de vistas: render por rol y control de acceso básico."""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from cuentas_de_cobro import services
from cuentas_de_cobro.models import (
    RequisitoDocumental,
    TipoDocumentoCargue,
    Vigencia,
)


class VistasTest(TestCase):
    def setUp(self):
        self.contratista = User.objects.create_user("contra", password="x")
        self.supervisor = User.objects.create_user("super", password="x")
        self.revisor = User.objects.create_user("rev", password="x")
        self.ajeno = User.objects.create_user("ajeno", password="x")
        self.contratista.groups.add(Group.objects.get(name="Contratista"))
        self.supervisor.groups.add(Group.objects.get(name="Supervisor"))
        self.revisor.groups.add(Group.objects.get(name="Revisor"))

        self.vigencia = Vigencia.objects.create(vigencia=2026)
        self.tipo = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        RequisitoDocumental.objects.create(vigencia=self.vigencia, tipo_documento=self.tipo)
        self.cuenta = services.crear_cuenta(self.contratista, self.vigencia, 6, "junio")

    def test_bandeja_render_por_rol(self):
        for u in (self.contratista, self.supervisor, self.revisor):
            self.client.force_login(u)
            resp = self.client.get(reverse("cuentas_cobro:bandeja"))
            self.assertEqual(resp.status_code, 200)

    def test_detalle_render_supervisor_y_contratista(self):
        # El supervisor ve la cuenta una vez entregada (intervención/acción).
        from django.core.files.uploadedfile import SimpleUploadedFile

        entrega = services.ultima_entrega(self.cuenta)
        services.adjuntar_documento(
            entrega, self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"))
        services.entregar(self.cuenta, self.contratista)
        url = reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        for u in (self.contratista, self.supervisor):
            self.client.force_login(u)
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_filtro_de_contratista_no_numerico_no_revienta(self):
        """El id sale de la querystring: si no es un número, filtrar por él hacía
        que el ORM lanzara ValueError y la bandeja respondiera 500."""
        self.client.force_login(self.supervisor)
        resp = self.client.get(reverse("cuentas_cobro:bandeja") + "?contratista=abc")
        self.assertEqual(resp.status_code, 200)

    def test_filtro_de_contratista_valido_si_filtra(self):
        otro = User.objects.create_user("otro_contratista", password="x")
        otro.groups.add(Group.objects.get(name="Contratista"))
        self.client.force_login(self.contratista)
        url = reverse("cuentas_cobro:bandeja")
        self.assertIn(self.cuenta, self.client.get(url).context["cuentas"])
        resp = self.client.get(f"{url}?contratista={otro.pk}")
        self.assertNotIn(self.cuenta, resp.context["cuentas"])

    def test_usuario_sin_rol_no_entra(self):
        self.client.force_login(self.ajeno)
        resp = self.client.get(reverse("cuentas_cobro:bandeja"))
        self.assertEqual(resp.status_code, 403)

    def test_contratista_no_ve_cuenta_ajena(self):
        otro = User.objects.create_user("otro", password="x")
        otro.groups.add(Group.objects.get(name="Contratista"))
        self.client.force_login(otro)
        url = reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_supervisor_no_puede_cargar_documentos(self):
        from cuentas_de_cobro import selectors

        from django.core.files.uploadedfile import SimpleUploadedFile

        self.assertFalse(selectors.puede_cargar_documentos(self.supervisor, self.cuenta))
        self.assertFalse(selectors.puede_cargar_documentos(self.revisor, self.cuenta))
        # Entregar la cuenta para que el supervisor la vea; aun así no puede entregar.
        entrega = services.ultima_entrega(self.cuenta)
        services.adjuntar_documento(
            entrega, self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"))
        services.entregar(self.cuenta, self.contratista)
        self.client.force_login(self.supervisor)
        url = reverse("cuentas_cobro:entregar", args=[self.cuenta.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_contratista_no_puede_cargar_cierre(self):
        # La carga del cierre pasó a radicación: el contratista no accede (403).
        self.client.force_login(self.contratista)
        url = reverse("cuentas_cobro:cierre_cargar", args=[self.cuenta.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_contratista_carga_documento(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.contratista)
        url = reverse("cuentas_cobro:documento_cargar", args=[self.cuenta.pk])
        resp = self.client.post(url, {
            "tipo_documento": self.tipo.pk,
            "documento": SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        })
        self.assertEqual(resp.status_code, 302)
        entrega = services.ultima_entrega(self.cuenta)
        self.assertEqual(entrega.documentoscuentacobro_set.count(), 1)

    def test_tras_entregar_el_contratista_ve_el_aviso_y_no_el_formulario(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        services.adjuntar_documento(
            services.ultima_entrega(self.cuenta), self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"))
        services.entregar(self.cuenta, self.contratista)
        self.client.force_login(self.contratista)
        cuerpo = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        ).content.decode()
        # Ya no se pinta el componente de subida (la URL que firma el permiso).
        firmar = reverse("cuentas_cobro:subida_firmar", args=[self.cuenta.pk, "entrega"])
        self.assertNotIn(firmar, cuerpo)
        self.assertIn("A la espera de la revisión de radicación", cuerpo)

    def test_form_de_carga_se_oculta_si_no_faltan_documentos(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        entrega = services.ultima_entrega(self.cuenta)
        services.adjuntar_documento(
            entrega, self.tipo,
            SimpleUploadedFile("d.pdf", b"x", content_type="application/pdf"),
        )
        self.assertEqual(services.documentos_faltantes(self.cuenta), [])
        self.client.force_login(self.contratista)
        resp = self.client.get(
            reverse("cuentas_cobro:cuenta_detalle", args=[self.cuenta.pk])
        )
        cuerpo = resp.content.decode()
        firmar = reverse("cuentas_cobro:subida_firmar", args=[self.cuenta.pk, "entrega"])
        self.assertNotIn(firmar, cuerpo)
        self.assertIn("Cargaste todos los documentos obligatorios", cuerpo)


class FiltrosDeLaBandejaTest(TestCase):
    """Los filtros de vigencia y mes, y lo que pasa si llegan mal por la URL."""

    def setUp(self):
        self.contratista = User.objects.create_user("contra", password="x")
        self.otro = User.objects.create_user("contra2", password="x")
        grupo = Group.objects.get(name="Contratista")
        self.contratista.groups.add(grupo)
        self.otro.groups.add(grupo)
        # `Consulta` ve todas las cuentas por diseño. El supervisor NO sirve aquí:
        # `cuentas_visibles` le exige haber participado o que la cuenta esté
        # entregada, y estas están recién creadas, así que no vería ninguna y la
        # prueba mediría el alcance en vez del filtro.
        self.consulta = User.objects.create_user("consulta", password="x")
        self.consulta.groups.add(Group.objects.get(name="Consulta"))

        self.v2025 = Vigencia.objects.create(vigencia=2025)
        self.v2026 = Vigencia.objects.create(vigencia=2026)
        tipo = TipoDocumentoCargue.objects.create(nombre="Cuenta de cobro")
        for v in (self.v2025, self.v2026):
            RequisitoDocumental.objects.create(vigencia=v, tipo_documento=tipo)

        # Tres cuentas: dos vigencias y dos meses distintos.
        self.c_2025_marzo = services.crear_cuenta(self.contratista, self.v2025, 3, "marzo")
        self.c_2026_marzo = services.crear_cuenta(self.contratista, self.v2026, 3, "marzo")
        self.c_2026_julio = services.crear_cuenta(self.contratista, self.v2026, 7, "julio")

    def _filas(self, **params):
        self.client.force_login(self.consulta)
        r = self.client.get(reverse("cuentas_cobro:bandeja"), params)
        self.assertEqual(r.status_code, 200)
        return {c.pk for c in r.context["cuentas"]}

    def test_sin_filtros_salen_todas(self):
        self.assertEqual(
            self._filas(),
            {self.c_2025_marzo.pk, self.c_2026_marzo.pk, self.c_2026_julio.pk},
        )

    def test_filtra_por_vigencia(self):
        self.assertEqual(
            self._filas(vigencia=self.v2026.pk),
            {self.c_2026_marzo.pk, self.c_2026_julio.pk},
        )

    def test_filtra_por_mes(self):
        self.assertEqual(
            self._filas(mes=3), {self.c_2025_marzo.pk, self.c_2026_marzo.pk}
        )

    def test_los_dos_juntos_se_cruzan(self):
        self.assertEqual(
            self._filas(vigencia=self.v2026.pk, mes=3), {self.c_2026_marzo.pk}
        )

    def test_un_parametro_que_no_es_numero_no_revienta(self):
        """Filtrar por algo que no es un id hace que el ORM lance ValueError.

        Es la vía directa a un 500 desde la barra de direcciones, y ya estaba
        cerrada para el contratista: los filtros nuevos usan la misma guarda.
        """
        for params in ({"vigencia": "abc"}, {"mes": "x"}, {"contratista": "'; --"},
                       {"vigencia": "1.5"}, {"mes": "-3"}):
            with self.subTest(params=params):
                # Un parámetro inservible se descarta: la bandeja sale completa.
                self.assertEqual(len(self._filas(**params)), 3)

    def test_un_mes_fuera_de_rango_no_deja_la_bandeja_vacia(self):
        """Pedir el mes 13 es "sin filtro", no una lista vacía sin explicación."""
        self.assertEqual(len(self._filas(mes=13)), 3)
        self.assertEqual(len(self._filas(mes=0)), 3)

    def test_solo_se_ofrecen_las_vigencias_y_meses_presentes(self):
        """Ninguna opción del desplegable puede devolver una lista vacía."""
        self.client.force_login(self.consulta)
        r = self.client.get(reverse("cuentas_cobro:bandeja"))
        self.assertEqual(
            [anio for _, anio in r.context["vigencias"]], [2026, 2025]
        )
        self.assertEqual(
            [etiqueta for _, etiqueta in r.context["meses"]], ["Marzo", "Julio"]
        )

    def test_el_contratista_solo_ve_opciones_de_lo_suyo(self):
        """El alcance manda: las opciones salen de las cuentas visibles."""
        services.crear_cuenta(self.otro, self.v2026, 11, "noviembre")
        self.client.force_login(self.contratista)
        r = self.client.get(reverse("cuentas_cobro:bandeja"))
        self.assertNotIn(
            "Noviembre", [etiqueta for _, etiqueta in r.context["meses"]],
            "se ofreció un mes que solo existe en la cuenta de otro contratista",
        )

    def test_el_filtro_elegido_queda_marcado_en_el_formulario(self):
        self.client.force_login(self.consulta)
        r = self.client.get(
            reverse("cuentas_cobro:bandeja"), {"vigencia": self.v2026.pk, "mes": 7}
        )
        html = r.content.decode()
        self.assertIn(f'value="{self.v2026.pk}" selected', html)
        self.assertIn('value="7" selected', html)
