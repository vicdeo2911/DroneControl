'use strict';
// Fetch only the latest frame with one outstanding request. Slow browsers skip
// frames rather than building a video queue that delays the flight preview.
window.LatestCameraPreview = class {
  constructor(image, path, onStats = () => {}) {
    this.image = image; this.path = path; this.onStats = onStats;
    this.fps = 15; this.sequence = -1; this.url = null; this.frames = 0;
    this.previewLimit = 15;
    this.started = performance.now(); this.running = true;
    this.next();
  }
  setRate(status) {
    const limit = Number(status.preview_fps_limit);
    if (Number.isFinite(limit) && limit > 0) this.previewLimit = limit;
    const capture = Number(status.settings?.fps);
    this.fps = Math.max(1, Math.min(60, this.previewLimit,
      Number.isFinite(capture) && capture > 0 ? capture : this.previewLimit));
  }
  async next() {
    if (!this.running) return;
    const began = performance.now(); let nextUrl = null;
    if (!document.hidden) {
      try {
        const response = await fetch(`${this.path}?after=${this.sequence}`, {
          cache: 'no-store', signal: AbortSignal.timeout(3500)});
        if (!response.ok) throw Error('Camera preview unavailable');
        const sequence = Number(response.headers.get('X-Frame-Sequence'));
        const ageHeader = response.headers.get('X-Frame-Age');
        const received = performance.now();
        nextUrl = URL.createObjectURL(await response.blob());
        if (!this.running) { URL.revokeObjectURL(nextUrl); return; }
        this.image.src = nextUrl;
        await this.image.decode();
        if (!this.running) { URL.revokeObjectURL(nextUrl); return; }
        if (this.url) URL.revokeObjectURL(this.url);
        this.url = nextUrl; nextUrl = null;
        if (sequence !== this.sequence) this.frames++;
        this.sequence = sequence;
        const elapsed = performance.now() - this.started;
        if (elapsed >= 1000) {
          this.onStats({ready: true, fps: this.frames * 1000 / elapsed,
            ageMs: ageHeader === null ? null : Number(ageHeader) * 1000 + performance.now() - received});
          this.frames = 0; this.started = performance.now();
        }
      } catch (error) {
        if (nextUrl) URL.revokeObjectURL(nextUrl);
        this.onStats({ready: false, error: error.message});
        this.frames = 0; this.started = performance.now();
      }
    } else {
      this.frames = 0; this.started = performance.now();
    }
    if (this.running) this.timer = setTimeout(() => this.next(), Math.max(1, 1000 / this.fps - (performance.now() - began)));
  }
  stop() {
    this.running = false; clearTimeout(this.timer);
    this.image.removeAttribute('src');
    if (this.url) { URL.revokeObjectURL(this.url); this.url = null; }
  }
};
