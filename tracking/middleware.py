"""Sesión del dashboard: quién entró y qué puede ver.

No hay usuarios en base de datos; el catálogo es DASHBOARD_USUARIOS en
settings, indexado por el correo con el que cada quien entra, y los
permisos se releen de ahí en cada petición para que quitarle el acceso a
alguien surta efecto de inmediato.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

CLAVE_SESION = 'dashboard_autenticado'

CLAVE_USUARIO = 'dashboard_usuario'

CLAVE_LOGIN_NUEVO = 'login_recien_hecho'


def nombre_usuario(request):
    """Correo del usuario de la sesión, o cadena vacía si no hay nadie."""
    return request.session.get(CLAVE_USUARIO) or ''


def nombre_visible(request):
    """Cómo se nombra al usuario en la barra: lo que va antes del '@'.

    En la sesión el usuario es su correo completo, que no cabe en la píldora
    de la cabecera; el correo entero se queda en el `title`.
    """
    return nombre_usuario(request).split('@')[0]


def cuenta_actual(request):
    """Ficha del usuario en el catálogo.

    Returns:
        None si no hay sesión, o si al usuario ya le quitaron el acceso.
    """
    return settings.DASHBOARD_USUARIOS.get(nombre_usuario(request))


def esta_autenticado(request):
    """Dice si la petición trae sesión y su usuario sigue en el catálogo."""
    return bool(request.session.get(CLAVE_SESION)) and cuenta_actual(request) is not None


def tiene_acceso_total(request):
    """Dice si el usuario lo ve todo: todas las empresas y el mapa de flota."""
    cuenta = cuenta_actual(request)
    return cuenta is not None and cuenta.get('empresas') is None


class LoginRequeridoMiddleware:
    """Manda al login a quien no haya entrado, guardando a dónde iba."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self._libre(request.path) and not esta_autenticado(request):
            login_url = reverse('tracking:login')
            return redirect(f'{login_url}?next={request.get_full_path()}')
        return self.get_response(request)

    def _libre(self, path):
        """Rutas que se ven sin sesión: login, portada, /admin/ y estáticos.

        Las páginas se comparan completas: el login es '/', y por prefijo
        dejaría libre el sitio entero.
        """
        publicas = (
            reverse('tracking:login'),
            reverse('tracking:login_antiguo'),
            reverse('tracking:home'),
            # TEMPORAL: el POST del botón de acceso sin cuenta llega sin sesión.
            reverse('tracking:acceso_temporal'),
        )
        if path in publicas:
            return True
        exentas = (
            '/admin/',
            settings.STATIC_URL or '/static/',
        )
        return any(path.startswith(p) for p in exentas)
