import os
from dotenv import load_dotenv
load_dotenv()
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, OrderArgs, OrderType

pk = os.getenv('POLYMARKET_PK')
pk = '0x'+pk if not pk.startswith('0x') else pk

# EOA CLIENT
c = ClobClient('https://clob.polymarket.com', key=pk, chain_id=137, signature_type=0)
c.set_api_creds(ApiCreds(
    api_key=os.getenv('POLYMARKET_API_KEY'),
    api_secret=os.getenv('POLYMARKET_API_SECRET'),
    api_passphrase=os.getenv('POLYMARKET_PASSPHRASE')
))

print("Testando criar uma ordem LIMIT com EOA (signature_type=0)...")
try:
    # Get a valid token to test with (let's find an active market from gamma)
    import requests
    resp = requests.get("https://gamma-api.polymarket.com/events?slug=will-bitcoin-hit-100k-in-2024")
    market = resp.json()[0]['markets'][0]
    token_id = market['clobTokenIds'][0] # YES token

    order_args = OrderArgs(
        price=0.10,
        size=5.0,
        side="BUY",
        token_id=token_id
    )
    signed_order = c.create_order(order_args)
    resp = c.post_order(signed_order, OrderType.FOK)
    print("RESPOSTA DA ORDEM:")
    print(resp)
except Exception as e:
    print("Erro ao colocar ordem:", e)
