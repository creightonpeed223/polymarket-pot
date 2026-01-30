"""
Cross-Platform Market Monitor
Shows market status and watches for arbitrage opportunities
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from difflib import SequenceMatcher

from dotenv import load_dotenv

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quant_arb.platforms import PolymarketConnector, KalshiConnector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suppress library noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)


def normalize(text):
    """Normalize text for comparison"""
    import re
    text = text.lower()
    text = re.sub(r'[^a-z0-9 ]', '', text)
    return ' '.join(text.split())


def find_matches(poly_markets, kalshi_markets, threshold=0.6):
    """Find matching markets across platforms"""
    matches = []
    seen = set()

    for pm in poly_markets:
        pq = normalize(pm.question)

        for km in kalshi_markets:
            if km.id in seen:
                continue
            kq = normalize(km.question)

            # Quick filter: check for common meaningful words
            p_words = set(pq.split())
            k_words = set(kq.split())
            common = p_words & k_words
            skip_words = {'will', 'the', 'be', 'in', 'a', 'of', 'to', 'and', 'or', 'before', 'after', 'next'}
            meaningful = common - skip_words

            if len(meaningful) < 2:
                continue

            ratio = SequenceMatcher(None, pq, kq).ratio()
            if ratio > threshold:
                matches.append((ratio, pm, km))
                seen.add(km.id)
                break

    return sorted(matches, key=lambda x: -x[0])


async def main():
    load_dotenv()

    print("=" * 70)
    print("CROSS-PLATFORM MARKET MONITOR")
    print("=" * 70)
    print()

    # Connect to platforms
    poly = PolymarketConnector(
        private_key=os.environ.get("POLYMARKET_PRIVATE_KEY"),
        funder_address=os.environ.get("POLYMARKET_FUNDER_ADDRESS"),
    )
    kalshi = KalshiConnector(
        api_key=os.environ.get("KALSHI_API_KEY"),
        private_key_path=os.environ.get("KALSHI_PRIVATE_KEY"),
    )

    print("Connecting to Polymarket...")
    await poly.connect()
    print("Connecting to Kalshi...")
    await kalshi.connect()

    scan_count = 0

    try:
        while True:
            scan_count += 1
            print(f"\n{'='*70}")
            print(f"SCAN #{scan_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 70)

            # Load markets
            print("\nLoading markets...")
            poly_markets = await poly.load_markets()
            kalshi_markets = await kalshi.load_markets()

            print(f"  Polymarket: {len(poly_markets)} markets")
            print(f"  Kalshi:     {len(kalshi_markets)} markets")

            # Find matches
            matches = find_matches(poly_markets, kalshi_markets, threshold=0.5)

            print(f"\n  Cross-platform matches: {len(matches)}")

            if matches:
                print("\n  POTENTIAL ARBITRAGE OPPORTUNITIES:")
                for ratio, pm, km in matches[:5]:
                    print(f"\n  {int(ratio*100)}% match:")
                    print(f"    Poly:   {pm.question[:60]}")
                    print(f"    Kalshi: {km.question[:60]}")
            else:
                print("\n  No matching markets found. Platforms have different market catalogs.")
                print("  The bot will continue monitoring for new opportunities...")

            # Show some market samples
            print("\n" + "-" * 70)
            print("SAMPLE MARKETS:")
            print("-" * 70)

            print("\nPolymarket (top 5 by volume):")
            for m in sorted(poly_markets, key=lambda x: -x.volume)[:5]:
                print(f"  ${m.volume:>12,.0f} | {m.question[:50]}")

            print("\nKalshi (top 5 by volume):")
            for m in sorted(kalshi_markets, key=lambda x: -x.volume)[:5]:
                print(f"  ${m.volume:>12,.0f} | {m.question[:50]}")

            # Wait before next scan
            print(f"\n[Next scan in 5 minutes... Press Ctrl+C to stop]")
            await asyncio.sleep(300)

    except KeyboardInterrupt:
        print("\n\nStopping monitor...")
    finally:
        await poly.disconnect()
        await kalshi.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
