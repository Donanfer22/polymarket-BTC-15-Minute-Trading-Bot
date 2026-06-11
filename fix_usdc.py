import os
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account
import time

load_dotenv()

pk = os.getenv('POLYMARKET_PK')
pk = '0x'+pk if not pk.startswith('0x') else pk
w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))
account = Account.from_key(pk)

USDC = '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359'
CTF_EXCHANGE = '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E'
NEG_RISK = '0xC5d563A36AE78145C45a50134d48A1215220f80a'
ABI = [{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
       {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]

usdc = w3.eth.contract(address=USDC, abi=ABI)
bal = usdc.functions.balanceOf(account.address).call()
print(f"USDC Nativo: ${bal/1e6:.4f}")

if bal > 0:
    print("Aprovando USDC Nativo para as exchanges...")
    MAX = 2**256 - 1
    nonce = w3.eth.get_transaction_count(account.address, 'pending')
    exchanges = [
        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        "0xC5d563A36AE78145C45a50134d48A1215220f80a",
        "0xE111180000d2663C0091e4f400237545B87B996B",
        "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
        "0xe2222d279d744050d28e00520010520000310F59"
    ]
    for addr in exchanges:
        try:
            tx = usdc.functions.approve(addr, MAX).build_transaction({
                'from': account.address, 'nonce': nonce,
                'gasPrice': w3.eth.gas_price, 'gas': 100000, 'chainId': 137
            })
            signed = w3.eth.account.sign_transaction(tx, pk)
            h = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"Approve {addr[:6]}...: {h.hex()}")
            nonce += 1
            time.sleep(1)
        except Exception as e:
            print(f"Erro no approve {addr}: {e}")
