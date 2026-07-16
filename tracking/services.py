"""
=============================================================================
 CAPA DE "SERVICIOS": prepara los datos para los dashboards
=============================================================================
api_client.py trae los datos CRUDOS del API. Este archivo los combina,
cuenta y resume para dejarlos listos tal como los pinta la página.

Hay una función por dashboard:

  - range_summary()  -> el dashboard principal (Servicios, Timbradas,
                        Ocupación Real, Ocupación Total y detalle por día,
                        todo filtrado por un rango de fechas desde/hasta).
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
      - vehiculos:       por cada bus, Servicios / Timbradas / Ocupación Real
                         acumulados en el rango (alimenta las 3 gráficas).
      - ocupacion_total: por cada bus, pasajeros únicos del DÍA (hoy) y del
                         MES en curso (la tabla "Ocupación Total").
      - detalle:         matriz de timbradas por día x bus (la tabla
                         "Detalle por día de las timbradas").

    Definiciones (explicación completa en api_client.py):
      - Timbradas      = eventos "PASAJERO IDENTIFICADO" (id 2720).
      - Ocupación Real = pasajeros únicos (tarjetas iButton distintas).
      - Servicios      = entradas del bus a las geocercas de referencia
                         (alertas de geocerca, ver _es_entrada_geocerca).

    Estrategia para no saturar el API (mínimo 30 s entre peticiones):
      * Eventos: UNA petición por bus que cubre del 1° del mes hasta hoy
        (sirve para las columnas Día y Mes). Si el rango pedido cae dentro
        de ese periodo, se reutiliza filtrando por fecha; solo si el rango
        es más viejo se hace una petición extra por bus.
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

    vehiculos = []            # filas para las 3 gráficas
    ocupacion_total = []      # filas para la tabla Ocupación Total
    conteo_dia_placa = Counter()   # {(fecha, placa): timbradas} para el detalle
    for veh in vehicles:
        equipo = veh.get('idgps')
        placa = veh.get('patente') or veh.get('nombre') or ''

        # Eventos del mes en curso (1 petición por bus, cache 30 min).
        ev_mes = []
        if equipo:
            try:
                ev_mes = api_client.get_passenger_events(
                    equipo, primer_dia_mes, hoy, cache_ttl=1800)
            except api_client.ApiError:
                ev_mes = []   # si un bus falla, no tumbar todo el dashboard

        # Eventos del rango pedido: reutilizar los del mes si se puede.
        if not equipo or hasta_efectivo < desde:
            ev_rango = []
        elif rango_dentro_del_mes:
            ev_rango = [e for e in ev_mes if desde <= e['fecha'] <= hasta_efectivo]
        else:
            ttl = 600 if hasta_efectivo == hoy else 24 * 3600
            try:
                ev_rango = api_client.get_passenger_events(
                    equipo, desde, hasta_efectivo, cache_ttl=ttl)
            except api_client.ApiError:
                ev_rango = []

        # Acumulados del rango (gráficas).
        pasajeros_rango = {e['pasajero'] for e in ev_rango if e['pasajero']}
        vehiculos.append({
            'bus': veh.get('nombre'),
            'placa': placa,
            'equipo': equipo,
            'servicios': servicios.get(str(equipo), 0),
            'timbradas': len(ev_rango),
            'ocupacion': len(pasajeros_rango),
        })

        # Tabla Ocupación Total: pasajeros únicos de HOY y del MES en curso.
        ocupacion_total.append({
            'placa': placa,
            'bus': veh.get('nombre'),
            'dia': len({e['pasajero'] for e in ev_mes
                        if e['pasajero'] and e['fecha'] == hoy}),
            'mes': len({e['pasajero'] for e in ev_mes if e['pasajero']}),
        })

        # Matriz del detalle por día.
        for e in ev_rango:
            conteo_dia_placa[(e['fecha'], placa)] += 1

    vehiculos.sort(key=lambda v: v['bus'] or '')
    ocupacion_total.sort(key=lambda v: v['bus'] or '')
    placas = [v['placa'] for v in vehiculos]
    return {
        'desde': desde,
        'hasta': hasta,
        'hoy': hoy,
        'mes': hoy[:7],                     # 'YYYY-MM' para el título de la tabla
        'vehiculos': vehiculos,
        'ocupacion_total': ocupacion_total,
        'detalle': {
            'placas': placas,
            'filas': [{'fecha': d,
                       'valores': [conteo_dia_placa.get((d, p), 0) for p in placas]}
                      for d in dias],
        },
    }
