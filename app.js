import { FFmpeg } from 'https://unpkg.com/@ffmpeg/ffmpeg@0.12.10/dist/esm/index.js';
import { fetchFile, toBlobURL } from 'https://unpkg.com/@ffmpeg/util@0.12.1/dist/esm/index.js';

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
    outputBlob: null,
    currentScreen: 0,
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
};

// ============================================
// Screen Navigation
// ============================================
function goToScreen(index) {
    const list = [screens.select, screens.options, screens.progress, screens.done];
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
// Quality Selector (pills instead of segmented)
// ============================================
const pills = dom.qualityControl.querySelectorAll('.pill');

pills.forEach((btn) => {
    btn.addEventListener('click', () => {
        pills.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.quality = btn.dataset.quality;

        const preset = QUALITY_PRESETS[state.quality];
        dom.qualityDesc.textContent = preset.desc;

        if (navigator.vibrate) navigator.vibrate(5);
    });
});

// ============================================
// FFmpeg Loading
// ============================================
async function loadFFmpeg() {
    if (state.ffmpegLoaded || state.ffmpeg) return;

    state.ffmpeg = new FFmpeg();
    dom.loadingBar.classList.remove('hidden');

    try {
        const baseURL = 'https://unpkg.com/@ffmpeg/core@0.12.6/dist/esm';

        dom.loadingBarFill.style.width = '10%';

        const coreURL = await toBlobURL(`${baseURL}/ffmpeg-core.js`, 'text/javascript');
        dom.loadingBarFill.style.width = '40%';

        const wasmURL = await toBlobURL(`${baseURL}/ffmpeg-core.wasm`, 'application/wasm');
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
            'Failed to load engine. Please refresh.';
    }
}

// ============================================
// Compression
// ============================================
dom.compressBtn.addEventListener('click', startCompression);

async function startCompression() {
    if (!state.file || !state.ffmpegLoaded) {
        if (!state.ffmpegLoaded) {
            dom.compressBtn.disabled = true;
            dom.compressBtn.querySelector('span').textContent = 'Loading...';
            await loadFFmpeg();
            dom.compressBtn.disabled = false;
            dom.compressBtn.querySelector('span').textContent = 'Compress';
        }
        return;
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

        await ffmpeg.writeFile(inputName, await fetchFile(state.file));

        dom.progressStatus.textContent = 'Compressing...';

        const args = buildFFmpegArgs(inputName, outputName, preset);
        await ffmpeg.exec(args);

        dom.progressStatus.textContent = 'Reading output...';
        updateProgress(99);

        const data = await ffmpeg.readFile(outputName);
        state.outputBlob = new Blob([data.buffer], { type: 'video/mp4' });

        await ffmpeg.deleteFile(inputName);
        await ffmpeg.deleteFile(outputName);

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
        output
    );

    return args;
}

function updateProgress(pct) {
    const circumference = 2 * Math.PI * 68; // r=68
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
