from dotenv import load_dotenv
import os
load_dotenv()
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

pk = os.getenv('POLYMARKET_PK')
pk = '0x'+pk if not pk.startswith('0x') else pk
c = ClobClient('https://clob.polymarket.com', key=pk, chain_id=137, signature_type=2, funder=os.getenv('POLYMARKET_FUNDER'))
c.set_api_creds(c.create_or_derive_api_key())

print('=== Teste autenticado ===')
params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
print(c.get_balance_allowance(params))
