"""Crypto Market Monitor V1 — public Binance USD-M futures data only.

Educational market-data collector. No authentication and no trading endpoints.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("crypto_monitor")
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def get_json(session: requests.Session, base: str, path: str, params: dict, timeout: int):
    url = base.rstrip("/") + path
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code in (418, 429) or 500 <= response.status_code < 600:
                response.raise_for_status()
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError("Unreachable")


def fetch_candles(session, cfg, symbol, interval, now_ms):
    data = get_json(session, cfg["api_base"], "/fapi/v1/klines", {
        "symbol": symbol, "interval": interval, "limit": cfg["kline_limit"]
    }, cfg["request_timeout_seconds"])
    if not isinstance(data, list) or not data:
        raise ValueError("No candle data")
    frame = pd.DataFrame(data)
    frame = frame.iloc[:, [0, 1, 2, 3, 4, 5, 6]]
    frame.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
    for field in ["open", "high", "low", "close", "volume"]:
        frame[field] = pd.to_numeric(frame[field], errors="raise")
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
    frame["close_time"] = pd.to_numeric(frame["close_time"], errors="raise").astype("int64")
    frame = frame[frame["close_time"] < now_ms].sort_values("open_time").drop_duplicates("open_time")
    if len(frame) < 250:
        raise ValueError(f"Only {len(frame)} closed candles; need >=250")
    return frame.reset_index(drop=True)


def wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing seeded with SMA of first period observations."""
    series = series.astype(float)
    out = pd.Series(np.nan, index=series.index, dtype=float)
    valid = series.dropna()
    if len(valid) < period:
        return out
    first_index = valid.index[period - 1]
    seed = valid.iloc[:period].mean()
    out.loc[first_index] = seed
    prev = seed
    for idx, value in valid.iloc[period:].items():
        prev = ((period - 1) * prev + value) / period
        out.loc[idx] = prev
    return out


def calculate(frame: pd.DataFrame) -> dict:
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    vol = frame["volume"]
    emas = {n: close.ewm(span=n, adjust=False, min_periods=n).mean() for n in (20, 50, 200)}
    delta = close.diff()
    avg_gain = wilder(delta.clip(lower=0), 14)
    avg_loss = wilder((-delta).clip(lower=0), 14)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    previous_close = close.shift(1)
    tr = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    atr = wilder(tr, 14)
    rvol = vol / vol.shift(1).rolling(20, min_periods=20).mean().replace(0, np.nan)
    last = len(frame) - 1
    e20, e50, e200 = (float(emas[n].iloc[last]) for n in (20, 50, 200))
    trend = "ASCENDENTE" if e20 > e50 > e200 else "DESCENDENTE" if e20 < e50 < e200 else "MIXTA"
    price = float(close.iloc[last])
    return {
        "close_utc": datetime.fromtimestamp((int(frame["close_time"].iloc[last]) + 1) / 1000, timezone.utc).isoformat(),
        "close": price, "volume": float(vol.iloc[last]), "trend": trend,
        "ema20": e20, "ema50": e50, "ema200": e200,
        "rsi14": float(rsi.iloc[last]), "atr14": float(atr.iloc[last]),
        "atr_percent": float(atr.iloc[last] / price * 100),
        "rvol20": float(rvol.iloc[last])
    }


def fetch_oi(session, cfg, symbol, now_ms):
    timeout = cfg["request_timeout_seconds"]
    base = cfg["api_base"]
    current = get_json(session, base, "/fapi/v1/openInterest", {"symbol": symbol}, timeout)
    oi_contracts = float(current["openInterest"])
    price_data = get_json(session, base, "/fapi/v1/premiumIndex", {"symbol": symbol}, timeout)
    mark = float(price_data["markPrice"])
    current_usd_estimate = oi_contracts * mark
    funding = float(price_data["lastFundingRate"])
    next_funding = price_data.get("nextFundingTime")
    history = {}
    for period in ("1h", "4h"):
        try:
            rows = get_json(session, base, "/futures/data/openInterestHist", {
                "symbol": symbol, "period": period, "limit": 40
            }, timeout)
            history[period] = sorted(rows, key=lambda r: int(r["timestamp"])) if isinstance(rows, list) else []
        except Exception as exc:
            LOG.warning("OI history unavailable %s %s: %s", symbol, period, exc)
            history[period] = []
        time.sleep(cfg["request_pause_seconds"])
    def change(period, hours):
        rows = history[period]
        if not rows:
            return None
        # Use completed OI buckets and an explicit timestamp-based lookup.
        end = max((r for r in rows if int(r["timestamp"]) <= now_ms), key=lambda r: int(r["timestamp"]), default=None)
        if end is None:
            return None
        target = int(end["timestamp"]) - hours * 3600 * 1000
        # Timestamp convention may be end-of-period; allow one-minute tolerance.
        old = min(rows, key=lambda r: abs(int(r["timestamp"]) - target))
        if abs(int(old["timestamp"]) - target) > 60000:
            return None
        a = float(old.get("sumOpenInterestValue", 0))
        b = float(end.get("sumOpenInterestValue", 0))
        return (b / a - 1) * 100 if a > 0 else None
    return {
        "open_interest_contracts": oi_contracts,
        "open_interest_usd_estimate": current_usd_estimate,
        "mark_price": mark,
        "funding_rate_percent": funding * 100,
        "next_funding_utc": datetime.fromtimestamp(int(next_funding)/1000, timezone.utc).isoformat() if next_funding else None,
        "oi_change_1h_percent": change("1h", 1),
        "oi_change_4h_percent": change("4h", 4),
        "oi_change_24h_percent": change("4h", 24),
        "oi_change_method": "historical sumOpenInterestValue, completed snapshots (USD notionals); not identical to current estimate",
    }


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, (np.floating, float)) and not math.isfinite(float(value)):
        return None
    return value


def write_db(db_path: Path, report: dict):
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS snapshots (run_utc TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_utc,symbol,interval))")
        conn.execute("CREATE TABLE IF NOT EXISTS derivatives (run_utc TEXT NOT NULL, symbol TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_utc,symbol))")
        for symbol, item in report["assets"].items():
            for interval, metrics in item["technicals"].items():
                conn.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)", (report["run_utc"], symbol, interval, json.dumps(metrics, ensure_ascii=False)))
            if item["derivatives"] is not None:
                conn.execute("INSERT OR REPLACE INTO derivatives VALUES (?,?,?)", (report["run_utc"], symbol, json.dumps(item["derivatives"], ensure_ascii=False)))


def fmt(x, digits=2):
    return "N/D" if x is None else f"{x:,.{digits}f}"


def make_html(report):
    parts = ["<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Crypto Monitor V1</title><style>body{font:15px system-ui,Arial;max-width:1200px;margin:32px auto;padding:0 16px;color:#182336}h1{margin-bottom:4px}h2{margin-top:30px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #d8dfe9;padding:9px;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#f0f4fa}small,.muted{color:#64748b}.warning{background:#fff6e6;padding:12px;border-radius:8px}</style></head><body>"]
    parts.append(f"<h1>Crypto Market Monitor V1</h1><p class='muted'>Consulta UTC: {html.escape(report['run_utc'])} · Argentina: {html.escape(report['run_art'])}</p>")
    parts.append("<p class='warning'>Datos de Binance USDⓈ-M Futures. No representan los contratos Coin-M de BingX. El OI en USD actual es una estimación de contratos × mark price. Sin señales operativas.</p>")
    parts.append("<h2>Indicadores técnicos (velas cerradas)</h2><table><tr><th>Par / marco</th><th>Cierre UTC</th><th>Precio</th><th>Tendencia</th><th>EMA20</th><th>EMA50</th><th>EMA200</th><th>RSI14</th><th>ATR14</th><th>ATR%</th><th>RVOL20</th></tr>")
    for symbol, item in report["assets"].items():
        for interval, m in item["technicals"].items():
            cells = [f"{symbol} / {interval}", m["close_utc"], fmt(m["close"]), m["trend"], fmt(m["ema20"]), fmt(m["ema50"]), fmt(m["ema200"]), fmt(m["rsi14"]), fmt(m["atr14"]), fmt(m["atr_percent"]), fmt(m["rvol20"])]
            parts.append("<tr>"+"".join(f"<td>{html.escape(str(v))}</td>" for v in cells)+"</tr>")
    parts.append("</table><h2>Derivados</h2><table><tr><th>Par</th><th>OI (contratos/base)</th><th>OI USD aprox.</th><th>ΔOI 1H</th><th>ΔOI 4H</th><th>ΔOI 24H</th><th>Funding último %</th><th>Próximo funding UTC</th></tr>")
    for symbol, item in report["assets"].items():
        d = item["derivatives"]
        if not d: continue
        vals = [symbol, fmt(d["open_interest_contracts"]), fmt(d["open_interest_usd_estimate"]), fmt(d["oi_change_1h_percent"]), fmt(d["oi_change_4h_percent"]), fmt(d["oi_change_24h_percent"]), fmt(d["funding_rate_percent"], 5), d["next_funding_utc"] or "N/D"]
        parts.append("<tr>"+"".join(f"<td>{html.escape(str(v))}</td>" for v in vals)+"</tr>")
    parts.append("</table>")
    if report["errors"]:
        parts.append("<h2>Errores / datos no disponibles</h2><ul>")
        parts.extend(f"<li>{html.escape(x)}</li>" for x in report["errors"])
        parts.append("</ul>")
    parts.append("<p><small>Fuente: <a href='https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data'>Binance Futures REST API</a>. No incluye noticias, macro, ETF, liquidaciones agregadas ni market cap en esta versión.</small></p></body></html>")
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    output = ROOT / cfg["output_directory"]
    output.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    report = {"run_utc": now.isoformat(), "run_art": now.astimezone(ART).isoformat(), "exchange": "Binance USD-M Futures", "assets": {}, "errors": []}
    with requests.Session() as session:
        session.headers.update({"User-Agent": "CryptoMarketMonitorV1/1.0"})
        for symbol in cfg["symbols"]:
            LOG.info("Collecting %s", symbol)
            item = {"technicals": {}, "derivatives": None}
            for interval in cfg["intervals"]:
                try:
                    candles = fetch_candles(session, cfg, symbol, interval, int(now.timestamp()*1000))
                    item["technicals"][interval] = calculate(candles)
                except Exception as exc:
                    msg = f"{symbol} {interval} technicals: {exc}"
                    report["errors"].append(msg)
                    LOG.warning(msg)
                time.sleep(cfg["request_pause_seconds"])
            try:
                item["derivatives"] = fetch_oi(session, cfg, symbol, int(now.timestamp()*1000))
            except Exception as exc:
                msg = f"{symbol} derivatives: {exc}"
                report["errors"].append(msg)
                LOG.warning(msg)
            report["assets"][symbol] = item
            time.sleep(cfg["request_pause_seconds"])
    report = clean(report)
    write_db(ROOT / cfg["database"], report)
    (output / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "latest.html").write_text(make_html(report), encoding="utf-8")
    rows = [{"symbol": symbol, "interval": interval, **m} for symbol, a in report["assets"].items() for interval, m in a["technicals"].items()]
    if rows:
        pd.DataFrame(rows).to_csv(output / "latest_technicals.csv", index=False, encoding="utf-8-sig")
    rows = [{"symbol": symbol, **a["derivatives"]} for symbol, a in report["assets"].items() if a["derivatives"]]
    if rows:
        pd.DataFrame(rows).to_csv(output / "latest_derivatives.csv", index=False, encoding="utf-8-sig")
    print(f"Report: {output / 'latest.html'}")
    print(f"Assets: {len(report['assets'])} | Errors: {len(report['errors'])}")
    return 0 if any(x["technicals"] for x in report["assets"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
