import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SAMPLES = {key: APP_DIR.parent / 'sample_data' / f'{key}.csv' for key in ('hall', 'reference')}
BAND_CENTRES = (63, 125, 250, 500, 1000, 2000, 4000, 8000)
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000
RULE_VERSION = 'rt-schroeder-1'
# 校准口径独立于规则版本登记：数值只有在两侧口径一致时才能直接相减。
CALIBRATION_BASIS = 'pa-sens-gain-1'
# 差异快照自身的规则版本；变化时旧差异保留但需重算。
COMPARISON_VERSION = 'retrofit-diff-1'


def database_path():
    return Path(os.environ.get('DECAY_DB', 'measurements.sqlite3'))
