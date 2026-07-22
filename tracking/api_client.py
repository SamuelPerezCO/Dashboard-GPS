import hashlib
import json
import logging
import re

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = 'gps_api_token'
TOKEN_TTL = 5 * 60 * 60
DEFAULT_RESPONSE_TTL = 60


class ApiConfigError(Exception):
    pass


class ApiError(Exception):
    pass


def _check_config():
    if not (settings.GPS_APIKEY and settings.GPS_USERNAME and settings.GPS_PASSWORD):
        raise ApiConfigError(
            'Faltan credenciales: escribe GPS_APIKEY, GPS_USERNAME y '
            'GPS_PASSWORD en el archivo .env y reinicia el servidor.'
        )


def _post(action, data):
    url = f"{settings.GPS_API_BASE_URL.rstrip('/')}/{action}"
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get('status') != 200:
        raise ApiError(f"{action} -> status {payload.get('status')}: {payload.get('data')}")
    return payload['data']


def get_token(force=False):
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
    return call('vehicleGetAll', cache_ttl=300)


def get_live_data():
    return call('getdata', {'UseUTCDate': '0', 'sensores': '1'})


def get_alerts(fecha, cache_ttl=120):
    data = call('getAlerts', {'equipo': '', 'fecha': fecha}, cache_ttl=cache_ttl)
    if isinstance(data, dict):
        data = [data]
    return data or []


def get_onbus_programmed_routes(fecha_inicio, fecha_final):
    data = call('getProgrammedRoutesOnBus', {
        'fecha_inicio': fecha_inicio,
        'fecha_final': fecha_final,
    })
    if isinstance(data, dict):
        return list(data.values())
    return data or []



EVENTO_PASAJERO = '2720'
_IBUTTON_RE = re.compile(r'"iButton_ID":"([0-9a-fA-F]+)"')


def get_passenger_events(equipo, fecha_ini, fecha_fin, cache_ttl=600):
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
