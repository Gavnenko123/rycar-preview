// ============================================================================
// Misprice Hunter — фронтенд-логика
// Запуск GitHub Actions workflow через API + отображение результатов
// ============================================================================

const $ = (id) => document.getElementById(id);

const STORAGE_KEYS = {
  token: 'mh_github_token',
  owner: 'mh_repo_owner',
  repo: 'mh_repo_name',
  queries: 'mh_queries',
};

const SEVERITY = {
  critical: { label: '🔴 КРИТИЧНО', cls: 'badge-danger' },
  high:     { label: '🟠 ВИСОКА',   cls: 'badge-danger' },
  medium:   { label: '🟡 Середня',  cls: 'badge-warn' },
  low:      { label: '⚪ Низька',   cls: 'badge' },
};

const CATEGORY_LABELS = {
  laptop: 'Ноутбуки', pc: 'ПК', phone: 'Смартфони', gpu: 'Відеокарти',
  cpu: 'Процесори', motherboard: 'Мат. плати', monitor: 'Монітори',
  tablet: 'Планшети', other: 'Інше',
};

// ---------------------------------------------------------------------------
// Инициализация — подгружаем сохранённые значения
// ---------------------------------------------------------------------------
function init() {
  // Восстанавливаем из localStorage
  const saved = (k) => localStorage.getItem(k);
  if (saved(STORAGE_KEYS.token))  $( 'token').value  = saved(STORAGE_KEYS.token);
  if (saved(STORAGE_KEYS.owner))  $( 'owner').value  = saved(STORAGE_KEYS.owner);
  if (saved(STORAGE_KEYS.repo))   $( 'repo').value   = saved(STORAGE_KEYS.repo);
  if (saved(STORAGE_KEYS.queries))$('queries').value = saved(STORAGE_KEYS.queries);

  $('runBtn').addEventListener('click', runScan);
  $('refreshBtn').addEventListener('click', loadResults);

  // Скачивание результатов сразу при загрузке
  loadResults();
}

// ---------------------------------------------------------------------------
// Сохранение настроек
// ---------------------------------------------------------------------------
function saveSettings() {
  localStorage.setItem(STORAGE_KEYS.token,   $('token').value.trim());
  localStorage.setItem(STORAGE_KEYS.owner,   $('owner').value.trim());
  localStorage.setItem(STORAGE_KEYS.repo,    $('repo').value.trim());
  localStorage.setItem(STORAGE_KEYS.queries, $('queries').value.trim());
}

// ---------------------------------------------------------------------------
// Статус-бар
// ---------------------------------------------------------------------------
function setStatus(text, kind = 'info', spinner = false) {
  const el = $('status');
  el.className = `status ${kind}`;
  el.innerHTML = `${spinner ? '<span class="spinner"></span>' : ''}<span>${text}</span>`;
}

function clearStatus() {
  $('status').className = 'status hidden';
  $('status').innerHTML = '';
}

// ---------------------------------------------------------------------------
// Запуск workflow через GitHub API
// ---------------------------------------------------------------------------
async function runScan() {
  const token = $('token').value.trim();
  const owner = $('owner').value.trim();
  const repo  = $('repo').value.trim();
  const queries = $('queries').value.trim();

  if (!token) {
    setStatus('⚠️ Введіть GitHub PAT (налаштування → Developer settings → Personal access tokens → Tokens (classic) → Generate new token, права: repo + workflow).', 'error');
    return;
  }
  if (!owner || !repo) {
    setStatus('⚠️ Вкажіть власника та назву репозиторію.', 'error');
    return;
  }
  if (!queries) {
    setStatus('⚠️ Вкажіть хоча б один пошуковий запит.', 'error');
    return;
  }

  saveSettings();
  $('runBtn').disabled = true;

  try {
    // 1. Находим ID workflow "Price Scan" (по имени файла или имени)
    setStatus('🔍 Пошук workflow...', 'info', true);
    const wfRes = await fetch(`https://api.github.com/repos/${owner}/${repo}/actions/workflows`, {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
    });
    if (!wfRes.ok) {
      const err = await wfRes.json().catch(() => ({}));
      throw new Error(`GitHub API ${wfRes.status}: ${err.message || wfRes.statusText}`);
    }
    const wfData = await wfRes.json();
    const wf = wfData.workflows.find(w => w.name === 'Price Scan' || w.path.endsWith('scan.yml'));
    if (!wf) {
      throw new Error('Workflow "Price Scan" не знайдено. Перевірте, що .github/workflows/scan.yml закомічений у репо.');
    }

    // 2. Запускаем workflow
    setStatus('▶️ Запуск workflow...', 'info', true);
    const dispRes = await fetch(`https://api.github.com/repos/${owner}/${repo}/actions/workflows/${wf.id}/dispatches`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        ref: 'main',
        inputs: { queries },
      }),
    });
    if (!dispRes.ok && dispRes.status !== 204) {
      const err = await dispRes.json().catch(() => ({}));
      throw new Error(`Не вдалося запустити: ${err.message || dispRes.statusText}`);
    }

    // 3. Ждём появления нового run (polling)
    setStatus('⏳ Workflow запущено. Чекаємо на появу run...', 'waiting', true);
    const run = await waitForRunToAppear(owner, repo, token);
    setStatus(`⚙️ Скан виконується (run #${run.run_number})...`, 'waiting', true);

    // 4. Ждём завершения
    const completed = await waitForRunCompletion(owner, repo, token, run.id);
    if (completed.conclusion === 'success') {
      setStatus('✅ Скан завершено успішно! Завантажуємо результати...', 'success', true);
    } else {
      setStatus(`⚠️ Workflow завершився зі статусом: ${completed.conclusion}. Можливо, частина сайтів заблокована. Завантажуємо те, що є...`, 'waiting', false);
    }

    // 5. Небольшая пауза, чтобы Pages увидел новый коммит
    await sleep(3000);

    // 6. Загружаем свежие результаты
    await loadResults(true);
    setStatus('✅ Готово! Результати оновлено.', 'success', false);

  } catch (e) {
    console.error(e);
    setStatus(`❌ Помилка: ${e.message}`, 'error');
  } finally {
    $('runBtn').disabled = false;
  }
}

async function waitForRunToAppear(owner, repo, token) {
  // После dispatch run появляется с задержкой 2-10 сек
  for (let i = 0; i < 30; i++) {
    await sleep(2000);
    const res = await fetch(`https://api.github.com/repos/${owner}/${repo}/actions/runs?per_page=5`, {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
      },
    });
    if (!res.ok) continue;
    const data = await res.json();
    const run = data.workflow_runs.find(r => r.name === 'Price Scan');
    if (run) return run;
  }
  throw new Error('Workflow run не з\'явився за 60 сек. Перевірте вкладку Actions на GitHub.');
}

async function waitForRunCompletion(owner, repo, token, runId) {
  // Workflow обычно идёт 1-3 минуты
  for (let i = 0; i < 90; i++) {  // 90 * 4 сек = 6 минут максимум
    await sleep(4000);
    const res = await fetch(`https://api.github.com/repos/${owner}/${repo}/actions/runs/${runId}`, {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
      },
    });
    if (!res.ok) continue;
    const run = await res.json();
    if (run.status === 'completed') return run;
  }
  throw new Error('Workflow не завершився за 6 хвилин. Перевірте вкладку Actions.');
}

// ---------------------------------------------------------------------------
// Загрузка результатов (docs/results.json)
// ---------------------------------------------------------------------------
async function loadResults(isFresh = false) {
  const owner = $('owner').value.trim() || 'gavnenko123';
  const repo  = $('repo').value.trim()  || 'rycar-preview';
  const branch = 'main';
  const base = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/misprice/docs`;

  try {
    if (isFresh) {
      // bust cache
      const bust = `?t=${Date.now()}`;
      await loadAll(base + bust, owner, repo, branch);
    } else {
      await loadAll(base, owner, repo, branch);
    }
  } catch (e) {
    console.error(e);
    showNoResults();
  }
}

async function loadAll(baseUrl, owner, repo, branch) {
  const res = await fetch(baseUrl.replace(/\.gitkeep.*$/, '') + '/results.json', { cache: 'no-store' });
  if (!res.ok) {
    if (res.status === 404) {
      showNoResults();
      return;
    }
    throw new Error(`HTTP ${res.status}`);
  }
  // Добавляем timestamp к URL для обхода кеша
  const data = await res.json();
  renderResults(data);

  // Обновляем ссылки на скачивание (raw с cache-bust)
  const t = Date.now();
  $('dlJson').href   = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/misprice/docs/results.json?t=${t}`;
  $('dlAlerts').href = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/misprice/docs/results_alerts.csv?t=${t}`;
  $('dlAll').href    = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/misprice/docs/results_all.csv?t=${t}`;
  $('dlLog').href    = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/misprice/docs/scan_log.txt?t=${t}`;

  $('footerStatus').textContent = `Останнє сканування: ${formatTime(data.scan_time_utc)}`;
}

function showNoResults() {
  ['summary', 'alertsSection', 'productsSection', 'noAlerts'].forEach(id => $(id).classList.add('hidden'));
  setStatus('ℹ️ Результатів ще немає. Запустіть скан кнопкою вище.', 'info');
}

// ---------------------------------------------------------------------------
// Рендеринг
// ---------------------------------------------------------------------------
function renderResults(data) {
  // Summary
  $('statTotal').textContent = data.total_products_scanned ?? '—';
  $('statAlerts').textContent = data.misprice_alerts_count ?? '—';
  $('statCritical').textContent = data.critical_count ?? '—';
  $('statTime').textContent = formatTime(data.scan_time_utc);
  $('summary').classList.remove('hidden');

  // Alerts
  const alerts = data.alerts || [];
  const body = $('alertsBody');
  body.innerHTML = '';
  if (alerts.length > 0) {
    $('alertsBadge').textContent = alerts.length;
    $('noAlerts').classList.add('hidden');
    $('alertsSection').classList.remove('hidden');
    for (const a of alerts) {
      const sev = SEVERITY[a.severity] || SEVERITY.low;
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="badge ${sev.cls}">${sev.label}</span></td>
        <td><strong>${a.misprice_score.toFixed(0)}</strong></td>
        <td class="price price-low">${formatPrice(a.current_price)}</td>
        <td class="text-muted">${formatPrice(a.median_price)}</td>
        <td class="text-danger">−${a.discount_vs_median_pct.toFixed(0)}%</td>
        <td><span class="badge">${CATEGORY_LABELS[a.category] || a.category}</span></td>
        <td class="name-cell">
          <div class="name truncate" title="${escapeHtml(a.name)}">${escapeHtml(a.name)}</div>
          <div class="reason">${escapeHtml(a.reason)}</div>
        </td>
        <td><span class="badge">${a.site}</span></td>
        <td><a class="icon-btn" href="${escapeHtml(a.url)}" target="_blank" rel="noopener" title="Відкрити товар">↗</a></td>
      `;
      body.appendChild(tr);
    }
  } else {
    $('alertsSection').classList.add('hidden');
    $('noAlerts').classList.remove('hidden');
  }

  // All products
  const products = data.all_products || [];
  $('productsBadge').textContent = products.length;
  const pbody = $('productsBody');
  pbody.innerHTML = '';
  if (products.length > 0) {
    $('productsSection').classList.remove('hidden');
    const sorted = [...products].sort((a, b) => a.price - b.price);
    for (const p of sorted) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="badge">${CATEGORY_LABELS[p.category] || p.category}</span></td>
        <td><span class="badge">${p.site}</span></td>
        <td class="price">${formatPrice(p.price)}</td>
        <td class="name-cell"><div class="name truncate" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</div></td>
        <td><a class="icon-btn" href="${escapeHtml(p.url)}" target="_blank" rel="noopener" title="Відкрити">↗</a></td>
      `;
      pbody.appendChild(tr);
    }
  } else {
    $('productsSection').classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// Утилиты
// ---------------------------------------------------------------------------
function formatPrice(p) {
  if (p == null) return '—';
  return new Intl.NumberFormat('ru-RU').format(Math.round(p)) + ' ₴';
}

function formatTime(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('ru-RU', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Старт
document.addEventListener('DOMContentLoaded', init);
