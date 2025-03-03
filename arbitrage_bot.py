"""
Arbitrage Trading Bot for Uniswap V2 and Crystal DEX
=================================================
Created by: ASHREI3126
Updated at: 2025-03-01
"""

import os
from web3 import Web3
from web3.contract import Contract
from eth_account import Account
from dotenv import load_dotenv
import json
import time
from decimal import Decimal
from typing import Tuple, Dict, Optional
from web3.types import TxParams, Wei, Address

# Enable account features
Account.enable_unaudited_hdwallet_features()

# Load environment variables
load_dotenv("Monad.env")

# Network and contract addresses
NETWORK_NAME = os.getenv("NETWORK_NAME", "Monad Testnet")
CHAIN_ID = int(os.getenv("CHAIN_ID", "10143"))
RPC_ENDPOINT = os.getenv("RPC_ENDPOINT")

# Convert addresses to checksum format
UNISWAP_V2_ROUTER02 = Web3.to_checksum_address(os.getenv("UNISWAP_V2_ROUTER02"))
WRAPPED_MONAD = Web3.to_checksum_address(os.getenv("WRAPPED_MONAD"))
CRYSTAL_ROUTER = Web3.to_checksum_address(os.getenv("CRYSTAL_ROUTER"))
USDC = Web3.to_checksum_address(os.getenv("USDC"))

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("PRIVATE_KEY not found in environment variables")

# Initialize Web3
web3 = Web3(Web3.HTTPProvider(RPC_ENDPOINT))
if not web3.is_connected():
    raise ConnectionError(f"Unable to connect to {NETWORK_NAME} at {RPC_ENDPOINT}")

print(f"Connected to {NETWORK_NAME} with Chain ID {CHAIN_ID}")

# Load Uniswap and Crystal ABI
try:
    with open("UniswapV2Router02.json") as f:
        router_abi = json.load(f)
except FileNotFoundError:
    raise FileNotFoundError("UniswapV2Router02.json not found")

# Contract instances
uniswap_router = web3.eth.contract(address=UNISWAP_V2_ROUTER02, abi=router_abi)
crystal_router = web3.eth.contract(address=CRYSTAL_ROUTER, abi=router_abi)

# Trading Constants
MAX_GAS_PRICE = 200  # Maximum gas price in gwei
MIN_PROFIT_MON = 0.015  # Minimum profit threshold in MON
SLIPPAGE = 0.995  # 0.5% slippage tolerance
MAX_GAS = 300000  # Maximum gas limit per transaction

def get_optimized_gas_price():
    """現在のガス価格を取得し、MAX_GAS_PRICE よりも低い場合はそれを使用"""
    current_gas_price = web3.eth.gas_price / 1e9  # gwei単位
    return min(current_gas_price, MAX_GAS_PRICE)

def get_dynamic_swap_amount(price_diff: float) -> int:
    """価格差に応じてスワップ量を調整"""
    if price_diff > 0.2:
        return 5 * (10 ** 18)  # 5 MON
    elif price_diff > 0.1:
        return 3 * (10 ** 18)  # 3 MON
    else:
        return 2 * (10 ** 18)  # 2 MON

def build_and_send_tx(tx: TxParams, private_key: str) -> Tuple[Optional[bytes], int]:
    """トランザクションをビルド、署名、送信"""
    try:
        signed_tx = Account.sign_transaction(tx, private_key)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        return tx_hash, receipt.gasUsed
    except Exception as e:
        print(f"Transaction failed: {str(e)}")
        return None, 0

def execute_exact_eth_for_tokens(router: Contract, amount_out_min: Wei, to: Address, deadline: int, amount_in: Wei) -> Tuple[Optional[bytes], int]:
    """MON (ETH相当) → USDC へのスワップ"""
    nonce = web3.eth.get_transaction_count(web3.eth.default_account)
    gas_price = web3.to_wei(get_optimized_gas_price(), 'gwei')
    
    tx = router.functions.swapExactETHForTokens(
        amount_out_min,
        [WRAPPED_MONAD, USDC],
        to,
        deadline
    ).build_transaction({
        'chainId': CHAIN_ID,
        'gas': MAX_GAS,
        'gasPrice': gas_price,
        'nonce': nonce,
        'value': amount_in
    })
    
    tx_hash, gas_used = build_and_send_tx(tx, PRIVATE_KEY)
    if tx_hash:
        print(f"✅ SwapExactETHForTokens successful")
        print(f"🔗 Transaction hash: {web3.to_hex(tx_hash)}")
    return tx_hash, gas_used

def execute_tokens_for_exact_eth(router: Contract, amount_out: Wei, amount_in_max: Wei, to: Address, deadline: int) -> Tuple[Optional[bytes], int]:
    """USDC → MON (ETH相当) へのスワップ"""
    nonce = web3.eth.get_transaction_count(web3.eth.default_account)
    gas_price = web3.to_wei(get_optimized_gas_price(), 'gwei')
    
    tx = router.functions.swapTokensForExactETH(
        amount_out,
        amount_in_max,
        [USDC, WRAPPED_MONAD],
        to,
        deadline
    ).build_transaction({
        'chainId': CHAIN_ID,
        'gas': MAX_GAS,
        'gasPrice': gas_price,
        'nonce': nonce,
        'value': 0
    })
    
    tx_hash, gas_used = build_and_send_tx(tx, PRIVATE_KEY)
    if tx_hash:
        print(f"✅ SwapTokensForExactETH successful")
        print(f"🔗 Transaction hash: {web3.to_hex(tx_hash)}")
    return tx_hash, gas_used

def check_arbitrage_opportunity():
    """価格差をチェックし、取引を実行するか判断"""
    current_gas_price = get_optimized_gas_price()
    price_uniswap = get_price(uniswap_router, WRAPPED_MONAD, USDC, 5 * (10 ** 18))
    price_crystal = get_price(crystal_router, WRAPPED_MONAD, USDC, 5 * (10 ** 18))

    price_uniswap_usdc = price_uniswap / (10 ** 6)
    price_crystal_usdc = price_crystal / (10 ** 6)
    price_diff_usdc = abs(price_uniswap_usdc - price_crystal_usdc) / 5

    swap_amount = get_dynamic_swap_amount(price_diff_usdc)
    
    if price_uniswap_usdc > price_crystal_usdc:
        profit_info = calculate_mon_profit(price_uniswap_usdc, price_crystal_usdc, swap_amount / (10 ** 18), current_gas_price)
    else:
        profit_info = calculate_mon_profit(price_crystal_usdc, price_uniswap_usdc, swap_amount / (10 ** 18), current_gas_price)

    print(f"\nガス価格: {current_gas_price:.2f} gwei")
    print(f"スワップ量: {swap_amount / (10 ** 18)} MON")
    print(f"利益: {profit_info['profit_mon']:.6f} MON")

if __name__ == "__main__":
    try:
        account = Account.from_key(PRIVATE_KEY)
        web3.eth.default_account = account.address
        while True:
            check_arbitrage_opportunity()
            time.sleep(20)
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"Critical error: {str(e)}")


def get_price(router: Contract, token_in: Address, token_out: Address, amount_in: Wei) -> int:
    """指定したトークンペアの出力額を取得"""
    try:
        path = [token_in, token_out]
        amounts_out = router.functions.getAmountsOut(amount_in, path).call()
        return amounts_out[-1]
    except Exception as e:
        print(f"⚠️ Error getting price: {str(e)}")
        return 0

def build_and_send_tx(tx: TxParams, private_key: str) -> Tuple[Optional[bytes], int]:
    """トランザクションをビルド、署名、送信"""
    try:
        signed_tx = Account.sign_transaction(tx, private_key)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)  # ✅ 修正: rawTransaction → raw_transaction
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        return tx_hash, receipt.gasUsed
    except Exception as e:
        print(f"❌ Transaction failed: {str(e)}")
        return None, 0

def calculate_mon_profit(sell_price_usdc: float, buy_price_usdc: float, 
                        amount_mon: float, gas_price_gwei: int) -> Dict[str, float]:
    """Arbitrage 取引の利益を MON 単位で計算"""
    actual_gas_price = get_optimized_gas_price()
    
    # ガスコストの計算（2回の取引を考慮）
    gas_cost_wei = MAX_GAS * 2 * web3.to_wei(actual_gas_price, 'gwei')
    gas_cost_usdc = (gas_cost_wei / 10**18) * (sell_price_usdc/5)  # USDC に換算
    gas_cost_mon = gas_cost_usdc / (sell_price_usdc/5)  # MON に換算

    # 売却後に買い戻せる MON 量を計算
    mon_buyback = (amount_mon * (sell_price_usdc - gas_cost_usdc)) / buy_price_usdc
    
    # 最終利益
    profit_mon = mon_buyback - amount_mon

    return {
        'gas_cost_usdc': gas_cost_usdc,
        'gas_cost_mon': gas_cost_mon,
        'mon_buyback': mon_buyback,
        'profit_mon': profit_mon,
        'gas_price_used': actual_gas_price
    }

def execute_arbitrage(high_price_router: Contract, low_price_router: Contract, 
                     price_diff_usdc: float) -> bool:
    """利益が出る場合にアービトラージを実行"""
    deadline = int(time.time()) + 60  # 1分のデッドライン
    account = web3.eth.default_account

    swap_amount = get_dynamic_swap_amount(price_diff_usdc)
    
    print("\nExecuting Arbitrage:")
    print(f"1. {swap_amount / (10 ** 18)} MON を売却して USDC を取得...")

    tx_hash1, gas1 = execute_exact_eth_for_tokens(
        high_price_router,
        int(swap_amount * price_diff_usdc * SLIPPAGE),  # 期待 USDC 出力
        account,
        deadline,
        swap_amount
    )

    if tx_hash1:
        print(f"2. 取得した USDC で {swap_amount / (10 ** 18)} MON を買い戻し...")
        tx_hash2, gas2 = execute_tokens_for_exact_eth(
            low_price_router,
            swap_amount,  # 取得する MON
            int(swap_amount * price_diff_usdc / SLIPPAGE),  # 必要 USDC
            account,
            deadline
        )
        if tx_hash2:
            print("\n✅ アービトラージ成功！")
            print(f"🔗 取引ハッシュ 1: {web3.to_hex(tx_hash1)}")
            print(f"🔗 取引ハッシュ 2: {web3.to_hex(tx_hash2)}")
            return True

    print("\n❌ アービトラージ失敗")
    return False

def check_arbitrage_opportunity():
    """価格差をチェックし、利益が出る場合に取引を実行"""
    current_gas_price = get_optimized_gas_price()
    price_uniswap = get_price(uniswap_router, WRAPPED_MONAD, USDC, 5 * (10 ** 18))
    price_crystal = get_price(crystal_router, WRAPPED_MONAD, USDC, 5 * (10 ** 18))

    price_uniswap_usdc = price_uniswap / (10 ** 6)
    price_crystal_usdc = price_crystal / (10 ** 6)
    price_diff_usdc = abs(price_uniswap_usdc - price_crystal_usdc) / 5

    swap_amount = get_dynamic_swap_amount(price_diff_usdc)
    
    if price_uniswap_usdc > price_crystal_usdc:
        profit_info = calculate_mon_profit(price_uniswap_usdc, price_crystal_usdc, swap_amount / (10 ** 18), current_gas_price)
    else:
        profit_info = calculate_mon_profit(price_crystal_usdc, price_uniswap_usdc, swap_amount / (10 ** 18), current_gas_price)

    print(f"\n📊 ガス価格: {current_gas_price:.2f} gwei")
    print(f"📊 スワップ量: {swap_amount / (10 ** 18)} MON")
    print(f"📊 利益: {profit_info['profit_mon']:.6f} MON")

    if profit_info['profit_mon'] >= MIN_PROFIT_MON:
        print("\n🚀 利益が閾値を超えました。取引を実行します！")
        if price_uniswap_usdc > price_crystal_usdc:
            execute_arbitrage(uniswap_router, crystal_router, price_uniswap_usdc)
        else:
            execute_arbitrage(crystal_router, uniswap_router, price_crystal_usdc)
    else:
        print("\n⚠️ 取引は利益を出せないためスキップします")

if __name__ == "__main__":
    try:
        account = Account.from_key(PRIVATE_KEY)
        web3.eth.default_account = account.address

        print(f"Bot started at: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC")
        print(f"Running as: {account.address}")
        print(f"Settings:")
        print(f"- Max Gas Price: {MAX_GAS_PRICE} gwei")
        print(f"- Min Profit: {MIN_PROFIT_MON} MON")
        print(f"- Slippage: {(1-SLIPPAGE)*100}%")

        while True:
            check_arbitrage_opportunity()
            time.sleep(20)
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"Critical error: {str(e)}")
