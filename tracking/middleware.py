"""Sesión del dashboard: quién entró y qué puede ver.

El catálogo de cuentas es DashboardUsuario en base de datos, y los permisos
se releen de ahí en cada petición para que quitarle el acceso a alguien (o
desactivarlo desde /admin/) surta efecto de inmediato.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .models import DashboardUsuario

CLAVE_SESION = 'dashboard_autenticado'

CLAVE_USUARIO = 'dashboard_usuario'

CLAVE_LOGIN_NUEVO = 'login_recien_hecho'


def nombre_usuario(request):
    """Usuario de la sesión, o cadena vacía si no hay nadie."""
    return request.session.get(CLAVE_USUARIO) or ''


CLAVE_CACHE_CUENTA = '_dashboard_cuenta_cache'


def cuenta_actual(request):
    """Ficha del usuario en el catálogo.

    Se cachea en el propio `request`: el middleware y la vista la piden
    cada uno por su lado, y sin esto cada petición le pegaría dos veces a
    Supabase por la red en vez de una.

    Returns:
        None si no hay sesión, o si al usuario ya le quitaron el acceso
        (borrado o desactivado en /admin/).
    """
    if hasattr(request, CLAVE_CACHE_CUENTA):
        return getattr(request, CLAVE_CACHE_CUENTA)
    nombre = nombre_usuario(request)
    cuenta = None
    if nombre:
        usuario = DashboardUsuario.objects.filter(usuario=nombre, activo=True).first()
        if usuario is not None:
            cuenta = {'empresas': usuario.empresas_tuple, 'puede_invitar': usuario.puede_invitar}
    setattr(request, CLAVE_CACHE_CUENTA, cuenta)
    return cuenta


def esta_autenticado(request):
    """Dice si la petición trae sesión y su usuario sigue en el catálogo."""
    return bool(request.session.get(CLAVE_SESION)) and cuenta_actual(request) is not None


def tiene_acceso_total(request):
    """Dice si el usuario lo ve todo: todas las empresas y el mapa de flota."""
    cuenta = cuenta_actual(request)
    return cuenta is not None and cuenta.get('empresas') is None


def puede_invitar(request):
    """Dice si el usuario de la sesión puede dar de alta invitaciones."""
    cuenta = cuenta_actual(request)
    return cuenta is not None and bool(cuenta.get('puede_invitar'))


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
            reverse('tracking:primera_vez'),
        )
        if path in publicas:
            return True
        exentas = (
            '/admin/',
            settings.STATIC_URL or '/static/',
        )
        return any(path.startswith(p) for p in exentas)
