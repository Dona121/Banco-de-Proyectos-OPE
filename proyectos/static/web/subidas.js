/* Subida de archivos directa al bucket, con porcentaje de avance.
 *
 * El archivo no pasa por el servidor: este sólo firma el permiso y registra el
 * resultado (el por qué está en cuentas/subidas.py). Se usa XMLHttpRequest y no
 * fetch porque es el único que informa del progreso de la subida.
 *
 * Si el navegador no ejecuta este script, el formulario clásico que está al lado
 * sigue funcionando: sirve para archivos pequeños.
 */
document.addEventListener("alpine:init", () => {
  Alpine.data("subidaDirecta", (urlFirmar, urlConfirmar) => ({
    urlFirmar,
    urlConfirmar,
    archivo: null,
    estado: "espera", // espera | subiendo | registrando | listo | error | cancelado
    porcentaje: 0,
    mensaje: "",
    // Petición en curso, para poder cancelarla.
    peticion: null,
    /* Paso de confirmación: el botón no envía, muestra lo que se va a enviar.
     * Varios cargues son irreversibles (responder un trámite final lo da por
     * hecho y, si es el último, cierra la cuenta en la misma transacción), y el
     * modelo no admite un estado intermedio "archivo puesto pero sin enviar":
     * un CheckConstraint prohíbe evidencia sin `realizado=True`. Esta pausa es
     * el único "¿seguro?" posible sin tocar models.py. */
    confirmando: false,
    /* Los campos extra (tipo, nombre, comentario) viven en el DOM, que NO es
     * reactivo: un getter que los lea no se vuelve a evaluar cuando el usuario
     * los escribe. Este contador, que sube con cada input/change del bloque, es
     * la dependencia reactiva que los getters declaran. Sin él, elegir primero
     * el archivo y después el tipo dejaba el botón deshabilitado para siempre. */
    tocado: 0,

    get ocupado() {
      return this.estado === "subiendo" || this.estado === "registrando";
    },

    /* Los campos que acompañan a la subida (tipo de documento, comentario...)
     * los declara cada sección en su plantilla con `data-subida`, y viajan tanto
     * al pedir el permiso como al confirmar. Así un mismo componente sirve para
     * secciones que piden datos distintos. */
    extras() {
      // `$root`, no `$el`: dentro de un manejador @click, `$el` es el elemento
      // que disparó el evento (el botón), así que la búsqueda no encontraba
      // nada: ni el token CSRF ni los campos, y el servidor respondía 403.
      return [...this.$root.querySelectorAll("[data-subida]")];
    },

    get puedeSubir() {
      this.tocado; // dependencia reactiva: ver el comentario de `tocado`
      if (!this.archivo || this.ocupado) return false;
      return this.extras()
        .filter((campo) => campo.required)
        .every((campo) => campo.value.trim() !== "");
    },

    get etiquetaTamano() {
      if (!this.archivo) return "";
      const mb = this.archivo.size / 1024 / 1024;
      return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.ceil(this.archivo.size / 1024)} KB`;
    },

    elegir(evento) {
      this.peticion = null;
      this.archivo = evento.target.files[0] || null;
      this.estado = "espera";
      this.porcentaje = 0;
      this.mensaje = "";
      this.confirmando = false; // cambiar de archivo deshace la confirmación
    },

    /* Lo que el usuario está a punto de enviar, para poder leerlo antes de
     * confirmar. Un `select` guarda el id, así que se muestra su texto; los
     * campos ocultos no se pintan porque no dicen nada legible. */
    get resumen() {
      this.tocado; // idem: el resumen lee los campos del DOM
      const filas = [];
      if (this.archivo) {
        filas.push({
          etiqueta: "Archivo",
          valor: `${this.archivo.name} (${this.etiquetaTamano})`,
        });
      }
      this.extras().forEach((campo) => {
        if (campo.type === "hidden") return;
        const valor =
          campo.tagName === "SELECT"
            ? (campo.options[campo.selectedIndex] || {}).text || ""
            : campo.value;
        if (valor.trim()) {
          filas.push({ etiqueta: campo.dataset.etiqueta || campo.name, valor: valor.trim() });
        }
      });
      return filas;
    },

    revisar() {
      if (!this.puedeSubir) return;
      this.confirmando = true;
    },

    volverAElegir() {
      this.confirmando = false;
    },

    csrf() {
      const campo = this.$root.querySelector('[name="csrfmiddlewaretoken"]');
      return campo ? campo.value : "";
    },

    datos() {
      const cuerpo = new FormData();
      cuerpo.append("csrfmiddlewaretoken", this.csrf());
      this.extras().forEach((campo) => cuerpo.append(campo.name, campo.value));
      return cuerpo;
    },

    async leer(respuesta) {
      // Un 403 devuelve la página de error en HTML: si se intenta leer como
      // JSON, la excepción tapa el motivo real.
      try {
        return await respuesta.json();
      } catch (e) {
        return {};
      }
    },

    get sePuedeCancelar() {
      return this.estado === "subiendo";
    },

    cancelar() {
      // Abortar el PUT deja el objeto sin crear en el bucket: S3 no guarda
      // subidas incompletas, así que no hay nada que limpiar después.
      if (this.peticion) this.peticion.abort();
      this.peticion = null;
      this.estado = "cancelado";
      this.porcentaje = 0;
      this.mensaje = "Subida cancelada. Puedes elegir otro archivo.";
    },

    fallar(mensaje) {
      this.estado = "error";
      this.mensaje = mensaje;
    },

    async subir() {
      if (!this.puedeSubir) return;
      this.confirmando = false; // a partir de aquí manda la barra de avance
      this.estado = "subiendo";
      this.porcentaje = 0;
      this.mensaje = "";

      // 1. Pedir permiso. El servidor comprueba el rol, valida la extensión y
      //    devuelve una URL firmada más un token que ata la subida a esta cuenta.
      let permiso;
      try {
        const datos = this.datos();
        datos.append("nombre", this.archivo.name);
        datos.append("content_type", this.archivo.type || "application/octet-stream");
        const respuesta = await fetch(this.urlFirmar, { method: "POST", body: datos });
        permiso = await this.leer(respuesta);
        if (!respuesta.ok) {
          return this.fallar(permiso.error || `El servidor rechazó la subida (${respuesta.status}).`);
        }
      } catch (e) {
        return this.fallar("No se pudo contactar al servidor.");
      }

      if (this.archivo.size > permiso.tamano_maximo) {
        const max = Math.round(permiso.tamano_maximo / 1024 / 1024);
        return this.fallar(`El archivo supera el máximo de ${max} MB.`);
      }

      // 2. Subir al bucket, informando del avance.
      try {
        await this.enviarAlBucket(permiso);
      } catch (e) {
        if (e && e.message === "cancelado") return; // el estado ya lo puso cancelar()
        return this.fallar(
          "La subida se interrumpió. Revisa tu conexión y vuelve a intentarlo."
        );
      }

      // 3. Confirmar: el servidor verifica que el archivo llegó y crea el registro.
      this.estado = "registrando";
      try {
        const datos = this.datos();
        datos.append("token", permiso.token);
        const respuesta = await fetch(this.urlConfirmar, { method: "POST", body: datos });
        const cuerpo = await this.leer(respuesta);
        if (!respuesta.ok) {
          return this.fallar(cuerpo.error || `No se pudo registrar el documento (${respuesta.status}).`);
        }
      } catch (e) {
        return this.fallar("El archivo subió, pero no se pudo registrar. Vuelve a intentarlo.");
      }

      this.estado = "listo";
      this.porcentaje = 100;
      window.location.reload();
    },

    enviarAlBucket(permiso) {
      return new Promise((resolver, rechazar) => {
        const peticion = new XMLHttpRequest();
        this.peticion = peticion;
        peticion.open(permiso.metodo, permiso.url, true);
        peticion.setRequestHeader("Content-Type", permiso.content_type);
        peticion.upload.addEventListener("progress", (evento) => {
          if (evento.lengthComputable) {
            this.porcentaje = Math.round((evento.loaded / evento.total) * 100);
          }
        });
        peticion.addEventListener("load", () =>
          peticion.status >= 200 && peticion.status < 300
            ? resolver()
            : rechazar(new Error(`HTTP ${peticion.status}`))
        );
        peticion.addEventListener("error", () => rechazar(new Error("red")));
        peticion.addEventListener("abort", () => rechazar(new Error("cancelado")));
        peticion.send(this.archivo);
      });
    },
  }));
});
