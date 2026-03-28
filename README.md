# PEAD Backtest

Tests Post-Earnings Announcement Drift: whether stocks continue to drift
in the direction of earnings surprises in the weeks following announcements.

## Hypothesis
When a company's EPS significantly beats analyst consensus, the stock
continues to drift upward for weeks. When it significantly misses, it
continues to drift downward. Markets underreact to earnings news initially.

## Data Source
FMP stable/earnings API (included in Starter plan).
Returns actual vs estimated EPS for thousands of tickers going back to 2010+.

## Setup

```bash
pip install pandas numpy yfinance scipy
cp config_example.py config.py
# Add your FMP API key to config.py
```

## Two-Step Process

### Step 1: Collect earnings data (run once, ~15-20 min for S&P 500)
```bash
python3 collect_earnings.py
python3 collect_earnings.py --validate   # Check DB summary
python3 collect_earnings.py --resume     # Resume if interrupted
```

### Step 2: Run backtest
```bash
python3 pead_backtest.py                     # Full backtest
python3 pead_backtest.py --sample 1000       # Fast sample test
python3 pead_backtest.py --beat 10 --miss 10 # Stricter thresholds
python3 pead_backtest.py --export            # Save CSV
```

## Output
- Alpha, win rate, t-stat at 2, 4, 8-week horizons
- BULL vs BEAR signal breakdown
- Surprise magnitude quintile breakdown
- Market cap tier breakdown (Large/Mid/Small)
- Sector breakdown

## Disclaimer
Research only. Not financial advice. MIT License.
