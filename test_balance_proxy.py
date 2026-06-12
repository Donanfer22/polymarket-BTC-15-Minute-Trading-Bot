from dotenv import load_dotenv
import os
load_dotenv()
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

pk = os.getenv('POLYMARKET_PK')
pk = '0x'+pk if not pk.startswith('0x') else pk
funder = os.getenv('POLYMARKET_FUNDER')

# Usando signature_type=1 (PROXY) que é o que o bot realmente usa!
c = ClobClient('https://clob.polymarket.com', key=pk, chain_id=137, signature_type=1, funder=funder)
c.set_api_creds(ApiCreds(
    api_key=os.getenv('POLYMARKET_API_KEY'),
    api_secret=os.getenv('POLYMARKET_API_SECRET'),
    api_passphrase=os.getenv('POLYMARKET_PASSPHRASE')
))

print(f"=== BALANCE PROXY WALLET ({funder}) ===")
try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    print(c.get_balance_allowance(params))
except Exception as e:
    print(f"Erro: {e}")
