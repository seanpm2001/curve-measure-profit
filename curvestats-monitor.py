#!/usr/bin/env python3

import lmdb
import json
import time
from time import sleep
from multiprocessing import Pool
from functools import partial

from curvestats.newpool import NewPool
from curvestats.tricrypto import Pool as CryptoPool
from curvestats.forexmeta import Pool as ForexPool

MPOOL_SIZE = 25

pools = {
        '2pool': (NewPool, ("0x7f90122BF0700F9E7e1F688fe926940E8839F353", "0x7f90122BF0700F9E7e1F688fe926940E8839F353"), 534055),
        'tricrypto': (CryptoPool, ("0x960ea3e3C7FB317332d990873d354E18d7645590", "0x8e0B8c8BB9db49a46697F3a5Bb8A308e744821D2"), 741890),
        'ren': (NewPool, ("0x3E01dD8a5E1fb3481F0F589056b428Fc308AF0Fb", "0x3E01dD8a5E1fb3481F0F589056b428Fc308AF0Fb"), 819996),
        'eursusd': (ForexPool, ("0xA827a652Ead76c6B0b3D19dba05452E06e25c27e", "0x3dFe1324A0ee9d86337d06aEB829dEb4528DB9CA", "0x7f90122BF0700F9E7e1F688fe926940E8839F353"), 2721013)
}
start_blocks = {}

DB_NAME = 'arbitrum.lmdb'  # <- DB [block][pool#]{...}


def init_pools():
    for i, p in pools.items():
        if isinstance(p, tuple):
            pools[i] = p[0](*p[1])
            start_blocks[i] = p[2]


def fetch_stats(block, i='compound'):
    try:
        init_pools()
        return pools[i].fetch_stats(block)
    except ValueError as e:
        if 'missing trie node' in str(e):
            print('missing trie node for', block)
            return {}
        else:
            raise


def int2uid(value):
    return int.to_bytes(value, 4, 'big')


def pools_not_in_block(tx, b):
    out = []
    block = tx.get(int2uid(b))
    if block:
        block = json.loads(block)
    if block:
        for k in pools:
            if k not in block:
                out.append(k)
    else:
        out = list(pools)
    return out


mpool = Pool(MPOOL_SIZE)
init_pools()


if __name__ == '__main__':
    from curvestats.w3 import w3 as our_w3
    w3 = our_w3()
    init_pools()

    db = lmdb.open(DB_NAME, map_size=(2 ** 35))

    start_block = w3.eth.getBlock('latest')['number'] - 900
    print('Monitor started')

    # Initial data
    with db.begin(write=True) as tx:
        if pools_not_in_block(tx, 0) or True:  # XXX
            tx.put(int2uid(0), json.dumps(
                        {k: {
                            'N': pool.N,
                            'underlying_N': pool.underlying_N if hasattr(pool, 'underlying_N') else pool.N,
                            'decimals': pool.decimals,
                            'underlying_decimals': pool.underlying_decimals if hasattr(pool, 'underlying_decimals') else pool.decimals,
                            'token': pool.token.address, 'pool': pool.pool.address,
                            'coins': [pool.coins[j].address for j in range(pool.N)],
                            'underlying_coins': [pool.underlying_coins[j].address for j in range(getattr(pool, 'underlying_N', pool.N))]}
                         for k, pool in pools.items()}).encode())

    while True:
        while True:
            try:
                current_block = w3.eth.getBlock('latest')['number'] + 1
                break
            except Exception:
                time.sleep(10)

        if current_block - start_block > MPOOL_SIZE:
            blocks = range(start_block, start_block + MPOOL_SIZE)
            with db.begin(write=True) as tx:
                pools_to_fetch = pools_not_in_block(tx, blocks[-1])
                if pools_to_fetch:
                    stats = {}
                    for p in pools_to_fetch:
                        if blocks[0] >= start_blocks[p]:
                            newstats = mpool.map(partial(fetch_stats, i=p), blocks)
                            for b, s in zip(blocks, newstats):
                                if b not in stats:
                                    stats[b] = {}
                                stats[b][p] = s
                    for b, v in stats.items():
                        block = tx.get(int2uid(b))
                        if block:
                            block = json.loads(block)
                            v.update(block)
                        tx.put(int2uid(b), json.dumps(v).encode())
                    pools_fetched = [p for p in pools_to_fetch
                                     if blocks[-1] in stats and p in stats[blocks[-1]]]
                    print('...', start_block, pools_fetched)
                else:
                    print('... already in DB:', start_block)
            start_block += MPOOL_SIZE

        else:
            if current_block > start_block:
                for block in range(start_block, current_block):
                    with db.begin(write=True) as tx:
                        if pools_not_in_block(tx, block):
                            stats = {}
                            for p, pool in pools.items():
                                if block >= start_blocks[p]:
                                    stats[p] = pool.fetch_stats(block)
                            print(block, [len(s['trades']) for s in stats.values()])
                            tx.put(int2uid(block), json.dumps(stats).encode())
                start_block = current_block

            sleep(15)
