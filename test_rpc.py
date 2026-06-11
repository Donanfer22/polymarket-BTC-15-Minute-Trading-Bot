from dotenv import load_dotenv
import os, requests

load_dotenv()

funder = os.getenv('POLYMARKET_FUNDER')
eoa = os.getenv('POLYMARKET_API_KEY_ADDRESS')

tokens = {
    'USDC.e': '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174',
    'USDC nativo': '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
    'USDT': '0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
}

def saldo(token, carteira):
    data = '0x70a08231000000000000000000000000' + carteira[2:].lower()
    payload = {'jsonrpc':'2.0','method':'eth_call','params':[{'to':token,'data':data},'latest'],'id':1}
    r = requests.post('https://polygon-rpc.com', json=payload, timeout=10).json()
    return int(r['result'],16)/1e6 if r.get('result') and r['result']!='0x' else 0

print('=== DEPOSIT WALLET (funder):', funder, '===')
for nome, addr in tokens.items():
    print(f'  {nome}: {saldo(addr, funder)}')
print('=== EOA (MetaMask):', eoa, '===')
for nome, addr in tokens.items():
    print(f'  {nome}: {saldo(addr, eoa)}')
