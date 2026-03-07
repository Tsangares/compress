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
        desc: 'Visually lossless. Slight file size reduction (~30% smaller).',
        crf: 23,
        preset: 'medium',
        audioBitrate: '128k',
        scale: null,
    },
    medium: {
        desc: 'Good balance of quality and file size (~60% smaller).',
        crf: 28,
        preset: 'medium',
        audioBitrate: '96k',
        scale: null,
    },
    low: {
        desc: 'Smaller files, reduced quality. Good for messaging (~80% smaller).',
        crf: 35,
        preset: 'fast',
        audioBitrate: '64k',
        scale: 720,
    },
    target: {
        desc: 'Calculates the best settings to fit under 10 MB. May reduce resolution.',
        targetMB: 10,
        preset: 'medium',
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
    progressRing: $('#progressRing'),
    progressPercent: $('#progressPercent'),
    progressStatus: $('#progressStatus'),
    cancelBtn: $('#cancelBtn'),
    beforeSize: $('#beforeSize'),
    afterSize: $('#afterSize'),
    savingsPercent: $('#savingsPercent'),
    saveBtn: $('#saveBtn'),
    shareBtn: $('#shareBtn'),
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
};

// ============================================
// Screen Navigation
// ============================================
function goToScreen(index, pushHistory = true) {
    const list = [screens.select, screens.options, screens.progress, screens.done, screens.about];
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
        // Go back to previous logical screen
        if (state.currentScreen === 4) {
            // About → home
            goToScreen(0, false);
        } else if (state.currentScreen === 3) {
            // Done → home (not back to progress)
            goToScreen(0, false);
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

    // Estimate output bitrate based on typical CRF results for resolution
    const h = Math.max(height, 480);
    const typicalKbps = {
        high:   h > 1080 ? 14000 : h > 720 ? 5500 : h > 480 ? 2800 : 1400,
        medium: h > 1080 ? 6000  : h > 720 ? 2500 : h > 480 ? 1200 : 600,
        low:    h > 1080 ? 2000  : h > 720 ? 800  : h > 480 ? 450  : 250,
    };

    const inputKbps = (fileSize * 8) / duration / 1000;
    const audioKbps = quality === 'high' ? 128 : quality === 'medium' ? 96 : 64;

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
dom.compressBtn.addEventListener('click', startCompression);

async function startCompression() {
    if (!state.file) return;

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
        '-movflags', '+faststart',
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
// PWA
// ============================================
let deferredPrompt;
window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
});

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
}

// Preload FFmpeg WASM immediately — don't wait for file selection
loadFFmpeg();
