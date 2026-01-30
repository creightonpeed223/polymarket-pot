"""Check Polymarket positions"""

import asyncio
import os
from dotenv import load_dotenv
import httpx

load_dotenv()

async def check_positions():
    """Check positions via Polymarket API"""

    # The wallet address
    wallet = os.getenv("POLYMARKET_FUNDER_ADDRESS", "0x4f81dF42365Af8408F299B8ad01E3d92333C3A83")

    async with httpx.AsyncClient() as client:
        # Try to get positions from the gamma API
        try:
            # Check user's positions
            resp = await client.get(
                f"https://gamma-api.polymarket.com/positions",
                params={"user": wallet.lower()},
                timeout=10.0
            )

            if resp.status_code == 200:
                positions = resp.json()

                if not positions:
                    print("No open positions found")
                    return

                print(f"Found {len(positions)} positions:\n")

                for pos in positions:
                    market_id = pos.get("market", {}).get("id", "?")
                    question = pos.get("market", {}).get("question", "Unknown")[:50]
                    outcome = pos.get("outcome", "?")
                    size = float(pos.get("size", 0))
                    avg_price = float(pos.get("avgPrice", 0))
                    current_price = float(pos.get("currentPrice", 0))
                    pnl = float(pos.get("pnl", 0))

                    print(f"Market: {question}...")
                    print(f"  Outcome: {outcome}")
                    print(f"  Size: {size:.2f} shares")
                    print(f"  Entry: ${avg_price:.3f} | Current: ${current_price:.3f}")
                    print(f"  P&L: ${pnl:.2f}")
                    print()
            else:
                print(f"API returned {resp.status_code}: {resp.text[:200]}")

        except Exception as e:
            print(f"Error: {e}")

        # Also try the CLOB API for open orders
        try:
            resp = await client.get(
                f"https://clob.polymarket.com/positions",
                params={"user": wallet.lower()},
                timeout=10.0
            )
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    print("\nCLOB Positions:")
                    print(data)
        except:
            pass

if __name__ == "__main__":
    asyncio.run(check_positions())
