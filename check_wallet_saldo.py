"""
Diagnóstico de carteira/saldo da Polymarket.

Compara o endereço da EOA (derivado do POLYMARKET_PK) com o POLYMARKET_FUNDER,
e mostra o wallet_type / saldo que o SDK enxerga ao fazer
AsyncSecureClient.create(private_key=pk, wallet=funder) — exatamente como o bot.

Objetivo: descobrir por que o pedido é rejeitado com
"maker address not allowed, please use the deposit wallet flow".
Causa provável: FUNDER aponta para a EOA (ou carteira errada) em vez da
carteira de depósito (deposit wallet / POLY_1271).

Rodar DENTRO do container do bot no VPS. Veja o comando no NOTES.md.
"""
import os
import asyncio


pk = os.getenv("POLYMARKET_PK", "")
pk = ("0x" + pk) if pk and not pk.startswith("0x") else pk
funder = os.getenv("POLYMARKET_FUNDER", "") or None


def mask(a):
    a = str(a) if a else ""
    return (a[:6] + "..." + a[-4:]) if len(a) >= 10 else (a or "(vazio)")


# Endereço da EOA derivado da private key
try:
    from eth_account import Account
    eoa = Account.from_key(pk).address
except Exception as e:
    eoa = f"<erro ao derivar EOA: {e}>"


def dump(c):
    for attr in ("wallet_type", "funder", "wallet", "proxy", "address",
                 "signer_address", "maker", "balance", "allowance"):
        if hasattr(c, attr):
            try:
                print(f"   {attr}: {getattr(c, attr)}")
            except Exception as e:
                print(f"   {attr}: <erro: {e}>")
    for meth in ("is_gasless_ready",):
        if hasattr(c, meth):
            try:
                print(f"   {meth}(): {getattr(c, meth)()}")
            except Exception as e:
                print(f"   {meth}(): <erro: {e}>")


async def main():
    from polymarket import AsyncSecureClient

    print("EOA (derivado do PK):", mask(eoa))
    print("FUNDER (.env/env):  ", mask(funder))
    same = isinstance(eoa, str) and funder and eoa.lower() == funder.lower()
    print("EOA == FUNDER? ->", bool(same),
          "  <<< se True, ESSE é o bug (FUNDER deveria ser a deposit wallet, não a EOA)")

    print("\n=== create(wallet=funder) [exatamente como o bot faz] ===")
    try:
        async with await AsyncSecureClient.create(private_key=pk, wallet=funder) as c:
            res = await c.get_balance_allowance(asset_type="COLLATERAL")
            print("   get_balance_allowance ->", res)
            dump(c)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("   ERRO:", e)

    print("\n>>> wallet_type deveria indicar DEPOSIT WALLET (POLY_1271).")
    print(">>> Se for EOA, ou se EOA==FUNDER, achamos o problema do 'maker not allowed'.")


if __name__ == "__main__":
    asyncio.run(main())
