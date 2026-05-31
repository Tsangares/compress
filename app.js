// FFmpeg loaded via UMD <script> tags — globals available
const { FFmpeg } = FFmpegWASM;
const { fetchFile, toBlobURL } = FFmpegUtil;

// ============================================
// State
// ============================================
const state = {
    file: null,
    duration: 0,
    width: 0,
    height: 0,
    quality: 'medium',
    ffmpeg: null,
    ffmpegLoaded: false,
    ffmpegLoading: false,
    outputBlob: null,
    currentScreen: 0,
    fileWritten: false,
    inputName: null,
    wakeLock: null,
    compressing: false,
};

const QUALITY_PRESETS = {
    high: {
        desc: 'Minimal compression. Keeps full resolution and detail.',
        crf: 23,
        preset: 'ultrafast',
        audioBitrate: '128k',
        scale: null,
    },
    medium: {
        desc: 'Strong compression at full resolution. Good for sharing.',
        crf: 30,
        preset: 'ultrafast',
        audioBitrate: '96k',
        scale: null,
    },
    low: {
        desc: 'Maximum compression at 480p. Best for messaging apps.',
        crf: 34,
        preset: 'ultrafast',
        audioBitrate: '64k',
        scale: 480,
    },
    target: {
        desc: 'Fits under 10 MB. Adjusts quality and resolution automatically.',
        targetMB: 10,
        preset: 'ultrafast',
        audioBitrate: '64k',
    },
};

// ============================================
// DOM
// ============================================
const $ = (sel) => document.querySelector(sel);
const screens = {
    select: $('#screen-select'),
    options: $('#screen-options'),
    progress: $('#screen-progress'),
    done: $('#screen-done'),
    about: $('#screen-about'),
    edit: $('#screen-edit'),
};

const dom = {
    fileInput: $('#fileInput'),
    dropZone: $('#dropZone'),
    preview: $('#preview'),
    infoSize: $('#infoSize'),
    infoDuration: $('#infoDuration'),
    infoRes: $('#infoRes'),
    qualityControl: $('#qualityControl'),
    qualityDesc: $('#qualityDesc'),
    compressBtn: $('#compressBtn'),
    editBtn: $('#editBtn'),
    progressRing: $('#progressRing'),
    progressPercent: $('#progressPercent'),
    progressStatus: $('#progressStatus'),
    cancelBtn: $('#cancelBtn'),
    beforeSize: $('#beforeSize'),
    afterSize: $('#afterSize'),
    savingsPercent: $('#savingsPercent'),
    saveBtn: $('#saveBtn'),
    shareBtn: $('#shareBtn'),
    shareLinkBtn: $('#shareLinkBtn'),
    shareLinkLabel: $('#shareLinkLabel'),
    shareHint: $('#shareHint'),
    anotherBtn: $('#anotherBtn'),
    engineStatus: $('#engineStatus'),
    engineFill: $('#engineFill'),
    engineLabel: $('#engineStatus .engine-label'),
    aboutBtn: $('#aboutBtn'),
    estQuick: $('#estQuick'),
    estAdvanced: $('#estAdvanced'),
    estTestedRow: $('#estTestedRow'),
    estTesting: $('#estTesting'),
    testEstimate: $('#testEstimate'),
    resumeCard: $('#resumeCard'),
    resumeName: $('#resumeName'),
    resumeMeta: $('#resumeMeta'),
    resumeBtn: $('#resumeBtn'),
    // Edit screen
    editPreview: $('#editPreview'),
    editCurrentTime: $('#editCurrentTime'),
    editTotalTime: $('#editTotalTime'),
    editPlayBtn: $('#editPlayBtn'),
    editPlayIcon: $('#editPlayIcon'),
    editPauseIcon: $('#editPauseIcon'),
    editSplitBtn: $('#editSplitBtn'),
    editDeleteBtn: $('#editDeleteBtn'),
    editUndoBtn: $('#editUndoBtn'),
    editExportBtn: $('#editExportBtn'),
    editBack: $('#editBack'),
    timelineScroll: $('#timelineScroll'),
    timelineTrack: $('#timelineTrack'),
    timelineThumbs: $('#timelineThumbs'),
    timelineSegments: $('#timelineSegments'),
    timelinePlayhead: $('#timelinePlayhead'),
    timelineRuler: $('#timelineRuler'),
    zoomInBtn: $('#zoomInBtn'),
    zoomOutBtn: $('#zoomOutBtn'),
    zoomFill: $('#zoomFill'),
    zoomLabel: $('#zoomLabel'),
    segmentList: $('#segmentList'),
};

// ============================================
// Screen Navigation
// ============================================
function goToScreen(index, pushHistory = true) {
    const list = [screens.select, screens.options, screens.progress, screens.done, screens.about, screens.edit];
    state.currentScreen = index;

    list.forEach((s, i) => {
        s.classList.remove('active', 'exit-left');
        if (i === index) s.classList.add('active');
        else if (i < index) s.classList.add('exit-left');
    });

    // Push browser history so system back gesture works
    if (pushHistory && index > 0) {
        history.pushState({ screen: index }, '');
    }

    // Show resume card on home screen if a file is loaded
    updateResumeCard(index);

    if (navigator.vibrate) navigator.vibrate(10);
}

function updateResumeCard(screenIndex) {
    if (screenIndex === 0 && state.file) {
        dom.resumeName.textContent = state.file.name;
        dom.resumeMeta.textContent = `${formatBytes(state.file.size)} · ${state.width}x${state.height}`;
        dom.resumeCard.classList.remove('hidden');
    } else {
        dom.resumeCard.classList.add('hidden');
    }
}

// System back button / swipe-back gesture
window.addEventListener('popstate', (e) => {
    if (state.currentScreen > 0) {
        if (state.currentScreen === 4) {
            goToScreen(0, false); // About → home
        } else if (state.currentScreen === 3) {
            goToScreen(0, false); // Done → home
        } else if (state.currentScreen === 5) {
            goToScreen(1, false); // Edit → options
        } else {
            goToScreen(state.currentScreen - 1, false);
        }
    }
});

// ============================================
// File Handling
// ============================================
function handleFile(file) {
    if (!file || !file.type.startsWith('video/')) return;
    state.file = file;
    state.fileWritten = false;

    const url = URL.createObjectURL(file);
    dom.preview.src = url;
    dom.preview.play().catch(() => {});

    dom.preview.onloadedmetadata = () => {
        state.duration = dom.preview.duration;
        state.width = dom.preview.videoWidth;
        state.height = dom.preview.videoHeight;

        dom.infoSize.textContent = formatBytes(file.size);
        dom.infoDuration.textContent = formatDuration(state.duration);
        dom.infoRes.textContent = `${state.width}x${state.height}`;

        // Reset estimation
        dom.estTestedRow.classList.add('hidden');
        dom.estTesting.classList.add('hidden');
        updateQuickEstimate();

        goToScreen(1);
    };
}

// Drag & drop
dom.dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dom.dropZone.classList.add('drag-over');
});
dom.dropZone.addEventListener('dragleave', () => {
    dom.dropZone.classList.remove('drag-over');
});
dom.dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dom.dropZone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
});

dom.fileInput.addEventListener('change', (e) => {
    if (e.target.files[0]) handleFile(e.target.files[0]);
});

// ============================================
// URL Download
// ============================================
const DL_API = '/api';

const urlDom = {
    input: $('#urlInput'),
    goBtn: $('#urlGoBtn'),
    status: $('#urlStatus'),
    statusText: $('#urlStatusText'),
    actions: $('#urlActions'),
    saveBtn: $('#urlSaveBtn'),
    shareBtn: $('#urlShareBtn'),
    shareLabel: $('#urlShareLabel'),
    shareHint: $('#urlShareHint'),
    audioBtn: $('#urlAudioBtn'),
    compressBtn: $('#urlCompressBtn'),
    editBtn: $('#urlEditBtn'),
};

let urlDownloadedFile = null;
let urlDownloadInfo = null;  // { id, filename } from /api/download

function setHomeCompact(on) {
    document.getElementById('screen-select')?.classList.toggle('url-active', !!on);
}

urlDom.goBtn.addEventListener('click', startUrlDownload);
urlDom.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startUrlDownload();
});

// User edits the URL after a download — reset to expanded picker view.
urlDom.input.addEventListener('input', () => {
    if (urlDom.actions.classList.contains('hidden')) return;
    urlDom.actions.classList.add('hidden');
    urlDom.status.classList.add('hidden');
    urlDownloadedFile = null;
    urlDownloadInfo = null;
    setHomeCompact(false);
});

async function startUrlDownload() {
    const url = urlDom.input.value.trim();
    if (!url) return;

    urlDom.goBtn.disabled = true;
    urlDom.input.disabled = true;
    urlDom.actions.classList.add('hidden');
    urlDom.status.classList.remove('hidden', 'done', 'error');
    urlDom.statusText.textContent = 'Fetching video info...';

    try {
        // Step 1: Get info
        const infoRes = await fetch(`${DL_API}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });

        if (!infoRes.ok) {
            const err = await infoRes.json().catch(() => ({}));
            throw new Error(err.detail || 'Could not fetch video info');
        }

        const info = await infoRes.json();
        const sizeHint = info.filesize_approx ? ` (~${formatBytes(info.filesize_approx)})` : '';
        urlDom.statusText.textContent = `Downloading "${info.title}"${sizeHint}...`;

        // Step 2: Download
        const dlRes = await fetch(`${DL_API}/download`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });

        if (!dlRes.ok) {
            const err = await dlRes.json().catch(() => ({}));
            throw new Error(err.detail || 'Download failed');
        }

        const dlData = await dlRes.json();
        urlDownloadInfo = { id: dlData.id, filename: dlData.filename, size: dlData.size };
        urlDom.statusText.textContent = `Loading ${formatBytes(dlData.size)}...`;

        // Step 3: Fetch the file to browser
        const fileRes = await fetch(`${DL_API}/file/${dlData.id}/${encodeURIComponent(dlData.filename)}`);
        if (!fileRes.ok) throw new Error('Could not load file');

        const blob = await fileRes.blob();
        urlDownloadedFile = new File([blob], dlData.filename, { type: 'video/mp4' });

        urlDom.status.classList.add('done');
        urlDom.statusText.textContent = `Ready — ${dlData.filename} (${formatBytes(dlData.size)})`;
        urlDom.actions.classList.remove('hidden');
        setHomeCompact(true);
    } catch (err) {
        urlDom.status.classList.add('error');
        urlDom.statusText.textContent = err.message;
    }

    urlDom.goBtn.disabled = false;
    urlDom.input.disabled = false;
}

// URL action buttons
urlDom.saveBtn.addEventListener('click', () => {
    if (!urlDownloadedFile) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(urlDownloadedFile);
    a.download = urlDownloadedFile.name;
    a.click();
    URL.revokeObjectURL(a.href);
    if (navigator.vibrate) navigator.vibrate(10);
});

urlDom.shareBtn.addEventListener('click', async () => {
    if (!urlDownloadInfo || urlDom.shareBtn.disabled) return;
    const origLabel = urlDom.shareLabel.textContent;
    urlDom.shareBtn.disabled = true;
    urlDom.shareLabel.textContent = 'Uploading…';
    try {
        const resp = await fetch(`${DL_API}/share/promote`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: urlDownloadInfo.id, filename: urlDownloadInfo.filename }),
        });
        if (!resp.ok) {
            let msg = `Share failed (${resp.status})`;
            try {
                const err = await resp.json();
                if (err?.detail) msg = err.detail;
            } catch (_) {}
            throw new Error(msg);
        }
        const data = await resp.json();
        const fullUrl = new URL(data.share_url || data.url, window.location.origin).toString();

        if (navigator.share && /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)) {
            try {
                await navigator.share({ url: fullUrl });
                urlDom.shareLabel.textContent = 'Shared!';
            } catch (err) {
                if (err.name !== 'AbortError') {
                    await navigator.clipboard.writeText(fullUrl);
                    urlDom.shareLabel.textContent = 'Link copied';
                } else {
                    urlDom.shareLabel.textContent = origLabel;
                }
            }
        } else {
            await navigator.clipboard.writeText(fullUrl);
            urlDom.shareLabel.textContent = 'Link copied';
        }

        if (navigator.vibrate) navigator.vibrate(10);
        urlDom.shareHint.textContent = `Link expires in 7 days • ${fullUrl}`;
    } catch (err) {
        urlDom.shareLabel.textContent = 'Failed';
        urlDom.shareHint.textContent = err.message || 'Could not create share link.';
    } finally {
        setTimeout(() => {
            urlDom.shareBtn.disabled = false;
            urlDom.shareLabel.textContent = origLabel;
        }, 4000);
    }
});

urlDom.audioBtn.addEventListener('click', async () => {
    const url = urlDom.input.value.trim();
    if (!url) return;

    urlDom.audioBtn.disabled = true;
    urlDom.status.classList.remove('hidden', 'done', 'error');
    urlDom.statusText.textContent = 'Extracting audio...';

    try {
        const res = await fetch(`${DL_API}/download-audio`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Audio download failed');
        }

        const data = await res.json();
        const fileRes = await fetch(`${DL_API}/file/${data.id}/${encodeURIComponent(data.filename)}`);
        if (!fileRes.ok) throw new Error('Could not load audio file');

        const blob = await fileRes.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = data.filename;
        a.click();
        URL.revokeObjectURL(a.href);

        urlDom.status.classList.add('done');
        urlDom.statusText.textContent = `Saved ${data.filename} (${formatBytes(data.size)})`;
        if (navigator.vibrate) navigator.vibrate(10);
    } catch (err) {
        urlDom.status.classList.add('error');
        urlDom.statusText.textContent = err.message;
    }

    urlDom.audioBtn.disabled = false;
});

urlDom.compressBtn.addEventListener('click', () => {
    if (!urlDownloadedFile) return;
    urlDom.actions.classList.add('hidden');
    setHomeCompact(false);
    handleFile(urlDownloadedFile);
});

urlDom.editBtn.addEventListener('click', () => {
    if (!urlDownloadedFile) return;
    urlDom.actions.classList.add('hidden');
    setHomeCompact(false);
    // Load file metadata then go to edit
    state.file = urlDownloadedFile;
    state.fileWritten = false;
    const url = URL.createObjectURL(urlDownloadedFile);
    dom.preview.src = url;
    dom.preview.onloadedmetadata = () => {
        state.duration = dom.preview.duration;
        state.width = dom.preview.videoWidth;
        state.height = dom.preview.videoHeight;
        enterEditMode();
    };
});

// ============================================
// Inbound share handoff (from /v/{id} viewer page)
// Handles ?share=ID&action=trim|compress
// ============================================
async function consumeShareParam() {
    const params = new URLSearchParams(window.location.search);
    const id = params.get('share');
    const action = params.get('action') || 'compress';
    if (!id || !/^[a-z0-9]{4,16}$/.test(id)) return;

    // Strip the params from the URL bar so refreshes don't re-fire
    history.replaceState({}, '', window.location.pathname);

    urlDom.status.classList.remove('hidden', 'done', 'error');
    urlDom.statusText.textContent = 'Loading shared video…';

    try {
        const metaResp = await fetch(`${DL_API}/share/${id}`);
        if (!metaResp.ok) {
            const err = await metaResp.json().catch(() => ({}));
            throw new Error(err.detail || `Share unavailable (${metaResp.status})`);
        }
        const meta = await metaResp.json();
        const fileResp = await fetch(meta.url);
        if (!fileResp.ok) throw new Error('Could not load shared file');
        const blob = await fileResp.blob();
        const file = new File([blob], meta.filename, { type: meta.mime || 'video/mp4' });

        urlDownloadedFile = file;
        urlDownloadInfo = { id: meta.id, filename: meta.filename, size: meta.size };

        urlDom.status.classList.add('hidden');

        if (action === 'trim') {
            urlDom.editBtn.click();
        } else {
            // default → compress
            handleFile(file);
        }
    } catch (e) {
        urlDom.status.classList.add('error');
        urlDom.statusText.textContent = e.message || 'Could not load shared video';
    }
}

consumeShareParam();

// ============================================
// Quality Selector
// ============================================
const pills = dom.qualityControl.querySelectorAll('.pill');

pills.forEach((btn) => {
    btn.addEventListener('click', () => {
        pills.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.quality = btn.dataset.quality;

        const preset = QUALITY_PRESETS[state.quality];
        dom.qualityDesc.textContent = preset.desc;

        // Reset tested estimate when quality changes
        dom.estTestedRow.classList.add('hidden');
        dom.estTesting.classList.add('hidden');
        updateQuickEstimate();

        if (navigator.vibrate) navigator.vibrate(5);
    });
});

// ============================================
// Estimation — Quick
// ============================================
function updateQuickEstimate() {
    if (!state.file) return;

    const est = quickEstimate(state.file.size, state.duration, state.height, state.quality);
    dom.estQuick.textContent = `~${formatBytes(est)}`;
}

function quickEstimate(fileSize, duration, height, quality) {
    if (quality === 'target') {
        return 10 * 1024 * 1024;
    }

    const preset = QUALITY_PRESETS[quality];
    // Use output resolution after scaling, not input
    const outHeight = (preset.scale && height > preset.scale) ? preset.scale : height;
    const h = Math.max(outHeight, 480);

    // Typical output bitrates for ultrafast at various CRFs and resolutions
    const typicalKbps = {
        high:   h > 1080 ? 12000 : h > 720 ? 5000 : h > 480 ? 2500 : 1200,
        medium: h > 1080 ? 4000  : h > 720 ? 1800 : h > 480 ? 800  : 400,
        low:    h > 1080 ? 1500  : h > 720 ? 600  : h > 480 ? 300  : 150,
    };

    const inputKbps = (fileSize * 8) / duration / 1000;
    const audioKbps = parseInt(preset.audioBitrate) || 64;

    // Output can't exceed input
    const videoKbps = Math.min(typicalKbps[quality], inputKbps * 0.9);
    const totalKbps = videoKbps + audioKbps;

    return (totalKbps * 1000 / 8) * duration;
}

// ============================================
// Estimation — Advanced (sample test)
// ============================================
dom.testEstimate.addEventListener('click', runAdvancedEstimate);

async function runAdvancedEstimate() {
    if (!state.file) return;
    if (!state.ffmpegLoaded) {
        dom.testEstimate.disabled = true;
        dom.testEstimate.textContent = 'Loading engine...';
        await loadFFmpeg();
        dom.testEstimate.disabled = false;
        dom.testEstimate.innerHTML = '<svg viewBox="0 0 20 20" fill="none"><path d="M10 3v14M3 10h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg> Test with sample';
        if (!state.ffmpegLoaded) return;
    }

    // Lock UI
    dom.testEstimate.disabled = true;
    dom.compressBtn.disabled = true;
    pills.forEach(p => p.disabled = true);
    dom.estTesting.classList.remove('hidden');
    dom.estTestedRow.classList.add('hidden');

    // Reset all steps
    resetSteps();

    const ffmpeg = state.ffmpeg;
    const preset = QUALITY_PRESETS[state.quality];
    const inputName = 'input' + getExtension(state.file.name);
    const sampleRaw = 'sample_raw.mp4';
    const sampleOut = 'sample_out.mp4';

    try {
        // Step 1: Load video
        stepActive('load', `${formatBytes(state.file.size)} video`);
        if (!state.fileWritten) {
            await ffmpeg.writeFile(inputName, await fetchFile(state.file));
            state.fileWritten = true;
            state.inputName = inputName;
        }
        stepDone('load');

        // Step 2: Extract sample (1s clip for speed)
        const sampleDuration = Math.min(1, state.duration * 0.5);
        const seekPoint = Math.max(0, (state.duration / 2) - (sampleDuration / 2));

        stepActive('extract', `${sampleDuration.toFixed(1)}s from middle`);
        await ffmpeg.exec([
            '-ss', String(seekPoint),
            '-i', inputName,
            '-t', String(sampleDuration),
            '-c', 'copy',
            '-y', sampleRaw,
        ]);

        const rawData = await ffmpeg.readFile(sampleRaw);
        const rawSize = rawData.length;
        stepDone('extract', formatBytes(rawSize));

        // Step 3: Encode sample (use veryfast preset for speed)
        stepActive('encode', 'Starting...');
        const encodeStart = Date.now();

        const progressHandler = ({ progress }) => {
            const pct = Math.min(Math.round(progress * 100), 99);
            const elapsed = (Date.now() - encodeStart) / 1000;
            let detail = `${pct}%`;
            if (pct > 5) {
                const eta = Math.round((elapsed / pct) * (100 - pct));
                detail += ` — ~${eta}s left`;
            }
            stepDetail('encode', detail);
        };
        ffmpeg.on('progress', progressHandler);

        // Use a fast preset for the test — ratio is close enough for estimation
        const testPreset = { ...preset, preset: 'veryfast' };
        const args = buildFFmpegArgs(sampleRaw, sampleOut, testPreset);
        await ffmpeg.exec(args);
        ffmpeg.off('progress', progressHandler);

        const outData = await ffmpeg.readFile(sampleOut);
        const outSize = outData.length;
        const encodeTime = ((Date.now() - encodeStart) / 1000).toFixed(1);
        stepDone('encode', `${formatBytes(outSize)} in ${encodeTime}s`);

        // Step 4: Calculate
        stepActive('calc');
        const ratio = outSize / rawSize;
        const estimatedTotal = state.file.size * ratio;
        const pctReduction = ((1 - ratio) * 100).toFixed(0);
        stepDone('calc', `${pctReduction}% reduction ratio`);

        dom.estAdvanced.textContent = `~${formatBytes(estimatedTotal)}`;
        dom.estTestedRow.classList.remove('hidden');

        // Clean up
        await ffmpeg.deleteFile(sampleRaw).catch(() => {});
        await ffmpeg.deleteFile(sampleOut).catch(() => {});

    } catch (err) {
        console.error('Advanced estimate failed:', err);
        dom.estAdvanced.textContent = 'Error';
        dom.estTestedRow.classList.remove('hidden');
    }

    // Unlock UI
    dom.testEstimate.disabled = false;
    dom.compressBtn.disabled = false;
    pills.forEach(p => p.disabled = false);
}

// Step UI helpers
function resetSteps() {
    document.querySelectorAll('.step').forEach(s => {
        s.classList.remove('active', 'done');
        const detail = s.querySelector('.step-detail');
        if (detail) detail.textContent = '';
    });
}

function stepActive(id, detail) {
    const el = document.getElementById(`step-${id}`);
    if (!el) return;
    el.classList.add('active');
    el.classList.remove('done');
    if (detail) el.querySelector('.step-detail').textContent = detail;
}

function stepDone(id, detail) {
    const el = document.getElementById(`step-${id}`);
    if (!el) return;
    el.classList.remove('active');
    el.classList.add('done');
    if (detail) el.querySelector('.step-detail').textContent = detail;
}

function stepDetail(id, text) {
    const el = document.getElementById(`step-${id}-detail`);
    if (el) el.textContent = text;
}

// ============================================
// FFmpeg Loading
// ============================================
async function loadFFmpeg() {
    if (state.ffmpegLoaded || state.ffmpegLoading) return;
    state.ffmpegLoading = true;

    state.ffmpeg = new FFmpeg();

    try {
        dom.engineLabel.textContent = 'Loading engine...';
        dom.engineFill.style.width = '10%';

        const coreURL = await toBlobURL('lib/ffmpeg-core.js', 'text/javascript');
        dom.engineFill.style.width = '40%';

        const wasmURL = await toBlobURL('lib/ffmpeg-core.wasm', 'application/wasm');
        dom.engineFill.style.width = '80%';

        await state.ffmpeg.load({ coreURL, wasmURL });
        dom.engineFill.style.width = '100%';

        state.ffmpegLoaded = true;
        dom.engineLabel.textContent = 'Engine ready';
        dom.engineStatus.classList.add('ready');
    } catch (err) {
        console.error('Failed to load FFmpeg:', err);
        dom.engineLabel.textContent = 'Engine failed — refresh to retry';
        state.ffmpegLoading = false;
    }
}

// ============================================
// Background Support (Wake Lock + Notifications)
// ============================================
async function acquireWakeLock() {
    if (!('wakeLock' in navigator)) return;
    try {
        state.wakeLock = await navigator.wakeLock.request('screen');
        state.wakeLock.addEventListener('release', () => { state.wakeLock = null; });
    } catch (e) {
        // Wake lock can fail if tab is hidden at request time — non-critical
    }
}

function releaseWakeLock() {
    if (state.wakeLock) {
        state.wakeLock.release().catch(() => {});
        state.wakeLock = null;
    }
}

// Re-acquire wake lock when tab becomes visible again (browser releases it on hide)
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && state.compressing) {
        acquireWakeLock();
    }
});

function notifyCompletion(savings) {
    if (document.visibilityState === 'visible') return;
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    new Notification('Compression complete', {
        body: `Your video is ${savings}% smaller. Tap to save.`,
        icon: 'icon-192.png',
        tag: 'compress-done',
    });
}

// ============================================
// Compression
// ============================================
const SERVER_COMPRESS_THRESHOLD = 50 * 1024 * 1024; // 50MB

dom.compressBtn.addEventListener('click', startCompression);

async function startCompression() {
    if (!state.file) return;

    // Large files: use server-side compression (native FFmpeg, much faster)
    if (state.file.size > SERVER_COMPRESS_THRESHOLD) {
        return startServerCompression();
    }

    if (!state.ffmpegLoaded) {
        dom.compressBtn.disabled = true;
        dom.compressBtn.querySelector('span').textContent = 'Loading...';
        await loadFFmpeg();
        dom.compressBtn.disabled = false;
        dom.compressBtn.querySelector('span').textContent = 'Compress';
        if (!state.ffmpegLoaded) return;
    }

    // Request notification permission early (requires user gesture)
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }

    state.compressing = true;
    await acquireWakeLock();
    goToScreen(2);

    const ffmpeg = state.ffmpeg;
    const preset = QUALITY_PRESETS[state.quality];
    const inputName = 'input' + getExtension(state.file.name);
    const outputName = 'output.mp4';
    const compressStart = Date.now();

    const progressHandler = ({ progress }) => {
        const pct = Math.min(Math.round(progress * 100), 99);
        updateProgress(pct);
        // Show ETA
        if (pct > 3) {
            const elapsed = (Date.now() - compressStart) / 1000;
            const eta = Math.round((elapsed / pct) * (100 - pct));
            const bgLabel = document.hidden ? ' (background)' : '';
            dom.progressStatus.textContent = `Compressing...${bgLabel} ~${eta}s left`;
        }
    };
    ffmpeg.on('progress', progressHandler);

    try {
        dom.progressStatus.textContent = 'Writing file...';
        updateProgress(0);

        // Reuse file if already written (from estimation)
        if (!state.fileWritten || state.inputName !== inputName) {
            await ffmpeg.writeFile(inputName, await fetchFile(state.file));
            state.fileWritten = true;
            state.inputName = inputName;
        }

        dom.progressStatus.textContent = 'Compressing...';

        const args = buildFFmpegArgs(inputName, outputName, preset);
        await ffmpeg.exec(args);

        ffmpeg.off('progress', progressHandler);

        dom.progressStatus.textContent = 'Reading output...';
        updateProgress(99);

        const data = await ffmpeg.readFile(outputName);
        state.outputBlob = new Blob([data.buffer], { type: 'video/mp4' });

        await ffmpeg.deleteFile(inputName).catch(() => {});
        await ffmpeg.deleteFile(outputName).catch(() => {});
        state.fileWritten = false;

        const encodeTime = (Date.now() - compressStart) / 1000;
        updateProgress(100);
        showDone(encodeTime);
    } catch (err) {
        ffmpeg.off('progress', progressHandler);
        console.error('Compression failed:', err);
        dom.progressStatus.textContent = 'Error: ' + err.message;
    }

    state.compressing = false;
    releaseWakeLock();
}

// ============================================
// Server-side Compression (for large files)
// ============================================
async function startServerCompression() {
    state.compressing = true;
    await acquireWakeLock();
    goToScreen(2);

    const compressStart = Date.now();

    try {
        // Step 1: Upload
        dom.progressStatus.textContent = `Uploading ${formatBytes(state.file.size)}...`;
        updateProgress(0);

        const formData = new FormData();
        formData.append('file', state.file);

        const uploadRes = await new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('POST', `${DL_API}/upload`);

            xhr.upload.onprogress = (e) => {
                if (e.lengthComputable) {
                    const pct = Math.round((e.loaded / e.total) * 40); // 0-40%
                    updateProgress(pct);
                    const elapsed = (Date.now() - compressStart) / 1000;
                    if (pct > 3) {
                        const uploadEta = Math.round((elapsed / pct) * (40 - pct));
                        dom.progressStatus.textContent = `Uploading... ${Math.round(e.loaded / e.total * 100)}% (~${uploadEta}s)`;
                    }
                }
            };

            xhr.onload = () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(JSON.parse(xhr.responseText));
                } else {
                    try { reject(new Error(JSON.parse(xhr.responseText).detail)); }
                    catch { reject(new Error(`Upload failed (${xhr.status})`)); }
                }
            };
            xhr.onerror = () => reject(new Error('Upload failed — network error'));
            xhr.send(formData);
        });

        // Step 2: Compress on server
        updateProgress(45);
        dom.progressStatus.textContent = 'Compressing on server...';

        const compressBody = {
            file_id: uploadRes.id,
            filename: uploadRes.filename,
            quality: state.quality,
        };
        if (state.quality === 'target') {
            compressBody.target_mb = QUALITY_PRESETS.target.targetMB;
        }

        const compressRes = await fetch(`${DL_API}/compress`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(compressBody),
        });

        if (!compressRes.ok) {
            const err = await compressRes.json().catch(() => ({}));
            throw new Error(err.detail || 'Server compression failed');
        }

        const compressData = await compressRes.json();
        updateProgress(80);

        // Step 3: Download result
        dom.progressStatus.textContent = `Downloading ${formatBytes(compressData.size)}...`;

        const fileRes = await fetch(`${DL_API}/file/${compressData.id}/${encodeURIComponent(compressData.filename)}`);
        if (!fileRes.ok) throw new Error('Could not download compressed file');

        const blob = await fileRes.blob();
        state.outputBlob = new Blob([blob], { type: 'video/mp4' });

        updateProgress(100);
        const encodeTime = (Date.now() - compressStart) / 1000;
        showDone(encodeTime);
    } catch (err) {
        console.error('Server compression failed:', err);
        dom.progressStatus.textContent = 'Error: ' + err.message;
    }

    state.compressing = false;
    releaseWakeLock();
}

function buildFFmpegArgs(input, output, preset) {
    const args = ['-i', input];

    if (state.quality === 'target') {
        const targetBytes = preset.targetMB * 1024 * 1024;
        const audioBitrateKbps = parseInt(preset.audioBitrate) || 64;
        const totalBitrateKbps = Math.floor((targetBytes * 8) / state.duration / 1000);
        let videoBitrateKbps = totalBitrateKbps - audioBitrateKbps;

        if (videoBitrateKbps < 200 && state.height > 480) {
            args.push('-vf', 'scale=-2:480');
        } else if (videoBitrateKbps < 500 && state.height > 720) {
            args.push('-vf', 'scale=-2:720');
        }

        videoBitrateKbps = Math.max(videoBitrateKbps, 100);

        args.push(
            '-c:v', 'libx264',
            '-preset', preset.preset,
            '-b:v', `${videoBitrateKbps}k`,
            '-maxrate', `${Math.floor(videoBitrateKbps * 1.5)}k`,
            '-bufsize', `${Math.floor(videoBitrateKbps * 2)}k`,
        );
    } else {
        if (preset.scale && state.height > preset.scale) {
            args.push('-vf', `scale=-2:${preset.scale}`);
        }

        args.push(
            '-c:v', 'libx264',
            '-preset', preset.preset,
            '-crf', String(preset.crf),
        );
    }

    args.push(
        '-c:a', 'aac',
        '-b:a', preset.audioBitrate,
        // Retain container metadata (creation time, GPS/location, rotation, etc.)
        '-map_metadata', '0',
        '-movflags', '+faststart+use_metadata_tags',
        '-y', output
    );

    return args;
}

function updateProgress(pct) {
    const circumference = 2 * Math.PI * 68;
    const offset = circumference - (pct / 100) * circumference;
    dom.progressRing.style.strokeDashoffset = offset;
    dom.progressPercent.textContent = pct;
}

// ============================================
// Done
// ============================================
function showDone(encodeTimeSec) {
    const originalSize = state.file.size;
    const compressedSize = state.outputBlob.size;
    const savings = ((1 - compressedSize / originalSize) * 100).toFixed(1);
    const preset = QUALITY_PRESETS[state.quality];

    // Hero comparison
    dom.beforeSize.textContent = formatBytes(originalSize);
    dom.afterSize.textContent = formatBytes(compressedSize);
    dom.savingsPercent.textContent = `${savings}%`;

    // Input stats
    const s = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

    s('statInRes', `${state.width} x ${state.height}`);
    s('statDuration', formatDuration(state.duration));
    const inBitrateKbps = Math.round((originalSize * 8) / state.duration / 1000);
    s('statInBitrate', `${formatBitrate(inBitrateKbps)}`);
    s('statInSize', formatBytes(originalSize));

    // Encoding stats
    s('statCodec', 'H.264 (libx264)');
    if (state.quality === 'target') {
        const targetBitrate = Math.floor((preset.targetMB * 1024 * 1024 * 8) / state.duration / 1000);
        s('statMode', `Target size (${preset.targetMB} MB)`);
    } else {
        s('statMode', `CRF ${preset.crf} (${state.quality})`);
    }
    s('statPreset', preset.preset);
    s('statAudio', `AAC @ ${preset.audioBitrate}ps`);
    s('statContainer', 'MP4 (faststart)');

    // Output stats
    s('statOutSize', formatBytes(compressedSize));
    const outBitrateKbps = Math.round((compressedSize * 8) / state.duration / 1000);
    s('statOutBitrate', formatBitrate(outBitrateKbps));
    const ratio = (originalSize / compressedSize).toFixed(1);
    s('statRatio', `${ratio}:1`);
    s('statSaved', formatBytes(originalSize - compressedSize));

    // Time stats
    const encodeMin = Math.floor(encodeTimeSec / 60);
    const encodeSec = Math.round(encodeTimeSec % 60);
    s('statTime', encodeMin > 0 ? `${encodeMin}m ${encodeSec}s` : `${encodeSec}s`);
    const speed = (state.duration / encodeTimeSec).toFixed(2);
    s('statSpeed', `${speed}x realtime`);

    // Explainer
    let explainer = '';
    if (state.quality === 'target') {
        explainer = `Target size mode calculates the maximum video bitrate that fits ${preset.targetMB} MB given the video duration (${formatDuration(state.duration)}). The encoder constrains output using a bitrate cap with buffered rate control, ensuring the final file stays under the target.`;
    } else {
        explainer = `CRF (Constant Rate Factor) mode lets the encoder decide the bitrate per-frame based on visual complexity. CRF ${preset.crf} targets "${state.quality}" quality — simpler frames get fewer bits, complex frames get more. This produces the best quality-per-byte but the output size varies by content.`;
    }
    if (preset.scale && state.height > preset.scale) {
        explainer += ` Resolution was scaled to ${preset.scale}p to reduce file size further.`;
    }
    explainer += ' Container uses "faststart" flag to move the moov atom to the front, allowing playback to begin before the full file downloads.';
    s('statExplainer', explainer);

    notifyCompletion(savings);
    if (navigator.vibrate) navigator.vibrate([50, 50, 100]);
    goToScreen(3);
}

function formatBitrate(kbps) {
    if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbps`;
    return `${kbps} kbps`;
}

dom.saveBtn.addEventListener('click', () => {
    if (!state.outputBlob) return;
    const baseName = state.file.name.replace(/\.[^.]+$/, '');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(state.outputBlob);
    a.download = `${baseName}_compressed.mp4`;
    a.click();
    URL.revokeObjectURL(a.href);
    if (navigator.vibrate) navigator.vibrate(10);
});

dom.shareBtn.addEventListener('click', async () => {
    if (!state.outputBlob || !navigator.share) {
        dom.saveBtn.click();
        return;
    }
    const baseName = state.file.name.replace(/\.[^.]+$/, '');
    const file = new File([state.outputBlob], `${baseName}_compressed.mp4`, { type: 'video/mp4' });
    try {
        await navigator.share({ files: [file] });
    } catch (err) {
        if (err.name !== 'AbortError') dom.saveBtn.click();
    }
});

const SHARE_MAX_BYTES = 200 * 1024 * 1024;

async function uploadShareBlob() {
    if (!state.outputBlob) throw new Error('No compressed video');
    if (state.outputBlob.size > SHARE_MAX_BYTES) {
        throw new Error('Too large to share (max 200 MB)');
    }
    const baseName = state.file.name.replace(/\.[^.]+$/, '');
    const filename = `${baseName}_compressed.mp4`;
    const fd = new FormData();
    fd.append('file', state.outputBlob, filename);
    const resp = await fetch('/api/share', { method: 'POST', body: fd });
    if (!resp.ok) {
        let msg = `Upload failed (${resp.status})`;
        try {
            const err = await resp.json();
            if (err?.detail) msg = err.detail;
        } catch (_) {}
        throw new Error(msg);
    }
    const data = await resp.json();
    return new URL(data.share_url || data.url, window.location.origin).toString();
}

dom.shareLinkBtn.addEventListener('click', async () => {
    if (!state.outputBlob || dom.shareLinkBtn.disabled) return;
    const origLabel = dom.shareLinkLabel.textContent;
    dom.shareLinkBtn.disabled = true;
    dom.shareLinkLabel.textContent = 'Uploading…';
    try {
        const url = await uploadShareBlob();

        // Prefer native share-with-URL on mobile; fall back to clipboard.
        if (navigator.share && /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)) {
            try {
                await navigator.share({ url });
                dom.shareLinkLabel.textContent = 'Shared!';
            } catch (err) {
                if (err.name !== 'AbortError') {
                    await navigator.clipboard.writeText(url);
                    dom.shareLinkLabel.textContent = 'Link copied';
                } else {
                    dom.shareLinkLabel.textContent = origLabel;
                }
            }
        } else {
            await navigator.clipboard.writeText(url);
            dom.shareLinkLabel.textContent = 'Link copied';
        }

        if (navigator.vibrate) navigator.vibrate(10);
        dom.shareHint.textContent = 'Link expires in 7 days.';
    } catch (err) {
        dom.shareLinkLabel.textContent = 'Failed';
        dom.shareHint.textContent = err.message || 'Could not create share link.';
    } finally {
        setTimeout(() => {
            dom.shareLinkBtn.disabled = false;
            dom.shareLinkLabel.textContent = origLabel;
        }, 3500);
    }
});

dom.anotherBtn.addEventListener('click', () => {
    state.file = null;
    state.outputBlob = null;
    state.duration = 0;
    state.width = 0;
    state.height = 0;
    state.fileWritten = false;
    dom.preview.src = '';
    dom.fileInput.value = '';
    goToScreen(0);
});

// ============================================
// Cancel & Back
// ============================================
dom.cancelBtn.addEventListener('click', () => goToScreen(1));

document.querySelectorAll('[data-back]').forEach(btn => {
    btn.addEventListener('click', () => {
        if (state.currentScreen > 0) goToScreen(state.currentScreen - 1);
    });
});

// About navigation
dom.aboutBtn.addEventListener('click', () => goToScreen(4));

document.querySelectorAll('[data-back-home]').forEach(btn => {
    btn.addEventListener('click', () => goToScreen(0));
});

// Resume — go back to options with previously loaded file
dom.resumeBtn.addEventListener('click', () => {
    if (state.file) goToScreen(1);
});

// ============================================
// Utilities
// ============================================
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    const val = bytes / Math.pow(1024, i);
    return val >= 100 ? `${Math.round(val)} ${units[i]}` : `${val.toFixed(1)} ${units[i]}`;
}

function formatDuration(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function getExtension(filename) {
    const match = filename.match(/\.[^.]+$/);
    return match ? match[0] : '.mp4';
}

// ============================================
// Editor — Split & Trim
// ============================================
const editState = {
    splits: [],          // sorted array of split times (seconds)
    deletedSegments: new Set(), // indices of deleted segments
    selectedSegment: -1,
    playing: false,
    thumbsGenerated: false,
    history: [],         // undo stack: { splits, deletedSegments }
    zoom: 1,             // 1x = fit to screen, up to 20x
    zoomMin: 1,
    zoomMax: 20,
    thumbCache: [],      // cached thumbnail ImageData for re-rendering
    baseWidth: 0,        // timeline width at 1x zoom
};

// Enter edit mode
dom.editBtn.addEventListener('click', () => {
    if (!state.file) return;
    enterEditMode();
});

function enterEditMode() {
    const url = URL.createObjectURL(state.file);
    dom.editPreview.src = url;
    dom.editPreview.currentTime = 0;

    dom.editPreview.onloadedmetadata = () => {
        dom.editTotalTime.textContent = formatDuration(dom.editPreview.duration);
        dom.editCurrentTime.textContent = formatDuration(0);

        // Reset edit state
        editState.splits = [];
        editState.deletedSegments = new Set();
        editState.selectedSegment = -1;
        editState.history = [];
        editState.thumbsGenerated = false;

        // Show screen first so layout is computed (clientWidth > 0)
        goToScreen(5);

        // Wait a frame for layout, then generate thumbnails
        requestAnimationFrame(() => {
            generateThumbnails();
            renderSegments();
            updateEditButtons();
        });
    };
}

dom.editBack.addEventListener('click', () => {
    dom.editPreview.pause();
    editState.playing = false;
    goToScreen(1);
});

// ---- Playback ----
dom.editPlayBtn.addEventListener('click', () => {
    if (editState.playing) {
        dom.editPreview.pause();
    } else {
        dom.editPreview.play();
    }
});

dom.editPreview.addEventListener('play', () => {
    editState.playing = true;
    dom.editPlayIcon.classList.add('hidden');
    dom.editPauseIcon.classList.remove('hidden');
});

dom.editPreview.addEventListener('pause', () => {
    editState.playing = false;
    dom.editPlayIcon.classList.remove('hidden');
    dom.editPauseIcon.classList.add('hidden');
});

dom.editPreview.addEventListener('timeupdate', () => {
    const t = dom.editPreview.currentTime;
    dom.editCurrentTime.textContent = formatDuration(t);
    updatePlayhead(t);
});

function updatePlayhead(t) {
    if (state.duration > 0) {
        const pct = (t / state.duration) * 100;
        dom.timelinePlayhead.style.left = `${pct}%`;

        // Auto-scroll timeline to follow playhead during playback
        if (editState.playing) {
            const trackW = dom.timelineTrack.offsetWidth;
            const scrollW = dom.timelineScroll.clientWidth;
            const playheadX = (t / state.duration) * trackW;
            const scrollLeft = dom.timelineScroll.scrollLeft;

            // If playhead is near the right edge, scroll to keep it centered
            if (playheadX > scrollLeft + scrollW * 0.75 || playheadX < scrollLeft + scrollW * 0.15) {
                dom.timelineScroll.scrollLeft = playheadX - scrollW * 0.3;
            }
        }
    }
}

// ---- Timeline touch/click scrubbing ----
function scrubTimeline(e) {
    const rect = dom.timelineTrack.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    const pct = Math.max(0, Math.min(1, x / rect.width));
    const time = pct * state.duration;
    dom.editPreview.currentTime = time;
    dom.editCurrentTime.textContent = formatDuration(time);
    updatePlayhead(time);
}

let scrubbing = false;
dom.timelineScroll.addEventListener('mousedown', (e) => {
    scrubbing = true;
    scrubTimeline(e);
});
dom.timelineScroll.addEventListener('touchstart', (e) => {
    // Only scrub with single touch (not pinch)
    if (e.touches.length === 1) {
        scrubbing = true;
        scrubTimeline(e);
    }
}, { passive: true });

window.addEventListener('mousemove', (e) => { if (scrubbing) scrubTimeline(e); });
window.addEventListener('touchmove', (e) => { if (scrubbing && e.touches.length === 1) scrubTimeline(e); }, { passive: true });
window.addEventListener('mouseup', () => { scrubbing = false; });
window.addEventListener('touchend', () => { scrubbing = false; });

// ---- Zoom controls ----
dom.zoomInBtn.addEventListener('click', () => setZoom(editState.zoom * 1.5));
dom.zoomOutBtn.addEventListener('click', () => setZoom(editState.zoom / 1.5));

// Mouse wheel zoom on timeline
dom.timelineScroll.addEventListener('wheel', (e) => {
    if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        // Zoom centered on mouse position
        const rect = dom.timelineScroll.getBoundingClientRect();
        const mouseX = e.clientX - rect.left + dom.timelineScroll.scrollLeft;
        const mousePct = mouseX / dom.timelineTrack.offsetWidth;

        setZoom(editState.zoom * factor);

        // Keep the point under the mouse stationary
        const newX = mousePct * dom.timelineTrack.offsetWidth;
        dom.timelineScroll.scrollLeft = newX - (e.clientX - rect.left);
    }
}, { passive: false });

// Pinch-to-zoom on timeline
let pinchStartDist = 0;
let pinchStartZoom = 1;

dom.timelineScroll.addEventListener('touchstart', (e) => {
    if (e.touches.length === 2) {
        scrubbing = false;
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        pinchStartDist = Math.hypot(dx, dy);
        pinchStartZoom = editState.zoom;
    }
}, { passive: true });

dom.timelineScroll.addEventListener('touchmove', (e) => {
    if (e.touches.length === 2) {
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        const dist = Math.hypot(dx, dy);
        const scale = dist / pinchStartDist;

        // Zoom centered on pinch midpoint
        const rect = dom.timelineScroll.getBoundingClientRect();
        const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        const scrollMidX = midX - rect.left + dom.timelineScroll.scrollLeft;
        const midPct = scrollMidX / dom.timelineTrack.offsetWidth;

        setZoom(pinchStartZoom * scale);

        const newMidX = midPct * dom.timelineTrack.offsetWidth;
        dom.timelineScroll.scrollLeft = newMidX - (midX - rect.left);
    }
}, { passive: true });

// Sync ruler with timeline scroll
dom.timelineScroll.addEventListener('scroll', () => {
    dom.timelineRuler.style.transform = `translateX(-${dom.timelineScroll.scrollLeft}px)`;
});

function setZoom(newZoom) {
    editState.zoom = Math.max(editState.zoomMin, Math.min(editState.zoomMax, newZoom));
    applyZoom();
}

function applyZoom() {
    const z = editState.zoom;
    const totalW = Math.round(editState.baseWidth * z);

    dom.timelineTrack.style.width = `${totalW}px`;
    dom.timelineThumbs.style.width = `${totalW}px`;

    // Update zoom UI
    const pct = ((z - editState.zoomMin) / (editState.zoomMax - editState.zoomMin)) * 100;
    dom.zoomFill.style.width = `${pct}%`;
    dom.zoomLabel.textContent = z < 10 ? `${z.toFixed(1)}x` : `${Math.round(z)}x`;

    // Regenerate thumbnails at new zoom level
    regenerateThumbsForZoom(totalW);

    // Update ruler
    renderRuler(totalW);

    // Re-render segments at new width
    renderSegments();

    // Update playhead
    updatePlayhead(dom.editPreview.currentTime);
}

// ---- Thumbnail generation ----
// Uses the edit preview video directly (no second copy in memory)
// Generates frames async with yields to keep UI responsive

let thumbGenAbort = null; // AbortController for cancelling in-progress generation

function generateThumbnails() {
    // Cancel any in-progress generation
    if (thumbGenAbort) thumbGenAbort.abort();
    thumbGenAbort = new AbortController();

    editState.baseWidth = dom.timelineScroll.clientWidth;
    editState.zoom = 1;
    editState.baseThumbCanvas = null;

    const totalW = editState.baseWidth;
    const canvas = dom.timelineThumbs;
    canvas.width = totalW;
    canvas.height = 56;
    dom.timelineTrack.style.width = `${totalW}px`;

    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#1a1a1a';
    ctx.fillRect(0, 0, totalW, 56);

    // Fewer thumbs for large/long files to reduce seeks
    const numThumbs = Math.max(10, Math.min(30, Math.ceil(state.duration / 3)));
    const thumbW = totalW / numThumbs;

    // Use a lightweight hidden video that only preloads metadata
    const tmpVideo = document.createElement('video');
    tmpVideo.preload = 'metadata';
    tmpVideo.muted = true;
    tmpVideo.playsInline = true;
    tmpVideo.src = dom.editPreview.src;

    const signal = thumbGenAbort.signal;
    let i = 0;

    function drawAndAdvance() {
        if (signal.aborted) { tmpVideo.src = ''; return; }

        const srcAspect = tmpVideo.videoWidth / tmpVideo.videoHeight;
        const drawH = 56;
        const drawW = drawH * srcAspect;
        const x = i * thumbW;
        const offsetX = (thumbW - drawW) / 2;
        ctx.drawImage(tmpVideo, x + Math.max(0, offsetX), 0, Math.min(thumbW, drawW), drawH);

        i++;
        if (i < numThumbs) {
            // Yield to the browser between seeks to prevent "not responding"
            setTimeout(() => {
                if (signal.aborted) { tmpVideo.src = ''; return; }
                tmpVideo.currentTime = (i / numThumbs) * state.duration;
            }, 0);
        } else {
            editState.thumbsGenerated = true;
            // Cache base thumbnails as a canvas (cheaper than ImageData)
            const cache = document.createElement('canvas');
            cache.width = totalW;
            cache.height = 56;
            cache.getContext('2d').drawImage(canvas, 0, 0);
            editState.baseThumbCanvas = cache;
            tmpVideo.src = '';
            renderRuler(totalW);
            dom.zoomFill.style.width = '0%';
            dom.zoomLabel.textContent = '1x';
        }
    }

    tmpVideo.onseeked = drawAndAdvance;
    tmpVideo.onloadeddata = () => {
        if (!signal.aborted) tmpVideo.currentTime = 0;
    };
}

function regenerateThumbsForZoom(totalW) {
    if (!editState.thumbsGenerated || !editState.baseThumbCanvas) return;

    const canvas = dom.timelineThumbs;
    canvas.width = totalW;
    canvas.height = 56;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#1a1a1a';
    ctx.fillRect(0, 0, totalW, 56);

    // Scale cached base thumbnails to zoomed width
    ctx.drawImage(editState.baseThumbCanvas, 0, 0, editState.baseThumbCanvas.width, 56, 0, 0, totalW, 56);
}

function renderRuler(totalW) {
    dom.timelineRuler.innerHTML = '';
    dom.timelineRuler.style.width = `${totalW}px`;

    if (state.duration <= 0) return;

    // Choose tick interval based on zoom
    const pxPerSec = totalW / state.duration;
    let interval;
    if (pxPerSec > 100) interval = 1;
    else if (pxPerSec > 40) interval = 2;
    else if (pxPerSec > 20) interval = 5;
    else if (pxPerSec > 8) interval = 10;
    else if (pxPerSec > 3) interval = 30;
    else interval = 60;

    // Major ticks at larger intervals
    const majorEvery = interval <= 5 ? 5 : interval <= 30 ? 6 : 5;

    for (let t = 0, idx = 0; t <= state.duration; t += interval, idx++) {
        const x = (t / state.duration) * totalW;
        const isMajor = idx % majorEvery === 0;

        const mark = document.createElement('div');
        mark.className = 'ruler-mark';
        mark.style.left = `${x}px`;

        const tick = document.createElement('div');
        tick.className = `ruler-tick${isMajor ? ' major' : ''}`;
        mark.appendChild(tick);

        if (isMajor) {
            const label = document.createElement('span');
            label.className = 'ruler-time';
            label.textContent = formatDuration(t);
            mark.appendChild(label);
        }

        dom.timelineRuler.appendChild(mark);
    }
}

// ---- Split ----
dom.editSplitBtn.addEventListener('click', () => {
    const t = dom.editPreview.currentTime;
    if (t <= 0.1 || t >= state.duration - 0.1) return; // can't split at very start/end
    if (editState.splits.some(s => Math.abs(s - t) < 0.3)) return; // too close to existing split

    pushEditHistory();
    editState.splits.push(t);
    editState.splits.sort((a, b) => a - b);
    renderSegments();
    updateEditButtons();
    if (navigator.vibrate) navigator.vibrate(15);
});

// ---- Delete selected segment ----
dom.editDeleteBtn.addEventListener('click', () => {
    if (editState.selectedSegment < 0) return;
    const segments = getSegments();
    if (segments.length <= 1) return;

    // Can't delete all segments
    const keptCount = segments.filter((_, i) => !editState.deletedSegments.has(i)).length;
    if (keptCount <= 1 && !editState.deletedSegments.has(editState.selectedSegment)) return;

    pushEditHistory();
    if (editState.deletedSegments.has(editState.selectedSegment)) {
        editState.deletedSegments.delete(editState.selectedSegment);
    } else {
        editState.deletedSegments.add(editState.selectedSegment);
    }
    renderSegments();
    updateEditButtons();
    if (navigator.vibrate) navigator.vibrate(10);
});

// ---- Undo ----
dom.editUndoBtn.addEventListener('click', () => {
    if (editState.history.length === 0) return;
    const prev = editState.history.pop();
    editState.splits = prev.splits;
    editState.deletedSegments = prev.deletedSegments;
    editState.selectedSegment = -1;
    renderSegments();
    updateEditButtons();
    if (navigator.vibrate) navigator.vibrate(10);
});

function pushEditHistory() {
    editState.history.push({
        splits: [...editState.splits],
        deletedSegments: new Set(editState.deletedSegments),
    });
    // Limit undo stack
    if (editState.history.length > 50) editState.history.shift();
}

function getSegments() {
    const points = [0, ...editState.splits, state.duration];
    const segments = [];
    for (let i = 0; i < points.length - 1; i++) {
        segments.push({ start: points[i], end: points[i + 1], index: i });
    }
    return segments;
}

function renderSegments() {
    const segments = getSegments();

    // Render timeline overlays
    dom.timelineSegments.innerHTML = '';
    segments.forEach((seg, i) => {
        const startPct = (seg.start / state.duration) * 100;
        const widthPct = ((seg.end - seg.start) / state.duration) * 100;

        const div = document.createElement('div');
        div.className = 'timeline-segment';
        if (editState.deletedSegments.has(i)) div.classList.add('deleted');
        if (editState.selectedSegment === i) div.classList.add('selected');
        div.style.left = `${startPct}%`;
        div.style.width = `${widthPct}%`;
        div.addEventListener('click', (e) => {
            e.stopPropagation();
            editState.selectedSegment = editState.selectedSegment === i ? -1 : i;
            renderSegments();
            updateEditButtons();
            if (navigator.vibrate) navigator.vibrate(5);
        });
        dom.timelineSegments.appendChild(div);
    });

    // Render split markers
    editState.splits.forEach(t => {
        const pct = (t / state.duration) * 100;
        const marker = document.createElement('div');
        marker.className = 'timeline-split';
        marker.style.left = `${pct}%`;
        dom.timelineSegments.appendChild(marker);
    });

    // Render segment list
    dom.segmentList.innerHTML = '';
    segments.forEach((seg, i) => {
        const isDeleted = editState.deletedSegments.has(i);
        const isSelected = editState.selectedSegment === i;
        const dur = seg.end - seg.start;

        const item = document.createElement('div');
        item.className = 'segment-item';
        if (isDeleted) item.classList.add('deleted');
        if (isSelected) item.classList.add('selected');

        item.innerHTML = `
            <div class="segment-item-num">${i + 1}</div>
            <div class="segment-item-info">
                <span class="segment-item-time">${formatTimePrecise(seg.start)} — ${formatTimePrecise(seg.end)}</span>
                <span class="segment-item-dur">${dur.toFixed(1)}s</span>
            </div>
            <span class="segment-item-status ${isDeleted ? 'cut' : 'keep'}">${isDeleted ? 'Cut' : 'Keep'}</span>
        `;

        item.addEventListener('click', () => {
            editState.selectedSegment = editState.selectedSegment === i ? -1 : i;
            // Seek to segment start
            dom.editPreview.currentTime = seg.start;
            renderSegments();
            updateEditButtons();
            if (navigator.vibrate) navigator.vibrate(5);
        });

        dom.segmentList.appendChild(item);
    });
}

function updateEditButtons() {
    const segments = getSegments();
    const hasSelection = editState.selectedSegment >= 0;
    const keptCount = segments.filter((_, i) => !editState.deletedSegments.has(i)).length;

    dom.editDeleteBtn.disabled = !hasSelection || (keptCount <= 1 && !editState.deletedSegments.has(editState.selectedSegment));
    dom.editUndoBtn.disabled = editState.history.length === 0;

    // Update delete button label based on whether segment is already deleted
    if (hasSelection && editState.deletedSegments.has(editState.selectedSegment)) {
        dom.editDeleteBtn.querySelector('span').textContent = 'Restore';
        dom.editDeleteBtn.classList.remove('danger');
    } else {
        dom.editDeleteBtn.querySelector('span').textContent = 'Delete';
        dom.editDeleteBtn.classList.add('danger');
    }

    // Export only if there are edits
    const hasEdits = editState.splits.length > 0 || editState.deletedSegments.size > 0;
    dom.editExportBtn.disabled = !hasEdits;
}

function formatTimePrecise(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

// ---- Export ----
dom.editExportBtn.addEventListener('click', exportEdit);

async function exportEdit() {
    const segments = getSegments().filter((_, i) => !editState.deletedSegments.has(i));
    if (segments.length === 0) return;

    // Load FFmpeg if needed
    if (!state.ffmpegLoaded) {
        dom.editExportBtn.disabled = true;
        dom.editExportBtn.querySelector('span').textContent = 'Loading...';
        await loadFFmpeg();
        dom.editExportBtn.disabled = false;
        dom.editExportBtn.querySelector('span').textContent = 'Export';
        if (!state.ffmpegLoaded) return;
    }

    dom.editPreview.pause();
    editState.playing = false;

    // Switch to progress screen
    goToScreen(2);
    dom.progressStatus.textContent = 'Writing file...';
    updateProgress(0);

    const ffmpeg = state.ffmpeg;
    const inputName = 'input' + getExtension(state.file.name);

    try {
        // Write input file
        await ffmpeg.writeFile(inputName, await fetchFile(state.file));
        updateProgress(10);

        if (segments.length === 1) {
            // Single segment — simple trim
            const seg = segments[0];
            dom.progressStatus.textContent = 'Trimming...';

            await ffmpeg.exec([
                '-ss', String(seg.start),
                '-i', inputName,
                '-t', String(seg.end - seg.start),
                '-c', 'copy',
                '-movflags', '+faststart',
                '-y', 'output.mp4',
            ]);
            updateProgress(80);
        } else {
            // Multiple segments — cut each then concat
            const partNames = [];

            for (let i = 0; i < segments.length; i++) {
                const seg = segments[i];
                const partName = `part${i}.mp4`;
                partNames.push(partName);

                dom.progressStatus.textContent = `Cutting segment ${i + 1} of ${segments.length}...`;
                const pct = 10 + (i / segments.length) * 60;
                updateProgress(Math.round(pct));

                await ffmpeg.exec([
                    '-ss', String(seg.start),
                    '-i', inputName,
                    '-t', String(seg.end - seg.start),
                    '-c', 'copy',
                    '-avoid_negative_ts', 'make_zero',
                    '-y', partName,
                ]);
            }

            // Build concat list
            dom.progressStatus.textContent = 'Joining segments...';
            updateProgress(75);

            const concatList = partNames.map(n => `file '${n}'`).join('\n');
            await ffmpeg.writeFile('concat.txt', concatList);

            await ffmpeg.exec([
                '-f', 'concat',
                '-safe', '0',
                '-i', 'concat.txt',
                '-c', 'copy',
                '-movflags', '+faststart',
                '-y', 'output.mp4',
            ]);

            // Cleanup parts
            for (const name of partNames) {
                await ffmpeg.deleteFile(name).catch(() => {});
            }
            await ffmpeg.deleteFile('concat.txt').catch(() => {});
        }

        updateProgress(90);
        dom.progressStatus.textContent = 'Reading output...';

        const data = await ffmpeg.readFile('output.mp4');
        state.outputBlob = new Blob([data.buffer], { type: 'video/mp4' });

        await ffmpeg.deleteFile(inputName).catch(() => {});
        await ffmpeg.deleteFile('output.mp4').catch(() => {});

        updateProgress(100);
        showEditDone();
    } catch (err) {
        console.error('Export failed:', err);
        dom.progressStatus.textContent = 'Error: ' + err.message;
    }
}

function showEditDone() {
    const originalSize = state.file.size;
    const outputSize = state.outputBlob.size;
    const savings = ((1 - outputSize / originalSize) * 100).toFixed(1);

    // Calculate kept duration
    const keptSegments = getSegments().filter((_, i) => !editState.deletedSegments.has(i));
    const keptDuration = keptSegments.reduce((sum, s) => sum + (s.end - s.start), 0);

    dom.beforeSize.textContent = formatBytes(originalSize);
    dom.afterSize.textContent = formatBytes(outputSize);
    dom.savingsPercent.textContent = `${savings}%`;

    const s = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    s('statInRes', `${state.width} x ${state.height}`);
    s('statDuration', `${formatDuration(state.duration)} → ${formatDuration(keptDuration)}`);
    s('statInBitrate', formatBitrate(Math.round((originalSize * 8) / state.duration / 1000)));
    s('statInSize', formatBytes(originalSize));
    s('statCodec', 'Copy (lossless)');
    s('statMode', `${keptSegments.length} segment${keptSegments.length > 1 ? 's' : ''} kept`);
    s('statPreset', 'N/A (stream copy)');
    s('statAudio', 'Copy (lossless)');
    s('statContainer', 'MP4 (faststart)');
    s('statOutSize', formatBytes(outputSize));
    s('statOutBitrate', formatBitrate(Math.round((outputSize * 8) / keptDuration / 1000)));
    const ratio = (originalSize / outputSize).toFixed(1);
    s('statRatio', `${ratio}:1`);
    s('statSaved', formatBytes(originalSize - outputSize));
    s('statTime', 'Instant (copy)');
    s('statSpeed', 'N/A');
    s('statExplainer', `Stream copy mode was used — no re-encoding. The original video and audio streams were copied directly, preserving full quality. ${editState.splits.length} cut${editState.splits.length !== 1 ? 's' : ''} removed ${formatDuration(state.duration - keptDuration)} of footage.`);

    if (navigator.vibrate) navigator.vibrate([50, 50, 100]);
    goToScreen(3);
}

// ============================================
// PWA install prompt
// ============================================
// We surface install affordances on the home screen because a bare PWA without
// a visible install button is effectively invisible on mobile. Branches:
//   • Already installed (display-mode: standalone or navigator.standalone) → hide
//   • Android Chromium with `beforeinstallprompt` → "Install" button calls prompt()
//   • iOS Safari → instructions (no programmatic install on iOS)
//   • In-app webviews (Instagram, FB, TikTok, etc.) → "Open in browser to install"
//   • Anything else (desktop browser, Firefox Android) → hide
// Dismissal is sticky for 14 days via localStorage.
(function setupInstallPrompt() {
    const DISMISS_KEY = 'compress_install_dismissed_at';
    const DISMISS_TTL_MS = 14 * 24 * 60 * 60 * 1000;

    const card = document.getElementById('installCard');
    const btn = document.getElementById('installBtn');
    const dismiss = document.getElementById('installDismiss');
    const titleEl = document.getElementById('installTitle');
    const subEl = document.getElementById('installSub');
    if (!card || !btn || !dismiss) return;

    const ua = navigator.userAgent || '';
    const isStandalone =
        window.matchMedia('(display-mode: standalone)').matches ||
        window.navigator.standalone === true;
    const isIOS = /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
    const isWebView = /FBAN|FBAV|Instagram|Line|TikTok|Snapchat|MicroMessenger|Twitter/i.test(ua);

    let deferredPrompt = null;

    function recentlyDismissed() {
        const v = parseInt(localStorage.getItem(DISMISS_KEY) || '0', 10);
        return v && (Date.now() - v) < DISMISS_TTL_MS;
    }

    function hide() { card.classList.add('hidden'); }
    function show(variant) {
        card.classList.remove('hidden', 'ios', 'webview');
        if (variant) card.classList.add(variant);
    }

    function showInstallButton() {
        titleEl.textContent = 'Install Compress';
        subEl.textContent = 'Runs offline. Lives on your home screen.';
        btn.textContent = 'Install';
        show();
    }
    function showIOSInstructions() {
        titleEl.textContent = 'Add to Home Screen';
        subEl.innerHTML = 'Tap <svg aria-hidden="true" viewBox="0 0 16 22" style="display:inline;vertical-align:-3px;width:13px;height:18px"><path d="M8 1v13M4 5l4-4 4 4" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 10v9a2 2 0 002 2h8a2 2 0 002-2v-9" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg> in Safari, then <em>Add to Home Screen</em>.';
        show('ios');
    }
    function showWebViewHint() {
        titleEl.textContent = 'Open in browser to install';
        subEl.textContent = 'In-app browsers can’t install web apps. Tap the menu and choose “Open in Safari/Chrome”.';
        show('webview');
    }

    // Decide what (if anything) to show
    if (isStandalone) {
        hide(); return;
    }
    if (recentlyDismissed()) {
        hide(); return;
    }

    if (isWebView && isIOS) {
        // In-app browser on iOS can't install; nudge them to Safari
        showWebViewHint();
    } else if (isIOS) {
        showIOSInstructions();
    }
    // Android Chromium path: wait for beforeinstallprompt to actually fire
    // (don't show a fake button if the browser won't honor it)

    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        deferredPrompt = e;
        if (!isStandalone && !recentlyDismissed()) showInstallButton();
    });

    btn.addEventListener('click', async () => {
        if (!deferredPrompt) return;
        btn.disabled = true;
        try {
            deferredPrompt.prompt();
            const choice = await deferredPrompt.userChoice;
            if (choice && choice.outcome === 'accepted') hide();
        } catch (_) {
            // Re-enable so user can retry if the prompt was dismissed weirdly
            btn.disabled = false;
        }
        deferredPrompt = null;
    });

    dismiss.addEventListener('click', () => {
        localStorage.setItem(DISMISS_KEY, String(Date.now()));
        hide();
    });

    window.addEventListener('appinstalled', () => {
        localStorage.setItem(DISMISS_KEY, String(Date.now() + DISMISS_TTL_MS));
        hide();
    });
})();

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});

    // Handle videos shared via share_target (from other apps)
    navigator.serviceWorker.addEventListener('message', (event) => {
        if (event.data?.type === 'shared-video' && event.data.file) {
            handleFile(event.data.file);
        }
    });
}

// Preload FFmpeg WASM immediately — don't wait for file selection
loadFFmpeg();

// Auto-trigger URL flow when launched via the share-target with a link
// (TikTok / YouTube / YouTube Music / IG / X → share → "Compress"). The SW
// extracts the URL from the share intent and redirects here with
// ?url=<encoded>. We download via the right endpoint (audio for music,
// video for everything else), then auto-promote the result to a 7-day
// /v/<id> share link and surface it via navigator.share or clipboard.
async function startUrlAudioShare(url) {
    urlDom.input.disabled = true;
    urlDom.actions.classList.add('hidden');
    urlDom.status.classList.remove('hidden', 'done', 'error');
    urlDom.statusText.textContent = 'Extracting audio…';
    try {
        const res = await fetch(`${DL_API}/download-audio`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Audio extraction failed');
        }
        const data = await res.json();
        urlDownloadInfo = { id: data.id, filename: data.filename };
        urlDom.status.classList.add('done');
        urlDom.statusText.textContent = `Ready — ${data.filename} (${formatBytes(data.size)})`;
        urlDom.actions.classList.remove('hidden');
        setHomeCompact(true);
    } catch (err) {
        urlDom.status.classList.add('error');
        urlDom.statusText.textContent = err.message;
    } finally {
        urlDom.input.disabled = false;
    }
}

(async function consumeShareUrl() {
    const sharedUrl = new URLSearchParams(location.search).get('url');
    if (!sharedUrl) return;
    if (!urlDom.input) return;
    urlDom.input.value = sharedUrl;
    history.replaceState(null, '', location.pathname);

    let host = '';
    try { host = new URL(sharedUrl).hostname.toLowerCase(); } catch (_) {}
    const isMusic = host === 'music.youtube.com' || host.endsWith('.music.youtube.com');

    // Defer a tick so the rest of init has bound listeners + buttons are wired.
    await new Promise((r) => setTimeout(r, 0));
    await (isMusic ? startUrlAudioShare(sharedUrl) : startUrlDownload());

    // Shared LINKS (TikTok / IG / YouTube / music) go directly to a /v/ share link.
    // (Shared FILES — video or image from Photos — go to the compress page instead;
    // those are handled by the share-target file branch, not this URL path.)
    if (urlDownloadInfo && urlDom.shareBtn) {
        urlDom.shareBtn.click();
    }
})();
