import os
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import redis
from dotenv import load_dotenv
import jwt
from datetime import datetime, timedelta

load_dotenv()

app = FastAPI(title="BTC Bot Dashboard API - Production")

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-polymarket-key-change-me-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

# Simples configuração de usuários (pode vir do .env na prática)
# Exemplo: USERS="admin:senha123,amiga:senha321"
users_env = os.getenv("USERS", "admin:admin")
USERS_DB = {}
for u in users_env.split(","):
    if ":" in u:
        k, v = u.split(":", 1)
        USERS_DB[k.strip()] = v.strip()

MOCK_REDIS = {}

def get_redis_client():
    try:
        client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=int(os.getenv('REDIS_DB', 2)),
            decode_responses=True,
            socket_connect_timeout=1
        )
        client.ping()
        return client
    except Exception as e:
        print(f"Redis not available on localhost, using in-memory mock for dashboard testing.")
        return None

redis_client = get_redis_client()

# --- Auth Logic ---
class LoginData(BaseModel):
    username: str
    password: str

def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

@app.get("/")
async def serve_dashboard(request: Request):
    return FileResponse(static_dir / "index.html")

@app.get("/tutorial")
async def serve_tutorial(request: Request):
    if not request.cookies.get("access_token"):
        return RedirectResponse(url="/")
    return FileResponse(static_dir / "tutorial.html")

async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        if token.startswith("Bearer "):
            token = token[7:]
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None or username not in USERS_DB:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.post("/api/login")
async def login(response: Response, data: LoginData):
    if data.username in USERS_DB and USERS_DB[data.username] == data.password:
        access_token = create_access_token(
            data={"sub": data.username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        # Set HttpOnly cookie for security
        response.set_cookie(
            key="access_token",
            value=f"Bearer {access_token}",
            httponly=True,
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            samesite="lax"
        )
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Incorrect username or password")

@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"status": "success"}

@app.get("/api/auth-check")
async def check_auth(username: str = Depends(get_current_user)):
    return {"status": "authenticated", "user": username}

# --- Settings & Credentials Logic ---
class SettingsData(BaseModel):
    api_key: str
    api_secret: str
    api_passphrase: str
    private_key: str
    trade_size: float = 1.0
    strategy_profile: str = "snowball"

@app.get("/api/settings")
async def get_settings(username: str = Depends(get_current_user)):
    settings_json = None
    if redis_client:
        settings_json = redis_client.get('btc_trading:credentials')
    else:
        settings_json = MOCK_REDIS.get('btc_trading:credentials')
        
    if settings_json:
        data = json.loads(settings_json)
        return {
            "api_key": data.get("api_key", ""),
            "api_secret": "***" if data.get("api_secret") else "",
            "api_passphrase": "***" if data.get("api_passphrase") else "",
            "private_key": "***" if data.get("private_key") else "",
            "trade_size": data.get("trade_size", 1.0),
            "strategy_profile": data.get("strategy_profile", "snowball")
        }
    return {}

@app.post("/api/settings")
async def save_settings(data: SettingsData, username: str = Depends(get_current_user)):
    new_data = data.model_dump()

    # Carrega o que já está salvo para poder preservar segredos mascarados.
    if redis_client:
        existing_json = redis_client.get('btc_trading:credentials')
    else:
        existing_json = MOCK_REDIS.get('btc_trading:credentials')
    existing = json.loads(existing_json) if existing_json else {}

    # PROTEÇÃO: o GET devolve os segredos mascarados ("***"). Se o usuário salvar
    # o painel sem redigitar, esses "***" voltam aqui e sobrescreveriam as chaves
    # reais no Redis (quebrando a autenticação do bot). Então, quando o campo vier
    # mascarado ou vazio, mantemos o valor que já estava salvo.
    secret_fields = ("api_secret", "api_passphrase", "private_key")
    for field in secret_fields:
        value = (new_data.get(field) or "").strip()
        if value in ("", "***"):
            new_data[field] = existing.get(field, "")

    # PROTEÇÃO: a Polymarket rejeita silenciosamente ordens abaixo de $5.00.
    try:
        if float(new_data.get("trade_size", 0)) < 5.0:
            new_data["trade_size"] = 5.0
    except (TypeError, ValueError):
        new_data["trade_size"] = 5.0

    if redis_client:
        redis_client.set('btc_trading:credentials', json.dumps(new_data))
    else:
        MOCK_REDIS['btc_trading:credentials'] = json.dumps(new_data)
    return {"status": "success"}

# --- Trading Mode Logic ---
class ModeUpdate(BaseModel):
    mode: str  # 'SIMULATION', 'LIVE', or 'STOPPED'

def get_current_mode():
    try:
        mode = None
        if redis_client:
            mode = redis_client.get('btc_trading:simulation_mode')
        else:
            mode_file = Path(__file__).parent.parent / "mode.txt"
            if mode_file.exists():
                with open(mode_file, "r", encoding="utf-8") as f:
                    mode = f.read().strip()
            if not mode:
                mode = MOCK_REDIS.get('btc_trading:simulation_mode')
            
        # Map values: '1' -> SIM, '0' -> LIVE, '-1' -> STOPPED
        if mode == '1': return "SIMULATION"
        if mode == '0': return "LIVE"
        if mode == '-1': return "STOPPED"
        return "UNKNOWN" # Default if not set
    except:
        return "ERROR"

@app.post("/api/mode")
async def set_mode(update: ModeUpdate, username: str = Depends(get_current_user)):
    mode_map = {
        'SIMULATION': '1',
        'SIM': '1',
        'LIVE': '0',
        'STOPPED': '-1'
    }
    
    val = mode_map.get(update.mode.upper())
    if not val:
        raise HTTPException(status_code=400, detail="Invalid mode")
        
    try:
        if redis_client:
            redis_client.set('btc_trading:simulation_mode', val)
        else:
            MOCK_REDIS['btc_trading:simulation_mode'] = val
            mode_file = Path(__file__).parent.parent / "mode.txt"
            with open(mode_file, "w", encoding="utf-8") as f:
                f.write(val)
        return {"status": "success", "mode": update.mode.upper()}
    except Exception as e:
        return {"error": str(e)}

# --- Data & Metrics Logic ---
def load_paper_trades():
    try:
        file_path = Path(__file__).parent.parent / 'data' / 'paper_trades.json'
        with open(file_path, 'r') as f:
            return json.load(f)
    except:
        return []

def calculate_metrics(trades):
    total_trades = len(trades)
    winning = sum(1 for t in trades if t.get('outcome') == 'WIN')
    losing = sum(1 for t in trades if t.get('outcome') == 'LOSS')
    win_rate = (winning / (winning + losing) * 100) if (winning + losing) > 0 else 0
    
    pnl = 0.0
    for t in trades:
        if t.get('outcome') == 'WIN':
            pnl += t.get('size_usd', 0) * 0.20
        elif t.get('outcome') == 'LOSS':
            pnl -= t.get('size_usd', 0) * 0.30
            
    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "pnl": round(pnl, 2),
        "winning": winning,
        "losing": losing
    }

@app.get("/api/status")
async def get_status(username: str = Depends(get_current_user)):
    mode = get_current_mode()
    trades = load_paper_trades()
    metrics = calculate_metrics(trades)
    return {
        "mode": mode,
        "metrics": metrics,
        "recent_trades": list(reversed(trades))[:50]
    }

# --- WebSockets Logic ---

# Status WebSocket
@app.websocket("/api/ws/status")
async def ws_status(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            mode = get_current_mode()
            trades = load_paper_trades()
            metrics = calculate_metrics(trades)
            await websocket.send_json({
                "mode": mode,
                "metrics": metrics,
                "recent_trades": list(reversed(trades))[:50]
            })
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass

# Live Logs WebSocket (Terminal de Wall Street)
@app.websocket("/api/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    if not redis_client:
        log_file = Path(__file__).parent.parent / "live_logs.txt"
        if not log_file.exists():
            log_file.touch()
        
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                f.seek(0, 2)  # Pula para o final do arquivo
                while True:
                    line = f.readline()
                    if line:
                        await websocket.send_text(line.strip())
                    else:
                        await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            pass
        return
        
    pubsub = redis_client.pubsub()
    pubsub.subscribe('btc_trading:live_logs')
    
    try:
        while True:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message['type'] == 'message':
                log_data = message['data']
                await websocket.send_text(log_data)
            else:
                await asyncio.sleep(0.1) # Prevents CPU blocking
    except WebSocketDisconnect:
        pubsub.unsubscribe('btc_trading:live_logs')

# Serve Static
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
