"""
=============================================================================
 VISTAS: conectan las URLs con las páginas y los datos
=============================================================================
Hay 2 tipos de vistas en este proyecto:

1. Vistas de PÁGINA (dashboard, fleet_dashboard):
   solo regresan el HTML. El HTML llega "vacío" (sin datos) y su JavaScript
   pide los datos después.

2. Vistas de API interno (api_dashboard, api_fleet):
   regresan JSON. Son las que el JavaScript del navegador llama con fetch().
   Estas sí consultan a services.py -> api_client.py -> WebService.

Diagrama:

   navegador ──GET /──────────────> dashboard ──> dashboard.html (solo HTML)
   navegador ──fetch /api/dashboard/?desde=...&hasta=...──> api_dashboard
                 ──> services.range_summary() ──> JSON
=============================================================================
"""

import logging
import re

from django.http import JsonResponse
from django.shortcuts import render

from . import api_client, services

logger = logging.getLogger(__name__)

# Valida que la fecha que manda el navegador tenga forma YYYY-MM-DD.
FECHA_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


# --- Vistas de página (solo HTML) -----------------------------------------

def dashboard(request):
    """Página principal: el dashboard del boceto (rango de fechas).

    Args:
        request (HttpRequest): La petición HTTP entrante.

    Returns:
        HttpResponse: El HTML de dashboard.html, sin datos (el JavaScript
            los pide después vía /api/dashboard/).
    """
    return render(request, 'tracking/dashboard.html')


def fleet_dashboard(request):
    """Vista de mapa de la flota. Guardada en /mapa/ (sin enlace en el menú).

    Args:
        request (HttpRequest): La petición HTTP entrante.

    Returns:
        HttpResponse: El HTML de fleet.html, sin datos (el JavaScript
            los pide después vía /api/fleet/).
    """
    return render(request, 'tracking/fleet.html')


# --- Vistas de API interno (JSON) ------------------------------------------

def _json_api(build):
    """Envuelve cualquier consulta en manejo de errores uniforme.

    Si algo falla, en lugar de romper la página se regresa {"error": "..."}
    con un código HTTP apropiado, y el JavaScript lo muestra como aviso rojo.

    Args:
        build (Callable[[], dict]): Función sin argumentos que produce el
            diccionario de datos a serializar como JSON.

    Returns:
        JsonResponse: Los datos en JSON si todo sale bien, o
            {"error": "..."} con estado 400, 502 o 503 según el tipo de falla.
    """
    try:
        return JsonResponse(build())
    except ValueError as exc:
        # 400 = petición inválida (por ejemplo, rango de fechas demasiado grande)
        return JsonResponse({'error': str(exc)}, status=400)
    except api_client.ApiConfigError as exc:
        # 503 = servicio no disponible (faltan credenciales en .env)
        return JsonResponse({'error': str(exc)}, status=503)
    except api_client.ApiError as exc:
        # 502 = el WebService de rastreo contestó con error
        logger.exception('Error del WebService')
        return JsonResponse({'error': f'El WebService respondió con error: {exc}'}, status=502)
    except Exception:
        # Cualquier otro problema (sin internet, timeout, etc.)
        logger.exception('Error consultando el API')
        return JsonResponse(
            {'error': 'No se pudo consultar el API de rastreo. Revisa la consola del servidor.'},
            status=502,
        )


def api_dashboard(request):
    """JSON del dashboard principal.

    Acepta ?desde=YYYY-MM-DD&hasta=YYYY-MM-DD (sin parámetros = hoy).

    Args:
        request (HttpRequest): La petición HTTP. Puede traer los parámetros
            GET "desde" y "hasta" en formato YYYY-MM-DD.

    Returns:
        JsonResponse: El resumen del rango de fechas, o {"error": "..."}
            con estado 400 si alguna fecha viene mal formada.
    """
    desde = request.GET.get('desde') or None
    hasta = request.GET.get('hasta') or None
    for f in (desde, hasta):
        if f and not FECHA_RE.match(f):
            return JsonResponse({'error': 'Fecha inválida, usa YYYY-MM-DD'}, status=400)
    return _json_api(lambda: services.range_summary(desde, hasta))


def api_fleet(request):
    """JSON con la flota completa. Lo usa la vista de mapa (/mapa/).

    Args:
        request (HttpRequest): La petición HTTP entrante (sin parámetros).

    Returns:
        JsonResponse: La flota completa con la posición actual de cada
            vehículo, o {"error": "..."} si el WebService falla.
    """
    return _json_api(services.fleet_summary)
