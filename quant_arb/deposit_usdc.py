"""
Deposit USDC into Polymarket
"""

import asyncio
import os
from dotenv import load_dotenv
from web3 import Web3
import json

load_dotenv()

# Polygon RPC
RPC_URL = "https://polygon-rpc.com"

# Contract addresses on Polygon
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # Bridged USDC
POLYMARKET_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # CTF Exchange

# ERC20 ABI (minimal for approve and transfer)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    }
]


def deposit_to_polymarket(amount_usdc: float):
    """Deposit USDC into Polymarket"""

    # Load credentials
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        print("Error: POLYMARKET_PRIVATE_KEY not found in .env")
        return False

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    # Connect to Polygon
    w3 = Web3(Web3.HTTPProvider(RPC_URL))

    if not w3.is_connected():
        print("Error: Could not connect to Polygon RPC")
        return False

    # Get account from private key
    account = w3.eth.account.from_key(private_key)
    wallet_address = account.address
    print(f"Wallet: {wallet_address}")

    # Check MATIC balance for gas
    matic_balance = w3.eth.get_balance(wallet_address)
    matic_balance_eth = w3.from_wei(matic_balance, 'ether')
    print(f"MATIC balance: {matic_balance_eth:.4f}")

    if matic_balance_eth < 0.01:
        print("Error: Not enough MATIC for gas")
        return False

    # Setup USDC contract
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI)

    # Check USDC balance
    usdc_balance = usdc.functions.balanceOf(wallet_address).call()
    usdc_balance_human = usdc_balance / 1e6
    print(f"USDC balance: ${usdc_balance_human:.2f}")

    # Amount to deposit (USDC has 6 decimals)
    amount_wei = int(amount_usdc * 1e6)

    if usdc_balance < amount_wei:
        print(f"Error: Not enough USDC. Have ${usdc_balance_human:.2f}, need ${amount_usdc:.2f}")
        return False

    print(f"\nDepositing ${amount_usdc:.2f} USDC to Polymarket...")

    # Check current allowance
    current_allowance = usdc.functions.allowance(wallet_address, POLYMARKET_EXCHANGE).call()
    print(f"Current allowance: ${current_allowance / 1e6:.2f}")

    # Step 1: Approve USDC spending if needed
    if current_allowance < amount_wei:
        print("\nStep 1: Approving USDC spending...")

        # Approve a large amount to avoid repeated approvals
        approve_amount = int(1e12)  # $1M worth

        nonce = w3.eth.get_transaction_count(wallet_address)
        gas_price = w3.eth.gas_price

        approve_tx = usdc.functions.approve(
            Web3.to_checksum_address(POLYMARKET_EXCHANGE),
            approve_amount
        ).build_transaction({
            'from': wallet_address,
            'nonce': nonce,
            'gas': 100000,
            'gasPrice': gas_price,
            'chainId': 137
        })

        signed_tx = w3.eth.account.sign_transaction(approve_tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"Approve tx: {tx_hash.hex()}")

        # Wait for confirmation
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt['status'] == 1:
            print("✓ Approval confirmed")
        else:
            print("✗ Approval failed")
            return False
    else:
        print("✓ Already approved")

    # Step 2: The USDC is now approved.
    # On Polymarket, you don't "deposit" - you just need approval.
    # When you place orders, the USDC is transferred automatically.

    print("\n" + "="*50)
    print("✓ USDC approved for Polymarket trading!")
    print(f"✓ You can now trade with up to ${usdc_balance_human:.2f}")
    print("="*50)
    print("\nNote: Polymarket doesn't require a separate deposit.")
    print("Your USDC stays in your wallet and is used when orders fill.")
    print("\nGo back to Polymarket and try selling your positions now.")

    return True


if __name__ == "__main__":
    import sys

    amount = 100.0  # Default $100
    if len(sys.argv) > 1:
        amount = float(sys.argv[1])

    deposit_to_polymarket(amount)
