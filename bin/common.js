'use strict';

/**
 * Shared helpers for the npm launcher.
 *
 * Termify is a Python application, so the npm package wraps it:
 *   - `setup.js`  creates a private Python environment in ~/.termify/venv
 *   - `termify.js` launches `python -m termify` inside that environment
 *
 * User data (likes, playlists, downloads, sessions) lives in ~/.termify/data
 * and survives npm updates. Override the root with TERMIFY_HOME.
 */

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const PKG_ROOT = path.join(__dirname, '..');
const HOME_ROOT = process.env.TERMIFY_HOME || path.join(os.homedir(), '.termify');
const VENV_DIR = path.join(HOME_ROOT, 'venv');
const DATA_DIR = path.join(HOME_ROOT, 'data');
const REQUIREMENTS = path.join(PKG_ROOT, 'requirements.txt');

function log(msg) {
  console.log(`[termify] ${msg}`);
}

function venvPython() {
  const candidate =
    process.platform === 'win32'
      ? path.join(VENV_DIR, 'Scripts', 'python.exe')
      : path.join(VENV_DIR, 'bin', 'python');
  return fs.existsSync(candidate) ? candidate : null;
}

function requirementsHash() {
  const raw = fs.readFileSync(REQUIREMENTS);
  return crypto.createHash('sha256').update(raw).digest('hex');
}

function markerPath() {
  return path.join(VENV_DIR, '.termify-deps');
}

function depsMatch() {
  try {
    return fs.readFileSync(markerPath(), 'utf8').trim() === requirementsHash();
  } catch (err) {
    return false;
  }
}

/** True when the private environment exists and matches requirements.txt. */
function isReady() {
  return Boolean(venvPython()) && depsMatch();
}

/** Find a system Python >= 3.10. TERMIFY_PYTHON forces a specific interpreter. */
function findPython() {
  if (process.env.TERMIFY_PYTHON) {
    return { cmd: process.env.TERMIFY_PYTHON, args: [] };
  }
  const candidates =
    process.platform === 'win32'
      ? [
          ['py', ['-3']],
          ['python', []],
          ['python3', []],
        ]
      : [
          ['python3', []],
          ['python', []],
        ];
  const probe = 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)';
  for (const [cmd, args] of candidates) {
    const result = spawnSync(cmd, [...args, '-c', probe], { stdio: 'ignore', windowsHide: true });
    if (!result.error && result.status === 0) {
      return { cmd, args };
    }
  }
  return null;
}

const PYTHON_HINT = [
  '',
  'Termify needs Python 3.10 or newer (the npm package installs everything else itself).',
  '',
  '  Windows:        https://www.python.org/downloads/  (tick "Add python.exe to PATH")',
  '  macOS:          brew install python        (or python.org)',
  '  Debian/Ubuntu:  sudo apt install python3 python3-venv',
  '',
  'Then run `termify` again - it will finish the setup automatically.',
  '',
].join('\n');

module.exports = {
  PKG_ROOT,
  HOME_ROOT,
  VENV_DIR,
  DATA_DIR,
  REQUIREMENTS,
  PYTHON_HINT,
  log,
  venvPython,
  requirementsHash,
  markerPath,
  depsMatch,
  isReady,
  findPython,
};
