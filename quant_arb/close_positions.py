"""
Close all Polymarket positions using API credentials
"""

import asyncio
import os
import time
import json
import hmac
import hashlib
import base64
import secrets
from dotenv import load_dotenv
from web3 import Web3
import httpx
from eth_account.messages import encode_typed_data

load_dotenv()

# Polymarket CLOB API
CLOB_URL = "https://clob.polymarket.com"

# EIP-712 Domain for order signing
ORDER_DOMAIN = {
    "name": "Polymarket CTF Exchange",
    "version": "1",
    "chainId": 137,
}

ORDER_TYPES = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ]
}


def create_hmac_signature(api_secret: str, timestamp: str, method: str, path: str, body: str = "") -> str:
    """Create HMAC signature for Polymarket API"""
    message = f"{timestamp}{method}{path}{body}"
    secret_bytes = base64.b64decode(api_secret)
    signature = hmac.new(secret_bytes, message.encode('utf-8'), hashlib.sha256)
    return base64.b64encode(signature.digest()).decode('utf-8')


def get_l2_headers(api_key: str, api_secret: str, passphrase: str, address: str,
                   method: str, path: str, body: str = "") -> dict:
    """Generate L2 authentication headers with all 5 required fields"""
    timestamp = str(int(time.time()))
    signature = create_hmac_signature(api_secret, timestamp, method, path, body)

    return {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": timestamp,
        "POLY_API_KEY": api_key,
        "POLY_PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


async def get_positions(wallet: str) -> list:
    """Get current positions"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://data-api.polymarket.com/positions",
            params={"user": wallet.lower()},
            timeout=10.0
        )
        if resp.status_code == 200:
            return resp.json()
    return []


async def get_orderbook(token_id: str) -> dict:
    """Get orderbook for a token"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{CLOB_URL}/book",
            params={"token_id": token_id},
            timeout=10.0
        )
        if resp.status_code == 200:
            return resp.json()
    return {}


async def close_positions():
    """Close all positions by selling"""

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    api_key = os.getenv("POLYMARKET_API_KEY")
    api_secret = os.getenv("POLYMARKET_API_SECRET")
    passphrase = os.getenv("POLYMARKET_PASSPHRASE")

    if not all([private_key, api_key, api_secret, passphrase]):
        print("Error: Missing credentials in .env")
        print(f"  Private key: {'✓' if private_key else '✗'}")
        print(f"  API key: {'✓' if api_key else '✗'}")
        print(f"  API secret: {'✓' if api_secret else '✗'}")
        print(f"  Passphrase: {'✓' if passphrase else '✗'}")
        return

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    w3 = Web3()
    account = w3.eth.account.from_key(private_key)
    wallet = account.address

    print(f"Wallet: {wallet}")
    print(f"API Key: {api_key[:8]}...")
    print("="*60)

    # Get positions
    positions = await get_positions(wallet)

    if not positions:
        print("No positions found!")
        return

    print(f"Found {len(positions)} positions\n")

    open_positions = [p for p in positions if not p.get('redeemable', False) and float(p.get('size', 0)) > 0.1]
    redeemable = [p for p in positions if p.get('redeemable', False)]

    if redeemable:
        print(f"Redeemable positions (market ended): {len(redeemable)}")
        for p in redeemable:
            print(f"  - {p.get('title', '?')[:40]}... (go to Polymarket to redeem)")
        print()

    if not open_positions:
        print("No open positions to close!")
        return

    print(f"Open positions to sell: {len(open_positions)}\n")

    async with httpx.AsyncClient() as client:
        for pos in open_positions:
            title = pos.get('title', 'Unknown')[:45]
            token_id = pos.get('asset')
            size = float(pos.get('size', 0))
            current_price = float(pos.get('curPrice', 0))

            print(f"Selling: {title}...")
            print(f"  Size: {size:.2f} shares")
            print(f"  Current price: ${current_price:.4f}")

            # Get orderbook to find best bid
            book = await get_orderbook(token_id)
            bids = book.get('bids', [])

            if not bids:
                print(f"  ⚠ No bids - placing limit sell at ${current_price * 0.9:.4f}")
                sell_price = max(0.001, current_price * 0.9)
            else:
                best_bid = float(bids[0].get('price', current_price * 0.9))
                sell_price = best_bid
                print(f"  Best bid: ${best_bid:.4f}")

            # Round size down
            sell_size = int(size * 100) / 100

            # Create order
            salt = secrets.randbelow(2**128)
            expiration = int(time.time()) + 86400  # 24 hours

            # For SELL orders on Polymarket:
            # makerAmount = shares to sell (in token units)
            # takerAmount = USDC to receive (in USDC units)
            maker_amount = int(sell_size * 1e6)
            taker_amount = int(sell_size * sell_price * 1e6)

            order_data = {
                "salt": salt,
                "maker": wallet,
                "signer": wallet,
                "taker": "0x0000000000000000000000000000000000000000",
                "tokenId": int(token_id),
                "makerAmount": maker_amount,
                "takerAmount": taker_amount,
                "expiration": expiration,
                "nonce": 0,
                "feeRateBps": 0,
                "side": 1,  # SELL
                "signatureType": 2,  # EOA
            }

            # Sign order with EIP-712
            typed_data = {
                "types": {
                    "EIP712Domain": [
                        {"name": "name", "type": "string"},
                        {"name": "version", "type": "string"},
                        {"name": "chainId", "type": "uint256"},
                    ],
                    **ORDER_TYPES
                },
                "primaryType": "Order",
                "domain": ORDER_DOMAIN,
                "message": order_data
            }

            encoded = encode_typed_data(full_message=typed_data)
            signed = w3.eth.account.sign_message(encoded, private_key=private_key)

            # Prepare API request
            order_payload = {
                "order": {
                    "salt": str(salt),
                    "maker": wallet,
                    "signer": wallet,
                    "taker": "0x0000000000000000000000000000000000000000",
                    "tokenId": str(token_id),
                    "makerAmount": str(maker_amount),
                    "takerAmount": str(taker_amount),
                    "expiration": str(expiration),
                    "nonce": "0",
                    "feeRateBps": "0",
                    "side": "SELL",
                    "signatureType": 2,
                },
                "signature": signed.signature.hex(),
                "owner": wallet,
                "orderType": "GTC",
            }

            body = json.dumps(order_payload)
            path = "/order"
            headers = get_l2_headers(api_key, api_secret, passphrase, wallet, "POST", path, body)

            try:
                resp = await client.post(
                    f"{CLOB_URL}{path}",
                    headers=headers,
                    content=body,
                    timeout=15.0
                )

                if resp.status_code == 200:
                    result = resp.json()
                    print(f"  ✓ Order placed: {result.get('orderID', 'OK')}")
                else:
                    print(f"  ✗ Failed: {resp.status_code} - {resp.text[:200]}")

            except Exception as e:
                print(f"  ✗ Error: {e}")

            print()
            await asyncio.sleep(0.5)

    print("="*60)
    print("Done! Check Polymarket to see your open sell orders.")


if __name__ == "__main__":
    asyncio.run(close_positions())
