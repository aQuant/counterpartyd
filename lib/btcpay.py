#! /usr/bin/python3

import binascii
import struct
import logging

from . import (util, config, exceptions, bitcoin, util)

FORMAT = '>32s32s'
LENGTH = 32 + 32
ID = 11


def validate (db, order_match_id):
    problems = []

    cursor = db.cursor()
    cursor.execute('''SELECT * FROM order_matches \
                      WHERE (validity = ? AND id = ?)''', ('Valid: awaiting BTC payment', order_match_id))
    order_matches = cursor.fetchall()
    cursor.close()
    if len(order_matches) == 0:
        problems.append('invalid order match ID, {}'.format(order_match_id))
        order_match = None
    elif len(order_matches) > 1:
        assert False
    else:
        order_match = order_matches[0]

    return order_match, problems

def create (db, order_match_id, unsigned=False):
    tx0_hash, tx1_hash = order_match_id[:64], order_match_id[64:] # UTF-8 encoding means that the indices are doubled.

    # Try to match.
    order_match, problems = validate(db, order_match_id)
    if problems: raise exceptions.BTCPayError(problems)

    # Figure out to which address the BTC are being paid.
    if order_match['backward_asset'] == 'BTC':
        source = order_match['tx1_address']
        destination = order_match['tx0_address']
        btc_amount = order_match['backward_amount']
    else:
        source = order_match['tx0_address']
        destination = order_match['tx1_address']
        btc_amount = order_match['forward_amount']

    tx0_hash_bytes, tx1_hash_bytes = binascii.unhexlify(bytes(tx0_hash, 'utf-8')), binascii.unhexlify(bytes(tx1_hash, 'utf-8'))
    data = config.PREFIX + struct.pack(config.TXTYPE_FORMAT, ID)
    data += struct.pack(FORMAT, tx0_hash_bytes, tx1_hash_bytes)
    return bitcoin.transaction(source, destination, btc_amount, config.MIN_FEE, data, unsigned=unsigned)

def parse (db, tx, message):
    btcpay_parse_cursor = db.cursor()

    # Unpack message.
    try:
        assert len(message) == LENGTH
        tx0_hash_bytes, tx1_hash_bytes = struct.unpack(FORMAT, message)
        tx0_hash, tx1_hash = binascii.hexlify(tx0_hash_bytes).decode('utf-8'), binascii.hexlify(tx1_hash_bytes).decode('utf-8')
        order_match_id = tx0_hash + tx1_hash
        validity = 'Valid'
    except struct.error as e:
        tx0_hash, tx1_hash = None, None
        validity = 'Invalid: could not unpack'

    if validity == 'Valid':
        # Try to match.
        order_match, problems = validate(db, order_match_id)
        if problems: validity = 'Invalid: ' + ';'.join(problems)

    if validity == 'Valid':
        # Credit source address for the currency that he bought with the bitcoins.
        # BTC must be paid all at once and come from the 'correct' address.
        if order_match['tx0_address'] == tx['source'] and tx['btc_amount'] >= order_match['forward_amount']:
            btcpay_parse_cursor.execute('''UPDATE order_matches SET validity=? WHERE (tx0_hash=? AND tx1_hash=?)''', ('Valid', tx0_hash, tx1_hash))
            if order_match['backward_asset'] != 'BTC':
                util.credit(db, tx['block_index'], tx['source'], order_match['backward_asset'], order_match['backward_amount'])
            validity = 'Paid'
        if order_match['tx1_address'] == tx['source'] and tx['btc_amount'] >= order_match['backward_amount']:
            btcpay_parse_cursor.execute('''UPDATE order_matches SET validity=? WHERE (tx0_hash=? AND tx1_hash=?)''', ('Valid', tx0_hash, tx1_hash))
            if order_match['forward_asset'] != 'BTC':
                util.credit(db, tx['block_index'], tx['source'], order_match['forward_asset'], order_match['forward_amount'])
            validity = 'Paid'
        logging.info('BTC Payment for Order Match: {} ({})'.format(order_match_id, tx['tx_hash']))

    # Add parsed transaction to message-type–specific table.
    element_data = {
        'tx_index': tx['tx_index'],
        'tx_hash': tx['tx_hash'],
        'block_index': tx['block_index'],
        'source': tx['source'],
        'order_match_id': order_match_id,
        'validity': validity,
    }
    btcpay_parse_cursor.execute(*util.get_insert_sql('btcpays', element_data))


    btcpay_parse_cursor.close()

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
