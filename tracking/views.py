"""Vistas HTTP de la app tracking.

Expone las páginas del dashboard (ocupación por rango de fechas y mapa
de flota en vivo) y sus respectivos endpoints JSON, que delegan toda
la lógica de negocio en :mod:`tracking.services`.
"""

import logging
import re

from django.http import JsonResponse
from django.shortcuts import render

from . import api_client, services

logger = logging.getLogger(__name__)

FECHA_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')  # Valida fechas en formato YYYY-MM-DD.


def dashboard(request):
    """Renderiza la página principal del dashboard de ocupación.

    Args:
        request: El HttpRequest entrante.

    Returns:
        HttpResponse con la plantilla ``tracking/dashboard.html``.
    """
    return render(request, 'tracking/dashboard.html')


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
