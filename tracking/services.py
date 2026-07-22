"""
=============================================================================
 CAPA DE "SERVICIOS": prepara los datos para los dashboards
=============================================================================
api_client.py trae los datos CRUDOS del API. Este archivo los combina,
cuenta y resume para dejarlos listos tal como los pinta la página.

Hay una función por dashboard:

  - range_summary()  -> el dashboard principal (Servicios, Timbradas,
                        % de Ocupación y detalle por día, todo filtrado por
                        un rango de fechas desde/hasta).
  - fleet_summary()  -> la vista de mapa (guardada en /mapa/, sin enlace
                        en el menú por ahora).

Cada una regresa un diccionario simple (números, textos y listas) que la
vista convierte a JSON y el JavaScript del navegador dibuja.
=============================================================================
"""

from collections import Counter
from datetime import datetime, timedelta

from . import api_client

def _hoy():
    """Fecha de hoy como texto 'YYYY-MM-DD' (la zona horaria viene de settings)."""
    return datetime.now().strftime('%Y-%m-%d')


def _norm_interno(interno):
    """
    Normaliza el número interno para poder emparejarlo sin importar los
    espacios ('INT 7074', 'int7074' -> 'INT7074').
    """
    return ''.join((interno or '').upper().split())


# Capacidad (número de asientos) de cada bus, por número interno. El API de
# rastreo NO entrega este dato, así que la flota lo mantiene aquí a mano. Se
# usa para el % de ocupación por vehículo (timbradas ÷ capacidad × 100).
_CAPACIDAD_CRUDA = {
    'INT 7074': 25, 'INT 7075': 25, 'INT 7076': 31, 'INT 7077': 37,
    'INT 7078': 37, 'INT 7079': 37, 'INT 7080': 37, 'INT 7088': 30,
    'INT 7091': 30, 'INT 7092': 30, 'INT 7093': 30, 'INT 7094': 30,
    'INT 7095': 30, 'INT 7097': 30, 'INT 7099': 30, 'INT 7202': 30,
    'INT 7203': 30, 'INT 7204': 30, 'INT 7227': 40, 'INT 7239': 40,
    'INT 7245': 40, 'INT 7248': 40, 'INT 7250': 40, 'INT 7269': 40,
    'INT 7273': 40, 'INT 7274': 40, 'INT 7275': 40, 'INT 7276': 40,
    'INT 7277': 40, 'INT 7278': 40,
}
# Búsqueda por interno normalizado (así 'INT 7074' e 'INT7074' encuentran lo mismo).
CAPACIDAD_POR_INTERNO = {_norm_interno(k): v for k, v in _CAPACIDAD_CRUDA.items()}


def fleet_summary():
    """
    Dashboard de FLOTA.

    Combina dos llamadas al API:
      1. get_vehicles()  -> catálogo de vehículos (nombre, placa, marca...)
      2. get_live_data() -> último reporte de cada GPS (posición, velocidad...)

    Se unen por el IMEI del equipo: "idgps" (vehículo) == "GpsIdentif" (reporte).
    """
    # Diccionario {IMEI: vehículo} para buscar rápido cada vehículo por su GPS.
    vehicles = {v.get('idgps'): v for v in api_client.get_vehicles()}
    units = api_client.get_live_data()
    hoy = _hoy()

    rows = []
    encendidas = movimiento = reportando_hoy = 0
    for u in units:
        veh = vehicles.get(u.get('GpsIdentif'), {})   # datos de catálogo (si hay)
        speed = float(u.get('GpsSpeed') or 0)
        ignition = str(u.get('Ignition')) == '1'      # el API manda "0"/"1" como texto
        report_date = u.get('ReportDate') or ''

        # Contadores para las tarjetas de arriba del dashboard.
        if ignition:
            encendidas += 1
        if speed > 0:
            movimiento += 1
        if report_date.startswith(hoy):
            reportando_hoy += 1

        # Una fila por unidad, con solo los campos que usa la página.
        rows.append({
            'unidad': u.get('UnitId'),
            'placa': u.get('UnitPlate'),
            'equipo': u.get('GpsIdentif'),
            'tipo': veh.get('tipo_vehiculo') or '',
            'marca': veh.get('marca') or '',
            'conductor': u.get('Conductor') or veh.get('conductor') or '',
            'lat': float(u['Latitude']) if u.get('Latitude') else None,
            'lng': float(u['Longitude']) if u.get('Longitude') else None,
            'velocidad': speed,
            'ignicion': ignition,
            'fecha_reporte': report_date,
            'reporto_hoy': report_date.startswith(hoy),
            'domicilio': u.get('Domicilio') or '',
            'bateria_veh': u.get('BateriaVeh'),
            'senal': u.get('Senal'),
        })

    # Orden: primero las que reportaron hoy, y dentro de eso las más rápidas.
    rows.sort(key=lambda r: (not r['reporto_hoy'], -(r['velocidad'])))
    return {
        'unidades': rows,
        'stats': {
            'total': len(rows),
            'encendidas': encendidas,
            'en_movimiento': movimiento,
            'reportando_hoy': reportando_hoy,
        },
    }


def _es_entrada_geocerca(alerta):
    """
    ¿Esta alerta es una ENTRADA a geocerca?

    Los SERVICIOS se cuentan así: en la plataforma web se dibujan los puntos
    de referencia como geocercas con alerta configurada para dispararse SOLO
    AL ENTRAR. Por lo tanto: cada alerta de geocerca = 1 servicio realizado.

    Se identifica de forma tolerante (el texto exacto depende de cómo se
    nombre la alerta en la plataforma) y se descarta cualquier alerta de
    SALIDA ("FUERA...") por si alguna quedara configurada de ambos lados.
    """
    tipo = (alerta.get('TipoAlerta') or '').upper()
    status = (alerta.get('StatusAlerta') or '').upper()
    desc = (alerta.get('Descripcion') or '').upper()
    if 'FUERA' in status or 'FUERA DE LA GEOCERCA' in desc:
        return False
    return 'GEOCERCA' in tipo or 'GEOCERCA' in desc or 'DENTRO' in status


def _servicios_por_equipo(fecha, es_hoy):
    """
    Cuenta los servicios (entradas a geocerca) por equipo en una fecha.

    getAlerts trae las alertas de TODA la flota en una sola petición.
    Ojo: la plataforma puede repetir la misma alerta (una copia por cada
    destinatario configurado), así que se eliminan duplicados comparando
    equipo + fecha + hora + descripción antes de contar.
    """
    ttl = 120 if es_hoy else 24 * 3600
    servicios = Counter()
    vistos = set()
    for a in api_client.get_alerts(fecha, cache_ttl=ttl):
        key = (a.get('Equipo'), a.get('Fecha'), a.get('Hora'), a.get('Descripcion'))
        if key in vistos:
            continue
        vistos.add(key)
        if _es_entrada_geocerca(a):
            servicios[str(a.get('Equipo'))] += 1
    return servicios


def _lista_dias(desde, hasta):
    """Lista de fechas 'YYYY-MM-DD' entre desde y hasta (ambos incluidos)."""
    d1 = datetime.strptime(desde, '%Y-%m-%d').date()
    d2 = datetime.strptime(hasta, '%Y-%m-%d').date()
    return [(d1 + timedelta(days=n)).isoformat() for n in range((d2 - d1).days + 1)]


def range_summary(desde=None, hasta=None):
    """
    Dashboard principal: todo filtrado por un rango de fechas desde/hasta.

    Regresa lo que pide el boceto del dashboard:
      - vehiculos:  por cada bus, Servicios / Timbradas y el % de Ocupación
                    acumulados en el rango (alimenta las 3 gráficas). Cada
                    fila trae también su capacidad de asientos.
      - detalle:    matriz de timbradas por día x bus (la tabla
                    "Detalle por día de las timbradas").

    Definiciones (explicación completa en api_client.py):
      - Timbradas = eventos "PASAJERO IDENTIFICADO" (id 2720).
      - Servicios = entradas del bus a las geocercas de referencia
                    (alertas de geocerca, ver _es_entrada_geocerca).
      - Ocupación = ocupación real (%) = promedio de pasajeros por viaje ÷
                    capacidad × 100 = timbradas ÷ (servicios × capacidad) × 100.
                    Los viajes son los Servicios (1 entrada a geocerca = 1 viaje);
                    capacidad = asientos del bus (ver CAPACIDAD_POR_INTERNO).
                    Es None si falta la capacidad o el bus no tiene servicios.

    Estrategia para no saturar el API (mínimo 30 s entre peticiones):
      * Eventos: si el rango cae dentro del mes en curso, UNA petición por
        bus del 1° del mes a hoy (cacheable y reutilizable entre rangos del
        mismo mes); si el rango es más viejo, una petición por bus acotada
        al rango pedido.
      * Servicios: getAlerts es por día pero trae TODA la flota, así que
        son pocas peticiones (una por día del rango).
      * Todo queda en cache (días pasados 24 h, datos de hoy unos minutos).
    """
    hoy = _hoy()
    desde = desde or hoy
    hasta = hasta or hoy
    if desde > hasta:                      # rango invertido: se corrige solo
        desde, hasta = hasta, desde
    # Sin límite de días: el navegador avisa antes de lanzar rangos grandes
    # (pueden tardar varios minutos por el mínimo de 30 s entre peticiones).
    dias = _lista_dias(desde, hasta)

    vehicles = api_client.get_vehicles()
    primer_dia_mes = hoy[:8] + '01'        # '2026-07-15' -> '2026-07-01'
    hasta_efectivo = min(hasta, hoy)       # no tiene caso consultar el futuro
    # ¿El rango pedido cabe dentro de "1° del mes -> hoy"? Entonces las
    # peticiones del mes sirven también para el rango (0 peticiones extra).
    rango_dentro_del_mes = desde >= primer_dia_mes

    # --- Servicios por bus: una petición getAlerts por cada día del rango ---
    servicios = Counter()
    for d in dias:
        if d > hoy:
            continue
        for equipo, n in _servicios_por_equipo(d, d == hoy).items():
            servicios[equipo] += n

    vehiculos = []            # filas para las 2 gráficas
    conteo_dia_interno = Counter()  # {(fecha, interno): timbradas} para el detalle
    for veh in vehicles:
        equipo = veh.get('idgps')
        # Identificamos cada vehículo por su NÚMERO INTERNO (campo "nombre",
        # p. ej. "INT 7074"), no por la placa (campo "patente").
        interno = veh.get('nombre') or veh.get('patente') or ''

        # Eventos del rango pedido. Si el rango cabe en el mes en curso se
        # pide el mes completo (1 petición por bus, cache 30 min, reutilizable
        # entre rangos del mismo mes) y se filtra; si es más viejo, se pide
        # solo el rango.
        if not equipo or hasta_efectivo < desde:
            ev_rango = []
        elif rango_dentro_del_mes:
            try:
                ev_mes = api_client.get_passenger_events(
                    equipo, primer_dia_mes, hoy, cache_ttl=1800)
            except api_client.ApiError:
                ev_mes = []   # si un bus falla, no tumbar todo el dashboard
            ev_rango = [e for e in ev_mes if desde <= e['fecha'] <= hasta_efectivo]
        else:
            ttl = 600 if hasta_efectivo == hoy else 24 * 3600
            try:
                ev_rango = api_client.get_passenger_events(
                    equipo, desde, hasta_efectivo, cache_ttl=ttl)
            except api_client.ApiError:
                ev_rango = []

        # Ocupación real (%) = promedio de pasajeros por viaje ÷ capacidad × 100
        #   = timbradas ÷ (viajes × capacidad) × 100.
        # Los viajes son los Servicios (cada entrada a la geocerca = 1 viaje).
        # Es None si falta la capacidad o el bus no tiene viajes (no se puede
        # dividir): así un bus sin servicios no aparece con 0% engañoso.
        timbradas = len(ev_rango)
        n_servicios = servicios.get(str(equipo), 0)
        capacidad = CAPACIDAD_POR_INTERNO.get(_norm_interno(interno))
        if capacidad and n_servicios:
            ocupacion = round(timbradas / (n_servicios * capacidad) * 100)
        else:
            ocupacion = None
        vehiculos.append({
            'interno': interno,
            'equipo': equipo,
            'servicios': n_servicios,
            'timbradas': timbradas,
            'capacidad': capacidad,
            'ocupacion': ocupacion,
        })

        # Matriz del detalle por día.
        for e in ev_rango:
            conteo_dia_interno[(e['fecha'], interno)] += 1

    vehiculos.sort(key=lambda v: v['interno'] or '')
    internos = [v['interno'] for v in vehiculos]
    return {
        'desde': desde,
        'hasta': hasta,
        'hoy': hoy,
        'vehiculos': vehiculos,
        'detalle': {
            'internos': internos,
            'filas': [{'fecha': d,
                       'valores': [conteo_dia_interno.get((d, p), 0) for p in internos]}
                      for d in dias],
        },
    }
