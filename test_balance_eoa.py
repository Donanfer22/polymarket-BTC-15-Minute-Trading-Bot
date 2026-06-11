import os
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

load_dotenv()
pk = os.getenv('POLYMARKET_PK')
pk = '0x'+pk if not pk.startswith('0x') else pk

w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))
account = Account.from_key(pk)
USDC_NATIVE = '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359'
ABI = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
       {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]

usdc = w3.eth.contract(address=USDC_NATIVE, abi=ABI)

print("=== BALANCE EOA (sig_type=0) ===")
try:
    bal = usdc.functions.balanceOf(account.address).call()
    
    exchanges = [
        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        "0xC5d563A36AE78145C45a50134d48A1215220f80a",
        "0xE111180000d2663C0091e4f400237545B87B996B",
        "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
        "0xe2222d279d744050d28e00520010520000310F59"
    ]
    
    allowances = {}
    for ex in exchanges:
        allw = usdc.functions.allowance(account.address, ex).call()
        allowances[ex] = 'MAX' if allw > 1000000e6 else str(allw)
        
    result = {'balance': str(bal), 'allowances': allowances}
    print(result)
except Exception as e:
    print(f"Erro: {e}")
