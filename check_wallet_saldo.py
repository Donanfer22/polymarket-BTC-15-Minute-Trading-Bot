"""
Diagnóstico de carteira/saldo da Polymarket.

Compara DUAS formas de inicializar o AsyncSecureClient:
  1. AUTO-DERIVADO  -> create(private_key=pk)            (exatamente como o bot.py faz hoje)
  2. EXPLICITO      -> create(private_key=pk, wallet=FUNDER)

Objetivo: descobrir se a carteira que o bot deriva sozinho é a MESMA onde
está o seu USDC (o POLYMARKET_FUNDER do .env). Se forem diferentes, o bot lê
saldo $0 e nunca entra, mesmo com dinheiro na conta.

Rodar DENTRO do container do bot no VPS (lá o SDK 'polymarket' está instalado
e as variáveis do .env já estão no ambiente). Veja o comando no NOTES.md.
"""
import os
import asyncio


pk = os.getenv("POLYMARKET_PK", "")
pk = ("0x" + pk) if pk and not pk.startswith("0x") else pk
funder = os.getenv("POLYMARKET_FUNDER", "")


def mask(a):
    a = str(a) if a else ""
    return (a[:6] + "..." + a[-4:]) if len(a) >= 10 else (a or "(vazio)")


def dump(client):
    """Imprime os atributos relevantes do client (nomes variam por versão do SDK)."""
    for attr in ("funder", "wallet", "proxy", "address", "signer_address",
                 "wallet_type", "balance", "allowance"):
        if hasattr(client, attr):
            try:
                print(f"   {attr}: {getattr(client, attr)}")
            except Exception as e:
                print(f"   {attr}: <erro: {e}>")


async def check(label, **kwargs):
    from polymarket import AsyncSecureClient
    print(f"\n=== {label} ===")
    try:
        async with await AsyncSecureClient.create(private_key=pk, **kwargs) as client:
            client = await client.setup_gasless_wallet()
            res = await client.get_balance_allowance(asset_type="COLLATERAL")
            print("   get_balance_allowance ->", res)
            dump(client)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("   ERRO:", e)


async def main():
    print("PK presente:", "sim" if pk else "NAO (POLYMARKET_PK vazio!)")
    print("FUNDER (.env):", mask(funder))

    # 1. Como o bot faz hoje (deriva a carteira sozinho)
    await check("AUTO-DERIVADO (como o bot.py faz: create SEM wallet)")

    # 2. Passando o funder explicitamente
    if funder:
        await check("EXPLICITO (create COM wallet=FUNDER)", wallet=funder)
    else:
        print("\n(POLYMARKET_FUNDER vazio no .env — pulei o teste explícito.)")

    print("\n>>> Compare: a carteira do AUTO-DERIVADO é a mesma do FUNDER?")
    print(">>> E qual das duas mostra o saldo de USDC que você depositou?")


if __name__ == "__main__":
    asyncio.run(main())
