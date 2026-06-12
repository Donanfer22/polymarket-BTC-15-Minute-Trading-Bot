from dotenv import load_dotenv
import os
load_dotenv()
from py_clob_client_v2.client import ClobClient

pk = os.getenv('POLYMARKET_PK')
pk = '0x' + pk if not pk.startswith('0x') else pk
funder = os.getenv('POLYMARKET_FUNDER')

print(f"Usando PK para endereço: {pk[:8]}...  Funder: {funder}")

client = ClobClient(
    'https://clob.polymarket.com',
    key=pk,
    chain_id=137,
    signature_type=2,
    funder=funder
)

creds = client.create_or_derive_api_key()
print()
print("=== COLE NO COOLIFY ===")
print(f"POLYMARKET_API_KEY={creds.api_key}")
print(f"POLYMARKET_API_KEY_ADDRESS={client.get_address()}")
print(f"POLYMARKET_API_SECRET={creds.api_secret}")
print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
