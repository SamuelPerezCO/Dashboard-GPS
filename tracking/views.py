"""Vistas del sitio: la portada pública, el login y el dashboard."""

import logging
import re
import threading

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme

from . import api_client, services
from .middleware import (CLAVE_LOGIN_NUEVO, CLAVE_SESION, CLAVE_USUARIO,
                         cuenta_actual, esta_autenticado, nombre_usuario,
                         tiene_acceso_total)

logger = logging.getLogger(__name__)

FECHA_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

MAX_INTENTOS = 5
BLOQUEO_SEGUNDOS = 60

CLAVE_PRECALENTAMIENTO = 'precalentamiento_dashboard'
PRECALENTAMIENTO_TTL = 600


def _precalentar_dashboard():
    """Precalienta el dashboard sin dejar escapar ningún error.

    Vive en un hilo que nadie espera: si algo falla solo queda en el log y
    el dashboard consultará normal, sin cache adelantado.
    """
    try:
        services.precalentar_ultimo_mes()
    except api_client.ApiConfigError as exc:
        logger.info('Precalentamiento omitido: %s', exc)
    except Exception:
        logger.exception('Falló el precalentamiento del dashboard')


def _lanzar_precalentamiento():
    """Arranca el precalentamiento en segundo plano, si toca.

    `cache.add` es atómico: solo la primera visita al login dentro de la
    ventana del TTL lanza el hilo, así un bot que escanee la página no
    dispara una consulta pesada por visita.

    Returns:
        True si lanzó un hilo nuevo; False si ya había uno reciente.
    """
    if not cache.add(CLAVE_PRECALENTAMIENTO, True, PRECALENTAMIENTO_TTL):
        return False
    threading.Thread(target=_precalentar_dashboard,
                     name='precalentar-dashboard', daemon=True).start()
    return True


def _clave_intentos(request):
    """Clave de cache donde se cuentan los intentos fallidos de una IP.

    Detrás de un proxy todas las peticiones comparten IP, así que ahí el
    freno pasa a ser global en vez de por visitante.
    """
    return f"login_intentos::{request.META.get('REMOTE_ADDR') or 'desconocida'}"


def _empresas_permitidas(request):
    """Pestañas que puede ver el usuario de la sesión."""
    cuenta = cuenta_actual(request) or {}
    return services.tabs_permitidas(cuenta.get('empresas'))


def home(request):
    """La portada pública, la que enseña el sitio sin pedir cuenta.

    Contenido fijo: no toca el API ni la sesión.
    """
    return render(request, 'tracking/home.html')


def login_view(request):
    """Formulario de acceso al dashboard, y la raíz del sitio.

    Compara contra DASHBOARD_USUARIOS: el usuario no distingue mayúsculas,
    la contraseña sí. Tras MAX_INTENTOS fallos desde la misma IP el login
    se bloquea un rato, porque el sitio es público.

    El GET además manda a precalentar el dashboard: mientras la persona
    escribe su contraseña, el servidor va adelantando consultas al API.
    """
    destino = request.POST.get('next') or request.GET.get('next') or ''
    if not url_has_allowed_host_and_scheme(
            destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        destino = reverse('tracking:dashboard')

    if esta_autenticado(request):
        return redirect(destino)

    if request.method != 'POST':
        _lanzar_precalentamiento()

    error = ''
    if request.method == 'POST':
        clave_intentos = _clave_intentos(request)
        intentos = cache.get(clave_intentos, 0)
        if intentos >= MAX_INTENTOS:
            error = (f'Demasiados intentos fallidos. Espera {BLOQUEO_SEGUNDOS} '
                     f'segundos y vuelve a intentarlo.')
        else:
            usuario = (request.POST.get('usuario') or '').strip().lower()
            clave = request.POST.get('clave') or ''
            cuenta = settings.DASHBOARD_USUARIOS.get(usuario)
            ok_clave = constant_time_compare(clave, cuenta['clave'] if cuenta else '')
            if cuenta and ok_clave:
                cache.delete(clave_intentos)
                request.session.cycle_key()
                request.session[CLAVE_SESION] = True
                request.session[CLAVE_USUARIO] = usuario
                request.session[CLAVE_LOGIN_NUEVO] = True
                return redirect(destino)
            cache.set(clave_intentos, intentos + 1, BLOQUEO_SEGUNDOS)
            error = 'Usuario o contraseña incorrectos.'

    return render(request, 'tracking/login.html', {'error': error, 'next': destino})


def logout_view(request):
    """Cierra la sesión. Solo por POST, para que nadie la cierre desde fuera."""
    if request.method == 'POST':
        request.session.flush()
    return redirect('tracking:login')


def _sesion_nueva(request):
    """Consume la marca del login y dice si esta es la página de entrada.

    Es de un solo uso: la gasta la primera página que se pinta después de
    entrar, que es la que sella su pestaña. Una recarga o una pestaña nueva
    ya la encuentran vacía.
    """
    return bool(request.session.pop(CLAVE_LOGIN_NUEVO, False))


def dashboard(request):
    """Dashboard de ocupación por rango de fechas.

    Solo dibuja las pestañas del usuario, pero el reparto que manda es el
    de api_dashboard: la URL del JSON se puede escribir a mano.
    """
    return render(request, 'tracking/dashboard.html', {
        'empresas': [{'valor': e, 'etiqueta': services.ETIQUETA_EMPRESA[e]}
                     for e in _empresas_permitidas(request)],
        'usuario': nombre_usuario(request),
        'sesion_nueva': _sesion_nueva(request),
    })


def fleet_dashboard(request):
    """Mapa de la flota en vivo, solo para acceso total.

    El mapa muestra la flota entera, que no está repartida por empresa;
    al resto se le manda a su dashboard.

    Returns:
        La página del mapa, o una redirección al dashboard.
    """
    if not tiene_acceso_total(request):
        return redirect('tracking:dashboard')
    return render(request, 'tracking/fleet.html', {
        'usuario': nombre_usuario(request),
        'sesion_nueva': _sesion_nueva(request),
    })


def _json_api(build):
    """Ejecuta `build` y envuelve el resultado, o el error, en un JsonResponse.

    Returns:
        400 si lo que pidieron no sirve, 503 si faltan credenciales y 502
        si el problema fue del WebService.
    """
    try:
        return JsonResponse(build())
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except api_client.ApiConfigError as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    except api_client.ApiError as exc:
        logger.exception('Error del WebService')
        return JsonResponse({'error': f'El WebService respondió con error: {exc}'}, status=502)
    except Exception:
        logger.exception('Error consultando el API')
        return JsonResponse(
            {'error': 'No se pudo consultar el API de rastreo. Revisa la consola del servidor.'},
            status=502,
        )


def api_dashboard(request):
    """JSON del dashboard para un rango de fechas.

    Aquí es donde se reparte el acceso de verdad: responde 403 a la empresa
    que el usuario no puede ver, y su total nunca pasa de lo suyo. Ocultar
    la pestaña en la plantilla no protege nada por sí solo.
    """
    desde = request.GET.get('desde') or None
    hasta = request.GET.get('hasta') or None
    for f in (desde, hasta):
        if f and not FECHA_RE.match(f):
            return JsonResponse({'error': 'Fecha inválida, usa YYYY-MM-DD'}, status=400)
    permitidas = _empresas_permitidas(request)
    empresa = (request.GET.get('empresa') or '').strip().upper() or None
    if empresa and empresa not in services.EMPRESAS:
        return JsonResponse(
            {'error': f'Empresa inválida: {empresa}. Usa una de {", ".join(permitidas)}.'},
            status=400,
        )
    if empresa and empresa not in permitidas:
        return JsonResponse(
            {'error': f'Tu usuario no tiene acceso a los viajes de {empresa}.'},
            status=403,
        )
    return _json_api(lambda: services.range_summary(desde, hasta, empresa, permitidas))


def api_fleet(request):
    """JSON del mapa de flota. Solo para acceso total, igual que la página."""
    if not tiene_acceso_total(request):
        return JsonResponse(
            {'error': 'Tu usuario no tiene acceso al mapa de flota.'}, status=403)
    return _json_api(services.fleet_summary)
