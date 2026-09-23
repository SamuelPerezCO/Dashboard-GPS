#!/usr/bin/env python
"""Revisa si este servidor puede correr el dashboard, antes de desplegarlo.

Se corre en el hosting (la «Terminal» de cPanel o por SSH), desde la carpeta
del proyecto, y ANTES de instalar nada:

    python comprobar_hosting.py

No importa Django ni usa ninguna librería de fuera: solo la biblioteca
estándar, para que funcione aunque el `pip install` todavía no se haya hecho
(o haya fallado). Responde a las dos preguntas que deciden el despliegue:

  1. ¿El Python de este hosting es lo bastante nuevo para Django 6?
  2. ¿Este hosting deja salir a Supabase por el puerto de Postgres?

La segunda es la importante: muchos hosting compartidos tienen cerradas las
conexiones salientes a puertos que no sean 80 y 443, y con Postgres cerrado
el dashboard arranca pero se cae en cuanto alguien entra.
"""

import os
import socket
import sys
from urllib.parse import urlparse

OK, MAL, AVISO = '[ OK ]', '[MAL ]', '[AVISO]'


def leer_env(nombre):
    """El valor de una variable, buscándola también en el .env de al lado."""
    if os.getenv(nombre):
        return os.getenv(nombre)
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    try:
        with open(ruta, encoding='utf-8') as f:
            for linea in f:
                linea = linea.strip()
                if linea.startswith(f'{nombre}=') and not linea.startswith('#'):
                    return linea.split('=', 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return ''


def probar_puerto(host, puerto, etiqueta):
    """Intenta abrir un TCP y cuenta qué pasó, sin lanzar excepciones."""
    try:
        socket.create_connection((host, puerto), timeout=8).close()
    except socket.gaierror:
        print(f'  {MAL} {etiqueta}: el nombre «{host}» no resuelve (DNS)')
        return False
    except socket.timeout:
        print(f'  {MAL} {etiqueta}: {host}:{puerto} no contesta (cortafuegos)')
        return False
    except OSError as e:
        print(f'  {MAL} {etiqueta}: {host}:{puerto} rechazado ({e})')
        return False
    print(f'  {OK} {etiqueta}: {host}:{puerto} abierto')
    return True


def main():
    problemas = []

    print('\n1. Versión de Python')
    v = sys.version_info
    print(f'     {sys.version.split()[0]}  ({sys.executable})')
    if v >= (3, 12):
        print(f'  {OK} sirve para Django 6.0')
    elif v >= (3, 10):
        print(f'  {AVISO} Django 6.0 pide 3.12 o más. Con este Python hay que '
              f'bajar a Django==5.2.* (LTS) en requirements.txt')
        problemas.append('python-viejo')
    else:
        print(f'  {MAL} demasiado viejo hasta para Django 5.2 (pide 3.10)')
        problemas.append('python-muy-viejo')

    print('\n2. Salida a Supabase (Postgres)')
    url = leer_env('DATABASE_URL')
    if not url:
        print(f'  {AVISO} no hay DATABASE_URL: pon la variable o el .env y '
              f'vuelve a correr esto')
        problemas.append('sin-database-url')
    else:
        partes = urlparse(url)
        host, puerto = partes.hostname, partes.port or 5432
        print(f'     host: {host}')
        abierto = probar_puerto(host, puerto, f'puerto configurado ({puerto})')
        if not abierto:
            problemas.append('postgres-cerrado')
            otro = 5432 if puerto == 6543 else 6543
            print(f'     probando el otro pooler de Supabase...')
            if probar_puerto(host, otro, f'alternativa ({otro})'):
                print(f'     -> cambia el puerto de DATABASE_URL a {otro}')
                problemas.remove('postgres-cerrado')

    print('\n3. Salida al WebService de Service24GPS')
    api = leer_env('GPS_API_BASE_URL') or 'https://api.service24gps.com/api/v1'
    probar_puerto(urlparse(api).hostname, 443, 'HTTPS (443)') or \
        problemas.append('gps-cerrado')

    print('\n' + '=' * 62)
    if not problemas:
        print('Todo en orden: este hosting puede correr el dashboard tal cual.')
        return 0
    print('Cosas que hay que resolver antes de desplegar:')
    consejos = {
        'python-viejo': 'Pide Python 3.12+ al hosting, o pon Django==5.2.* '
                        'en requirements.txt.',
        'python-muy-viejo': 'Este hosting no sirve: pide Python 3.12.',
        'sin-database-url': 'Configura DATABASE_URL y repite la comprobación.',
        'postgres-cerrado': 'El hosting bloquea la salida a Postgres. Pide a '
                            'soporte que abra el puerto; si dicen que no, el '
                            'dashboard no puede hablar con Supabase por SQL.',
        'gps-cerrado': 'Sin salida HTTPS el dashboard no tiene datos. Soporte '
                       'tiene que abrirla.',
    }
    for p in problemas:
        print(f'  - {consejos[p]}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
