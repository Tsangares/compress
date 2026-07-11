// shared.js — tiny shared utilities for the compress.applesauce.chat sub-pages
// (photos/, v/). Not a module — classic script, exposes window.CompressShared.
// The main app (app.js, an ES module) keeps its own copies of these for now.
(function () {
    function formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        const val = bytes / Math.pow(1024, i);
        return val >= 100 ? `${Math.round(val)} ${units[i]}` : `${val.toFixed(1)} ${units[i]}`;
    }

    // Native share sheet on mobile, clipboard fallback everywhere else.
    // Returns 'shared' | 'copied' | 'aborted'.
    async function shareOrCopy(url) {
        if (navigator.share && /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)) {
            try {
                await navigator.share({ url });
                return 'shared';
            } catch (err) {
                if (err.name === 'AbortError') return 'aborted';
                await navigator.clipboard.writeText(url);
                return 'copied';
            }
        }
        await navigator.clipboard.writeText(url);
        return 'copied';
    }

    window.CompressShared = { formatBytes, shareOrCopy };
})();
