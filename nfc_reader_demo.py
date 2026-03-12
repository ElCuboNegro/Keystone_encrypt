#!/usr/bin/env python3
"""
nfc_reader_demo.py -- Demo: monitor NFC card events using keystone_nfc.

Usage:
    python DEMO/nfc_reader_demo.py
    python DEMO/nfc_reader_demo.py --once
    python DEMO/nfc_reader_demo.py --list-readers
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from keystone_nfc import CardInfo, KeystoneReader
from keystone_nfc.exceptions import NoCardError, NoReaderError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)


def run_event_loop(reader_name=None):
    reader = KeystoneReader(reader_name)

    @reader.on_card_inserted
    def inserted(card: CardInfo):
        print()
        print('=' * 50)
        print('CARD DETECTED')
        print('=' * 50)
        print(card)
        print('=' * 50)

    @reader.on_card_removed
    def removed():
        print('[  ] Card removed — waiting...')

    @reader.on_error
    def error(exc):
        print(f'[!] Monitor error: {exc}')

    print('Monitoring for cards. Press Ctrl+C to stop.')
    print(f'Reader: {reader._resolve_reader()}')
    print()

    with reader:
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print('\nStopped.')


def run_once(reader_name=None, timeout=30.0):
    reader = KeystoneReader(reader_name)
    print(f'Waiting for card (timeout={timeout}s)...')
    card = reader.read_once(timeout=timeout)
    print()
    print('=' * 50)
    print(card)
    print('=' * 50)


def list_readers():
    readers = KeystoneReader().available_readers()
    if not readers:
        print('No readers found.')
    else:
        print('Available readers:')
        for i, r in enumerate(readers):
            print(f'  [{i}] {r}')


def main():
    parser = argparse.ArgumentParser(description='keystone_nfc demo')
    parser.add_argument('--once', action='store_true',
                        help='Read one card then exit')
    parser.add_argument('--list-readers', action='store_true',
                        help='Print available readers and exit')
    parser.add_argument('--reader', '-r', default=None,
                        help='Reader name or substring (default: first available)')
    parser.add_argument('--timeout', type=float, default=30.0,
                        help='Timeout for --once mode (default: 30s)')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    if args.debug:
        logging.getLogger('keystone_nfc').setLevel(logging.DEBUG)

    try:
        if args.list_readers:
            list_readers()
        elif args.once:
            run_once(args.reader, args.timeout)
        else:
            run_event_loop(args.reader)

    except NoReaderError as e:
        print(f'[ERROR] {e}')
        sys.exit(1)
    except NoCardError as e:
        print(f'[TIMEOUT] {e}')
        sys.exit(2)


if __name__ == '__main__':
    main()
