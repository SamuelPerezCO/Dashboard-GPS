"""Vistas HTTP de la app tracking.

Expone las páginas del dashboard (ocupación por rango de fechas y mapa
de flota en vivo) y sus respectivos endpoints JSON, que delegan toda
la lógica de negocio en :mod:`tracking.services`.
"""

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
from .middleware import CLAVE_SESION, esta_autenticado

logger = logging.getLogger(__name__)

FECHA_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')  # Valida fechas en formato YYYY-MM-DD.

# Freno al ensayo y error: tras MAX_INTENTOS fallos seguidos desde la misma IP
# el login se bloquea BLOQUEO_SEGUNDOS. La contraseña es de 4 dígitos y el
# sitio es público, así que sin esto se adivina en segundos.
MAX_INTENTOS = 5
BLOQUEO_SEGUNDOS = 60

# --- Precalentamiento del dashboard ---------------------------------------
# Mientras el usuario escribe su contraseña en /entrar/, un hilo aparte va
# consultando el API con el rango con el que abre el dashboard (el último
# mes, ver services.rango_ultimo_mes). Al entrar, esa primera consulta ya
# está en cache y carga casi de inmediato en vez de tardar varios segundos.
CLAVE_PRECALENTAMIENTO = 'precalentamiento_dashboard'
# La marca vive el mismo tiempo que los datos que deja calientes (10 min):
# precalentar más seguido no aporta nada, y de paso evita que cada visita
# al login —o un bot que lo escanee— dispare consultas pesadas al API.
PRECALENTAMIENTO_TTL = 600


def _precalentar_dashboard():
    """Corre el precalentamiento del dashboard; nunca deja escapar un error.

    Vive en un hilo sin nadie que lo espere: si algo falla, solo queda en
    el log y el dashboard consultará normal (sin cache adelantado).
    """
    try:
        services.precalentar_ultimo_mes()
    except api_client.ApiConfigError as exc:
        # Sin credenciales (desarrollo recién clonado) no es un error grave.
        logger.info('Precalentamiento omitido: %s', exc)
    except Exception:
        logger.exception('Falló el precalentamiento del dashboard')


def _lanzar_precalentamiento():
    """Arranca el precalentamiento en segundo plano, si toca.

    ``cache.add`` es atómico: solo el primero que llegue pone la marca y
    lanza el hilo; las demás visitas al login dentro de la ventana del
    TTL no hacen nada. (El cache es por proceso, igual que el resto del
    cacheo del proyecto: con el único worker de gunicorn en Render, el
    hilo y las peticiones comparten memoria.)

    Returns:
        True si se lanzó un hilo nuevo; False si ya había un
        precalentamiento reciente o en curso.
    """
    if not cache.add(CLAVE_PRECALENTAMIENTO, True, PRECALENTAMIENTO_TTL):
        return False
    threading.Thread(target=_precalentar_dashboard,
                     name='precalentar-dashboard', daemon=True).start()
    return True


def _clave_intentos(request):
    """Construye la clave de cache donde se cuentan los intentos fallidos.

    Args:
        request: El HttpRequest entrante.

    Returns:
        Cadena con la clave de cache, derivada de la IP de origen. Detrás
        de un proxy (Render, Cloudflare) todas las peticiones comparten
        IP, así que el freno pasa a ser global en vez de por visitante.
    """
    return f"login_intentos::{request.META.get('REMOTE_ADDR') or 'desconocida'}"


def login_view(request):
    """Muestra y procesa el formulario de acceso al dashboard.

    Compara contra el usuario y la contraseña de settings
    (``DASHBOARD_USER`` / ``DASHBOARD_PASSWORD``); no hay usuarios en
    base de datos.

    Al mostrar el formulario (GET) lanza además el precalentamiento del
    dashboard en segundo plano: mientras la persona escribe su
    contraseña, el servidor ya va consultando el último mes al API.

    Args:
        request: El HttpRequest entrante. En POST espera los campos
            ``usuario``, ``clave`` y opcionalmente ``next``.

    Returns:
        HttpResponse con el formulario, o una redirección a ``next``
        (o al dashboard) cuando las credenciales son correctas.
    """
    destino = request.POST.get('next') or request.GET.get('next') or ''
    # Sin esta comprobación, un ?next=https://otro-sitio convertiría el login
    # en un trampolín para llevar al usuario a una página ajena.
    if not url_has_allowed_host_and_scheme(
            destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        destino = reverse('tracking:dashboard')

    if esta_autenticado(request):
        return redirect(destino)

    if request.method != 'POST':
        # Mientras el usuario escribe su contraseña, el servidor va
        # adelantando la consulta con la que abre el dashboard.
        _lanzar_precalentamiento()

    error = ''
    if request.method == 'POST':
        clave_intentos = _clave_intentos(request)
        intentos = cache.get(clave_intentos, 0)
        if intentos >= MAX_INTENTOS:
            error = (f'Demasiados intentos fallidos. Espera {BLOQUEO_SEGUNDOS} '
                     f'segundos y vuelve a intentarlo.')
        else:
            usuario = (request.POST.get('usuario') or '').strip()
            clave = request.POST.get('clave') or ''
            # constant_time_compare no delata la contraseña por el tiempo que
            # tarda en responder. Se evalúan las dos para no cortar antes.
            ok_usuario = constant_time_compare(usuario, settings.DASHBOARD_USER)
            ok_clave = constant_time_compare(clave, settings.DASHBOARD_PASSWORD)
            if ok_usuario and ok_clave:
                cache.delete(clave_intentos)
                # Renueva el identificador de sesión al entrar, para que una
                # sesión creada antes del login no quede válida después.
                request.session.cycle_key()
                request.session[CLAVE_SESION] = True
                return redirect(destino)
            cache.set(clave_intentos, intentos + 1, BLOQUEO_SEGUNDOS)
            error = 'Usuario o contraseña incorrectos.'

    return render(request, 'tracking/login.html', {'error': error, 'next': destino})


def logout_view(request):
    """Cierra la sesión y devuelve al formulario de acceso.

    Solo cierra la sesión por POST (el botón del encabezado, con su
    token CSRF); así una imagen o un enlace de otra página no pueden
    sacar al usuario del dashboard.

    Args:
        request: El HttpRequest entrante.

    Returns:
        Redirección a la página de login.
    """
    if request.method == 'POST':
        request.session.flush()
    return redirect('tracking:login')


def dashboard(request):
    """Renderiza la página principal del dashboard de ocupación.

    Pasa el catálogo de empresas para que las pestañas se dibujen a
    partir de :data:`tracking.services.EMPRESAS` y no haya que
    mantener la misma lista duplicada en la plantilla.

    Args:
        request: El HttpRequest entrante.

    Returns:
        HttpResponse con la plantilla ``tracking/dashboard.html``.
    """
    return render(request, 'tracking/dashboard.html', {
        'empresas': [{'valor': e, 'etiqueta': services.ETIQUETA_EMPRESA[e]}
                     for e in services.EMPRESAS],
    })


def fleet_dashboard(request):
    """Renderiza la página del mapa de flota en vivo.

    Args:
        request: El HttpRequest entrante.

    Returns:
        HttpResponse con la plantilla ``tracking/fleet.html``.
    """
    return render(request, 'tracking/fleet.html')


def _json_api(build):
    """Ejecuta ``build`` y envuelve el resultado (o error) en un JsonResponse.

    Centraliza el manejo de errores de los endpoints JSON, traduciendo
    las excepciones conocidas del cliente del API a códigos de estado
    HTTP apropiados.

    Args:
        build: Función sin argumentos que construye y devuelve el
            diccionario de datos a serializar como JSON.

    Returns:
        JsonResponse con los datos de ``build()`` si todo sale bien, o
        con ``{'error': ...}`` y el status HTTP correspondiente si
        ocurre un error de validación (400), de configuración (503),
        del WebService (502) o inesperado (502).
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
    """Endpoint JSON con el resumen de ocupación para un rango de fechas.

    Lee los parámetros de querystring ``desde``, ``hasta`` y ``empresa``,
    los valida y delega el cálculo en :func:`tracking.services.range_summary`.

    Args:
        request: El HttpRequest entrante. Parámetros GET esperados:

            * ``desde`` (opcional): fecha inicial, formato ``YYYY-MM-DD``.
            * ``hasta`` (opcional): fecha final, formato ``YYYY-MM-DD``.
            * ``empresa`` (opcional): uno de los valores de
              :data:`tracking.services.EMPRESAS`.

    Returns:
        JsonResponse con el resumen de ocupación, o con un error 400 si
        las fechas o la empresa no son válidas.
    """
    desde = request.GET.get('desde') or None
    hasta = request.GET.get('hasta') or None
    for f in (desde, hasta):
        if f and not FECHA_RE.match(f):
            return JsonResponse({'error': 'Fecha inválida, usa YYYY-MM-DD'}, status=400)
    empresa = (request.GET.get('empresa') or '').strip().upper() or None
    if empresa and empresa not in services.EMPRESAS:
        return JsonResponse(
            {'error': f'Empresa inválida: {empresa}. Usa una de {", ".join(services.EMPRESAS)}.'},
            status=400,
        )
    return _json_api(lambda: services.range_summary(desde, hasta, empresa))


def api_fleet(request):
    """Endpoint JSON con el estado en vivo de toda la flota.

    Args:
        request: El HttpRequest entrante.

    Returns:
        JsonResponse con el resultado de :func:`tracking.services.fleet_summary`.
    """
    return _json_api(services.fleet_summary)
