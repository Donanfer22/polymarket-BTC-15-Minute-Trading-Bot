# 1. Mudar para o diretório correto do bot
Set-Location -Path "C:\Users\cerqu\Documents\Projetos_IDE\trader_bitcoin_polymarket\polymarket-BTC-15-Minute-Trading-Bot"

# Permite a execução do script de ativação do venv temporariamente nesta sessão
Set-ExecutionPolicy Bypass -Scope Process -Force

# 2. Ativar o ambiente virtual
.\venv\Scripts\Activate.ps1

# 3. Rodar o bot
python bot.py

# 4. Manter o terminal aberto após finalizar (pause)
Write-Host "`nO bot finalizou sua execução. Pressione qualquer tecla para fechar..."
$Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") | Out-Null
