"""Middleware que exige iniciar sesión para ver el dashboard.

El dashboard no tiene usuarios en base de datos: se protege con un único
usuario/contraseña definido en settings (``DASHBOARD_USER`` /
``DASHBOARD_PASSWORD``). Cuando alguien entra bien, :mod:`tracking.views`
marca la sesión con :data:`CLAVE_SESION` y este middleware es el que
comprueba esa marca en todas las peticiones.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

# Marca que se guarda en la sesión cuando el login fue correcto.
CLAVE_SESION = 'dashboard_autenticado'


def esta_autenticado(request):
    """Indica si la petición viene de una sesión que ya inició sesión.

    Args:
        request: El HttpRequest entrante.

    Returns:
        True si la sesión tiene la marca de autenticación.
    """
    return bool(request.session.get(CLAVE_SESION))


class LoginRequeridoMiddleware:
    """Redirige al login cualquier petición sin sesión iniciada.

    Quedan libres el propio login, el panel ``/admin/`` (que tiene su
    propio inicio de sesión) y los archivos estáticos.
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
        exentas = (
            reverse('tracking:login'),
            '/admin/',                        # tiene su propio login
            settings.STATIC_URL or '/static/',
        )
        return any(path.startswith(p) for p in exentas)
