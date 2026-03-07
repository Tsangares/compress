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
    loadingBar: $('#loading-bar'),
    loadingBarFill: $('.engine-loader-fill'),
    aboutBtn: $('#aboutBtn'),
    estQuick: $('#estQuick'),
    estAdvanced: $('#estAdvanced'),
    estTestedRow: $('#estTestedRow'),
    estTesting: $('#estTesting'),
    testEstimate: $('#testEstimate'),
};

// ============================================
// Screen Navigation
// ============================================
function goToScreen(index) {
    const list = [screens.select, screens.options, screens.progress, screens.done, screens.about];
    state.currentScreen = index;

    list.forEach((s, i) => {
        s.classList.remove('active', 'exit-left');
        if (i === index) s.classList.add('active');
        else if (i < index) s.classList.add('exit-left');
    });

    if (navigator.vibrate) navigator.vibrate(10);
}

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

    loadFFmpeg();
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
    if (!state.file || !state.ffmpegLoaded) {
        if (!state.ffmpegLoaded) {
            await loadFFmpeg();
            if (!state.ffmpegLoaded) return;
        }
    }

    dom.testEstimate.disabled = true;
    dom.estTesting.classList.remove('hidden');
    dom.estTestedRow.classList.add('hidden');

    const ffmpeg = state.ffmpeg;
    const preset = QUALITY_PRESETS[state.quality];
    const inputName = 'input' + getExtension(state.file.name);
    const sampleRaw = 'sample_raw.mp4';
    const sampleOut = 'sample_out.mp4';

    try {
        // Write full file to FS if not already there
        if (!state.fileWritten) {
            await ffmpeg.writeFile(inputName, await fetchFile(state.file));
            state.fileWritten = true;
            state.inputName = inputName;
        }

        // Extract a sample clip from the middle (3 seconds, or full video if < 6s)
        const sampleDuration = Math.min(3, state.duration * 0.8);
        const seekPoint = Math.max(0, (state.duration / 2) - (sampleDuration / 2));

        await ffmpeg.exec([
            '-ss', String(seekPoint),
            '-i', inputName,
            '-t', String(sampleDuration),
            '-c', 'copy',
            '-y', sampleRaw,
        ]);

        // Read sample to get raw size
        const rawData = await ffmpeg.readFile(sampleRaw);
        const rawSize = rawData.length;

        // Compress the sample with current quality settings
        const args = buildFFmpegArgs(sampleRaw, sampleOut, preset);
        await ffmpeg.exec(args);

        // Read compressed sample
        const outData = await ffmpeg.readFile(sampleOut);
        const outSize = outData.length;

        // Calculate ratio and extrapolate
        const ratio = outSize / rawSize;
        const estimatedTotal = state.file.size * ratio;

        dom.estAdvanced.textContent = `~${formatBytes(estimatedTotal)}`;
        dom.estTestedRow.classList.remove('hidden');

        // Clean up temp files
        await ffmpeg.deleteFile(sampleRaw).catch(() => {});
        await ffmpeg.deleteFile(sampleOut).catch(() => {});

    } catch (err) {
        console.error('Advanced estimate failed:', err);
        dom.estAdvanced.textContent = 'Error';
        dom.estTestedRow.classList.remove('hidden');
    }

    dom.estTesting.classList.add('hidden');
    dom.testEstimate.disabled = false;
}

// ============================================
// FFmpeg Loading
// ============================================
async function loadFFmpeg() {
    if (state.ffmpegLoaded || state.ffmpegLoading) return;
    state.ffmpegLoading = true;

    state.ffmpeg = new FFmpeg();
    dom.loadingBar.classList.remove('hidden');

    try {
        dom.loadingBarFill.style.width = '10%';

        const coreURL = await toBlobURL('lib/ffmpeg-core.js', 'text/javascript');
        dom.loadingBarFill.style.width = '40%';

        const wasmURL = await toBlobURL('lib/ffmpeg-core.wasm', 'application/wasm');
        dom.loadingBarFill.style.width = '80%';

        await state.ffmpeg.load({ coreURL, wasmURL });
        dom.loadingBarFill.style.width = '100%';

        state.ffmpegLoaded = true;

        setTimeout(() => {
            dom.loadingBar.classList.add('hidden');
        }, 500);
    } catch (err) {
        console.error('Failed to load FFmpeg:', err);
        dom.loadingBar.querySelector('.engine-loader-label').textContent =
            'Failed to load engine. Refresh to retry.';
        state.ffmpegLoading = false;
    }
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

    goToScreen(2);

    const ffmpeg = state.ffmpeg;
    const preset = QUALITY_PRESETS[state.quality];
    const inputName = 'input' + getExtension(state.file.name);
    const outputName = 'output.mp4';

    ffmpeg.on('progress', ({ progress }) => {
        const pct = Math.min(Math.round(progress * 100), 99);
        updateProgress(pct);
    });

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

        dom.progressStatus.textContent = 'Reading output...';
        updateProgress(99);

        const data = await ffmpeg.readFile(outputName);
        state.outputBlob = new Blob([data.buffer], { type: 'video/mp4' });

        await ffmpeg.deleteFile(inputName).catch(() => {});
        await ffmpeg.deleteFile(outputName).catch(() => {});
        state.fileWritten = false;

        updateProgress(100);
        showDone();
    } catch (err) {
        console.error('Compression failed:', err);
        dom.progressStatus.textContent = 'Error: ' + err.message;
    }
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
function showDone() {
    const originalSize = state.file.size;
    const compressedSize = state.outputBlob.size;
    const savings = ((1 - compressedSize / originalSize) * 100).toFixed(1);

    dom.beforeSize.textContent = formatBytes(originalSize);
    dom.afterSize.textContent = formatBytes(compressedSize);
    dom.savingsPercent.textContent = `${savings}%`;

    if (navigator.vibrate) navigator.vibrate([50, 50, 100]);
    goToScreen(3);
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
