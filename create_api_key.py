import os
from py_clob_client.client import ClobClient

def main():
    print("=========================================================")
    print("GERADOR DE CHAVES DA POLYMARKET (L2 TRADING KEY)")
    print("=========================================================")
    print("Sua carteira principal da MetaMask é: 0xd26aA96685300932D9f40ff3Af8a7090D6037aC2")
    print("Para gerar as chaves de API, você precisa colar a Chave Privada DESSA carteira MetaMask abaixo.")
    print("COMO PEGAR A CHAVE NA METAMASK:")
    print("1. Abra a extensão MetaMask")
    print("2. Clique nos 3 pontinhos (Opções da Conta)")
    print("3. Detalhes da Conta")
    print("4. Mostrar Chave Privada")
    print("=========================================================\n")
    
    private_key = input("Cole a sua Chave Privada da MetaMask aqui: ").strip()
    
    if not private_key:
        print("\nErro: Chave privada não pode ser vazia.")
        return
        
    try:
        host = "https://clob.polymarket.com"
        chain_id = 137 # Polygon
        
        # Initialize client with Level 1 (requires only host, chain_id, and private key)
        client = ClobClient(host, key=private_key, chain_id=chain_id)
        
        print("\nGerando chaves da API na Polymarket...")
        creds = client.create_or_derive_api_creds()
        
        print("\n" + "="*60)
        print("✅ SUCESSO! SUAS NOVAS CHAVES ESTÃO ABAIXO:")
        print("="*60)
        print(f"POLYMARKET_PK={private_key}")
        print(f"POLYMARKET_API_KEY={creds.api_key}")
        print(f"POLYMARKET_API_SECRET={creds.api_secret}")
        print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
        print("="*60)
        print("\n-> Vá no seu Dashboard, clique em Settings & Credentials, apague as velhas e COLE ESSAS QUATRO (4) CHAVES NOVAS!")
        
    except Exception as e:
        print(f"\n❌ Erro ao gerar chaves: {str(e)}")

if __name__ == "__main__":
    main()
