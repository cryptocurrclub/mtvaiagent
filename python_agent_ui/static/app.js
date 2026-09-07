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
