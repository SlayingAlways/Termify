#!/usr/bin/env node
'use strict';

/**
 * The `termify` command.
 *
 * Runs the Python app inside the private environment that `setup.js` builds
 * (~/.termify/venv). If that environment is missing - first run, or a skipped
 * failed postinstall - it is created here first, so the command always works.
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const c = require('./common');

function finishSetup() {
  c.log('First run: finishing the one-time setup.');
  spawnSync(process.execPath, [path.join(__dirname, 'setup.js')], {
    stdio: 'inherit',
    cwd: c.PKG_ROOT,
  });
}

function main() {
  if (process.argv.includes('--update-deps')) {
    c.log('Refreshing the Python dependencies (yt-dlp and friends) ...');
    const r = spawnSync(process.execPath, [path.join(__dirname, 'setup.js'), '--force'], {
      stdio: 'inherit',
      cwd: c.PKG_ROOT,
    });
    return r.status === 0 ? 0 : 1;
  }

  if (!c.isReady()) {
    finishSetup();
  }
  const python = c.venvPython();
  if (!python) {
    console.error(
      [
        '[termify] The Python environment is not ready.',
        '  Run this once to retry the setup:  node ' + path.join(c.PKG_ROOT, 'bin', 'setup.js'),
        c.PYTHON_HINT,
      ].join('\n')
    );
    return 1;
  }

  fs.mkdirSync(c.DATA_DIR, { recursive: true });
  const env = { ...process.env, PYTHONUTF8: process.env.PYTHONUTF8 || '1' };
  if (!env.TERMIFY_DATA_DIR) {
    env.TERMIFY_DATA_DIR = c.DATA_DIR;
  }

  const result = spawnSync(python, ['-m', 'termify', ...process.argv.slice(2)], {
    stdio: 'inherit',
    cwd: c.PKG_ROOT,
    env,
  });
  if (result.error) {
    console.error(`[termify] Could not start Python: ${result.error.message}`);
    return 1;
  }
  return typeof result.status === 'number' ? result.status : 1;
}

process.exit(main());
