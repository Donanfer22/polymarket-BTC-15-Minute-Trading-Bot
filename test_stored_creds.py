from dotenv import load_dotenv
import os
load_dotenv()
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

pk = os.getenv('POLYMARKET_PK')
pk = '0x' + pk if not pk.startswith('0x') else pk

print("Testando com credenciais ESTATICAS (sem re-derivar)...")
c = ClobClient('https://clob.polymarket.com', key=pk, chain_id=137,
               signature_type=2, funder=os.getenv('POLYMARKET_FUNDER'))
c.set_api_creds(ApiCreds(
    api_key=os.getenv('POLYMARKET_API_KEY'),
    api_secret=os.getenv('POLYMARKET_API_SECRET'),
    api_passphrase=os.getenv('POLYMARKET_PASSPHRASE')
))

try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
    result = c.get_balance_allowance(params)
    print("SUCCESS:", result)
except Exception as e:
    print("ERROR:", e)
