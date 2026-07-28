"""Middleware que exige iniciar sesión para ver el dashboard.

El dashboard no tiene usuarios en base de datos: se protege con el
catálogo ``DASHBOARD_USUARIOS`` de settings (ahí se agregan y se quitan
usuarios). Cuando alguien entra bien, :mod:`tracking.views` marca la
sesión con :data:`CLAVE_SESION` y guarda su nombre en
:data:`CLAVE_USUARIO`; este middleware es el que comprueba esa marca en
todas las peticiones.

En la sesión solo va el NOMBRE del usuario, nunca sus permisos: los
permisos se releen del catálogo en cada petición
(:func:`cuenta_actual`). Así, cambiar quién ve qué en settings surte
efecto de inmediato, y no queda gente paseándose con permisos viejos
guardados en su cookie.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

# Marca que se guarda en la sesión cuando el login fue correcto.
CLAVE_SESION = 'dashboard_autenticado'

# Nombre (en minúsculas) del usuario que inició sesión: la llave con la que se
# busca su ficha en settings.DASHBOARD_USUARIOS.
CLAVE_USUARIO = 'dashboard_usuario'

# Marca de un solo uso que el login deja puesta para la PRIMERA página que se
# pinte después de entrar. La cookie de sesión la comparten todas las pestañas
# del navegador, así que por sí sola no distingue "la pestaña donde entré" de
# "una pestaña nueva abierta mañana sobre la misma cookie". Esta marca es la que
# permite hacer esa diferencia: la página que la recibe sella su pestaña en
# sessionStorage (que sí muere con la pestaña), y una pestaña sin sello cierra
# la sesión sola. Ver el bloque "guardia_pestana" de tracking/base.html.
CLAVE_LOGIN_NUEVO = 'login_recien_hecho'


def nombre_usuario(request):
    """Devuelve el nombre del usuario de la sesión, o cadena vacía.

    Args:
        request: El HttpRequest entrante.

    Returns:
        El nombre en minúsculas tal como está en el catálogo.
    """
    return request.session.get(CLAVE_USUARIO) or ''


def cuenta_actual(request):
    """Busca en el catálogo la ficha del usuario que inició sesión.

    Args:
        request: El HttpRequest entrante.

    Returns:
        El diccionario del usuario en ``settings.DASHBOARD_USUARIOS``
        (con ``clave`` y ``empresas``), o None si la sesión no tiene
        usuario o si ese usuario ya no existe en el catálogo (por
        ejemplo, porque se le quitó el acceso).
    """
    return settings.DASHBOARD_USUARIOS.get(nombre_usuario(request))


def esta_autenticado(request):
    """Indica si la petición viene de una sesión que ya inició sesión.

    Args:
        request: El HttpRequest entrante.

    Returns:
        True si la sesión tiene la marca de autenticación y su usuario
        sigue existiendo en el catálogo.
    """
    return bool(request.session.get(CLAVE_SESION)) and cuenta_actual(request) is not None


def tiene_acceso_total(request):
    """Dice si el usuario de la sesión puede verlo todo.

    Es el permiso del usuario ``admin``: todas las empresas y las
    páginas que no están repartidas por empresa, como el mapa de flota
    en vivo (que muestra la flota completa, no los viajes de un cliente).

    Args:
        request: El HttpRequest entrante.

    Returns:
        True si su entrada del catálogo tiene ``empresas`` en None.
    """
    cuenta = cuenta_actual(request)
    return cuenta is not None and cuenta.get('empresas') is None


class LoginRequeridoMiddleware:
    """Redirige al login cualquier petición sin sesión iniciada.

    Quedan libres la portada pública, el propio login, el panel
    ``/admin/`` (que tiene su propio inicio de sesión) y los archivos
    estáticos.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self._libre(request.path) and not esta_autenticado(request):
            login_url = reverse('tracking:login')
            # ``next`` guarda a dónde quería ir, para volver ahí tras entrar.
            return redirect(f'{login_url}?next={request.get_full_path()}')
        return self.get_response(request)

    def _libre(self, path):
        """Dice si una ruta se puede ver sin iniciar sesión.

        Args:
            path: Ruta de la petición (``request.path``).

        Returns:
            True si la ruta está exenta de pedir sesión.
        """
        # La portada se compara COMPLETA, no por prefijo: es '/', y por
        # prefijo dejaría libre el sitio entero.
        if path == reverse('tracking:home'):
            return True
        exentas = (
            reverse('tracking:login'),
            '/admin/',                        # tiene su propio login
            settings.STATIC_URL or '/static/',
        )
        return any(path.startswith(p) for p in exentas)
