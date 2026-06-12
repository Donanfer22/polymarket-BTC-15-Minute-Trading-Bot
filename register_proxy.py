from dotenv import load_dotenv
import os
load_dotenv()
from py_clob_client_v2.client import ClobClient

pk = os.getenv('POLYMARKET_PK')
pk = '0x'+pk if not pk.startswith('0x') else pk
funder = os.getenv('POLYMARKET_FUNDER')

print(f"Registrando EOA como proxy para funder: {funder}")
print(f"EOA (signer): {pk[:10]}...")

# Usar sig_type=1 (POLY_PROXY) — o fluxo correto para deposit wallet
c = ClobClient('https://clob.polymarket.com', key=pk, chain_id=137,
               signature_type=1, funder=funder)

# Esta chamada registra o EOA como operador autorizado do funder
creds = c.create_or_derive_api_creds()
print(f"\nCredenciais com sig_type=1:")
print(f"POLYMARKET_API_KEY={creds.api_key}")
print(f"POLYMARKET_API_SECRET={creds.api_secret}")
print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
