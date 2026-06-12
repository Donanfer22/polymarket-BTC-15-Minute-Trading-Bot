import os
from dotenv import load_dotenv, set_key
from py_clob_client_v2.client import ClobClient

load_dotenv()
pk = os.getenv('POLYMARKET_PK')
pk = '0x'+pk if not pk.startswith('0x') else pk

print("Gerando novas chaves de API para a sua carteira EOA (MetaMask)...")
c = ClobClient('https://clob.polymarket.com', key=pk, chain_id=137, signature_type=0)
creds = c.create_or_derive_api_creds()

env_path = os.path.join(os.path.dirname(__file__), '.env')
set_key(env_path, 'POLYMARKET_API_KEY', creds.api_key)
set_key(env_path, 'POLYMARKET_API_SECRET', creds.api_secret)
set_key(env_path, 'POLYMARKET_PASSPHRASE', creds.api_passphrase)
# We don't need the Funder anymore, but we can leave it or clear it.
print("Chaves geradas e salvas no .env com sucesso!")
