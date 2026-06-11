from dotenv import load_dotenv
import os
import requests

load_dotenv()

# Endereco do funder (deposit wallet)
funder = os.getenv('POLYMARKET_FUNDER')

# Consulta o saldo on-chain direto na Polygon via API publica
# USDC.e antigo
usdce = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
# USDC nativo novo
usdc_native = '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359'

for nome, contrato in [('USDC.e (antigo)', usdce), ('USDC nativo (novo)', usdc_native)]:
    url = f'https://api.polygonscan.com/api?module=account&action=tokenbalance&contractaddress={contrato}&address={funder}&tag=latest'
    try:
        r = requests.get(url, timeout=10).json()
        saldo = int(r.get('result', 0)) / 1e6
        print(f'{nome}: {saldo} USDC  (contrato {contrato})')
    except Exception as e:
        print(f'{nome}: erro - {e}')
