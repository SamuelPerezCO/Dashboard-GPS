"""
=============================================================================
 CLIENTE DEL API DE SERVICE24GPS
=============================================================================
Este archivo es la ÚNICA parte del proyecto que habla directamente con el
WebService de rastreo (https://api.service24gps.com/api/v1/).
El resto del proyecto (services.py, views.py) usa las funciones de aquí
y nunca hace peticiones HTTP por su cuenta.

Cómo funciona el API (según "Doc Apikey.docx" v2.4):

1. TODAS las peticiones son POST y se mandan como formulario (form-data).
2. Toda petición lleva 2 llaves:
     - apikey : llave fija que identifica a tu empresa (está en el .env).
     - token  : llave temporal que se pide con usuario y contraseña
                usando el método "gettoken". Dura 6 horas.
3. La respuesta siempre es JSON con la forma: {"status": 200, "data": ...}
4. El API pide un mínimo de 30 segundos entre peticiones. Por eso aquí
   CACHEAMOS (guardamos temporalmente) cada respuesta: si el dashboard
   pide lo mismo dos veces seguidas, la segunda vez se responde desde la
   memoria del servidor sin llamar al API.

Flujo típico de una petición:

    dashboard (navegador)
        └─> views.py  ─> services.py ─> api_client.call("vehicleGetAll")
                                             │
                                             ├─ ¿está en cache? ──> sí: regresa lo guardado
                                             └─ no: pide token (get_token) y hace el POST
=============================================================================
"""

import hashlib
import json
import logging
import re

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Llave con la que guardamos el token en el cache de Django.
TOKEN_CACHE_KEY = 'gps_api_token'
# El token real dura 6 horas; lo guardamos 5 para renovarlo antes de que caduque.
TOKEN_TTL = 5 * 60 * 60
# Tiempo (en segundos) que se guarda cada respuesta del API por defecto.
DEFAULT_RESPONSE_TTL = 60


class ApiConfigError(Exception):
    """Se lanza cuando faltan credenciales en el archivo .env."""


class ApiError(Exception):
    """Se lanza cuando el WebService responde con un status distinto de 200."""


def _check_config():
    """Verifica que el .env tenga apikey, usuario y contraseña."""
    if not (settings.GPS_APIKEY and settings.GPS_USERNAME and settings.GPS_PASSWORD):
        raise ApiConfigError(
            'Faltan credenciales: escribe GPS_APIKEY, GPS_USERNAME y '
            'GPS_PASSWORD en el archivo .env y reinicia el servidor.'
        )


def _post(action, data):
    """
    Hace el POST real al WebService.

    - action: nombre del método del API (ej. "vehicleGetAll").
    - data:   diccionario que se envía como formulario.

    Regresa solo el contenido de "data" de la respuesta JSON,
    o lanza ApiError si el API contestó con error.
    """
    url = f"{settings.GPS_API_BASE_URL.rstrip('/')}/{action}"
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()          # error si el servidor HTTP falló (500, etc.)
    payload = resp.json()
    if payload.get('status') != 200:  # el API avisa sus errores en "status"
        raise ApiError(f"{action} -> status {payload.get('status')}: {payload.get('data')}")
    return payload['data']


def get_token(force=False):
    """
    Obtiene el token de autenticación y lo guarda en cache.

    La primera vez llama a "gettoken" con usuario y contraseña.
    Las siguientes veces regresa el token guardado (hasta por 5 horas).
    Con force=True ignora el cache y pide uno nuevo (útil cuando caducó).
    """
    _check_config()
    if not force:
        token = cache.get(TOKEN_CACHE_KEY)
        if token:
            return token
    data = _post('gettoken', {
        'apikey': settings.GPS_APIKEY,
        'token': '',                      # la doc pide mandarlo vacío aquí
        'username': settings.GPS_USERNAME,
        'password': settings.GPS_PASSWORD,
    })
    # Si se pidiera con get_info=1, "data" sería un dict; sin él es el token directo.
    token = data['token'] if isinstance(data, dict) else data
    cache.set(TOKEN_CACHE_KEY, token, TOKEN_TTL)
    return token


def call(action, params=None, cache_ttl=DEFAULT_RESPONSE_TTL):
    """
    Función central: llama cualquier método del API con token automático.

    1. Arma una llave de cache única con el método + sus parámetros.
    2. Si la respuesta ya está en cache, la regresa sin llamar al API.
    3. Si no, pide el token, hace el POST y guarda el resultado.
    4. Si el API rechaza la petición (p. ej. token caducado), renueva el
       token UNA vez y reintenta.
    """
    params = params or {}
    # json.dumps con sort_keys garantiza que los mismos parámetros generen
    # siempre la misma llave; md5 la acorta a un tamaño fijo.
    raw_key = f'gps_api::{action}::{json.dumps(params, sort_keys=True)}'
    cache_key = 'gps_api::' + hashlib.md5(raw_key.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = {'apikey': settings.GPS_APIKEY, 'token': get_token(), **params}
    try:
        result = _post(action, data)
    except ApiError:
        # El token pudo haber expirado: renovar una vez y reintentar.
        logger.info('Reintentando %s con token nuevo', action)
        data['token'] = get_token(force=True)
        result = _post(action, data)

    cache.set(cache_key, result, cache_ttl)
    return result


# ---------------------------------------------------------------------------
# Métodos concretos que usan los dashboards
# ---------------------------------------------------------------------------

def get_vehicles():
    """
    Lista de vehículos asignados al usuario del .env (método vehicleGetAll).
    Cada vehículo trae: id, nombre, patente (placa), idgps (IMEI del equipo),
    marca, tipo_vehiculo, etc. Cache de 5 minutos porque la flota casi no cambia.
    """
    return call('vehicleGetAll', cache_ttl=300)


def get_live_data():
    """
    Última posición/reporte de cada unidad (método getdata).
    Trae latitud, longitud, velocidad, ignición, fecha del reporte, domicilio
    y los sensores. Cache de 60 segundos (es la vista "en vivo").
    """
    return call('getdata', {'UseUTCDate': '0', 'sensores': '1'})


def get_alerts(fecha, cache_ttl=120):
    """
    Alertas del día de TODOS los equipos (método getAlerts).

    Una sola petición trae las alertas de toda la flota: pasajeros
    identificados, entradas a geocerca, pánico, etc. El dashboard usa las
    de geocerca para contar SERVICIOS (ver services.py).
    - equipo vacío = todos los activos del usuario.
    - fecha 'YYYY-MM-DD'.
    """
    data = call('getAlerts', {'equipo': '', 'fecha': fecha}, cache_ttl=cache_ttl)
    if isinstance(data, dict):   # si el API regresa una sola alerta como dict
        data = [data]
    return data or []


def get_onbus_programmed_routes(fecha_inicio, fecha_final):
    """Rutas programadas OnBus en un rango de fechas (máx. 5 días)."""
    data = call('getProgrammedRoutesOnBus', {
        'fecha_inicio': fecha_inicio,
        'fecha_final': fecha_final,
    })
    # El API devuelve un dict indexado por "NombreFechaHora": lo convertimos a lista.
    if isinstance(data, dict):
        return list(data.values())
    return data or []


# ---------------------------------------------------------------------------
# Ocupación Real / Timbradas (plataforma OnBus)
# ---------------------------------------------------------------------------
# IMPORTANTE: "Ocupación Real" y "Timbradas" NO existen como campos en la
# documentación del API; son etiquetas de la pantalla OnBus de la plataforma.
# Investigando el API real (flota Expreso Brasilia, 2026-07-15) se encontró
# cómo se calculan:
#
#   * Cada pasajero al subir "timbra" acercando su tarjeta iButton al lector
#     del bus. Eso genera el evento id 2720 "PASAJERO IDENTIFICADO" que se
#     puede consultar con el método historyGetEvents.
#   * Timbradas      = cuántos eventos 2720 hubo en el día.
#   * Ocupación Real = cuántos pasajeros DISTINTOS (iButton_ID únicos) hubo.
#
# El id del pasajero viene dentro del campo "datos_extras" (un JSON en texto),
# por eso se extrae con una expresión regular.

EVENTO_PASAJERO = '2720'
_IBUTTON_RE = re.compile(r'"iButton_ID":"([0-9a-fA-F]+)"')


def get_passenger_events(equipo, fecha_ini, fecha_fin, cache_ttl=600):
    """
    Timbradas de UN bus en un RANGO de fechas.

    - equipo: IMEI del GPS del bus (campo "idgps" de vehicleGetAll).
    - fecha_ini / fecha_fin: 'YYYY-MM-DD' (puede ser el mismo día).

    Regresa una lista de:
        {'fecha': 'YYYY-MM-DD', 'hora': 'HH:MM:SS', 'pasajero': iButtonID}

    Se pide al API solo el evento 2720 (idsEvents) para que la respuesta
    sea pequeña; el API acepta rangos de varios días en una sola petición
    (verificado con 15 días).
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
        # Un iButton de puros ceros es una lectura vacía, no un pasajero real.
        if pasajero and set(pasajero) == {'0'}:
            pasajero = None
        events.append({
            'fecha': ev.get('fecha') or '',
            'hora': ev.get('hora') or '',
            'pasajero': pasajero,
        })
    return events


def parse_sensores(sensores):
    """
    El campo "Sensores" de getdata llega como TEXTO con JSON anidado:
        {"82": {"Bateria_gps": {"nombre": ..., "valor": ...}}, ...}
    Esta función lo convierte a una lista plana y fácil de usar:
        [{'nombre': 'Bateria_gps', 'valor': '3.97'}, ...]
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
