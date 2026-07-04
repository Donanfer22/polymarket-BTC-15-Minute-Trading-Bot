import os
import asyncio
from polymarket import AsyncSecureClient

async def test():
    pk = os.getenv('POLYMARKET_PK')
    if not pk:
        print("Erro: POLYMARKET_PK não está definido no ambiente.")
        return
    pk = '0x' + pk if not pk.startswith('0x') else pk
    async with await AsyncSecureClient.create(private_key=pk) as client:
        client = await client.setup_gasless_wallet()
        await client.get_balance_allowance(asset_type='COLLATERAL')
        print('Funder/EOA:', client.funder)
        print('Derived Wallet:', getattr(client, 'wallet', None) or getattr(client, 'proxy', None))
        print('Balance:', getattr(client, 'balance', None))
        print('Allowance:', getattr(client, 'allowance', None))

asyncio.run(test())
