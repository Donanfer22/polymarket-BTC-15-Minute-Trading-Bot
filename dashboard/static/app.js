// Constants
const API_URL = '/api';
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_STATUS_URL = `${wsProtocol}//${window.location.host}/api/ws/status`;
const WS_LOGS_URL = `${wsProtocol}//${window.location.host}/api/ws/logs`;

// State
let pnlChart = null;
let statusWs = null;
let logsWs = null;

document.addEventListener('DOMContentLoaded', () => {
    checkAuth();
    
    // Forms
    document.getElementById('login-form').addEventListener('submit', handleLogin);
    document.getElementById('settings-form').addEventListener('submit', handleSaveSettings);
});

// --- Auth Logic ---
async function checkAuth() {
    try {
        const res = await fetch(`${API_URL}/auth-check`);
        if (res.ok) {
            initDashboard();
        } else {
            showLogin();
        }
    } catch (e) {
        showLogin();
    }
}

async function handleLogin(e) {
    e.preventDefault();
    const user = document.getElementById('login-user').value;
    const pass = document.getElementById('login-pass').value;
    const errorEl = document.getElementById('login-error');
    
    try {
        const res = await fetch(`${API_URL}/login`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: user, password: pass})
        });
        
        if (res.ok) {
            errorEl.innerText = '';
            initDashboard();
        } else {
            errorEl.innerText = 'Invalid username or password';
        }
    } catch (err) {
        errorEl.innerText = 'Connection error';
    }
}

async function logout() {
    await fetch(`${API_URL}/logout`, { method: 'POST' });
    
    // Disconnect WS
    if(statusWs) statusWs.close();
    if(logsWs) logsWs.close();
    
    document.getElementById('dashboard-container').classList.add('hidden');
    showLogin();
}

function showLogin() {
    document.getElementById('login-overlay').classList.add('active');
    document.getElementById('login-pass').value = '';
}

// --- Init ---
function initDashboard() {
    document.getElementById('login-overlay').classList.remove('active');
    document.getElementById('dashboard-container').classList.remove('hidden');
    
    if(!pnlChart) initChart();
    
    loadSettings();
    connectStatusWS();
    connectLogsWS();
}

// --- Settings Logic ---
function openSettings() {
    document.getElementById('settings-modal').classList.add('active');
}

function closeSettings() {
    document.getElementById('settings-modal').classList.remove('active');
}

async function loadSettings() {
    try {
        const res = await fetch(`${API_URL}/settings`);
        if (res.ok) {
            const data = await res.json();
            document.getElementById('set-api-key').value = data.api_key || '';
            document.getElementById('set-api-secret').value = data.api_secret || '';
            document.getElementById('set-api-passphrase').value = data.api_passphrase || '';
            document.getElementById('set-private-key').value = data.private_key || '';
            document.getElementById('set-trade-size').value = data.trade_size || '1.00';
            if (data.strategy_profile) {
                document.getElementById('set-strategy-profile').value = data.strategy_profile;
            }
        }
    } catch(e) { console.error("Error loading settings"); }
}

async function handleSaveSettings(e) {
    e.preventDefault();
    const data = {
        api_key: document.getElementById('set-api-key').value,
        api_secret: document.getElementById('set-api-secret').value,
        api_passphrase: document.getElementById('set-api-passphrase').value,
        private_key: document.getElementById('set-private-key').value,
        trade_size: parseFloat(document.getElementById('set-trade-size').value || "1.00"),
        strategy_profile: document.getElementById('set-strategy-profile').value || 'snowball'
    };
    
    try {
        const res = await fetch(`${API_URL}/settings`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        if (res.ok) {
            closeSettings();
        } else {
            alert('Failed to save settings');
        }
    } catch(err) {
        alert('Connection error');
    }
}

// --- Trading Controls ---
async function setMode(mode) {
    try {
        const res = await fetch(`${API_URL}/mode`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ mode })
        });
        
        const data = await res.json();
        if(data.status !== 'success') {
            const errorMsg = data.error || data.detail || 'Unknown error';
            if (errorMsg === 'Not authenticated' || res.status === 401) {
                alert('Sessão expirada. Por favor, recarregue a página (F5) para fazer login novamente.');
                window.location.reload();
            } else {
                alert('Error setting mode: ' + errorMsg);
            }
        }
    } catch (e) {
        alert('Failed to connect to API');
    }
}

// --- WebSockets ---
function connectStatusWS() {
    statusWs = new WebSocket(WS_STATUS_URL);

    statusWs.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updateDashboard(data);
    };

    statusWs.onclose = () => {
        document.getElementById('mode-text').innerText = 'DISCONNECTED';
        document.getElementById('mode-badge').className = 'status-badge';
        setTimeout(() => {
            if(!document.getElementById('login-overlay').classList.contains('active')) {
                connectStatusWS();
            }
        }, 3000);
    };
}

function connectLogsWS() {
    logsWs = new WebSocket(WS_LOGS_URL);
    const terminal = document.getElementById('terminal-logs');

    logsWs.onmessage = (event) => {
        const text = event.data;
        const line = document.createElement('div');
        line.className = 'log-line';
        
        // Simples parser de cor
        if(text.includes('[INFO]')) {
            line.innerHTML = text.replace('[INFO]', '<span class="log-info">[INFO]</span>');
        } else if(text.includes('[WARN]')) {
            line.innerHTML = text.replace('[WARN]', '<span class="log-warn">[WARN]</span>');
        } else if(text.includes('[ERROR]')) {
            line.innerHTML = text.replace('[ERROR]', '<span class="log-error">[ERROR]</span>');
        } else {
            line.innerText = text;
        }
        
        terminal.appendChild(line);
        
        // Auto scroll if near bottom
        if (terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 100) {
            terminal.scrollTop = terminal.scrollHeight;
        }
        
        // Keep max 200 lines
        while(terminal.children.length > 200) {
            terminal.removeChild(terminal.firstChild);
        }
    };
    
    logsWs.onclose = () => {
        setTimeout(() => {
            if(!document.getElementById('login-overlay').classList.contains('active')) {
                connectLogsWS();
            }
        }, 3000);
    };
}

// --- UI Updates ---
function updateDashboard(data) {
    const badge = document.getElementById('mode-badge');
    const badgeText = document.getElementById('mode-text');
    const btnStop = document.getElementById('btn-stop');
    const btnSim = document.getElementById('btn-sim');
    const btnLive = document.getElementById('btn-live');

    badgeText.innerText = data.mode;
    
    // Reset classes
    badge.className = 'status-badge';
    btnStop.classList.remove('active');
    btnSim.classList.remove('active');
    btnLive.classList.remove('active');
    
    if (data.mode === 'SIMULATION') {
        badge.classList.add('sim');
        btnSim.classList.add('active');
    } else if (data.mode === 'LIVE') {
        badge.classList.add('live');
        btnLive.classList.add('active');
    } else if (data.mode === 'STOPPED') {
        // We can add a custom color for stopped in CSS, fallback is neutral
        badgeText.innerText = 'STOPPED';
        btnStop.classList.add('active');
    }

    const pnlEl = document.getElementById('val-pnl');
    const pnl = data.metrics.pnl;
    pnlEl.innerText = `$${pnl.toFixed(2)}`;
    pnlEl.className = 'metric-value ' + (pnl >= 0 ? 'positive' : 'negative');
    
    document.getElementById('val-winrate').innerText = `${data.metrics.win_rate}%`;
    document.getElementById('val-total-trades').innerText = data.metrics.total_trades;

    updateChart(data.recent_trades);
    updateTable(data.recent_trades);
}

function initChart() {
    const ctx = document.getElementById('pnlChart').getContext('2d');
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.font.family = 'Inter';

    pnlChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Cumulative P&L ($)',
                data: [],
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 2,
                pointRadius: 3,
                pointBackgroundColor: '#0b0f19',
                pointBorderColor: '#3b82f6',
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index', intersect: false,
                    backgroundColor: 'rgba(18, 25, 43, 0.9)',
                    titleColor: '#f8fafc', bodyColor: '#f8fafc',
                    borderColor: 'rgba(255, 255, 255, 0.1)', borderWidth: 1
                }
            },
            scales: {
                x: { grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false } },
                y: { grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false } }
            },
            interaction: { mode: 'nearest', axis: 'x', intersect: false }
        }
    });
}

function updateChart(trades) {
    if (!pnlChart || !trades || trades.length === 0) return;

    const chronoTrades = [...trades].reverse();
    let currentPnl = 0;
    const labels = [];
    const dataPoints = [];

    chronoTrades.forEach(t => {
        const date = new Date(t.timestamp);
        labels.push(`${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`);
        
        if (t.outcome === 'WIN') currentPnl += (t.size_usd || 0) * 0.20;
        else if (t.outcome === 'LOSS') currentPnl -= (t.size_usd || 0) * 0.30;
        
        dataPoints.push(currentPnl);
    });

    pnlChart.data.labels = labels;
    pnlChart.data.datasets[0].data = dataPoints;
    
    if (currentPnl >= 0) {
        pnlChart.data.datasets[0].borderColor = '#10b981';
        pnlChart.data.datasets[0].backgroundColor = 'rgba(16, 185, 129, 0.1)';
        pnlChart.data.datasets[0].pointBorderColor = '#10b981';
    } else {
        pnlChart.data.datasets[0].borderColor = '#ef4444';
        pnlChart.data.datasets[0].backgroundColor = 'rgba(239, 68, 68, 0.1)';
        pnlChart.data.datasets[0].pointBorderColor = '#ef4444';
    }
    pnlChart.update();
}

function updateTable(trades) {
    const tbody = document.getElementById('trades-table-body');
    tbody.innerHTML = '';

    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">No trades found.</td></tr>';
        return;
    }

    trades.forEach(t => {
        const tr = document.createElement('tr');
        const date = new Date(t.timestamp);
        const timeStr = `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
        
        let outcomeBadge = `<span class="badge pending">PENDING</span>`;
        if (t.outcome === 'WIN') outcomeBadge = `<span class="badge win">WIN</span>`;
        else if (t.outcome === 'LOSS') outcomeBadge = `<span class="badge loss">LOSS</span>`;

        const size = t.size_usd ? `$${t.size_usd.toFixed(2)}` : '-';
        const price = t.price ? `$${t.price.toFixed(4)}` : '-';
        const conf = t.signal_confidence ? `${(t.signal_confidence * 100).toFixed(1)}%` : '-';

        tr.innerHTML = `
            <td>${timeStr}</td>
            <td><strong>${t.direction}</strong></td>
            <td>${size}</td>
            <td>${price}</td>
            <td>${conf}</td>
            <td>${outcomeBadge}</td>
        `;
        tbody.appendChild(tr);
    });
}
