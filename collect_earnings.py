#!/usr/bin/env python3
"""
PEAD Data Collector
===================
Fetches earnings history (actual vs estimated EPS) from FMP stable/earnings API
for a universe of tickers and stores in local SQLite DB for backtesting.

Run once before pead_backtest.py.

Usage:
  python3 collect_earnings.py              # Fetch S&P 500 (from config)
  python3 collect_earnings.py --validate   # Show DB summary after collection
  python3 collect_earnings.py --resume     # Skip tickers already in DB
"""

import sys
import time
import sqlite3
import argparse
import urllib.request
import json
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import config as cfg

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / cfg.EARNINGS_DB


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def create_database():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS earnings (
            ticker          TEXT NOT NULL,
            report_date     TEXT NOT NULL,
            eps_actual      REAL,
            eps_estimated   REAL,
            rev_actual      REAL,
            rev_estimated   REAL,
            surprise_pct    REAL,
            PRIMARY KEY (ticker, report_date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_metadata (
            ticker      TEXT PRIMARY KEY,
            name        TEXT,
            sector      TEXT,
            market_cap  REAL,
            fetched_at  TEXT
        )
    """)
    cur.execute('CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings(report_date)')
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# FMP API calls
# ---------------------------------------------------------------------------

def fmp_get(endpoint, params=None):
    """Simple FMP GET with rate limiting."""
    base = f'{cfg.FMP_BASE_URL}/{endpoint}'
    if params:
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{base}?apikey={cfg.FMP_API_KEY}&{qs}'
    else:
        url = f'{base}?apikey={cfg.FMP_API_KEY}'

    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        return None


def get_sp500_tickers():
    """Return S&P 500 tickers via Wikipedia (free, no API key needed)."""
    try:
        import urllib.request
        # Wikipedia S&P 500 list -- parse the table directly
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode('utf-8')
        # Extract tickers from the first table -- they're in <td> after Symbol column
        import re
        # Find all ticker symbols in the first wikitable
        tickers = re.findall(r'<td><a[^>]*>([A-Z]{1,5}(?:\.[A-Z])?)</a>', html)
        if tickers and len(tickers) > 400:
            print(f'Wikipedia: {len(tickers)} tickers')
            return tickers
    except Exception as e:
        print(f'  Wikipedia fetch failed: {e}')

    # Hardcoded fallback -- major S&P 500 components
    print('  Using hardcoded ticker list...')
    return [
        'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','BRK.B','UNH','LLY',
        'JPM','XOM','V','AVGO','PG','MA','COST','HD','CVX','MRK','ABBV','CRM',
        'BAC','PEP','KO','TMO','ORCL','CSCO','ACN','MCD','ABT','WMT','DHR',
        'TXN','NKE','ADBE','PM','LIN','NEE','RTX','AMGN','QCOM','T','UNP','CAT',
        'GS','IBM','INTU','SPGI','ELV','ISRG','MDT','SYK','VRTX','REGN','CB',
        'AXP','BLK','DE','ADI','PLD','GILD','BKNG','C','MDLZ','ZTS','LRCX',
        'MMC','TJX','CI','MO','AON','ETN','NOC','GE','SHW','DUK','SO','CL',
        'WM','APD','FCX','EMR','ITW','FDX','MCO','NSC','PSX','PH','CTAS',
        'MSI','AFL','KLAC','MCHP','PAYX','ROST','D','AEP','EXC','XEL','HUM',
        'USB','TRV','ALL','STZ','CME','ICE','ECL','CARR','OTIS','IDXX','IQV',
        'HCA','EW','BAX','BSX','A','DXCM','MTD','BIO','VRSK','FAST','CTSH',
        'AMAT','MNST','CPRT','AIG','PRU','MET','LHX','L','AVB','EQR','PEG',
        'ES','WEC','AWK','PPL','CMS','NI','ATO','CNP','LNT','EVRG','OKE',
        'WMB','KMI','LNG','PSA','WELL','DLR','SBAC','AMT','CCI','SPG','VTR',
        'O','NLY','AGNC','ARE','BXP','KIM','REG','CPT','MAA','UDR','EXR',
        'CBRE','JLL','GWW','ROK','PNR','XYL','IEX','ROP','KEYS','TRMB','FTV',
        'NDAQ','CBOE','SCHW','STT','BK','NTRS','RF','CFG','HBAN','KEY','MTB',
        'CMA','FITB','ZION','WAL','FHN','SBNY','SIVB','SNV','EWBC','PPBI',
        'JPM','WFC','BAC','C','GS','MS','BLK','CB','MET','PRU','AFL','AIG',
        'MMC','AON','TRV','ALL','PGR','HIG','L','MKL','RNR','RE','AWL','EG',
        'GPC','LKQ','AAP','AZO','ORLY','SNA','SWK','DOV','IR','AME','TT',
        'CARR','OTIS','UTX','HON','MMM','GE','BA','LMT','NOC','RTX','GD','TDG',
        'HEI','TDY','CW','DRS','AXON','LDOS','SAIC','BAH','CACI','MANT','IC',
        'GOOGL','GOOG','META','NFLX','DIS','CMCSA','PARA','WBD','FOX','FOXA',
        'NYT','GCI','MDP','IAC','MTCH','BMBL','SNAP','PINS','TWTR','SPOT',
        'AMZN','EBAY','ETSY','W','CHWY','CVNA','CARVANA','DKNG','PENN','MGM',
        'LVS','WYNN','CZR','VICI','GLPI','RPRX','JAZZ','PRGO','VTRS','MYL',
        'PFE','MRK','ABBV','BMY','AMGN','GILD','BIIB','REGN','VRTX','SGEN',
        'ALNY','BLUE','MRNA','BNTX','NVAX','INO','ARCT','CVAC','SRPT','RARE'
    ]


def fetch_earnings(ticker):
    """Fetch earnings history for one ticker from FMP stable/earnings."""
    url = f'https://financialmodelingprep.com/stable/earnings?symbol={ticker}&apikey={cfg.FMP_API_KEY}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def fetch_company_profile(ticker):
    """Fetch company profile (sector, market cap) from FMP."""
    url = f'https://financialmodelingprep.com/stable/profile?symbol={ticker}&apikey={cfg.FMP_API_KEY}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        if isinstance(data, list) and data:
            return data[0]
        elif isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Data processing & storage
# ---------------------------------------------------------------------------

def compute_surprise_pct(actual, estimated):
    """Compute EPS surprise as percentage."""
    if actual is None or estimated is None:
        return None
    if abs(estimated) < cfg.MIN_ABS_EPS:
        return None
    return (actual - estimated) / abs(estimated) * 100


def store_earnings(conn, ticker, earnings_data):
    """Insert earnings records into DB."""
    cur = conn.cursor()
    inserted = 0
    for row in earnings_data:
        date = row.get('date')
        eps_actual = row.get('epsActual')
        eps_estimated = row.get('epsEstimated')
        rev_actual = row.get('revenueActual')
        rev_estimated = row.get('revenueEstimated')

        if not date:
            continue
        # Skip future dates
        if date > datetime.today().strftime('%Y-%m-%d'):
            continue
        # Skip if no actual (not yet reported)
        if eps_actual is None:
            continue

        surprise_pct = compute_surprise_pct(eps_actual, eps_estimated)

        cur.execute("""
            INSERT OR REPLACE INTO earnings
            (ticker, report_date, eps_actual, eps_estimated, rev_actual, rev_estimated, surprise_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ticker, date, eps_actual, eps_estimated, rev_actual, rev_estimated, surprise_pct))
        inserted += 1

    conn.commit()
    return inserted


def store_profile(conn, ticker, profile):
    """Store company metadata."""
    conn.execute("""
        INSERT OR REPLACE INTO ticker_metadata (ticker, name, sector, market_cap, fetched_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        ticker,
        profile.get('companyName') or profile.get('name', ''),
        profile.get('sector', ''),
        profile.get('mktCap') or profile.get('marketCap'),
        datetime.today().strftime('%Y-%m-%d %H:%M:%S')
    ))
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description='PEAD Earnings Data Collector')
    parser.add_argument('--validate', action='store_true', help='Show DB summary')
    parser.add_argument('--resume', action='store_true', help='Skip tickers already fetched')
    parser.add_argument('--ticker', type=str, default=None, help='Fetch single ticker')
    return parser.parse_args()


def validate_db(conn):
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM earnings')
    total = cur.fetchone()[0]
    cur.execute('SELECT COUNT(DISTINCT ticker) FROM earnings')
    tickers = cur.fetchone()[0]
    cur.execute('SELECT MIN(report_date), MAX(report_date) FROM earnings')
    min_date, max_date = cur.fetchone()
    cur.execute('SELECT COUNT(*) FROM earnings WHERE surprise_pct IS NOT NULL')
    with_surprise = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM earnings WHERE surprise_pct >= ? AND report_date >= ?',
                (cfg.BEAT_THRESHOLD, cfg.BACKTEST_START))
    bull_signals = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM earnings WHERE surprise_pct <= ? AND report_date >= ?',
                (-cfg.MISS_THRESHOLD, cfg.BACKTEST_START))
    bear_signals = cur.fetchone()[0]

    print()
    print('=' * 60)
    print('  PEAD DATABASE SUMMARY')
    print('=' * 60)
    print(f'  Total records:       {total:,}')
    print(f'  Unique tickers:      {tickers:,}')
    print(f'  Date range:          {min_date} to {max_date}')
    print(f'  With surprise %:     {with_surprise:,}')
    print(f'  BULL signals (>={cfg.BEAT_THRESHOLD}%): {bull_signals:,} (from {cfg.BACKTEST_START})')
    print(f'  BEAR signals (<=-{cfg.MISS_THRESHOLD}%): {bear_signals:,} (from {cfg.BACKTEST_START})')
    print('=' * 60)


def main():
    args = parse_args()
    conn = create_database()

    if args.validate:
        validate_db(conn)
        conn.close()
        return

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        print(f'\n  Fetching {cfg.UNIVERSE} ticker list...', end=' ', flush=True)
        tickers = get_sp500_tickers()
        if not tickers:
            print('FAILED to get ticker list. Check FMP key.')
            conn.close()
            return
        print(f'{len(tickers)} tickers')

    if args.resume:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT ticker FROM ticker_metadata')
        done = {r[0] for r in cur.fetchall()}
        tickers = [t for t in tickers if t not in done]
        print(f'  Resuming: {len(tickers)} tickers remaining')

    total = len(tickers)
    total_records = 0
    failed = 0

    print(f'  Collecting earnings data for {total} tickers...')
    print(f'  (Rate limit: {cfg.FMP_REQUEST_DELAY}s delay = ~{int(1/cfg.FMP_REQUEST_DELAY * 60)} req/min)')
    print()

    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0 or i == total:
            print(f'  [{i}/{total}] {len(tickers)-i} remaining, {total_records:,} records so far...')

        # Fetch earnings
        earnings = fetch_earnings(ticker)
        time.sleep(cfg.FMP_REQUEST_DELAY)

        if not earnings:
            failed += 1
            continue

        n = store_earnings(conn, ticker, earnings)
        total_records += n

        # Fetch profile (sector, market cap) -- every ticker, same delay
        profile = fetch_company_profile(ticker)
        time.sleep(cfg.FMP_REQUEST_DELAY)
        store_profile(conn, ticker, profile)

    print()
    print(f'  Collection complete.')
    print(f'  Records stored: {total_records:,}')
    print(f'  Failed/empty:   {failed}')

    validate_db(conn)
    conn.close()


if __name__ == '__main__':
    main()
