"""Cliente delgado para el WebService de Service24GPS.

Este módulo encapsula toda la comunicación HTTP con el API de
Service24GPS: manejo de token (con cache y renovación automática),
cacheo de respuestas y wrappers específicos para cada acción del API
que usa el dashboard (vehículos, posiciones en vivo, alertas de
geocerca, eventos de pasajero, etc.).
"""

import hashlib
import json
import logging
import re

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = 'gps_api_token'  # Clave de cache para el token de autenticación.
TOKEN_TTL = 5 * 60 * 60  # Vigencia del token en cache, en segundos (5 horas).
DEFAULT_RESPONSE_TTL = 60  # TTL por defecto para respuestas cacheadas del API.


class ApiConfigError(Exception):
    """Error lanzado cuando faltan credenciales del API en la configuración."""


class ApiError(Exception):
    """Error lanzado cuando el WebService responde con un status distinto de 200."""


def _check_config():
    """Valida que las credenciales del API estén presentes en settings.

    Raises:
        ApiConfigError: Si falta GPS_APIKEY, GPS_USERNAME o GPS_PASSWORD.
    """
    if not (settings.GPS_APIKEY and settings.GPS_USERNAME and settings.GPS_PASSWORD):
        raise ApiConfigError(
            'Faltan credenciales: escribe GPS_APIKEY, GPS_USERNAME y '
            'GPS_PASSWORD en el archivo .env y reinicia el servidor.'
        )


def _post(action, data):
    """Envía una petición POST a una acción del WebService.

    Args:
        action: Nombre de la acción del API (por ejemplo, ``'gettoken'``).
        data: Diccionario de parámetros de formulario a enviar.

    Returns:
        El contenido del campo ``data`` de la respuesta JSON del API.

    Raises:
        requests.HTTPError: Si la respuesta HTTP no es exitosa.
        ApiError: Si el API responde con un ``status`` distinto de 200.
    """
    url = f"{settings.GPS_API_BASE_URL.rstrip('/')}/{action}"
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get('status') != 200:
        raise ApiError(f"{action} -> status {payload.get('status')}: {payload.get('data')}")
    return payload['data']


def get_token(force=False):
    """Obtiene un token de autenticación válido para el API.

    Por defecto reutiliza el token guardado en cache; solo pide uno
    nuevo al WebService si no hay token cacheado o si ``force`` es True.

    Args:
        force: Si es True, ignora el cache y solicita un token nuevo.

    Returns:
        El token de autenticación como cadena.

    Raises:
        ApiConfigError: Si faltan credenciales configuradas.
        ApiError: Si el WebService rechaza la solicitud de token.
    """
    _check_config()
    if not force:
        token = cache.get(TOKEN_CACHE_KEY)
        if token:
            return token
    data = _post('gettoken', {
        'apikey': settings.GPS_APIKEY,
        'token': '',
        'username': settings.GPS_USERNAME,
        'password': settings.GPS_PASSWORD,
    })
    token = data['token'] if isinstance(data, dict) else data
    cache.set(TOKEN_CACHE_KEY, token, TOKEN_TTL)
    return token


def call(action, params=None, cache_ttl=DEFAULT_RESPONSE_TTL):
    """Llama a una acción del API con cache de respuesta y reintento de token.

    El resultado se cachea usando una clave derivada de la acción y sus
    parámetros. Si la primera llamada falla por un token expirado,
    se reintenta una vez forzando un token nuevo.

    Args:
        action: Nombre de la acción del API a invocar.
        params: Parámetros adicionales para la acción. Si es None, se
            usa un diccionario vacío.
        cache_ttl: Segundos que la respuesta permanece en cache.

    Returns:
        Los datos devueltos por el API (ya sea desde cache o frescos).

    Raises:
        ApiConfigError: Si faltan credenciales configuradas.
        ApiError: Si la llamada falla incluso después de renovar el token.
    """
    params = params or {}
    raw_key = f'gps_api::{action}::{json.dumps(params, sort_keys=True)}'
    cache_key = 'gps_api::' + hashlib.md5(raw_key.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = {'apikey': settings.GPS_APIKEY, 'token': get_token(), **params}
    try:
        result = _post(action, data)
    except ApiError:
        logger.info('Reintentando %s con token nuevo', action)
        data['token'] = get_token(force=True)
        result = _post(action, data)

    cache.set(cache_key, result, cache_ttl)
    return result


def get_vehicles():
    """Obtiene el catálogo completo de vehículos registrados en el API.

    Returns:
        Lista de diccionarios con la información de cada vehículo
        (incluye claves como ``idgps``, ``nombre``, ``tipo_vehiculo``, etc.).
    """
    return call('vehicleGetAll', cache_ttl=300)


def get_live_data():
    """Obtiene la posición y sensores actuales de todas las unidades.

    Returns:
        Lista de diccionarios con la posición GPS, velocidad, ignición y
        sensores en tiempo real de cada unidad reportante.
    """
    return call('getdata', {'UseUTCDate': '0', 'sensores': '1'})


def get_alerts(fecha, cache_ttl=120):
    """Obtiene las alertas generadas por las unidades en una fecha dada.

    Incluye alertas de entrada y salida de geocerca, entre otros tipos.

    Args:
        fecha: Fecha a consultar, en formato ``YYYY-MM-DD``.
        cache_ttl: Segundos que la respuesta permanece en cache.

    Returns:
        Lista de diccionarios con cada alerta. Nunca es None.
    """
    data = call('getAlerts', {'equipo': '', 'fecha': fecha}, cache_ttl=cache_ttl)
    if isinstance(data, dict):
        data = [data]
    return data or []


def get_onbus_programmed_routes(fecha_inicio, fecha_final):
    """Obtiene las rutas programadas en OnBus dentro de un rango de fechas.

    Args:
        fecha_inicio: Fecha inicial del rango, en formato ``YYYY-MM-DD``.
        fecha_final: Fecha final del rango, en formato ``YYYY-MM-DD``.

    Returns:
        Lista de diccionarios con las rutas programadas. Nunca es None.
    """
    data = call('getProgrammedRoutesOnBus', {
        'fecha_inicio': fecha_inicio,
        'fecha_final': fecha_final,
    })
    if isinstance(data, dict):
        return list(data.values())
    return data or []


EVENTO_PASAJERO = '2720'  # Código de evento del API que representa un timbrado de pasajero.
_IBUTTON_RE = re.compile(r'"iButton_ID":"([0-9a-fA-F]+)"')  # Extrae el ID del iButton del campo datos_extras.


def get_passenger_events(equipo, fecha_ini, fecha_fin, cache_ttl=600):
    """Obtiene los eventos de "timbrado" de pasajero (iButton) de una unidad.

    Consulta el historial de eventos del API filtrando por el código de
    evento de pasajero y extrae el identificador del iButton del texto
    libre de ``datos_extras`` mediante una expresión regular.

    Args:
        equipo: Identificador del equipo GPS (``idgps``) a consultar.
        fecha_ini: Fecha inicial del rango, en formato ``YYYY-MM-DD``.
        fecha_fin: Fecha final del rango, en formato ``YYYY-MM-DD``.
        cache_ttl: Segundos que la respuesta permanece en cache.

    Returns:
        Lista de diccionarios con las claves ``fecha``, ``hora`` y
        ``pasajero`` (este último puede ser None si no se pudo
        identificar el iButton o si es un ID compuesto solo de ceros).
    """
    data = call('historyGetEvents', {
        'equipo': equipo,
        'fechaIni': f'{fecha_ini} 00:00:00',
        'fechaFin': f'{fecha_fin} 23:59:59',
        'format': 'DateTime',
        'idsEvents': EVENTO_PASAJERO,
    }, cache_ttl=cache_ttl)
    events = []
    for ev in data or []:
        extras = ev.get('datos_extras') or ''
        m = _IBUTTON_RE.search(extras)
        pasajero = m.group(1) if m else None
        if pasajero and set(pasajero) == {'0'}:
            pasajero = None
        events.append({
            'fecha': ev.get('fecha') or '',
            'hora': ev.get('hora') or '',
            'pasajero': pasajero,
        })
    return events


def parse_sensores(sensores):
    """Normaliza el campo de sensores del API a una lista plana.

    El API puede devolver los sensores como una cadena JSON o como un
    diccionario anidado por grupos; esta función aplana esa estructura
    en una lista uniforme de pares nombre/valor.

    Args:
        sensores: Sensores crudos, ya sea como cadena JSON o como dict
            anidado (grupo -> item -> {nombre, valor}).

    Returns:
        Lista de diccionarios con las claves ``nombre`` y ``valor``.
        Devuelve una lista vacía si ``sensores`` no se puede interpretar.
    """
    if isinstance(sensores, str):
        try:
            sensores = json.loads(sensores)
        except ValueError:
            return []
    result = []
    if isinstance(sensores, dict):
        for group in sensores.values():
            if isinstance(group, dict):
                for item in group.values():
                    if isinstance(item, dict) and 'nombre' in item:
                        result.append({
                            'nombre': item.get('nombre'),
                            'valor': item.get('valor'),
                        })
    return result
