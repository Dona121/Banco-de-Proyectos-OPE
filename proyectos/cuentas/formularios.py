"""Guardas de formulario para lo que los modelos definitivos no pueden llevar.

`contenido/models.py` y `cuentas_de_cobro/models.py` son definitivos, así que
cuando el `clean()` de un modelo da por supuesto que un campo ya tiene valor, el
arreglo tiene que vivir en el formulario. Es el mismo criterio que
`cuentas/validadores.py` sigue con los archivos.
"""


class ModeloValidadoTrasElFormularioMixin:
    """No valida el modelo si el formulario ya falló en sus propios campos.

    Django llama al `clean()` del modelo desde `_post_clean()` **aunque el
    formulario ya tenga errores de campo**: excluye del `full_clean` los campos
    con error, pero el `clean()` del modelo se ejecuta igual y lee los
    atributos, que siguen vacíos.

    `Actividades.clean()` compara las dos fechas sin comprobar que existan, así
    que enviar el formulario de actividad con las fechas en blanco reventaba con
    `TypeError: '>' not supported between instances of 'NoneType' and
    'NoneType'`: **un 500 al pulsar Guardar**, en la aplicación y en el admin.
    Los `clean()` de cuentas de cobro leen una clave ajena (`self.asignacion`,
    `self.cuenta_entrega`), que sin asignar lanza `RelatedObjectDoesNotExist`:
    el mismo 500 por otra puerta.

    Mientras haya errores de campo no hay nada que validar a nivel de modelo: el
    usuario tiene que corregir eso primero, y es exactamente lo que ve.
    """

    def _post_clean(self):
        if self.errors:
            return
        super()._post_clean()


class ModeloValidadoAdminMixin:
    """Lleva la guarda a los formularios que el admin genera por su cuenta.

    Se mezcla sobre la clase que construye el admin, como
    `ArchivoValidadoAdminMixin`, en vez de declarar un `form` por modelo.
    """

    @staticmethod
    def _con_guarda(base):
        return type(base.__name__, (ModeloValidadoTrasElFormularioMixin, base), {})

    def get_form(self, request, obj=None, **kwargs):
        return self._con_guarda(super().get_form(request, obj, **kwargs))


class ModeloValidadoInlineMixin(ModeloValidadoAdminMixin):
    """Lo mismo para los inlines."""

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form = self._con_guarda(formset.form)
        return formset
