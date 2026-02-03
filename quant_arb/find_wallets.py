"""
Find wallet addresses for Polymarket traders
Run this to get the wallet addresses you need for whale_tracker.py
"""

import asyncio
import httpx


TRADERS = [
    "ilovecircle",
    "Axios",
    "abeautifulmind",
    "Domer",
]


async def get_wallet_address(username: str) -> str:
    """Fetch wallet address for a Polymarket username via leaderboard API"""
    url = "https://data-api.polymarket.com/leaderboard"

    async with httpx.AsyncClient() as client:
        try:
            # Try to find user in leaderboard
            response = await client.get(
                url,
                params={
                    "userName": username,
                    "limit": 1,
                },
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0].get("proxyWallet", "NOT FOUND")
                return "NOT FOUND in leaderboard"
            else:
                return f"ERROR: {response.status_code}"

        except Exception as e:
            return f"ERROR: {e}"


async def main():
    print("=" * 60)
    print("POLYMARKET WALLET FINDER")
    print("=" * 60)
    print()

    print("Looking up wallet addresses...")
    print()

    results = {}

    for username in TRADERS:
        wallet = await get_wallet_address(username)
        results[username] = wallet

        if wallet.startswith("0x"):
            print(f"  {username}: {wallet}")
        else:
            print(f"  {username}: {wallet}")

    print()
    print("=" * 60)
    print("Copy these into whale_tracker.py TRADERS_TO_TRACK:")
    print("=" * 60)
    print()
    print("TRADERS_TO_TRACK = {")
    for username, wallet in results.items():
        if wallet.startswith("0x"):
            print(f'    "{username}": "{wallet}",')
        else:
            print(f'    "{username}": "",  # {wallet}')
    print("}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
