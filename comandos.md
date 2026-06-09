# 🚀 Comandos Essenciais do Robô Polymarket

Guarde este arquivo no seu VS Code para nunca mais esquecer os comandos de inicialização!

Certifique-se sempre de estar na pasta raiz do projeto antes de rodar os comandos:
`cd c:\Users\cerqu\Documents\Projetos_IDE\trader_bitcoin_polymarket\polymarket-BTC-15-Minute-Trading-Bot`

---

## 1. Como abrir o Painel Web (Dashboard)
Para iniciar a API do servidor do painel, abra um terminal e cole o comando:

```bash
python -m uvicorn dashboard.api:app --reload
```
**Onde acessar:**
Após rodar o comando, abra o seu navegador de internet e acesse:
👉 **[http://localhost:8000](http://localhost:8000)**

---

## 2. Como iniciar o Robô (Bot de Trading) - Modo Anti-Queda
O robô é programado para "limpar a memória" e reiniciar sozinho periodicamente. Para que ele suba de volta automaticamente no seu terminal do Windows, abra um **SEGUNDO** terminal no VS Code e cole este comando longo (tudo de uma vez):

```powershell
while ($true) { python bot.py --test-mode; echo "Reiniciando robô..."; Start-Sleep -Seconds 3 }
```
*(Para parar ele de vez, basta apertar `Ctrl + C` repetidamente até voltar a linha de comando normal).*

---

## 3. Como iniciar os serviços locais do Docker (Redis + Grafana)
Se o seu computador reiniciou e o Redis estiver desligado (dando erro de conexão), rode este comando antes de tudo para subir o banco de dados de memória em segundo plano:

```bash
docker-compose up -d
```

---

## 💡 Dica de Ouro
Se os comandos não quiserem iniciar e der "comando não encontrado" ou "módulo não encontrado", lembre-se de sempre ativar o ambiente virtual do Python (`venv`) no terminal antes de rodar qualquer coisa:
```bash
venv\Scripts\activate
```
