# PEAD Backtest Configuration -- EXAMPLE (safe for GitHub)
FMP_API_KEY = 'your_fmp_api_key_here'
FMP_BASE_URL = 'https://financialmodelingprep.com/stable'
EARNINGS_DB = 'pead_data.db'
UNIVERSE = 'sp500'
BEAT_THRESHOLD = 5.0
MISS_THRESHOLD = 5.0
ENTRY_DELAY_DAYS = 2
MIN_ABS_EPS = 0.01
HOLD_PERIODS = [2, 4, 8]
BACKTEST_START = '2018-01-01'
BACKTEST_END = None
FMP_REQUEST_DELAY = 0.25
RESULTS_DIR = 'results'
