/**
 * Target adapter interface.
 *
 * A target is whatever the camera is pointed at for an episode: the LoL app, the
 * GitLab web UI, or the offline fixture. The recorder owns pacing, overlays, and
 * video capture; a target owns only "what do I navigate to, and what does an
 * [ACTION] beat mean here".
 *
 * Every method is optional except `id` and `open`. The recorder falls back to
 * generic behaviour (dwell on the current page) for anything a target declines
 * to handle.
 */
export class Target {
  /** @type {string} CLI name, e.g. 'lol' */
  static id = 'base';

  /** @param {{baseUrl?: string, repoDir?: string, log: (s: string) => void}} opts */
  constructor(opts = {}) {
    this.opts = opts;
  }

  /**
   * Fail fast, before Chromium launches, when this target cannot be reached.
   * Throw an Error whose message tells the operator exactly what to fix
   * (VPN off, repo not cloned, no saved auth). Never throw a bare fetch error.
   * @returns {Promise<void>}
   */
  async preflight() {}

  /**
   * Put the page on the episode's opening screen.
   * @param {import('playwright').Page} page
   * @param {object} episode Parsed episode spec.
   * @returns {Promise<void>}
   */
  async open(page, episode) {
    throw new Error(`${this.constructor.id}: open() not implemented`);
  }

  /**
   * Optionally perform an [ACTION] beat. Return true if handled; return false
   * (or omit the method) to let the recorder just dwell for the beat's duration.
   * @param {import('playwright').Page} page
   * @param {object} beat  A beat of kind 'action'.
   * @param {object} ctx   { episode, segment, index }
   * @returns {Promise<boolean>}
   */
  async action(page, beat, ctx) { return false; }

  /** Cleanup between episodes. @returns {Promise<void>} */
  async close(page) {}
}
