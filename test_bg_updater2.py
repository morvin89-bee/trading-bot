import sys
sys.path.insert(0, r'C:\Users\KakiTangan\trading-bot')
from dashboard.utils.shared_state import SharedState
from dashboard.utils.background_updater import BackgroundUpdater
import strategy_mean_reversion as mr
import time

state = SharedState('test_bg3.json')
strategy = mr.MeanReversionStrategy(config_path=r'C:\Users\KakiTangan\trading-bot\config_strategy.yaml')
pairs = ['btc_idr', 'eth_idr']

updater = BackgroundUpdater(state, strategy, pairs, interval=2)
updater.start()
print('Started')

for i in range(5):
    time.sleep(3)
    data = state.get('market_data', {})
    print('Iteration {}: {} pairs'.format(i+1, len(data)))
    for k, v in list(data.items())[:2]:
        z_I = v.get('z_I_to_T')
        z_T = v.get('z_T_to_I')
        print('  {}: z_I={}, z_T={}'.format(k, z_I, z_T))

updater.stop()
print('Stopped')