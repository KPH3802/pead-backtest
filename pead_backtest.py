#!/usr/bin/env python3
"""
PEAD (Post-Earnings Announcement Drift) Backtest
=================================================
Tests whether stocks continue to drift in the direction of earnings surprises
in the weeks following the announcement.

Signal logic:
  BULL: EPS surprise >= +BEAT_THRESHOLD % -> buy, hold N weeks
  BEAR: EPS surprise <= -MISS_THRESHOLD % -> short, hold N weeks

Requires pead_data.db populated by collect_earnings.py first.

Usage:
  python3 pead_backtest.py                      # Full backtest
  python3 pead_backtest.py --beat 10 --miss 10  # Stricter threshold
  python3 pead_backtest.py --sample 1000        # Random sample (fast test)
  python3 pead_backtest.py --export             # Save CSV to results/
"""

import sys
import argparse
import sqlite3
import warnings
import os
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import yfinance as yf
from scipy import stats

warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).parent))
import config as cfg

SCRIPT_DIR = Path(__file__).parent
RESULTS_DIR = SCRIPT_DIR / cfg.RESULTS_DIR
DB_PATH = SCRIPT_DIR / cfg.EARNINGS_DB


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_signals(beat_threshold, miss_threshold, start, end):
    """Load all qualifying PEAD signal events from the earnings DB."""
    conn = sqlite3.connect(str(DB_PATH))

    conditions = [
        f'(surprise_pct >= {beat_threshold} OR surprise_pct <= -{miss_threshold})',
        'eps_actual IS NOT NULL',
        'eps_estimated IS NOT NULL',
        f'ABS(eps_estimated) >= {cfg.MIN_ABS_EPS}',
    ]
    if start:
        conditions.append(f'e.report_date >= \"{start}\"')
    if end:
        conditions.append(f'e.report_date <= \"{end}\"')

    query = f"""
        SELECT e.ticker, e.report_date, e.eps_actual, e.eps_estimated,
               e.surprise_pct, e.rev_actual, e.rev_estimated,
               m.sector, m.market_cap, m.name
        FROM earnings e
        LEFT JOIN ticker_metadata m ON e.ticker = m.ticker
        WHERE {' AND '.join(conditions)}
        ORDER BY e.report_date, e.ticker
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    df['report_date'] = pd.to_datetime(df['report_date'])
    df['signal'] = np.where(df['surprise_pct'] >= beat_threshold, 'BULL', 'BEAR')

    return df


# ---------------------------------------------------------------------------
# Price fetching (single ticker, same approach as SI backtest)
# ---------------------------------------------------------------------------

def fetch_prices(ticker, start, end=None):
    """Fetch adjusted close prices via yfinance."""
    end = end or datetime.today().strftime('%Y-%m-%d')
    try:
        data = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if data is None or data.empty:
            return None
        if 'Close' not in data.columns:
            return None
        s = data['Close'].squeeze().dropna()
        s.index = pd.to_datetime(s.index)
        return s if not s.empty else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Forward return computation
# ---------------------------------------------------------------------------

def compute_forward_returns(signals_df, hold_periods, entry_delay_days):
    """
    For each signal, fetch prices and compute forward returns.
    Entry: ENTRY_DELAY_DAYS trading days after report date
    BULL = long, BEAR = short
    """
    # Fetch prices per ticker (avoid re-fetching)
    tickers = signals_df['ticker'].unique().tolist()
    total = len(tickers)
    price_cache = {}

    price_start = (pd.to_datetime(cfg.BACKTEST_START) - timedelta(weeks=4)).strftime('%Y-%m-%d')
    price_end = cfg.BACKTEST_END or datetime.today().strftime('%Y-%m-%d')

    print(f'  Fetching prices for {total} unique tickers...')
    for i, ticker in enumerate(tickers, 1):
        if i % 100 == 0 or i == total:
            print(f'    {i}/{total} ({len(price_cache)} retrieved)...', flush=True)
        import contextlib
        with open(os.devnull, 'w') as devnull:
            with contextlib.redirect_stderr(devnull):
                prices = fetch_prices(ticker, price_start, price_end)
        if prices is not None:
            price_cache[ticker] = prices

    print(f'  Got prices for {len(price_cache)} of {total} tickers')

    # Compute returns
    trades = []
    for _, row in signals_df.iterrows():
        ticker = row['ticker']
        report_date = row['report_date']
        signal = row['signal']

        if ticker not in price_cache:
            continue

        prices = price_cache[ticker]

        # Entry: first available price ENTRY_DELAY_DAYS after report
        entry_from = report_date + timedelta(days=entry_delay_days)
        future = prices[prices.index >= entry_from]
        if future.empty:
            continue

        entry_date = future.index[0]
        entry_price = future.iloc[0]

        trade = {
            'ticker': ticker,
            'report_date': report_date,
            'entry_date': entry_date,
            'signal': signal,
            'surprise_pct': row['surprise_pct'],
            'eps_actual': row['eps_actual'],
            'eps_estimated': row['eps_estimated'],
            'sector': row.get('sector', ''),
            'market_cap': row.get('market_cap'),
            'entry_price': entry_price,
        }

        for weeks in hold_periods:
            target_date = entry_date + timedelta(weeks=weeks)
            future_exit = prices[prices.index >= target_date]
            if future_exit.empty:
                trade[f'ret_{weeks}w'] = np.nan
                continue
            exit_price = future_exit.iloc[0]
            raw_ret = (exit_price - entry_price) / entry_price
            ret = raw_ret if signal == 'BULL' else -raw_ret
            trade[f'ret_{weeks}w'] = ret

        trades.append(trade)

    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Statistics & reporting
# ---------------------------------------------------------------------------

def compute_stats(rets):
    rets = rets.dropna()
    if len(rets) < 10:
        return dict(n=len(rets), alpha=np.nan, win_rate=np.nan, t_stat=np.nan, p_value=np.nan)
    t, p = stats.ttest_1samp(rets, 0)
    return dict(n=len(rets), alpha=rets.mean(), win_rate=(rets > 0).mean(), t_stat=t, p_value=p)


def sig_stars(p):
    if pd.isna(p): return ''
    if p < 0.01: return '***'
    if p < 0.05: return '**'
    if p < 0.10: return '*'
    return ''


def print_sep(char='=', w=70):
    print(char * w)


def print_stats_block(label, trades, hold_periods):
    print(f'  {label}  ({len(trades)} trades)')
    print(f'  {"Horizon":<10} {"N":>6} {"Alpha":>9} {"Win%":>8} {"t-stat":>8} {"p-val":>8}  Sig')
    print('  ' + '-' * 58)
    for weeks in hold_periods:
        col = f'ret_{weeks}w'
        if col not in trades.columns:
            continue
        s = compute_stats(trades[col])
        if pd.isna(s['alpha']):
            print(f'  {weeks}w{"":<8} {int(s["n"]):>6} {"--":>9}')
        else:
            stars = sig_stars(s['p_value'])
            print(
                f'  {weeks}w{"":<8} {int(s["n"]):>6} '
                f'{s["alpha"]:>+8.2%} {s["win_rate"]:>8.1%} '
                f'{s["t_stat"]:>8.2f} {s["p_value"]:>8.4f}  {stars}'
            )
    print()


def print_cap_breakdown(trades, col_weeks=4):
    """Break results by market cap tier."""
    col = f'ret_{col_weeks}w'
    if col not in trades.columns or trades.empty:
        return

    t = trades.copy().dropna(subset=['market_cap'])
    if t.empty:
        return

    # Define tiers
    def cap_tier(mc):
        if pd.isna(mc): return 'Unknown'
        if mc >= 10e9: return 'Large (>$10B)'
        if mc >= 2e9: return 'Mid ($2-10B)'
        return 'Small (<$2B)'

    t['cap_tier'] = t['market_cap'].apply(cap_tier)

    print(f'  MARKET CAP BREAKDOWN ({col_weeks}w return)')
    print(f'  {"Tier":<18} {"N":>6} {"Alpha":>9} {"Win%":>8} {"t-stat":>8}  Sig')
    print('  ' + '-' * 55)
    for tier in ['Large (>$10B)', 'Mid ($2-10B)', 'Small (<$2B)']:
        subset = t[t['cap_tier'] == tier]
        s = compute_stats(subset[col])
        if pd.isna(s['alpha']):
            print(f'  {tier:<18} {int(s["n"]):>6} {"--":>9}')
        else:
            stars = sig_stars(s['p_value'])
            print(f'  {tier:<18} {int(s["n"]):>6} {s["alpha"]:>+8.2%} {s["win_rate"]:>8.1%} {s["t_stat"]:>8.2f}  {stars}')
    print()


def print_sector_breakdown(trades, col_weeks=4):
    """Break results by sector."""
    col = f'ret_{col_weeks}w'
    if col not in trades.columns or trades.empty:
        return

    t = trades.dropna(subset=['sector'])
    t = t[t['sector'] != '']
    if t.empty:
        return

    sector_stats = []
    for sector in sorted(t['sector'].unique()):
        subset = t[t['sector'] == sector]
        s = compute_stats(subset[col])
        if not pd.isna(s['alpha']) and s['n'] >= 10:
            sector_stats.append((sector, s))

    if not sector_stats:
        return

    sector_stats.sort(key=lambda x: x[1]['t_stat'], reverse=True)

    print(f'  SECTOR BREAKDOWN ({col_weeks}w return, min 10 trades, sorted by t-stat)')
    print(f'  {"Sector":<28} {"N":>6} {"Alpha":>9} {"Win%":>8} {"t-stat":>8}  Sig')
    print('  ' + '-' * 65)
    for sector, s in sector_stats:
        stars = sig_stars(s['p_value'])
        print(f'  {sector:<28} {int(s["n"]):>6} {s["alpha"]:>+8.2%} {s["win_rate"]:>8.1%} {s["t_stat"]:>8.2f}  {stars}')
    print()


def print_surprise_magnitude_breakdown(trades, col_weeks=4):
    """Break results by surprise magnitude quintile."""
    col = f'ret_{col_weeks}w'
    if col not in trades.columns or trades.empty:
        return

    t = trades.copy()
    t['surprise_abs'] = t['surprise_pct'].abs()
    t['magnitude'] = pd.qcut(t['surprise_abs'], q=5,
                              labels=['Q1 (smallest)', 'Q2', 'Q3', 'Q4', 'Q5 (largest)'])

    print(f'  SURPRISE MAGNITUDE ({col_weeks}w return, by abs surprise % quintile)')
    print(f'  {"Quintile":<16} {"N":>6} {"Alpha":>9} {"Win%":>8} {"t-stat":>8}  Sig')
    print('  ' + '-' * 55)
    for q in ['Q1 (smallest)', 'Q2', 'Q3', 'Q4', 'Q5 (largest)']:
        subset = t[t['magnitude'] == q]
        s = compute_stats(subset[col])
        if pd.isna(s['alpha']):
            print(f'  {q:<16} {int(s["n"]):>6} {"--":>9}')
        else:
            stars = sig_stars(s['p_value'])
            print(f'  {q:<16} {int(s["n"]):>6} {s["alpha"]:>+8.2%} {s["win_rate"]:>8.1%} {s["t_stat"]:>8.2f}  {stars}')
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description='PEAD Backtest')
    parser.add_argument('--beat', type=float, default=cfg.BEAT_THRESHOLD,
                        help=f'Bull threshold: EPS beat >= this %% (default: {cfg.BEAT_THRESHOLD})')
    parser.add_argument('--miss', type=float, default=cfg.MISS_THRESHOLD,
                        help=f'Bear threshold: EPS miss >= this %% (default: {cfg.MISS_THRESHOLD})')
    parser.add_argument('--sample', type=int, default=None,
                        help='Random sample N signals (fast test)')
    parser.add_argument('--export', action='store_true')
    return parser.parse_args()


def main():
    args = parse_args()

    if not DB_PATH.exists():
        print(f'ERROR: {DB_PATH} not found. Run collect_earnings.py first.')
        sys.exit(1)

    print()
    print_sep()
    print('  PEAD BACKTEST -- Post-Earnings Announcement Drift')
    print(f'  Run date:      {datetime.today().strftime("%Y-%m-%d")}')
    print(f'  DB:            {DB_PATH}')
    print(f'  BULL signal:   EPS surprise >= +{args.beat:.0f}%')
    print(f'  BEAR signal:   EPS surprise <= -{args.miss:.0f}%')
    print(f'  Entry:         {cfg.ENTRY_DELAY_DAYS} days after report date')
    print(f'  Horizons:      {cfg.HOLD_PERIODS} weeks')
    print_sep()

    # Load signals
    print(f'\n  Loading signals...', end=' ', flush=True)
    signals = load_signals(args.beat, args.miss, cfg.BACKTEST_START, cfg.BACKTEST_END)
    print(f'{len(signals):,} signals')
    print(f'  BULL: {(signals["signal"]=="BULL").sum():,}  BEAR: {(signals["signal"]=="BEAR").sum():,}')
    print(f'  Date range: {signals["report_date"].min().date()} to {signals["report_date"].max().date()}')
    print(f'  Unique tickers: {signals["ticker"].nunique():,}')

    if args.sample and args.sample < len(signals):
        print(f'  Sampling {args.sample} signals randomly...')
        signals = signals.sample(n=args.sample, random_state=42).reset_index(drop=True)

    # Compute forward returns
    trades = compute_forward_returns(signals, cfg.HOLD_PERIODS, cfg.ENTRY_DELAY_DAYS)
    print(f'  Trades with price data: {len(trades):,}')

    if trades.empty:
        print('  No trades. Check DB and price fetching.')
        return

    bull_trades = trades[trades['signal'] == 'BULL']
    bear_trades = trades[trades['signal'] == 'BEAR']

    # --- Results ---
    print()
    print_sep()
    print('  RESULTS')
    print_sep()

    print()
    print_stats_block('ALL signals', trades, cfg.HOLD_PERIODS)
    print_stats_block('BULL signals (EPS beat -> long)', bull_trades, cfg.HOLD_PERIODS)
    print_stats_block('BEAR signals (EPS miss -> short)', bear_trades, cfg.HOLD_PERIODS)

    print_sep('-')
    print_surprise_magnitude_breakdown(trades, col_weeks=4)

    print_sep('-')
    print_cap_breakdown(trades, col_weeks=4)

    print_sep('-')
    print_sector_breakdown(trades, col_weeks=4)

    print('  * p<0.10  ** p<0.05  *** p<0.01')

    # Export
    if args.export:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.today().strftime('%Y%m%d_%H%M%S')
        out_path = RESULTS_DIR / f'pead_backtest_{ts}.csv'
        trades.to_csv(out_path, index=False)
        print(f'\n  Exported to: {out_path}')


if __name__ == '__main__':
    main()
