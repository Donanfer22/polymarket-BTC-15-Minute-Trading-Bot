import os
from dotenv import load_dotenv
load_dotenv()
from web3 import Web3
from eth_account import Account

pk = os.getenv('POLYMARKET_PK')
pk = '0x'+pk if not pk.startswith('0x') else pk
w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))
account = Account.from_key(pk)
print(f"EOA: {account.address}")

USDC = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
CTF_EXCHANGE = '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E'
NEG_RISK = '0xC5d563A36AE78145C45a50134d48A1215220f80a'
ABI = [{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
       {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]

usdc = w3.eth.contract(address=USDC, abi=ABI)
bal = usdc.functions.balanceOf(account.address).call()
print(f"USDC na carteira: ${bal/1e6:.2f}")

MAX = 2**256 - 1
for name, addr in [("CTFExchange", CTF_EXCHANGE), ("NegRiskExchange", NEG_RISK)]:
    nonce = w3.eth.get_transaction_count(account.address, 'pending')
    tx = usdc.functions.approve(addr, MAX).build_transaction({
        'from': account.address, 'nonce': nonce,
        'gasPrice': w3.eth.gas_price, 'gas': 100000, 'chainId': 137
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Approve {name}: {h.hex()}")
