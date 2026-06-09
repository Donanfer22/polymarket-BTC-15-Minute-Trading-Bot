<div align="center">
  <h1>📈 Polymarket BTC 15-Minute Trading Bot</h1>
  <p><b>High-Performance SaaS Trading Architecture for Polymarket Prediction Markets</b></p>
</div>

---

Um ecossistema completo de trading assíncrono construído sobre o motor **Nautilus Trader**, desenhado especificamente para operar os mercados de previsão "Bitcoin Price in 15 Minutes" na Polymarket.

Esta versão inclui uma **Arquitetura SaaS Profissional**, contando com um Painel Web interativo, gerenciamento de estado via Redis, integração com WebSockets para logs ao vivo e pronto para deploy na nuvem.

## 🚀 Principais Funcionalidades

- **Late-Window Trading Strategy**: O bot executa um modelo matemático que aguarda os 13 primeiros minutos do mercado de 15 minutos em silêncio. Apenas no momento crítico (minutos 13-14), onde a tendência já está praticamente formada, ele calcula a volatilidade e executa a ordem com até 80% de precisão.
- **Painel de Controle Web (SaaS)**: Uma interface premium com design *Glassmorphism* que permite controlar o robô de qualquer lugar do mundo pelo navegador.
- **Comunicação em Tempo Real**: Logs de execução, ordens e eventos do motor do bot são transmitidos do terminal nativo direto para o navegador com 0 delay usando WebSockets (FastAPI).
- **Gerenciamento de Estado Distribuído**: Utiliza **Redis** para comunicação entre o Painel Web e o processo do Bot. Pause, inicie e mude os modos do robô em tempo real sem precisar reiniciar o sistema.
- **Modos de Operação Dinâmicos**: Transição suave entre `STOPPED` (Emergência), `SIMULATION` (Paper Trading) e `LIVE` (Dinheiro Real).
- **Pronto para Deploy (Docker)**: Configurado com `docker-compose.yml` para ser hospedado instantaneamente em qualquer VPS, AWS, ou Easypanel.

## 🏗️ Arquitetura do Sistema

1. **Engine de Trading**: `Nautilus Trader` (Motor de alta frequência em Rust/Cython).
2. **Backend**: `FastAPI` (Python) fornecendo rotas seguras e WebSockets.
3. **Frontend**: Interface nativa em HTML/JS com Vanilla CSS.
4. **Mensageria & Banco**: `Redis` atuando como Message Broker (Pub/Sub) e gerenciador de estado (Cache).
5. **Autenticação**: Segurança JWT baseada em cookies HTTP-Only para proteger o acesso remoto ao painel.

## 💻 Instalação Local (Desenvolvimento)

**1. Clone o repositório**
```bash
git clone https://github.com/SeuUsuario/polymarket-btc-15m-bot.git
cd polymarket-btc-15m-bot
```

**2. Ambiente Virtual**
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Instale as dependências
pip install -r requirements.txt
```

**3. Instale o Redis (obrigatório para comunicação)**
- Windows: Utilize WSL ou Memurai.
- Mac/Linux: `sudo apt install redis-server` / `brew install redis`

**4. Variáveis de Ambiente**
Crie um arquivo `.env` na raiz do projeto (use o `.env.example` como base):
```env
POLYMARKET_API_KEY=sua_chave
POLYMARKET_SECRET=seu_secret
POLYMARKET_PASSPHRASE=sua_senha
```

## 🕹️ Como Iniciar a Operação

Este projeto roda com processos separados (Arquitetura de Microsserviços).

**Passo 1: Iniciar o Dashboard (Painel Web)**
Abra o primeiro terminal e rode o servidor web:
```bash
python -m uvicorn dashboard.api:app --reload
```
Acesse `http://localhost:8000` (Login padrão: `admin` / `admin123`).

**Passo 2: Iniciar o Bot de Execução**
Abra um segundo terminal na mesma pasta e inicie o motor principal:
```bash
python bot.py --test-mode
```

Controle tudo pelo painel no seu navegador!

## 🐳 Deploy na Nuvem (Coolify)

O projeto já contém um arquivo `docker-compose.yml` totalmente arquitetado para produção e **Zero-Downtime Deployments** através do [Coolify](https://coolify.io).

1. Crie um novo projeto "Docker Compose" no seu painel do Coolify.
2. Conecte seu repositório do Github.
3. Defina os **Domínios** na aba "Configuration" (O Coolify fará o roteamento automático para a porta interna `8000` que deixamos exposta através da instrução `expose` no docker-compose).
4. Preencha a aba **Environment Variables** (Copie os dados do seu arquivo `.env.example`).
5. **Atenção à Segurança e Persistência**: A arquitetura provisiona 3 serviços rodando em containers isolados:
   - `redis` (Banco de dados de controle interno - Inacessível por fora para evitar ataques).
   - `bot` (O motor de execução de alta frequência).
   - `dashboard` (A API do seu painel e os WebSockets seguros).
   *(Nota: O histórico de trades simulados é salvo em uma pasta `/data` que utiliza Docker Volumes (bot_data) para que ambos os containers consigam ler o histórico perfeitamente e os dados sobrevivam a reinicializações).*
6. Clique em **Deploy** e o seu sistema de alta frequência institucional estará operando no mundo inteiro em 2 minutos.

## ⚠️ Aviso de Risco
Este software foi desenvolvido com fins educacionais e de pesquisa algorítmica. O mercado de criptomoedas e mercados de previsão (Prediction Markets) carregam altíssimo risco financeiro. Os desenvolvedores não se responsabilizam por quaisquer perdas financeiras resultantes do uso deste bot. Sempre teste exaustivamente no modo `SIMULATION` antes de operar com fundos reais.
