from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

CLAVE_SESION = 'dashboard_autenticado'

CLAVE_USUARIO = 'dashboard_usuario'

CLAVE_LOGIN_NUEVO = 'login_recien_hecho'


def nombre_usuario(request):
    return request.session.get(CLAVE_USUARIO) or ''


def cuenta_actual(request):
    return settings.DASHBOARD_USUARIOS.get(nombre_usuario(request))


def esta_autenticado(request):
    return bool(request.session.get(CLAVE_SESION)) and cuenta_actual(request) is not None


def tiene_acceso_total(request):
    cuenta = cuenta_actual(request)
    return cuenta is not None and cuenta.get('empresas') is None


class LoginRequeridoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self._libre(request.path) and not esta_autenticado(request):
            login_url = reverse('tracking:login')
            return redirect(f'{login_url}?next={request.get_full_path()}')
        return self.get_response(request)

    def _libre(self, path):
        if path == reverse('tracking:home'):
            return True
        exentas = (
            reverse('tracking:login'),
            '/admin/',
            settings.STATIC_URL or '/static/',
        )
        return any(path.startswith(p) for p in exentas)
