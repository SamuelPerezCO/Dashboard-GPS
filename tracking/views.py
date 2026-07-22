import logging
import re

from django.http import JsonResponse
from django.shortcuts import render

from . import api_client, services

logger = logging.getLogger(__name__)

FECHA_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')



def dashboard(request):
    return render(request, 'tracking/dashboard.html')


def fleet_dashboard(request):
    return render(request, 'tracking/fleet.html')



def _json_api(build):
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
    return _json_api(services.fleet_summary)
