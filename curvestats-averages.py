#!/usr/bin/env python3

from collections import defaultdict
from time import time
import lmdb
import json

DB_NAME = 'fantom.lmdb'  # <- DB [block][pool#]{...}
DIR = 'json-ftm'
START_BLOCK = 2320910
TICKS = [1, 5, 10, 15, 30, 60 * 24]  # min
day_ago = time() - 86400

summarized_data = {}
db = lmdb.open(DB_NAME, map_size=(2 ** 35))


def int2uid(value):
    return int.to_bytes(value, 4, 'big')


def get_block(b):
    with db.begin(write=False) as tx:
        obj = tx.get(int2uid(b))
        if not obj:
            return False
        return json.loads(obj)


if __name__ == "__main__":
    b = START_BLOCK
    decimals = {
            '2pool': [18, 6],
            'fusdt': [6, 18],
            'ren': [8, 8],
            'tricrypto': [6, 8, 18],
            'ibftm': [18, 6, 6],
    }
    underlying_decimals = {'fusdt': [6, 18, 6], 'tricrypto': [6, 8, 18], 'ibftm': [18, 6, 6]}
    start_blocks = {}
    virtual_prices = []
    daily_volumes = defaultdict(float)
    pools = ['2pool', 'fusdt', 'ren', 'tricrypto', 'ibftm']
    andred_pools = set(['fusdt'])
    ctr = 0
    while True:
        block = get_block(b)
        if not block:
            ctr += 1
            b += 1
            if ctr > 100:
                break
            else:
                continue
        else:
            ctr = 0

        if 'timestamp' not in block[pools[0]]:
            b += 1
            continue

        virtual_prices.append(
                [block[pools[0]]['timestamp']] +
                [block[pool]['virtual_price'] / 1e18 if pool in block and 'virtual_price' in block[pool] else 0 for pool in pools])

        for pool in block:
            if 'timestamp' not in block[pool]:
                continue

            if pool not in summarized_data:
                summarized_data[pool] = {}

            if pool not in pools or start_blocks.get(pool, 0) >= b:
                continue

            for tick in TICKS:
                ts = block[pool]['timestamp'] // (tick * 60) * (tick * 60)
                if tick not in summarized_data[pool]:
                    summarized_data[pool][tick] = {}
                if ts not in summarized_data[pool][tick]:
                    summarized_data[pool][tick][ts] = {}
                obj = block[pool].copy()
                obj['volume'] = summarized_data[pool][tick][ts].get('volume', {})
                obj['prices'] = summarized_data[pool][tick][ts].get('prices', {})
                for t in obj['trades']:
                    pair = sorted([(t['sold_id'], t['tokens_sold']), (t['bought_id'], t['tokens_bought'])])
                    pair, tokens = list(zip(*pair))  # (id1, id2), (vol1, vol2)
                    jpair = '{}-{}'.format(*pair)
                    if t.get('underlying', False):
                        if pool in andred_pools and pair[0] >= 1 and t['sold_id'] == pair[0]:
                            t0 = tokens[0] * block[pool]['virtual_price'] // 10**18
                        else:
                            t0 = tokens[0] * 10 ** (18 - underlying_decimals[pool][pair[0]])
                        if pool in andred_pools and pair[1] >= 1 and t['sold_id'] == pair[1]:
                            t1 = tokens[1] * block[pool]['virtual_price'] // 10**18
                        else:
                            t1 = tokens[1] * 10 ** (18 - underlying_decimals[pool][pair[1]])
                    else:
                        t0 = tokens[0] * 10 ** (18 - decimals[pool][pair[0]])
                        t1 = tokens[1] * 10 ** (18 - decimals[pool][pair[1]])
                    if tick == 5 and ts > day_ago:
                        daily_volumes[pool] += (t0 + t1) / (2 * 1e18)
                    if t1 > 0 and t0 > 0:
                        price = t1 / t0
                        if jpair not in obj['prices']:
                            obj['prices'][jpair] = []
                        obj['prices'][jpair].append(price)
                    if jpair in obj['volume']:
                        obj['volume'][jpair] = (obj['volume'][jpair][0] + tokens[0]), (obj['volume'][jpair][1] + tokens[1])
                    else:
                        obj['volume'][jpair] = tokens
                del obj['trades']
                for jpair in obj['prices']:
                    prices = obj['prices'][jpair]
                    # OHLC
                    if prices:
                        obj['prices'][jpair] = [prices[0], min(prices), max(prices), prices[-1]]
                summarized_data[pool][tick][ts] = obj

        b += 1

    last_time, *p_last = virtual_prices[-1]
    first_time, *p_first = virtual_prices[0]

    day_ix = [(abs(vp[0] - (last_time - 86400)), tuple(vp)) for i, vp in enumerate(virtual_prices)]
    week_ix = [(abs(vp[0] - (last_time - 7 * 86400)), tuple(vp)) for i, vp in enumerate(virtual_prices)]
    month_ix = [(abs(vp[0] - (last_time - 30 * 86400)), tuple(vp)) for i, vp in enumerate(virtual_prices)]

    vps = {'day': min(day_ix)[1],
           'week': min(week_ix)[1],
           'month': min(month_ix)[1]}

    profits = {
        interval: {
            pool: ((last / v) ** (86400 / (last_time - vp[0]))) ** 365 - 1
            if v > 0 else 0
            for pool, v, last in zip(pools, vp[1:], p_last)}
        for interval, vp in vps.items()
    }
    profits['total'] = {}
    for i, pool in enumerate(pools):
        try:
            t, v = [(vp[0], vp[i + 1]) for vp in virtual_prices if vp[i + 1] > 0][0]
            profits['total'][pool] = ((p_last[i] / v) ** (86400 / (last_time - t))) ** 365 - 1
        except IndexError:
            profits['total'][pool] = 0

    for pool in summarized_data:
        for t in summarized_data[pool]:
            data = sorted(summarized_data[pool][t].values(), key=lambda x: x['timestamp'])[-1000:]
            with open(f'{DIR}/{pool}-{t}m.json', 'w') as f:
                json.dump(data, f)
    with open(f'{DIR}/virtual-prices.json', 'w') as f:
        json.dump({
            'pools': pools,
            'virtual_prices': virtual_prices}, f)
    with open(f'{DIR}/apys.json', 'w') as f:
        json.dump({'apy': profits, 'volume': daily_volumes}, f)
