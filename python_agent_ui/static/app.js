const state = {
  account: null,
  chainId: null,
  nonce: null,
  signature: null,
  isAuthenticated: false,
};

const walletStatusEl = document.getElementById('walletStatus');
const walletAddressEl = document.getElementById('walletAddress');
const networkInfoEl = document.getElementById('networkInfo');
const walletBalanceEl = document.getElementById('walletBalance');
const transactionStatusEl = document.getElementById('transactionStatus');
const jobStatusEl = document.getElementById('jobStatus');
const agentResponseEl = document.getElementById('agentResponse');
const connectWalletBtn = document.getElementById('connectWalletBtn');
const authenticateBtn = document.getElementById('authenticateBtn');
const triggerBtn = document.getElementById('triggerBtn');
const agentPromptEl = document.getElementById('agentPrompt');

function setWalletStatus(label, type = 'disconnected') {
  walletStatusEl.textContent = label;
  walletStatusEl.className = `status-pill ${type}`;
  updateConnectButton();
}

function shortAddress(address) {
  if (!address) return '';
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

function updateConnectButton() {
  if (state.account) {
    connectWalletBtn.textContent = `Connected to ${shortAddress(state.account)}`;
    connectWalletBtn.classList.add('is-connected');
    connectWalletBtn.title = state.account;
  } else {
    connectWalletBtn.textContent = 'Connect Wallet';
    connectWalletBtn.classList.remove('is-connected');
    connectWalletBtn.removeAttribute('title');
  }
}

function setTxStatus(message, type = 'idle') {
  transactionStatusEl.textContent = message;
  transactionStatusEl.className = `status-box ${type}`;
}

function setJobStatus(message, type = 'idle') {
  jobStatusEl.textContent = message;
  jobStatusEl.className = `status-box ${type}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

async function apiFetch(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    let message = payload.message || 'Request failed';
    if (payload.detail) {
      message = typeof payload.detail === 'string'
        ? payload.detail
        : JSON.stringify(payload.detail, null, 2);
    }
    throw new Error(message);
  }

  return payload;
}

async function getWalletBalance(address) {
  try {
    const data = await apiFetch('/api/wallet/balance', {
      method: 'POST',
      body: JSON.stringify({ address }),
    });
    return data.balance ?? '0 ETH';
  } catch (error) {
    return 'Unavailable';
  }
}

const TARGET_CHAIN_ID = 62621;
const TARGET_CHAIN_ID_HEX = `0x${TARGET_CHAIN_ID.toString(16)}`;
const TARGET_CHAIN_CONFIG = {
  chainId: TARGET_CHAIN_ID_HEX,
  chainName: 'MultiVAC',
  nativeCurrency: {
    name: 'MultiVAC',
    symbol: 'MTV',
    decimals: 18,
  },
  rpcUrls: ['https://rpc.mtv.ac'],
  blockExplorerUrls: ['https://scan.mtv.ac'],
};

async function ensureCorrectNetwork() {
  if (!window.ethereum) {
    throw new Error('MetaMask is not installed.');
  }

  const currentChainIdHex = await window.ethereum.request({ method: 'eth_chainId' });
  const currentChainId = Number.parseInt(currentChainIdHex, 16);
  state.chainId = currentChainId;

  if (currentChainId !== TARGET_CHAIN_ID) {
    setTxStatus(`Switching MetaMask to chain ${TARGET_CHAIN_ID}...`, 'idle');
    try {
      await window.ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: TARGET_CHAIN_ID_HEX }],
      });
    } catch (switchError) {
      const code = switchError?.code;
      if (code === 4902) {
        try {
          await window.ethereum.request({
            method: 'wallet_addEthereumChain',
            params: [TARGET_CHAIN_CONFIG],
          });
        } catch (addError) {
          const message = addError?.message || 'The network could not be added.';
          setTxStatus(`Unable to add chain ${TARGET_CHAIN_ID}. ${message}`, 'error');
          throw new Error(`Unable to add chain ${TARGET_CHAIN_ID}. ${message}`);
        }
      } else {
        const message = switchError?.message || 'Network switch was rejected.';
        setTxStatus(`Unable to switch to chain ${TARGET_CHAIN_ID}. ${message}`, 'error');
        throw new Error(`Unable to switch to chain ${TARGET_CHAIN_ID}. ${message}`);
      }
    }

    const updatedChainIdHex = await window.ethereum.request({ method: 'eth_chainId' });
    state.chainId = Number.parseInt(updatedChainIdHex, 16);
    networkInfoEl.textContent = `Chain ID ${state.chainId}`;
    setTxStatus('Network switched. Authenticate to continue.', 'success');
  }

  return state.chainId;
}

async function connectWallet() {
  if (!window.ethereum) {
    setTxStatus('MetaMask is not installed. Install it in Chrome and try again.', 'error');
    return;
  }

  try {
    const accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
    if (!accounts || !accounts.length) {
      throw new Error('No wallet account was selected.');
    }

    state.account = accounts[0];
    await ensureCorrectNetwork();

    walletAddressEl.textContent = state.account;
    networkInfoEl.textContent = `Chain ID ${state.chainId}`;
    setWalletStatus('Connected', 'connected');

    const balance = await getWalletBalance(state.account);
    walletBalanceEl.textContent = balance;

    setTxStatus('Wallet connected. Authenticate to continue.', 'idle');
    triggerBtn.disabled = false;
  } catch (error) {
    console.error(error);
    setWalletStatus('Connection rejected', 'disconnected');
    setTxStatus(error.message || 'MetaMask connection failed', 'error');
  }
}

async function authenticateWallet() {
  if (!state.account) {
    setTxStatus('Connect a wallet first.', 'error');
    return;
  }

  try {
    const activeChainId = await ensureCorrectNetwork();
    if (activeChainId !== TARGET_CHAIN_ID) {
      throw new Error(`MetaMask must be connected to chain ${TARGET_CHAIN_ID}.`);
    }

    const challengeResponse = await apiFetch('/api/auth/challenge', {
      method: 'POST',
      body: JSON.stringify({ address: state.account, chainId: TARGET_CHAIN_ID }),
    });

    state.nonce = challengeResponse.nonce;
    const typedData = challengeResponse.typedData;
    const signature = await window.ethereum.request({
      method: 'eth_signTypedData_v4',
      params: [state.account, typedData],
    });

    const verifyResponse = await apiFetch('/api/auth/verify', {
      method: 'POST',
      body: JSON.stringify({
        address: state.account,
        nonce: state.nonce,
        signature,
        chainId: state.chainId,
      }),
    });

    state.signature = signature;
    state.isAuthenticated = true;
    setTxStatus(`Authenticated for ${verifyResponse.address}`, 'success');
    triggerBtn.disabled = false;
  } catch (error) {
    console.error(error);
    state.isAuthenticated = false;
    setTxStatus(error.message || 'Authentication failed.', 'error');
  }
}

async function pollAgentJob(jobId) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const job = await apiFetch(`/api/agent/jobs/${jobId}`);
    if (job.status === 'queued') {
      setJobStatus('Queued: waiting to start', 'idle');
    } else if (job.status === 'running') {
      setJobStatus('Running: sending batch transactions...', 'idle');
    } else if (job.status === 'completed') {
      setJobStatus('Completed: batch finished successfully', 'success');
      return job.result;
    } else if (job.status === 'failed') {
      setJobStatus(`Failed: ${job.error || 'Unknown error'}`, 'error');
      throw new Error(job.error || 'Job failed');
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }

  setJobStatus('Timed out waiting for the batch to finish', 'error');
  throw new Error('Batch job timed out after waiting for completion.');
}

async function triggerAgent() {
  if (!state.account || !state.isAuthenticated) {
    setTxStatus('Authenticate first with MetaMask.', 'error');
    return;
  }

  const prompt = agentPromptEl.value.trim();
  if (!prompt) {
    setTxStatus('Enter a prompt before triggering the agent.', 'error');
    return;
  }

  const batchCount = Math.max(1, Number(batchTotalEl.value) || 100);
  const batchSize = Math.max(1, Number(batchSizeEl.value) || 100);
  const nBatches = Math.ceil(batchCount / batchSize);
  if (batchCount > 50) {
    setTxStatus(`Large run requested (${batchCount} tx in ${nBatches} batches of ${batchSize}). This may take several minutes on-chain.`, 'idle');
  }

  try {
    setTxStatus('Submitting job to backend...', 'idle');
    const queued = await apiFetch('/api/agent/trigger', {
      method: 'POST',
      body: JSON.stringify({
        address: state.account,
        signature: state.signature,
        nonce: state.nonce,
        prompt,
        chainId: state.chainId,
        batchCount,
        batchSize,
      }),
    });

    agentResponseEl.textContent = JSON.stringify(queued, null, 2);
    setJobStatus('Queued: accepted by backend', 'idle');
    setTxStatus(`Background job queued: ${queued.jobId}. Waiting for completion...`, 'idle');

    const result = await pollAgentJob(queued.jobId);
    const responseText = JSON.stringify(result, null, 2);
    agentResponseEl.textContent = responseText;
    const totalTime = result.totalTimeSeconds ?? result.agentResponse?.totalTimeSeconds;
    if (Array.isArray(result.txHashes) && result.txHashes.length > 0) {
      const count = result.txHashes.length;
      const totalLabel = totalTime !== undefined && totalTime !== null ? ` in ${totalTime}s total` : '';
      setTxStatus(`Batch submitted: ${count} transactions sent${totalLabel}. First hash: ${result.txHashes[0]}`, 'success');
    } else if (result.txHash) {
      const totalLabel = totalTime !== undefined && totalTime !== null ? ` in ${totalTime}s total` : '';
      setTxStatus(`Transaction submitted: ${result.txHash}${totalLabel}`, 'success');
    } else if (typeof result.message === 'string' && /already known|no new native/i.test(result.message)) {
      setTxStatus('Duplicate transaction detected by the node: no new transfer was broadcast.', 'idle');
    } else {
      setTxStatus(result.message || 'Agent request accepted by backend.', 'success');
    }
  } catch (error) {
    console.error(error);
    const message = error && typeof error === 'object' && 'message' in error && typeof error.message === 'string'
      ? error.message
      : JSON.stringify(error, null, 2);
    setJobStatus(`Failed: ${message}`, 'error');
    setTxStatus(message || 'Agent request failed.', 'error');
    agentResponseEl.textContent = JSON.stringify({ error: message }, null, 2);
  }
}

async function initializeWalletState() {
  if (!window.ethereum) {
    setWalletStatus('MetaMask missing', 'disconnected');
    return;
  }

  const accounts = await window.ethereum.request({ method: 'eth_accounts' }).catch(() => []);
  if (accounts && accounts.length) {
    state.account = accounts[0];
    const chainIdHex = await window.ethereum.request({ method: 'eth_chainId' }).catch(() => '0x1');
    const chainId = Number.parseInt(chainIdHex, 16);
    state.chainId = chainId;

    walletAddressEl.textContent = state.account;
    networkInfoEl.textContent = `Chain ID ${chainId}`;
    setWalletStatus('Connected', 'connected');
    const balance = await getWalletBalance(state.account);
    walletBalanceEl.textContent = balance;
    triggerBtn.disabled = false;
  }

  window.ethereum.on('accountsChanged', async (accounts) => {
    if (!accounts.length) {
      state.account = null;
      state.nonce = null;
      state.signature = null;
      state.isAuthenticated = false;
      walletAddressEl.textContent = '—';
      networkInfoEl.textContent = '—';
      walletBalanceEl.textContent = '—';
      setWalletStatus('Not connected', 'disconnected');
      setTxStatus('Wallet disconnected.', 'idle');
      triggerBtn.disabled = true;
      return;
    }

    state.account = accounts[0];
    walletAddressEl.textContent = state.account;
    const balance = await getWalletBalance(state.account);
    walletBalanceEl.textContent = balance;
    setWalletStatus('Connected', 'connected');
    setTxStatus('Wallet reconnected. Authenticate again if needed.', 'idle');
  });

  window.ethereum.on('chainChanged', async (chainIdHex) => {
    state.chainId = Number.parseInt(chainIdHex, 16);
    networkInfoEl.textContent = `Chain ID ${state.chainId}`;
    if (state.account) {
      const balance = await getWalletBalance(state.account);
      walletBalanceEl.textContent = balance;
    }
  });
}

const txCountEl = document.getElementById('txCount');
const txTargetEl = document.getElementById('txTarget');
const txBarFillEl = document.getElementById('txBarFill');
const txCountStatusEl = document.getElementById('txCountStatus');
const batchTotalEl = document.getElementById('batchTotal');
const batchSizeEl = document.getElementById('batchSize');
const runBatchBtn = document.getElementById('runBatchBtn');
const batchSummaryEl = document.getElementById('batchSummary');
const batchTableBodyEl = document.getElementById('batchTableBody');
const batchPaginationEl = document.getElementById('batchPagination');
const batchPageSizeEl = document.getElementById('batchPageSize');
const batchPrevBtn = document.getElementById('batchPrevBtn');
const batchNextBtn = document.getElementById('batchNextBtn');
const batchPageInfoEl = document.getElementById('batchPageInfo');
const feesNativeEl = document.getElementById('feesNative');
const feesUsdEl = document.getElementById('feesUsd');
const feesMetaEl = document.getElementById('feesMeta');

function formatNativeAmount(value, symbol) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  let text;
  if (n === 0) text = '0';
  else if (n < 1e-6) text = n.toExponential(2);
  else if (n < 1) text = n.toFixed(6);
  else text = n.toLocaleString(undefined, { maximumFractionDigits: 6 });
  return `${text} ${symbol || 'MTV'}`;
}

function formatUsd(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  if (n === 0) return '$0.00';
  if (Math.abs(n) < 0.01) return `$${n.toFixed(6)}`;
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD' });
}

function renderFees(fees) {
  if (!feesNativeEl || !feesUsdEl || !feesMetaEl) return;
  if (!fees || fees.totalFeeNative == null) {
    feesNativeEl.textContent = '—';
    feesUsdEl.textContent = '—';
    feesMetaEl.textContent = 'No fee data yet';
    return;
  }

  const symbol = fees.nativeSymbol || 'MTV';
  feesNativeEl.textContent = formatNativeAmount(fees.totalFeeNative, symbol);
  feesUsdEl.textContent = fees.priceUsd == null ? 'Price unavailable' : formatUsd(fees.totalFeeUsd);

  const parts = [];
  if (fees.txCount != null) parts.push(`${Number(fees.txCount).toLocaleString()} tx`);
  if (fees.gasPriceGwei != null) {
    parts.push(`${Number(fees.gasPriceGwei).toFixed(3)} gwei × ${Number(fees.gasPerTx || 21000).toLocaleString()} gas`);
  }
  if (fees.priceUsd != null) {
    parts.push(`1 ${symbol} = ${formatUsd(fees.priceUsd)}`);
    if (fees.priceSource) {
      parts.push(`via ${fees.priceSource}${fees.priceStale ? ' (cached)' : ''}`);
    }
  } else if (fees.priceError) {
    parts.push('CoinGecko price unavailable');
  }
  feesMetaEl.textContent = parts.join(' · ') || 'Price via CoinGecko';
}

function setElStatus(el, message, type = 'idle') {
  el.textContent = message;
  el.className = `status-box ${type}`;
}

function updateBatchSummary() {
  const total = Math.max(1, Number(batchTotalEl.value) || 0);
  const size = Math.max(1, Number(batchSizeEl.value) || 1);
  const n = Math.ceil(total / size);
  batchSummaryEl.textContent = `${n} batch${n === 1 ? '' : 'es'} of ${size} · real broadcast`;
}

const batchPager = { page: 0, size: 10, batches: [] };

function batchRowHtml(b) {
  const failed = Number(b.failed || 0);
  const feeNative = b.feeNative != null
    ? formatNativeAmount(b.feeNative, '').trim()
    : (b.feeMtv != null ? formatNativeAmount(b.feeMtv, '').trim() : '—');
  const feeUsd = b.feeUsd != null ? formatUsd(b.feeUsd) : '—';
  return `<tr class="${failed ? 'batch-row-failed' : ''}">`
    + `<td>#${b.batch}</td>`
    + `<td>${Number(b.requested || 0).toLocaleString()}</td>`
    + `<td>${Number(b.sent || 0).toLocaleString()}</td>`
    + `<td>${Number(b.skipped || 0).toLocaleString()}</td>`
    + `<td>${failed.toLocaleString()}</td>`
    + `<td>${b.timeSeconds != null ? b.timeSeconds : '—'}</td>`
    + `<td>${feeNative}</td>`
    + `<td>${feeUsd}</td>`
    + '</tr>';
}

function pageCount() {
  return Math.max(1, Math.ceil(batchPager.batches.length / batchPager.size));
}

function renderBatchPage() {
  const batches = batchPager.batches;
  if (!batches.length) {
    batchTableBodyEl.innerHTML = '<tr><td colspan="8" class="batch-empty">No batches yet</td></tr>';
    if (batchPaginationEl) batchPaginationEl.hidden = true;
    return;
  }

  const pages = pageCount();
  batchPager.page = Math.min(Math.max(0, batchPager.page), pages - 1);
  const start = batchPager.page * batchPager.size;
  const slice = batches.slice(start, start + batchPager.size);

  batchTableBodyEl.innerHTML = slice.map(batchRowHtml).join('');

  if (batchPaginationEl) {
    batchPaginationEl.hidden = false;
    const first = start + 1;
    const last = start + slice.length;
    batchPageInfoEl.textContent =
      `${first.toLocaleString()}–${last.toLocaleString()} of ${batches.length.toLocaleString()} · page ${batchPager.page + 1} of ${pages}`;
    batchPrevBtn.disabled = batchPager.page <= 0;
    batchNextBtn.disabled = batchPager.page >= pages - 1;
  }
}

function renderBatchTable(batches) {
  const list = Array.isArray(batches) ? batches : [];
  const prevLen = batchPager.batches.length;
  const wasOnLastPage = prevLen > 0 && batchPager.page >= pageCount() - 1;

  batchPager.batches = list;

  // While a run streams in new batches, keep following the tail only if the
  // user was already on the last page; otherwise hold their current page.
  if (wasOnLastPage && list.length > prevLen) {
    batchPager.page = pageCount() - 1;
  }
  renderBatchPage();
}

async function refreshTxCount() {
  try {
    const run = await apiFetch('/api/agent/last-run');
    const target = Number(run.target || 0);
    const submitted = Number(run.submitted || 0);
    const mined = run.minedCount != null ? Number(run.minedCount) : null;
    const failed = Number(run.failed || run.failedCount || 0);
    const skipped = Number(run.skipped || run.skippedCount || run.rejected || 0);

    // The headline is the number of transactions submitted to the node.
    txCountEl.textContent = submitted.toLocaleString();
    txTargetEl.textContent = target ? `/ ${target.toLocaleString()}` : '';
    txBarFillEl.style.width = target ? `${Math.min(100, (submitted / target) * 100)}%` : '0%';

    renderBatchTable(run.batches);
    renderFees(run.fees);

    const batchInfo = run.batchCount
      ? ` across ${run.batchCount} batches of ${run.batchSize}`
      : '';
    const dryLabel = run.dryRun ? ' [DRY RUN]' : '';

    let detail;
    let type = 'idle';
    if (run.status === 'none' || (!submitted && !target)) {
      detail = 'No batch run yet';
    } else if (run.status === 'running') {
      detail = `Submitting…${dryLabel} ${submitted}/${target}${batchInfo}`
        + ` · batch ${run.batchesDone || 0}/${run.batchCount || '?'}`;
    } else {
      detail = `${submitted.toLocaleString()} of ${target.toLocaleString()} submitted${batchInfo}${dryLabel}`;
      if (skipped) detail += ` · ${skipped} already known`;
      if (failed) detail += ` · ${failed} failed`;
      if (run.throughputTps) detail += ` · ${run.throughputTps} tx/s`;
      if (run.totalTimeSeconds != null) detail += ` · ${run.totalTimeSeconds}s`;
      if (mined != null) {
        detail += mined >= target
          ? ` · all ${target.toLocaleString()} mined on-chain`
          : ` · ${mined.toLocaleString()}/${target.toLocaleString()} mined, rest pending`;
      }
      if (failed) type = 'error';
      else if (submitted >= target) type = 'success';
    }
    setElStatus(txCountStatusEl, detail, type);
  } catch (error) {
    // leave the last known value in place
  }
}

async function runBatch() {
  const count = Math.max(1, Number(batchTotalEl.value) || 0);
  const batchSize = Math.max(1, Number(batchSizeEl.value) || 1);

  if (!window.confirm(
    `Broadcast ${count} REAL transactions in batches of ${batchSize}? This spends MTV and cannot be undone.`)) {
    return;
  }

  runBatchBtn.disabled = true;
  const original = runBatchBtn.textContent;
  runBatchBtn.textContent = 'Running…';
  try {
    const res = await apiFetch('/api/agent/batch-run', {
      method: 'POST',
      body: JSON.stringify({ count, batchSize, dryRun: false }),
    });
    setElStatus(txCountStatusEl,
      `Started: ${res.batches} batches of ${res.batchSize}…`, 'idle');
  } catch (error) {
    setElStatus(txCountStatusEl, error.message || 'Failed to start batch run', 'error');
  } finally {
    setTimeout(() => {
      runBatchBtn.disabled = false;
      runBatchBtn.textContent = original;
    }, 1500);
  }
}

// Wallet wiring first so a later failure in the optional batch panel can
// never leave Connect Wallet / Authenticate / Trigger unbound.
connectWalletBtn.addEventListener('click', connectWallet);
authenticateBtn.addEventListener('click', authenticateWallet);
triggerBtn.addEventListener('click', triggerAgent);
initializeWalletState();

try {
  if (batchTotalEl && batchSizeEl && runBatchBtn) {
    batchTotalEl.addEventListener('input', updateBatchSummary);
    batchSizeEl.addEventListener('input', updateBatchSummary);
    runBatchBtn.addEventListener('click', runBatch);
    updateBatchSummary();
  }
  if (batchPrevBtn && batchNextBtn && batchPageSizeEl) {
    batchPrevBtn.addEventListener('click', () => {
      batchPager.page -= 1;
      renderBatchPage();
    });
    batchNextBtn.addEventListener('click', () => {
      batchPager.page += 1;
      renderBatchPage();
    });
    batchPageSizeEl.addEventListener('change', () => {
      const size = Number(batchPageSizeEl.value) || 10;
      const firstRow = batchPager.page * batchPager.size;
      batchPager.size = size;
      batchPager.page = Math.floor(firstRow / size);
      renderBatchPage();
    });
  }
  if (txCountEl) {
    refreshTxCount();
    setInterval(refreshTxCount, 1000);
  }
} catch (error) {
  console.error('Batch panel init failed (wallet flow unaffected):', error);
}

/* ------------------------------------------------------------------ *
 * "Run Forever" background runner: SSE-driven, poll fallback.
 * The backend is a FastAPI asyncio task streaming state over
 * text/event-stream; this client keeps a last-known snapshot so a
 * dropped stream degrades gracefully and reconnects on its own.
 * ------------------------------------------------------------------ */
const foreverBtn = document.getElementById('foreverBtn');
const foreverIntervalEl = document.getElementById('foreverInterval');
const foreverCustomWrapEl = document.getElementById('foreverCustomWrap');
const foreverCustomEl = document.getElementById('foreverCustom');
const foreverDryRunEl = document.getElementById('foreverDryRun');
const foreverBadgeEl = document.getElementById('foreverBadge');
const foreverLineEl = document.getElementById('foreverLine');
const foreverMetricsEl = document.getElementById('foreverMetrics');
const foreverErrorEl = document.getElementById('foreverError');
const fmCyclesEl = document.getElementById('fmCycles');
const fmRateEl = document.getElementById('fmRate');
const fmTxEl = document.getElementById('fmTx');
const fmTxFailedEl = document.getElementById('fmTxFailed');
const fmStreakEl = document.getElementById('fmStreak');
const fmNextEl = document.getElementById('fmNext');
const foreverLogEl = document.getElementById('foreverLog');
const foreverLogBodyEl = document.getElementById('foreverLogBody');
const foreverLogScrollEl = document.getElementById('foreverLogScroll');
const foreverLogFailEl = document.getElementById('foreverLogFail');
const foreverAutoScrollEl = document.getElementById('foreverAutoScroll');
const flFeeEl = document.getElementById('flFee');
const flUsdEl = document.getElementById('flUsd');
const flRuntimeEl = document.getElementById('flRuntime');

const forever = {
  snapshot: null,
  streamConnected: false,
  source: null,
  reconnectAt: 0,
};

// Incremental cycle-log table state. Rows are keyed by cycle id and patched
// in place (never rebuilt wholesale) so rapid SSE updates don't thrash the DOM.
const foreverLog = {
  sessionKey: null,
  rows: new Map(),   // id -> <tr>
  stick: true,       // keep pinned to the newest row unless the user scrolls up
  rafId: 0,
  queued: null,
};

/* Format a duration by magnitude: <60s -> seconds, <60min -> "3.5 min",
 * otherwise "2.25 hr". */
function fmtDuration(ms) {
  if (ms == null || Number.isNaN(Number(ms))) return '—';
  const secs = Math.max(0, Number(ms) / 1000);
  if (secs < 60) return `${secs < 10 ? secs.toFixed(1) : Math.round(secs)}s`;
  if (secs < 3600) return `${(secs / 60).toFixed(1)} min`;
  return `${(secs / 3600).toFixed(2)} hr`;
}

const FOREVER_BADGE = {
  idle: ['Idle', 'idle'],
  stopped: ['Stopped', 'idle'],
  stopping: ['Stopping…', 'warn'],
  submitting: ['Submitting', 'run'],
  waiting: ['Waiting', 'run'],
  backoff: ['Retrying', 'warn'],
  circuit_open: ['Circuit open', 'error'],
};

function resolveForeverInterval() {
  if (foreverIntervalEl.value === 'custom') {
    return Math.max(1, Math.min(3600, Number(foreverCustomEl.value) || 15));
  }
  return Number(foreverIntervalEl.value) || 10;
}

function syncForeverCustomVisibility() {
  foreverCustomWrapEl.hidden = foreverIntervalEl.value !== 'custom';
}

function foreverCountdown(nextRunAtIso) {
  if (!nextRunAtIso) return null;
  const ms = new Date(nextRunAtIso).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  return Math.max(0, Math.round(ms / 1000));
}

function renderForever() {
  const s = forever.snapshot;
  if (!s) return;

  const [label, cls] = FOREVER_BADGE[s.phase] || ['Idle', 'idle'];
  foreverBadgeEl.textContent = forever.streamConnected || !s.active
    ? label
    : `${label} · reconnecting`;
  foreverBadgeEl.className = `forever-badge ${cls}`;

  let line = s.message || '—';
  const secs = foreverCountdown(s.nextRunAt);
  if (secs != null && (s.phase === 'waiting' || s.phase === 'backoff' || s.phase === 'circuit_open')) {
    line += ` (next in ${secs}s)`;
  }
  foreverLineEl.textContent = line;

  const started = s.cycles || 0;
  foreverMetricsEl.hidden = started === 0 && !s.active;
  fmCyclesEl.textContent = started.toLocaleString();
  fmRateEl.textContent = started
    ? `${Math.round((s.cyclesSucceeded / started) * 100)}%  (${s.cyclesSucceeded}/${started})`
    : '—';
  fmTxEl.textContent = Number(s.txSubmitted || 0).toLocaleString();
  fmTxFailedEl.textContent = Number(s.txFailed || 0).toLocaleString();
  fmStreakEl.textContent = Number(s.consecutiveFailures || 0).toLocaleString();
  fmNextEl.textContent = secs != null ? `${secs}s` : (s.active ? '—' : 'stopped');

  if (s.lastError) {
    foreverErrorEl.hidden = false;
    foreverErrorEl.textContent = `Last error: ${s.lastError}`;
  } else {
    foreverErrorEl.hidden = true;
  }

  foreverBtn.textContent = s.active ? 'Stop' : 'Run Forever';
  foreverBtn.classList.toggle('is-stop', !!s.active);
}

/* ---- incremental cycle-log data table ---- */

const LOG_STATUS_LABEL = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
};

function logCells(entry, priceUsd) {
  const accumMtv = Number(entry.cumulativeFeeMtv || 0);
  const usd = priceUsd != null ? accumMtv * priceUsd : null;
  const elapsedMs = entry.status === 'running' || entry.status === 'pending'
    ? liveRuntimeMs()
    : entry.runtimeMs;
  return {
    status: `<span class="log-badge ${entry.status}">${LOG_STATUS_LABEL[entry.status] || entry.status}</span>`,
    batch: entry.id === 'pending' ? `#${entry.cycle}` : `#${entry.cycle}`,
    cycles: entry.subBatches != null ? Number(entry.subBatches).toLocaleString() : '…',
    fee: accumMtv.toFixed(6),
    usd: usd != null ? formatUsd(usd) : '—',
    elapsed: entry.id === 'pending'
      ? (entry.pendingLabel || '…')
      : fmtDuration(elapsedMs),
    error: entry.error || '',
  };
}

function makeLogRow(entry, priceUsd) {
  const tr = document.createElement('tr');
  tr.dataset.id = entry.id;
  const c = logCells(entry, priceUsd);
  tr.className = `log-${entry.status}`;
  tr.innerHTML =
    `<td>${c.status}</td>`
    + `<td>${c.batch}</td>`
    + `<td>${c.cycles}</td>`
    + `<td>${c.fee}</td>`
    + `<td>${c.usd}</td>`
    + `<td>${c.elapsed}${c.error ? `<span class="log-err-cell">${escapeHtml(c.error)}</span>` : ''}</td>`;
  return tr;
}

function patchLogRow(tr, entry, priceUsd) {
  const c = logCells(entry, priceUsd);
  if (tr.className !== `log-${entry.status}`) tr.className = `log-${entry.status}`;
  const tds = tr.children;
  if (tds[0].innerHTML !== c.status) tds[0].innerHTML = c.status;
  if (tds[2].textContent !== c.cycles) tds[2].textContent = c.cycles;
  if (tds[3].textContent !== c.fee) tds[3].textContent = c.fee;
  if (tds[4].textContent !== c.usd) tds[4].textContent = c.usd;
  const last = c.elapsed + (c.error ? `<span class="log-err-cell">${escapeHtml(c.error)}</span>` : '');
  if (tds[5].innerHTML !== last) tds[5].innerHTML = last;
}

function liveRuntimeMs() {
  const s = forever.snapshot;
  if (!s || !s.startedAt) return s ? s.runtimeMs : 0;
  if (!s.active) return s.runtimeMs;
  return Date.now() - new Date(s.startedAt).getTime();
}

function renderForeverLogNow(s) {
  if (!s) return;
  const log = Array.isArray(s.cycleLog) ? s.cycleLog : [];

  const show = log.length > 0 || s.active;
  foreverLogEl.hidden = !show;
  if (!show) {
    foreverLogBodyEl.innerHTML = '';
    foreverLog.rows.clear();
    foreverLog.sessionKey = null;
    return;
  }

  // New runner session -> drop the previous session's rows.
  const key = s.startedAt || 'none';
  if (key !== foreverLog.sessionKey) {
    foreverLog.sessionKey = key;
    foreverLog.rows.clear();
    foreverLogBodyEl.innerHTML = '';
    foreverLog.stick = true;
  }

  const priceUsd = s.priceUsd;
  const seen = new Set();

  // Real cycle rows, in chronological order.
  for (const entry of log) {
    seen.add(entry.id);
    const existing = foreverLog.rows.get(entry.id);
    if (existing) {
      patchLogRow(existing, entry, priceUsd);
    } else {
      const tr = makeLogRow(entry, priceUsd);
      foreverLogBodyEl.appendChild(tr);
      foreverLog.rows.set(entry.id, tr);
    }
  }

  // Optional trailing "pending" pseudo-row for the next cycle while waiting.
  const pendingId = 'pending';
  if (s.active && (s.phase === 'waiting' || s.phase === 'circuit_open')) {
    const secs = foreverCountdown(s.nextRunAt);
    const pendingEntry = {
      id: pendingId,
      cycle: (s.cycles || 0) + 1,
      status: 'pending',
      subBatches: null,
      cumulativeFeeMtv: s.cumulativeFeeMtv || 0,
      pendingLabel: secs != null ? `next in ${secs}s` : 'queued',
    };
    seen.add(pendingId);
    const existing = foreverLog.rows.get(pendingId);
    if (existing) {
      patchLogRow(existing, pendingEntry, priceUsd);
    } else {
      const tr = makeLogRow(pendingEntry, priceUsd);
      foreverLogBodyEl.appendChild(tr);
      foreverLog.rows.set(pendingId, tr);
    }
  }

  // Remove rows that no longer belong (pending row once its cycle starts,
  // or old rows trimmed from the bounded server-side log).
  for (const [id, tr] of foreverLog.rows) {
    if (!seen.has(id)) {
      tr.remove();
      foreverLog.rows.delete(id);
    }
  }

  // Footer: cumulative totals with unit-scaled runtime.
  const cumMtv = Number(s.cumulativeFeeMtv || 0);
  flFeeEl.textContent = `${cumMtv.toFixed(6)} MTV`;
  flUsdEl.textContent = priceUsd != null
    ? formatUsd(cumMtv * priceUsd) + (s.priceStale ? ' (cached rate)' : '')
    : 'rate unavailable';
  flRuntimeEl.textContent = fmtDuration(liveRuntimeMs());

  // Failed cycles are surfaced separately without interrupting the listing.
  const failures = log.filter((e) => e.status === 'failed');
  if (failures.length) {
    const lastErr = failures[failures.length - 1].error || 'unknown error';
    foreverLogFailEl.hidden = false;
    foreverLogFailEl.textContent =
      `${failures.length} failed cycle${failures.length === 1 ? '' : 's'} · last: ${lastErr}`;
  } else {
    foreverLogFailEl.hidden = true;
  }

  if (foreverAutoScrollEl.checked && foreverLog.stick) {
    foreverLogScrollEl.scrollTop = foreverLogScrollEl.scrollHeight;
  }
}

/* Coalesce bursts of snapshots into one paint per animation frame so a
 * fast interval (e.g. every 3s with many events) can't freeze the UI. */
function scheduleForeverLogRender(s) {
  foreverLog.queued = s;
  if (foreverLog.rafId) return;
  foreverLog.rafId = requestAnimationFrame(() => {
    foreverLog.rafId = 0;
    const snap = foreverLog.queued;
    foreverLog.queued = null;
    try {
      renderForeverLogNow(snap);
    } catch (err) {
      console.error('cycle-log render failed', err);
    }
  });
}

/* Cheap per-second refresh of just the live-changing cells. */
function tickForeverLog() {
  const s = forever.snapshot;
  if (!s || foreverLogEl.hidden) return;
  const elapsed = fmtDuration(liveRuntimeMs());
  flRuntimeEl.textContent = elapsed;
  for (const [id, tr] of foreverLog.rows) {
    if (id === 'pending') {
      const secs = foreverCountdown(s.nextRunAt);
      tr.children[5].textContent = secs != null ? `next in ${secs}s` : 'queued';
    } else if (tr.className === 'log-running') {
      tr.children[5].textContent = elapsed;
    }
  }
}

function applyForeverSnapshot(s) {
  if (!s || typeof s !== 'object') return;
  forever.snapshot = s;
  // Keep the interval picker in sync when the server clamps/normalises it.
  if (s.config && document.activeElement !== foreverIntervalEl
      && document.activeElement !== foreverCustomEl) {
    const iv = Number(s.config.intervalSeconds);
    const preset = ['5', '10', '30', '60'];
    if (preset.includes(String(iv))) {
      foreverIntervalEl.value = String(iv);
    } else {
      foreverIntervalEl.value = 'custom';
      foreverCustomEl.value = iv;
    }
    syncForeverCustomVisibility();
  }
  renderForever();
  scheduleForeverLogRender(s);
}

async function startForever() {
  const intervalSeconds = resolveForeverInterval();
  const dryRun = foreverDryRunEl.checked;
  const count = Math.max(1, Number(batchTotalEl.value) || 1000);
  const batchSize = Math.max(1, Number(batchSizeEl.value) || 100);

  if (!dryRun && !window.confirm(
    `Run Forever will broadcast ${count} REAL transactions every ${intervalSeconds}s `
    + 'until you press Stop. This spends MTV continuously. Continue?')) {
    return;
  }

  foreverBtn.disabled = true;
  try {
    const s = await apiFetch('/api/forever/start', {
      method: 'POST',
      body: JSON.stringify({ intervalSeconds, count, batchSize, dryRun }),
    });
    applyForeverSnapshot(s);
  } catch (error) {
    foreverErrorEl.hidden = false;
    foreverErrorEl.textContent = error.message || 'Failed to start Run Forever';
  } finally {
    foreverBtn.disabled = false;
  }
}

async function stopForever() {
  foreverBtn.disabled = true;
  foreverBadgeEl.textContent = 'Stopping…';
  foreverBadgeEl.className = 'forever-badge warn';
  try {
    const s = await apiFetch('/api/forever/stop', { method: 'POST' });
    applyForeverSnapshot(s);
  } catch (error) {
    foreverErrorEl.hidden = false;
    foreverErrorEl.textContent = error.message || 'Failed to stop Run Forever';
  } finally {
    foreverBtn.disabled = false;
  }
}

async function pushForeverConfig() {
  if (!forever.snapshot || !forever.snapshot.active) return;
  try {
    const s = await apiFetch('/api/forever/config', {
      method: 'POST',
      body: JSON.stringify({
        intervalSeconds: resolveForeverInterval(),
        dryRun: foreverDryRunEl.checked,
      }),
    });
    applyForeverSnapshot(s);
  } catch (error) {
    console.error('Failed to update Run Forever config', error);
  }
}

function connectForeverStream() {
  if (typeof EventSource === 'undefined') return;
  try {
    if (forever.source) forever.source.close();
    const source = new EventSource('/api/forever/stream');
    forever.source = source;

    source.addEventListener('forever', (event) => {
      forever.streamConnected = true;
      try {
        applyForeverSnapshot(JSON.parse(event.data));
      } catch (parseErr) {
        console.error('Bad forever event', parseErr);
      }
    });

    source.onopen = () => {
      forever.streamConnected = true;
      renderForever();
    };

    source.onerror = () => {
      // EventSource retries on its own; reflect the gap in the UI and let
      // the status poll keep numbers fresh until the stream is back.
      forever.streamConnected = false;
      renderForever();
    };
  } catch (error) {
    console.error('Run Forever stream unavailable, using poll fallback', error);
  }
}

async function pollForeverStatus() {
  if (forever.streamConnected) return; // stream is authoritative when up
  try {
    const s = await apiFetch('/api/forever/status');
    applyForeverSnapshot(s);
  } catch (error) {
    /* keep last-known snapshot */
  }
}

try {
  if (foreverBtn) {
    syncForeverCustomVisibility();
    foreverIntervalEl.addEventListener('change', () => {
      syncForeverCustomVisibility();
      pushForeverConfig();
    });
    foreverCustomEl.addEventListener('change', pushForeverConfig);
    foreverDryRunEl.addEventListener('change', pushForeverConfig);
    foreverBtn.addEventListener('click', () => {
      if (forever.snapshot && forever.snapshot.active) stopForever();
      else startForever();
    });

    // Track whether the user is pinned to the newest row; if they scroll up
    // we stop auto-scrolling until they return to the bottom.
    foreverLogScrollEl.addEventListener('scroll', () => {
      const gap = foreverLogScrollEl.scrollHeight
        - foreverLogScrollEl.scrollTop - foreverLogScrollEl.clientHeight;
      foreverLog.stick = gap < 24;
    });
    foreverAutoScrollEl.addEventListener('change', () => {
      if (foreverAutoScrollEl.checked) {
        foreverLog.stick = true;
        foreverLogScrollEl.scrollTop = foreverLogScrollEl.scrollHeight;
      }
    });

    // Coming back to the tab: pull fresh state and repaint from the full
    // snapshot so the log catches up on whatever ran while we were away.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        pollForeverStatus();
        if (forever.snapshot) scheduleForeverLogRender(forever.snapshot);
        if (!forever.streamConnected) connectForeverStream();
      }
    });

    connectForeverStream();
    pollForeverStatus();
    setInterval(pollForeverStatus, 3000);
    // Re-tick the countdown + live durations once a second off the last snapshot.
    setInterval(() => {
      if (!forever.snapshot) return;
      renderForever();
      tickForeverLog();
    }, 1000);
    // Reopen a stream that has been down for a while (belt and braces).
    setInterval(() => {
      if (!forever.streamConnected) connectForeverStream();
    }, 15000);
  }
} catch (error) {
  console.error('Run Forever panel init failed:', error);
}
