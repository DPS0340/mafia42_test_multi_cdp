/**
 * MegaphoneMonitor — 재사용 가능한 확성기 모니터 Web Component.
 *
 * 사용법:
 *   <!-- 서버 모드 -->
 *   <megaphone-monitor mode="server" id="monitor"></megaphone-monitor>
 *
 *   <!-- 테스트 모드 -->
 *   <megaphone-monitor mode="test" id="monitor" speed="200"
 *       channels='[{"id":0,"name":"초보"},{"id":1,"name":"1채널"}]'>
 *   </megaphone-monitor>
 *
 * Public API:
 *   monitor.addMessage(msg)
 *   monitor.addTab(id, name)
 *   monitor.updateStatus(id, status)
 *   monitor.clearMessages()
 *   monitor.exportMessages('json' | 'csv') → Blob
 *   monitor.search(query)
 *   monitor.start()  // 테스트 모드 시뮬레이션 시작
 *   monitor.stop()   // 테스트 모드 시뮬레이션 정지
 *
 * Custom Events:
 *   megaphone-message  → { detail: { message } }
 *   megaphone-status   → { detail: { channel_id, status } }
 *   megaphone-global   → { detail: { seq } }
 */

class MegaphoneMonitor extends HTMLElement {
  static get observedAttributes() {
    return ['mode', 'channels', 'speed'];
  }

  // ──────────────────────────────────────────────
  // State
  // ──────────────────────────────────────────────

  /** @type {Record<number, string>} */
  _channelStatus = {};

  /** @type {Map<number, {id: number, name: string}>} */
  _channelMap = new Map();

  /** @type {Array<object>} */
  _messages = [];

  /** @type {Record<number, Array<object>>} */
  _channelMsgs = {};

  /** @type {Map<number, object>} */
  _bySeq = new Map();

  /** @type {string} */
  _activeTab = 'all';

  /** @type {number} */
  _totalCount = 0;

  /** @type {boolean} */
  _autoScroll = true;

  /** @type {string} */
  _filterText = '';

  /** @type {number} */
  _simSeq = 0;

  /** @type {boolean} */
  _simGlobalMode = false;

  /** @type {boolean} */
  _simRunning = false;

  /** @type {number|null} */
  _simInterval = null;

  /** @type {number} */
  _simSpeed = 200;

  /** @type {string} */
  _mode = 'server';

  /** @type {Array<{id: number, name: string}>} */
  _testChannels = [];

  /** @type {EventSource|null} */
  _es = null;

  /** @type {number} */
  _maxBuffer = 500;

  /** @type {string} */
  _timeFormat = localStorage.getItem('megaphone-timeFormat') || 'full';

  /** @type {boolean} */
  _hideSpam = localStorage.getItem('megaphone-hideSpam') !== 'false';

  /** @type {number} */
  _currentPage = 1;

  /** @type {number} */
  _pageSize = 20;

  /** @type {Map<string, number>} */
  _nicknameCount = new Map();

  /** @type {Set<number>} */
  _activeChannels = new Set();

  // ──────────────────────────────────────────────
  // Constructor
  // ──────────────────────────────────────────────

  constructor() {
    super();
    this._shadow = this.attachShadow({ mode: 'open' });
    this._render();
  }

  connectedCallback() {
    try {
      this._mode = this.getAttribute('mode') || 'server';
      this._maxBuffer = parseInt(this.getAttribute('max-buffer') || '500', 10);
      this._simSpeed = parseInt(this.getAttribute('speed') || '200', 10);

      if (this._mode === 'test') {
        this._parseTestChannels();
        if (this._testChannels.length > 0) {
          this._setConnStatus('시뮬레이션 준비됨 (시작 버튼 클릭)');
        } else {
          console.error('[MegaphoneMonitor] channels 속성이 비어있거나 잘못되었습니다.');
          this._setConnStatus('오류: channels 속성 확인');
        }
      } else {
        this._connectServer();
      }
    } catch (e) {
      console.error('[MegaphoneMonitor] 연결 오류:', e);
      this._setConnStatus('오류: ' + e.message);
    }
  }

  disconnectedCallback() {
    if (this._simInterval) { clearInterval(this._simInterval); this._simInterval = null; }
    if (this._es) { this._es.close(); this._es = null; }
  }

  attributeChangedCallback(name, oldVal, newVal) {
    if (name === 'mode' && newVal !== oldVal) {
      this._mode = newVal || 'server';
      if (this._mode === 'test') {
        this._parseTestChannels();
      } else {
        this._connectServer();
      }
    }
    if (name === 'speed' && this._mode === 'test') {
      this._simSpeed = parseInt(newVal || '200', 10);
      if (this._simRunning) {
        clearInterval(this._simInterval);
        this._simInterval = setInterval(() => this._simTick(), this._simSpeed);
      }
    }
    if (name === 'channels' && this._mode === 'test') {
      this._parseTestChannels();
    }
  }

  // ──────────────────────────────────────────────
  // Shadow DOM
  // ──────────────────────────────────────────────

  _render() {
    this._shadow.innerHTML = `
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:host {
  --color-bg: #0f0f23;
  --color-surface: #1a1a2e;
  --color-surface-hover: #252540;
  --color-border: #2a2a4a;
  --color-text: #e8e8e8;
  --color-text-muted: #8888aa;
  --color-accent: #e94560;
  --color-accent-hover: #ff6b81;
  --color-success: #4ecca3;
  --color-warning: #f0c929;
  --color-info: #89c2d9;

  display: block;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
  background: var(--color-bg);
  color: var(--color-text);
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  line-height: 1.5;
}

*, *::before, *::after {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

/* ── Header ── */
header {
  background: var(--color-surface);
  padding: 16px 24px;
  border-bottom: 1px solid var(--color-border);
}

.header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo h1 {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-accent);
  letter-spacing: -0.02em;
}

.logo .subtitle {
  font-size: 12px;
  color: var(--color-text-muted);
  font-weight: 400;
}

.status {
  font-size: 13px;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-text-muted);
}

.status-dot.connected { background: var(--color-success); }
.status-dot.connecting { background: var(--color-warning); animation: pulse 1s infinite; }
.status-dot.disconnected { background: var(--color-accent); }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* ── Search & Filters ── */
.search-section {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.search-box {
  flex: 1;
  min-width: 200px;
  position: relative;
}

.search-box input {
  width: 100%;
  padding: 10px 16px 10px 40px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  color: var(--color-text);
  font-size: 14px;
  transition: all 0.2s;
}

.search-box input:focus {
  outline: none;
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(233, 69, 96, 0.2);
}

.search-box input::placeholder {
  color: var(--color-text-muted);
}

.search-box::before {
  content: '🔍';
  position: absolute;
  left: 14px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 14px;
  opacity: 0.6;
}

.filters {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.filter-group {
  display: flex;
  gap: 4px;
  align-items: center;
}

.filter-label {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-right: 4px;
}

.filter-btn {
  padding: 6px 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text-muted);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.filter-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.filter-btn.active {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: white;
}

.filter-btn.active:hover {
  background: var(--color-accent-hover);
}

/* ── Toggle Controls ── */
.toggle-controls {
  display: flex;
  gap: 12px;
  align-items: center;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--color-text-muted);
  cursor: pointer;
}

.toggle-label input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--color-accent);
}

/* ── Main Content ── */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 16px 24px;
}

/* ── Popular Nicknames ── */
.popular-section {
  margin-bottom: 16px;
}

.popular-title {
  font-size: 13px;
  color: var(--color-text-muted);
  margin-bottom: 8px;
  font-weight: 500;
}

.popular-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.popular-item {
  padding: 4px 10px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 16px;
  font-size: 12px;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: all 0.2s;
}

.popular-item:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-accent);
}

.popular-item .count {
  margin-left: 4px;
  color: var(--color-accent);
  font-weight: 600;
}

/* ── Tabs ── */
.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  overflow-x: auto;
  padding-bottom: 4px;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}

.tabs::-webkit-scrollbar {
  display: none;
}

.tab {
  padding: 8px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  color: var(--color-text-muted);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  flex-shrink: 0;
}

.tab:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.tab.active {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: white;
}

.tab .dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}

.dot.connected { background: var(--color-success); }
.dot.connecting { background: var(--color-warning); animation: pulse 1s infinite; }
.dot.disconnected { background: var(--color-accent); }
.dot.idle { background: var(--color-text-muted); }

.tab .count {
  margin-left: 6px;
  background: rgba(255, 255, 255, 0.2);
  padding: 1px 6px;
  border-radius: 10px;
  font-size: 11px;
}

.tab .pop {
  margin-left: 6px;
  color: var(--color-info);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  opacity: 0.8;
}

.tab .pop::before {
  content: "\\1F465";
  margin-right: 2px;
  font-size: 10px;
}

/* ── Message Table ── */
.table-container {
  flex: 1;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
}

.message-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.message-table thead {
  position: sticky;
  top: 0;
  z-index: 10;
}

.message-table th {
  background: var(--color-surface-hover);
  padding: 12px 16px;
  text-align: left;
  font-weight: 600;
  font-size: 12px;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--color-border);
}

.message-table th:first-child {
  width: 60px;
  text-align: center;
}

.message-table th:nth-child(2) {
  width: 180px;
}

.message-table th:nth-child(3) {
  width: 120px;
}

.message-table th:nth-child(5) {
  width: 140px;
}

.message-table tbody tr {
  border-bottom: 1px solid var(--color-border);
  transition: background 0.15s;
}

.message-table tbody tr:hover {
  background: var(--color-surface-hover);
}

.message-table tbody tr:last-child {
  border-bottom: none;
}

.message-table td {
  padding: 12px 16px;
  vertical-align: middle;
}

.message-table td:first-child {
  text-align: center;
  color: var(--color-text-muted);
  font-size: 12px;
}

.message-table .time {
  color: var(--color-text-muted);
  font-size: 12px;
  font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Mono', 'Droid Sans Mono', 'Source Code Pro', monospace;
}

.message-table .nickname {
  color: var(--color-info);
  font-weight: 500;
  cursor: pointer;
}

.message-table .nickname:hover {
  color: var(--color-accent);
  text-decoration: underline;
}

.message-table .content {
  color: var(--color-text);
  word-break: break-word;
}

.message-table .content mark {
  background: var(--color-warning);
  color: var(--color-bg);
  padding: 0 2px;
  border-radius: 2px;
}

.message-table .channel {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.channel-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

/* Channel colors */
.ch-0 { background: #1b4332; }
.ch-1 { background: #1b3a4b; }
.ch-2 { background: #3c1642; }
.ch-3 { background: #432818; }
.ch-20 { background: #4a0e0e; }
.ch-42 { background: #0b2545; }
.ch-142 { background: #3a0ca3; }
.ch-global { background: var(--color-accent); }

/* ── Empty State ── */
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--color-text-muted);
}

.empty-state .icon {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.5;
}

.empty-state .title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--color-text);
}

.empty-state .subtitle {
  font-size: 14px;
}

/* ── Pagination ── */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 16px;
  padding: 12px 0;
}

.pagination button {
  padding: 8px 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text-muted);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  min-width: 36px;
}

.pagination button:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.pagination button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pagination button.active {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: white;
}

.pagination .info {
  font-size: 13px;
  color: var(--color-text-muted);
  margin: 0 8px;
}

/* ── Footer ── */
footer {
  background: var(--color-surface);
  padding: 12px 24px;
  border-top: 1px solid var(--color-border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.footer-stats {
  display: flex;
  gap: 16px;
  align-items: center;
}

.stat {
  font-size: 13px;
  color: var(--color-text-muted);
}

.stat strong {
  color: var(--color-text);
  font-weight: 600;
}

.footer-controls {
  display: flex;
  gap: 8px;
  align-items: center;
}

.sim-controls {
  display: none;
  gap: 8px;
  align-items: center;
}

.sim-controls.visible {
  display: flex;
}

.sim-controls label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.sim-controls select {
  padding: 6px 10px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text);
  font-size: 12px;
}

.sim-controls button {
  padding: 8px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  color: var(--color-text-muted);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.sim-controls button:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.sim-controls button.active {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: white;
}

/* ── Mobile: ≤ 768px ── */
@media (max-width: 768px) {
  header {
    padding: 12px 16px;
  }

  .header-top {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .search-section {
    flex-direction: column;
    align-items: stretch;
  }

  .filters {
    flex-wrap: wrap;
  }

  .main-content {
    padding: 12px 16px;
  }

  .tabs {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  .tab {
    padding: 6px 12px;
    font-size: 12px;
  }

  .table-container {
    overflow-x: auto;
  }

  .message-table {
    min-width: 600px;
  }

  .message-table th,
  .message-table td {
    padding: 10px 12px;
  }

  footer {
    padding: 12px 16px;
    flex-direction: column;
    align-items: flex-start;
  }

  .footer-stats {
    flex-wrap: wrap;
    gap: 12px;
  }

  .footer-controls {
    flex-wrap: wrap;
  }
}

/* ── Small phone: ≤ 480px ── */
@media (max-width: 480px) {
  .logo h1 {
    font-size: 18px;
  }

  .search-box input {
    padding: 8px 12px 8px 36px;
    font-size: 13px;
  }

  .filter-btn {
    padding: 4px 8px;
    font-size: 11px;
  }

  .popular-item {
    padding: 3px 8px;
    font-size: 11px;
  }

  .message-table th,
  .message-table td {
    padding: 8px 10px;
    font-size: 12px;
  }

  .message-table th:first-child,
  .message-table td:first-child {
    display: none;
  }

  .message-table th:nth-child(2),
  .message-table td:nth-child(2) {
    width: 120px;
  }

  .pagination button {
    padding: 6px 10px;
    font-size: 12px;
  }
}
</style>

<header>
  <div class="header-top">
    <div class="logo">
      <h1>MAFIA42 확성기</h1>
      <span class="subtitle">실시간 모니터</span>
    </div>
    <div class="status">
      <span class="status-dot" id="statusDot"></span>
      <span id="connStatus">로딩 중...</span>
    </div>
  </div>

  <div class="search-section">
    <div class="search-box">
      <input type="text" id="searchInput" placeholder="닉네임 또는 메시지 검색..." />
    </div>

    <div class="filters">
      <div class="filter-group">
        <span class="filter-label">위치:</span>
        <div id="channelFilters"></div>
      </div>

      <div class="toggle-controls">
        <label class="toggle-label">
          <input type="checkbox" id="spamToggle" />
          스팸 숨김
        </label>
      </div>
    </div>
  </div>
</header>

<div class="main-content">
  <div class="popular-section" id="popularSection" style="display: none;">
    <div class="popular-title">실시간 인기 닉네임</div>
    <div class="popular-list" id="popularList"></div>
  </div>

  <div class="tabs" id="tabs">
    <div class="tab active" data-ch="all">전체</div>
  </div>

  <div class="table-container" id="tableContainer">
    <table class="message-table">
      <thead>
        <tr>
          <th>#</th>
          <th>시간</th>
          <th>닉네임</th>
          <th>내용</th>
          <th>위치</th>
        </tr>
      </thead>
      <tbody id="messageBody">
      </tbody>
    </table>
  </div>

  <div class="pagination" id="pagination">
    <button id="prevPage" disabled>‹</button>
    <span class="info" id="pageInfo">1 / 1</span>
    <button id="nextPage" disabled>›</button>
  </div>
</div>

<footer>
  <div class="footer-stats">
    <span class="stat">메시지: <strong id="msgCount">0</strong></span>
    <span class="stat">채널: <strong id="connCount">0/0</strong></span>
  </div>

  <div class="footer-controls">
    <div class="sim-controls" id="simControls">
      <label>속도:</label>
      <select id="speedSelect">
        <option value="500">느림 (500ms)</option>
        <option value="200" selected>보통 (200ms)</option>
        <option value="50">빠름 (50ms)</option>
        <option value="0">정지</option>
      </select>
      <button id="startBtn">시작</button>
      <button id="exportBtn">내보내기</button>
      <button id="clearBtn">지우기</button>
    </div>
  </div>
</footer>
`;

    // Cache DOM references.
    this._els = {};
    [
      'statusDot', 'connStatus', 'searchInput', 'channelFilters', 'spamToggle',
      'popularSection', 'popularList', 'tabs', 'tableContainer', 'messageBody',
      'pagination', 'prevPage', 'nextPage', 'pageInfo', 'msgCount', 'connCount',
      'simControls', 'speedSelect', 'startBtn', 'exportBtn', 'clearBtn'
    ].forEach(id => {
      this._els[id] = this._shadow.getElementById(id);
    });

    // Initialize search input
    this._els.searchInput.addEventListener('input', () => {
      this._filterText = this._els.searchInput.value.trim().toLowerCase();
      this._currentPage = 1;
      this._renderMessages();
    });

    // Initialize spam toggle
    this._els.spamToggle.checked = this._hideSpam;
    this._els.spamToggle.addEventListener('change', () => {
      this._hideSpam = this._els.spamToggle.checked;
      localStorage.setItem('megaphone-hideSpam', this._hideSpam.toString());
      this._currentPage = 1;
      this._renderMessages();
    });

    // Initialize pagination
    this._els.prevPage.addEventListener('click', () => {
      if (this._currentPage > 1) {
        this._currentPage--;
        this._renderMessages();
      }
    });

    this._els.nextPage.addEventListener('click', () => {
      const totalPages = this._getTotalPages();
      if (this._currentPage < totalPages) {
        this._currentPage++;
        this._renderMessages();
      }
    });

    // Initialize simulation controls
    this._els.speedSelect.addEventListener('change', () => {
      this._simSpeed = parseInt(this._els.speedSelect.value, 10);
      if (this._simRunning && this._simInterval) {
        clearInterval(this._simInterval);
        this._simInterval = setInterval(() => this._simTick(), this._simSpeed);
      }
    });

    this._els.startBtn.addEventListener('click', () => this._toggleSimulation());
    this._els.exportBtn.addEventListener('click', () => this.exportMessages());
    this._els.clearBtn.addEventListener('click', () => this.clearMessages());

    // Tab "전체" click
    this._els.tabs.querySelector('.tab[data-ch="all"]').addEventListener('click', () => this.switchTab('all'));

    // Scroll listener for auto-scroll
    this._els.tableContainer.addEventListener('scroll', () => {
      const container = this._els.tableContainer;
      this._autoScroll = container.scrollTop + container.clientHeight >= container.scrollHeight - 50;
    });
  }

  // ──────────────────────────────────────────────
  // Server mode — SSE + API
  // ──────────────────────────────────────────────

  _connectServer() {
    this._els.simControls.classList.remove('visible');

    // SSE connection.
    this._es = new EventSource('/events');
    this._es.onopen = () => {
      this._setConnStatus('실시간 수신 중');
      this._els.statusDot.className = 'status-dot connected';
    };
    this._es.onerror = () => {
      this._setConnStatus('서버 연결 끊김');
      this._els.statusDot.className = 'status-dot disconnected';
    };
    this._es.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.type === 'status') {
        this.addTab(data.channel_id, data.channel_name);
        this.updateStatus(data.channel_id, data.status);
      } else if (data.type === 'update' && data.scope === 'global') {
        this._markGlobal(data.seq);
      } else if (data.type === 'channels') {
        data.list.forEach(ch => { this.addTab(ch.id, ch.name); this.updateStatus(ch.id, ch.status); });
      } else if (data.type === 'population') {
        this._updatePopulation(data.channel_id, data.channel_name, data.population);
      } else {
        this.addMessage(data);
      }
    };

    // Initial data.
    fetch('/api/messages').then(r => r.json()).then(msgs => {
      msgs.forEach(m => this.addMessage(m));
      // Show empty state if no messages received
      if (msgs.length === 0) this._renderMessages();
    }).catch(() => { this._renderMessages(); });

    fetch('/api/channels').then(r => r.json()).then(chs => {
      chs.forEach(ch => {
        this.addTab(ch.id, ch.name);
        if (ch.status) this.updateStatus(ch.id, ch.status);
        if (ch.population != null) this._updatePopulation(ch.id, ch.name, ch.population);
      });
    }).catch(() => {});
  }

  // ──────────────────────────────────────────────
  // Test mode — dummy data + simulation
  // ──────────────────────────────────────────────

  _parseTestChannels() {
    const raw = this.getAttribute('channels');
    if (raw) {
      try { this._testChannels = JSON.parse(raw); } catch { this._testChannels = []; }
    }
    // Show simulation controls when test channels are valid
    if (this._testChannels.length > 0 && this._els.simControls) {
      this._els.simControls.classList.add('visible');
    }
  }

  _toggleSimulation() {
    this._simRunning = !this._simRunning;
    const btn = this._els.startBtn;
    if (this._simRunning) {
      btn.textContent = '정지';
      btn.classList.add('active');
      this._startSimulation();
    } else {
      btn.textContent = '시작';
      btn.classList.remove('active');
      this._stopSimulation();
    }
  }

  _startSimulation() {
    let connectIdx = 0;

    function connectNext() {
      if (connectIdx < this._testChannels.length) {
        const ch = this._testChannels[connectIdx];
        this.addTab(ch.id, ch.name);
        this.updateStatus(ch.id, 'connecting');
        connectIdx++;
        setTimeout(connectNext.bind(this), 300);
      } else {
        this._setConnStatus('시뮬레이션 중...');
        this._els.statusDot.className = 'status-dot connected';
        this._simInterval = setInterval(() => this._simTick(), this._simSpeed);
      }
    }
    connectNext.call(this);
  }

  _stopSimulation() {
    if (this._simInterval) { clearInterval(this._simInterval); this._simInterval = null; }
  }

  _simTick() {
    if (this._simSpeed === 0) return;

    const ch = this._testChannels[Math.floor(Math.random() * this._testChannels.length)];
    const sender = DUMMY_SENDERS[Math.floor(Math.random() * DUMMY_SENDERS.length)];
    const msg = DUMMY_MESSAGES[Math.floor(Math.random() * DUMMY_MESSAGES.length)];

    this._simGlobalMode = (this._simSeq > 0 && this._simSeq % 15 === 0);

    const times = [];
    for (let i = 0; i < 3; i++) {
      const d = new Date(Date.now() - Math.random() * 5000);
      const pad = n => String(n).padStart(2, '0');
      times.push(`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`);
    }
    times.sort();

    if (this._simGlobalMode) {
      const globalMsg = GLOBAL_MESSAGES[Math.floor(Math.random() * GLOBAL_MESSAGES.length)];
      this._simSeq++;
      const seq = this._simSeq;

      const targetChannels = [...this._testChannels].sort(() => Math.random() - 0.5).slice(0, 3);
      const firstCh = targetChannels[0];

      const msgObj = {
        seq, channel_id: firstCh.id, channel_name: firstCh.name, time: times[0],
        sender, message: globalMsg, scope: 'server', msg_id: seq * 1000, metadata: 0,
      };
      this.addMessage(msgObj);

      targetChannels.forEach(tc => {
        if (tc.id !== firstCh.id) this.updateStatus(tc.id, 'connected');
      });

      setTimeout(() => {
        this._markGlobal(seq);
        this._setConnStatus('전체 확성기 감지! 📢');
        setTimeout(() => {
          this._setConnStatus('시뮬레이션 중...');
          this._els.statusDot.className = 'status-dot connected';
        }, 3000);
      }, 800);

    } else {
      this._simSeq++;
      const msgObj = {
        seq: this._simSeq, channel_id: ch.id, channel_name: ch.name, time: times[0],
        sender, message: msg, scope: 'server', msg_id: this._simSeq * 1000, metadata: 0,
      };
      this.addMessage(msgObj);
    }
  }

  // ──────────────────────────────────────────────
  // Public API
  // ──────────────────────────────────────────────

  /**
   * Add a megaphone message to the monitor.
   * @param {object} msg — { seq, channel_id, channel_name, time, sender, message, scope, ... }
   */
  addMessage(msg) {
    // Dedup: skip if seq already seen (overlap between SSE and initial fetch).
    if (msg.seq != null && this._bySeq.has(msg.seq)) return;

    this._messages.push(msg);
    this._bySeq.set(msg.seq, msg);

    if (this._messages.length > this._maxBuffer) {
      const dropped = this._messages.shift();
      if (dropped) this._bySeq.delete(dropped.seq);
    }

    if (!this._channelMsgs[msg.channel_id]) this._channelMsgs[msg.channel_id] = [];
    this._channelMsgs[msg.channel_id].push(msg);
    if (this._channelMsgs[msg.channel_id].length > this._maxBuffer) this._channelMsgs[msg.channel_id].shift();

    this._totalCount++;
    this._els.msgCount.textContent = this._totalCount;

    // Track nickname popularity
    if (msg.sender) {
      const count = (this._nicknameCount.get(msg.sender) || 0) + 1;
      this._nicknameCount.set(msg.sender, count);
      this._updatePopularNicknames();
    }

    // Re-render if on current tab or "all"
    if (this._activeTab === 'all' || this._activeTab == msg.channel_id) {
      this._renderMessages();
    }

    // Dispatch custom event.
    this.dispatchEvent(new CustomEvent('megaphone-message', { detail: { message: msg } }));
  }

  /**
   * Add a channel tab.
   * @param {number} id
   * @param {string} name
   */
  addTab(id, name) {
    if (this._channelMap.has(id)) return;
    this._channelMap.set(id, { id, name });

    // Add tab
    const tab = document.createElement('div');
    tab.className = 'tab';
    tab.dataset.ch = id;
    tab.innerHTML = `<span class="dot connecting"></span>${name}`;
    tab.addEventListener('click', () => this.switchTab(id));
    this._els.tabs.appendChild(tab);

    // Add filter button
    const filterBtn = document.createElement('button');
    filterBtn.className = 'filter-btn active';
    filterBtn.dataset.ch = id;
    filterBtn.textContent = name;
    filterBtn.addEventListener('click', () => {
      filterBtn.classList.toggle('active');
      this._activeChannels.toggle(id);
      this._renderMessages();
    });
    this._els.channelFilters.appendChild(filterBtn);

    this._activeChannels.add(id);
    this._updateConnCount();
  }

  /**
   * Update channel connection status.
   * @param {number} id
   * @param {string} status — 'connected' | 'connecting' | 'disconnected' | 'denied'
   */
  updateStatus(id, status) {
    this._channelStatus[id] = status;
    const tab = this._els.tabs.querySelector(`.tab[data-ch="${id}"]`);
    if (tab) {
      const dot = tab.querySelector('.dot');
      if (dot) dot.className = 'dot ' + status;
    }
    this._updateConnCount();

    this.dispatchEvent(new CustomEvent('megaphone-status', {
      detail: { channel_id: id, status },
    }));
  }

  /**
   * Switch active tab.
   * @param {string|number} ch
   */
  switchTab(ch) {
    this._activeTab = ch;
    this._els.tabs.querySelectorAll('.tab').forEach(t => {
      t.classList.toggle('active', t.dataset.ch == ch);
    });
    this._currentPage = 1;
    this._renderMessages();
  }

  /**
   * Clear all messages.
   */
  clearMessages() {
    this._messages.length = 0;
    this._channelMsgs = {};
    this._bySeq.clear();
    this._totalCount = 0;
    this._simSeq = 0;
    this._simGlobalMode = false;
    this._nicknameCount.clear();
    this._currentPage = 1;
    this._els.msgCount.textContent = '0';
    this._renderMessages();
    this._updatePopularNicknames();
  }

  /**
   * Export all messages as JSON or CSV Blob.
   * @param {'json'|'csv'} format
   * @returns {Promise<Blob>}
   */
  exportMessages(format = 'json') {
    if (this._messages.length === 0) {
      alert('내보낼 메시지가 없습니다.');
      return Promise.resolve(new Blob());
    }

    if (format === 'csv') {
      const output = [];
      output.push('time,channel_id,channel_name,scope,sender,message');
      this._messages.forEach(m => {
        output.push(
          `"${m.time || ''}","${m.channel_id || ''}","${m.channel_name || ''}","${m.scope || ''}","${m.sender || ''}","${(m.message || '').replace(/"/g, '""')}"`
        );
      });
      return Promise.resolve(new Blob([output.join('\n')], { type: 'text/csv' }));
    }

    return Promise.resolve(new Blob([JSON.stringify(this._messages, null, 2)], { type: 'application/json' }));
  }

  /**
   * Search/filter messages.
   * @param {string} query
   */
  search(query) {
    this._filterText = (query || '').trim().toLowerCase();
    this._currentPage = 1;
    this._renderMessages();
  }

  /**
   * Start simulation (test mode).
   */
  start() {
    this._toggleSimulation();
  }

  /**
   * Stop simulation (test mode).
   */
  stop() {
    if (this._simRunning) this._toggleSimulation();
  }

  // ──────────────────────────────────────────────
  // Internal helpers
  // ──────────────────────────────────────────────

  _setConnStatus(text) { this._els.connStatus.textContent = text; }

  _formatTime(timeStr) {
    if (!timeStr) return '';
    if (this._timeFormat === 'hide') return '';
    if (this._timeFormat === 'time') {
      // Extract time portion from "2026-06-17 00:42" or similar
      const match = timeStr.match(/(\d{2}:\d{2})/);
      return match ? match[1] : timeStr;
    }
    return timeStr; // full
  }

  _isSpam(msg) {
    if (!this._hideSpam) return false;
    const spamPatterns = [
      /급처.*삽니다/,
      /최고가.*매입/,
      /DM.*주세요/,
      /귓.*주세요/,
      /팝니다.*바로/,
      /삽니다.*가격/,
    ];
    return spamPatterns.some(p => p.test(msg.message));
  }

  _updateConnCount() {
    const connected = Object.values(this._channelStatus).filter(s => s === 'connected').length;
    const total = this._channelMap.size;
    this._els.connCount.textContent = `${connected}/${total}`;
  }

  _updatePopulation(chId, chName, n) {
    if (n == null) return;
    this._channelPopulation = this._channelPopulation || {};
    this._channelPopulation[chId] = n;
    // Ensure tab exists
    if (chName) this.addTab(chId, chName);
    // Update pop display on tab
    const tab = this._els.tabs.querySelector(`.tab[data-ch="${chId}"]`);
    if (tab) {
      let popEl = tab.querySelector('.pop');
      if (!popEl) {
        popEl = document.createElement('span');
        popEl.className = 'pop';
        tab.appendChild(popEl);
      }
      popEl.textContent = n.toLocaleString();
    }
    // Update total in footer
    const sum = Object.values(this._channelPopulation).reduce((a, b) => a + b, 0);
    let popTotal = this._els.popTotal;
    if (!popTotal) {
      popTotal = document.createElement('span');
      popTotal.className = 'stat';
      popTotal.id = 'popTotal';
      this._els.footerStats = this._els.footerStats || this._shadow.querySelector('.footer-stats');
      if (this._els.footerStats) this._els.footerStats.appendChild(popTotal);
      this._els.popTotal = popTotal;
    }
    popTotal.innerHTML = `총 접속: <strong>${sum.toLocaleString()}</strong>`;
  }

  _updatePopularNicknames() {
    const sorted = [...this._nicknameCount.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);

    if (sorted.length === 0) {
      this._els.popularSection.style.display = 'none';
      return;
    }

    this._els.popularSection.style.display = 'block';
    this._els.popularList.innerHTML = sorted.map(([name, count], i) => `
      <div class="popular-item" data-name="${name}">
        ${i + 1}. ${name} <span class="count">${count}</span>
      </div>
    `).join('');

    // Add click handlers
    this._els.popularList.querySelectorAll('.popular-item').forEach(item => {
      item.addEventListener('click', () => {
        this._els.searchInput.value = item.dataset.name;
        this._filterText = item.dataset.name.toLowerCase();
        this._currentPage = 1;
        this._renderMessages();
      });
    });
  }

  _getFilteredMessages() {
    const list = this._activeTab === 'all' ? this._messages : (this._channelMsgs[this._activeTab] || []);
    return list.filter(msg => {
      // Channel filter (skip if channels haven't loaded yet)
      if (this._activeChannels.size > 0 && !this._activeChannels.has(msg.channel_id)) return false;

      // Spam filter
      if (this._isSpam(msg)) return false;

      // Text filter
      if (this._filterText) {
        const senderMatch = msg.sender && msg.sender.toLowerCase().includes(this._filterText);
        const messageMatch = msg.message && msg.message.toLowerCase().includes(this._filterText);
        if (!senderMatch && !messageMatch) return false;
      }

      return true;
    });
  }

  _getTotalPages() {
    const filtered = this._getFilteredMessages();
    return Math.max(1, Math.ceil(filtered.length / this._pageSize));
  }

  _renderMessages() {
    const filtered = this._getFilteredMessages();
    const totalPages = this._getTotalPages();

    // Ensure current page is valid
    if (this._currentPage > totalPages) this._currentPage = totalPages;
    if (this._currentPage < 1) this._currentPage = 1;

    // Get page slice
    const startIdx = (this._currentPage - 1) * this._pageSize;
    const endIdx = startIdx + this._pageSize;
    const pageMessages = filtered.slice(startIdx, endIdx);

    // Clear table body
    this._els.messageBody.innerHTML = '';

    if (pageMessages.length === 0) {
      this._els.messageBody.innerHTML = `
        <tr>
          <td colspan="5">
            <div class="empty-state">
              <div class="icon">📢</div>
              <div class="title">확성기 메시지 대기 중...</div>
              <div class="subtitle">메시지가 여기에 표시됩니다</div>
            </div>
          </td>
        </tr>
      `;
    } else {
      pageMessages.forEach((msg, i) => {
        const tr = document.createElement('tr');
        tr.dataset.seq = msg.seq;

        // Row number
        const tdNum = document.createElement('td');
        tdNum.textContent = startIdx + i + 1;

        // Time
        const tdTime = document.createElement('td');
        tdTime.className = 'time';
        tdTime.textContent = this._formatTime(msg.time);

        // Nickname (click to search)
        const tdNick = document.createElement('td');
        tdNick.className = 'nickname';
        tdNick.textContent = msg.sender;
        tdNick.title = `${msg.sender} — 클릭하여 검색`;
        tdNick.addEventListener('click', () => {
          this._els.searchInput.value = msg.sender;
          this._filterText = msg.sender.toLowerCase();
          this._currentPage = 1;
          this._renderMessages();
        });

        // Content
        const tdContent = document.createElement('td');
        tdContent.className = 'content';
        tdContent.innerHTML = this._highlightText(msg.message);

        // Channel
        const tdChannel = document.createElement('td');
        tdChannel.className = 'channel';
        const chClass = msg.scope === 'global' ? 'ch-global' : `ch-${msg.channel_id}`;
        const chName = msg.scope === 'global' ? '전체' : msg.channel_name;
        tdChannel.innerHTML = `
          <span class="channel-dot ${chClass}"></span>
          <span>${chName}</span>
        `;

        tr.append(tdNum, tdTime, tdNick, tdContent, tdChannel);
        this._els.messageBody.appendChild(tr);
      });
    }

    // Update pagination
    this._els.pageInfo.textContent = `${this._currentPage} / ${totalPages}`;
    this._els.prevPage.disabled = this._currentPage <= 1;
    this._els.nextPage.disabled = this._currentPage >= totalPages;

    // Auto-scroll to bottom if enabled
    if (this._autoScroll && this._currentPage === totalPages) {
      this._els.tableContainer.scrollTop = this._els.tableContainer.scrollHeight;
    }
  }

  _highlightText(text) {
    if (!this._filterText) return this._escapeHtml(text);
    const escaped = this._escapeHtml(text);
    const regex = new RegExp(`(${this._escapeRegex(this._filterText)})`, 'gi');
    return escaped.replace(regex, '<mark>$1</mark>');
  }

  _escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  _escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  renderMessages() {
    this._renderMessages();
  }

  _markGlobal(seq) {
    const msg = this._bySeq.get(seq);
    if (msg) msg.scope = 'global';

    // Re-render if visible
    if (this._activeTab === 'all' || this._activeTab == msg?.channel_id) {
      this._renderMessages();
    }

    this.dispatchEvent(new CustomEvent('megaphone-global', { detail: { seq } }));
  }
}

customElements.define('megaphone-monitor', MegaphoneMonitor);

// ──────────────────────────────────────────────
// Dummy data (for test mode)
// ──────────────────────────────────────────────

const DUMMY_SENDERS = [
  "마피아보스", "요원X", "감시자", "익명의용자", "간첩07",
  "추적자", "대장", "조력자", "알리바이", "최후의증인",
  "배신자", "수사관", "용의자A", "목격자", "변호사",
  "판사", "배심원", "경호원", "의사", "기자",
  "도박꾼", "상인", "요술사", "기사", "마법사",
];

const DUMMY_MESSAGES = [
  "부탁: 랭크 채널 맴버 구합니다. 실력 있는 분 환영.",
  "공지: 내일 8시 이벤트 진행합니다. 많은 참여 바랍니다.",
  "구인: 게임 잘하시는 분 모집합니다. DM 주세요.",
  "안내: 채널 규칙 준수해 주세요. 위반 시 제재 있습니다.",
  "감사: 오랜 시간 함께해 주셔서 감사합니다.",
  "문의: 계정 관련 문의는 DM으로 부탁드립니다.",
  "광고: 신규 서버 오픈합니다. 많은 가입 바랍니다.",
  "소식: 다음 주 금요일 대결전 있습니다. 준비하세요.",
  "요청: 게임 시작 전 채팅 부탁드립니다.",
  "안내: 신규 플레이어 환영합니다. 질문은 언제든지.",
  "공지: 서버 유지보수 예정입니다. 사전에 공지 드리겠습니다.",
  "부탁: 게임 진행 중 채팅 자제해 주세요.",
  "소식: 주간 랭킹 업데이트 완료되었습니다.",
  "문의: 계정 복구 문의는 1:1 문의해 주세요.",
  "안내: 신규 채널 오픈 예정입니다. 기대해 주세요.",
  "감사: 1주년 기념 이벤트 진행 중입니다. 축하합니다.",
  "광고: 게임 팁 공유합니다. DM으로 문의.",
  "소식: 다음 대결전 일정 공지드립니다.",
  "부탁: 게임 매너 지켜주세요. 서로 존중합시다.",
  "공지: 신규 아이템 업데이트 예정입니다.",
  "문의: 게임 관련 버그 신고는 DM으로.",
  "안내: 게임 시간 조정 예정입니다. 사전 공지.",
  "감사: 오랜 이용 감사드립니다. 계속 응원 부탁드립니다.",
  "소식: 신규 캐릭터 출시 예정입니다. 많은 관심.",
  "부탁: 게임 진행 중 불쾌한 채팅 자제해 주세요.",
  "공지: 이벤트 당첨자 발표 예정입니다.",
  "문의: 게임 설정 문의는 1:1 문의해 주세요.",
  "안내: 게임 서버 용량 증설 완료되었습니다.",
  "감사: 커뮤니티 활성화 감사드립니다.",
  "광고: 게임 가이드 영상 링크입니다. 확인 바랍니다.",
  "소식: 다음 시즌 시작 예정입니다. 준비하세요.",
  "부탁: 게임 진행 중 음란물 금지합니다.",
  "공지: 신규 기능 테스트 서버 오픈합니다.",
  "문의: 게임 계정 분실 문의는 DM으로.",
  "안내: 게임 업데이트 내역 공지드립니다.",
  "감사: 200만 다운로드 축하드립니다.",
  "소식: 신규 맵 출시 예정입니다. 많은 기대.",
  "부탁: 게임 진행 중 스포일러 자제해 주세요.",
  "공지: 이벤트 참가 방법 공지드립니다.",
  "문의: 게임 밸런스 문의는 DM으로.",
  "안내: 게임 시즌 종료 예정입니다. 마무리 바랍니다.",
  "감사: 플레이어 피드백 적극 반영해 드립니다.",
  "소식: 신규 이벤트 당첨자 발표 예정입니다.",
  "부탁: 게임 진행 중 부정행위 금지합니다.",
  "공지: 게임 보안 강화 공지드립니다.",
  "문의: 게임 계정 정산 문의는 DM으로.",
  "안내: 게임 신규 시즌 시작 예정입니다.",
  "감사: 오랜 이용 감사드립니다. 계속 발전하겠습니다.",
];

const GLOBAL_MESSAGES = [
  "전체: 게임 시작 전 채팅 부탁드립니다.",
  "전체: 내일 8시 이벤트 진행합니다. 많은 참여 바랍니다.",
  "전체: 신규 서버 오픈합니다. 많은 가입 바랍니다.",
  "전체: 주간 랭킹 업데이트 완료되었습니다.",
  "전체: 게임 팁 공유합니다. DM으로 문의.",
  "전체: 다음 대결전 일정 공지드립니다.",
  "전체: 게임 매너 지켜주세요. 서로 존중합시다.",
  "전체: 신규 아이템 업데이트 예정입니다.",
  "전체: 게임 관련 버그 신고는 DM으로.",
  "전체: 게임 시간 조정 예정입니다. 사전 공지.",
  "전체: 오랜 이용 감사드립니다. 계속 응원 부탁드립니다.",
  "전체: 신규 캐릭터 출시 예정입니다. 많은 관심.",
  "전체: 게임 진행 중 불쾌한 채팅 자제해 주세요.",
  "전체: 이벤트 당첨자 발표 예정입니다.",
  "전체: 게임 설정 문의는 1:1 문의해 주세요.",
  "전체: 게임 서버 용량 증설 완료되었습니다.",
  "전체: 커뮤니티 활성화 감사드립니다.",
  "전체: 게임 가이드 영상 링크입니다. 확인 바랍니다.",
  "전체: 다음 시즌 시작 예정입니다. 준비하세요.",
  "전체: 게임 진행 중 음란물 금지합니다.",
  "전체: 신규 기능 테스트 서버 오픈합니다.",
  "전체: 게임 계정 분실 문의는 DM으로.",
  "전체: 게임 업데이트 내역 공지드립니다.",
  "전체: 200만 다운로드 축하드립니다.",
  "전체: 신규 맵 출시 예정입니다. 많은 기대.",
  "전체: 게임 진행 중 스포일러 자제해 주세요.",
  "전체: 이벤트 참가 방법 공지드립니다.",
  "전체: 게임 밸런스 문의는 DM으로.",
  "전체: 게임 시즌 종료 예정입니다. 마무리 바랍니다.",
  "전체: 플레이어 피드백 적극 반영해 드립니다.",
  "전체: 신규 이벤트 당첨자 발표 예정입니다.",
  "전체: 게임 진행 중 부정행위 금지합니다.",
  "전체: 게임 보안 강화 공지드립니다.",
  "전체: 게임 계정 정산 문의는 DM으로.",
  "전체: 게임 신규 시즌 시작 예정입니다.",
  "전체: 오랜 이용 감사드립니다. 계속 발전하겠습니다.",
  "전체: 게임 시작 전 채팅 부탁드립니다.",
  "전체: 내일 8시 이벤트 진행합니다. 많은 참여 바랍니다.",
  "전체: 신규 서버 오픈합니다. 많은 가입 바랍니다.",
  "전체: 주간 랭킹 업데이트 완료되었습니다.",
];
