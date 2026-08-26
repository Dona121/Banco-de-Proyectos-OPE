/** Configuración de Tailwind para la plataforma.
 *
 *  Antes vivía inline en un <script> dentro de templates/base.html, junto al
 *  CDN de Tailwind. Ese CDN es el COMPILADOR de Tailwind (~400 KB de JS que
 *  generan el CSS en el navegador en cada carga): en un teléfono con datos
 *  móviles eso se traduce en la página sin estilos. Ahora el CSS se compila
 *  aquí, una sola vez, y WhiteNoise lo sirve como archivo estático.
 *
 *  Para regenerarlo tras cambiar clases en las plantillas:
 *      ./tailwindcss -c tailwind.config.js -i tailwind.src.css \
 *                    -o static/web/tailwind.css --minify
 */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./*/templates/**/*.html",
    "./*/templatetags/*.py",
    "./*/forms.py",
  ],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: '#109d39', dark: '#0c7d2d', darker: '#095e22', light: '#e7f6ec', 50: '#f0faf3' },
        azul:  { DEFAULT: '#0b72ab', light: '#e6f1f7' },
        rojo:  { DEFAULT: '#d92a34', light: '#fbe9ea' },
        ambar: { DEFAULT: '#ffa700', light: '#fff4e0' },
      },
      fontFamily: {
        sans: ['"Open Sans"', 'system-ui', 'sans-serif'],
        head: ['Montserrat', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
