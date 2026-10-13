# Crypto Market Monitor V1

Sistema de datos de mercado **sin claves de API y sin ejecución de operaciones**.

## Requisitos

- Windows 11 con Python 3.11+ y `py` en PATH (o Python equivalente en Linux)
- Internet con acceso a la API pública Binance Futures USD-M

## Instalación en Windows

1. Extraer el ZIP en una carpeta, por ejemplo `C:\CryptoMonitor`.
2. Ejecutar `install_windows.bat` una vez (crea `.venv` e instala dependencias).
3. Ejecutar `run_monitor.bat`.
4. Abrir `output\latest.html` en un navegador.
5. Datos crudos: `output\latest.json`, `latest_technicals.csv`, `latest_derivatives.csv` y `crypto_monitor.sqlite3`.
6. Log: `logs\monitor.log`.

En Linux: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python crypto_monitor.py`.

## Programación en Windows (Task Scheduler)

Crear tres tareas en Programador de tareas que ejecuten `C:\CryptoMonitor\run_monitor.bat` (ajustar la ruta):

- China: 22:30 ART, domingo a jueves (en horario de Argentina).
- Apertura USA: 09:30 hora de **America/New_York**, días hábiles.
- Post-cierre USA: 16:30 hora de **America/New_York**, días hábiles.

**Importante:** Windows Task Scheduler en un PC configurado en Argentina no ajusta automáticamente los triggers a `America/New_York`. Para V1, usar 10:30/17:30 ART durante horario de verano de EE.UU. y 11:30/18:30 ART durante horario estándar, o utilizar un scheduler que soporte zonas IANA y calendario bursátil. Task Scheduler tampoco omite automáticamente feriados de bolsas; las tareas se pueden ejecutar esos días, pero etiquetar el reporte como sesión de bolsa cerrada requerirá calendario adicional.

El PC debe estar encendido y con conexión. En Propiedades de tarea, activar `Ejecutar tanto si el usuario inició sesión como si no` si corresponde; desactivar suspensión o permitir reactivación.

## Datos y definiciones

- Fuente: `https://fapi.binance.com`, contratos USDⓈ-M, **no** BingX Coin-M.
- Velas 1H y 4H, se excluye la vela en curso.
- EMA20/50/200 calculadas desde cierres con pandas EWM (`adjust=False`).
- RSI14 y ATR14 usan suavizado Wilder con semilla SMA.
- RVOL20 = volumen de última vela cerrada / promedio de 20 velas anteriores.
- Tendencia = ASCENDENTE si EMA20>EMA50>EMA200; DESCENDENTE si EMA20<EMA50<EMA200; MIXTA en otro caso.
- OI actual en unidades de activo base del contrato, USD estimado = OI × mark price.
- Variaciones históricas OI 1h/4h/24h desde `sumOpenInterestValue` (USD) de snapshots históricos, **no directamente comparables** al valor instantáneo de OI actual.
- Funding = último `lastFundingRate` publicado por Binance, expresado como porcentaje. La periodicidad depende del contrato; revisar `nextFundingTime`.
- Campos faltantes: N/D. Se mantienen errores en JSON/HTML/log.

## Alcance y limitaciones

- No incluye aún noticias, calendario macro, market cap, ETF flows, radar automático de todo el mercado ni liquidaciones agregadas.
- ZEC/HYPE pueden no estar disponibles en Binance USD-M; se registra error sin interrumpir BTC/ETH/SOL.
- Algunos países/redes bloquean endpoints Binance (HTTP 451/403). En ese caso habrá que configurar otro proveedor; no se deben fabricar resultados.
- El endpoint de OI histórico tiene retención limitada; la base SQLite almacena snapshots desde la primera ejecución.
- El sistema produce archivos locales. Las tareas de ChatGPT **no pueden leer automáticamente** `output/latest.json` en tu PC. Para integración completa hace falta un destino accesible mediante un conector o servicio autorizado.
- Los datos son informativos, no recomendaciones de trading.
